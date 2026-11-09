from __future__ import annotations

import argparse
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from src.generate_500kv_blockage_map import generate_blockage_map
from toolbox_shared.trade_batch import plan_batch_outputs


DEFAULT_TOPOLOGY = MODULE_ROOT / "500kV节点拓扑.md"


def result_html_name(source: Path) -> str:
    return f"{Path(source).stem}_500kV分析.html"


def run_many(
    raw_price_paths: list[Path],
    output_root: Path,
    *,
    topology_path: Path = DEFAULT_TOPOLOGY,
    threshold: float = 80.0,
    fetch_maintenance: bool = False,
    maintenance_json: Path | None = None,
) -> list[Path]:
    topology_path = Path(topology_path).resolve()
    output_root = Path(output_root).resolve()
    if not topology_path.is_file():
        raise FileNotFoundError(f"拓扑文件不存在：{topology_path}")
    if threshold < 0:
        raise ValueError("价差阈值不能小于 0")
    if not raw_price_paths:
        raise ValueError("至少需要一个节点电价 Excel")

    planned = plan_batch_outputs(raw_price_paths, output_root, suffix="_500kV分析.html")
    for raw_price_path, _ in planned:
        raw_price_path = Path(raw_price_path).resolve()
        if not raw_price_path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{raw_price_path}")
        if raw_price_path.suffix.lower() != ".xlsx":
            raise ValueError(f"仅支持 .xlsx 文件：{raw_price_path}")

    snapshots = {}
    if fetch_maintenance and maintenance_json:
        raise ValueError('在线抓取检修与本地检修快照不能同时选择。')
    if fetch_maintenance or maintenance_json:
        from toolbox_shared.section_maintenance import workbook_trade_date, collect_snapshots, read_snapshot
        days = {p.resolve(): workbook_trade_date(p) for p, _ in planned}
        if maintenance_json:
            snapshots = {p: read_snapshot(maintenance_json, day) for p, day in days.items()}
        else:
            collected = collect_snapshots(list(days.values()))
            snapshots = {p: collected[day] for p, day in days.items()}
    output_root.mkdir(parents=True, exist_ok=True)
    html_paths: list[Path] = []
    for index, (raw_price_path, output_path) in enumerate(planned, start=1):
        raw_price_path = raw_price_path.resolve()
        print(f"[{index}/{len(raw_price_paths)}] 开始处理：{raw_price_path.name}", flush=True)
        result = generate_blockage_map(
            topology_path=topology_path,
            raw_price_path=raw_price_path,
            output_dir=output_root,
            threshold=threshold,
            html_name=output_path.name,
            maintenance_data=snapshots.get(raw_price_path),
        )
        html_paths.append(result.html_path)
        print(
            f"完成：保留 {result.kept_rows} 行，删除 {result.removed_rows} 行，"
            f"识别 {result.flagged_edges} 条断面、{result.flagged_intervals} 个触发时点",
            flush=True,
        )
        print(f"分析网页：{result.html_path}", flush=True)

    print(f"全部完成：共生成 {len(html_paths)} 个分析网页。", flush=True)
    return html_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="批量生成广东500kV节点断面分析网页。")
    parser.add_argument("raw_prices", nargs="+", type=Path, help="原始节点电价 Excel，可传多个")
    parser.add_argument("--output-root", required=True, type=Path, help="结果根目录")
    parser.add_argument("--topology", default=DEFAULT_TOPOLOGY, type=Path, help="拓扑 Markdown 或编辑器 JSON")
    parser.add_argument("--threshold", default=80.0, type=float, help="价差阈值，默认80元/MWh")
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument('--fetch-maintenance', action='store_true', help='按 Excel 标题 D / D−1 在线读取检修')
    maintenance.add_argument('--maintenance-json', type=Path, help='读取同日期检修接口快照（离线核对）')
    args = parser.parse_args()
    run_many(
        args.raw_prices,
        args.output_root,
        topology_path=args.topology,
        threshold=args.threshold,
        fetch_maintenance=args.fetch_maintenance,
        maintenance_json=args.maintenance_json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
