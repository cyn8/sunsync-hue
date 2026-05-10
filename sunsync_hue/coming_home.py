from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod
from sunsync_hue.apply import apply_scene_to_room
from sunsync_hue.bridge import BridgeClient, BridgeError, Room
from sunsync_hue.daylight import current_scene_for
from sunsync_hue.exclusion import excluded_ids_for_room

logger = logging.getLogger(__name__)


class ComingHomeCoordinator:
    """Track HomeKit arrival/motion events and evaluate coming-home transitions."""

    def __init__(
        self,
        *,
        cfg: cfg_mod.Config,
        dry_run: bool,
        apply_lock: threading.Lock,
    ) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.apply_lock = apply_lock
        self._event_lock = threading.Lock()
        self._last_arrival: datetime | None = None
        self._last_motion: datetime | None = None
        self._transition_running = False

    def record_arrival(self) -> dict[str, Any]:
        return self._record("arrival")

    def record_motion(self) -> dict[str, Any]:
        return self._record("motion")

    def _record(self, event_name: str) -> dict[str, Any]:
        now = datetime.now(tz=ZoneInfo(self.cfg.location.timezone))
        with self._event_lock:
            if event_name == "arrival":
                self._last_arrival = now
            elif event_name == "motion":
                self._last_motion = now
            else:
                raise ValueError(f"unknown coming-home event {event_name!r}")

            self._expire_old_events_locked(now)
            paired = self._events_are_paired_locked()
            queued = False
            already_running = self._transition_running
            if paired:
                self._reset_paired_events_locked()
                if not self._transition_running:
                    self._transition_running = True
                    queued = True
                    threading.Thread(
                        target=self._run_transition,
                        name="sunsync-coming-home",
                        daemon=True,
                    ).start()

        logger.info(
            "coming home: recorded %s%s",
            event_name,
            ", queued evaluation" if queued else "",
        )
        return {
            "ok": True,
            "event": event_name,
            "paired": paired,
            "evaluation_queued": queued,
            "evaluation_running": already_running,
            "window_seconds": self.cfg.webhook.coming_home_window_seconds,
        }

    def _expire_old_events_locked(self, now: datetime) -> None:
        window = timedelta(seconds=self.cfg.webhook.coming_home_window_seconds)
        if self._last_arrival and now - self._last_arrival > window:
            self._last_arrival = None
        if self._last_motion and now - self._last_motion > window:
            self._last_motion = None

    def _events_are_paired_locked(self) -> bool:
        if self._last_arrival is None or self._last_motion is None:
            return False
        window = timedelta(seconds=self.cfg.webhook.coming_home_window_seconds)
        return abs(self._last_arrival - self._last_motion) <= window

    def _reset_paired_events_locked(self) -> None:
        self._last_arrival = None
        self._last_motion = None

    def _run_transition(self) -> None:
        try:
            self._evaluate_and_transition()
        except Exception:
            logger.exception("coming home: evaluation failed unexpectedly")
        finally:
            with self._event_lock:
                self._transition_running = False

    def _evaluate_and_transition(self) -> bool:
        tz = ZoneInfo(self.cfg.location.timezone)
        now = datetime.now(tz=tz)
        scene = current_scene_for(now, self.cfg.location, self.cfg.schedule)
        logger.info("coming home: evaluating transition to %r", scene)

        with self.apply_lock:
            try:
                st = state_mod.load()
            except Exception as e:
                logger.error("coming home: failed to load state file: %s", e)
                return False

            jobs: list[tuple[Room, dict, set[str]]] = []
            try:
                with BridgeClient(self.cfg.bridge.ip, self.cfg.bridge.app_key) as bridge:
                    rooms_by_name = {r.name: r for r in bridge.get_rooms()}
                    for room_name in self.cfg.rooms.monitored:
                        room = rooms_by_name.get(room_name)
                        if room is None:
                            logger.warning(
                                "coming home: %s not found on bridge; skipping transition",
                                room_name,
                            )
                            return False

                        try:
                            room_lights = bridge.get_room_lights(room)
                        except BridgeError as e:
                            logger.warning(
                                "coming home: %s bridge read failed: %s",
                                room_name,
                                e,
                            )
                            return False

                        excluded_ids = excluded_ids_for_room(
                            room_lights,
                            self.cfg.rooms.excluded.get(room_name, []),
                        )
                        managed_lights = {
                            lid: light
                            for lid, light in room_lights.items()
                            if lid not in excluded_ids
                        }
                        if not managed_lights:
                            logger.info(
                                "coming home: %s has no managed lights; ignoring",
                                room_name,
                            )
                            continue
                        on_lights = [
                            light.name for light in managed_lights.values() if light.on
                        ]
                        if on_lights:
                            logger.info(
                                "coming home: %s has managed lights already on (%s); "
                                "skipping transition",
                                room_name,
                                ", ".join(on_lights),
                            )
                            return False

                        room_state = st.rooms.get(room_name)
                        if room_state is None or scene not in room_state.scenes:
                            logger.info(
                                "coming home: %s has no learned %r scene; "
                                "skipping transition",
                                room_name,
                                scene,
                            )
                            return False
                        scene_snap = {
                            lid: snap
                            for lid, snap in room_state.scenes[scene].items()
                            if lid not in excluded_ids
                        }
                        if not scene_snap:
                            logger.info(
                                "coming home: %s has no managed lights in %r scene; "
                                "skipping transition",
                                room_name,
                                scene,
                            )
                            return False
                        jobs.append((room, scene_snap, excluded_ids))

                    if not jobs:
                        logger.info("coming home: no eligible rooms to transition")
                        return False

                    for room, scene_snap, excluded_ids in jobs:
                        logger.info(
                            "coming home: %s all managed lights off; applying %r",
                            room.name,
                            scene,
                        )
                        apply_scene_to_room(
                            bridge=bridge,
                            room=room,
                            scene_name=scene,
                            scene_snap=scene_snap,
                            transition_ms=self.cfg.transitions.duration_ms,
                            state=st,
                            cfg=self.cfg,
                            dry_run=self.dry_run,
                            excluded_ids=excluded_ids,
                        )
            except BridgeError as e:
                logger.error("coming home: bridge unreachable: %s", e)
                return False

            if not self.dry_run:
                try:
                    state_mod.save(st)
                except Exception as e:
                    logger.error("coming home: failed to save state: %s", e)
                    return False

        logger.info("coming home: transition to %r complete", scene)
        return True


class WebhookHandle:
    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self.server = server
        self.thread = thread

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_webhook_server(
    *,
    cfg: cfg_mod.Config,
    coordinator: ComingHomeCoordinator,
) -> WebhookHandle:
    handler = _make_handler(coordinator)
    server = ThreadingHTTPServer((cfg.webhook.host, cfg.webhook.port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="sunsync-webhook",
        daemon=True,
    )
    thread.start()
    return WebhookHandle(server, thread)


def _make_handler(coordinator: ComingHomeCoordinator) -> type[BaseHTTPRequestHandler]:
    class ComingHomeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                self._send_json(200, {"ok": True})
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            self._drain_request_body()
            if path == "/coming-home/arrival":
                self._send_json(202, coordinator.record_arrival())
                return
            if path == "/coming-home/motion":
                self._send_json(202, coordinator.record_motion())
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("webhook: " + fmt, *args)

        def _drain_request_body(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 0:
                self.rfile.read(length)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ComingHomeHandler
