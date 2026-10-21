"""Compare this round against a source snapshot, with real local inputs only."""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

import numpy as np
from pandas.testing import assert_frame_equal, assert_series_equal

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/"电力工具箱"),str(ROOT/"广东电价预测"),str(ROOT/"广东运行日报PDF解析"),str(ROOT/"500kV断面分析")]
from toolbox.runtime import load_module
from src import deduplicate_500kv_prices as dedup
from src import generate_500kv_blockage_map as pipeline
from gd_price_forecast import data,features
from gd_pdf_tool import parser as pdf
from benchmark_consumers import workbook_snapshot


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result;spec.loader.exec_module(result)
    return result


def run(args):
    old_d=module("old_dedup_remaining",args.baseline_dir/"500kV断面分析/src/deduplicate_500kv_prices.py")
    old_data=module("gd_price_forecast.old_data_remaining",args.baseline_dir/"广东电价预测/gd_price_forecast/data.py")
    old_features=module("gd_price_forecast.old_features_remaining",args.baseline_dir/"广东电价预测/gd_price_forecast/features.py")
    old_pdf=module("gd_pdf_tool.old_parser_remaining",args.baseline_dir/"广东运行日报PDF解析/gd_pdf_tool/parser.py")
    old_summary=load_module("old_summary_remaining",args.baseline_dir/"电力工具脚本/电量汇总工具/summarize_511_excel.py")
    summary=load_module("new_summary_remaining",ROOT/"电力工具脚本/电量汇总工具/summarize_511_excel.py")
    topology=ROOT/"500kV断面分析/500kV节点拓扑.md"
    os.environ.update(TOOLBOX_XLSX_BACKEND="rust",TOOLBOX_500KV_BACKEND="rust")
    chart=pdf.analyze_pdf(args.pdf)
    times={mode:{} for mode in ("before","after")};references={};counts={}
    def measure(mode,stage,function,verify):
        t=time.perf_counter();result=function();elapsed=time.perf_counter()-t
        times[mode].setdefault(stage,[]).append(elapsed)
        verify(result)
        print(f"{mode} {stage}: {elapsed:.4f}s",flush=True)
        return result
    def equal(label,value):
        if label not in references:references[label]=value
        elif value!=references[label]:raise AssertionError(f"{label} differs")
    def frame(value):
        if "frame" not in references:references["frame"]=value
        else:assert_frame_equal(references["frame"],value,check_exact=True)
    def feature_result(value):
        if "features" not in references:references["features"]=value
        else:
            assert_frame_equal(references["features"][0],value[0],check_exact=True)
            assert_series_equal(references["features"][1],value[1],check_exact=True)
            assert_frame_equal(references["features"][2],value[2],check_exact=True)
    def chart_result(value):
        equal("pixels",value.pixel_counts)
        equal("quote_rows",[asdict(row) for row in value.rows])
    def pdf_result(value):
        equal("pdf_rows",[asdict(row) for row in value.rows])
        equal("pdf_date",value.report_date)
        equal("pdf_baseline",value.baseline_capacity)
        equal("pdf_preview",value.chart_preview_png)
    with tempfile.TemporaryDirectory(prefix="remaining-native-bench-") as temporary:
        output=Path(temporary)
        for repeat in range(args.repeats):
            for mode in (("before","after") if repeat%2==0 else ("after","before")):
                before=mode=="before"
                os.environ["TOOLBOX_COMPUTE_BACKEND"]="python" if before else "rust"
                os.environ["TOOLBOX_FEATURE_BACKEND"]="python" if before else "auto"
                d=old_d if before else dedup
                def clean():
                    result=d.process_workbook(args.prices,output/"clean.xlsx",topology)
                    return result
                def clean_verify(result):
                    equal("clean_counts",(result.kept_rows,result.removed_rows,result.processed_sheets))
                    equal("clean_cells",workbook_snapshot(result.output_path))
                measure(mode,"500kv_clean",clean,clean_verify)
                def map_run():
                    with patch.object(pipeline,"process_workbook",d.process_workbook):
                        # Stable intermediate paths allow exact HTML metadata
                        # comparison as well as business-value comparison.
                        return pipeline._run_pipeline(topology,args.prices,output,80.0,
                                                      output/"map.html",output/"processed.xlsx")
                def map_verify(result):
                    equal("map_html",result.html_path.read_bytes())
                    equal("map_counts",(result.flagged_edges,result.flagged_intervals))
                measure(mode,"500kv_full",map_run,map_verify)
                f=measure(mode,"forecast_read",lambda:(old_data if before else data).load_model_data(args.forecast),frame)
                counts["forecast_rows"]=len(f)
                feature=measure(mode,"features",lambda:(old_features if before else features).build_features(
                    f,exogenous_mode="lagged",supply=True,holidays=True),feature_result)
                counts["feature_columns"]=len(feature[0].columns)
                def summary_run():
                    return (old_summary if before else summary).run_summary(args.summary_dir,output/"summary.xlsx")
                measure(mode,"summary_export",summary_run,lambda path:equal("summary_cells",workbook_snapshot(path)))
                measure(mode,"pdf_chart",lambda:(old_pdf if before else pdf).analyze_chart_image(
                    chart.chart_image,chart.baseline_capacity,chart.declaration_info),chart_result)
                parsed=measure(mode,"pdf_full",lambda:(old_pdf if before else pdf).parse_pdf(args.pdf),pdf_result)
                counts["pdf_bands"]=len(parsed.rows)
    stages={stage:{mode:dict(seconds=times[mode][stage],median=statistics.median(times[mode][stage]))
                   for mode in times} for stage in times["before"]}
    for stage in stages.values():stage["speedup"]=stage["before"]["median"]/stage["after"]["median"]
    return dict(repeats=args.repeats,baseline="source snapshot after the first two Rust phases",
                inputs={"prices":args.prices.name,"forecast":args.forecast.name,"summary":args.summary_dir.name,"pdf":args.pdf.name},
                counts=counts,stages=stages,
                parity="exact cell values/types/styles, HTML bytes, forecast frame/features including dtypes and NaNs, PDF pixels/quote rows/date/preview",
                notes="Alternating order; imports/compilation excluded; child process startup and IPC included. Local workflows only; no website or upload.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline-dir",type=Path,required=True)
    parser.add_argument("--prices",type=Path,default=ROOT/"500kV断面分析/local_data/inputs/日前节点电价查询(2026-07-20).xlsx")
    parser.add_argument("--forecast",type=Path,default=ROOT/"广东电价预测/输出/广东电价数据总表.xlsx")
    parser.add_argument("--summary-dir",type=Path,default=ROOT/".toolbox/migration-sample-source/summary-upload/10-8")
    parser.add_argument("--pdf",type=Path,default=ROOT/".toolbox/migration-sample-source/daily-pdf-parser/广东电力现货市场2026年10月运行日报（10.11）.pdf")
    parser.add_argument("--repeats",type=int,default=3)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.repeats<1:parser.error("--repeats must be positive")
    report=run(args)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({key:stage["speedup"] for key,stage in report["stages"].items()},indent=2))
