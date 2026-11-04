from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill

COLUMNS = ["日期", "时刻", "日前电价"]


def _write_sheet(workbook, name: str, sheet_frame: pd.DataFrame) -> None:
    sheet_frame.to_excel(workbook, sheet_name=name, index=False)
    sheet = workbook.book[name]
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = {"A": 14, "B": 10, "C": 16}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet["C"][1:]:
        cell.number_format = "0.00"


def write_price_workbook(path: Path, prediction: pd.DataFrame) -> None:
    """Write one sheet per predicted date, 96 quarter-hour prices each."""
    if prediction.empty:
        raise ValueError("没有可导出的预测结果")
    frame = prediction[["日期", "时刻", "预测日前电价"]].copy()
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    dates = sorted(frame["日期"].unique())
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for offset, day in enumerate(dates, start=1):
            part = frame.loc[frame["日期"].eq(day), ["时刻", "预测日前电价"]].copy()
            part.insert(0, "日期", day)
            part = part.rename(columns={"预测日前电价": "日前电价"})
            part = part.sort_values("时刻").reset_index(drop=True)
            if len(part) != 96 or part["时刻"].nunique() != 96:
                raise ValueError(
                    f"{pd.Timestamp(day).date()} 输出必须包含 96 个时点，当前为 {len(part)} 行"
                )
            part["日期"] = part["日期"].dt.date
            _write_sheet(writer, f"D+{offset}日前电价", part[COLUMNS])


# Backwards-compatible name: experiment runners export a single D+1 sheet.
write_d1_price_workbook = write_price_workbook
