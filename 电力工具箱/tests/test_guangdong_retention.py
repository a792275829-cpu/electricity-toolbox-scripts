"""Rolling-year bounds for the persistent forecast master, including API gaps."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pandas.testing import assert_frame_equal

from toolbox.runtime import load_module

ROOT = Path(__file__).resolve().parents[2]


class MasterRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module('forecast_retention', ROOT / '广东电价预测/update_gdfire_data.py')

    def curve(self, start, end):
        frame = self.module.empty_time_frame(start, end).drop(columns=['时点序号'])
        frame['全省日前平均电价'] = 300.
        frame['日前统调负荷(MW)'] = 60000.
        return frame

    def test_calendar_year_includes_d_and_handles_leap_day(self):
        for end, start, days in (
            ('2026-11-04', '2025-11-05', 365),
            ('2024-03-01', '2023-03-02', 366),
            ('2024-02-29', '2023-03-01', 366),
            ('2025-02-28', '2024-02-29', 366),
        ):
            with self.subTest(end=end):
                actual_start, actual_end = self.module.master_date_window(end)
                self.assertEqual(str(actual_start.date()), start)
                self.assertEqual(str(actual_end.date()), end)
                self.assertEqual((actual_end - actual_start).days + 1, days)

    def run_update(self, current, *, config=None, increment=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        master = Path(temporary.name) / 'master.xlsx'
        if current is not None:
            master.write_bytes(b'original master')
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        settings = dict(username='test', password='test', refreshDays=0)
        settings.update(config or {})
        with (
            patch.object(self.module, 'load_config', return_value=settings),
            patch.object(self.module, 'resolve_paths', return_value={
                'masterWorkbook': master, 'initialWorkbook': master.parent / 'absent.xlsx'}),
            patch.object(self.module, 'read_data_workbook', return_value=current),
            patch.object(self.module, 'write_large_xlsx') as writer,
            patch.object(self.module, 'GDFireClient', return_value=client) as factory,
            patch.object(self.module, 'build_increment', return_value=increment) as builder,
        ):
            result = self.module.update_master_workbook(run_date='2026-11-04')
        return result, writer, factory, client, builder

    def test_complete_master_is_truncated_without_network_even_with_future_rows(self):
        current = self.curve('2025-11-04', '2026-11-05')
        result, writer, factory, _, _ = self.run_update(current)
        factory.assert_not_called()
        expected = self.curve('2025-11-05', '2026-11-04')
        assert_frame_equal(writer.call_args.args[0].reset_index(drop=True), expected)
        self.assertTrue(result['updated'])
        self.assertEqual(result['requested_dates'], [])

    def test_complete_bounded_master_is_not_rewritten(self):
        result, writer, factory, _, _ = self.run_update(self.curve('2025-11-05', '2026-11-04'))
        writer.assert_not_called()
        factory.assert_not_called()
        self.assertFalse(result['updated'])

    def test_leading_gaps_fetch_from_window_start_and_exclude_api_outliers(self):
        current = self.curve('2026-11-04', '2026-11-05')
        increment = self.curve('2025-11-04', '2026-11-05')
        result, writer, _, client, builder = self.run_update(current, increment=increment)
        self.assertEqual(builder.call_args.args[:2], ('2025-11-05', '2026-11-03'))
        self.assertEqual(client.get.call_args_list[0].args[1]['startDate'], '2025-11-05')
        self.assertEqual(result['requested_dates'][0], '2025-11-05')
        self.assertEqual(result['requested_dates'][-1], '2026-11-03')
        self.assertEqual(result['remaining_missing_dates'], [])
        assert_frame_equal(writer.call_args.args[0].reset_index(drop=True), self.curve('2025-11-05', '2026-11-04'))

    def test_cold_start_ignores_legacy_initial_date(self):
        result, writer, _, _, builder = self.run_update(
            None, config={'initialDate': '1900-01-01'},
            increment=self.curve('2025-11-05', '2026-11-04'),
        )
        self.assertEqual(builder.call_args.args[:2], ('2025-11-05', '2026-11-04'))
        self.assertEqual(len(result['requested_dates']), 365)
        self.assertEqual(len(writer.call_args.args[0]), 365 * 96)

    def test_all_old_rows_still_fetch_the_whole_window(self):
        result, writer, _, _, builder = self.run_update(
            self.curve('2020-01-01', '2020-01-01'),
            increment=self.curve('2025-11-05', '2026-11-04'),
        )
        self.assertEqual(builder.call_args.args[:2], ('2025-11-05', '2026-11-04'))
        self.assertEqual(result['remaining_missing_dates'], [])
        self.assertEqual(writer.call_args.args[0]['日期'].min().date(), date(2025, 11, 5))

    def test_refresh_cannot_expand_beyond_one_year(self):
        frame = self.curve('2025-11-05', '2026-11-04')
        result, _, _, _, builder = self.run_update(
            frame, config={'refreshDays': 999}, increment=frame,
        )
        self.assertEqual(builder.call_args.args[:2], ('2025-11-05', '2026-11-04'))
        self.assertEqual(len(result['requested_dates']), 365)
