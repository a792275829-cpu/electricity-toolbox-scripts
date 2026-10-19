from __future__ import annotations

import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from contextlib import nullcontext, redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image


MODULE_PATH = Path(__file__).resolve().parents[1] / "wind_forecast_screenshot.py"
SPEC = importlib.util.spec_from_file_location("wind_forecast_screenshot_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def api_row(day: date, offset: float = 0.0):
    return {
        "date": day.isoformat() + " 00:00:00",
        "forecastPower": [offset + index for index in range(96)],
    }


class WindForecastScreenshotTests(unittest.TestCase):
    def test_bundled_font_keeps_size_and_chinese_glyphs_without_system_fonts(self) -> None:
        with patch.object(MODULE.sys, "platform", "font-fallback-test"):
            regular = MODULE._font(44)
            bold = MODULE._font(48, bold=True)
            same_size_bold = MODULE._font(44, bold=True)
        self.assertEqual(regular.size, 44)
        self.assertEqual(bold.size, 48)
        self.assertEqual(bold.getname()[0], "Noto Serif SC")
        for text in ("日期", "日合计", "（万千瓦时）", "日平均", "10 月 13 日", "1234"):
            with self.subTest(text=text):
                box = bold.getbbox(text)
                self.assertGreater(box[3] - box[1], 30)
        # Missing Chinese glyphs would produce identical tofu masks.
        self.assertNotEqual(bytes(bold.getmask("日")), bytes(bold.getmask("期")))
        self.assertNotEqual(bytes(regular.getmask("日")), bytes(same_size_bold.getmask("日")))

    def test_bundled_font_render_is_relocatable_and_uses_full_size_text(self) -> None:
        start = date(2026, 10, 13)
        rows = MODULE.select_seven_rows([api_row(start + timedelta(days=i)) for i in range(7)])
        with tempfile.TemporaryDirectory(prefix="风力预测 字体测试-") as directory:
            root = Path(directory)
            shutil.copytree(MODULE_PATH.parent / "fonts", root / "fonts")
            with patch.object(MODULE, "SCRIPT_DIR", root), patch.object(MODULE.sys, "platform", "font-fallback-test"):
                output = MODULE.render_table_png(rows, root / "输出" / "test.png")
            with Image.open(output) as image:
                # Exclude cell borders; tiny default fonts only occupy ~10 rows.
                cell = image.crop((310, 10, 510, 90)).convert("L")
                ink = cell.point(lambda pixel: 255 if pixel < 90 else 0).getbbox()
                self.assertIsNotNone(ink)
                self.assertGreater(ink[3] - ink[1], 30)
                self.assertEqual(image.size, (1842, 1980))
                for column in range(7):
                    left = 301 + column * 220
                    header = image.crop((left + 3, 4, left + 218, 98)).convert("L")
                    ink = header.point(lambda pixel: 255 if pixel < 90 else 0).getbbox()
                    self.assertGreaterEqual(ink[0], 6)
                    self.assertLessEqual(ink[2], 209)

    def test_missing_bundled_font_reports_error_instead_of_rendering_tiny_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(MODULE, "SCRIPT_DIR", Path(directory)), patch.object(MODULE.sys, "platform", "font-fallback-test"):
                with self.assertRaisesRegex(RuntimeError, "截图中文字体加载失败"):
                    MODULE._font(48, bold=True)

    def test_empty_forecast_is_unavailable_but_malformed_data_is_an_error(self) -> None:
        for raw in ([], None):
            with self.subTest(raw=raw), self.assertRaisesRegex(MODULE.ForecastDataUnavailable, "未返回风力预测记录"):
                MODULE.select_seven_rows(raw)
        with self.assertRaisesRegex(RuntimeError, "data 不是列表") as error:
            MODULE.select_seven_rows({"unexpected": 1})
        self.assertNotIsInstance(error.exception, MODULE.ForecastDataUnavailable)

    def test_unavailable_cli_reports_date_and_reason_without_failure(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", [str(MODULE_PATH), "2026-08-21"]),
            patch.object(MODULE, "load_config", return_value=MODULE.Config()),
            patch.object(MODULE, "generate_screenshot", side_effect=MODULE.ForecastDataUnavailable("接口未返回风力预测记录")),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            self.assertEqual(MODULE.main(), 0)
        self.assertIn("无数据：运行日期 2026-08-21。原因：接口未返回风力预测记录", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_technical_failure_still_returns_nonzero(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", [str(MODULE_PATH), "2026-08-21"]),
            patch.object(MODULE, "load_config", return_value=MODULE.Config()),
            patch.object(MODULE, "generate_screenshot", side_effect=RuntimeError("网络请求超时")),
            redirect_stderr(stderr),
        ):
            self.assertEqual(MODULE.main(), 1)
        self.assertIn("失败：网络请求超时", stderr.getvalue())

    def test_zero_power_is_valid_data(self) -> None:
        start = date(2026, 8, 21)
        raw = [{"date": (start + timedelta(days=index)).isoformat(), "forecastPower": [0] * 96}
               for index in range(7)]
        rows = MODULE.select_seven_rows(raw)
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row.daily_total == 0 and row.hours == (0.0,) * 24 for row in rows))

    def test_empty_query_closes_browser_and_does_not_render(self) -> None:
        context = Mock()
        auth = SimpleNamespace(
            AuthStateLock=nullcontext,
            launch_context=lambda *args, **kwargs: context,
            ensure_login=lambda *args: None,
            switch_company=lambda *args: None,
            request_json_with_retry=lambda *args, **kwargs: {"data": []},
        )
        with (
            patch.object(MODULE, "load_auth_module", return_value=auth),
            patch.object(MODULE, "sync_playwright", return_value=nullcontext(object())),
            patch.object(MODULE, "render_table_png") as render,
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(MODULE.ForecastDataUnavailable):
                MODULE.generate_screenshot("2026-08-21", MODULE.Config())
        context.close.assert_called_once()
        render.assert_not_called()

    def test_nonconsecutive_forecast_explains_the_gap(self) -> None:
        start = date(2026, 8, 21)
        raw = [api_row(start + timedelta(days=index)) for index in (0, 1, 2, 4, 5, 6, 7)]
        with self.assertRaisesRegex(MODULE.ForecastDataUnavailable, "2026-08-23 后为 2026-08-25"):
            MODULE.select_seven_rows(raw)

    def test_selects_first_seven_dates_and_builds_24_hour_values(self) -> None:
        start = date(2026, 8, 21)
        raw = [api_row(start + timedelta(days=index), index * 100) for index in range(10)]
        rows = MODULE.select_seven_rows(list(reversed(raw)))

        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0].forecast_date, "2026-08-21")
        self.assertEqual(rows[-1].forecast_date, "2026-08-27")
        self.assertEqual(len(rows[0].hours), 24)
        self.assertEqual(rows[0].hours[0], 1.5)
        self.assertEqual(rows[0].hours[23], 93.5)
        self.assertEqual(rows[0].daily_total, 1140.0)
        self.assertEqual(rows[0].daily_average, 47.5)
        self.assertEqual(MODULE.average_of_24_hours(rows[0]), 47.5)
        self.assertEqual(MODULE.format_shifted_value(1420.709), "142")
        self.assertEqual(MODULE.format_shifted_value(91.4625), "9")
        self.assertEqual(MODULE.format_shifted_value(15), "2")
        self.assertEqual(MODULE.format_shifted_value(0), "0")

    def test_rejects_incomplete_96_point_day(self) -> None:
        start = date(2026, 8, 21)
        raw = [api_row(start + timedelta(days=index)) for index in range(7)]
        raw[2]["forecastPower"] = [1] * 95
        with self.assertRaisesRegex(MODULE.ForecastDataUnavailable, "只有 6 个完整预测日期.*实际为 95 个"):
            MODULE.select_seven_rows(raw)

    def test_rendered_png_has_23_hour_column_and_expected_dimensions(self) -> None:
        start = date(2026, 8, 21)
        rows = MODULE.select_seven_rows([api_row(start + timedelta(days=index)) for index in range(7)])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "forecast.png"
            MODULE.render_table_png(rows, output)
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (1842, 1980))
                self.assertEqual(image.getpixel((10, 10)), (220, 230, 247))
                self.assertEqual(image.getpixel((10, 110)), (238, 236, 239))
            self.assertTrue(output.read_bytes().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
