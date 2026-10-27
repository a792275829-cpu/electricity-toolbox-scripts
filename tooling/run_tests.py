#!/usr/bin/env python3
"""One module per process: Tk, dynamic imports and cwd never leak across tests."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "toolbox": "电力工具箱/tests",
    "desktop": "desktop/tests/python",
    "section": "500kV断面分析/tests",
    "trade": "电力工具脚本/电力交易分析工具/tests",
    "morning": "每日生产经营情况汇报自动生成工具/scripts",
    "wind": "风力预测截图/tests",
    "spot": "现货价格更新/tests",
    "group": "集团每日上传/tests",
    "maintenance": "tooling/tests",
}


def save_report(report, results):
    if report is None:
        return
    report = Path(report)
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=report.parent,
                                         prefix=f'.{report.name}-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(results, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, report)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(suites, *, timeout=180, native=False, report=None, pattern="test_*.py"):
    results = []
    save_report(report, results)
    for suite in suites:
        folder = ROOT / SUITES[suite]
        modules = sorted(folder.glob(pattern))
        if not modules:
            raise ValueError(f"No tests found: {folder / pattern}")
        for module in modules:
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            env["PYTHONPATH"] = os.pathsep.join([
                str(ROOT), str(ROOT / "电力工具箱"), str(folder), env.get("PYTHONPATH", "")])
            if native:
                env.update(TOOLBOX_XLSX_BACKEND="rust", TOOLBOX_COMPUTE_BACKEND="rust",
                           TOOLBOX_500KV_BACKEND="rust")
            data_directory = tempfile.TemporaryDirectory(prefix="toolbox-test-data-")
            env.update(TOOLBOX_DATA_ROOT=data_directory.name, TOOLBOX_CODE_ROOT=str(ROOT))
            started = time.monotonic()
            process = subprocess.Popen(
                    [sys.executable, "-m", "unittest", "discover", "-s", str(folder),
                     "-p", module.name, "-v"], cwd=folder.parent, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                output = stdout + stderr
                code = process.returncode
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
                output = stdout + stderr
                output += f"\nModule timed out after {timeout}s"
                code = 124
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise
            finally:
                data_directory.cleanup()
            counts = re.findall(r"^Ran (\d+) tests? in", output, re.MULTILINE)
            count = int(counts[-1]) if counts else 0
            # unittest returning 0 with no discovered cases must never green-light CI.
            if code == 0 and count == 0:
                code = 1
                output += "\nNo test cases were executed"
            result = dict(suite=suite, module=module.name, returncode=code,
                          tests=count,
                          seconds=round(time.monotonic() - started, 3), output=output)
            results.append(result)
            save_report(report, results)
            print(f"{'PASS' if code == 0 else 'FAIL'} {suite}/{module.name} "
                  f"({result['tests']} tests, {result['seconds']}s)", flush=True)
            if code:
                print(output, flush=True)
    print(f"{sum(r['tests'] for r in results)} tests in {len(results)} modules; "
          f"{sum(r['returncode'] != 0 for r in results)} failed modules", flush=True)
    return int(any(r["returncode"] for r in results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", choices=SUITES)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return run(args.suite or list(SUITES), timeout=args.timeout, native=args.native,
               report=args.report, pattern=args.pattern)


if __name__ == "__main__":
    raise SystemExit(main())
