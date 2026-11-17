"""Capture the authoritative Tk layouts using isolated development data."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ["TOOLBOX_DATA_ROOT"] = str(Path.home() / "Library/Application Support/com.electricity.toolbox/development/data/ui-reference")
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]


def main():
    import tkinter as tk
    from PIL import ImageGrab
    from toolbox.app import ToolboxApp
    from toolbox.catalog import default_catalog
    output = ROOT / "logs/ui-reference"
    output.mkdir(parents=True, exist_ok=True)
    with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}):
        app = ToolboxApp(workspace=ROOT)
    app.title("电力工具箱 · 原界面对照")
    app.geometry("1440x820+20+40")
    app.attributes("-topmost", True)
    app.lift()
    failures = []
    app.report_callback_exception = lambda kind, value, trace: failures.append(str(value))

    def tree(widget, origin):
        data = dict(kind=widget.winfo_class(), path=str(widget),
                    x=widget.winfo_rootx()-origin[0], y=widget.winfo_rooty()-origin[1],
                    width=widget.winfo_width(), height=widget.winfo_height(), mapped=bool(widget.winfo_ismapped()))
        for key in ("text", "style", "state", "show", "orient"):
            try: data[key] = str(widget.cget(key))
            except tk.TclError: pass
        if hasattr(widget, "get") and data.get("show", "") not in {"*", "•"}:
            try: data["value"] = widget.get() if data["kind"] not in {"Text", "Listbox"} else widget.get("1.0", "end-1c") if data["kind"] == "Text" else widget.get(0, "end")
            except (tk.TclError, TypeError): pass
        if data["kind"] == "Treeview":
            data["columns"] = widget.cget("columns")
            data["headings"] = [widget.heading(c)["text"] for c in widget.cget("columns")]
            data["rows"] = [widget.item(row)["values"] for row in widget.get_children()]
        data["children"] = [tree(child, origin) for child in widget.winfo_children()]
        return data

    records = []
    try:
        for tool in default_catalog():
            app.show_page(tool.name)
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                app.update()
                page = app.pages.get(tool.name)
                if page is not None and getattr(page, "_writer_load_done", True): break
                time.sleep(.02)
            app.update()
            time.sleep(.08)
            origin = (app.winfo_rootx(), app.winfo_rooty())
            record = dict(id=tool.tool_id, name=tool.name, tree=tree(app, origin))
            records.append(record)
            ImageGrab.grab(bbox=(*origin, origin[0]+app.winfo_width(), origin[1]+app.winfo_height())).save(output / f"{tool.tool_id}.png")
        (output / "widgets.json").write_text(json.dumps(dict(pages=records, failures=failures), ensure_ascii=False, indent=2, default=str))
        print(f"Captured {len(records)} original pages; callback errors: {len(failures)}", flush=True)
    finally:
        app.destroy()


if __name__ == "__main__": main()
