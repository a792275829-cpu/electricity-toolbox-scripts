"""Fresh-process time and RSS comparison for sparse benefit inspection."""
import argparse
from dataclasses import asdict
from datetime import date
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def worker(args):
    sys.path.insert(0,str(ROOT))
    mode=args.worker
    source=(args.baseline_dir if mode=='before' else ROOT)/'功率调整收益统计表更新/update_power_benefit.py'
    spec=importlib.util.spec_from_file_location('benefit_rss_worker',source)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    os.environ['TOOLBOX_BENEFIT_INSPECT_BACKEND']='stream' if mode=='after' else 'full'
    os.environ['TOOLBOX_XLSX_BACKEND']='rust'
    start=time.perf_counter();plan=module.inspect_workbook(args.inputs[0],end_date=date.fromisoformat(args.end_date));elapsed=time.perf_counter()-start
    scale=1024*1024 if sys.platform=='darwin' else 1024
    # Separate high-water marks; their sum is not a simultaneous process-tree RSS.
    parent_peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/scale
    child_peak=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/scale
    value=json.dumps(asdict(plan),default=str,sort_keys=True,ensure_ascii=False).encode()
    print(json.dumps(dict(seconds=elapsed,parent_peak_mib=parent_peak,child_peak_mib=child_peak,
                         cells=len(plan.cells),plan_sha256=hashlib.sha256(value).hexdigest())))


def run(args):
    records={};before_hash={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in args.inputs}
    for path in args.inputs:
        values={mode:[] for mode in ['before','after']}
        reference=None
        for repeat in range(args.repeats):
            for mode in (('before','after') if repeat%2==0 else ('after','before')):
                command=[sys.executable,str(Path(__file__).resolve()),'--worker',mode,
                         '--baseline-dir',str(args.baseline_dir),'--end-date',args.end_date,'--inputs',str(path)]
                result=subprocess.run(command,check=True,text=True,capture_output=True,cwd=ROOT)
                item=json.loads(result.stdout);values[mode].append(item)
                if reference is None:reference=item['plan_sha256']
                elif item['plan_sha256']!=reference:raise AssertionError(path.name+' inspection plan differs')
                print(path.name,mode,json.dumps(item),flush=True)
        record={'runs':values}
        for mode,runs in values.items():
            record[mode]={key:statistics.median(item[key] for item in runs) for key in ['seconds','parent_peak_mib','child_peak_mib']}
        record['speedup']=record['before']['seconds']/record['after']['seconds']
        record['parent_peak_reduction']=1-record['after']['parent_peak_mib']/record['before']['parent_peak_mib']
        record['cells']=values['after'][0]['cells'];record['plan_sha256']=reference
        records[path.name]=record
    assert before_hash=={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in args.inputs}
    report={'repeats':args.repeats,'end_date':args.end_date,'cases':records,
            'rss_scope':'Fresh worker lifetime high-water mark, including imports and inspection but excluding result serialization; Python parent and native child separately. Not summed and not whole-application RSS.',
            'timing_scope':'Inspection includes source parsing and native startup/IPC in baseline; excludes worker startup/imports and result serialization; alternating fresh processes.',
            'original_inputs_unchanged':True,'parity':'Complete ordered target plans including date/hour/field/format/style ID/fingerprint/path match in all runs.'}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',choices=['before','after'])
    parser.add_argument('--baseline-dir',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,nargs='+',required=True)
    parser.add_argument('--end-date',default='2026-10-20')
    parser.add_argument('--repeats',type=int,default=5)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.worker:worker(args)
    else:run(args)
