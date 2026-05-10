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
    evening_local_time: time = time(22, 0)
    # Treated as next-day if it falls at-or-before evening_local_time
    # (e.g. 01:00 means 01:00 the following morning).
    night_local_time: time = time(1, 0)


@dataclass
class MatchingConfig:
    brightness_tolerance: float = 3.0
    xy_tolerance: float = 0.02
    mirek_tolerance: int = 10


@dataclass
class RoomsConfig:
    monitored: list[str] = field(default_factory=list)
    # Per-room exclusion lists, keyed by room name. Light identification is
    # by Hue light name; if the user renames a light in the Hue app the
    # exclusion silently lapses (intended — we resolve names to IDs fresh
    # on every command).
    excluded: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class WebhookConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8765
    coming_home_window_seconds: int = 900


@dataclass
class Config:
    bridge: BridgeConfig
    location: LocationConfig
    transitions: TransitionConfig
    schedule: ScheduleConfig
    matching: MatchingConfig
    rooms: RoomsConfig
    webhook: WebhookConfig

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
        evening_local_time=_parse_time_str(
            sched_t.get("evening_local_time", "22:00"), "schedule"
        ),
        night_local_time=_parse_time_str(
            sched_t.get("night_local_time", "01:00"), "schedule"
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

    excluded_t = rooms_t.get("excluded", {})
    if not isinstance(excluded_t, dict):
        raise ConfigError("[rooms.excluded] must be a table mapping room → list of light names")
    excluded: dict[str, list[str]] = {}
    for room_name, lights in excluded_t.items():
        if not isinstance(lights, list) or not all(isinstance(n, str) for n in lights):
            raise ConfigError(
                f"[rooms.excluded].{room_name!r} must be a list of light name strings"
            )
        if lights:
            excluded[room_name] = list(lights)

    rooms = RoomsConfig(monitored=list(monitored), excluded=excluded)

    webhook_t = raw.get("webhook", {})
    webhook = WebhookConfig(
        enabled=bool(webhook_t.get("enabled", False)),
        host=str(webhook_t.get("host", "0.0.0.0")),
        port=int(webhook_t.get("port", 8765)),
        coming_home_window_seconds=int(
            webhook_t.get("coming_home_window_seconds", 900)
        ),
    )

    if transitions.duration_ms < 0 or transitions.duration_ms > 600_000:
        raise ConfigError(
            f"[transitions].duration_ms out of range (0–600000): {transitions.duration_ms}"
        )
    if webhook.port < 1 or webhook.port > 65535:
        raise ConfigError(
            f"[webhook].port out of range (1–65535): {webhook.port}"
        )
    if webhook.coming_home_window_seconds < 1:
        raise ConfigError(
            "[webhook].coming_home_window_seconds must be at least 1"
        )

    return Config(
        bridge=bridge,
        location=location,
        transitions=transitions,
        schedule=schedule,
        matching=matching,
        rooms=rooms,
        webhook=webhook,
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
    elt = cfg.schedule.evening_local_time
    nlt = cfg.schedule.night_local_time
    lines.append(f'evening_local_time = "{elt.hour:02d}:{elt.minute:02d}"')
    lines.append(f'night_local_time = "{nlt.hour:02d}:{nlt.minute:02d}"')
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
    excluded_rooms = {r: ls for r, ls in cfg.rooms.excluded.items() if ls}
    if excluded_rooms:
        lines.append("[rooms.excluded]")
        for room_name in sorted(excluded_rooms):
            lights = ", ".join(f'"{n}"' for n in excluded_rooms[room_name])
            lines.append(f'"{room_name}" = [{lights}]')
        lines.append("")
    lines.append("[webhook]")
    lines.append(f"enabled = {str(cfg.webhook.enabled).lower()}")
    lines.append(f'host = "{cfg.webhook.host}"')
    lines.append(f"port = {cfg.webhook.port}")
    lines.append(
        f"coming_home_window_seconds = {cfg.webhook.coming_home_window_seconds}"
    )
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
        webhook=WebhookConfig(),
    )
