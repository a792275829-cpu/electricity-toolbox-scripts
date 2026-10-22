"""Shared native log console for toolbox pages and the standalone WPS writer."""
from __future__ import annotations

import json
import re
import time
import uuid
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from toolbox_storage import data_path
from toolbox_redaction import redact


class _LogBuffer:
    """Index complete log chunks without joining the entire run for each page."""
    def __init__(self):
        self.chunks = []
        self.ends = []
        self.length = 0

    def append(self, content):
        if content:
            self.chunks.append(content)
            self.length += len(content)
            self.ends.append(self.length)

    def slice(self, start, end):
        index = bisect_right(self.ends, start)
        parts = []
        while index < len(self.chunks) and start < end:
            offset = self.ends[index - 1] if index else 0
            part = self.chunks[index][start - offset:end - offset]
            parts.append(part)
            start += len(part)
            index += 1
        return "".join(parts)

    def find(self, query, start=0, *, ignore_case=False):
        index = bisect_right(self.ends, start)
        carry = ""
        pattern = re.compile(re.escape(query), re.IGNORECASE) if ignore_case else None
        while index < len(self.chunks):
            offset = self.ends[index - 1] if index else 0
            offset = max(start, offset)
            part = self.chunks[index][offset - (self.ends[index - 1] if index else 0):]
            block = carry + part
            match = pattern.search(block) if pattern is not None else None
            found = (match.start() if match else -1) if pattern is not None else block.find(query)
            if found >= 0:
                return offset - len(carry) + found
            carry = block[-(len(query) - 1):] if len(query) > 1 else ""
            index += 1
        return -1



def explain_error(text: str) -> tuple[str, str] | None:
    """Use specific failure signals, never ordinary mentions of login/network."""
    lower = text.lower()
    rules = (
        (("modulenotfounderror", "importerror", "no module named", "缺少运行依赖"), "缺少运行依赖", "开发版请运行 setup_macos.command；安装版请更新或重新安装工具箱。"),
        (("executable doesn't exist", "playwright install", "找不到浏览器"), "浏览器组件缺失", "开发版请重新初始化环境；安装版请更新或重新安装工具箱。"),
        (("permissionerror", "permission denied", "operation not permitted", "只读", "文件被占用"), "文件访问权限不足或被占用", "关闭占用文件的 Excel/WPS，检查文件和输出目录的访问权限后重试。"),
        (("filenotfounderror", "no such file or directory", "文件不存在", "找不到文件"), "找不到输入文件或程序", "重新选择输入文件，确认文件未移动且安装目录完整。"),
        (("登录状态已过期", "登录状态不可用", "登录失败", "登录过期", "登录已过期", "未登录", "请先登录", "unauthorized", "status 401", "认证失败"), "登录状态失效或认证失败", "在本模块重新登录，检查账号后重试；各模块登录状态独立。"),
        (("status 403", "forbidden", "无访问权限"), "没有访问权限", "确认账号有目标网页或文档的访问、编辑权限。"),
        (("等待登录态锁超时", "profile is already in use", "processsingleton", "浏览器配置被占用"), "浏览器或登录状态正在被占用", "等待本模块其他任务退出，再关闭其专用浏览器后重试。"),
        (("name_not_resolved", "getaddrinfo failed", "connection refused", "connection reset", "网络连接失败"), "网络连接失败", "检查网络、代理和目标网站是否能正常访问，再重试。"),
        (("timeout", "timed out", "超时"), "请求或等待超时", "检查网络及目标网站；若需登录，完成登录后重试。上传或写入前先确认是否已有部分成功。"),
        (("no space left on device", "磁盘空间不足"), "磁盘空间不足", "释放输出目录所在磁盘的空间后重试，先核对是否已有部分输出。"),
        (("jsondecodeerror", "expecting value: line 1 column 1", "接口返回非json"), "接口返回了无法解析的内容", "检查网站是否正常、登录是否失效；若网站已改版，请复制错误详情以便适配。"),
        (("certificate_verify_failed", "err_cert_"), "安全连接证书校验失败", "检查系统日期、网络代理及目标网站证书，勿关闭证书校验。"),
        (("badzipfile", "not a zip file", "invalidfileexception", "文件损坏"), "工作簿格式不正确或文件损坏", "用 Excel/WPS 打开文件并另存为有效的 .xlsx 文件，再重新选择。"),
        (("worksheet does not exist", "worksheet named", "工作表不存在", "未找到工作表", "找不到工作表"), "工作表或模板不匹配", "核对所选文件、工作表名称和模板版本，保留原始文件后重试。"),
        (("回读不一致", "回读数据与来源不一致"), "写入结果与来源不一致", "核对目标文档、工作表和写入区域；检查已写入内容后再决定是否重试。"),
    )
    for markers, summary, advice in rules:
        if any(marker in lower for marker in markers):
            return summary, advice
    return None


