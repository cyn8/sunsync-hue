from __future__ import annotations

import logging
import time

import questionary
import typer

from sunsync_hue import config as cfg_mod
from sunsync_hue import state as state_mod
from sunsync_hue.bridge import BridgeClient, Room
from sunsync_hue.config import SCENE_NAMES
from sunsync_hue.snapshot import snapshot_room

logger = logging.getLogger(__name__)


def run() -> None:
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

        room_by_name: dict[str, Room] = {r.name: r for r in rooms_with_lights}

        # 2. For each room x scene, capture state.
        for room_name in selected:
            room = room_by_name[room_name]
            typer.secho(f"\n=== {room_name} ===", fg=typer.colors.CYAN, bold=True)

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
                snap = snapshot_room(lights)
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
