"""Manual login routing uses the same state directories as the business tools."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

STATE_DIRS = {
    "online-energy": "上网电量抓取", "wind-forecast": "风力预测截图", "market-table": "市场表更新",
    "transaction-info": "交易系统信息更新", "power-benefit": "功率调整收益统计表更新", "spot-price": "现货价格更新",
    "daily-pdf-parser": "广东运行日报PDF解析", "report": "每日生产经营情况汇报自动生成工具/scripts",
    "market-report": "南方分公司电力市场日报/.state", "guangdong-price": "广东电价预测",
}


def login_tool(workspace: Path, tool_id: str):
    from types import SimpleNamespace
    from .auth_loader import load_login_module, standalone_login
    from .credentials import read_login_config
    state = workspace / STATE_DIRS[tool_id]
    config = read_login_config(state / "config.json", workspace / "上网电量抓取/config.json", workspace=workspace)
    auth = load_login_module(workspace, module_name="manual_login_" + tool_id.replace("-", "_"), state_dir=state)
    standalone_login(auth, SimpleNamespace(username=str(config.get("username") or ""), password=str(config.get("password") or "")),
                     app_url=auth.LOGIN_URL, log=print, label=tool_id)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from toolbox_shared.login_tools import login_tool, STATE_DIRS
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--tool", choices=STATE_DIRS, required=True)
    args = parser.parse_args()
    login_tool(args.workspace, args.tool)
