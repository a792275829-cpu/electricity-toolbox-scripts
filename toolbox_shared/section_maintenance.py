"""500kV disclosure maintenance, separate from price-based congestion rules."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from toolbox_shared.section_schema import _column_map, normalize_price_node_name
from toolbox_shared.storage import WORKSPACE_ROOT

API_PATH = '/gdfire/api/data/net/ptt/queryCompareInfo'
APP_URL = 'https://xhxt.chng.com.cn/gdfire/SpotDecisionSupport/InfoCompare'
STATE_DIR = WORKSPACE_ROOT / '500kV断面分析/local_data/检修登录'
# Reviewed against the wiring diagram; never infer arbitrary endpoints from initials.
LINE_ENDPOINTS = {
    '蓄荔': ('广蓄A厂', '荔西站'),
    '蓄能AB联': ('广蓄A厂', '广蓄B厂'),
    '海胪': ('海门电厂', '胪岗站'),
    '胪盘': ('胪岗站', '盘龙站'),
    '陆吉': ('陆丰站', '吉康站'),
    '盘吉': ('盘龙站', '吉康站'),
}
DATE_RE = re.compile(r'(?<!\d)(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})(?:日)?(?!\d)')


def workbook_trade_date(path: Path) -> date:
    """Only sheet titles and cells before the price header establish D, never filenames."""
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    found = set()
    try:
        for sheet in book.worksheets:
            title_values = [sheet.title]
            for row in sheet.iter_rows(max_row=30, max_col=8, values_only=True):
                if _column_map(row):
                    break
                title_values.extend(v for v in row if v is not None)
            for value in title_values:
                if isinstance(value, (date, datetime)):
                    found.add(value.date() if isinstance(value, datetime) else value)
                else:
                    for m in DATE_RE.finditer(str(value)):
                        found.add(date(*map(int, m.groups())))
    finally:
        book.close()
    if len(found) != 1:
        raise ValueError('Excel 标题日期必须唯一，无法确定检修运行日 D；请核对工作表标题或表头前的日期。')
    return found.pop()


def normalize_snapshot(raw: dict, run_date: date) -> dict:
    """Accept an API capture or our portable snapshot, validating dates and business status."""
    if 'payload' in raw:
        query = parse_qs(urlsplit(raw.get('url', '')).query)
        if raw.get('status') != 200 or query.get('runDate') != [run_date.isoformat()] or query.get('compareDate') != [(run_date-timedelta(days=1)).isoformat()]:
            raise ValueError('检修接口样本日期与 Excel 标题 D / D−1 不一致，或 HTTP 状态失败。')
        payload = raw['payload']
        if not isinstance(payload, dict) or payload.get('retCode') != 'T200' or not isinstance(payload.get('data'), list):
            raise ValueError('检修接口未返回有效数据列表。')
        rows = payload['data']
        source = raw.get('url', '')
    else:
        if raw.get('run_date') != run_date.isoformat() or raw.get('compare_date') != (run_date-timedelta(days=1)).isoformat():
            raise ValueError('检修快照日期与 Excel 标题 D / D−1 不一致。')
        rows = raw.get('records')
        source = raw.get('source', '')
    if not isinstance(rows, list):
        raise ValueError('检修数据必须为列表。')
    checked = []
    for row in rows:
        if not isinstance(row, dict) or not {'elementName', 'voltageLevel', 'deviceType', 'diff', 'startDate', 'endDate', 'runDate'} <= row.keys():
            raise ValueError('检修记录缺少必需字段。')
        if row['diff'] not in {'EQUAL', 'ADD', 'DELETE', 'INCREASE', 'DECREASE', 'REMOVE', 'REDUCE'}:
            raise ValueError('检修接口返回未知对比状态，已停止以免误标线路。')
        for key in ('startDate', 'endDate', 'runDate'):
            datetime.fromisoformat(row[key])
        if not isinstance(row['elementName'], str) or not row['elementName'].strip():
            raise ValueError('检修记录元件名称为空。')
        checked.append(dict(row))
    return dict(run_date=run_date.isoformat(), compare_date=(run_date-timedelta(days=1)).isoformat(),
                source=source, fetched_at=raw.get('fetched_at', ''), records=checked)


def read_snapshot(path: Path, run_date: date) -> dict:
    return normalize_snapshot(json.loads(Path(path).read_text(encoding='utf-8')), run_date)


def fetch_snapshot(context, run_date: date) -> dict:
    """The page's date-only query selects current versions for both days."""
    from toolbox_shared.qingneng_login import BASE_URL
    params = dict(runDate=run_date.isoformat(), compareDate=(run_date-timedelta(days=1)).isoformat(),
                  runType=1, compareType=1)
    from playwright.sync_api import Error as PlaywrightError
    from toolbox_shared.qingneng_login import LoginError, _probe_failure_reason
    try:
        response = context.request.get(BASE_URL + API_PATH, params=params, timeout=20000)
    except PlaywrightError as exc:
        raise LoginError('network', '读取输变电检修信息失败：'+_probe_failure_reason(exc)) from None
    if response.status != 200:
        raise RuntimeError(f'读取输变电检修信息失败（HTTP {response.status}）。')
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError('检修接口未返回 JSON。') from None
    from urllib.parse import urlencode
    raw = dict(status=200, url=BASE_URL+API_PATH+'?'+urlencode(params), payload=payload,
               fetched_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    return normalize_snapshot(raw, run_date)


def collect_snapshots(days: list[date]) -> dict[date, dict]:
    from playwright.sync_api import sync_playwright
    from toolbox_shared.auth_loader import load_login_module
    auth = load_login_module(WORKSPACE_ROOT, module_name='section_maintenance_auth',
                             state_dir=STATE_DIR, app_url=APP_URL)
    config = auth.load_config()
    result = {}
    with auth.AuthStateLock():
        with sync_playwright() as playwright:
            context = auth.launch_context(playwright, headless=True)
            try:
                auth.ensure_login(context, config)
                for day in dict.fromkeys(days):
                    result[day] = fetch_snapshot(context, day)
            finally:
                from toolbox_shared.qingneng_login import close_preserving_error
                close_preserving_error(context)
    return result


def attach_maintenance(data: dict, snapshot: dict) -> None:
    """Annotate only explicit known station pairs; retain unsupported equipment for review."""
    lookup = {tuple(sorted(normalize_price_node_name(e[k]) for k in ('a','b'))): e for e in data['edges']}
    for edge in data['edges']:
        edge['maintenance'] = []
    separate, unmatched, removed = [], [], []
    line_count = 0
    for record in snapshot['records']:
        if str(record['voltageLevel']) != '500':
            continue
        if record['diff'] in {'DELETE', 'DECREASE', 'REMOVE', 'REDUCE'}:
            removed.append(record)
            continue
        if record['runDate'][:10] != snapshot['run_date']:
            raise ValueError('检修记录运行日与 Excel 标题日期不一致。')
        name = record['elementName']
        match = next((m for prefix in LINE_ENDPOINTS if (m := re.search(re.escape(prefix)+r'(?:[甲乙丙丁戊己庚辛壬癸]|[一二三四五六七八九十0-9ⅠⅡⅢ])*线', name))), None)
        if record['deviceType'] not in {'LINE', 'SWITCH'} or (record['deviceType']=='SWITCH' and ('线' not in name or '母线' in name)):
            separate.append(record)
            continue
        line_count += 1
        if match is None:
            unmatched.append(record)
            continue
        prefix = next(p for p in LINE_ENDPOINTS if match.group().startswith(p))
        endpoints = LINE_ENDPOINTS[prefix]
        if prefix == '蓄荔':
            if name.startswith('广蓄B厂'):
                endpoints = ('广蓄B厂', '荔西站')
            elif not name.startswith('广蓄A厂'):
                unmatched.append(record)
                continue
        edge = lookup.get(tuple(sorted(map(normalize_price_node_name, endpoints))))
        if edge is None:
            unmatched.append(record)
            continue
        start = record['startDate']
        display_start = start[:10] if start[11:] in ('00:00:00', '') else start
        suffix = '开关检修' if record['deviceType']=='SWITCH' else '检修'
        edge['maintenance'].append(dict(record, label=f'{match.group()}{suffix} · {display_start} 起'))
    data['maintenance'] = dict(run_date=snapshot['run_date'], compare_date=snapshot['compare_date'],
        source=snapshot.get('source', ''), fetched_at=snapshot.get('fetched_at', ''),
        total=len(snapshot['records']), line_records=line_count,
        matched_records=sum(len(e['maintenance']) for e in data['edges']),
        matched_edges=sum(bool(e['maintenance']) for e in data['edges']),
        separate=separate, unmatched=unmatched, removed=removed)
