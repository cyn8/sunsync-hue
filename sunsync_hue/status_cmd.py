from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import scenes as scenes_mod
from sunsync_hue import state as state_mod
from sunsync_hue.daylight import compute_events


def run() -> None:
    cfg = cfg_mod.load()
    scenes = scenes_mod.load()
    st = state_mod.load()
    tz = ZoneInfo(cfg.location.timezone)
    now = datetime.now(tz=tz)

    typer.secho("Configuration", bold=True)
    typer.echo(f"  config:   {cfg_mod.config_path()}")
    typer.echo(f"  scenes:   {scenes_mod.learned_scenes_path()}")
    typer.echo(f"  state:    {state_mod.state_path()}")
    typer.echo(f"  bridge:   {cfg.bridge.ip}")
    typer.echo(
        f"  location: {cfg.location.latitude:.4f}, {cfg.location.longitude:.4f} "
        f"({cfg.location.timezone})"
    )
    typer.echo(f"  transition: {cfg.transitions.duration_ms}ms")
    webhook = (
        f"enabled on {cfg.webhook.host}:{cfg.webhook.port} "
        f"({cfg.webhook.coming_home_window_seconds}s coming-home window)"
        if cfg.webhook.enabled
        else "disabled"
    )
    typer.echo(f"  webhook:  {webhook}")
    typer.echo(
        f"  schedule: day at sunrise, afternoon at golden hour, "
        f"evening at nautical dusk, "
        f"night {cfg.schedule.night_local_time.strftime('%-I:%M %p')}"
    )

    typer.secho("\nToday's events", bold=True)
    events = compute_events(now.date(), cfg.location, cfg.schedule)
    for ev in events:
        marker = " ←" if ev.when <= now and (
            ev is events[-1] or events[events.index(ev) + 1].when > now
        ) else ""
        when_str = ev.when.strftime("%-I:%M:%S %p %Z")
        day_offset = (ev.when.date() - now.date()).days
        if day_offset:
            when_str += f" (+{day_offset}d)" if day_offset > 0 else f" ({day_offset}d)"
        typer.echo(f"  {ev.scene:10s} {when_str}{marker}")

    typer.secho("\nMonitored rooms", bold=True)
    if not cfg.rooms.monitored:
        typer.echo("  (none — run `sunsync-hue learn`)")
    for room_name in cfg.rooms.monitored:
        learned_room = scenes.rooms.get(room_name)
        if learned_room is None:
            typer.echo(f"  {room_name}: no scenes learned")
            continue
        scene_names = ", ".join(sorted(learned_room.scenes.keys()))
        rs = st.rooms.get(room_name)
        last = (
            f"{rs.last_applied.scene} at "
            f"{rs.last_applied.at.astimezone(tz).strftime('%Y-%m-%d %-I:%M:%S %p %Z')}"
            if rs and rs.last_applied
            else "never"
        )
        typer.echo(f"  {room_name}")
        typer.echo(f"    scenes:       {scene_names or '(none)'}")
        typer.echo(f"    last applied: {last}")
        excluded = cfg.rooms.excluded.get(room_name) or []
        if excluded:
            typer.echo(f"    excluded:     {', '.join(excluded)}")
