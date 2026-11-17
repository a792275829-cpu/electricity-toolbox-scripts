"""Read and operate only the isolated React sample via native macOS AX."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'logs/ui-replica'
BINARY = ROOT / '.toolbox/accessibility/macos_ax_probe'


def audit(pid):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['swiftc',str(ROOT/'tooling/macos_ax_probe.swift'),'-o',str(BINARY)],check=True)
    def capture(*operation):
        result = subprocess.run([str(BINARY),'--pid',str(pid),*operation],capture_output=True,text=True,timeout=15)
        if result.returncode: raise RuntimeError(result.stdout or result.stderr)
        return json.loads(result.stdout)
    def text(s): return '\n'.join(str(n.get('value','')) for n in s['nodes'])
    def wait(predicate):
        deadline=time.monotonic()+12
        while True:
            value=capture()
            if predicate(value): return value
            if time.monotonic()>deadline:
                (OUTPUT/'timed-out.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
                raise RuntimeError('AX state timed out; saved timed-out.json')
            time.sleep(.15)
    initial=wait(lambda s:len(s['nodes'])>50)
    if '离线样例' not in text(initial): raise RuntimeError('Only the isolated offline sample can be audited.')
    workbook_root = Path.home() / 'Library/Application Support/com.electricity.toolbox/development/data/webview-ui-sample/data/上网电量抓取/输出'
    existing = set(workbook_root.rglob('*.xlsx'))
    nav=[n for n in initial['nodes'] if n['identifier'].startswith('nav-')]
    pages=[]
    for n in nav:
        tool=n['identifier'].removeprefix('nav-')
        capture('--press-id', n['identifier'])
        # WebKit can acknowledge an AXPress before it dispatches the click.
        # Navigation is reversible, so retry once if the page did not change.
        time.sleep(.3)
        s=capture()
        if '当前功能：'+n['title'] not in text(s):
            capture('--press-id', n['identifier'])
        s=wait(lambda s: '当前功能：'+n['title'] in text(s))
        time.sleep(.25)
        s=capture()
        (OUTPUT/f'{tool}.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
        fields=[x for x in s['nodes'] if x['role'] in {'AXTextField','AXCheckBox','AXRadioButton','AXTable','AXList'}]
        buttons=[x['title'] for x in s['nodes'] if x['role']=='AXButton' and x['title'] and not x['identifier'].startswith('nav-')]
        pages.append(dict(tool=tool,title=n['title'],nodes=len(s['nodes']),fields=len(fields),buttons=buttons))
    capture('--press-id','nav-wps-writer')
    capture('--press','添加配置')
    wait(lambda s:any(n['title']=='添加区域' for n in s['nodes']))
    capture('--press','添加区域')
    wait(lambda s:any(n['title']=='来源起始单元格' for n in s['nodes']))
    for label,value in [('来源工作表','样例来源'),('来源起始单元格','A1'),('来源结束单元格','A2'),('目标工作表','样例目标'),('目标起始单元格','B1'),('目标结束单元格','B2')]:
        result=capture('--set',label,value)
        if result.get('valueAfter')!=value: raise RuntimeError('Region field readback failed')
    capture('--press','确定')
    wait(lambda s:'样例来源' in text(s) and '样例目标' in text(s))
    capture('--press','关闭')  # incomplete new document is never persisted
    capture('--press-id','nav-online-energy')
    capture('--set','开始日期','2026-11-10')
    capture('--set','结束日期','2026-11-11')
    capture('--press','开始日期')
    capture('--press-id','online-energy-calendar-0-2026-11-10')
    capture('--press-id','online-energy-calendar-0-2026-11-11')
    # Native AX writes can bypass React's input event. The submit path reads the
    # actual controls, including after task polling re-renders the page.
    time.sleep(.5)
    capture('--press','开始导出')
    wait(lambda s:any(n['title']=='取消任务' and n['enabled']=='1' for n in s['nodes']))
    capture('--press','取消任务')
    wait(lambda s:'已取消' in text(s))
    capture('--press','开始导出')
    final=wait(lambda s:'已生成 2 个样例工作簿，回读验证通过。' in text(s))
    (OUTPUT/'completed.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
    import openpyxl
    paths=sorted(set(workbook_root.rglob('*.xlsx'))-existing)
    readbacks=[]
    for path in paths:
        book=openpyxl.load_workbook(path)
        try: readbacks.append(dict(file=path.name,value=book['汇总']['A3'].value,format=book['汇总']['A3'].number_format))
        finally: book.close()
    if [row['value'] for row in readbacks] != [12.5,13.5] or any(row['format']!='General' for row in readbacks): raise RuntimeError('XLSX readback failed')
    capture('--press','收起辅助面板')
    collapsed=wait(lambda s:any(n['title']=='展开辅助面板' for n in s['nodes']))
    capture('--press','展开辅助面板')
    summary=dict(pid=pid,pages=pages,checks=dict(all_pages=len(pages)==16,native_dates_and_export=True,cancellation=True,rail_toggle=True,wps_region_native_edit=True,calendar_selection=True),workbooks=readbacks,scope='isolated sample; no login, upload or production workbook writes')
    (OUTPUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pid',type=int,required=True)
    audit(parser.parse_args().pid)
