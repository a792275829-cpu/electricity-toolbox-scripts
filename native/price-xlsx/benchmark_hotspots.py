"""Compare shared XLSX hot paths against an explicit source and binary snapshot."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from zipfile import ZipFile
from pandas.testing import assert_frame_equal

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'电力工具箱'),str(ROOT/'电力工具脚本/电力交易分析工具')]
from toolbox.runtime import load_module
import toolbox_native_excel as excel
import toolbox_native_write as writer


def snapshot(path):
    with ZipFile(path) as archive:
        result={}
        for name in ['xl/worksheets/sheet1.xml','xl/styles.xml','xl/workbook.xml']:
            digest=hashlib.sha256()
            with archive.open(name) as source:
                for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
            result[name]=digest.hexdigest()
        return result


def run(args):
    os.environ['TOOLBOX_XLSX_BACKEND']='rust'
    old_excel=load_module('old_excel_hotspots',args.baseline_dir/'toolbox_native_excel.py')
    old_writer=load_module('old_writer_hotspots',args.baseline_dir/'toolbox_native_write.py')
    old_binary=args.baseline_dir/'bin/toolbox-price-xlsx'
    old_excel.native_binary=lambda:old_binary
    old_writer.native_binary=lambda:old_binary
    references={};timings={};counts={}
    def equal(key,value):
        if key not in references:references[key]=value
        elif references[key]!=value:raise AssertionError(key+' differs')
    def same_frame(value):
        if 'frame' not in references:references['frame']=value
        else:assert_frame_equal(references['frame'],value,check_exact=True)
    def measure(mode,stage,fn):
        start=time.perf_counter();value=fn();elapsed=time.perf_counter()-start
        timings.setdefault(stage,{}).setdefault(mode,[]).append(elapsed)
        print(f'{mode} {stage}: {elapsed:.4f}s',flush=True)
        return value
    with tempfile.TemporaryDirectory(prefix='xlsx-hotspots-') as folder:
        folder=Path(folder)
        for repeat in range(args.repeats):
            for mode in (('before','after') if repeat%2==0 else ('after','before')):
                reader,output_writer=(old_excel,old_writer) if mode=='before' else (excel,writer)
                frame=measure(mode,'forecast_read',lambda:reader.read_model_frame(args.forecast))
                same_frame(frame);counts['forecast_shape']=list(frame.shape)
                layout=measure(mode,'layout',lambda:output_writer.read_table_layout(args.forecast))
                equal('layout',layout)
                path=folder/(mode+'.xlsx')
                def write():
                    current_layout=output_writer.read_table_layout(args.forecast)
                    assert output_writer.save_frame(frame,path,'整合数据',current_layout)
                measure(mode,'forecast_write_with_layout',write)
                if repeat==0:
                    equal('output_xml',snapshot(path))
                    same_frame(excel.read_model_frame(path))
    result={'repeats':args.repeats,'baseline':'previous optimization round, already using Rust read_frame/write_frame','counts':counts,'stages':{},'parity':'exact dataframes including dtypes/NaNs; table layout styles/widths; full worksheet/style/workbook XML SHA256; exported data reopened and compared','notes':'Alternating order; startup/IPC/spool/compression included; imports and parity checks excluded; real inputs never overwritten.'}
    for stage,modes in timings.items():
        result['stages'][stage]={mode:{'seconds':values,'median':statistics.median(values)} for mode,values in modes.items()}
        result['stages'][stage]['speedup']=statistics.median(modes['before'])/statistics.median(modes['after'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-dir',type=Path,required=True)
    parser.add_argument('--forecast',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=3)
    run(parser.parse_args())
