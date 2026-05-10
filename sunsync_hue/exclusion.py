from __future__ import annotations

from sunsync_hue.bridge import LightState


def excluded_ids_for_room(
    room_lights: dict[str, LightState],
    excluded_names: list[str],
) -> set[str]:
    """Resolve a list of excluded light *names* to the set of bridge IDs.

    Names are matched case-sensitively against the live `LightState.name`.
    Names with no matching light in the room (e.g. the user renamed the
    bulb in the Hue app) are silently ignored — the exclusion lapses and
    sunsync-hue starts managing that light again.
    """
    if not excluded_names:
        return set()
    wanted = set(excluded_names)
    return {lid for lid, ls in room_lights.items() if ls.name in wanted}


def filter_scenes(
    scenes: dict[str, dict],
    excluded_ids: set[str],
) -> dict[str, dict]:
    """Drop excluded light IDs from each scene's snapshot dict."""
    if not excluded_ids:
        return scenes
    return {
        name: {lid: snap for lid, snap in snaps.items() if lid not in excluded_ids}
        for name, snaps in scenes.items()
    }
