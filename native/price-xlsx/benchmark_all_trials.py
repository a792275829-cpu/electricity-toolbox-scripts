"""Fresh-process experiments: verify, transport, incremental merge, batch reuse."""
import argparse
from dataclasses import asdict
from datetime import date
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value)
    return value

def xml_digest(path):
    with ZipFile(path) as archive:
        return {name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}

def frame_digest(frame):
    import pandas as pd
    payload=(repr(list(frame.columns))+repr([str(x) for x in frame.dtypes])).encode()+pd.util.hash_pandas_object(frame,index=True).values.tobytes()
    return hashlib.sha256(payload).hexdigest()

def worker(args):
    sys.path[:0]=[str(ROOT),str(ROOT/'电力工具箱'),str(ROOT/'广东电价预测'),str(ROOT/'电力工具脚本/电力交易分析工具')]
    os.environ['TOOLBOX_XLSX_BACKEND']='rust'
    before=args.worker=='before'
    source=args.baseline_dir if before else ROOT
    detail={}
    with tempfile.TemporaryDirectory(prefix='toolbox-all-trial-') as folder:
        folder=Path(folder)
        if args.job=='verify':
            m=module('all_trial_benefit',source/'功率调整收益统计表更新/update_power_benefit.py')
            m.WORKSPACE=ROOT
            target=folder/args.input.name;shutil.copy2(args.input,target)
            os.environ['TOOLBOX_BENEFIT_VERIFY_BACKEND']='stream' if not before else 'full'
            original_verify=m.verify_workbook
            verify_time=[]
            def verify(*params):
                t=time.perf_counter();original_verify(*params);verify_time.append(time.perf_counter()-t)
            m.verify_workbook=verify
            start=time.perf_counter();plan=m.inspect_workbook(target,end_date=date(2026,10,20))
            detail['inspect_seconds']=time.perf_counter()-start
            series={day:{column:tuple(((-50 if column in ['L','N'] else index*100)+h/100) for h in range(24)) for index,column in enumerate(m.FIELDS,1)} for day in {c.run_date for c in plan.cells}}
            prepared=m.prepare_workbook(plan,series)
            elapsed=time.perf_counter()-start
            detail.update(verify_seconds=sum(verify_time),cells=len(prepared.expectations))
            result=lambda:xml_digest(prepared.temporary)
        elif args.job=='transport':
            from toolbox_shared import native_excel as excel
            if before:
                excel=module('all_trial_old_excel',args.baseline_dir/'toolbox_native_excel.py')
                excel.native_binary=lambda: args.baseline_dir/'bin/toolbox-price-xlsx'
            os.environ['TOOLBOX_FRAME_TRANSPORT']='pipe' if before else 'file'
            start=time.perf_counter();frame=excel.read_model_frame(args.input);elapsed=time.perf_counter()-start
            detail['shape']=list(frame.shape)
            result=lambda:frame_digest(frame)
        elif args.job in ['merge_overlap','merge_append']:
            import pandas as pd
            m=module('all_trial_forecast',ROOT/'广东电价预测/update_gdfire_data.py')
            frame=pd.read_pickle(args.frame_cache)
            inc=frame.tail(96).copy();future=inc.copy();future['日期']=pd.to_datetime(future['日期'])+pd.Timedelta(days=1)
            increment=pd.concat([inc,future],ignore_index=True) if args.job=='merge_overlap' else future
            os.environ['TOOLBOX_FORECAST_MERGE']='full' if before else 'incremental'
            selected=[];fn=m._merge_changed_rows
            def candidate(*values):
                value=fn(*values);selected.append(value is not None);return value
            m._merge_changed_rows=candidate
            start=time.perf_counter();frame=m.merge_data_frames(frame,increment);elapsed=time.perf_counter()-start
            detail.update(shape=list(frame.shape),incremental_used=bool(selected and selected[0]))
            result=lambda:frame_digest(frame)
        else:
            from toolbox_shared import native_excel as excel
            from toolbox.runtime import load_module
            m=load_module('all_trial_report',ROOT/'电力工具脚本/电力交易分析工具/generate_electricity_report.py')
            paths=args.batch_inputs
            if args.job=='batch_repeat':paths=[paths[0]]*len(paths)
            os.environ['TOOLBOX_EXCEL_SESSION']='oneshot' if before else 'session'
            launched=[];popen=excel.subprocess.Popen
            def spawn(*a,**kw):
                value=popen(*a,**kw);launched.append(value);return value
            excel.subprocess.Popen=spawn
            start=time.perf_counter();outputs=m.generate_batch(paths,folder);elapsed=time.perf_counter()-start
            detail.update(inputs=len(paths),native_launches=len(launched),children_closed=all(p.poll() is not None for p in launched))
            assert detail['children_closed']
            result=lambda:[hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs]
        scale=1024*1024 if sys.platform=='darwin' else 1024
        parent_peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/scale
        child_peak=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/scale
        value=json.dumps(result(),sort_keys=True,ensure_ascii=False).encode()
        print(json.dumps(dict(seconds=elapsed,parent_peak_mib=parent_peak,child_peak_mib=child_peak,
                             result_sha256=hashlib.sha256(value).hexdigest(),**detail)))

