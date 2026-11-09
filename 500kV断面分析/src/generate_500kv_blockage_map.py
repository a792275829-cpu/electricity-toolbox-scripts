from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.analyze_500kv_section_gaps import analyze_section_gaps
from src.build_500kv_map_html import build_map_html
from src.deduplicate_500kv_prices import process_workbook


def default_html_name(raw_price_path: Path) -> str:
    return f"{Path(raw_price_path).stem}_500kV分析.html"


@dataclass(frozen=True)
class BlockageMapResult:
    html_path: Path
    kept_rows: int
    removed_rows: int
    flagged_edges: int
    flagged_intervals: int


def generate_blockage_map(
    topology_path: Path,
    raw_price_path: Path,
    output_dir: Path,
    threshold: float = 80.0,
    html_name: str | None = None,
    maintenance_data: dict | None = None,
) -> BlockageMapResult:
    topology_path = Path(topology_path)
    raw_price_path = Path(raw_price_path)
    output_dir = Path(output_dir)
    if maintenance_data is not None:
        from toolbox_shared.section_maintenance import normalize_snapshot, workbook_trade_date
        maintenance_data = normalize_snapshot(maintenance_data, workbook_trade_date(raw_price_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = output_dir / (html_name or default_html_name(raw_price_path))

    with tempfile.TemporaryDirectory(prefix="500kv_blockage_") as tmp:
        work_dir = Path(tmp)
        temp_processed_path = work_dir / f"{raw_price_path.stem}_500kV去重{raw_price_path.suffix}"
        result = _run_pipeline(
            topology_path,
            raw_price_path,
            work_dir,
            threshold,
            html_path,
            temp_processed_path,
            maintenance_data,
        )
    for legacy_path in (
        output_dir / "500kv_section_price_gap_analysis.xlsx",
        output_dir / f"{raw_price_path.stem}_500kV去重{raw_price_path.suffix}",
        output_dir / "guangdong_500kv_section_map.html",
    ):
        if legacy_path != html_path:
            legacy_path.unlink(missing_ok=True)
    return result


def _run_pipeline(
    topology_path: Path,
    raw_price_path: Path,
    output_dir: Path,
    threshold: float,
    html_path: Path,
    processed_price_path: Path,
    maintenance_data: dict | None = None,
) -> BlockageMapResult:
    if topology_path.suffix.lower() == '.json':
        return _run_editor_pipeline(topology_path, raw_price_path, output_dir, threshold, html_path, maintenance_data)
    dedupe_result = process_workbook(
        raw_price_path,
        processed_price_path,
        topology_path=topology_path,
    )
    analysis_result = analyze_section_gaps(
        topology_path=topology_path,
        workbook_path=processed_price_path,
        output_dir=output_dir,
        threshold=threshold,
        export_workbook=False,
    )
    build_map_html(
        topology_path=topology_path,
        original_price_path=raw_price_path,
        processed_price_path=processed_price_path,
        analysis_path=analysis_result.output_path,
        output_path=html_path,
        analysis_data=analysis_result.data,
        threshold=threshold,
        maintenance_data=maintenance_data,
    )

    return BlockageMapResult(
        html_path=html_path,
        kept_rows=dedupe_result.kept_rows,
        removed_rows=dedupe_result.removed_rows,
        flagged_edges=analysis_result.flagged_edges,
        flagged_intervals=analysis_result.flagged_intervals,
    )



def _run_editor_pipeline(topology_path, raw_price_path, work_dir, threshold, html_path, maintenance_data=None):
    from toolbox_shared.section_topology import (
        load_catalog, read_catalog, synchronize, materialize_analysis,
    )
    # Re-read each day's workbook; saved curves never stand in for new input data.
    imported = read_catalog(raw_price_path)
    current = synchronize(load_catalog(topology_path), imported)
    from src.node_city_catalog import NODE_CITY
    from toolbox_shared.section_schema import normalize_price_node_name
    cities = {normalize_price_node_name(name): city for name, city in NODE_CITY.items()}
    for node in current['nodes']:
        if not node.get('city'):
            node['city'] = cities.get(node['station'], '')
    topology, processed, positions = materialize_analysis(current, work_dir / 'editor')
    analysis = analyze_section_gaps(topology, processed, work_dir, threshold, export_workbook=False)
    build_map_html(topology, raw_price_path, processed, analysis.output_path, html_path,
                   node_positions_path=positions, analysis_data=analysis.data, threshold=threshold,
                   maintenance_data=maintenance_data)
    return BlockageMapResult(html_path, analysis.matched_nodes,
                            max(0, imported['rows']-analysis.matched_nodes),
                            analysis.flagged_edges, analysis.flagged_intervals)

def main() -> None:
    parser = argparse.ArgumentParser(description="由原始节点电价Excel一键生成广东500kV阻塞断面地图。")
    parser.add_argument("--topology", required=True, type=Path, help="拓扑 Markdown 文件路径")
    parser.add_argument("--raw-prices", required=True, type=Path, help="原始节点电价 Excel 文件路径")
    parser.add_argument("--output-dir", required=True, type=Path, help="输出目录")
    parser.add_argument("--threshold", default=80.0, type=float, help="价差阈值，默认 80 元/MWh")
    parser.add_argument("--html-name", help="输出 HTML 文件名；默认使用原数据文件名加_500kV分析")
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument('--fetch-maintenance', action='store_true')
    maintenance.add_argument('--maintenance-json', type=Path)
    args = parser.parse_args()

    maintenance_data = None
    if args.fetch_maintenance or args.maintenance_json:
        from toolbox_shared.section_maintenance import workbook_trade_date, collect_snapshots, read_snapshot
        day = workbook_trade_date(args.raw_prices)
        maintenance_data = (read_snapshot(args.maintenance_json, day) if args.maintenance_json
                            else collect_snapshots([day])[day])

    result = generate_blockage_map(
        topology_path=args.topology,
        raw_price_path=args.raw_prices,
        output_dir=args.output_dir,
        threshold=args.threshold,
        html_name=args.html_name,
        maintenance_data=maintenance_data,
    )

    print(f"阻塞地图HTML: {result.html_path}")
    print(f"保留500kV行数: {result.kept_rows}")
    print(f"删除行数: {result.removed_rows}")
    print(f"触发断面边数: {result.flagged_edges}")
    print(f"触发时段数: {result.flagged_intervals}")


if __name__ == "__main__":
    main()
