from __future__ import annotations

import tomllib
import unittest

from sunsync_hue.config import ConfigError, parse, render


def _raw_config(extra: str = "") -> dict:
    return tomllib.loads(
        f"""
[bridge]
ip = "192.0.2.10"
app_key = "app-key"

[location]
latitude = -33.9075
longitude = 151.1985
timezone = "Australia/Sydney"

{extra}
"""
    )


class ConfigTests(unittest.TestCase):
    def test_missing_sun_offsets_defaults_to_zero(self) -> None:
        cfg = parse(_raw_config())

        self.assertEqual(cfg.schedule.sun_offsets.day, 0)
        self.assertEqual(cfg.schedule.sun_offsets.afternoon, 0)
        self.assertEqual(cfg.schedule.sun_offsets.evening, 0)

    def test_signed_sun_offsets_parse_as_minutes(self) -> None:
        cfg = parse(
            _raw_config(
                """
[schedule.sun_offsets]
day = +20
afternoon = -15
evening = 0
"""
            )
        )

        self.assertEqual(cfg.schedule.sun_offsets.day, 20)
        self.assertEqual(cfg.schedule.sun_offsets.afternoon, -15)
        self.assertEqual(cfg.schedule.sun_offsets.evening, 0)

    def test_non_integer_sun_offset_is_invalid(self) -> None:
        with self.assertRaises(ConfigError):
            parse(
                _raw_config(
                    """
[schedule.sun_offsets]
afternoon = "+20"
"""
                )
            )

    def test_render_includes_sun_offsets_table(self) -> None:
        rendered = render(parse(_raw_config()))

        self.assertIn("[schedule.sun_offsets]", rendered)
        self.assertIn("day = 0", rendered)
        self.assertIn("afternoon = 0", rendered)
        self.assertIn("evening = 0", rendered)


if __name__ == "__main__":
    unittest.main()
