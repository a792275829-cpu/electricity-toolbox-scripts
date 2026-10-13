from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import openpyxl

from toolbox.runtime import ToolPaths, load_module


class DummyAuth:
    @staticmethod
    def unit_display_name(unit):
        return unit.get("unitName") or ""

    @staticmethod
    def is_renewable_unit(unit):
        return unit.get("subType") in {"4", "5"}


class SpotPriceUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = ToolPaths(Path(__file__).resolve().parents[2])
        cls.module = load_module("spot_price_update_tests", cls.paths.spot_price_update)

    def test_parse_price_payload_extracts_exact_24_points(self) -> None:
        points = [str(300 + index / 10) for index in range(24)]
        payload = {
            "retCode": "T200",
            "data": {
                "unitDateList": [
                    {
                        "date": "2026-08-30 00:00:00",
                        "unitId": "unit-1",
                        "acPrice": points,
                    }
                ]
            },
        }
        parsed = self.module.parse_price_payload(
            payload,
            unit_id="unit-1",
            expected_dates=(date(2026, 8, 30),),
            label="测试日前电价",
        )
        self.assertEqual(len(parsed[date(2026, 8, 30)]), 24)
        self.assertAlmostEqual(parsed[date(2026, 8, 30)][23], 302.3)

    def test_parse_price_payload_rejects_partial_day(self) -> None:
        payload = {
            "data": {
                "unitDateList": [
                    {
                        "date": "2026-08-30",
                        "unitId": "unit-1",
                        "acPrice": [300] * 23,
                    }
                ]
            }
        }
        with self.assertRaisesRegex(RuntimeError, "应有 24 个电价点"):
            self.module.parse_price_payload(
                payload,
                unit_id="unit-1",
                expected_dates=(date(2026, 8, 30),),
                label="测试实时电价",
            )

    def test_parse_price_payload_can_skip_incomplete_dates_by_price_type(self) -> None:
        complete_date = date(2026, 9, 1)
        incomplete_date = date(2026, 9, 2)
        payload = {
            "data": {
                "unitDateList": [
                    {
                        "date": complete_date.isoformat(),
                        "unitId": "unit-1",
                        "acPrice": [300] * 24,
                    },
                    {
                        "date": incomplete_date.isoformat(),
                        "unitId": "unit-1",
                        "acPrice": [],
                    },
                ]
            }
        }

        parsed = self.module.parse_price_payload(
            payload,
            unit_id="unit-1",
            expected_dates=(complete_date, incomplete_date),
            label="测试实时电价",
            skip_incomplete=True,
        )

        self.assertEqual(set(parsed), {complete_date})
        self.assertEqual(len(parsed[complete_date]), 24)

    def test_company_and_station_mapping(self) -> None:
        target = self.module.default_targets(Path("归湖.xlsx"), Path("鮀莲.xlsx"))[0]
        tenant = self.module.resolve_tenant(
            [
                {"tenantId": "a", "name": "华能（汕头金平）新能源有限责任公司"},
                {"tenantId": "b", "name": "华能（潮州潮安）新能源有限责任公司"},
            ],
            target,
        )
        unit = self.module.resolve_unit(
            [
                {"id": "x", "unitName": "其他风电场", "subType": "4"},
                {"id": "y", "unitName": "华归光伏电站", "subType": "5"},
            ],
            target,
            DummyAuth,
        )
        self.assertEqual(tenant["tenantId"], "b")
        self.assertEqual(unit["id"], "y")

    def test_haifeng_mapping_and_station_selection(self) -> None:
        targets = self.module.default_targets(
            Path("归湖.xlsx"), Path("鮀莲.xlsx"), Path("海风.xlsx"),
            selected_stations=("海风",),
        )
        self.assertEqual([target.key for target in targets], ["海风"])
        self.assertEqual(targets[0].workbook, Path("海风.xlsx"))
        tenant = self.module.resolve_tenant(
            [
                {"tenantId": "a", "name": "华能（汕头金平）新能源有限责任公司"},
                {"tenantId": "b", "name": "华能广东汕头海上风电有限责任公司"},
            ], targets[0],
        )
        unit = self.module.resolve_unit(
            [
                {"id": "x", "unitName": "其他风电场", "subType": "4"},
                {"id": "y", "unitName": "广澳海风场", "subType": "4"},
            ], targets[0], DummyAuth,
        )
        self.assertEqual(tenant["tenantId"], "b")
        self.assertEqual(unit["id"], "y")
        self.assertEqual(
            [target.key for target in self.module.default_targets()],
            ["归湖", "鮀莲", "海风"],
        )
        self.assertEqual(
            [target.key for target in self.module.default_targets(selected_stations=("归湖", "海风"))],
            ["归湖", "海风"],
        )

    def test_station_selection_rejects_empty_and_unknown(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少选择一个"):
            self.module.default_targets(selected_stations=())
        with self.assertRaisesRegex(ValueError, "未知场站"):
            self.module.default_targets(selected_stations=("不存在",))

    def test_cli_dry_run_fetches_only_selected_station(self) -> None:
        with patch("sys.argv", [
            "update_spot_prices.py", "--stations", "海风", "--dry-run",
            "--haifeng-workbook", "/tmp/海风.xlsx",
            "--guihu-workbook", "/not-present/归湖.xlsx",
            "--tuolian-workbook", "/not-present/鮀莲.xlsx",
        ]), patch.object(self.module, "load_config", return_value=self.module.Config()), \
                patch.object(self.module, "collect_target_prices", return_value=()) as collect:
            self.assertEqual(self.module.main(), 0)
        targets = collect.call_args.args[2]
        self.assertEqual([target.key for target in targets], ["海风"])
        self.assertEqual(targets[0].workbook, Path("/tmp/海风.xlsx"))

    def _build_fixture(self, path: Path, target_date: date) -> None:
        workbook = openpyxl.Workbook()
        day_ahead = workbook.active
        day_ahead.title = self.module.DAY_AHEAD_SHEET
        real_time = workbook.create_sheet(self.module.REAL_TIME_SHEET)
        for sheet in (day_ahead, real_time):
            sheet.append(["日期"] + [f"{hour}时" for hour in range(24)])
            sheet.append([datetime.combine(target_date, datetime.min.time())] + [None] * 24)
            sheet["A2"].number_format = "m/d/yy"
            for column in range(2, 26):
                sheet.cell(row=2, column=column).number_format = "0.0000"
        workbook.save(path)
        workbook.close()

    def test_prepare_and_commit_preserve_formats_and_write_both_sheets(self) -> None:
        target_date = date(2026, 8, 30)
        with tempfile.TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "归湖.xlsx"
            self._build_fixture(workbook_path, target_date)
            target = self.module.TargetSpec(
                key="归湖",
                workbook=workbook_path,
                tenant_keywords=("潮州潮安",),
                unit_keywords=("华归",),
            )
            prices = self.module.TargetPrices(
                target=target,
                tenant_name="华能（潮州潮安）新能源有限责任公司",
                unit_id="unit-1",
                unit_name="华归光伏电站",
                day_ahead={target_date: tuple(300 + index for index in range(24))},
                real_time={target_date: tuple(400 + index for index in range(24))},
            )
            prepared = self.module.prepare_workbook(prices, overwrite=True)
            backups = self.module.commit_prepared_workbooks((prepared,), create_backup=True)

            workbook = openpyxl.load_workbook(workbook_path, data_only=False)
            try:
                self.assertEqual(workbook[self.module.DAY_AHEAD_SHEET]["B2"].value, 300)
                self.assertEqual(workbook[self.module.DAY_AHEAD_SHEET]["Y2"].value, 323)
                self.assertEqual(workbook[self.module.REAL_TIME_SHEET]["B2"].value, 400)
                self.assertEqual(workbook[self.module.REAL_TIME_SHEET]["Y2"].value, 423)
                self.assertEqual(
                    workbook[self.module.DAY_AHEAD_SHEET]["B2"].number_format,
                    "0.0000",
                )
            finally:
                workbook.close()
            self.assertTrue(backups[workbook_path.resolve()].is_file())

    def test_prepare_can_update_day_ahead_when_real_time_is_missing(self) -> None:
        target_date = date(2026, 9, 1)
        with tempfile.TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "归湖.xlsx"
            self._build_fixture(workbook_path, target_date)
            target = self.module.TargetSpec(
                key="归湖",
                workbook=workbook_path,
                tenant_keywords=("潮州潮安",),
                unit_keywords=("华归",),
            )
            prices = self.module.TargetPrices(
                target=target,
                tenant_name="华能（潮州潮安）新能源有限责任公司",
                unit_id="unit-1",
                unit_name="华归光伏电站",
                day_ahead={target_date: tuple(300 + index for index in range(24))},
                real_time={},
            )

            prepared = self.module.prepare_workbook(prices, overwrite=True)
            self.module.commit_prepared_workbooks((prepared,), create_backup=False)

            workbook = openpyxl.load_workbook(workbook_path, data_only=False)
            try:
                self.assertEqual(workbook[self.module.DAY_AHEAD_SHEET]["B2"].value, 300)
                self.assertEqual(workbook[self.module.DAY_AHEAD_SHEET]["Y2"].value, 323)
                self.assertIsNone(workbook[self.module.REAL_TIME_SHEET]["B2"].value)
                self.assertIsNone(workbook[self.module.REAL_TIME_SHEET]["Y2"].value)
            finally:
                workbook.close()

    def test_skip_existing_leaves_row_unchanged(self) -> None:
        target_date = date(2026, 8, 30)
        with tempfile.TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "归湖.xlsx"
            self._build_fixture(workbook_path, target_date)
            workbook = openpyxl.load_workbook(workbook_path)
            workbook[self.module.DAY_AHEAD_SHEET]["B2"] = 999
            workbook.save(workbook_path)
            workbook.close()

            workbook = openpyxl.load_workbook(workbook_path)
            expectations, updated, skipped, row_values = self.module.plan_price_sheet(
                workbook,
                self.module.DAY_AHEAD_SHEET,
                {target_date: tuple(300 + index for index in range(24))},
                overwrite=False,
            )
            try:
                self.assertEqual(expectations, [])
                self.assertEqual(updated, [])
                self.assertEqual(skipped, [target_date])
                self.assertEqual(row_values, {})
                self.assertEqual(workbook[self.module.DAY_AHEAD_SHEET]["B2"].value, 999)
            finally:
                workbook.close()

    def test_fixture_dates_cover_requested_range(self) -> None:
        start = date(2026, 8, 30)
        self.assertEqual(
            self.module.date_range(start, start + timedelta(days=2)),
            (start, start + timedelta(days=1), start + timedelta(days=2)),
        )


if __name__ == "__main__":
    unittest.main()
