from __future__ import annotations

import logging

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod
from sunsync_hue.apply import apply_scene_to_room
from sunsync_hue.bridge import BridgeClient
from sunsync_hue.config import SCENE_NAMES
from sunsync_hue.daylight import previous_scene
from sunsync_hue.exclusion import excluded_ids_for_room, filter_scenes
from sunsync_hue.matcher import room_matches_any_scene
from sunsync_hue.snapshot import snapshot_room

logger = logging.getLogger(__name__)


def run(scene: str, room: str | None, force: bool, dry_run: bool) -> None:
    if scene not in SCENE_NAMES:
        typer.secho(
            f"Unknown scene {scene!r}. Valid: {', '.join(SCENE_NAMES)}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    cfg = cfg_mod.load()
    st = state_mod.load()

    target_rooms: list[str]
    if room is not None:
        if room not in cfg.rooms.monitored:
            typer.secho(
                f"Room {room!r} is not in the monitored set "
                f"({', '.join(cfg.rooms.monitored)}).",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        target_rooms = [room]
    else:
        target_rooms = list(cfg.rooms.monitored)

    if not target_rooms:
        typer.echo("No rooms to apply to.")
        raise typer.Exit(code=1)

    with BridgeClient(cfg.bridge.ip, cfg.bridge.app_key) as bridge:
        rooms_by_name = {r.name: r for r in bridge.get_rooms()}

        any_changed = False
        for room_name in target_rooms:
            r = rooms_by_name.get(room_name)
            if r is None:
                typer.secho(
                    f"  {room_name}: NOT FOUND on bridge", fg=typer.colors.RED
                )
                continue

            room_state = st.rooms.get(room_name)
            if room_state is None or scene not in room_state.scenes:
                typer.secho(
                    f"  {room_name}: scene {scene!r} not learned — skipping",
                    fg=typer.colors.YELLOW,
                )
                continue

            room_lights = bridge.get_room_lights(r)
            excluded_ids = excluded_ids_for_room(
                room_lights, cfg.rooms.excluded.get(room_name, [])
            )
            managed_scenes = filter_scenes(room_state.scenes, excluded_ids)
            if not managed_scenes.get(scene):
                typer.secho(
                    f"  {room_name}: SKIP — every light in this room is excluded",
                    fg=typer.colors.YELLOW,
                )
                continue

            if not force:
                prev = previous_scene(scene)
                if not managed_scenes.get(prev):
                    typer.secho(
                        f"  {room_name}: SKIP — previous scene {prev!r} not learned "
                        "(use --force to override)",
                        fg=typer.colors.YELLOW,
                    )
                    continue
                managed_current = {
                    lid: snap for lid, snap in snapshot_room(room_lights).items()
                    if lid not in excluded_ids
                }
                current_match = room_matches_any_scene(
                    managed_current, managed_scenes, cfg.matching, room_name=room_name
                )
                if current_match != prev:
                    on = (
                        f"on {current_match!r}"
                        if current_match
                        else "on no learned scene"
                    )
                    typer.secho(
                        f"  {room_name}: SKIP — {on}, not previous scene {prev!r} "
                        "(use --force to override)",
                        fg=typer.colors.YELLOW,
                    )
                    continue

            apply_scene_to_room(
                bridge=bridge,
                room=r,
                scene_name=scene,
                scene_snap=room_state.scenes[scene],
                transition_ms=cfg.transitions.duration_ms,
                state=st,
                cfg=cfg,
                dry_run=dry_run,
                excluded_ids=excluded_ids,
            )
            any_changed = True

    if any_changed and not dry_run:
        state_mod.save(st)
