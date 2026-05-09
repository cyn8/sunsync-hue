from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

CONFIG_FILENAME = "config.toml"
APP_DIR = "sunsync-hue"

SCENE_NAMES = ("Day", "Afternoon", "Evening", "Night")


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_DIR / CONFIG_FILENAME


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / APP_DIR / "state.json"


class ConfigError(ValueError):
    pass


@dataclass
class BridgeConfig:
    ip: str
    app_key: str


@dataclass
class LocationConfig:
    latitude: float
    longitude: float
    timezone: str
    elevation: float = 0.0


@dataclass
class TransitionConfig:
    duration_ms: int = 30_000


@dataclass
class ScheduleConfig:
    afternoon_offset_minutes: int = 90
    evening_event: str = "sunset"
    night_event: str = "astronomical_dusk"
    night_latest_local_time: time = time(22, 30)


@dataclass
class MatchingConfig:
    brightness_tolerance: float = 3.0
    xy_tolerance: float = 0.02
    mirek_tolerance: int = 10


@dataclass
class RoomsConfig:
    monitored: list[str] = field(default_factory=list)


@dataclass
class Config:
    bridge: BridgeConfig
    location: LocationConfig
    transitions: TransitionConfig
    schedule: ScheduleConfig
    matching: MatchingConfig
    rooms: RoomsConfig

    @property
    def path(self) -> Path:
        return config_path()


def _require(table: dict, key: str, where: str) -> object:
    if key not in table:
        raise ConfigError(f"missing required key '{key}' in [{where}]")
    return table[key]


def _parse_time_str(value: str, where: str) -> time:
    try:
        hh, mm = value.split(":", 1)
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as e:
        raise ConfigError(
            f"invalid time '{value}' in [{where}] — expected HH:MM"
        ) from e


def parse(raw: dict) -> Config:
    bridge_t = raw.get("bridge", {})
    bridge = BridgeConfig(
        ip=str(_require(bridge_t, "ip", "bridge")),
        app_key=str(_require(bridge_t, "app_key", "bridge")),
    )

    loc_t = raw.get("location", {})
    location = LocationConfig(
        latitude=float(_require(loc_t, "latitude", "location")),
        longitude=float(_require(loc_t, "longitude", "location")),
        timezone=str(_require(loc_t, "timezone", "location")),
        elevation=float(loc_t.get("elevation", 0.0)),
    )

    trans_t = raw.get("transitions", {})
    transitions = TransitionConfig(
        duration_ms=int(trans_t.get("duration_ms", 30_000)),
    )

    sched_t = raw.get("schedule", {})
    schedule = ScheduleConfig(
        afternoon_offset_minutes=int(sched_t.get("afternoon_offset_minutes", 90)),
        evening_event=str(sched_t.get("evening_event", "sunset")),
        night_event=str(sched_t.get("night_event", "astronomical_dusk")),
        night_latest_local_time=_parse_time_str(
            sched_t.get("night_latest_local_time", "22:30"), "schedule"
        ),
    )

    match_t = raw.get("matching", {})
    matching = MatchingConfig(
        brightness_tolerance=float(match_t.get("brightness_tolerance", 3.0)),
        xy_tolerance=float(match_t.get("xy_tolerance", 0.02)),
        mirek_tolerance=int(match_t.get("mirek_tolerance", 10)),
    )

    rooms_t = raw.get("rooms", {})
    monitored = rooms_t.get("monitored", [])
    if not isinstance(monitored, list) or not all(isinstance(r, str) for r in monitored):
        raise ConfigError("[rooms].monitored must be a list of strings")
    rooms = RoomsConfig(monitored=list(monitored))

    if schedule.evening_event not in ("sunset", "civil_dusk"):
        raise ConfigError(
            f"[schedule].evening_event must be 'sunset' or 'civil_dusk', "
            f"got {schedule.evening_event!r}"
        )
    if schedule.night_event not in ("astronomical_dusk", "civil_dusk", "sunset"):
        raise ConfigError(
            f"[schedule].night_event must be one of "
            f"'astronomical_dusk' | 'civil_dusk' | 'sunset', "
            f"got {schedule.night_event!r}"
        )
    if transitions.duration_ms < 0 or transitions.duration_ms > 600_000:
        raise ConfigError(
            f"[transitions].duration_ms out of range (0–600000): {transitions.duration_ms}"
        )

    return Config(
        bridge=bridge,
        location=location,
        transitions=transitions,
        schedule=schedule,
        matching=matching,
        rooms=rooms,
    )


def load(path: Path | None = None) -> Config:
    p = path or config_path()
    if not p.exists():
        raise ConfigError(
            f"config not found at {p} — run `sunsync-hue setup` to create it"
        )
    with p.open("rb") as f:
        raw = tomllib.load(f)
    return parse(raw)


def render(cfg: Config) -> str:
    """Render config as a TOML string. Hand-rolled to avoid an extra dependency."""
    lines: list[str] = []
    lines.append("[bridge]")
    lines.append(f'ip = "{cfg.bridge.ip}"')
    lines.append(f'app_key = "{cfg.bridge.app_key}"')
    lines.append("")
    lines.append("[location]")
    lines.append(f"latitude = {cfg.location.latitude}")
    lines.append(f"longitude = {cfg.location.longitude}")
    lines.append(f'timezone = "{cfg.location.timezone}"')
    lines.append(f"elevation = {cfg.location.elevation}")
    lines.append("")
    lines.append("[transitions]")
    lines.append(f"duration_ms = {cfg.transitions.duration_ms}")
    lines.append("")
    lines.append("[schedule]")
    lines.append(f"afternoon_offset_minutes = {cfg.schedule.afternoon_offset_minutes}")
    lines.append(f'evening_event = "{cfg.schedule.evening_event}"')
    lines.append(f'night_event = "{cfg.schedule.night_event}"')
    nlt = cfg.schedule.night_latest_local_time
    lines.append(f'night_latest_local_time = "{nlt.hour:02d}:{nlt.minute:02d}"')
    lines.append("")
    lines.append("[matching]")
    lines.append(f"brightness_tolerance = {cfg.matching.brightness_tolerance}")
    lines.append(f"xy_tolerance = {cfg.matching.xy_tolerance}")
    lines.append(f"mirek_tolerance = {cfg.matching.mirek_tolerance}")
    lines.append("")
    lines.append("[rooms]")
    monitored = ", ".join(f'"{r}"' for r in cfg.rooms.monitored)
    lines.append(f"monitored = [{monitored}]")
    lines.append("")
    return "\n".join(lines)


def save(cfg: Config, path: Path | None = None) -> None:
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(cfg))


def default_for_pairing(
    bridge_ip: str,
    app_key: str,
    latitude: float,
    longitude: float,
    timezone: str,
) -> Config:
    return Config(
        bridge=BridgeConfig(ip=bridge_ip, app_key=app_key),
        location=LocationConfig(
            latitude=latitude, longitude=longitude, timezone=timezone
        ),
        transitions=TransitionConfig(),
        schedule=ScheduleConfig(),
        matching=MatchingConfig(),
        rooms=RoomsConfig(),
    )
