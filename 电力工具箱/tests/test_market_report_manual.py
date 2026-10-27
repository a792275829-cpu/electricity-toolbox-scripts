from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import tkinter as tk
import unittest
from contextlib import ExitStack, nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "南方分公司电力市场日报"))
from gd_power_report import manual_market, pipeline
from gd_power_report.collector import market
from gd_power_report.config import AppConfig
from gd_power_report.company_clearing_xlsx import _strategy_lines
from gd_power_report.legacy_concise_report import _split_strategy_profit
from gd_power_report.report import _prepare_appendix_for_display
from toolbox.market_report_page import MarketReportPage
from toolbox.runtime import TaskRegistry, ToolPaths
from toolbox import workflows


DAY = date(2026, 10, 15)


def complete_manual():
    return {"report_date": DAY.isoformat(), **{
        period: {field.key: {"value": 100, "comparison": 90} for field in manual_market.MARKET_FIELDS}
        for period in ("day_ahead", "realtime")
    }}


class ManualKernelTests(unittest.TestCase):
    def test_complete_36_values_roundtrip_through_rust(self):
        data = manual_market.normalize_manual_market(complete_manual(), DAY)
        self.assertEqual(sum(len(pair) for p in ("day_ahead", "realtime") for pair in data[p].values()), 36)
        for period in ("day_ahead", "realtime"):
            merged = manual_market.merge_manual_market([], period, data, DAY)
            self.assertEqual(merged["missing"], [])
            self.assertEqual(merged["used"], 18)
            fuel = next(c for c in merged["cards"] if c.get("thermal_breakdown"))
            self.assertEqual((fuel["value"], fuel["delta"]), (200, 20))
            self.assertEqual(manual_market.merge_manual_market(merged["cards"], period, data, DAY)["used"], 0)

    def test_date_and_numeric_contracts(self):
        for data in (
            {"report_date": "2026-10-14"},
            {"realtime": {"coal": {"value": 1.5}}},
            {"realtime": {"gas": {"comparison": -1}}},
            {"day_ahead": {"load": {"value": True}}},
            {"day_ahead": {"price": {"value": "NaN"}}},
            {"unknown": {}},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                manual_market.normalize_manual_market(data, DAY)
        result = manual_market.normalize_manual_market({"realtime": {"price": {"value": -50, "comparison": 0}}}, DAY)
        self.assertEqual(manual_market.preview_manual_market(result, DAY)["deltas"]["realtime"]["price"], -50)

    def test_nonfinite_website_value_is_missing_and_baseline_is_preserved(self):
        for invalid in (float("nan"), float("inf"), True):
            with self.subTest(invalid=invalid):
                cards = [{"label": "统调实际负荷峰值", "value": invalid, "comparison_value": 80}]
                data = {"realtime": {"load": {"value": 100, "comparison": 999}}}
                result = manual_market.merge_manual_market(cards, "realtime", data, DAY)
                self.assertEqual(result["cards"][0]["delta"], 20)
                self.assertEqual(result["cards"][0]["comparison_value"], 80)

    def test_short_titles_and_notes_remain_in_source_order(self):
        for title in ("粤东区域", "粤东策略", "粤东策略研究", "粤东区域运行策略", "粤东区域运行策略研究"):
            for prefix in ("", "2."):
                for separator in ("：", ":", "\n"):
                    with self.subTest(title=title, prefix=prefix, separator=separator):
                        rows = _strategy_lines(f"{prefix}{title}{separator}策略总述。\n东莞厂也需要调整报价。\n3.东莞厂独立策略。")
                        self.assertEqual([r["plant"] for r in rows], [title, "东莞厂"])
                        self.assertEqual(_split_strategy_profit(rows[0])["plant"], "粤东区域")
                        self.assertIn("东莞厂也需要调整报价", rows[0]["text"])
        notes = ["1、原始备注。", "注：含税（13%）口径。", "注:原始公式。"]
        table = {"column_widths": [42], "rows": [{"cells": [{"value": v}]} for v in ["电价", *notes]]}
        prepared = _prepare_appendix_for_display(table, "day_ahead", source_faithful_day_recap=True)
        self.assertEqual(prepared["blocks"][0]["notes"], notes)
        self.assertEqual(len(prepared["blocks"][0]["rows"]), 1)


class PartialCollectorTests(unittest.TestCase):
    def test_failed_board_keeps_load_and_each_fuel_and_releases_lock(self):
        class Response:
            ok, status = True, 200
            def __init__(self, data): self.data = data
            def json(self): return {"retCode": "T200", "data": self.data}
            def dispose(self): pass

        class Context:
            def __init__(self): self.request, self.closed = self, False
            def get(self, url, **kwargs):
                if url.endswith("/spot/market/info"):
                    raise RuntimeError("board HTTP 503")
                return Response({"dataNetLoadDTOList": [{
                    "loadType": "1", "forecastPeriodList": [
                        {"date": "2026-10-13", "max": 153000},
                        {"date": "2026-10-14", "max": 155000},
                        {"date": "2026-10-15", "max": 157000},
                    ], "actualPeriodList": [{"date": "2026-10-13", "max": 154000}],
                }]})
            def post(self, url, *, data, **kwargs):
                # One fuel endpoint is valid even though its peer fuel is absent.
                group = data["spotClearEleGroupList"][0]
                return Response([{"date": data["startDate"], "dataType": "2" if group.startswith("REAL_TIME") else "1",
                                  "dataItemGroup": group, "unitCount": [114] * 24}])
            def close(self): self.closed = True

        context = Context()
        lock = mock.MagicMock()
        legacy = SimpleNamespace(
            BASE_URL="https://example.test", AuthStateLock=lambda: lock,
            load_config=lambda: SimpleNamespace(headless=True),
            launch_context=lambda *a, **k: context, ensure_login=lambda *a: None,
        )
        with mock.patch.object(market, "load_toolbox_auth_module", return_value=legacy), \
             mock.patch.object(market, "sync_playwright", return_value=nullcontext(object())):
            raw = market.collect_realtime_market_comparison(AppConfig(ROOT), date(2026, 10, 13), day_ahead_date=DAY, allow_partial_market=True)
        self.assertTrue(context.closed)
        lock.__exit__.assert_called_once()
        self.assertIn("HTTP 503", raw["errors"]["market_board_status"])
        for period, label, value in (("day_ahead", "统调负荷峰值", 15700), ("realtime", "统调实际负荷峰值", 15400)):
            cards = raw[f"market_{period}_cards"]
            self.assertEqual(next(c for c in cards if c["label"] == label)["value"], value)
            thermal = next(c for c in cards if c.get("thermal_breakdown"))["thermal_breakdown"]
            self.assertEqual(thermal["coal"], 114)
            self.assertIsNone(thermal["gas"])


class ManualPipelineTests(unittest.TestCase):
    def setup_pipeline(self, stack):
        from test_market_report_optimizations import ReportInputAndErrorTests
        helper = ReportInputAndErrorTests()
        _, _, collect, render = helper.inputs(stack)
        pipeline.parse_daily_report_workbook.side_effect = [
            {"report_date": DAY.isoformat(), "report_date_label": "10月15日", "day_ahead": {"cards": []}, "realtime": {"cards": []},
             "operations": {"rows": []}, "appendices": {}, "market": {"day_ahead": [{"label": "全省日前均价", "value": 350, "delta": 50}], "realtime": []}},
            {"report_date": "2026-10-13", "realtime": {"cards": [{"label": "实时出清电量", "value": 10}]},
             "market": {"day_ahead": [{"label": "全省日前均价", "value": 250}], "realtime": [{"label": "全省实时均价", "value": 300}]}},
        ]
        pipeline.parse_day_ahead_comparison_workbook.return_value = {"report_date": "2026-10-14", "report_date_label": "10月14日", "cards": [], "operations": {}, "source_path": "previous.xlsx"}
        return collect, render

    def run_pipeline(self, directory, data):
        return pipeline.run_pipeline(AppConfig.from_workspace(directory), DAY, "day.xlsx",
            comparison_clearing_xlsx_path="previous.xlsx", realtime_detail_xlsx_path="d2.xlsx", manual_market=data)

    def test_website_failure_uses_manual_values_and_keeps_atomic_pdf_pair(self):
        from test_market_report_optimizations import write_pdf
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            collect, render = self.setup_pipeline(stack)
            collect.side_effect = RuntimeError("site HTTP 503")
            captured = {}
            def export(folder, report, progress):
                captured.update(copy.deepcopy(report))
                phone, printing = folder / "phone.pdf", folder / "print.pdf"
                write_pdf(phone); write_pdf(printing, 2)
                return folder / "phone.html", phone, printing
            render.side_effect = export
            result = self.run_pipeline(directory, complete_manual())
            collect.assert_called_once()
            self.assertTrue(collect.call_args.kwargs["allow_partial_market"])
            self.assertEqual(set(result.run_dir.iterdir()), {result.preview_pdf_path, result.print_pdf_path})
            self.assertEqual(len(PdfReader(result.preview_pdf_path).pages), 1)
            price = next(c for c in captured["market"]["realtime"] if c["label"] == "全省实时均价")
            self.assertEqual((price["value"], price["comparison_value"], price["delta"]), (100, 90, 10))

    def test_missing_manual_baseline_stops_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            collect, render = self.setup_pipeline(stack)
            collect.side_effect = RuntimeError("site HTTP 503")
            data = complete_manual()
            del data["realtime"]["gas"]["comparison"]
            with self.assertRaisesRegex(ValueError, "气机开机台数对比值"):
                self.run_pipeline(directory, data)
            render.assert_not_called()
            self.assertFalse(AppConfig.from_workspace(directory).outputs_dir.exists())

    def test_workbook_prices_fill_missing_manual_price_fields(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            collect, render = self.setup_pipeline(stack)
            collect.side_effect = RuntimeError("site HTTP 503")
            data = complete_manual()
            for period in ("day_ahead", "realtime"):
                del data[period]["price"]
            def inspect(folder, report, progress):
                day_price = next(c for c in report["market"]["day_ahead"] if c["label"] == "全省日前均价")
                rt_price = next(c for c in report["market"]["realtime"] if c["label"] == "全省实时均价")
                self.assertEqual((day_price["value"], day_price["comparison_value"], day_price["delta"]), (350, 300, 50))
                self.assertEqual((rt_price["value"], rt_price["comparison_value"], rt_price["delta"]), (300, 250, 50))
                self.assertEqual(rt_price["source_kind"], "workbook")
                raise RuntimeError("price source verified before rendering")
            render.side_effect = inspect
            with self.assertRaisesRegex(RuntimeError, "price source verified"):
                self.run_pipeline(directory, data)

    def test_website_price_survives_d2_adoption(self):
        report = {"market": {"realtime": [{"label": "全省实时均价", "value": -5, "comparison_value": 0,
                   "delta": -5, "source_kind": "website"}]}, "realtime": {}, "appendices": {}}
        detail = {"realtime": {"cards": [{"label": "实时出清电量", "value": 10}]}, "market": {
            "day_ahead": [{"label": "全省日前均价", "value": 250}], "realtime": [{"label": "全省实时均价", "value": 300}]}}
        pipeline._adopt_realtime_detail_source(report, detail, [{"realtime": 10}])
        price = report["market"]["realtime"][0]
        self.assertEqual((price["value"], price["comparison_value"], price["delta"]), (-5, 0, -5))


class ManualUiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.registry = TaskRegistry()
        self.page = MarketReportPage(self.root, ToolPaths(ROOT), self.registry)
        self.page.report_date.set(DAY.isoformat())

    def tearDown(self):
        if self.page.market_editor is not None and self.page.market_editor.winfo_exists():
            self.page.market_editor.close()
        self.page.destroy()
        self.registry.terminate_all()
        self.root.destroy()

    def test_36_fields_delta_save_and_date_change_clears_dialog(self):
        self.page.open_market_editor()
        editor = self.page.market_editor
        self.assertEqual(sum(len(pair) for p in editor.values.values() for pair in p.values()), 36)
        editor.values["realtime"]["price"]["value"].set("-50")
        editor.values["realtime"]["price"]["comparison"].set("0")
        editor.refresh()
        self.assertEqual(editor.deltas["realtime"]["price"].get(), "-50")
        editor.save()
        self.assertEqual(json.loads(self.page.manual_market.get())["realtime"]["price"],
                         {"value": -50.0, "comparison": 0.0})
        self.page.open_market_editor()
        self.page.report_date.set("2026-10-16")
        self.assertFalse(self.page.market_editor.winfo_exists())
        self.assertEqual(self.page.manual_market.get(), "")

    def test_ui_and_service_plan_forward_same_validated_values(self):
        self.page.save_manual_market(complete_manual())
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{n}.xlsx" for n in range(3)]
            for path, var in zip(paths, (self.page.day_report, self.page.previous_report, self.page.d2_report)):
                path.touch(); var.set(str(path.resolve()))
            command = self.page.build_command()
            values = {key: getattr(self.page, key).get() for key in (
                "report_date", "day_report", "previous_report", "d2_report", "manual_market")}
            validated = workflows.validate(ToolPaths(ROOT), "market-report", "run", values)
            service_command = workflows.plan(ToolPaths(ROOT), "market-report", "run", validated)[0][0]
            self.assertEqual(command, service_command)
            self.assertEqual(json.loads(command[command.index("--manual-market") + 1])["report_date"], DAY.isoformat())
            from toolbox.ui_tasks import RemoteTaskRegistry
            registry = RemoteTaskRegistry(None)
            with mock.patch.object(registry, "execute_remote", return_value={}) as execute:
                worker = registry.prepare_page_worker(self.page, "生成日报", command=command, process=True)
                self.assertEqual(worker(None), 0)
                self.assertEqual(json.loads(execute.call_args.args[2]["manual_market"]), complete_manual())


class ManualPdfTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("TOOLBOX_REPORT_PDF_E2E") == "1", "设置 TOOLBOX_REPORT_PDF_E2E=1 执行真实 Chromium PDF 回读")
    def test_real_pdf_pair_contains_short_title_and_manual_market_values(self):
        data = manual_market.normalize_manual_market(complete_manual(), DAY)
        data["realtime"]["price"] = {"value": -50, "comparison": 0}
        cards = {period: manual_market.merge_manual_market([], period, data, DAY)["cards"] for period in ("day_ahead", "realtime")}
        table = {"column_widths": [42, 42], "rows": [
            {"cells": [{"value": "电量"}, {"value": "电价"}]},
            {"cells": [{"value": 14}, {"value": 453}]},
            {"cells": [{"value": "注：含税（13%）口径。", "colspan": 2}]},
        ]}
        for row in table["rows"]:
            for cell in row["cells"]:
                cell.setdefault("rowspan", 1)
                cell.setdefault("colspan", 1)
        report = {"report_date": DAY.isoformat(), "market": cards,
                  "day_ahead": {"cards": []}, "realtime": {"cards": []},
                  "operations": {"dates": [], "rows": []},
                  "appendices": {"day_ahead": table, "realtime": {"rows": [], "column_widths": []}},
                  "strategies": [{"plant": "粤东区域运行策略研究", "text": "策略总述。"}]}
        with tempfile.TemporaryDirectory() as directory:
            _, phone, printing = pipeline._render_concise_outputs(Path(directory), report, lambda _: None)
            self.assertEqual(len(PdfReader(phone).pages), 1)
            for output in (phone, printing):
                text = "".join(page.extract_text() for page in PdfReader(output).pages)
                self.assertIn("粤东区域", text)
                self.assertNotIn("粤东区域运行策略研究", text)
                self.assertIn("-50", text)
                self.assertIn("13%", text)


if __name__ == "__main__":
    unittest.main()
