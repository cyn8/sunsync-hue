from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from sunsync_hue.config import LocationConfig, ScheduleConfig

SCENE_ORDER = ("Day", "Afternoon", "Evening", "Night")


def previous_scene(scene: str) -> str:
    """The scene that should precede `scene` in chronological order (wraps Day←Night)."""
    i = SCENE_ORDER.index(scene)
    return SCENE_ORDER[(i - 1) % len(SCENE_ORDER)]


@dataclass
class DaylightEvent:
    scene: str
    when: datetime  # timezone-aware


def _location(loc: LocationConfig) -> tuple[LocationInfo, ZoneInfo]:
    tz = ZoneInfo(loc.timezone)
    info = LocationInfo(
        name="here",
        region="here",
        timezone=loc.timezone,
        latitude=loc.latitude,
        longitude=loc.longitude,
    )
    return info, tz


def compute_events(
    on_date: date,
    location: LocationConfig,
    schedule: ScheduleConfig,
) -> list[DaylightEvent]:
    """Return the four scene events for the given local date, in chronological order.

    Events are timezone-aware (zoneinfo). All times are local to `location.timezone`.
    """
    info, tz = _location(location)

    s = sun(info.observer, date=on_date, tzinfo=tz)
    sunrise = s["sunrise"]
    sunset = s["sunset"]

    day_at = sunrise
    afternoon_at = sunset
    evening_at = datetime.combine(on_date, schedule.evening_local_time, tz)
    night_at = datetime.combine(on_date, schedule.night_local_time, tz)

    # If night_local_time is at/before evening (e.g. 01:00 vs 22:00) it belongs
    # to the next morning, after evening has fired.
    if night_at <= evening_at:
        night_at = night_at + timedelta(days=1)

    events = [
        DaylightEvent("Day", day_at),
        DaylightEvent("Afternoon", afternoon_at),
        DaylightEvent("Evening", evening_at),
        DaylightEvent("Night", night_at),
    ]
    events.sort(key=lambda e: e.when)
    return events


def upcoming_events(
    now: datetime,
    location: LocationConfig,
    schedule: ScheduleConfig,
    *,
    horizon_days: int = 2,
) -> list[DaylightEvent]:
    """All events strictly after `now` within `horizon_days` (today inclusive)."""
    tz = ZoneInfo(location.timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    today = now.astimezone(tz).date()

    out: list[DaylightEvent] = []
    for offset in range(horizon_days + 1):
        d = today + timedelta(days=offset)
        for ev in compute_events(d, location, schedule):
            if ev.when > now:
                out.append(ev)
    return out


def current_scene_for(
    now: datetime,
    location: LocationConfig,
    schedule: ScheduleConfig,
) -> str:
    """Which scene "should" the lights be in right now (latest event whose time has passed)?

    Used by `--catch-up`. Walks backward through today's and yesterday's events
    and returns the most recent one that has already fired.
    """
    tz = ZoneInfo(location.timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    today = now.astimezone(tz).date()

    candidates: list[DaylightEvent] = []
    for offset in (-1, 0):
        d = today + timedelta(days=offset)
        candidates.extend(compute_events(d, location, schedule))
    candidates = [e for e in candidates if e.when <= now]
    candidates.sort(key=lambda e: e.when)
    if not candidates:
        return "Night"  # extreme edge case; safest fallback
    return candidates[-1].scene
