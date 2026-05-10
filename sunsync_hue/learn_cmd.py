from __future__ import annotations

import logging
import time

import questionary
import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod
from sunsync_hue.bridge import BridgeClient, Room
from sunsync_hue.config import SCENE_NAMES
from sunsync_hue.exclusion import excluded_ids_for_room
from sunsync_hue.snapshot import snapshot_room

logger = logging.getLogger(__name__)


def run(room: str | None = None) -> None:
    cfg = cfg_mod.load()
    st = state_mod.load()

    with BridgeClient(cfg.bridge.ip, cfg.bridge.app_key) as bridge:
        rooms = bridge.get_rooms()
        if not rooms:
            typer.secho(
                "No rooms found on the bridge. Set up rooms in the Hue app first.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

        rooms_with_lights = [r for r in rooms if r.light_ids]
        if not rooms_with_lights:
            typer.secho(
                "No rooms contain any lights.", fg=typer.colors.RED
            )
            raise typer.Exit(code=1)

        room_by_name: dict[str, Room] = {r.name: r for r in rooms_with_lights}

        if room is not None:
            _learn_single(room, bridge, room_by_name, rooms, cfg, st)
            return

        # 1. Pick rooms to monitor (prefill from current config)
        choices = [
            questionary.Choice(
                title=f"{r.name}  ({len(r.light_ids)} lights)",
                value=r.name,
                checked=(r.name in cfg.rooms.monitored),
            )
            for r in rooms_with_lights
        ]
        selected: list[str] | None = questionary.checkbox(
            "Which rooms should sunsync-hue manage?",
            choices=choices,
        ).ask()
        if not selected:
            typer.echo("No rooms selected — nothing to learn.")
            raise typer.Exit(code=0)

        # 2. For each room x scene, capture state.
        for room_name in selected:
            _learn_one_room(bridge, room_by_name[room_name], cfg, st)

        # 3. Drop any previously-monitored rooms the user deselected.
        for known in list(st.rooms.keys()):
            if known not in selected:
                typer.echo(f"Removing previously-monitored room: {known}")
                del st.rooms[known]

    # 4. Persist.
    cfg.rooms.monitored = list(selected)
    cfg_mod.save(cfg)
    state_mod.save(st)
    typer.secho(
        f"\nDone. Monitoring {len(selected)} room(s). "
        "Run `sunsync-hue check` to verify.",
        fg=typer.colors.GREEN,
    )


def _learn_single(
    room_name: str,
    bridge: BridgeClient,
    room_by_name: dict[str, Room],
    all_rooms: list[Room],
    cfg: cfg_mod.Config,
    st: state_mod.State,
) -> None:
    """Single-room re-learn. Validates, optionally adds to monitored, captures scenes."""
    if room_name not in room_by_name:
        # Distinguish "exists on bridge but has no lights" from "not on bridge at all".
        bridge_names = [r.name for r in all_rooms]
        if room_name in bridge_names:
            typer.secho(
                f"Room {room_name!r} contains no lights — nothing to learn.",
                fg=typer.colors.RED,
            )
        else:
            available = ", ".join(r.name for r in room_by_name.values()) or "(none)"
            typer.secho(
                f"Room {room_name!r} not found on bridge. Available: {available}",
                fg=typer.colors.RED,
            )
        raise typer.Exit(code=1)

    newly_monitored = False
    if room_name not in cfg.rooms.monitored:
        confirmed = questionary.confirm(
            f"{room_name!r} isn't currently monitored — add it?",
            default=True,
        ).ask()
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(code=0)
        newly_monitored = True

    _learn_one_room(bridge, room_by_name[room_name], cfg, st)

    if newly_monitored:
        cfg.rooms.monitored.append(room_name)

    cfg_mod.save(cfg)
    state_mod.save(st)
    typer.secho(
        f"\nDone. Updated {room_name}. Run `sunsync-hue check` to verify.",
        fg=typer.colors.GREEN,
    )


def _learn_one_room(
    bridge: BridgeClient,
    room: Room,
    cfg: cfg_mod.Config,
    st: state_mod.State,
) -> None:
    """Run the per-room flow: light-exclusion checkbox + 4-scene capture, write state."""
    room_name = room.name
    typer.secho(f"\n=== {room_name} ===", fg=typer.colors.CYAN, bold=True)

    # Per-room: pick which lights to include (uncheck to exclude).
    lights_in_room = bridge.get_room_lights(room)
    current_excluded = cfg.rooms.excluded.get(room_name, [])
    light_choices = [
        questionary.Choice(
            title=ls.name,
            value=ls.name,
            checked=(ls.name not in current_excluded),
        )
        for ls in lights_in_room.values()
    ]
    included_names: list[str] | None = questionary.checkbox(
        f"Which lights in '{room_name}' should sunsync-hue manage? "
        "(uncheck to exclude)",
        choices=light_choices,
    ).ask()
    if included_names is None:
        typer.echo("Aborted.")
        raise typer.Exit(code=1)
    included_set = set(included_names)
    new_excluded = [
        ls.name for ls in lights_in_room.values()
        if ls.name not in included_set
    ]
    if new_excluded:
        cfg.rooms.excluded[room_name] = new_excluded
        typer.echo(f"  excluding: {', '.join(new_excluded)}")
    else:
        cfg.rooms.excluded.pop(room_name, None)

    existing = st.rooms.get(room_name)
    scenes_so_far = (
        dict(existing.scenes) if existing and existing.scenes else {}
    )

    for scene_name in SCENE_NAMES:
        already = scene_name in scenes_so_far
        prompt = (
            f"Set up '{room_name}' for {scene_name} in your Hue app, "
            "then press Enter to capture"
        )
        if already:
            prompt += "  (Enter to recapture, 's' to skip)"
        response = questionary.text(prompt).ask()
        if response is None:
            typer.echo("Aborted.")
            raise typer.Exit(code=1)
        if already and response.strip().lower() == "s":
            typer.echo(f"  → kept existing {scene_name}")
            continue

        # Settle for a moment in case the user just tapped a switch.
        time.sleep(0.5)
        lights = bridge.get_room_lights(room)
        excluded_ids = excluded_ids_for_room(lights, new_excluded)
        managed = {lid: ls for lid, ls in lights.items() if lid not in excluded_ids}
        snap = snapshot_room(managed)
        scenes_so_far[scene_name] = snap
        typer.secho(
            f"  → captured {scene_name} ({len(snap)} lights)",
            fg=typer.colors.GREEN,
        )

    st.rooms[room_name] = state_mod.RoomState(
        room_id=room.id,
        scenes=scenes_so_far,
        last_applied=existing.last_applied if existing else None,
    )
