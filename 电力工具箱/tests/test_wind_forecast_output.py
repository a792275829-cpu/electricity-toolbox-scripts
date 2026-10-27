from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from PIL import Image

from toolbox.runtime import ToolPaths, load_module
from toolbox_shared.storage import wind_forecast_output_dir


ROOT = Path(__file__).resolve().parents[2]


class WindForecastOutputTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("test_wind_forecast_output", ToolPaths(ROOT).wind_forecast)

    def test_default_ignores_other_tools_output_and_empty_own_settings(self):
        expected = Path.home() / "Downloads" / "风力预测截图"
        self.assertEqual(self.module.OUTPUT_DIR, expected)
        self.assertEqual(self.module.Config().output_dir, expected)
        for own in ({}, {"outputDir": ""}, {"outputDir": None}):
            with (
                self.subTest(own=own),
                mock.patch.object(self.module, "_load_json", side_effect=[{"outputDir": "/other-tool-output"}, own]),
                mock.patch.object(self.module, "apply_credentials", side_effect=lambda raw: raw),
            ):
                self.assertEqual(self.module.load_config().output_dir, expected)

    def test_toolbox_and_script_share_default_and_custom_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = ToolPaths(root)
            own = paths.wind_forecast_dir / "config.json"
            fallback = paths.online_energy_dir / "config.json"
            own.parent.mkdir()
            fallback.parent.mkdir()
            fallback.write_text(json.dumps({"outputDir": "/other-tool-output"}), encoding="utf-8")
            for raw, expected in (
                ({}, Path.home() / "Downloads" / "风力预测截图"),
                ({"outputDir": "~/Downloads/自定义风力截图"}, Path.home() / "Downloads" / "自定义风力截图"),
                ({"outputDir": "自定义输出"}, paths.wind_forecast_dir / "自定义输出"),
                ({"outputDir": str(root / "自定义截图")}, root / "自定义截图"),
            ):
                own.write_text(json.dumps(raw), encoding="utf-8")
                with (
                    self.subTest(raw=raw),
                    mock.patch.object(self.module, "SCRIPT_DIR", paths.wind_forecast_dir),
                    mock.patch.object(self.module, "CONFIG_PATH", own),
                    mock.patch.object(self.module, "FALLBACK_CONFIG_PATH", fallback),
                    mock.patch.object(self.module, "apply_credentials", side_effect=lambda raw: raw),
                ):
                    self.assertEqual(self.module.load_config().output_dir, expected)
                    self.assertEqual(paths.wind_forecast_output_dir, expected)

    def test_managed_app_keeps_downloads_default_and_resolves_own_custom_config(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {
            "TOOLBOX_CODE_ROOT": str(ROOT), "TOOLBOX_DATA_ROOT": folder,
        }):
            paths = ToolPaths(ROOT)
            self.assertEqual(paths.wind_forecast_output_dir, Path.home() / "Downloads" / "风力预测截图")
            own = Path(folder) / "风力预测截图" / "config.json"
            own.parent.mkdir()
            own.write_text(json.dumps({"outputDir": "自定义输出"}), encoding="utf-8")
            expected = own.parent.resolve() / "自定义输出"
            self.assertEqual(paths.wind_forecast_output_dir, expected)
            self.assertEqual(wind_forecast_output_dir("自定义输出", script_dir=paths.wind_forecast_dir), expected)

    def test_render_creates_downloads_subfolder_and_png_can_be_read_back(self):
        start = date(2026, 10, 25)
        rows = self.module.select_seven_rows([
            {"date": (start + timedelta(days=index)).isoformat(), "forecastPower": [12.5] * 96}
            for index in range(7)
        ])
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(Path, "home", return_value=Path(folder)):
            output_dir = wind_forecast_output_dir()
            self.assertFalse(output_dir.exists())
            output = self.module.render_table_png(rows, self.module.output_path_for(rows, output_dir))
            self.assertEqual(output, Path(folder) / "Downloads" / "风力预测截图" / "风力预测_20261025_至_20261031.png")
            with Image.open(output) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (1842, 1980))

    def test_desktop_task_returns_configured_output_directory(self):
        from toolbox import workflows

        with tempfile.TemporaryDirectory() as folder:
            paths = ToolPaths(Path(folder))
            paths.wind_forecast_dir.mkdir()
            output = Path(folder) / "自定义风力截图"
            output.mkdir()
            (paths.wind_forecast_dir / "config.json").write_text(
                json.dumps({"outputDir": str(output)}), encoding="utf-8",
            )
            with mock.patch.object(workflows.subprocess, "run") as run:
                result = workflows.execute(paths, "wind-forecast", "run", {"run_date": "2026-10-25"})
            run.assert_called_once()
            self.assertEqual(result["outputs"], [str(output)])


if __name__ == "__main__":
    unittest.main()
