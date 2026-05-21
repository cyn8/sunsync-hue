from __future__ import annotations

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import scenes as scenes_mod
from sunsync_hue import state as state_mod


def run(room: str) -> None:
    cfg = cfg_mod.load()
    learned = scenes_mod.load()
    st = state_mod.load()

    in_config = room in cfg.rooms.monitored
    in_scenes = room in learned.rooms
    in_state = room in st.rooms
    in_excluded = room in cfg.rooms.excluded

    if not in_config and not in_scenes and not in_state and not in_excluded:
        typer.secho(
            f"Room {room!r} is not currently monitored.", fg=typer.colors.YELLOW
        )
        raise typer.Exit(code=1)

    if in_config or in_excluded:
        cfg.rooms.monitored = [r for r in cfg.rooms.monitored if r != room]
        cfg.rooms.excluded.pop(room, None)
        cfg_mod.save(cfg)
        typer.echo(f"Removed {room!r} from monitored rooms in config.")

    if in_scenes:
        del learned.rooms[room]
        scenes_mod.save(learned)
        typer.echo(f"Dropped learned scenes for {room!r}.")

    if in_state:
        del st.rooms[room]
        state_mod.save(st)
        typer.echo(f"Dropped runtime state for {room!r}.")

    typer.secho("Done.", fg=typer.colors.GREEN)
