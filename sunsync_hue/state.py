from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sunsync_hue.config import state_path

SCHEMA_VERSION = 1


@dataclass
class LightSnapshot:
    on: bool
    brightness: float | None  # 0..100; None when off (we don't compare it)
    color_mode: str  # "xy" | "ct" | "none"
    xy: tuple[float, float] | None
    mirek: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "on": self.on,
            "brightness": self.brightness,
            "color_mode": self.color_mode,
            "xy": list(self.xy) if self.xy is not None else None,
            "mirek": self.mirek,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LightSnapshot:
        xy = d.get("xy")
        return cls(
            on=bool(d["on"]),
            brightness=None if d.get("brightness") is None else float(d["brightness"]),
            color_mode=str(d.get("color_mode", "none")),
            xy=(float(xy[0]), float(xy[1])) if xy else None,
            mirek=None if d.get("mirek") is None else int(d["mirek"]),
        )


@dataclass
class LastApplied:
    scene: str
    at: datetime  # timezone-aware

    def to_dict(self) -> dict[str, Any]:
        return {"scene": self.scene, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LastApplied:
        return cls(scene=str(d["scene"]), at=datetime.fromisoformat(d["at"]))


@dataclass
class RoomState:
    room_id: str
    scenes: dict[str, dict[str, LightSnapshot]] = field(default_factory=dict)
    # scenes: { scene_name: { light_id: LightSnapshot } }
    last_applied: LastApplied | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "scenes": {
                scene: {lid: snap.to_dict() for lid, snap in lights.items()}
                for scene, lights in self.scenes.items()
            },
            "last_applied": (
                self.last_applied.to_dict() if self.last_applied else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RoomState:
        scenes = {
            scene: {
                lid: LightSnapshot.from_dict(snap) for lid, snap in lights.items()
            }
            for scene, lights in d.get("scenes", {}).items()
        }
        last = d.get("last_applied")
        return cls(
            room_id=str(d["room_id"]),
            scenes=scenes,
            last_applied=LastApplied.from_dict(last) if last else None,
        )


@dataclass
class State:
    rooms: dict[str, RoomState] = field(default_factory=dict)  # keyed by room name

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "rooms": {name: room.to_dict() for name, room in self.rooms.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> State:
        version = d.get("schema_version", 1)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported state schema version {version} (expected {SCHEMA_VERSION})"
            )
        rooms = {
            name: RoomState.from_dict(room) for name, room in d.get("rooms", {}).items()
        }
        return cls(rooms=rooms)


def load(path: Path | None = None) -> State:
    p = path or state_path()
    if not p.exists():
        return State()
    with p.open("r") as f:
        return State.from_dict(json.load(f))


def save(state: State, path: Path | None = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".state.", suffix=".json.tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state.to_dict(), f, indent=2, sort_keys=True)
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
