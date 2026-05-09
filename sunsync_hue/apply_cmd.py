from __future__ import annotations

import logging

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod
from sunsync_hue.apply import apply_scene_to_room
from sunsync_hue.bridge import BridgeClient
from sunsync_hue.config import SCENE_NAMES
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

            if not force:
                current = snapshot_room(bridge.get_room_lights(r))
                matched = room_matches_any_scene(
                    current, room_state.scenes, cfg.matching, room_name=room_name
                )
                if matched is None:
                    typer.secho(
                        f"  {room_name}: SKIP — does not match any learned scene "
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
            )
            any_changed = True

    if any_changed and not dry_run:
        state_mod.save(st)
