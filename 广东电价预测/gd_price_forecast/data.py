from pathlib import Path

import pandas as pd

TARGET = "全省日前平均电价"


def drop_unavailable_model_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove fields unavailable when a day-ahead forecast is produced."""
    drop = [
        column
        for column in frame.columns
        if column.startswith("实际")
        or "实时" in column
        or column in {"来源文件", "来源记录数"}
    ]
    return frame.drop(columns=drop, errors="ignore")


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"日期", "时刻", TARGET}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"缺少必要字段: {sorted(missing)}")
    result = frame.copy()
    result["日期"] = pd.to_datetime(result["日期"], errors="raise").dt.normalize()
    parsed_time = pd.to_datetime(result["时刻"].astype(str), format="mixed", errors="coerce")
    if parsed_time.isna().any():
        raise ValueError("时刻字段包含无法解析的值")
    result["时刻"] = parsed_time.dt.strftime("%H:%M")
    return result.sort_values(["日期", "时刻"]).reset_index(drop=True)


def load_model_data(path: Path | str) -> pd.DataFrame:
    with pd.ExcelFile(path) as workbook:
        sheet = (
            "整合数据" if "整合数据" in workbook.sheet_names else workbook.sheet_names[0]
        )
        source = pd.read_excel(workbook, sheet_name=sheet)
    frame = _normalize(drop_unavailable_model_columns(source))
    validate_quarter_hour_grain(frame)
    return frame


def validate_quarter_hour_grain(frame: pd.DataFrame) -> None:
    normalized = _normalize(frame)
    if normalized.duplicated(["日期", "时刻"]).any():
        raise ValueError("日期+时刻存在重复记录")
    counts = normalized.groupby("日期", observed=True)["时刻"].nunique()
    bad = counts[counts != 96]
    if not bad.empty:
        raise ValueError(f"每天必须有96个时点，异常日期: {bad.to_dict()}")
    expected_slots = {f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)}
    if set(normalized["时刻"]) != expected_slots:
        raise ValueError("时刻必须为00:00至23:45的标准15分钟时点")
    dates = pd.DatetimeIndex(normalized["日期"].unique()).sort_values()
    missing = pd.date_range(dates.min(), dates.max()).difference(dates)
    if len(missing):
        raise ValueError(f"日期不连续，缺少: {missing.strftime('%Y-%m-%d').tolist()}")


def split_labeled_prediction_dates(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized = _normalize(frame)
    completeness = normalized.groupby("日期", observed=True)[TARGET].apply(lambda s: s.notna().all())
    partial = normalized.groupby("日期", observed=True)[TARGET].apply(lambda s: s.notna().any() and s.isna().any())
    if partial.any():
        raise ValueError(f"目标存在部分缺失日期: {list(partial[partial].index)}")
    labeled_dates = completeness[completeness].index
    return (
        normalized[normalized["日期"].isin(labeled_dates)].copy(),
        normalized[~normalized["日期"].isin(labeled_dates)].copy(),
    )
