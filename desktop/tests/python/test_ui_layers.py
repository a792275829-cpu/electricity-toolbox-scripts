"""Preserve native widgets while routing execution to the business service."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱')]
from toolbox.ui_tasks import RemoteTaskRegistry
from toolbox.service import ToolboxService


class LocalTransport:
    def __init__(self, service): self.service = service
    def call(self, method, params=None): return self.service.call(method, params or {})


class NativeLayerTests(unittest.TestCase):
    def test_original_widgets_and_geometry_match_with_remote_registry(self):
        from toolbox.app import ToolboxApp
        from toolbox.runtime import TaskRegistry
        from toolbox.catalog import default_catalog
        def tree(widget):
            try: text = str(widget.cget('text'))
            except Exception: text = ''
            if text.startswith('PY_VAR'):
                text = widget.get() if hasattr(widget, 'get') else ''
            return (widget.winfo_class(), text, widget.winfo_x(), widget.winfo_y(),
                    widget.winfo_width(), widget.winfo_height(), tuple(tree(w) for w in widget.winfo_children()))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TOOLBOX_DATA_ROOT': directory}), patch('toolbox.ui.credentials_panel.CredentialStore.load', return_value={}):
            signatures = []
            for registry in [TaskRegistry(), RemoteTaskRegistry(LocalTransport(ToolboxService(ROOT)))]:
                app = ToolboxApp(workspace=ROOT, registry=registry)
                try:
                    pages = []
                    for descriptor in default_catalog():
                        app.show_page(descriptor.name)
                        app.update_idletasks()
                        pages.append((descriptor.name, tree(app.pages[descriptor.name])))
                    signatures.append((tree(app.sidebar), pages))
                finally: app.destroy()
            self.assertEqual(signatures[0], signatures[1])

    def test_original_export_button_uses_remote_business_process(self):
        from toolbox.app import ToolboxApp
        with tempfile.TemporaryDirectory() as directory, patch('toolbox.pages.messagebox.showinfo'):
            root = Path(directory)
            script = root / '上网电量抓取/export_online_energy.py'
            script.parent.mkdir()
            script.write_text('print("REMOTE_UI_SAMPLE_OK",flush=True)\n')
            service = ToolboxService(root)
            registry = RemoteTaskRegistry(LocalTransport(service))
            app = ToolboxApp(workspace=root, registry=registry)
            try:
                page = app.pages['导出上网电量']
                page.run_date.set('2026-10-20')
                page.start_export()
                deadline = time.monotonic() + 12
                while page.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(.02)
                self.assertFalse(page.busy)
                self.assertIn('REMOTE_UI_SAMPLE_OK', page.log_console.contents())
                self.assertEqual(service.registry.snapshots()[0].state.value, 'succeeded')
                self.assertEqual(registry.snapshots()[0].state.value, 'succeeded')
            finally:
                registry.shutdown(timeout=5)
                service.registry.shutdown(timeout=5)
                app.destroy()

    def test_remote_failure_preserves_original_trace_once_in_view_and_file(self):
        import gc
        import tkinter as tk
        from toolbox.widgets import ToolPage

        gc.collect()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TOOLBOX_DATA_ROOT': directory}), patch('toolbox.widgets.messagebox.showerror'):
            root = Path(directory)
            script = root / '上网电量抓取/export_online_energy.py'
            script.parent.mkdir()
            script.write_text('raise TimeoutError("original remote timeout")\n')
            service = ToolboxService(root)
            registry = RemoteTaskRegistry(LocalTransport(service))
            window = tk.Tk()
            window.withdraw()
            page = ToolPage(window, registry=registry, title='日志回归')
            def worker(token):
                return registry.execute_remote('online-energy', 'run', {'run_date': '2026-10-20'}, token, page.append_log)
            worker._remote_business = True
            try:
                page.run_in_thread(worker, status='运行中')
                deadline = time.monotonic() + 12
                while page.busy and time.monotonic() < deadline:
                    page._poll_queues()
                    window.update_idletasks()
                    time.sleep(.01)
                self.assertFalse(page.busy)
                text = page.log_console.contents()
                # One business traceback and one worker traceback; neither is
                # replayed by the service error or by a local UI traceback.
                self.assertEqual(2, text.count('Traceback (most recent call last):'))
                self.assertEqual(1, text.count('执行失败 · 耗时'))
                self.assertEqual(1, text.count('建议：'))
                self.assertIn('命令退出码 1', text)
                self.assertIn('超时', page.log_console.summary.get())
                self.assertEqual(text, page.log_console.path.read_text())
                self.assertEqual(text, page.log_text.get('1.0', 'end-1c'))
            finally:
                registry.shutdown(timeout=5)
                service.registry.shutdown(timeout=5)
                page.destroy()
                window.destroy()


if __name__ == '__main__': unittest.main()
