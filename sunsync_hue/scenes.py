from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sunsync_hue.config import learned_scenes_path, state_path
from sunsync_hue.state import LightSnapshot
from sunsync_hue import state as state_mod

SCHEMA_VERSION = 1


@dataclass
class LearnedRoom:
    room_id: str
    scenes: dict[str, dict[str, LightSnapshot]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "scenes": {
                scene: {lid: snap.to_dict() for lid, snap in lights.items()}
                for scene, lights in self.scenes.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LearnedRoom:
        scenes = {
            scene: {
                lid: LightSnapshot.from_dict(snap) for lid, snap in lights.items()
            }
            for scene, lights in d.get("scenes", {}).items()
        }
        return cls(room_id=str(d["room_id"]), scenes=scenes)


@dataclass
class LearnedScenes:
    rooms: dict[str, LearnedRoom] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "rooms": {name: room.to_dict() for name, room in self.rooms.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LearnedScenes:
        version = d.get("schema_version", 1)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported scenes schema version {version} "
                f"(expected {SCHEMA_VERSION})"
            )
        rooms = {
            name: LearnedRoom.from_dict(room)
            for name, room in d.get("rooms", {}).items()
        }
        return cls(rooms=rooms)


def load(path: Path | None = None) -> LearnedScenes:
    p = path or learned_scenes_path()
    if not p.exists():
        migrated = _migrate_from_old_state(p)
        if migrated is not None:
            return migrated
        return LearnedScenes()
    with p.open("r") as f:
        return LearnedScenes.from_dict(json.load(f))


def save(scenes: LearnedScenes, path: Path | None = None) -> None:
    p = path or learned_scenes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".scenes.", suffix=".json.tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(scenes.to_dict(), f, indent=2, sort_keys=True)
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _migrate_from_old_state(new_path: Path) -> LearnedScenes | None:
    old_path = state_path()
    if not old_path.exists():
        return None

    with old_path.open("r") as f:
        raw = json.load(f)

    learned_rooms: dict[str, LearnedRoom] = {}
    for room_name, room in raw.get("rooms", {}).items():
        scenes_raw = room.get("scenes", {})
        if not scenes_raw:
            continue
        learned_rooms[room_name] = LearnedRoom.from_dict(
            {
                "room_id": room["room_id"],
                "scenes": scenes_raw,
            }
        )

    if not learned_rooms:
        return None

    learned = LearnedScenes(rooms=learned_rooms)
    save(learned, new_path)

    # Rewrite runtime state in the new shape, preserving last_applied and
    # dropping the formerly embedded learned scenes.
    state_mod.save(state_mod.load())
    return learned
