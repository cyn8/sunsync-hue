from __future__ import annotations

import logging
import sys
import time
import warnings

import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import discovery
from sunsync_hue.logging_setup import setup_logging

# Bridge uses self-signed cert; suppress the noise.
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

app = typer.Typer(
    add_completion=False,
    help="Non-destructive Philips Hue scene daemon driven by actual daylight.",
    no_args_is_help=True,
)

logger = logging.getLogger("sunsync_hue.cli")


@app.callback()
def _root(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    setup_logging(verbose=verbose)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


@app.command()
def setup(
    ip: str = typer.Option(
        None,
        "--ip",
        help="Bridge IP. If omitted, the bridge is discovered on the LAN.",
    ),
    latitude: float = typer.Option(
        -33.9075, "--latitude", help="Latitude for daylight calculations."
    ),
    longitude: float = typer.Option(
        151.1985, "--longitude", help="Longitude for daylight calculations."
    ),
    timezone: str = typer.Option(
        "Australia/Sydney", "--timezone", help="IANA timezone name."
    ),
) -> None:
    """One-time bridge pairing. Press the link button on top of the bridge first."""
    if ip is None:
        typer.echo("Discovering Hue bridges on your network…")
        bridges = discovery.discover()
        if not bridges:
            typer.secho(
                "No bridges found. Provide --ip manually.", fg=typer.colors.RED
            )
            raise typer.Exit(code=1)
        if len(bridges) == 1:
            chosen = bridges[0]
            typer.echo(f"Found bridge at {chosen.ip}")
        else:
            import questionary

            choice = questionary.select(
                "Multiple bridges found — choose one:",
                choices=[
                    f"{b.ip}" + (f"  ({b.id})" if b.id else "") for b in bridges
                ],
            ).ask()
            if not choice:
                raise typer.Exit(code=1)
            ip_only = choice.split()[0]
            chosen = next(b for b in bridges if b.ip == ip_only)
        ip = chosen.ip

    typer.echo("\nPress the link button on top of the Hue bridge,")
    typer.echo("then press Enter here within 30 seconds…")
    input()

    # Try a few times in case the user pressed Enter slightly before the button.
    last_err: discovery.BridgePairingError | None = None
    for attempt in range(5):
        try:
            app_key = discovery.pair(ip)
            break
        except discovery.BridgePairingError as e:
            last_err = e
            if e.link_button_not_pressed and attempt < 4:
                typer.echo("Link button not detected, retrying in 2s…")
                time.sleep(2)
                continue
            typer.secho(f"Pairing failed: {e}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from e
    else:
        typer.secho(f"Pairing failed: {last_err}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    config = cfg_mod.default_for_pairing(
        bridge_ip=ip,
        app_key=app_key,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
    )
    cfg_mod.save(config)
    typer.secho(
        f"\nSaved config to {cfg_mod.config_path()}", fg=typer.colors.GREEN
    )
    typer.echo("Next: run `sunsync-hue learn` to capture your scenes.")


# ---------------------------------------------------------------------------
# Stub commands — filled in by later modules
# ---------------------------------------------------------------------------


@app.command()
def learn(
    room: str = typer.Argument(
        None,
        help="Room name to re-learn. If omitted, opens the interactive picker for all rooms.",
    ),
) -> None:
    """Interactively capture per-room scene snapshots."""
    from sunsync_hue.learn_cmd import run as run_learn

    run_learn(room=room)


@app.command()
def check() -> None:
    """Run the match check for every monitored room and print the verdict."""
    from sunsync_hue.check_cmd import run as run_check

    run_check()


@app.command()
def apply(
    scene: str = typer.Argument(..., help="Scene name (Day/Afternoon/Evening/Night)."),
    room: str = typer.Option(None, "--room", "-r", help="Limit to one room."),
    force: bool = typer.Option(
        False, "--force", help="Skip the match check and apply unconditionally."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Log what would happen without writing."
    ),
) -> None:
    """Manually apply a scene (still respects the match check unless --force)."""
    from sunsync_hue.apply_cmd import run as run_apply

    run_apply(scene=scene, room=room, force=force, dry_run=dry_run)


@app.command()
def run(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Log what would happen without writing."
    ),
    catch_up: bool = typer.Option(
        False,
        "--catch-up",
        help="On startup, identify the current scene by clock and apply it "
        "(still respecting the match check).",
    ),
) -> None:
    """Run the daemon."""
    from sunsync_hue.scheduler import run as run_daemon

    run_daemon(dry_run=dry_run, catch_up=catch_up)


@app.command()
def status() -> None:
    """Print parsed config, today's event times, and last-applied per room."""
    from sunsync_hue.status_cmd import run as run_status

    run_status()


@app.command()
def forget(
    room: str = typer.Argument(..., help="Room name to remove from monitoring."),
) -> None:
    """Remove a room from monitoring and drop its snapshots."""
    from sunsync_hue.forget_cmd import run as run_forget

    run_forget(room)


@app.command()
def exclude(
    room: str = typer.Argument(..., help="Room the light belongs to."),
    light: str = typer.Argument(..., help="Hue light name to exclude."),
) -> None:
    """Exclude a light from sunsync-hue's management for a room."""
    from sunsync_hue.exclude_cmd import run_exclude

    run_exclude(room, light)


@app.command()
def include(
    room: str = typer.Argument(..., help="Room the light belongs to."),
    light: str = typer.Argument(..., help="Hue light name to re-include."),
) -> None:
    """Re-include a previously excluded light."""
    from sunsync_hue.exclude_cmd import run_include

    run_include(room, light)


def main() -> None:
    try:
        app()
    except cfg_mod.ConfigError as e:
        typer.secho(f"Config error: {e}", fg=typer.colors.RED, err=True)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
