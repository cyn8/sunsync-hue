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
    logger.info(
        "%s: applying %r to %d light(s) (transition %dms)%s",
        room.name, scene_name, len(scene_snap), transition_ms,
        " [DRY-RUN]" if dry_run else "",
    )

    for light_id, expected in scene_snap.items():
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
        new_baseline = snapshot_room(lights)
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
    # match checks will compare against.
    if new_baseline is not None:
        room_state.scenes[scene_name] = new_baseline

    tz = ZoneInfo(cfg.location.timezone)
    room_state.last_applied = LastApplied(
        scene=scene_name, at=datetime.now(tz=tz)
    )
