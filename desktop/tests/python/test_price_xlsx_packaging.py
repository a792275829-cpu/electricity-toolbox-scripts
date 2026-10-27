from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packaging/tauri"))
import prepare_payload
import prepare_workspace


class PriceXlsxPackagingTests(unittest.TestCase):
    def test_build_is_required_before_packaging(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(prepare_payload, "run") as run:
            with self.assertRaises(FileNotFoundError):
                prepare_payload.prepare_price_xlsx(Path(tmp), "aarch64-apple-darwin")
            self.assertIn("--target", run.call_args.args[0])
            self.assertIn("--output", run.call_args.args[0])

    def test_managed_install_preserves_executable_mode(self):
        binary = ROOT / "500kV断面分析/bin/toolbox-price-xlsx"
        if not binary.is_file():
            self.skipTest("build native/price-xlsx first")
        with tempfile.TemporaryDirectory() as tmp:
            import json
            import shutil
            base = Path(tmp)
            payload = base / "payload"
            name = "500kV断面分析/bin/toolbox-price-xlsx"
            staged = payload / "app" / name
            staged.parent.mkdir(parents=True)
            shutil.copy2(binary, staged)
            modules = sorted(path for path in (ROOT / "toolbox_shared").glob("*.py"))
            module_files = []
            for module in modules:
                relative = module.relative_to(ROOT).as_posix()
                self.assertTrue(prepare_payload.runtime_source(Path(relative)), relative)
                target_file = payload / "app" / relative
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(module, target_file)
                module_files.append(relative)
            (payload / "manifest.json").write_text(json.dumps({
                "version": "test", "build": "native-test",
                "files": {name: prepare_workspace.digest(staged),
                          **{relative: prepare_workspace.digest(payload / "app" / relative)
                             for relative in module_files}},
            }))
            installed = base / "installed"
            prepare_workspace.install(payload, installed)
            target = installed / name
            self.assertTrue(os.access(target, os.X_OK))
            self.assertEqual(target.read_bytes(), binary.read_bytes())
            import subprocess
            workbook = base / "installed-test.xlsx"
            for operation in ("write", "read"):
                result = subprocess.run([str(target)], input=json.dumps(dict(
                    protocol=1, operation=operation, path=str(workbook),
                    sheets=[dict(name="中文表", rows=[["电价", 123.45]])],
                )), text=True, capture_output=True, check=True)
                response = json.loads(result.stdout)
                if operation == "read":
                    self.assertEqual(response["sheets"][0]["rows"], [["电价", 123.45]])
            # Import only the installed adapter; its root must discover the
            # installed binary without consulting the source checkout.
            subprocess.run([sys.executable, "-c",
                "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd())); "
                "from toolbox_shared.native_excel import load_value_workbook; "
                "b=load_value_workbook(sys.argv[1]); "
                "assert b.active['B1'].value == 123.45; b.close()", str(workbook)],
                cwd=installed, env=dict(os.environ, TOOLBOX_XLSX_BACKEND="rust"),
                capture_output=True, text=True, check=True)
            subprocess.run([sys.executable, "-c",
                "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd())); "
                "import numpy as np; from openpyxl import Workbook,load_workbook; "
                "from toolbox_shared.native_write import save_workbook,save_frame; "
                "from toolbox_shared.native_compute import unmix,native_session; "
                "b=Workbook(); b.active['A1']='installed'; save_workbook(b,sys.argv[1]); "
                "assert load_workbook(sys.argv[1]).active['A1'].value=='installed'; "
                "ctx=native_session(); ctx.__enter__(); "
                "r=unmix(np.full((8,4,3),255,dtype=np.uint8),np.full((8,3),255.),[None,None,None]); "
                "assert np.count_nonzero(r)==0;ctx.__exit__(None,None,None); "
                "import pandas as pd; assert save_frame(pd.DataFrame({'比例':[.125]}),sys.argv[1],'整合数据',{}); "
                "assert load_workbook(sys.argv[1]).active['A2'].value==.125; "
                "from toolbox_shared.workbook_inspection import load_inspection_pair; "
                "a,c=load_inspection_pair(sys.argv[1],columns=(1,),target_columns=(),sheet_filter=lambda n: True); "
                "assert a['整合数据']['A2'].value==.125; a.close(); c.close()", str(workbook)],
                cwd=installed, env=dict(os.environ, TOOLBOX_XLSX_BACKEND="rust",TOOLBOX_COMPUTE_BACKEND="rust"),
                capture_output=True, text=True, check=True)
