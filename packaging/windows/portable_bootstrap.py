"""Entry point shared by the relocatable GUI and diagnostic launchers."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def configure(root: Path) -> None:
    root = root.resolve()
    runtime = root / "runtime"
    os.environ["TOOLBOX_PORTABLE"] = "1"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(runtime / "ms-playwright")
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ.pop("PYTHONHOME", None)
    os.environ.pop("PYTHONPATH", None)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.environ["PATH"] = os.pathsep.join((
        str(runtime / "python311"), str(runtime / "node"), os.environ.get("PATH", "")
    ))
    sys.path[:0] = [str(root), str(root / "电力工具箱")]
    os.chdir(root)


def main(root: Path, *, diagnostics: bool = False) -> int:
    configure(root)
    try:
        if diagnostics or "--self-test" in sys.argv:
            from portable_smoke import run

            return run(root)
        from toolbox.app import main as app_main

        return app_main()
    except Exception:
        log_path = root / "logs" / "windows-startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        if diagnostics:
            traceback.print_exc()
        else:
            import tkinter as tk
            from tkinter import messagebox

            app = tk.Tk()
            app.withdraw()
            messagebox.showerror("电力工具箱启动失败", f"请运行“环境自检.exe”。\n错误日志：{log_path}")
            app.destroy()
        return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parents[2]))

