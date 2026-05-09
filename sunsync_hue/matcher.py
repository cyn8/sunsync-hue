from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from sunsync_hue.config import MatchingConfig
from sunsync_hue.state import LightSnapshot

logger = logging.getLogger(__name__)


@dataclass
class LightMismatch:
    light_id: str
    field: str
    expected: object
    actual: object
    delta: float | None = None


def light_matches(
    current: LightSnapshot,
    expected: LightSnapshot,
    tol: MatchingConfig,
) -> tuple[bool, list[LightMismatch]]:
    """Compare a current light state against an expected (snapshotted) state.

    Returns (matches, [mismatches]). When matches is True, mismatches is empty.
    Always returns ALL mismatches so callers can log every reason.
    """
    mismatches: list[LightMismatch] = []
    lid = "<light>"  # caller fills in via wrapping; kept generic here

    # on/off — exact
    if current.on != expected.on:
        mismatches.append(
            LightMismatch(lid, "on", expected.on, current.on)
        )
        # If off-state mismatches, brightness/colour comparisons are nonsense.
        return False, mismatches

    # If both are off, nothing else to compare.
    if not current.on:
        return True, []

    # brightness — tolerance
    if expected.brightness is not None and current.brightness is not None:
        delta = abs(current.brightness - expected.brightness)
        if delta > tol.brightness_tolerance:
            mismatches.append(
                LightMismatch(
                    lid, "brightness", expected.brightness, current.brightness, delta
                )
            )

    # color_mode — must match if expected has one
    if expected.color_mode != "none":
        if current.color_mode != expected.color_mode:
            mismatches.append(
                LightMismatch(
                    lid, "color_mode", expected.color_mode, current.color_mode
                )
            )
        elif expected.color_mode == "ct":
            if expected.mirek is not None and current.mirek is not None:
                delta = abs(current.mirek - expected.mirek)
                if delta > tol.mirek_tolerance:
                    mismatches.append(
                        LightMismatch(
                            lid, "mirek", expected.mirek, current.mirek, float(delta)
                        )
                    )
        elif expected.color_mode == "xy":
            if expected.xy is not None and current.xy is not None:
                dx = current.xy[0] - expected.xy[0]
                dy = current.xy[1] - expected.xy[1]
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > tol.xy_tolerance:
                    mismatches.append(
                        LightMismatch(lid, "xy", expected.xy, current.xy, dist)
                    )

    return (not mismatches), mismatches


def room_matches_any_scene(
    current: dict[str, LightSnapshot],
    scenes: dict[str, dict[str, LightSnapshot]],
    tol: MatchingConfig,
    *,
    room_name: str = "<room>",
) -> str | None:
    """Return the first scene name whose snapshot matches the current room state.

    A scene matches when *every* light in that scene's snapshot is found in
    `current` and matches per `light_matches`. Extra lights in `current` that
    aren't in the snapshot are ignored (a new bulb shouldn't break matching).

    Returns None if no learned scene matches — caller should SKIP this room.
    """
    if not scenes:
        return None

    for scene_name, scene_snap in scenes.items():
        all_match = True
        for light_id, expected in scene_snap.items():
            cur = current.get(light_id)
            if cur is None:
                logger.debug(
                    "%s: scene %r — light %s missing from current state",
                    room_name, scene_name, light_id,
                )
                all_match = False
                break
            ok, mismatches = light_matches(cur, expected, tol)
            if not ok:
                if logger.isEnabledFor(logging.DEBUG):
                    for m in mismatches:
                        logger.debug(
                            "%s: scene %r — light %s field %s expected %r got %r%s",
                            room_name, scene_name, light_id, m.field,
                            m.expected, m.actual,
                            f" (delta {m.delta:.3f})" if m.delta is not None else "",
                        )
                all_match = False
                break
        if all_match:
            return scene_name

    return None
