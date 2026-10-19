"""Reproducible ablations, development-only selection and sealed holdout.

The source workbook stores latest historical versions, not as-of snapshots.
Same-day exogenous experiments are conditional diagnostics and cannot win the
operational selection. Weather source provenance is unverified: exploratory.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from time import perf_counter

import joblib
import numpy as np
import pandas as pd
from openpyxl import load_workbook

from .calendar_features import CALENDAR_SOURCES, calendar_features
from .data import TARGET, load_model_data, split_labeled_prediction_dates
from .features import build_features, FORECAST_COLUMNS, WEATHER_COLUMNS
from .modeling import fit_model, predict_model
from .reporting import write_d1_price_workbook

MODEL_CONFIG = dict(name="hist_l15_experiment", kind="hist_gradient_boosting", params=dict(
    max_iter=200, learning_rate=.05, max_leaf_nodes=15, min_samples_leaf=40,
    l2_regularization=3., early_stopping=False,
))
DEVELOPMENT_STARTS = ("2025-05-02", "2025-10-05", "2026-02-20", "2026-05-02", "2026-06-18", "2026-08-15")


def candidate_specs():
    def spec(name, months=3, mode='none', holidays=False, supply=False, weather=False,
             half_life=None, eligible=True, fixed_weight=None, leaves=15, original_parameters=False):
        return dict(name=name, months=months, options=dict(exogenous_mode=mode, holidays=holidays,
                    supply=supply, weather=weather), half_life=half_life, eligible=eligible,
                    fixed_weight=fixed_weight, leaves=leaves, original_parameters=original_parameters)
    return [
        spec('旧特征_预测日置空_固定70%', mode='legacy', fixed_weight=.7),
        spec('原参数旧特征_预测日置空_固定70%',mode='legacy',fixed_weight=.7,original_parameters=True),
        spec('安全入口_滞后供需_原参数固定70%',mode='lagged',fixed_weight=.7,original_parameters=True),
        spec('历史价格_3月'), spec('价格加节假日_3月', holidays=True),
        spec('价格加滞后供需_3月', mode='lagged'),
        spec('节假日加滞后供需_3月', mode='lagged', holidays=True),
        *[spec(f'节假日供需衍生_{m}月', m, 'lagged', True, True) for m in (3, 6, 12)],
        *[spec(f'节假日供需衍生_{m}月_近期加权', m, 'lagged', True, True, half_life=90) for m in (6, 12)],
        spec('节假日供需衍生_6月_31叶', 6, 'lagged', True, True, leaves=31),
        spec('滞后天气探索_6月', 6, 'lagged', True, True, True, eligible=False),
        *[spec(f'当日供需条件情景_{m}月', m, 'published', True, True, eligible=False) for m in (3, 6, 12)],
    ]


def prepare_data(input_path, as_of):
    frame = load_model_data(input_path)
    labeled, _ = split_labeled_prediction_dates(frame)
    if labeled.empty:
        raise ValueError('没有完整96点日前电价标签')
    as_of = pd.Timestamp(as_of).normalize()
    frame = frame.loc[frame['日期'].le(as_of)].copy().reset_index(drop=True)
    labeled = labeled.loc[labeled['日期'].le(as_of)]
    last_labeled = labeled['日期'].max()
    if pd.isna(last_labeled):
        raise ValueError('预测截止日之前没有完整电价标签')
    # Extend only after a confirmed source day; preserve any existing future
    # forecast inputs but never consume their price labels.
    target_day = as_of + pd.Timedelta(days=1)
    if not frame['日期'].eq(target_day).any():
        future = pd.DataFrame({'日期': target_day,
                               '时刻': [f'{h:02d}:{m:02d}' for h in range(24) for m in (0, 15, 30, 45)]})
        frame = pd.concat([frame, future.reindex(columns=frame.columns)], ignore_index=True)
    frame.loc[frame['日期'].eq(target_day), TARGET] = np.nan
    return frame, last_labeled, target_day


def training_indices(identity, y, before, months):
    dates = identity['日期']
    cutoff = pd.Timestamp(before) - pd.DateOffset(months=int(months))
    rows = dates.lt(before) & dates.ge(cutoff) & y.notna()
    return np.flatnonzero(rows.to_numpy())


def sample_weights(identity, indices, before, half_life):
    if half_life is None:
        return None
    ages = (pd.Timestamp(before) - identity.iloc[indices]['日期']).dt.days.to_numpy()
    return np.exp2(-ages / float(half_life))


def _config(spec, quantile=None):
    config = deepcopy(MODEL_CONFIG)
    config['params']['max_leaf_nodes'] = spec['leaves']
    if spec.get('original_parameters'):
        config['params'].update(max_iter=400,learning_rate=.04,early_stopping='auto')
    if quantile is not None:
        config['params'].update(loss='quantile', quantile=float(quantile))
    return config


def _mask_legacy(X, frame, indices):
    result = X.iloc[indices].copy()
    for name in frame:
        if name in result and name not in {'日期', '时刻', TARGET, '来源文件', '来源记录数'}:
            result[name] = np.nan
            if name + '__missing' in result:
                result[name + '__missing'] = 1
    return result


def _mix(prediction, baseline, weight):
    return np.where(np.isfinite(baseline), weight * prediction + (1-weight) * baseline, prediction)


def _metrics(y, p):
    error = np.asarray(y) - np.asarray(p)
    return dict(mae=float(np.mean(np.abs(error))), rmse=float(np.sqrt(np.mean(error**2))),
                p90_absolute_error=float(np.quantile(np.abs(error), .9)), n=int(len(error)))


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def run_comparison(input_path, output_dir, *, as_of, specs=None, development_starts=None, holdout_days=14,
                   future_snapshot=None):
    started = perf_counter()
    input_path, output_dir = Path(input_path).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    frame, last_labeled, target_day = prepare_data(input_path, as_of)
    if future_snapshot is not None:
        from .future_inputs import attach_forecast_snapshot
        frame=attach_forecast_snapshot(frame,future_snapshot,target_day)
    specs = specs or candidate_specs()
    features = {}
    for spec in specs:
        key = tuple(sorted(spec['options'].items()))
        if key not in features:
            features[key] = build_features(frame, **spec['options'])
    identity, y = next(iter(features.values()))[2], next(iter(features.values()))[1]
    labeled_dates = sorted(identity.loc[y.notna(), '日期'].unique())
    holdout_dates = pd.DatetimeIndex(labeled_dates[-holdout_days:])
    if len(holdout_dates) != holdout_days:
        raise ValueError('留出日期不足')
    holdout_start = holdout_dates.min()
    holdout = np.flatnonzero(identity['日期'].isin(holdout_dates))
    starts = development_starts or DEVELOPMENT_STARTS
    folds = []
    for start in starts:
        days = pd.date_range(start, periods=7)
        if days.max() >= holdout_start:
            continue
        idx = np.flatnonzero(identity['日期'].isin(days) & y.notna())
        if len(idx) == 7*96:
            folds.append((pd.Timestamp(start), idx))
    if len(folds) < 2:
        raise ValueError('至少需要两个不与留出期重叠的完整7天开发窗口')
    development, dev_predictions, selection = [], {}, []
    candidates_by_name = {s['name']: s for s in specs}
    for number, spec in enumerate(specs, 1):
        print(f"开发期 {number}/{len(specs)}：{spec['name']}", flush=True)
        X, _, _ = features[tuple(sorted(spec['options'].items()))]
        outputs = []
        for start, valid in folds:
            train = training_indices(identity, y, start, spec['months'])
            if len(train) < 30*96:
                raise ValueError(f'{spec["name"]} 开发窗口训练数据不足30天')
            assert identity.iloc[train]['日期'].max() < identity.iloc[valid]['日期'].min()
            model = fit_model(X.iloc[train], y.iloc[train], _config(spec),
                              sample_weights(identity, train, start, spec['half_life']))
            valid_X = _mask_legacy(X, frame, valid) if spec['options']['exogenous_mode'] == 'legacy' else X.iloc[valid]
            outputs.append(pd.DataFrame({'row': valid, 'fold': str(start.date()), 'actual': y.iloc[valid].to_numpy(),
                                         'model': predict_model(model, valid_X),
                                         'd1': X.iloc[valid]['price_lag_1d'].to_numpy(),
                                         'd7': X.iloc[valid]['price_lag_7d'].to_numpy()}))
        predictions = pd.concat(outputs, ignore_index=True)
        dev_predictions[spec['name']] = predictions
        choices = []
        for baseline in ('d1', 'd7'):
            weights = [spec['fixed_weight']] if spec['fixed_weight'] is not None else [0., .25, .5, .7, .85, 1.]
            if spec['fixed_weight'] is not None and baseline != 'd1':
                continue
            for weight in weights:
                mixed = _mix(predictions['model'], predictions[baseline], weight)
                fold_errors = pd.DataFrame({'fold': predictions['fold'], 'error': np.abs(predictions['actual']-mixed)}).groupby('fold')['error'].mean()
                row = dict(name=spec['name'], baseline=baseline, weight=float(weight),
                           development_mae=float(fold_errors.mean()), development_std=float(fold_errors.std(ddof=0)),
                           eligible=bool(spec['eligible']), folds=len(folds))
                development.append(row)
                choices.append(row)
        best = min(choices, key=lambda r: (r['development_mae'], r['development_std']))
        selection.append(best)
        print(f"  开发MAE={best['development_mae']:.3f} 权重={best['weight']:.2f} 基线={best['baseline']}", flush=True)
    winner = min((r for r in selection if r['eligible']), key=lambda r: (r['development_mae'], r['development_std']))
    conditional_choices=[r for r in selection if candidates_by_name[r['name']]['options']['exogenous_mode']=='published']
    conditional_winner=min(conditional_choices,key=lambda r:(r['development_mae'],r['development_std'])) if conditional_choices else None
    # Winner is sealed before reading holdout performance; no promotion by holdout rank.
    _write_json(output_dir/'开发期锁定方案.json', dict(winner=winner, conditional_winner=conditional_winner, holdout_start=str(holdout_start.date()),
                                                    holdout_end=str(holdout_dates.max().date())))
    result_rows, grouped, point_outputs, holdout_models = [], [], [], {}
    cal = calendar_features(identity['日期'])
    thresholds = y.loc[identity['日期'].lt(holdout_start)].quantile([.1, .9])
    for number, chosen in enumerate(selection, 1):
        spec = candidates_by_name[chosen['name']]
        print(f"留出期 {number}/{len(selection)}：{chosen['name']}", flush=True)
        X, _, _ = features[tuple(sorted(spec['options'].items()))]
        train = training_indices(identity, y, holdout_start, spec['months'])
        model = fit_model(X.iloc[train], y.iloc[train], _config(spec),
                          sample_weights(identity, train, holdout_start, spec['half_life']))
        valid_X = _mask_legacy(X, frame, holdout) if spec['options']['exogenous_mode'] == 'legacy' else X.iloc[holdout]
        raw = predict_model(model, valid_X)
        baseline = X.iloc[holdout]['price_lag_1d' if chosen['baseline'] == 'd1' else 'price_lag_7d'].to_numpy()
        prediction = _mix(raw, baseline, chosen['weight'])
        result_rows.append(dict(**chosen, **{'holdout_'+k:v for k,v in _metrics(y.iloc[holdout], prediction).items()},
                                selected=chosen['name'] == winner['name'], months=spec['months'], half_life=spec['half_life'],
                                holdout_raw_model_mae=_metrics(y.iloc[holdout],raw)['mae'],
                                holdout_fixed70_d1_mae=_metrics(y.iloc[holdout],_mix(raw,X.iloc[holdout]['price_lag_1d'].to_numpy(),.7))['mae']))
        part = identity.iloc[holdout].copy()
        part['方案'] = chosen['name']; part['实际电价'] = y.iloc[holdout].to_numpy(); part['预测电价'] = prediction
        part['模型原始预测']=raw
        part['D-1基线']=X.iloc[holdout]['price_lag_1d'].to_numpy()
        part['D-7基线']=X.iloc[holdout]['price_lag_7d'].to_numpy()
        part['绝对误差'] = np.abs(part['实际电价']-prediction)
        point_outputs.append(part)
        masks = {'全部': np.ones(len(holdout), dtype=bool),
                 '法定假期': cal.iloc[holdout]['holiday_kind'].gt(0).to_numpy(),
                 '假期末日': cal.iloc[holdout]['is_holiday_last'].eq(1).to_numpy(),
                 '节后首工作日': cal.iloc[holdout]['post_holiday_workday'].eq(1).to_numpy(),
                 '较高价时点': y.iloc[holdout].ge(thresholds.loc[.9]).to_numpy(),
                 '较低价时点': y.iloc[holdout].le(thresholds.loc[.1]).to_numpy()}
        for label, mask in masks.items():
            if mask.any():
                grouped.append(dict(方案=chosen['name'], 分组=label, **_metrics(y.iloc[holdout].to_numpy()[mask], prediction[mask])))
        if chosen['name'] == winner['name']:
            holdout_models['point'] = model
            holdout_models['point_prediction'] = prediction
    first_X = next(iter(features.values()))[0]
    for lag in (1, 7):
        prediction = first_X.iloc[holdout][f'price_lag_{lag}d'].to_numpy()
        mask = np.isfinite(prediction)
        result_rows.append(dict(name=f'D-{lag}同刻基线', eligible=True, selected=False,
                                **{'holdout_'+k:v for k,v in _metrics(y.iloc[holdout].to_numpy()[mask], prediction[mask]).items()}))
    comparison = pd.DataFrame(result_rows)
    dev_table = pd.DataFrame(development)
    all_predictions = pd.concat(point_outputs, ignore_index=True)
    comparison.to_csv(output_dir/'方案对比.csv', index=False, encoding='utf-8-sig')
    dev_table.to_csv(output_dir/'开发期权重比较.csv', index=False, encoding='utf-8-sig')
    all_predictions.to_csv(output_dir/'留出期逐点预测.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(grouped).to_csv(output_dir/'分组误差.csv', index=False, encoding='utf-8-sig')

    selected_spec = candidates_by_name[winner['name']]
    X, _, _ = features[tuple(sorted(selected_spec['options'].items()))]
    quantile_dev = []
    for start, valid in folds:
        train = training_indices(identity, y, start, selected_spec['months'])
        predictions = []
        for q in (.1, .9):
            model = fit_model(X.iloc[train], y.iloc[train], _config(selected_spec, q),
                              sample_weights(identity, train, start, selected_spec['half_life']))
            valid_X = _mask_legacy(X, frame, valid) if selected_spec['options']['exogenous_mode'] == 'legacy' else X.iloc[valid]
            base = X.iloc[valid]['price_lag_1d' if winner['baseline'] == 'd1' else 'price_lag_7d'].to_numpy()
            predictions.append(_mix(predict_model(model, valid_X), base, winner['weight']))
        lo, hi = np.minimum(*predictions), np.maximum(*predictions)
        quantile_dev.extend(np.maximum.reduce([lo-y.iloc[valid].to_numpy(), y.iloc[valid].to_numpy()-hi, np.zeros(len(valid))]))
    calibration = float(np.quantile(quantile_dev, .8, method='higher'))
    quantile_holdout = []
    train = training_indices(identity, y, holdout_start, selected_spec['months'])
    for q in (.1, .9):
        model = fit_model(X.iloc[train], y.iloc[train], _config(selected_spec, q),
                          sample_weights(identity, train, holdout_start, selected_spec['half_life']))
        valid_X = _mask_legacy(X, frame, holdout) if selected_spec['options']['exogenous_mode'] == 'legacy' else X.iloc[holdout]
        base = X.iloc[holdout]['price_lag_1d' if winner['baseline'] == 'd1' else 'price_lag_7d'].to_numpy()
        quantile_holdout.append(_mix(predict_model(model, valid_X), base, winner['weight']))
    lo, hi = np.minimum(*quantile_holdout), np.maximum(*quantile_holdout)
    qpoint = holdout_models['point_prediction']
    raw_lo, raw_hi = np.minimum(lo, qpoint), np.maximum(hi, qpoint)
    lo, hi = raw_lo-calibration, raw_hi+calibration
    truth = y.iloc[holdout].to_numpy()
    interval_metrics = dict(nominal_coverage=.8, raw_coverage=float(np.mean((truth>=raw_lo)&(truth<=raw_hi))),
                            calibrated_coverage=float(np.mean((truth>=lo)&(truth<=hi))),
                            mean_width=float(np.mean(hi-lo)), calibration_adjustment=calibration,
                            calibration_points=len(quantile_dev), holdout_points=len(truth),
                            note='开发期残差校准；时间相关性和分布变化下不保证80%覆盖率。')
    interval_points = identity.iloc[holdout].copy()
    interval_points['实际电价']=truth; interval_points['中值预测']=qpoint
    interval_points['下界']=lo; interval_points['上界']=hi
    interval_points.to_csv(output_dir/'留出期区间.csv', index=False, encoding='utf-8-sig')
    weather_diagnostics=[]
    for column in WEATHER_COLUMNS:
        if column in frame:
            valid=frame[column].notna()
            latest=frame.loc[valid,'日期'].max()
            weather_diagnostics.append(dict(field=column,last_valid_date=str(latest.date()) if pd.notna(latest) else None,
                                            holdout_nonnull_fraction=float(frame.iloc[holdout][column].notna().mean())))

    future = pd.DataFrame()
    conditional_future = pd.DataFrame()
    conditional_status='未生成：没有通过发布时间核验的完整预测日供需快照'
    forecast_status = '未生成：最新完整标签与预测基准日不一致'
    if last_labeled == pd.Timestamp(as_of).normalize():
        future_idx = np.flatnonzero(identity['日期'].eq(target_day))
        train = training_indices(identity, y, target_day, selected_spec['months'])
        final_model = fit_model(X.iloc[train], y.iloc[train], _config(selected_spec),
                                sample_weights(identity, train, target_day, selected_spec['half_life']))
        fX = _mask_legacy(X, frame, future_idx) if selected_spec['options']['exogenous_mode'] == 'legacy' else X.iloc[future_idx]
        base = X.iloc[future_idx]['price_lag_1d' if winner['baseline']=='d1' else 'price_lag_7d'].to_numpy()
        point = _mix(predict_model(final_model, fX), base, winner['weight'])
        quantiles = []
        for q in (.1, .9):
            model = fit_model(X.iloc[train], y.iloc[train], _config(selected_spec, q),
                              sample_weights(identity, train, target_day, selected_spec['half_life']))
            quantiles.append(_mix(predict_model(model, fX), base, winner['weight']))
        future = identity.iloc[future_idx].copy()
        future['预测日前电价']=point
        future['下界']=np.minimum(np.minimum(*quantiles),point)-calibration
        future['上界']=np.maximum(np.maximum(*quantiles),point)+calibration
        if len(future)!=96 or not np.isfinite(future[['预测日前电价','下界','上界']]).all().all():
            raise ValueError('预测结果必须有96个有限数值')
        write_d1_price_workbook(output_dir/'D+1日前电价.xlsx', future)
        joblib.dump(dict(model=final_model, feature_options=selected_spec['options'], winner=winner,
                         columns=X.columns.tolist(), as_of=str(as_of)), output_dir/'实验选定模型.joblib')
        forecast_status='已生成历史信息模型预测'
        if conditional_winner is not None and future_snapshot is not None:
            spec=candidates_by_name[conditional_winner['name']]
            cX,_,_=features[tuple(sorted(spec['options'].items()))]
            if all(c in frame and frame.loc[identity['日期'].eq(target_day),c].notna().all() for c in FORECAST_COLUMNS):
                ctrain=training_indices(identity,y,target_day,spec['months'])
                cm=fit_model(cX.iloc[ctrain],y.iloc[ctrain],_config(spec),
                             sample_weights(identity,ctrain,target_day,spec['half_life']))
                cp=predict_model(cm,cX.iloc[future_idx])
                cb=cX.iloc[future_idx]['price_lag_1d' if conditional_winner['baseline']=='d1' else 'price_lag_7d'].to_numpy()
                conditional_future=identity.iloc[future_idx].copy()
                conditional_future['预测日前电价']=_mix(cp,cb,conditional_winner['weight'])
                if not np.isfinite(conditional_future['预测日前电价']).all():
                    raise ValueError('当日供需条件预测包含非有限值')
                write_d1_price_workbook(output_dir/'当日供需条件实验_D+1日前电价.xlsx',conditional_future)
                conditional_status='已生成；仅适用于该快照截止时间之后，不证明更早交易时点可用'
    limitations = [
        '历史表保存最新版本，无逐日发布时间快照；滞后供需也无法完全消除后续修订影响。',
        '当日供需情景缺少历史可用性证明，不参与正式方案选择。',
        '天气字段空间口径和原始来源未确认；仅测试滞后天气，排除正式选择。',
        '每个开发窗口/留出期开始前训练一次；每天更新D-1特征，并非每日重新训练。',
        '留出期不参与模型、窗口、权重或区间校准选择；总体MAE不能代表所有季节。',
    ]
    summary = dict(as_of=str(pd.Timestamp(as_of).date()), last_labeled_date=str(last_labeled.date()),
                   target_date=str(target_day.date()), input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
                   candidate_count=len(specs), development_windows=[str(d.date()) for d,_ in folds],
                   holdout_start=str(holdout_start.date()), holdout_end=str(holdout_dates.max().date()),
                   winner=winner, selected_holdout_metrics=next(r for r in result_rows if r.get('selected')),
                   conditional_winner=conditional_winner, conditional_forecast_status=conditional_status,
                   interval_metrics=interval_metrics, forecast_status=forecast_status,
                   weather_diagnostics=weather_diagnostics,
                   calendar_sources=CALENDAR_SOURCES, limitations=limitations,
                   runtime_seconds=perf_counter()-started)
    _write_json(output_dir/'实验结果.json', summary)
    with pd.ExcelWriter(output_dir/'算法实验对比.xlsx', engine='openpyxl') as writer:
        comparison.to_excel(writer, sheet_name='方案对比', index=False)
        pd.DataFrame(grouped).to_excel(writer, sheet_name='分组误差', index=False)
        dev_table.to_excel(writer, sheet_name='开发期权重比较', index=False)
        interval_points.to_excel(writer, sheet_name='留出期区间', index=False)
        pd.DataFrame([interval_metrics]).to_excel(writer, sheet_name='区间评估', index=False)
        future.to_excel(writer, sheet_name='预测日96点', index=False)
        conditional_future.to_excel(writer,sheet_name='当日供需条件预测',index=False)
        pd.DataFrame({'说明':limitations}).to_excel(writer, sheet_name='实验说明', index=False)
        for sheet in writer.book:
            sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width=min(45, max(14,len(str(col[0].value))+4))
    book=load_workbook(output_dir/'算法实验对比.xlsx', read_only=True, data_only=True)
    assert book['方案对比'].max_row==len(comparison)+1
    assert book['留出期区间'].max_row==len(holdout)+1
    if not future.empty:
        assert book['预测日96点'].max_row==97
        assert np.isclose(book['预测日96点']['C2'].value,float(future.iloc[0]['预测日前电价']))
    book.close()
    if not future.empty:
        book=load_workbook(output_dir/'D+1日前电价.xlsx',read_only=True,data_only=True)
        assert book.active.max_row==97 and book.active['C2'].number_format=='0.00'
        assert book.active['B2'].value=='00:00' and book.active['B97'].value=='23:45'
        assert np.isclose(book.active['C2'].value,float(future.iloc[0]['预测日前电价']))
        book.close()
    if not conditional_future.empty:
        book=load_workbook(output_dir/'当日供需条件实验_D+1日前电价.xlsx',read_only=True,data_only=True)
        assert book.active.max_row==97 and book.active['C2'].number_format=='0.00'
        assert np.isclose(book.active['C2'].value,float(conditional_future.iloc[0]['预测日前电价']))
        book.close()
    return summary
