from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from openpyxl import Workbook

from toolbox.review_classification import STATION_ALIASES, identify_station, missing_review_stations, prepare_review_folder
from toolbox.tasks import CancellationToken, TaskCancelled


class ReviewClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "10-10"
        self.source.mkdir()
        self.template = Path(self.temporary.name) / "模板"
        self.template.mkdir()
        for station in STATION_ALIASES:
            (self.template / station).mkdir()

    def workbook(self, name: str, *titles: str, folder: Path | None = None, value=1) -> Path:
        folder = folder or self.source
        folder.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        workbook.remove(workbook.active)
        for title in titles:
            sheet = workbook.create_sheet(title)
            sheet.append(["时刻", "电量", "结算电价"])
            sheet.append(["00:00", value, 300.25])
            sheet["B2"].number_format = "0.000"
        path = folder / name
        workbook.save(path)
        workbook.close()
        return path

    def prepare(self, **options) -> Path:
        return prepare_review_folder(
            self.source, logger=lambda _: None, token=CancellationToken(), template=self.template, **options,
        )

    def test_identifies_all_six_stations_from_real_export_sheet_names(self) -> None:
        cases = {
            "谢岗#1、#2机组": "东莞", "华归光伏电站": "归湖", "汕头#1机组": "汕头",
            "海门#3机组": "海门", "广澳海风场": "海风", "鮀莲光伏电站": "鮀莲",
        }
        for index, (title, station) in enumerate(cases.items()):
            path = self.workbook(f"日前交易结果查询(2026-10-10) ({index}).xlsx", title)
            self.assertEqual(identify_station(path), station)
        original = {(identify_station(path), path.name): path.read_bytes() for path in self.source.glob("*.xlsx")}
        result = self.prepare()
        self.assertEqual({path.name for path in result.iterdir() if path.is_dir()}, set(STATION_ALIASES))
        self.assertEqual(len(list(result.glob("*/*.xlsx"))), 6)
        self.assertEqual(list(self.source.glob("*.xlsx")), [])
        for (station, name), content in original.items():
            self.assertEqual((result / station / name).read_bytes(), content)
        self.assertEqual(result, self.source.resolve())
        self.assertFalse((self.source / "已归类").exists())
        self.assertEqual(list(self.template.glob("*/*")), [])

    def test_workbook_identity_takes_priority_over_filename(self) -> None:
        path = self.workbook("汕头日前交易结果查询.xlsx", "鮀莲光伏电站")
        self.assertEqual(identify_station(path), "鮀莲")

    def test_specific_renewable_company_does_not_match_shantou_power_station(self) -> None:
        path = self.workbook("日前交易结果查询(2026-10-10).xlsx", "华能（汕头金平）新能源")
        self.assertEqual(identify_station(path), "鮀莲")

    def test_ambiguous_or_unknown_files_stop_before_moving(self) -> None:
        for titles in (("Sheet1",), ("汕头#1机组", "海门#1机组")):
            with self.subTest(titles=titles):
                self.workbook("日前交易结果查询(2026-10-10).xlsx", *titles)
                with self.assertRaisesRegex(ValueError, "自动归类已停止"):
                    self.prepare()
                self.assertEqual(list(self.source.glob("*/*.xlsx")), [])

    def test_same_name_different_contents_preserved_and_repeat_is_idempotent(self) -> None:
        name = "日前交易结果查询(2026-10-10).xlsx"
        original = self.workbook(name, "汕头#1机组", value=1)
        grouped = self.workbook(name, "汕头#2机组", folder=self.source / "汕头", value=2)
        content = {original.read_bytes(), grouped.read_bytes()}
        result = self.prepare()
        self.assertEqual(len(list((result / "汕头").glob("*.xlsx"))), 2)
        self.assertEqual({path.read_bytes() for path in (result / "汕头").glob("*.xlsx")},
                         content)
        self.assertEqual(self.prepare(), result)

    def test_new_same_name_input_keeps_previous_file_without_overwrite(self) -> None:
        name = "日前交易结果查询(2026-10-10).xlsx"
        self.workbook(name, "汕头#1机组")
        result = self.prepare()
        previous = (result / "汕头" / name).read_bytes()
        new = self.workbook(name, "汕头#1机组", value=9).read_bytes()
        self.assertEqual(self.prepare(), result)
        self.assertEqual((result / "汕头" / name).read_bytes(), previous)
        self.assertEqual({path.read_bytes() for path in (result / "汕头").glob("*.xlsx")}, {previous, new})
        self.assertEqual(self.prepare(), result)
        self.assertEqual(len(list((result / "汕头").glob("*.xlsx"))), 2)

    def test_summary_and_excel_lock_files_are_excluded(self) -> None:
        self.workbook("日前交易结果查询(2026-10-10).xlsx", "汕头#1机组")
        self.workbook("10-10_汇总.xlsx", "Sheet1")
        self.workbook("custom-report.xlsx", "Sheet1")
        (self.source / "~$日前交易结果查询.xlsx").write_bytes(b"lock")
        result = self.prepare(excluded=(self.source / "custom-report.xlsx",))
        self.assertEqual(len(list(result.glob("*/*.xlsx"))), 1)

    def test_single_station_and_already_grouped_folders_keep_original_flow(self) -> None:
        self.workbook("日前交易结果查询(2026-10-10).xlsx", "海门#1机组", folder=self.source / "海门")
        self.assertEqual(self.prepare(), self.source.resolve())
        station = self.source / "海门"
        result = prepare_review_folder(station, logger=lambda _: None, token=CancellationToken(), template=self.template)
        self.assertEqual(result, station.resolve())

    def test_template_folder_is_never_used_as_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "模板目录只用于参考"):
            prepare_review_folder(self.template, logger=lambda _: None, token=CancellationToken(), template=self.template)

    def test_cancellation_does_not_start_copying(self) -> None:
        self.workbook("日前交易结果查询(2026-10-10).xlsx", "汕头#1机组")
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(TaskCancelled):
            prepare_review_folder(self.source, logger=lambda _: None, token=token, template=self.template)
        self.assertFalse((self.source / "已归类").exists())

    def test_filename_never_determines_station(self) -> None:
        path = self.workbook("海门日前交易结果查询(2026-10-10).xlsx", "Sheet1")
        with self.assertRaisesRegex(ValueError, "工作表名中无明确单元"):
            identify_station(path)

    def test_missing_station_names_are_reported_without_stopping(self) -> None:
        self.workbook("日前交易结果查询(2026-10-10).xlsx", "海门#1机组")
        logs = []
        result = prepare_review_folder(self.source, logger=logs.append, token=CancellationToken(), template=self.template)
        self.assertEqual(set(missing_review_stations(result)), set(STATION_ALIASES) - {"海门"})
        self.assertTrue(any("缺少单元数据" in line and "东莞" in line for line in logs))
        self.assertEqual(len(list((result / "海门").glob("*.xlsx"))), 1)

    def test_invalid_date_or_market_is_reported_before_moving(self) -> None:
        self.workbook("日前交易结果查询(2026-02-30).xlsx", "海门#1机组")
        with self.assertRaisesRegex(ValueError, "有效日期"):
            self.prepare()
        self.assertEqual(list(self.source.glob("*/*.xlsx")), [])

    def test_interrupted_move_restores_original_locations(self) -> None:
        from toolbox import review_classification as classifier
        first = self.workbook("日前交易结果查询(2026-10-10).xlsx", "汕头#1机组")
        second = self.workbook("实时交易结果查询(2026-10-08).xlsx", "海门#1机组")
        originals = {path: path.read_bytes() for path in (first, second)}
        actual_move = classifier._move_without_overwrite
        calls = 0

        def move(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("interrupted")
            actual_move(source, destination)

        with mock.patch.object(classifier, "_move_without_overwrite", side_effect=move):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.prepare()
        self.assertTrue(all(path.read_bytes() == content for path, content in originals.items()))
        self.assertEqual(list(self.source.glob("*/*.xlsx")), [])

    def test_template_is_excluded_from_default_date_fallback(self) -> None:
        from toolbox.page_support import default_review_folder_for_date
        from datetime import date
        self.assertEqual(default_review_folder_for_date(date(2026, 10, 10), self.source.parent), self.source)

    def test_unit_prefixes_are_removed_and_duplicate_names_get_trailing_counters(self) -> None:
        first = self.workbook("1,2号机实时交易结果查询(2026-10-06).xlsx", "海门#1机组", "海门#2机组")
        second = self.workbook("3,4号机实时交易结果查询(2026-10-06).xlsx", "海门#3机组", "海门#4机组")
        contents = {first.read_bytes(), second.read_bytes()}
        self.prepare()
        folder = self.source / "海门"
        self.assertEqual({path.name for path in folder.iterdir()}, {
            "实时交易结果查询(2026-10-06).xlsx", "实时交易结果查询(2026-10-06)(2).xlsx",
        })
        self.assertEqual({path.read_bytes() for path in folder.iterdir()}, contents)
        self.prepare()
        self.assertEqual(len(list(folder.iterdir())), 2)

    def test_already_grouped_and_single_station_exports_are_renamed(self) -> None:
        folder = self.source / "海门"
        original = self.workbook("1,2号机日前交易结果查询(2026-10-08).xlsx", "海门#1机组", folder=folder)
        content = original.read_bytes()
        self.prepare()
        expected = folder / "日前交易结果查询(2026-10-08).xlsx"
        self.assertEqual(expected.read_bytes(), content)
        self.assertFalse(original.exists())
        extra = self.workbook("3,4号机实时交易结果查询(2026-10-06).xlsx", "海门#3机组", folder=folder)
        content = extra.read_bytes()
        prepare_review_folder(folder, logger=lambda _: None, token=CancellationToken(), template=self.template)
        self.assertEqual((folder / "实时交易结果查询(2026-10-06).xlsx").read_bytes(), content)

    def test_normalization_keeps_existing_standard_file_and_does_not_overwrite(self) -> None:
        folder = self.source / "海门"
        original = self.workbook("实时交易结果查询(2026-10-06).xlsx", "海门#1机组", folder=folder)
        original_bytes = original.read_bytes()
        incoming = self.workbook("3,4号机实时交易结果查询(2026-10-06).xlsx", "海门#3机组")
        incoming_bytes = incoming.read_bytes()
        self.prepare()
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual((folder / "实时交易结果查询(2026-10-06)(2).xlsx").read_bytes(), incoming_bytes)

    def test_filename_date_and_market_are_normalized_but_station_uses_sheets(self) -> None:
        self.workbook("汕头1,2号机日前交易结果查询（2026.10.08） (下载1).xlsx", "鮀莲光伏电站")
        self.prepare()
        self.assertTrue((self.source / "鮀莲" / "日前交易结果查询(2026-10-08).xlsx").is_file())
        self.assertEqual(list((self.source / "汕头").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
