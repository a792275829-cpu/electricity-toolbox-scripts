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
    threshold: float = 100.0,
    html_name: str | None = None,
) -> BlockageMapResult:
    topology_path = Path(topology_path)
    raw_price_path = Path(raw_price_path)
    output_dir = Path(output_dir)
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
) -> BlockageMapResult:
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
    )
    build_map_html(
        topology_path=topology_path,
        original_price_path=raw_price_path,
        processed_price_path=processed_price_path,
        analysis_path=analysis_result.output_path,
        output_path=html_path,
    )

    return BlockageMapResult(
        html_path=html_path,
        kept_rows=dedupe_result.kept_rows,
        removed_rows=dedupe_result.removed_rows,
        flagged_edges=analysis_result.flagged_edges,
        flagged_intervals=analysis_result.flagged_intervals,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="由原始节点电价Excel一键生成广东500kV阻塞断面地图。")
    parser.add_argument("--topology", required=True, type=Path, help="拓扑 Markdown 文件路径")
    parser.add_argument("--raw-prices", required=True, type=Path, help="原始节点电价 Excel 文件路径")
    parser.add_argument("--output-dir", required=True, type=Path, help="输出目录")
    parser.add_argument("--threshold", default=100.0, type=float, help="价差阈值，默认 100 元/MWh")
    parser.add_argument("--html-name", help="输出 HTML 文件名；默认使用原数据文件名加_500kV分析")
    args = parser.parse_args()

    result = generate_blockage_map(
        topology_path=args.topology,
        raw_price_path=args.raw_prices,
        output_dir=args.output_dir,
        threshold=args.threshold,
        html_name=args.html_name,
    )

    print(f"阻塞地图HTML: {result.html_path}")
    print(f"保留500kV行数: {result.kept_rows}")
    print(f"删除行数: {result.removed_rows}")
    print(f"触发断面边数: {result.flagged_edges}")
    print(f"触发时段数: {result.flagged_intervals}")


if __name__ == "__main__":
    main()
