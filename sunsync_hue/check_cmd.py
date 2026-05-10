from __future__ import annotations

import logging

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod
from sunsync_hue.bridge import BridgeClient
from sunsync_hue.exclusion import excluded_ids_for_room, filter_scenes
from sunsync_hue.matcher import room_matches_any_scene
from sunsync_hue.snapshot import snapshot_room

logger = logging.getLogger(__name__)


def run() -> None:
    cfg = cfg_mod.load()
    st = state_mod.load()

    if not cfg.rooms.monitored:
        typer.echo("No rooms monitored. Run `sunsync-hue learn` first.")
        raise typer.Exit(code=1)

    with BridgeClient(cfg.bridge.ip, cfg.bridge.app_key) as bridge:
        rooms = {r.name: r for r in bridge.get_rooms()}

        for room_name in cfg.rooms.monitored:
            room = rooms.get(room_name)
            if room is None:
                typer.secho(
                    f"  {room_name}: NOT FOUND on bridge", fg=typer.colors.RED
                )
                continue

            room_state = st.rooms.get(room_name)
            if room_state is None or not room_state.scenes:
                typer.secho(
                    f"  {room_name}: SKIP — no scenes learned", fg=typer.colors.YELLOW
                )
                continue

            room_lights = bridge.get_room_lights(room)
            excluded_ids = excluded_ids_for_room(
                room_lights, cfg.rooms.excluded.get(room_name, [])
            )
            current = {
                lid: snap for lid, snap in snapshot_room(room_lights).items()
                if lid not in excluded_ids
            }
            managed_scenes = filter_scenes(room_state.scenes, excluded_ids)
            if not any(managed_scenes.values()):
                typer.secho(
                    f"  {room_name}: SKIP — every light in this room is excluded",
                    fg=typer.colors.YELLOW,
                )
                continue
            matched = room_matches_any_scene(
                current, managed_scenes, cfg.matching, room_name=room_name
            )
            if matched is None:
                typer.secho(
                    f"  {room_name}: SKIP — does not match any learned scene "
                    "(manual override detected)",
                    fg=typer.colors.YELLOW,
                )
            else:
                typer.secho(
                    f"  {room_name}: matches '{matched}' ({len(current)} lights)",
                    fg=typer.colors.GREEN,
                )
