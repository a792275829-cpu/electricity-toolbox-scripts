"""Debug-only Rust broker smoke entry; operate only on disposable sample copies."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱"), str(ROOT / "desktop/python")]
import native_bridge
from toolbox.app import ToolboxApp


class SmokeApp(ToolboxApp):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.withdraw()
        self.fixture = Path(os.environ["TOOLBOX_UI_SMOKE_DIR"])
        self.deadline = time.monotonic() + 30
        self.after(100, self.begin)

    def begin(self):
        self.show_page("500kV断面分析")
        page = self.pages["500kV断面分析"]
        page.set_files([self.fixture / "prices.xlsx"])
        page.output_root.set(str(self.fixture / "output"))
        page.start_analysis()
        self.after(100, self.finish)

    def finish(self):
        page = self.pages["500kV断面分析"]
        if page.busy and time.monotonic() < self.deadline:
            self.after(100, self.finish)
            return
        result = dict(ui_pid=os.getpid(), service=self.registry.client.call("ping"),
                      state=self.registry.snapshots()[-1].state.value,
                      html_exists=(self.fixture / "output/prices_500kV分析.html").is_file(),
                      log=page.log_console.contents())
        (self.fixture / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        self.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--remote-business", action="store_true")
    args = parser.parse_args()
    native_bridge.ToolboxApp = SmokeApp
    with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}), patch("toolbox.section_analysis_page.messagebox.showinfo"), patch("toolbox.widgets.messagebox.showerror"):
        native_bridge.serve(args.workspace, remote_business=True)
