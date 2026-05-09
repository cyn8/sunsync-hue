from __future__ import annotations

from sunsync_hue.bridge import LightState
from sunsync_hue.state import LightSnapshot


def snapshot_from_light(light: LightState) -> LightSnapshot:
    """Capture a per-light snapshot from a current bridge reading.

    For off lights, brightness is set to None (the bridge reports a stale
    "target when next on" value that we deliberately ignore).
    """
    return LightSnapshot(
        on=light.on,
        brightness=light.brightness if light.on else None,
        color_mode=light.color_mode,
        xy=light.xy,
        mirek=light.mirek,
    )


def snapshot_room(
    lights: dict[str, LightState],
) -> dict[str, LightSnapshot]:
    """Capture a per-light snapshot for every light in a room."""
    return {lid: snapshot_from_light(ls) for lid, ls in lights.items()}
