from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import dusk, sun

from sunsync_hue.config import LocationConfig, ScheduleConfig

SCENE_ORDER = ("Day", "Afternoon", "Evening", "Night")


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

    # Day
    day_at = sunrise

    # Afternoon — sunset minus configurable offset
    afternoon_at = sunset - timedelta(minutes=schedule.afternoon_offset_minutes)

    # Evening
    if schedule.evening_event == "civil_dusk":
        evening_at = s["dusk"]  # astral's "dusk" defaults to civil (depression=6)
    else:  # "sunset"
        evening_at = sunset

    # Night — astronomical_dusk (depression=18) or civil_dusk or sunset, capped
    if schedule.night_event == "astronomical_dusk":
        try:
            night_at = dusk(info.observer, date=on_date, tzinfo=tz, depression=18)
        except ValueError:
            # At extreme latitudes/dates astral can fail; fall back to the cap.
            night_at = datetime.combine(on_date, schedule.night_latest_local_time, tz)
    elif schedule.night_event == "civil_dusk":
        night_at = dusk(info.observer, date=on_date, tzinfo=tz, depression=6)
    else:  # "sunset"
        night_at = sunset

    cap = datetime.combine(on_date, schedule.night_latest_local_time, tz)
    if night_at > cap:
        night_at = cap

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
