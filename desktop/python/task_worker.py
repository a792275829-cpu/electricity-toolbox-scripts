"""Isolate optional business dependencies and own their subprocess lifecycle."""
from __future__ import annotations
import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "电力工具箱")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    from toolbox.runtime import ToolPaths
    from toolbox.workflows import execute
    from toolbox.service import RESULT_PREFIX, ERROR_PREFIX
    from toolbox.tasks.errors import error_detail
    request = json.loads(args.request.read_text(encoding="utf-8"))
    try:
        result = execute(ToolPaths(args.workspace), request["tool_id"], request["action"], request["values"])
    except Exception as exc:
        detail = error_detail(exc, tool_id=request["tool_id"], action=request["action"])
        print(ERROR_PREFIX + json.dumps(detail, ensure_ascii=False), flush=True)
        raise
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