def run(args):
    records={}
    inputs=[args.forecast,args.benefit,*args.batch_inputs]
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    with tempfile.TemporaryDirectory(prefix='forecast-merge-input-') as folder:
        cache=Path(folder)/'frame.pkl'
        sys.path.insert(0,str(ROOT))
        from toolbox_shared.native_excel import read_model_frame
        read_model_frame(args.forecast).to_pickle(cache)
        for job in args.jobs:
            values={'before':[],'after':[]};reference=None
            for repeat in range(args.repeats):
                for mode in (('before','after') if repeat%2==0 else ('after','before')):
                    source=args.benefit if job=='verify' else args.forecast
                    command=[sys.executable,str(Path(__file__).resolve()),'--worker',mode,'--job',job,
                             '--baseline-dir',str(args.baseline_dir),'--input',str(source),'--frame-cache',str(cache)]
                    if job.startswith('batch'):command+=['--batch-inputs',*[str(p) for p in args.batch_inputs]]
                    run=subprocess.run(command,text=True,capture_output=True,cwd=ROOT,check=True)
                    item=json.loads(run.stdout);values[mode].append(item)
                    if reference is None:reference=item['result_sha256']
                    elif item['result_sha256']!=reference:raise AssertionError(job+' result differs')
                    print(job,mode,json.dumps(item),flush=True)
            record={'runs':values}
            for mode,runs in values.items():
                record[mode]={key:statistics.median(v[key] for v in runs) for key in ['seconds','parent_peak_mib','child_peak_mib']}
                if job=='verify':record[mode]['verify_seconds']=statistics.median(v['verify_seconds'] for v in runs)
            record['speedup']=record['before']['seconds']/record['after']['seconds']
            record['parent_peak_reduction']=1-record['after']['parent_peak_mib']/record['before']['parent_peak_mib']
            record['result_sha256']=reference;records[job]=record
            report={'repeats':args.repeats,'cases':records,'rss_scope':'Separate parent/child worker lifetime RSS high-water marks; includes imports/input preparation, excludes result digest. Not summed and not whole-application memory. Merge uses a self-generated trusted pickle to exclude XLSX parsing from its RSS baseline.',
                    'timing_scope':'Fresh worker for each run; alternating order; timed operation includes native startup/IPC; imports, source copy and parity digest excluded.',
                    'original_inputs_unchanged':hashes=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
            assert report['original_inputs_unchanged']
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--worker',choices=['before','after']);p.add_argument('--job');p.add_argument('--input',type=Path)
    p.add_argument('--baseline-dir',type=Path,required=True);p.add_argument('--forecast',type=Path);p.add_argument('--benefit',type=Path);p.add_argument('--batch-inputs',type=Path,nargs='+')
    p.add_argument('--frame-cache',type=Path);p.add_argument('--output',type=Path);p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--jobs',nargs='+',default=['verify','transport','merge_overlap','merge_append','batch_unique','batch_repeat'])
    args=p.parse_args();worker(args) if args.worker else run(args)
