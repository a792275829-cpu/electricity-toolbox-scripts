"""Collect target-day forecast-only inputs with publication-time checks.

Reuse the updater's client, schema mapping and 96-point parser. No actual values
or target prices may enter the forecast input snapshot.
"""
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def filter_forecast_payload(payload, target_date, cutoff):
    import update_gdfire_data as updater
    cutoff = pd.Timestamp(cutoff)
    if cutoff.tzinfo is None:
        raise ValueError('预测截止时间必须带时区')
    cutoff = cutoff.tz_convert('Asia/Shanghai')
    items, publications = [], []
    for item in payload.get('data', {}).get('dataNetLoadDTOList', []):
        kind = str(item.get('loadType'))
        if kind not in updater.FORECAST_LOAD_COLUMNS:
            continue
        records=[]
        for record in item.get('forecastPeriodList') or []:
            if str(record.get('date',''))[:10] != str(target_date):
                continue
            if not record.get('versionDate'):
                continue
            published=pd.Timestamp(record['versionDate'])
            published=published.tz_localize('Asia/Shanghai') if published.tzinfo is None else published.tz_convert('Asia/Shanghai')
            if published <= cutoff:
                records.append(record)
        valid=updater.latest_period_records(records)
        items.append({'loadType':kind,'forecastPeriodList':valid,'actualPeriodList':[]})
        publications.extend({'field':updater.FORECAST_LOAD_COLUMNS[kind], 'published_at':str(r['versionDate'])} for r in valid)
    return {'data':{'dataNetLoadDTOList':items}}, publications


def fetch_forecast_snapshot(target_date, output_dir, cutoff=None):
    import update_gdfire_data as updater
    output_dir=Path(output_dir); output_dir.mkdir(parents=True,exist_ok=True)
    cutoff=cutoff or datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    config=updater.load_config()
    client=updater.GDFireClient()
    client.login(str(config['username']),str(config['password']))
    payload=client.get('/gdfire/api/data/net/load',dict(startDate=target_date,endDate=target_date,provinceAreaId=updater.PROVINCE_AREA_ID))
    (output_dir/'预测日负荷接口原始.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    filtered,publications=filter_forecast_payload(payload,target_date,cutoff)
    frame=updater.build_increment(target_date,target_date,filtered,{'data':{}})
    columns=['日期','时刻',*updater.FORECAST_LOAD_COLUMNS.values()]
    frame=frame.reindex(columns=columns)
    frame.to_csv(output_dir/'预测日可用供需.csv',index=False,encoding='utf-8-sig')
    receipt=dict(target_date=target_date,cutoff=str(cutoff),rows=len(frame),
                 complete_fields=[c for c in columns[2:] if frame[c].notna().all()],publications=publications,
                 note='仅接受带发布时间且不晚于截止时间的预测曲线；未接入实际曲线或任何电价标签。')
    (output_dir/'预测日供需来源.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return frame,receipt


def attach_forecast_snapshot(frame, snapshot, target_date):
    from .features import FORECAST_COLUMNS
    result=frame.copy()
    snapshot=snapshot.copy()
    snapshot['日期']=pd.to_datetime(snapshot['日期']).dt.normalize()
    target=pd.Timestamp(target_date).normalize()
    slots={f'{h:02d}:{m:02d}' for h in range(24) for m in (0,15,30,45)}
    if len(snapshot)!=96 or not snapshot['日期'].eq(target).all() or set(snapshot['时刻'])!=slots:
        raise ValueError('预测日供需快照必须为指定日期的96个唯一时点')
    if not result['日期'].eq(target).any():
        addition=snapshot[['日期','时刻']].reindex(columns=result.columns)
        result=pd.concat([result,addition],ignore_index=True)
    indexed=snapshot.set_index('时刻')
    mask=result['日期'].eq(target)
    for column in FORECAST_COLUMNS:
        if column in indexed:
            result.loc[mask,column]=result.loc[mask,'时刻'].map(indexed[column])
    return result


def validate_forecast_snapshot(snapshot, receipt, target_date):
    """Require complete forecast curves and publication evidence for production."""
    import numpy as np
    from .features import FORECAST_COLUMNS

    if snapshot is None or receipt is None:
        raise ValueError('没有预测日供需快照及发布时间记录')
    if receipt.get('target_date') != str(target_date):
        raise ValueError('供需快照日期与预测日不一致')
    cutoff=pd.Timestamp(receipt.get('cutoff'))
    if pd.isna(cutoff) or cutoff.tzinfo is None:
        raise ValueError('供需快照截止时间必须带时区')
    if cutoff > pd.Timestamp.now(tz='Asia/Shanghai'):
        raise ValueError('供需快照截止时间不能晚于当前时间')
    target_start=pd.Timestamp(target_date).tz_localize('Asia/Shanghai')
    if cutoff >= target_start:
        raise ValueError('日前供需快照必须在预测日开始之前归档')
    # Reuse the same canonical date/slot validation as the attachment path.
    attach_forecast_snapshot(snapshot[['日期','时刻']].iloc[:0], snapshot, target_date)
    if not set(FORECAST_COLUMNS).issubset(snapshot.columns):
        raise ValueError('预测日10类供需字段未齐全')
    values=snapshot[list(FORECAST_COLUMNS)].apply(pd.to_numeric,errors='coerce')
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError('预测日供需曲线含缺失或非有限值')
    published_fields=set()
    for record in receipt.get('publications',[]):
        published=pd.Timestamp(record.get('published_at'))
        if pd.isna(published):
            raise ValueError('供需曲线缺少发布时间')
        published=published.tz_localize('Asia/Shanghai') if published.tzinfo is None else published
        if published > cutoff:
            raise ValueError('供需曲线发布时间晚于快照截止时间')
        published_fields.add(record.get('field'))
    if not set(FORECAST_COLUMNS).issubset(published_fields):
        raise ValueError('预测日10类供需发布时间记录未齐全')
    return snapshot.assign(**{column:values[column] for column in FORECAST_COLUMNS})
