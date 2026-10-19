"""Read requested cell values and formats with one scan per worksheet."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from openpyxl.utils.cell import coordinate_to_tuple


def read_cell_snapshots(
    workbook, requests: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], tuple[Any, str | None]]:
    grouped = defaultdict(lambda: defaultdict(dict))
    result = {}
    for sheet_name, coordinate in requests:
        row, column = coordinate_to_tuple(coordinate)
        grouped[sheet_name][row][column] = coordinate
        result[(sheet_name, coordinate)] = (None, None)
    for sheet_name, requested_rows in grouped.items():
        columns = [column for row in requested_rows.values() for column in row]
        first_column, last_column = min(columns), max(columns)
        rows = workbook[sheet_name].iter_rows(
            min_row=min(requested_rows), max_row=max(requested_rows),
            min_col=first_column, max_col=last_column,
        )
        for row_number, cells in enumerate(rows, start=min(requested_rows)):
            for column, coordinate in requested_rows.get(row_number, {}).items():
                cell = cells[column - first_column]
                result[(sheet_name, coordinate)] = (cell.value, cell.number_format)
    return result
