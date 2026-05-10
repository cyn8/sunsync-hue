from __future__ import annotations

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue.bridge import BridgeClient


def _resolve_room_lights(cfg: cfg_mod.Config, room_name: str) -> list[str]:
    """Return the live list of light names for `room_name`, or exit with an error."""
    if room_name not in cfg.rooms.monitored:
        typer.secho(
            f"Room {room_name!r} is not monitored. "
            f"Run `sunsync-hue learn` first to add it.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    with BridgeClient(cfg.bridge.ip, cfg.bridge.app_key) as bridge:
        rooms_by_name = {r.name: r for r in bridge.get_rooms()}
        room = rooms_by_name.get(room_name)
        if room is None:
            typer.secho(
                f"Room {room_name!r} was not found on the bridge.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        lights = bridge.get_room_lights(room)
    return sorted({ls.name for ls in lights.values()})


def run_exclude(room: str, light: str) -> None:
    cfg = cfg_mod.load()
    light_names = _resolve_room_lights(cfg, room)

    if light not in light_names:
        typer.secho(
            f"Light {light!r} is not in room {room!r}.", fg=typer.colors.RED
        )
        typer.echo(f"  Available: {', '.join(light_names)}")
        raise typer.Exit(code=1)

    current = list(cfg.rooms.excluded.get(room, []))
    if light in current:
        typer.secho(
            f"{light!r} is already excluded from {room!r}.", fg=typer.colors.YELLOW
        )
        return

    current.append(light)
    cfg.rooms.excluded[room] = current
    cfg_mod.save(cfg)
    typer.secho(
        f"Excluded {light!r} from {room!r}.", fg=typer.colors.GREEN
    )


def run_include(room: str, light: str) -> None:
    cfg = cfg_mod.load()
    if room not in cfg.rooms.monitored:
        typer.secho(
            f"Room {room!r} is not monitored.", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)

    current = list(cfg.rooms.excluded.get(room, []))
    if light not in current:
        typer.secho(
            f"{light!r} is not currently excluded from {room!r}.",
            fg=typer.colors.YELLOW,
        )
        return

    current.remove(light)
    if current:
        cfg.rooms.excluded[room] = current
    else:
        cfg.rooms.excluded.pop(room, None)
    cfg_mod.save(cfg)
    typer.secho(
        f"Re-included {light!r} in {room!r}.", fg=typer.colors.GREEN
    )
