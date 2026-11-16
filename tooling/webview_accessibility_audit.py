"""Exercise the offline Tauri pilot through macOS AX, then read its real XLSX files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
CONTROL_IDS = {"online-start-date", "online-end-date", "online-export", "online-output", "online-cancel", "online-status"}


def audit(pid):
    LOGS.mkdir(exist_ok=True)
    # Use one compiled AX client identity for the full run, avoiding repeated
    # Swift interpreter startup and transient WebKit accessibility discovery.
    binary = ROOT / ".toolbox/accessibility/macos_ax_probe"
    binary.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["swiftc", str(ROOT / "tooling/macos_ax_probe.swift"), "-o", str(binary)], check=True)

    def capture(label, *operation):
        result = subprocess.run([str(binary), "--pid", str(pid), *operation],
                                capture_output=True, text=True, timeout=15)
        (LOGS / f"webview-ax-{label}.json").write_text(result.stdout)
        if not result.stdout:
            raise RuntimeError(f"AX 调用失败，退出码 {result.returncode}：{result.stderr}")
        return json.loads(result.stdout)

    def nodes(snapshot):
        return snapshot.get("nodes", [])

    def by_id(snapshot, identifier):
        return next((node for node in nodes(snapshot) if node["identifier"] == identifier), {})

    def visible_text(snapshot):
        return "\n".join(str(node.get("value", "")) for node in nodes(snapshot))

    def wait_for(label, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while True:
            snapshot = capture(label)
            if predicate(snapshot):
                return snapshot
            if time.monotonic() >= deadline:
                raise RuntimeError(f"等待 {label} 超时；已保留原始 AX 树。")
            time.sleep(0.15)

    initial = wait_for("initial", lambda s: all(by_id(s, key) for key in CONTROL_IDS))
    if "样例模式：" not in visible_text(initial):
        raise RuntimeError("只允许审计离线样例窗口。")
    directory_text = next((node["value"] for node in nodes(initial) if str(node.get("value", "")).startswith("输出目录：")), "")
    output = Path(directory_text.removeprefix("输出目录：").strip()).resolve()
    expected_root = Path.home() / "Library/Application Support/com.electricity.toolbox/development/data/webview-pilot"
    if not output.is_relative_to(expected_root.resolve()) or not output.is_dir():
        raise RuntimeError("输出目录不是隔离的 WebView 样例目录。")
    existing = set(output.rglob("*.xlsx"))
    checks = {
        "six_named_controls": all(by_id(initial, key).get("title") or by_id(initial, key).get("description") for key in CONTROL_IDS),
        "stable_identifiers": all(by_id(initial, key).get("identifierStable") for key in CONTROL_IDS),
        "enabled_states": by_id(initial, "online-export").get("enabled") == "1" and by_id(initial, "online-cancel").get("enabled") == "0",
        "dates_settable": all(by_id(initial, key).get("valueSettable") for key in ("online-start-date", "online-end-date")),
    }

    # A reversed range must fail before creating any task output.
    capture("invalid-start", "--set", "导出开始日期", "2026-11-12")
    capture("invalid-end", "--set", "导出结束日期", "2026-11-10")
    capture("invalid-press", "--press", "开始导出")
    invalid = wait_for("invalid-result", lambda s: "结束日期不能早于开始日期" in visible_text(s))
    negative = {"reversed_range_rejected": set(output.rglob("*.xlsx")) == existing}

    start = capture("set-start", "--set", "导出开始日期", "2026-11-10")
    end = capture("set-end", "--set", "导出结束日期", "2026-11-11")
    checks["date_write_readback"] = start.get("result") == 0 and start.get("valueAfter") == "2026-11-10" and end.get("result") == 0 and end.get("valueAfter") == "2026-11-11"

    # Cancellation must reach the task engine, not just change button text.
    capture("cancel-start", "--press", "开始导出")
    running = wait_for("running", lambda s: by_id(s, "online-cancel").get("enabled") == "1")
    checks["enabled_states"] &= by_id(running, "online-export").get("enabled") == "0"
    capture("cancel-press", "--press", "取消任务")
    wait_for("cancelled", lambda s: "已取消" in visible_text(s) and by_id(s, "online-export").get("enabled") == "1")
    negative["cancelled_without_output"] = set(output.rglob("*.xlsx")) == existing

    capture("export-press", "--press", "开始导出")
    finished = wait_for("completed", lambda s: "已生成 2 个样例工作簿，回读验证通过。" in visible_text(s))
    paths = sorted(set(output.rglob("*.xlsx")) - existing)
    import openpyxl
    workbooks = []
    for path in paths:
        book = openpyxl.load_workbook(path)
        try:
            sheet = book["汇总"]
            workbooks.append(dict(path=str(path), sheet=sheet.title, A1=sheet["A1"].value,
                                  A2=sheet["A2"].value, A3=sheet["A3"].value,
                                  number_format=sheet["A3"].number_format))
        finally:
            book.close()
    checks["press_reaches_worker_and_xlsx"] = (
        [path.name for path in paths] == ["上网电量汇总_2026-11-10.xlsx", "上网电量汇总_2026-11-11.xlsx"]
        and [book["A3"] for book in workbooks] == [12.5, 13.5]
        and all(book["A1"] == "汕头 实际上网电量（万千瓦时）" and book["A2"] == "1号机" and book["number_format"] == "General" for book in workbooks)
    )
    checks["stable_identifiers"] &= all(by_id(finished, key).get("identifier") == by_id(initial, key).get("identifier") for key in CONTROL_IDS)
    summary = dict(pid=pid, checks=checks, negative_checks=negative, workbooks=workbooks,
                   compatible=all(checks.values()) and all(negative.values()),
                   scope="offline Tauri WKWebView pilot; native AX only; no website login or collection")
    (LOGS / "webview-accessibility-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["compatible"] else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()
    if args.pid is None:
        receipt = LOGS / "webview-pilot-process.json"
        if not receipt.exists():
            parser.error("请先运行 00_启动/Tauri开发版.command --webview-pilot。")
        args.pid = json.loads(receipt.read_text())["ui_pid"]
    raise SystemExit(audit(args.pid))
