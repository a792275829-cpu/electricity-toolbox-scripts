from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from src.generate_500kv_blockage_map import generate_blockage_map


DEFAULT_TOPOLOGY = MODULE_ROOT / "500kV节点拓扑.md"


def result_folder_name(source: Path) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", Path(source).stem).strip()
    return cleaned or "500kV断面分析结果"


def result_html_name(source: Path) -> str:
    return f"{Path(source).stem}_500kV分析.html"


def run_many(
    raw_price_paths: list[Path],
    output_root: Path,
    *,
    topology_path: Path = DEFAULT_TOPOLOGY,
    threshold: float = 100.0,
) -> list[Path]:
    topology_path = Path(topology_path).resolve()
    output_root = Path(output_root).resolve()
    if not topology_path.is_file():
        raise FileNotFoundError(f"拓扑文件不存在：{topology_path}")
    if threshold < 0:
        raise ValueError("价差阈值不能小于 0")
    if not raw_price_paths:
        raise ValueError("至少需要一个节点电价 Excel")

    output_root.mkdir(parents=True, exist_ok=True)
    html_paths: list[Path] = []
    for index, raw_price_path in enumerate(raw_price_paths, start=1):
        raw_price_path = Path(raw_price_path).resolve()
        if not raw_price_path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{raw_price_path}")
        if raw_price_path.suffix.lower() != ".xlsx":
            raise ValueError(f"仅支持 .xlsx 文件：{raw_price_path}")

        output_dir = output_root / result_folder_name(raw_price_path)
        print(f"[{index}/{len(raw_price_paths)}] 开始处理：{raw_price_path.name}", flush=True)
        result = generate_blockage_map(
            topology_path=topology_path,
            raw_price_path=raw_price_path,
            output_dir=output_dir,
            threshold=threshold,
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
    parser.add_argument("--topology", default=DEFAULT_TOPOLOGY, type=Path, help="500kV拓扑 Markdown")
    parser.add_argument("--threshold", default=100.0, type=float, help="价差阈值，默认100元/MWh")
    args = parser.parse_args()
    run_many(
        args.raw_prices,
        args.output_root,
        topology_path=args.topology,
        threshold=args.threshold,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
