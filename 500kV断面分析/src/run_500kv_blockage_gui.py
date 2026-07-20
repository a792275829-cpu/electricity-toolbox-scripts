from __future__ import annotations

import queue
import re
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.generate_500kv_blockage_map import generate_blockage_map


MODULE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPOLOGY = MODULE_ROOT / "500kV节点拓扑.md"
DEFAULT_OUTPUT_DIR = MODULE_ROOT / "result"


def safe_folder_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return cleaned or "analysis"


def main() -> None:
    root = tk.Tk()
    root.title("500kV 断面分析")
    root.geometry("860x560")

    topology_var = tk.StringVar(value=str(DEFAULT_TOPOLOGY))
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_dir_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
    threshold_var = tk.StringVar(value="100")
    selected_files: list[Path] = []
    running = tk.BooleanVar(value=False)
    ui_queue: queue.Queue[tuple[str, str, str | None]] = queue.Queue()

    frame = tk.Frame(root, padx=14, pady=14)
    frame.pack(fill=tk.BOTH, expand=True)

    def row(label: str, row_index: int) -> tk.Entry:
        tk.Label(frame, text=label, anchor="w").grid(row=row_index, column=0, sticky="w", pady=5)
        entry = tk.Entry(frame)
        entry.grid(row=row_index, column=1, sticky="ew", padx=(8, 8), pady=5)
        return entry

    frame.columnconfigure(1, weight=1)

    topology_entry = row("拓扑 Markdown", 0)
    topology_entry.configure(textvariable=topology_var)

    def choose_topology() -> None:
        path = filedialog.askopenfilename(
            title="选择拓扑 Markdown",
            initialfile=topology_var.get(),
            filetypes=[("Markdown", "*.md"), ("All files", "*.*")],
        )
        if path:
            topology_var.set(path)

    tk.Button(frame, text="选择", command=choose_topology).grid(row=0, column=2, sticky="ew", pady=5)

    output_entry = row("输出目录", 1)
    output_entry.configure(textvariable=output_dir_var)

    def choose_output_dir() -> None:
        path = filedialog.askdirectory(title="选择输出目录", initialdir=output_dir_var.get())
        if path:
            output_dir_var.set(path)

    tk.Button(frame, text="选择", command=choose_output_dir).grid(row=1, column=2, sticky="ew", pady=5)

    tk.Label(frame, text="价差阈值", anchor="w").grid(row=2, column=0, sticky="w", pady=5)
    threshold_entry = tk.Entry(frame, textvariable=threshold_var, width=12)
    threshold_entry.grid(row=2, column=1, sticky="w", padx=(8, 8), pady=5)

    files_label = tk.StringVar(value="未选择原始电价 Excel")
    tk.Label(frame, textvariable=files_label, anchor="w").grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 5))

    def choose_files() -> None:
        paths = filedialog.askopenfilenames(
            title="选择一份或多份原始节点电价 Excel",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if not paths:
            return
        selected_files.clear()
        selected_files.extend(Path(path) for path in paths)
        files_label.set(f"已选择 {len(selected_files)} 份原始数据")
        log.delete("1.0", tk.END)
        for path in selected_files:
            write_log(f"已选择: {path}")

    tk.Button(frame, text="选择原始数据", command=choose_files).grid(row=3, column=2, sticky="ew", pady=(10, 5))

    log = scrolledtext.ScrolledText(frame, height=20, wrap=tk.WORD)
    log.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 8))
    frame.rowconfigure(4, weight=1)

    def write_log(message: str) -> None:
        log.insert(tk.END, message + "\n")
        log.see(tk.END)
        root.update_idletasks()

    def write_log_async(message: str) -> None:
        ui_queue.put(("log", message, None))

    def show_info_async(title: str, message: str) -> None:
        ui_queue.put(("info", title, message))

    def show_error_async(title: str, message: str) -> None:
        ui_queue.put(("error", title, message))

    def finish_async() -> None:
        ui_queue.put(("finish", "", None))

    def poll_ui_queue() -> None:
        while True:
            try:
                kind, value, extra = ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                write_log(value)
            elif kind == "info":
                messagebox.showinfo(value, extra or "")
            elif kind == "error":
                messagebox.showerror(value, extra or "")
            elif kind == "finish":
                running.set(False)
                set_buttons_state(tk.NORMAL)

        root.after(80, poll_ui_queue)

    def set_buttons_state(state: str) -> None:
        start_button.configure(state=state)

    def run_batch(topology_text: str, output_dir_text: str, threshold_text: str, raw_files: list[Path]) -> None:
        try:
            topology_path = Path(topology_text).expanduser()
            output_dir = Path(output_dir_text).expanduser()
            threshold = float(threshold_text)
            if not topology_path.exists():
                raise FileNotFoundError(f"拓扑文件不存在: {topology_path}")
            if not raw_files:
                raise ValueError("请先选择一份或多份原始节点电价 Excel。")

            output_dir.mkdir(parents=True, exist_ok=True)
            write_log_async("")
            write_log_async("开始批量分析...")
            success = 0
            for index, raw_path in enumerate(raw_files, start=1):
                write_log_async("")
                write_log_async(f"[{index}/{len(raw_files)}] {raw_path.name}")
                case_dir = output_dir / safe_folder_name(raw_path.stem)
                try:
                    result = generate_blockage_map(
                        topology_path=topology_path,
                        raw_price_path=raw_path,
                        output_dir=case_dir,
                        threshold=threshold,
                    )
                except Exception as exc:
                    error_text = traceback.format_exc()
                    write_log_async(f"失败: {exc}")
                    write_log_async(error_text)
                    continue

                success += 1
                write_log_async(f"阻塞地图: {result.html_path}")
                write_log_async(f"保留500kV行数: {result.kept_rows}；删除行数: {result.removed_rows}")
                write_log_async(f"触发断面边数: {result.flagged_edges}；触发时段数: {result.flagged_intervals}")

            write_log_async("")
            write_log_async(f"批量完成: 成功 {success}/{len(raw_files)} 份。")
            if success:
                show_info_async("完成", f"成功分析 {success} 份数据。")
            else:
                show_error_async("未生成结果", "所有数据都分析失败，请查看日志中的错误详情。")
        except Exception as exc:
            error_text = traceback.format_exc()
            show_error_async("错误", str(exc))
            write_log_async(f"错误: {exc}")
            write_log_async(error_text)
        finally:
            finish_async()

    def start() -> None:
        if running.get():
            return
        topology_text = topology_var.get()
        output_dir_text = output_dir_var.get()
        threshold_text = threshold_var.get()
        raw_files = list(selected_files)
        running.set(True)
        set_buttons_state(tk.DISABLED)
        threading.Thread(
            target=run_batch,
            args=(topology_text, output_dir_text, threshold_text, raw_files),
            daemon=True,
        ).start()

    start_button = tk.Button(frame, text="开始分析", height=2, command=start)
    start_button.grid(row=5, column=0, columnspan=3, sticky="ew")

    root.after(80, poll_ui_queue)
    root.mainloop()


if __name__ == "__main__":
    main()
