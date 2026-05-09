from __future__ import annotations

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod


def run(room: str) -> None:
    cfg = cfg_mod.load()
    st = state_mod.load()

    in_config = room in cfg.rooms.monitored
    in_state = room in st.rooms

    if not in_config and not in_state:
        typer.secho(
            f"Room {room!r} is not currently monitored.", fg=typer.colors.YELLOW
        )
        raise typer.Exit(code=1)

    if in_config:
        cfg.rooms.monitored = [r for r in cfg.rooms.monitored if r != room]
        cfg_mod.save(cfg)
        typer.echo(f"Removed {room!r} from monitored rooms in config.")

    if in_state:
        del st.rooms[room]
        state_mod.save(st)
        typer.echo(f"Dropped snapshots for {room!r} from state.")

    typer.secho("Done.", fg=typer.colors.GREEN)
