import ctypes
import errno
import importlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'电力工具箱')]


class MaintenanceNextTests(unittest.TestCase):
    def test_all_page_ids_and_bound_fields_match_service_contract(self):
        from toolbox.catalog import default_catalog
        from toolbox.page_contracts import PAGE_BINDINGS
        from toolbox.runtime import ToolPaths
        from toolbox.service_catalog import desktop_catalog
        forms = {item['id']: item for item in desktop_catalog(ToolPaths(ROOT))}
        for item in default_catalog():
            module = importlib.import_module(item.page_module, 'toolbox')
            page = getattr(module, item.page_class)
            self.assertEqual(page.tool_id, item.tool_id)
            if item.tool_id in PAGE_BINDINGS:
                fields = {f['key'] for f in forms[item.tool_id]['fields']}
                self.assertLessEqual(set(PAGE_BINDINGS[item.tool_id].fields), fields)

    def test_explicit_action_survives_renaming_page_and_status(self):
        from toolbox.ui_tasks import RemoteTaskRegistry
        registry = RemoteTaskRegistry(None)
        page = type('CompletelyRenamed', (), dict(tool_id='summary-upload'))()
        page.input_dir = SimpleNamespace(get=lambda: '/fixture')
        page.output_file = SimpleNamespace(get=lambda: '/fixture/output.xlsx')
        page.mode = SimpleNamespace(get=lambda: 'summary')
        page.append_log = Mock()
        with patch.object(registry, 'execute_remote', return_value={}) as execute:
            registry.prepare_page_worker(page, '任意展示文案', action='preview')(None)
            self.assertEqual(execute.call_args.args[:2], ('summary-upload','preview'))
            self.assertNotIn('output_file', execute.call_args.args[2])
        page.tool_id = 'online-energy'
        with patch.object(registry, 'execute_remote', return_value={}) as execute:
            registry.prepare_page_worker(page, '不是登录文案', action='login')(None)
            self.assertEqual(execute.call_args.args[:3], ('online-energy','login',{}))

    def test_runtime_retains_shared_wps_service_class_and_lock_identity(self):
        from toolbox.runtime import load_module
        gui = load_module('shared_wps_identity_regression', ROOT/'wps自动/wps_excel_to_kdocs_gui.py')
        service = importlib.import_module('toolbox_shared.wps_service')
        browser = importlib.import_module('toolbox_shared.wps_browser')
        self.assertIs(gui._wps_service, service)
        self.assertIs(gui.MappingRow, service.MappingRow)
        self.assertIs(gui.WPS_CHROME_START_LOCK, browser.WPS_CHROME_START_LOCK)

    def test_library_modules_do_not_patch_sys_path(self):
        """Only entry points and the module loader may adjust sys.path."""
        import re
        package = ROOT / "电力工具箱/toolbox"
        allowed = {"电力工具箱/toolbox/runtime.py"}
        offenders = []
        for path in sorted(package.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if relative in allowed:
                continue
            if re.search(r"sys\.path\.(insert|append)", path.read_text(encoding="utf-8")):
                offenders.append(relative)
        self.assertEqual(offenders, [], "库模块不应自行修改 sys.path，改由入口脚本负责")

    def test_no_module_injects_the_same_path_twice(self):
        import re
        pattern = re.compile(r"sys\.path\.(?:insert|append)\([^)]*\)")
        duplicates = []
        skip_roots = {"dist", ".toolbox", "logs", ".venv", ".git", "node_modules", "target", "__pycache__"}
        for path in sorted(ROOT.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if skip_roots & set(Path(relative).parts):
                continue
            if "/tests/" in relative or relative.startswith("tooling/tests"):
                continue
            body = path.read_text(encoding="utf-8", errors="ignore")
            calls = pattern.findall(body)
            repeated = {call for call in calls if calls.count(call) > 1}
            if repeated:
                duplicates.append((relative, sorted(repeated)))
        self.assertEqual(duplicates, [], "同一文件重复注入同一路径")

    def test_native_exclusive_publish_and_unsupported_rename_fallback(self):
        from toolbox_shared import atomic_output as atomic
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root/'source', root/'target'
            source.write_bytes(b'prepared')
            atomic.publish_new_file(source, target)
            self.assertEqual(target.read_bytes(), b'prepared')
            source.write_bytes(b'new')
            with self.assertRaises(FileExistsError):
                atomic.publish_new_file(source, target)
            self.assertEqual(target.read_bytes(), b'prepared')
            target.unlink()

            def unsupported(*args):
                ctypes.set_errno(errno.ENOTSUP)
                return -1

            with patch.object(atomic, '_rename_exclusive', return_value=unsupported):
                atomic.publish_new_file(source, target)
            self.assertEqual(target.read_bytes(), b'new')

    def test_error_metadata_survives_actual_worker_and_remote_registry(self):
        from toolbox.service import ToolboxService
        from toolbox.ui_tasks import RemoteTaskRegistry
        from toolbox.tasks import CancellationToken
        from toolbox.tasks.errors import RemoteTaskError
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'invalid.xlsx'
            source.write_text('not a workbook')
            service = ToolboxService(ROOT)
            try:
                task = service.start(dict(request_id='structured-error-fixture', tool_id='trade-analysis', action='run', confirmed=True,
                    values=dict(files=[str(source)], output_dir=str(Path(temp)/'out'))))
                deadline = time.monotonic()+15
                while service.registry.snapshot(task).state.value not in ('failed','succeeded') and time.monotonic()<deadline:
                    time.sleep(.02)
                result = next(s for s in service.snapshots() if s['id']==task)
                self.assertEqual(result['state'], 'failed')
                detail = result['error_detail']
                self.assertEqual((detail['tool_id'],detail['action'],detail['stage']), ('trade-analysis','run','execute'))
                self.assertEqual(detail['exit_code'], 1)
                self.assertIn('Traceback', result['error'])
                client = SimpleNamespace(call=lambda method, values=None:
                    task if method=='start' else dict(cursor=0,events=[],pending=0) if method=='events' else [result])
                with self.assertRaises(RemoteTaskError) as raised:
                    RemoteTaskRegistry(client).execute_remote('trade-analysis','run',{},CancellationToken(),lambda _: None)
                self.assertEqual(raised.exception.error_detail, detail)
            finally:
                service.registry.shutdown(timeout=5)

    def test_wps_service_has_no_tk_and_profile_paths_stay_isolated(self):
        code = '''
import sys, importlib.abc
class NoTk(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == 'tkinter' or name.startswith('tkinter.'):
            raise AssertionError(name)
sys.meta_path.insert(0, NoTk())
from toolbox_shared import wps_service as service, wps_browser as browser
assert service.PROFILE_DIR == browser.PROFILE_DIR
assert service.PROFILE_DIR.name == 'wps-browser-profile'
from toolbox import workflows
try:
    workflows.execute_wps(None,'inspect',{'operation':'{"method":"invalid"}'},print)
except ValueError:
    pass
else:
    raise AssertionError('validation was bypassed')
'''
        result = subprocess.run([sys.executable,'-c',code], env=dict(os.environ,
            PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'电力工具箱')])), capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_report_is_valid_and_keeps_completed_modules_on_interrupt(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/'test_a.py').write_text('import unittest, os\nfrom pathlib import Path\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(Path(os.environ["TOOLBOX_DATA_ROOT"]).is_dir())\n')
            started = folder/'started'
            (folder/'test_b.py').write_text(f'import time\nfrom pathlib import Path\nPath({str(started)!r}).touch()\ntime.sleep(60)\n')
            report = folder/'report.json'
            code = f'''import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('runner',{str(ROOT/'tooling/run_tests.py')!r})
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
r.SUITES['fixture']={str(folder)!r}
r.run(['fixture'],report=Path({str(report)!r}))
'''
            process = subprocess.Popen([sys.executable,'-c',code], stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                deadline = time.monotonic()+10
                while not started.exists() and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertTrue(started.exists())
                self.assertEqual(json.loads(report.read_text())[0]['module'],'test_a.py')
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=5)
                self.assertEqual(len(json.loads(report.read_text())),1)
                self.assertFalse(list(folder.glob('.report.json-*')))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
