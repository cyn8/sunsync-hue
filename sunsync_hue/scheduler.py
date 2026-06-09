from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sunsync_hue import config as cfg_mod
from sunsync_hue import scenes as scenes_mod
from sunsync_hue import state as state_mod
from sunsync_hue.apply import apply_scene_to_room
from sunsync_hue.bridge import BridgeClient, BridgeError
from sunsync_hue.coming_home import ComingHomeCoordinator, start_webhook_server
from sunsync_hue.daylight import (
    compute_events,
    current_scene_for,
    previous_scene,
    upcoming_events,
)
from sunsync_hue.exclusion import excluded_ids_for_room, filter_scenes
from sunsync_hue.matcher import room_matches_any_scene
from sunsync_hue.snapshot import snapshot_room

logger = logging.getLogger(__name__)

# Sleep loop wakes every SHORT_SLEEP_SEC to check the SIGTERM flag.
SHORT_SLEEP_SEC = 30


_shutdown = False


def _on_signal(signum, frame):  # noqa: ANN001
    global _shutdown
    logger.info("received signal %s, shutting down after current tick", signum)
    _shutdown = True


def run(*, dry_run: bool, catch_up: bool) -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    cfg = cfg_mod.load()
    tz = ZoneInfo(cfg.location.timezone)
    apply_lock = threading.Lock()
    webhook = None

    if not cfg.rooms.monitored:
        logger.error("no rooms monitored — run `sunsync-hue learn` first")
        return

    logger.info(
        "starting daemon — bridge %s, %d monitored room(s): %s",
        cfg.bridge.ip,
        len(cfg.rooms.monitored),
        ", ".join(cfg.rooms.monitored),
    )
    offsets = cfg.schedule.sun_offsets
    logger.info(
        "transition: %dms; day=sunrise%s, afternoon=golden hour%s, "
        "evening=nautical dusk%s, night=%s",
        cfg.transitions.duration_ms,
        _offset_suffix(offsets.day),
        _offset_suffix(offsets.afternoon),
        _offset_suffix(offsets.evening),
        cfg.schedule.night_local_time.strftime("%-I:%M %p"),
    )

    today_events = compute_events(
        datetime.now(tz=tz).date(), cfg.location, cfg.schedule
    )
    logger.info(
        "today's events: %s",
        ", ".join(
            f"{e.scene}@{e.when.strftime('%-I:%M %p')}" for e in today_events
        ),
    )

    if cfg.webhook.enabled:
        coordinator = ComingHomeCoordinator(
            cfg=cfg,
            dry_run=dry_run,
            apply_lock=apply_lock,
        )
        webhook = start_webhook_server(cfg=cfg, coordinator=coordinator)
        host, port = webhook.server.server_address[:2]
        logger.info(
            "webhook listener enabled on http://%s:%s "
            "(coming-home window %ds)",
            host,
            port,
            cfg.webhook.coming_home_window_seconds,
        )
    else:
        logger.info("webhook listener disabled")

    try:
        if catch_up:
            scene = current_scene_for(datetime.now(tz=tz), cfg.location, cfg.schedule)
            logger.info("--catch-up: current scene by clock is %r", scene)
            _do_tick(
                cfg=cfg,
                scene=scene,
                dry_run=dry_run,
                label="catch-up",
                apply_lock=apply_lock,
            )

        while not _shutdown:
            # Recompute on every iteration — sunrise/sunset shift daily, and DST.
            now = datetime.now(tz=tz)
            upcoming = upcoming_events(now, cfg.location, cfg.schedule)
            if not upcoming:
                # Should never happen with horizon_days >= 1; sleep an hour.
                logger.warning("no upcoming events found, sleeping 1h")
                _interruptible_sleep(3600)
                continue

            next_event = upcoming[0]
            wait = (next_event.when - now).total_seconds()
            logger.info(
                "next trigger: %s at %s (in %s)",
                next_event.scene,
                next_event.when.strftime("%Y-%m-%d %-I:%M:%S %p %Z"),
                _human_duration(wait),
            )

            _interruptible_sleep(wait)
            if _shutdown:
                break

            logger.info("trigger fired: %s", next_event.scene)
            _do_tick(
                cfg=cfg,
                scene=next_event.scene,
                dry_run=dry_run,
                label="trigger",
                apply_lock=apply_lock,
            )

            # Tiny pad so we don't immediately re-pick the same event due to
            # clock skew at sub-second precision.
            time.sleep(1)
    finally:
        if webhook is not None:
            logger.info("stopping webhook listener")
            webhook.stop()

        logger.info("daemon stopped")