class LogConsole(ttk.Frame):
    DISPLAY_CHAR_LIMIT = 250_000
    DISPLAY_LINE_LIMIT = 4_000
    EXPANDED_PAGE_SIZE = 16_000

    def __init__(self, parent, *, module: str, directory: Path | None = None):
        super().__init__(parent)
        self.module = module
        self.directory = directory or data_path(Path(__file__).resolve().parent / "logs" / "runs")
        self._initialized = False
        self.finished = False
        self.path: Path | None = None
        self.started = time.monotonic()
        self.failure = ""
        self.storage_error = ""
        self._log = _LogBuffer()
        self._expanded = None
        self._expanded_text = None
        self._expanded_source = self._log
        self._expanded_start = 0
        self._expanded_follow = True
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.header = ttk.Frame(self)
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.header.columnconfigure(1, weight=1)
        ttk.Label(self.header, text="运行日志", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.status = tk.StringVar(self, "")
        ttk.Label(self.header, textvariable=self.status).grid(row=0, column=1, sticky="w", padx=12)
        self.copy_button = ttk.Button(self.header, text="复制错误", command=self.copy_error, style="Primary.TButton")
        self.copy_button.grid(row=0, column=2, padx=(0, 6))
        self.expand_button = ttk.Button(self.header, text="展开", command=self.expand)
        self.expand_button.grid(row=0, column=3)

        self.body = ttk.Frame(self, style="Card.TFrame", padding=8)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(1, weight=1)
        self.summary = tk.StringVar(self, "")
        self.summary_label = ttk.Label(self.body, textvariable=self.summary, foreground="#b42318", wraplength=750)
        self.summary_label.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.summary_label.grid_remove()
        self.summary.trace_add("write", lambda *_: self._show_if_nonempty(self.summary_label, self.summary))
        self.bind("<Configure>", self._resize_summary)
        self.text = tk.Text(self.body, height=6, wrap="word", font="TkFixedFont", borderwidth=0, state="disabled")
        self.text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.body, orient="vertical", command=self.text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        self.text.tag_configure("error", foreground="#b42318")
        for key in ("<Command-a>", "<Control-a>"):
            self.text.bind(key, self._select_all)
        self.notice = tk.StringVar(self, "")
        self.notice_label = ttk.Label(self, textvariable=self.notice, wraplength=750)
        self.notice_label.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.notice_label.grid_remove()
        self.notice.trace_add("write", lambda *_: self._show_if_nonempty(self.notice_label, self.notice))

    @staticmethod
    def _show_if_nonempty(widget, variable):
        if variable.get():
            widget.grid()
        else:
            widget.grid_remove()

    def _resize_summary(self, event):
        width = max(180, event.width - 24)
        if int(self.summary_label.cget("wraplength")) != width:
            self.summary_label.configure(wraplength=width)

    def _select_all(self, _event):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def begin(self, name="", *, render=True):
        self._initialized = True
        self.finished = False
        self.started = time.monotonic()
        self.failure = ""
        if render:
            self.summary.set("")
            self.notice.set("")
            self.status.set(name)
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
        self.path = None
        self._log = _LogBuffer()
        if self._expanded_text is not None and self._expanded_text.winfo_exists() and getattr(self, "_view_current", True):
            self._expanded_source = self._log
            if render:
                self._refresh_expanded(follow=True)
        self.storage_error = ""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.path = self.directory / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.log"
            self.path.touch(mode=0o600)
        except OSError as exc:
            self.storage_error = f"日志未能保存：{exc}"
            if render:
                self.notice.set(self.storage_error)
        root = Path(__file__).resolve().parent
        version = "未知"
        for version_path in (root / "desktop/src-tauri/tauri.conf.json", root.parent / "manifest.json"):
            try:
                version = json.loads(version_path.read_text(encoding="utf-8"))["version"]
                break
            except (OSError, ValueError, KeyError):
                continue
        self.append(f"模块：{self.module}  版本：{version}  开始时间：{datetime.now():%Y-%m-%d %H:%M:%S}", render=render)

    def append(self, content: str, *, render=True):
        if not self._initialized:
            self.begin(render=render)
        content = redact(content.rstrip("\n")) + "\n"
        # Keep all text even if storage becomes unavailable during a run.
        self._log.append(content)
        if self.path is not None:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(content)
            except OSError as exc:
                self.storage_error = f"日志未能保存：{exc}"
                if render:
                    self.notice.set(self.storage_error)
        if not render:
            return
        follow = self.text.yview()[1] >= 0.995
        top = self.text.index("@0,0")
        self.text.configure(state="normal")
        tag = "error" if any(x in content.lower() for x in ("error", "失败", "超时", "traceback", "不一致")) else ""
        # Trim before inserting: a single large traceback/JSON line can make
        # Tk word wrapping expensive even when the line count stays below 5000.
        lines = content[-self.DISPLAY_CHAR_LIMIT:].splitlines(keepends=True)[-5000:]
        visible = "".join(line if len(line) <= self.DISPLAY_LINE_LIMIT else
                          "…" + line[-self.DISPLAY_LINE_LIMIT:] for line in lines)
        self.text.insert("end", visible, tag)
        excess = max(0, int(self.text.index("end-1c").split(".")[0]) - 1 - 5000)
        if excess:
            self.text.delete("1.0", f"{excess + 1}.0")
        cutoff = self.text.index(f"end-1c-{self.DISPLAY_CHAR_LIMIT}c")
        if self.text.compare(cutoff, ">", "1.0"):
            excess += int(cutoff.split(".")[0]) - 1
            self.text.delete("1.0", cutoff)
        if follow:
            self.text.see("end")
        else:
            self.text.yview(f"{max(1, int(top.split('.')[0]) - excess)}.0")
        self.text.configure(state="disabled")
        if self._expanded_text is not None and self._expanded_text.winfo_exists() and getattr(self, "_view_current", True):
            if self._expanded_follow and self._expanded_text.yview()[1] >= 0.995:
                self._refresh_expanded(follow=True)
            else:
                self._expanded_follow = False
                self._update_page_controls()

    def _refresh_expanded(self, *, follow=False):
        text = self._expanded_text
        source = self._expanded_source
        last = max(0, (source.length - 1) // self.EXPANDED_PAGE_SIZE) * self.EXPANDED_PAGE_SIZE
        self._expanded_start = last if follow else min(self._expanded_start, last)
        self._expanded_follow = follow
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", source.slice(self._expanded_start, self._expanded_start + self.EXPANDED_PAGE_SIZE))
        if follow:
            text.see("end")
        else:
            text.see("1.0")
        text.configure(state="disabled")
        self._update_page_controls()

    def _update_page_controls(self):
        pages = max(1, (self._expanded_source.length + self.EXPANDED_PAGE_SIZE - 1) // self.EXPANDED_PAGE_SIZE)
        page = self._expanded_start // self.EXPANDED_PAGE_SIZE + 1
        self._page_status.set(f"第 {page} / {pages} 页")
        self._previous_page.configure(state="normal" if page > 1 else "disabled")
        self._next_page.configure(state="normal" if page < pages else "disabled")

    def _move_expanded_page(self, direction):
        self._expanded_start = max(0, self._expanded_start + direction * self.EXPANDED_PAGE_SIZE)
        self._refresh_expanded()

    def contents(self):
        return "".join(self._log.chunks)

    def finish(self, state: str, summary="", advice=""):
        if not self._initialized:
            self.begin()
        self.finished = True
        self.failure = redact(f"原因：{summary}\n建议：{advice}") if summary else ""
        self.summary.set(self.failure)
        self.status.set(f"{state} · 耗时 {time.monotonic() - self.started:.1f} 秒")
        self.append(self.status.get() + ("\n" + self.failure if self.failure else ""))

    def copy_error(self):
        text = redact(f"{self.module}\n{self.status.get()}\n{self.failure}\n\n完整日志：\n{self.contents()}")
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.notice.set("已复制错误详情，可直接粘贴发送" if self.failure else "已复制本次日志，可直接粘贴发送")
        except tk.TclError as exc:
            self.notice.set(f"复制失败，请重试：{exc}")

    def expand(self):
        if self._expanded is not None and self._expanded.winfo_exists():
            self._expanded.lift()
            return
        window = tk.Toplevel(self)
        self._expanded = window
        window.title(f"{self.module} · 完整运行日志")
        window.geometry("960x640")
        bar = ttk.Frame(window, padding=8)
        bar.pack(fill="x")
        records = [("本次运行", None)]
        try:
            for path in sorted(self.directory.glob("*.log"), reverse=True):
                if path == self.path:
                    continue
                header = path.open(encoding="utf-8")
                with header:
                    first = header.readline()
                if f"模块：{self.module}  " in first:
                    records.append((path.stem, path))
                if len(records) >= 21:
                    break
        except OSError:
            pass
        selected = ttk.Combobox(bar, state="readonly", values=[label for label, _ in records], width=30)
        selected.current(0)
        selected.pack(side="left")
        self._view_current = True
        self._expanded_source = self._log
        def choose(_event=None):
            path = records[selected.current()][1]
            self._view_current = path is None
            try:
                source = self._log
                if path is not None:
                    source = _LogBuffer()
                    with path.open(encoding="utf-8") as handle:
                        while chunk := handle.read(self.EXPANDED_PAGE_SIZE):
                            source.append(chunk)
                self._expanded_source = source
                self._refresh_expanded(follow=True)
            except OSError as exc:
                self.notice.set(f"历史日志读取失败：{exc}")
        selected.bind("<<ComboboxSelected>>", choose)
        def copy():
            if self._view_current:
                self.copy_error()
            else:
                self.clipboard_clear()
                self.clipboard_append(redact("".join(self._expanded_source.chunks)))
                self.notice.set("已复制所选运行日志")
        ttk.Button(bar, text="复制错误", command=copy).pack(side="right")
        search = ttk.Entry(bar, width=18)
        search.pack(side="left", padx=8)
        def find(_event=None):
            query = search.get()
            text.tag_remove("match", "1.0", "end")
            if not query:
                return
            start = self._expanded_start + len(text.get("1.0", "insert")) + 1
            source = self._expanded_source
            pos = source.find(query, start, ignore_case=True)
            if pos < 0:
                pos = source.find(query, ignore_case=True)
            if pos >= 0:
                self._expanded_start = pos
                self._refresh_expanded()
                local = pos - self._expanded_start
                index = f"1.0+{local}c"
                text.tag_add("match", index, f"{index}+{len(query)}c")
                text.mark_set("insert", index)
                text.see(index)
        ttk.Button(bar, text="查找", command=find).pack(side="left")
        search.bind("<Return>", find)
        pages = ttk.Frame(window, padding=(8, 0, 8, 8))
        pages.pack(fill="x")
        self._previous_page = ttk.Button(pages, text="上一页", command=lambda: self._move_expanded_page(-1))
        self._previous_page.pack(side="left")
        self._next_page = ttk.Button(pages, text="下一页", command=lambda: self._move_expanded_page(1))
        self._next_page.pack(side="left", padx=6)
        ttk.Button(pages, text="最新日志", command=lambda: self._refresh_expanded(follow=True)).pack(side="left")
        self._page_status = tk.StringVar(self)
        ttk.Label(pages, textvariable=self._page_status).pack(side="left", padx=12)
        from tkinter.scrolledtext import ScrolledText
        # Each page is exact text, including long lines. Horizontal scrolling
        # avoids expensive word wrapping without truncating the full log.
        text = ScrolledText(window, wrap="none", font="TkFixedFont")
        self._expanded_text = text
        horizontal = ttk.Scrollbar(window, orient="horizontal", command=text.xview)
        horizontal.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        text.configure(xscrollcommand=horizontal.set)
        text.pack(fill="both", expand=True, padx=12)
        text.tag_configure("match", background="#fef08a")
        self._refresh_expanded(follow=True)
        def select_all(_event):
            text.tag_add("sel", "1.0", "end-1c")
            return "break"
        text.bind("<Command-a>", select_all)
