from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from toolbox import commands, workflows
from toolbox.catalog import default_catalog
from toolbox.private_batch_upload_page import MAX_BYTES, PrivateBatchUploadPage
from toolbox.runtime import TaskRegistry, ToolPaths
from toolbox.service import ToolboxService
from toolbox.service_catalog import desktop_catalog

ROOT = Path(__file__).resolve().parents[2]
UPLOADER_SCRIPTS = ROOT / "电力工具脚本" / "private-data-uploader-tool" / "scripts"


def node_available() -> bool:
    return shutil.which("node") is not None


class BatchPlanNodeTests(unittest.TestCase):
    """纯逻辑在 batch-plan.mjs 里，直接用 node 跑断言。"""

    def run_node(self, body: str):
        script = (
            f"import * as m from {json.dumps(str(UPLOADER_SCRIPTS / 'batch-plan.mjs'))};\n"
            "const file=(name,size)=>({path:'/tmp/'+name,size});\n"
            f"{body}\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "check.mjs"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", str(path)], capture_output=True, text=True, timeout=60,
            )
        if result.returncode != 0:
            self.fail(result.stdout + result.stderr)
        return json.loads(result.stdout)

    def setUp(self) -> None:
        if not node_available():
            self.skipTest("node 不可用")

    def test_batches_split_on_twelve_files(self) -> None:
        sizes = self.run_node(
            "const files = Array.from({length:13}, (_, i) => file('a'+i+'.xlsx', 1000));\n"
            "console.log(JSON.stringify(m.planBatches(files).map(b => b.files.length)));"
        )
        self.assertEqual(sizes, [12, 1])

    def test_batches_split_on_ten_megabytes(self) -> None:
        sizes = self.run_node(
            "const files = [file('a.xlsx', 6*1048576), file('b.xlsx', 6*1048576)];\n"
            "console.log(JSON.stringify(m.planBatches(files).map(b => b.files.length)));"
        )
        self.assertEqual(sizes, [1, 1])

    def test_single_file_over_limit_is_rejected(self) -> None:
        result = self.run_node(
            "try { m.planBatches([file('big.xlsx', 11*1048576)]); console.log(JSON.stringify('no-error')); }\n"
            "catch (error) { console.log(JSON.stringify(error.message)); }"
        )
        self.assertIn("10MB", result)

    def test_import_result_marks_files_with_detail_as_failed(self) -> None:
        summary = self.run_node(
            "const s = m.summarizeImport([{date:'2026-11-05'},{date:'2026-11-04',detail:'模板不匹配'}]);\n"
            "console.log(JSON.stringify({ok:s.succeeded.length, failed:s.failed.map(f=>f.detail)}));"
        )
        self.assertEqual(summary, {"ok": 1, "failed": ["模板不匹配"]})

    def test_multipart_repeats_files_field(self) -> None:
        body = self.run_node(
            "const {body} = m.buildMultipart([{path:'/tmp/日前.xlsx',size:1,buffer:Buffer.from('a')},"
            "{path:'/tmp/实时.xlsx',size:1,buffer:Buffer.from('b')}], {personalDataTypeSourceMap:'{}'});\n"
            "const text = body.toString('utf8');\n"
            "console.log(JSON.stringify({files:(text.match(/name=\"files\"/g)||[]).length,"
            "map:text.includes('name=\"personalDataTypeSourceMap\"'), cn:text.includes('日前.xlsx')}));"
        )
        self.assertEqual(body, {"files": 2, "map": True, "cn": True})

    def test_dates_fall_back_to_filename_or_date_flag(self) -> None:
        result = self.run_node(
            "console.log(JSON.stringify({"
            "names: m.inferDatesFromNames([{path:'/a/20261025结算单.xlsx'},{path:'/a/x.xlsx'}], undefined),"
            "flag: m.inferDatesFromNames([{path:'/a/x.xlsx'}], '2026-10-25'),"
            "none: m.inferDatesFromNames([{path:'/a/x.xlsx'}], undefined)}));"
        )
        self.assertEqual(result, {"names": ["2026-10-25"], "flag": ["2026-10-25"], "none": []})

    def test_source_codes_map_to_human_readable_names(self) -> None:
        result = self.run_node(
            "console.log(JSON.stringify({a:m.sourceName('1'), b:m.sourceName(101),"
            " c:m.sourceName('GUO_HN_RI_XIN'), d:m.sourceName('')}));"
        )
        self.assertEqual(result, {"a": "电力交易平台", "b": "国能日新", "c": "国能日新", "d": ""})

    def test_status_summary_only_counts_uploaded_rows(self) -> None:
        result = self.run_node(
            "console.log(JSON.stringify({"
            "mixed: m.summarizeStatus([{importStatus:'1'},{importStatus:0},{importStatus:'1'}]),"
            "empty: m.summarizeStatus([]), invalid: m.summarizeStatus(undefined)}));"
        )
        self.assertEqual(result, {"mixed": {"total": 3, "uploaded": 2},
                                  "empty": {"total": 0, "uploaded": 0},
                                  "invalid": {"total": 0, "uploaded": 0}})

    def test_new_energy_source_map_covers_declare_and_raw(self) -> None:
        result = self.run_node(
            "console.log(JSON.stringify({\n"
            "  withSource: m.buildSourceMap({source:'国能日新'}, 'NEW_ENERGY'),\n"
            "  plain: m.buildSourceMap({}, 'NEW_ENERGY'),\n"
            "  fire: m.buildSourceMap({source:'南瑞'}, 'FIRE'),\n"
            "}));"
        )
        self.assertEqual(result["withSource"], {
            "DECLARE_POWER_PREDICT": "DISPATCH_OPS_PLATFORM",
            "RAW_POWER_PREDICT": "GUO_HN_RI_XIN",
        })
        self.assertEqual(result["plain"], {"DECLARE_POWER_PREDICT": "DISPATCH_OPS_PLATFORM"})
        self.assertEqual(result["fire"], {"RAW_POWER_PREDICT": "NAN_RUI"})


