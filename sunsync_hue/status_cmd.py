from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod
from sunsync_hue.daylight import compute_events


def run() -> None:
    cfg = cfg_mod.load()
    st = state_mod.load()
    tz = ZoneInfo(cfg.location.timezone)
    now = datetime.now(tz=tz)

    typer.secho("Configuration", bold=True)
    typer.echo(f"  config:   {cfg_mod.config_path()}")
    typer.echo(f"  state:    {state_mod.state_path()}")
    typer.echo(f"  bridge:   {cfg.bridge.ip}")
    typer.echo(
        f"  location: {cfg.location.latitude:.4f}, {cfg.location.longitude:.4f} "
        f"({cfg.location.timezone})"
    )
    typer.echo(f"  transition: {cfg.transitions.duration_ms}ms")
    typer.echo(
        f"  schedule: afternoon offset {cfg.schedule.afternoon_offset_minutes}min, "
        f"evening={cfg.schedule.evening_event}, night={cfg.schedule.night_event} "
        f"(cap {cfg.schedule.night_latest_local_time.strftime('%H:%M')})"
    )

    typer.secho("\nToday's events", bold=True)
    events = compute_events(now.date(), cfg.location, cfg.schedule)
    for ev in events:
        marker = " ←" if ev.when <= now and (
            ev is events[-1] or events[events.index(ev) + 1].when > now
        ) else ""
        typer.echo(f"  {ev.scene:10s} {ev.when.strftime('%H:%M:%S %Z')}{marker}")

    typer.secho("\nMonitored rooms", bold=True)
    if not cfg.rooms.monitored:
        typer.echo("  (none — run `sunsync-hue learn`)")
    for room_name in cfg.rooms.monitored:
        rs = st.rooms.get(room_name)
        if rs is None:
            typer.echo(f"  {room_name}: no scenes learned")
            continue
        scenes = ", ".join(sorted(rs.scenes.keys()))
        last = (
            f"{rs.last_applied.scene} at "
            f"{rs.last_applied.at.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}"
            if rs.last_applied
            else "never"
        )
        typer.echo(f"  {room_name}")
        typer.echo(f"    scenes:       {scenes or '(none)'}")
        typer.echo(f"    last applied: {last}")
