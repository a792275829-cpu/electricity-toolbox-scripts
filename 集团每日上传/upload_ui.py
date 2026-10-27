"""Group daily upload: Tk window for picking files and running the upload.

The window reads everything it needs from the entry script at call time, so it stays
a thin shell over ``run_api_upload`` and the login helpers.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from upload_files import UPLOAD_TYPES


def choose_files_interactively() -> list[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("无法打开文件选择窗口，请改用命令行传入两个文件路径。") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askopenfilenames(
        title="请选择能销数据 Excel；也可同时选择省内数据",
        initialdir=str(SCRIPT_DIR),
        filetypes=[
            ("Excel 文件", "*.xlsx *.xls"),
            ("所有文件", "*.*"),
        ],
    )
    root.destroy()
    return [Path(item) for item in selected]


def run_gui() -> None:
    from upload_daily_report import (
        DEFAULT_PROVINCE_NAME,
        SCRIPT_DIR,
        choose_files_interactively,
        current_log_sink,
        interactive_login,
        launch_context,
        load_config,
        prepare_upload_files,
        run_api_upload,
        set_log_sink,
    )

    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        raise RuntimeError("当前 Python 环境不可用 tkinter，无法打开图形界面。") from exc

    root = tk.Tk()
    root.title("集团日报上传")
    root.geometry("760x520")
    root.minsize(680, 460)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    selected_paths: list[Path] = []
    log_events: queue.Queue[tuple[str, str]] = queue.Queue()
    force_var = tk.BooleanVar(value=False)
    summary_var = tk.StringVar(value="请选择能销数据 Excel；省内数据缺省时使用内置模板")
    file_lines_var = tk.StringVar(value="未选择文件")

    def gui_log(message: str) -> None:
        log_events.put(("log", message))

    def append_log(message: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", message + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def refresh_selection_text() -> None:
        if not selected_paths:
            file_lines_var.set("未选择文件")
            summary_var.set("请选择能销数据 Excel；省内数据缺省时使用内置模板")
            return
        try:
            target_date, uploads = prepare_upload_files(selected_paths)
            lines = [f"{UPLOAD_TYPES[item.kind]['name']}：{item.path.name}" for item in uploads]
            file_lines_var.set("\n".join(lines))
            summary_var.set(f"已识别日期：{target_date}；目标省份：{DEFAULT_PROVINCE_NAME}")
        except Exception as exc:
            file_lines_var.set("\n".join(path.name for path in selected_paths))
            summary_var.set(f"文件校验失败：{exc}")

    def choose_files() -> None:
        nonlocal selected_paths
        selected = filedialog.askopenfilenames(
            title="请选择能销数据 Excel；也可同时选择省内数据",
            initialdir=str(SCRIPT_DIR),
            filetypes=[
                ("Excel 文件", "*.xlsx *.xls"),
                ("所有文件", "*.*"),
            ],
        )
        if not selected:
            return
        selected_paths = [Path(item) for item in selected]
        refresh_selection_text()

    def set_busy(is_busy: bool) -> None:
        state = "disabled" if is_busy else "normal"
        choose_button.configure(state=state)
        upload_button.configure(state=state)
        login_button.configure(state=state)
        force_check.configure(state=state)

    def start_upload() -> None:
        if not selected_paths:
            messagebox.showwarning("缺少文件", "请先选择能销数据 Excel 文件。")
            return
        try:
            prepare_upload_files(selected_paths)
        except Exception as exc:
            messagebox.showerror("文件校验失败", str(exc))
            return

        set_busy(True)
        append_log("")
        append_log("开始处理...")

        def worker() -> None:
            old_sink = current_log_sink()
            set_log_sink(gui_log)
            try:
                run_api_upload(selected_paths, force=force_var.get())
                log_events.put(("done", "处理完成。"))
            except Exception as exc:
                log_events.put(("error", str(exc)))
            finally:
                set_log_sink(old_sink)
                log_events.put(("idle", ""))

        threading.Thread(target=worker, daemon=True).start()

    def start_login() -> None:
        set_busy(True)
        append_log("")
        append_log("打开浏览器登录窗口...")

        def worker() -> None:
            old_sink = current_log_sink()
            set_log_sink(gui_log)
            try:
                config = load_config()
                with sync_playwright() as playwright:
                    context = launch_context(playwright, headless=False)
                    try:
                        interactive_login(context, config)
                    finally:
                        context.close()
                log_events.put(("done", "登录态已保存。"))
            except Exception as exc:
                log_events.put(("error", str(exc)))
            finally:
                set_log_sink(old_sink)
                log_events.put(("idle", ""))

        threading.Thread(target=worker, daemon=True).start()

    def poll_log_events() -> None:
        try:
            while True:
                kind, message = log_events.get_nowait()
                if kind == "log":
                    append_log(message)
                elif kind == "done":
                    append_log(message)
                    messagebox.showinfo("完成", message)
                elif kind == "error":
                    append_log(f"失败：{message}")
                    messagebox.showerror("失败", message)
                elif kind == "idle":
                    set_busy(False)
        except queue.Empty:
            pass
        root.after(120, poll_log_events)

    outer = ttk.Frame(root, padding=16)
    outer.pack(fill="both", expand=True)

    header = ttk.Frame(outer)
    header.pack(fill="x")
    ttk.Label(header, text="集团日报上传", font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
    ttk.Label(header, text=f"{DEFAULT_PROVINCE_NAME}  接口模式", foreground="#555").pack(side="right")

    file_panel = ttk.LabelFrame(outer, text="文件", padding=12)
    file_panel.pack(fill="x", pady=(14, 10))
    ttk.Label(file_panel, textvariable=summary_var).pack(anchor="w")
    ttk.Label(file_panel, textvariable=file_lines_var, foreground="#333", justify="left").pack(anchor="w", pady=(8, 0))

    actions = ttk.Frame(outer)
    actions.pack(fill="x", pady=(0, 10))
    from toolbox_shared.file_button import FileSelectButton
    choose_button = FileSelectButton(actions, text="选择文件", command=choose_files)
    choose_button.pack(side="left")
    upload_button = ttk.Button(actions, text="开始上传", command=start_upload)
    upload_button.pack(side="left", padx=(8, 0))
    login_button = ttk.Button(actions, text="登录/刷新登录态", command=start_login)
    login_button.pack(side="left", padx=(8, 0))
    force_check = ttk.Checkbutton(actions, text="已上传也覆盖", variable=force_var)
    force_check.pack(side="right")

    log_panel = ttk.LabelFrame(outer, text="日志", padding=8)
    log_panel.pack(fill="both", expand=True)
    log_box = tk.Text(log_panel, height=12, wrap="word", state="disabled", font=("Consolas", 10))
    log_scroll = ttk.Scrollbar(log_panel, orient="vertical", command=log_box.yview)
    log_box.configure(yscrollcommand=log_scroll.set)
    log_box.pack(side="left", fill="both", expand=True)
    log_scroll.pack(side="right", fill="y")

    append_log("操作：选择能销 Excel -> 开始上传。上传完成后会自动新建集团日报。")
    poll_log_events()
    root.mainloop()