class PrivateBatchRegistrationTests(unittest.TestCase):
    def test_tool_is_registered_in_upload_category(self) -> None:
        tool = next(t for t in default_catalog() if t.tool_id == "private-batch-upload")
        self.assertEqual(tool.category, "上传与写入")
        self.assertEqual(tool.name, "私有数据批量上传")
        self.assertEqual(tool.page_module, ".private_batch_upload_page")

    def test_desktop_catalog_exposes_unit_and_file_fields(self) -> None:
        tool = next(t for t in desktop_catalog(ToolPaths(ROOT)) if t["id"] == "private-batch-upload")
        fields = {field["key"]: field for field in tool["fields"]}
        self.assertEqual([c[0] for c in fields["unit"]["choices"]], ["海风", "鮀莲", "归湖", "东莞", "汕头", "海门"])
        self.assertEqual(fields["files"]["kind"], "files")
        self.assertNotIn("power_source", fields)
        self.assertEqual([c[0] for c in fields["summary_type"]["choices"]], ["none", "formal", "temporary"])
        self.assertEqual([c[1] for c in fields["summary_type"]["choices"]],
                         ["非月度结算单", "正式账单", "临时账单"])
        self.assertEqual([a["id"] for a in tool["actions"]], ["run", "login"])

    def test_service_requires_confirmation_with_unit_and_count(self) -> None:
        message = ToolboxService.confirmation(
            "private-batch-upload", "run", {"unit": "汕头", "files": ["a.xlsx", "b.xlsx"]},
        )
        self.assertIn("汕头", message)
        self.assertIn("2 个文件", message)


class PrivateBatchCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.paths = ToolPaths(ROOT)

    def make_file(self, name: str, size: int = 16) -> Path:
        path = self.folder / name
        path.write_bytes(b"0" * size)
        return path

    def test_command_uses_batch_mode_with_unit_and_files(self) -> None:
        file = self.make_file("日前.xlsx")
        command = commands.private_batch(self.paths, "汕头", [file])
        self.assertIn("--batch", command)
        # 目录模式是逐文件模拟页面上传，批量模块不能退化到那条路径。
        self.assertNotIn("--execute", command)
        self.assertNotIn("--source", command)
        self.assertEqual(command[command.index("--unit") + 1], "汕头")
        self.assertEqual(command[command.index("--files") + 1], str(file))
        self.assertNotIn("--summary-type", command)
        self.assertNotIn("--no-verify", command)

    def test_summary_type_and_verify_flags_are_passed_through(self) -> None:
        file = self.make_file("月度结算.xlsx")
        command = commands.private_batch(self.paths, "海风", [file], summary_type="temporary", verify=False)
        self.assertEqual(command[command.index("--summary-type") + 1], "temporary")
        self.assertIn("--no-verify", command)

    def test_power_source_is_passed_only_when_selected(self) -> None:
        file = self.make_file("原始功率预测.xlsx")
        default = commands.private_batch(self.paths, "海风", [file])
        self.assertNotIn("--power-source", default)
        chosen = commands.private_batch(self.paths, "海风", [file], power_source="国能日新")
        self.assertEqual(chosen[chosen.index("--power-source") + 1], "国能日新")

    def test_login_uses_dedicated_batch_state(self) -> None:
        command = commands.login(self.paths, "private-batch-upload")
        self.assertIn("--login", command)
        self.assertIn("--headed", command)
        self.assertIn("--batch", command)

    def test_workflow_rejects_file_over_ten_megabytes(self) -> None:
        big = self.make_file("big.xlsx", MAX_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "10MB"):
            workflows.validate(self.paths, "private-batch-upload", "run",
                               {"unit": "海风", "files": [str(big)], "summary_type": "none", "verify": True})

    def test_workflow_plan_points_at_uploader_directory(self) -> None:
        file = self.make_file("日前.xlsx")
        plan = workflows.plan(self.paths, "private-batch-upload", "run",
                              {"unit": "归湖", "files": [str(file)], "summary_type": "none", "verify": True})
        command, cwd = plan[0]
        self.assertEqual(cwd, self.paths.private_uploader_dir)
        self.assertIn("upload-private-data.mjs", command[1])


class PrivateBatchPageTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.page = PrivateBatchUploadPage(
            self.root, ToolPaths(ROOT), TaskRegistry(),
        )
        self.page.file_dialogs.settings_path = self.folder / "dialogs.json"

    def tearDown(self) -> None:
        self.page.destroy()
        self.root.destroy()

    def test_selected_files_are_summarized_and_split(self) -> None:
        files = []
        for index in range(13):
            path = self.folder / f"{index}.xlsx"
            path.write_bytes(b"0" * 1024)
            files.append(path)
        self.page.apply_files(files)
        self.assertEqual(len(self.page.selected_paths), 13)
        self.assertEqual(PrivateBatchUploadPage.batch_count(files), 2)
        self.assertIn("13 个文件", self.page.summary_var.get())
        self.assertIn("2 批", self.page.summary_var.get())

    def test_unsupported_files_are_skipped(self) -> None:
        good = self.folder / "日前.xlsx"
        good.write_bytes(b"0")
        bad = self.folder / "说明.txt"
        bad.write_bytes(b"0")
        self.page.apply_files([good, bad])
        self.assertEqual(self.page.selected_paths, [good])
        self.assertIn("已跳过", self.page.summary_var.get())

    def test_upload_command_matches_selected_unit(self) -> None:
        path = self.folder / "日前.xlsx"
        path.write_bytes(b"0")
        self.page.apply_files([path])
        self.page.unit.set("海门")
        command = self.page.build_upload_command()
        self.assertEqual(command[command.index("--unit") + 1], "海门")
        self.assertEqual(command[command.index("--files") + 1], str(path))
        self.assertNotIn("--power-source", command)

    def test_monthly_checkbox_controls_summary_type(self) -> None:
        path = self.folder / "结算.xlsx"
        path.write_bytes(b"0")
        self.page.apply_files([path])
        self.assertNotIn("--summary-type", self.page.build_upload_command())
        self.assertTrue(self.page.monthly_buttons[0].instate(["disabled"]))

        self.page.monthly.set(True)
        self.assertFalse(self.page.monthly_buttons[0].instate(["disabled"]))
        self.assertEqual(self.page.summary_type.get(), "formal")
        command = self.page.build_upload_command()
        self.assertEqual(command[command.index("--summary-type") + 1], "formal")

        self.page.monthly_type.set("temporary")
        command = self.page.build_upload_command()
        self.assertEqual(command[command.index("--summary-type") + 1], "temporary")

        self.page.monthly.set(False)
        self.assertEqual(self.page.summary_type.get(), "none")
        self.assertEqual(self.page.monthly_type.get(), "temporary")
        self.assertNotIn("--summary-type", self.page.build_upload_command())

    def test_page_has_no_power_source_selector(self) -> None:
        self.assertFalse(hasattr(self.page, "power_source"))
        self.assertFalse(hasattr(self.page, "source_buttons"))


if __name__ == "__main__":
    unittest.main()
