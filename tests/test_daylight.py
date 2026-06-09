from __future__ import annotations

from datetime import date, datetime, time, timedelta
import unittest
from zoneinfo import ZoneInfo

from astral import LocationInfo, SunDirection
from astral.sun import golden_hour

from sunsync_hue.config import LocationConfig, ScheduleConfig, SunOffsetsConfig
from sunsync_hue.daylight import compute_events


LOCATION = LocationConfig(
    latitude=-33.9075,
    longitude=151.1985,
    timezone="Australia/Sydney",
)
EVENT_DATE = date(2026, 5, 31)


def _events_by_scene(schedule: ScheduleConfig) -> dict[str, datetime]:
    return {ev.scene: ev.when for ev in compute_events(EVENT_DATE, LOCATION, schedule)}


class DaylightTests(unittest.TestCase):
    def test_afternoon_uses_start_of_setting_golden_hour(self) -> None:
        events = _events_by_scene(ScheduleConfig())
        info = LocationInfo(
            name="here",
            region="here",
            timezone=LOCATION.timezone,
            latitude=LOCATION.latitude,
            longitude=LOCATION.longitude,
        )

        expected = golden_hour(
            info.observer,
            date=EVENT_DATE,
            direction=SunDirection.SETTING,
            tzinfo=ZoneInfo(LOCATION.timezone),
        )[0]

        self.assertEqual(events["Afternoon"], expected)

    def test_sun_offsets_shift_only_sun_controlled_events(self) -> None:
        base = _events_by_scene(ScheduleConfig(night_local_time=time(23, 30)))
        shifted = _events_by_scene(
            ScheduleConfig(
                night_local_time=time(23, 30),
                sun_offsets=SunOffsetsConfig(day=20, afternoon=-15, evening=5),
            )
        )

        self.assertEqual(shifted["Day"], base["Day"] + timedelta(minutes=20))
        self.assertEqual(
            shifted["Afternoon"], base["Afternoon"] - timedelta(minutes=15)
        )
        self.assertEqual(shifted["Evening"], base["Evening"] + timedelta(minutes=5))
        self.assertEqual(shifted["Night"], base["Night"])

    def test_evening_offset_affects_next_day_night_rollover(self) -> None:
        base_evening = _events_by_scene(ScheduleConfig())["Evening"]
        early_night = time(base_evening.hour, base_evening.minute)
        events = _events_by_scene(
            ScheduleConfig(
                night_local_time=early_night,
                sun_offsets=SunOffsetsConfig(evening=10),
            )
        )

        self.assertEqual(events["Night"].date(), EVENT_DATE + timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
