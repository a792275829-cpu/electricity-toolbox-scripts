"""Run a 30-station prelabel sample without changing any existing annotation draft."""
from __future__ import annotations
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import html
import base64
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from toolbox_shared import section_basemap as maps, section_topology as model
from toolbox_shared.section_prelabel import OCR_RECIPE, recognize, prelabel
from toolbox_shared.section_vision_review import configured_reviewer, decisions_reviewer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prices', type=Path, required=True)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--ocr-engine', choices=('macos-vision', 'ppocrv6'), default='macos-vision')
    p.add_argument('--vision', choices=('none', 'qwen', 'gemini'), default='none')
    p.add_argument('--ocr-cache', type=Path, help='Reuse only OCR with the same image digest and engine')
    p.add_argument('--baseline', action='store_true', help='Disable OCR retries and circle refinement for comparison')
    p.add_argument('--sample-file', type=Path, help='Fixed JSON list of {station, voltage}; includes missed OCR stations')
    p.add_argument('--review-decisions', type=Path, help='Replay visual review of the exact numbered candidates; no API call')
    args = p.parse_args()
    reviewer = configured_reviewer(args.vision)
    if args.review_decisions and args.vision != 'none':
        p.error('本地复核文件与在线视觉复核只能选择一种')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    image = Image.open(args.image).convert('RGB')
    prices_digest = sha256(args.prices.read_bytes()).hexdigest()
    metadata = maps.store_image(args.image.read_bytes(), args.image.name)
    digest = metadata['id']
    seed = model.seed_catalog()
    # This is an independent trial catalog. Do not read or save a user's draft.
    seed['nodes'] = []; seed['edges'] = []
    data = model.synchronize(seed, model.read_catalog(args.prices))
    data['base_map'] = metadata; data['schematic_generated'] = False
    sample = None
    if args.sample_file:
        sample = [(r['station'], r['voltage']) for r in json.loads(args.sample_file.read_text())]
        available = {(n['station'], n['voltage']) for n in data['nodes'] if n['present']}
        if not 1 <= len(sample) <= 30 or len(set(sample)) != len(sample) or any(g not in available for g in sample):
            p.error('固定小样须为1至30个不重复的现有站点及电压')
    recipe = 'native1500-baseline' if args.baseline else OCR_RECIPE
    if args.review_decisions:
        reviewer = decisions_reviewer(args.review_decisions, digest, recipe)
    if args.ocr_cache:
        cache = json.loads(args.ocr_cache.read_text())
        if cache['image_sha256'] != digest or cache['engine'] != args.ocr_engine or cache.get('recipe') not in (recipe, 'native1500-baseline'):
            p.error('OCR缓存的底图、引擎或处理版本不匹配')
        records = cache['records']
        if cache.get('recipe') != recipe:
            records = recognize(image, args.ocr_engine, station_names=sorted({n['station'] for n in data['nodes']}),
                                base_records=records, enhanced=True)
    else:
        records = recognize(image, args.ocr_engine, station_names=sorted({n['station'] for n in data['nodes']}), enhanced=not args.baseline)
    (args.output_dir/'ocr.json').write_text(json.dumps(dict(image_sha256=digest, engine=args.ocr_engine, recipe=recipe, records=records), ensure_ascii=False))
    result = prelabel(data, records=records, engine=args.ocr_engine, reviewer=reviewer, sample=sample, enhanced=not args.baseline)
    report = result['report']; report['vision_provider'] = 'offline-decisions' if args.review_decisions else args.vision
    report['execution'] = dict(ocr_source='cache' if args.ocr_cache else 'actual-inference',
                              ocr_cache_upgraded=bool(args.ocr_cache and cache.get('recipe') != recipe),
                              geometry='actual-algorithm', online_vision_api_called=args.vision != 'none',
                              independent_human_ground_truth=False)
    if args.review_decisions:
        report['review_source'] = json.loads(args.review_decisions.read_text()).get('source', '本地视觉复核记录')
    report['inputs'] = dict(image_sha256=digest, width=image.width, height=image.height,
                          prices_sha256=prices_digest, sample_file=args.sample_file.name if args.sample_file else None)
    if args.ocr_engine == 'ppocrv6':
        report['models'] = dict(detection='PP-OCRv6_medium_det', recognition='PP-OCRv6_medium_rec')
    truth_path = ROOT/'500kV断面分析/data/500kv_node_positions.json'
    truth = json.loads(truth_path.read_text()) if truth_path.is_file() else dict(image={}, nodes=[])
    comparable = truth['image'].get('sha256') == digest
    references = {model.station_name(n['name']): n['anchor'] for n in truth['nodes'] if n.get('status')=='verified'} if comparable else {}
    errors = []
    for row in report['rows']:
        reference = references.get(row['station']) if row['voltage']==500 else None
        row['reference'] = reference
        row['center_error_px'] = round(math.hypot(row['point']['x']-reference['x'], row['point']['y']-reference['y']), 2) if row['point'] and reference else None
        row['suspected_mismatch'] = row['center_error_px'] is not None and row['center_error_px'] > 30
        if row['suspected_mismatch']:
            warning = f"与已有人工锚点相差{row['center_error_px']}像素，疑似错配；请重点核对站名及符号，已有锚点也须复核"
            for node in result['data']['nodes']:
                if (node['station'], node['voltage']) == (row['station'], row['voltage']) and node.get('map_candidate'):
                    node['map_candidate']['warning'] = warning
        if row['center_error_px'] is not None:
            errors.append(row['center_error_px'])
    report['reference_comparison'] = dict(same_image=comparable, measured=len(errors), within_10px=sum(e<=10 for e in errors),
                                         over_30px=sum(e>30 for e in errors), mean_error_px=round(sum(errors)/len(errors),2) if errors else None,
                                         median_error_px=round(median(errors),2) if errors else None,
                                         max_error_px=max(errors) if errors else None,
                                         note='仅同底图500kV已有人工锚点可比较；锚点须复核，超30px为疑似错配。站名词表匹配数不是识别准确率；圆心统计包含疑似错配，不代表纯拟合精度')
    report['sample_selection'] = '固定站点清单（不依赖本次OCR结果）' if sample else '按OCR密度及未识别站名分层探索；非准确率评测'
    report['suspected_mismatches'] = sum(r['suspected_mismatch'] for r in report['rows'])
    output = args.output_dir
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (output/'待核对初标.json').write_text(json.dumps(maps.export_backup(result['data']), ensure_ascii=False))
    # Original-resolution crops for each trial, including unresolved examples.
    font = ImageFont.truetype(str(ROOT/'风力预测截图/fonts/NotoSerifSC.ttf'), 18)
    sheet = Image.new('RGB', (4*420, math.ceil(len(report['rows'])/4)*330), '#f3f5f7')
    for i, row in enumerate(report['rows']):
        x, y = (i%4)*420, (i//4)*330
        draw = ImageDraw.Draw(sheet)
        draw.text((x+10,y+8), f"{i+1}. {row['station']} {row['voltage']}kV", font=font, fill='black')
        draw.text((x+10,y+33), row['reason'], font=font, fill='#795000')
        b = row.get('box'); point = row['point']
        if b or row['reference']:
            center = point or row['reference'] or dict(x=b['x']+b['width']/2, y=b['y']+b['height']/2)
            left, top = max(0, min(image.width-400, int(center['x']-200))), max(0,min(image.height-250,int(center['y']-125)))
            crop = image.crop((left,top,left+400,top+250)); d=ImageDraw.Draw(crop)
            if b:
                d.rectangle((b['x']-left,b['y']-top,b['x']+b['width']-left,b['y']+b['height']-top), outline='#d70066', width=2)
            if point:
                a, c = point['x']-left, point['y']-top
                d.ellipse((a-10,c-10,a+10,c+10), outline='#ff9800',width=3)
            if row['reference']:
                a, c = row['reference']['x']-left, row['reference']['y']-top
                d.line((a-12,c,a+12,c), fill='#00a0ff', width=2)
                d.line((a,c-12,a,c+12), fill='#00a0ff', width=2)
            sheet.paste(crop,(x+10,y+65))
    sheet.save(output/'30站小样.png')
    preview = base64.b64encode((output/'30站小样.png').read_bytes()).decode('ascii')
    rows_html = ''.join('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in
                        (r['station'], r['voltage'], r['text'] or '未匹配', r['point'] or '未决', r['reason']))+'</tr>'
                        for r in report['rows'])
    (output/'核对报告.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>拓扑初标核对报告</title>'
        '<style>body{font:16px system-ui;margin:24px;color:#17202a}td,th{border:1px solid #ccc;padding:8px}'
        'table{border-collapse:collapse}img{max-width:100%;height:auto}pre{white-space:pre-wrap}</style>'
        '<h1>拓扑初标核对报告</h1><p>候选均为待核对；无独立人工电压／坐标真值，候选数量不是准确率。</p>'
        '<pre>'+html.escape(json.dumps({k:v for k,v in report.items() if k != 'rows'},ensure_ascii=False,indent=2))+'</pre>'
        '<table><tr><th>站名</th><th>电压</th><th>OCR文字</th><th>候选</th><th>结论</th></tr>'+rows_html+'</table>'
        '<p><a href="待核对初标.json" download>下载可导入待核对JSON</a></p>'
        '<img alt="原图逐站候选与文字框" src="data:image/png;base64,'+preview+'"></html>', encoding='utf-8')
    # Read back the deliverable and validate it before reporting success.
    restored = maps.import_backup(json.loads((output/'待核对初标.json').read_text()))
    model.validate_catalog(restored)
    assert not any(n.get('map_anchor') for n in restored['nodes'])
    assert sha256(args.prices.read_bytes()).hexdigest() == prices_digest, '原始电价表发生变化'
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}, ensure_ascii=False, indent=2))
    print(output.resolve())


if __name__ == '__main__':
    main()
