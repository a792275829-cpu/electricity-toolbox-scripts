from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOL_DIR.parent
for entry in (str(ROOT), str(TOOL_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from gd_power_report.workday_calendar import (  # noqa: E402
    format_date_status, is_workday,
)


class WorkdayTests(unittest.TestCase):
    def test_configured_holidays_are_not_workdays(self):
        for value in (date(2026, 1, 1), date(2026, 10, 1), date(2026, 10, 7),
                      date(2026, 2, 16), date(2026, 5, 1)):
            with self.subTest(value=value):
                self.assertFalse(is_workday(value))

    def test_weekday_outside_holidays_is_a_workday(self):
        self.assertTrue(is_workday(date(2026, 10, 8)))
        self.assertTrue(is_workday(date(2026, 3, 2)))

    def test_adjusted_working_weekends_count_as_workdays(self):
        for value in (date(2026, 1, 4), date(2026, 2, 14),
                      date(2026, 5, 9), date(2026, 10, 10)):
            with self.subTest(value=value):
                self.assertTrue(is_workday(value))
                self.assertEqual(format_date_status(value).split("，")[-1], "调休工作日（补班）")

    def test_plain_weekend_is_not_a_workday(self):
        self.assertFalse(is_workday(date(2026, 10, 11)))
        self.assertFalse(is_workday(date(2026, 10, 17)))

    def test_unconfigured_year_falls_back_to_weekday(self):
        self.assertTrue(is_workday(date(2027, 3, 1)))
        self.assertFalse(is_workday(date(2027, 3, 6)))
        self.assertEqual(format_date_status(date(2027, 3, 1)).split("，")[-1], "日历待更新")


class DateStatusTextTests(unittest.TestCase):
    def test_holiday_wording(self):
        self.assertEqual(format_date_status(date(2026, 10, 1)), "10月1日，周四，节假日")

    def test_workday_and_weekend_wording(self):
        self.assertEqual(format_date_status(date(2026, 10, 8)), "10月8日，周四，工作日")
        self.assertEqual(format_date_status(date(2026, 10, 11)), "10月11日，周日，周末休息日")

    def test_unconfigured_year_is_marked(self):
        self.assertEqual(format_date_status(date(2028, 1, 1)), "1月1日，周六，日历待更新")


if __name__ == "__main__":
    unittest.main()
