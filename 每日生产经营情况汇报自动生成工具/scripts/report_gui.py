#!/usr/bin/env python3
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import os
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name in {"scripts", "\u811a\u672c"} else SCRIPT_DIR
DOWNLOADS_DIR = Path.home() / "Downloads"
PREFERRED_PYTHON = Path(r"C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe")


def open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(path)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def python_executable() -> str:
    return str(PREFERRED_PYTHON) if PREFERRED_PYTHON.exists() else sys.executable


class ReportTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("每日生产经营情况汇报生成工具")
        self.geometry("920x640")
        self.minsize(820, 560)

        self.messages: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.report_date = tk.StringVar(value=date.today().strftime("%Y-%m-%d"))
        self.online_workbook = tk.StringVar()
        self.day_ahead_workbook = tk.StringVar()
        self.daily_clearing_workbook = tk.StringVar()
        self.template_report = tk.StringVar()
        self.fetch_data = tk.BooleanVar(value=True)
        self.generate_word = tk.BooleanVar(value=True)

        self._build_ui()
        self.after(100, self._drain_messages)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="只选择报告日期和三个 Excel 数据源", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(
            root,
            text="日期口径：报告日用于现货策略和日前出清段落；前一日预留给上网电量段落；前两日预留给日清分表。",
            wraplength=860,
        ).pack(anchor=tk.W, pady=(6, 12))

        form = ttk.Frame(root)
        form.pack(fill=tk.X)
        ttk.Label(form, text="报告日期：").pack(side=tk.LEFT)
        ttk.Entry(form, textvariable=self.report_date, width=18).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(form, text="格式 YYYY-MM-DD，例如 2026-05-13").pack(side=tk.LEFT)
        ttk.Button(form, text="按日期自动匹配 Excel", command=self.auto_fill_workbooks).pack(side=tk.LEFT, padx=(16, 0))

        workbook_box = ttk.LabelFrame(root, text="Excel 数据源")
        workbook_box.pack(fill=tk.X, pady=(14, 8))
        self._add_file_row(workbook_box, "前一日 Excel（上网电量预留）：", self.online_workbook, 0)
        self._add_file_row(workbook_box, "报告日 Excel（日前电量/日前价格）：", self.day_ahead_workbook, 1)
        self._add_file_row(workbook_box, "前两日 Excel（日清分预留）：", self.daily_clearing_workbook, 2)

        template_box = ttk.LabelFrame(root, text="Word 模板")
        template_box.pack(fill=tk.X, pady=(4, 8))
        self._add_file_row(
            template_box,
            "模板报告（可选，不选则自动用前一日报告）：",
            self.template_report,
            0,
            command=lambda: self.choose_template_report(self.template_report),
        )

        options = ttk.Frame(root)
        options.pack(fill=tk.X, pady=(8, 8))
        ttk.Checkbutton(options, text="抓取网站数据并汇总 Excel", variable=self.fetch_data).pack(side=tk.LEFT)
        ttk.Checkbutton(options, text="生成 Word 报告", variable=self.generate_word).pack(side=tk.LEFT, padx=(18, 0))

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(8, 12))
        self.run_button = ttk.Button(actions, text="开始生成", command=self.start)
        self.run_button.pack(side=tk.LEFT)
        ttk.Button(actions, text="打开输出目录", command=self.open_exports).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(actions, text="清空日志", command=lambda: self.log.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=(10, 0))

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(0, 8))

        self.log = tk.Text(root, height=18, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)
        self._write("准备就绪。建议先点“按日期自动匹配 Excel”，确认三个文件后再生成。\n")

    def _add_file_row(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        row: int,
        *,
        command=None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(10, 6), pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky=tk.EW, pady=6)
        ttk.Button(parent, text="浏览", command=command or (lambda: self.choose_workbook(variable))).grid(row=row, column=2, padx=8, pady=6)
        parent.columnconfigure(1, weight=1)

    def choose_workbook(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="选择出清情况 Excel",
            initialdir=str(DOWNLOADS_DIR if DOWNLOADS_DIR.exists() else Path.home()),
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if path:
            variable.set(path)

    def choose_template_report(self, variable: tk.StringVar) -> None:
        output_dir = APP_ROOT / "输出"
        path = filedialog.askopenfilename(
            title="选择 Word 模板报告",
            initialdir=str(output_dir if output_dir.exists() else APP_ROOT),
            filetypes=[("Word 报告", "*.docx"), ("所有文件", "*.*")],
        )
        if path:
            variable.set(path)

    def auto_fill_workbooks(self) -> None:
        try:
            report_day = datetime.strptime(self.report_date.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("日期格式错误", "请填写 YYYY-MM-DD 格式，例如 2026-05-13。")
            return

        mapping = [
            (self.online_workbook, report_day - timedelta(days=1)),
            (self.day_ahead_workbook, report_day),
            (self.daily_clearing_workbook, report_day - timedelta(days=2)),
        ]
        missing: list[str] = []
        for variable, target_day in mapping:
            path = self.find_workbook_for_day(target_day)
            if path is None:
                missing.append(target_day.strftime("%Y-%m-%d"))
            else:
                variable.set(str(path))

        if missing:
            messagebox.showwarning("部分文件未找到", "未在 Downloads 找到这些日期的出清情况 Excel：" + "、".join(missing))

    @staticmethod
    def find_workbook_for_day(target_day: date) -> Path | None:
        date_token = f"{target_day.year}.{target_day.month}.{target_day.day}"
        candidates = [
            path
            for path in DOWNLOADS_DIR.glob("*.xlsx")
            if not path.name.startswith("~$")
            and "出清情况" in path.name
            and date_token in path.name
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def open_exports(self) -> None:
        path = APP_ROOT / "\u8f93\u51fa"
        open_directory(path)

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        report_date = self.report_date.get().strip()
        if not report_date:
            messagebox.showerror("缺少日期", "请填写报告日期。")
            return
        if not self.fetch_data.get() and not self.generate_word.get():
            messagebox.showerror("未选择任务", "请至少选择一个任务。")
            return

        if self.generate_word.get():
            required = [
                ("前一日 Excel", self.online_workbook.get().strip()),
                ("报告日 Excel", self.day_ahead_workbook.get().strip()),
                ("前两日 Excel", self.daily_clearing_workbook.get().strip()),
            ]
            missing = [name for name, path in required if not path]
            if missing:
                messagebox.showerror("缺少 Excel 数据源", "请选择：" + "、".join(missing))
                return
            not_found = [path for _, path in required if not Path(path).exists()]
            template_path = self.template_report.get().strip()
            if template_path and not Path(template_path).exists():
                not_found.append(template_path)
            if not_found:
                messagebox.showerror("Excel 文件不存在", "\n".join(not_found))
                return

        self.run_button.configure(state=tk.DISABLED)
        self.progress.start(10)
        self._write(f"\n开始处理报告日期：{report_date}\n")
        self.worker = threading.Thread(target=self._run_tasks, args=(report_date,), daemon=True)
        self.worker.start()

    def _run_tasks(self, report_date: str) -> None:
        try:
            if self.fetch_data.get():
                self._run_command([python_executable(), str(SCRIPT_DIR / "fetch_daily_data.py"), report_date])
            if self.generate_word.get():
                command = [
                    python_executable(),
                    str(SCRIPT_DIR / "generate_red_marked_report.py"),
                    report_date,
                    "--online-workbook",
                    self.online_workbook.get().strip(),
                    "--day-ahead-workbook",
                    self.day_ahead_workbook.get().strip(),
                    "--daily-clearing-workbook",
                    self.daily_clearing_workbook.get().strip(),
                ]
                template_path = self.template_report.get().strip()
                if template_path:
                    command.extend(["--template", template_path])
                self._run_command(command)
            self.messages.put("\n全部任务完成。\n")
        except Exception as exc:
            self.messages.put(f"\n失败：{exc}\n")
        finally:
            self.messages.put("__DONE__")

    def _run_command(self, command: list[str]) -> None:
        self.messages.put(f"\n> {' '.join(command)}\n")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            command,
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.messages.put(line)
        code = process.wait()
        if code != 0:
            raise RuntimeError(f"命令退出码 {code}: {' '.join(command)}")

    def _drain_messages(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if message == "__DONE__":
                self.progress.stop()
                self.run_button.configure(state=tk.NORMAL)
            else:
                self._write(message)
        self.after(100, self._drain_messages)

    def _write(self, text: str) -> None:
        self.log.insert(tk.END, text)
        self.log.see(tk.END)


def main() -> int:
    app = ReportTool()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
