import argparse
import pathlib
import subprocess
import sys
import threading


WORKSPACE = pathlib.Path(__file__).resolve().parents[2]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
from toolbox_shared.native_excel import load_value_workbook
from toolbox_shared.trade_batch import plan_batch_outputs, iter_batch_reports
from toolbox_shared.trade_report import (
    THERMAL_COMPANIES,
    RENEWABLE_UNITS,
    POINT_TYPE_ORDER,
    num,
    fmt,
    esc,
    normalize_pair_label,
    merge_offer_segments,
    to_hour_number,
    classify_point,
    iter_row_windows,
    read_cell_values,
    ReportBuilder,
)
DEFAULT_OUTPUT_NAME = "electricity_clearing_report.html"
DEFAULT_OUTPUT_DIR = pathlib.Path.home() / "Downloads"


def open_directory(path):
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def generate_report(xlsx_path, out_path=None):
    return ReportBuilder(xlsx_path, out_path=out_path).build()


def batch_reports(xlsx_paths, output_dir, *, continue_on_error=False):
    return iter_batch_reports(xlsx_paths, output_dir, generate_report,
                              continue_on_error=continue_on_error)


def generate_batch(xlsx_paths, output_dir):
    return [result for _, _, result, _ in batch_reports(xlsx_paths, output_dir)]


def _load_gui():
    # CLI/service users can run even when Tcl/Tk is not installed.
    global tk, filedialog, messagebox, ttk, FileSelectButton
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from toolbox_shared.file_button import FileSelectButton


class ReportApp:
    def __init__(self, root):
        _load_gui()
        self.root = root
        self.root.title("电力交易出清分析")
        self.root.geometry("860x620")
        self.files = []
        self.output_dir = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.status = tk.StringVar(value="请选择一个或多个 Excel 文件。")
        self._build_ui()

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text="电力交易出清分析", font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor="w")
        ttk.Label(frame, text="可一次选择多个出清 Excel，批量生成 HTML 报告。", foreground="#667085").pack(anchor="w", pady=(4, 14))

        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x")
        FileSelectButton(toolbar, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(toolbar, text="移除选中", command=self.remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="清空列表", command=self.clear_files).pack(side="left", padx=(8, 0))

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True, pady=(12, 12))
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, font=("Consolas", 10))
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        output_frame = ttk.LabelFrame(frame, text="输出目录", padding=12)
        output_frame.pack(fill="x")
        ttk.Entry(output_frame, textvariable=self.output_dir).pack(side="left", fill="x", expand=True)
        FileSelectButton(output_frame, text="选择目录", command=self.choose_output_dir).pack(side="left", padx=(8, 0))

        action_frame = ttk.Frame(frame)
        action_frame.pack(fill="x", pady=(14, 8))
        self.run_button = ttk.Button(action_frame, text="开始分析", command=self.run_batch)
        self.run_button.pack(side="left")
        ttk.Button(action_frame, text="打开输出目录", command=self.open_output_dir).pack(side="left", padx=(8, 0))

        log_frame = ttk.LabelFrame(frame, text="运行日志", padding=12)
        log_frame.pack(fill="both", expand=False, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=10, wrap="word", state="disabled", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True)

        status_bar = ttk.Label(frame, textvariable=self.status, foreground="#475467")
        status_bar.pack(anchor="w", pady=(10, 0))

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择出清 Excel 文件",
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if not paths:
            return
        existing = {str(path) for path in self.files}
        for path in paths:
            if path not in existing:
                self.files.append(pathlib.Path(path))
                self.listbox.insert(tk.END, path)
        self.status.set(f"已选择 {len(self.files)} 个文件。")

    def remove_selected(self):
        selected = list(self.listbox.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self.files[index]
            self.listbox.delete(index)
        self.status.set(f"剩余 {len(self.files)} 个文件。")

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, tk.END)
        self.status.set("文件列表已清空。")

    def choose_output_dir(self):
        path = filedialog.askdirectory(title="选择输出目录", initialdir=self.output_dir.get())
        if path:
            self.output_dir.set(path)

    def open_output_dir(self):
        path = pathlib.Path(self.output_dir.get())
        open_directory(path)

    def append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def run_batch(self):
        if not self.files:
            messagebox.showwarning("没有文件", "请先添加至少一个 Excel 文件。")
            return
        output_dir = pathlib.Path(self.output_dir.get())
        output_dir.mkdir(parents=True, exist_ok=True)
        self.run_button.configure(state="disabled")
        self.status.set("正在生成报告，请稍候...")
        self.append_log(f"开始批量分析，共 {len(self.files)} 个文件。")
        threading.Thread(target=self._run_batch_worker, daemon=True).start()

    def _run_batch_worker(self):
        success = []
        errors = []
        output_dir = pathlib.Path(self.output_dir.get())
        try:
            for path, out_path, _, error in batch_reports(self.files, output_dir, continue_on_error=True):
                if error is None:
                    success.append(out_path)
                    self.root.after(0, self.append_log, f"完成：{path.name} -> {out_path.name}")
                else:
                    errors.append((path, error))
                    self.root.after(0, self.append_log, f"失败：{path.name} -> {error}")
        except ValueError as exc:
            self.root.after(0, self.append_log, str(exc))
            self.root.after(0, self._finish_batch, [], [(None, exc)])
            return
        self.root.after(0, self._finish_batch, success, errors)

    def _finish_batch(self, success, errors):
        self.run_button.configure(state="normal")
        if errors:
            self.status.set(f"完成 {len(success)} 个，失败 {len(errors)} 个。")
            messagebox.showwarning("部分完成", f"成功 {len(success)} 个，失败 {len(errors)} 个。请查看日志。")
        else:
            self.status.set(f"全部完成，共生成 {len(success)} 个报告。")
            messagebox.showinfo("完成", f"已生成 {len(success)} 个报告。")


def launch_gui():
    _load_gui()
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:  # noqa: BLE001
        pass
    ReportApp(root)
    root.mainloop()


def parse_args():
    parser = argparse.ArgumentParser(description="电力交易出清分析报告生成器")
    parser.add_argument("xlsx", nargs="*", help="一个或多个 Excel 文件路径")
    parser.add_argument("--output-dir", help="批量输出目录")
    parser.add_argument("--output", help="单文件输出 HTML 路径")
    parser.add_argument("--no-gui", action="store_true", help="强制命令行模式")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.no_gui and not args.xlsx:
        launch_gui()
        return
    if not args.xlsx:
        raise SystemExit("命令行模式下至少传入一个 Excel 文件。")
    if len(args.xlsx) == 1:
        out_path = pathlib.Path(args.output) if args.output else DEFAULT_OUTPUT_DIR / DEFAULT_OUTPUT_NAME
        result = generate_report(args.xlsx[0], out_path=out_path)
        print(result)
        return
    output_dir = pathlib.Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    for result in generate_batch(args.xlsx, output_dir):
        print(result)


if __name__ == "__main__":
    main()
