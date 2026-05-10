from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sunsync_hue.bridge import BridgeClient, Room
from sunsync_hue.config import Config
from sunsync_hue.snapshot import snapshot_room
from sunsync_hue.state import LastApplied, LightSnapshot, RoomState, State

logger = logging.getLogger(__name__)

SETTLE_SECONDS = 1.5


def apply_scene_to_room(
    *,
    bridge: BridgeClient,
    room: Room,
    scene_name: str,
    scene_snap: dict[str, LightSnapshot],
    transition_ms: int,
    state: State,
    cfg: Config,
    dry_run: bool = False,
    excluded_ids: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Push a learned snapshot to the room and update the baseline.

    For each light in the snapshot:
      - If snap.on is False → send {on: false} (no brightness/colour).
      - Otherwise send on=true + brightness + (xy or mirek depending on color_mode).
      - All sends include dynamics.duration = transition_ms.

    Then sleep SETTLE_SECONDS, re-read the room from the bridge, and store the
    re-read state as the new baseline. The re-read uses bridge-reported values
    so subsequent match checks compare in the same coordinate system.
    """
    managed_snap = {
        lid: snap for lid, snap in scene_snap.items() if lid not in excluded_ids
    }
    logger.info(
        "%s: applying %r to %d light(s) (transition %dms)%s",
        room.name, scene_name, len(managed_snap), transition_ms,
        " [DRY-RUN]" if dry_run else "",
    )

    for light_id, expected in managed_snap.items():
        if expected.on:
            payload_kwargs = {
                "on": True,
                "brightness": expected.brightness,
                "transition_ms": transition_ms,
            }
            if expected.color_mode == "ct" and expected.mirek is not None:
                payload_kwargs["mirek"] = expected.mirek
            elif expected.color_mode == "xy" and expected.xy is not None:
                payload_kwargs["xy"] = expected.xy
        else:
            payload_kwargs = {"on": False, "transition_ms": transition_ms}

        if dry_run:
            logger.info(
                "  WOULD PUT light %s: %s", light_id, payload_kwargs
            )
        else:
            try:
                bridge.put_light_state(light_id, **payload_kwargs)
            except Exception as e:
                logger.error(
                    "  failed to set light %s in %s: %s", light_id, room.name, e
                )

    if dry_run:
        # Don't re-snapshot in dry-run; nothing changed and we don't want to
        # overwrite the baseline.
        return

    # Wait for transition to register, then capture the new baseline.
    time.sleep(SETTLE_SECONDS)
    try:
        lights = bridge.get_room_lights(room)
        managed_lights = {
            lid: ls for lid, ls in lights.items() if lid not in excluded_ids
        }
        new_baseline = snapshot_room(managed_lights)
    except Exception as e:
        logger.warning(
            "%s: re-snapshot after apply failed: %s — baseline not updated",
            room.name, e,
        )
        new_baseline = None

    room_state = state.rooms.get(room.name)
    if room_state is None:
        room_state = RoomState(room_id=room.id)
        state.rooms[room.name] = room_state

    # Update the baseline for the scene we just applied — this is what future
    # match checks will compare against. Excluded lights aren't re-snapshotted,
    # so preserve their existing entries (lets un-excluding restore tracking
    # without a re-learn).
    if new_baseline is not None:
        prior = room_state.scenes.get(scene_name, {})
        merged = {lid: snap for lid, snap in prior.items() if lid in excluded_ids}
        merged.update(new_baseline)
        room_state.scenes[scene_name] = merged

    tz = ZoneInfo(cfg.location.timezone)
    room_state.last_applied = LastApplied(
        scene=scene_name, at=datetime.now(tz=tz)
    )