def _do_tick(
    *,
    cfg: cfg_mod.Config,
    scene: str,
    dry_run: bool,
    label: str,
    apply_lock: threading.Lock,
) -> None:
    """Run the per-room match check + maybe apply for one trigger."""
    with apply_lock:
        try:
            learned = scenes_mod.load()
            st = state_mod.load()
        except Exception as e:
            logger.error("failed to load local data files: %s — skipping tick", e)
            return

        try:
            with BridgeClient(cfg.bridge.ip, cfg.bridge.app_key) as bridge:
                rooms_by_name = {r.name: r for r in bridge.get_rooms()}

                any_changed = False
                for room_name in cfg.rooms.monitored:
                    r = rooms_by_name.get(room_name)
                    if r is None:
                        logger.warning(
                            "%s: not found on bridge — skipping (was it deleted?)",
                            room_name,
                        )
                        continue

                    learned_room = learned.rooms.get(room_name)
                    if learned_room is None or scene not in learned_room.scenes:
                        logger.info(
                            "%s: SKIP — scene %r not learned for this room",
                            room_name, scene,
                        )
                        continue

                    try:
                        room_lights = bridge.get_room_lights(r)
                    except BridgeError as e:
                        logger.warning("%s: bridge read failed: %s", room_name, e)
                        continue

                    excluded_ids = excluded_ids_for_room(
                        room_lights, cfg.rooms.excluded.get(room_name, [])
                    )
                    managed_scenes = filter_scenes(learned_room.scenes, excluded_ids)
                    if not managed_scenes.get(scene):
                        logger.info(
                            "%s: SKIP — every light in this room is excluded",
                            room_name,
                        )
                        continue
                    current = {
                        lid: snap for lid, snap in snapshot_room(room_lights).items()
                        if lid not in excluded_ids
                    }

                    prev = previous_scene(scene)
                    if not managed_scenes.get(prev):
                        logger.info(
                            "%s: SKIP — previous scene %r not learned for this room",
                            room_name, prev,
                        )
                        continue

                    current_match = room_matches_any_scene(
                        current, managed_scenes, cfg.matching, room_name=room_name
                    )
                    if current_match != prev:
                        on = (
                            f"on {current_match!r}"
                            if current_match
                            else "on no learned scene"
                        )
                        logger.info(
                            "%s: SKIP — %s, not previous scene %r "
                            "(manual override or skipped transition)",
                            room_name, on, prev,
                        )
                        continue

                    logger.info(
                        "%s: on previous scene %r → applying %r",
                        room_name, prev, scene,
                    )
                    apply_scene_to_room(
                        bridge=bridge,
                        room=r,
                        scene_name=scene,
                        scene_snap=learned_room.scenes[scene],
                        transition_ms=cfg.transitions.duration_ms,
                        state=st,
                        cfg=cfg,
                        dry_run=dry_run,
                        excluded_ids=excluded_ids,
                    )
                    any_changed = True
        except BridgeError as e:
            logger.error("[%s] bridge unreachable: %s", label, e)
            return

        if any_changed and not dry_run:
            try:
                state_mod.save(st)
            except Exception as e:
                logger.error("failed to save state: %s", e)


def _interruptible_sleep(seconds: float) -> None:
    """Sleep up to `seconds`, in chunks of SHORT_SLEEP_SEC, checking _shutdown."""
    end = time.monotonic() + max(0.0, seconds)
    while not _shutdown:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(SHORT_SLEEP_SEC, remaining))


def _human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _offset_suffix(minutes: int) -> str:
    if minutes == 0:
        return ""
    sign = "+" if minutes > 0 else ""
    return f" ({sign}{minutes}m)"
