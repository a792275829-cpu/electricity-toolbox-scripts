"""Split prepared print tables at changes in their column headers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def split_print_table_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Give each day-recap subtable its own repeatable header without changing cells."""
    result: list[dict[str, Any]] = []
    for block in deepcopy(blocks):
        if block.get("block_kind") != "day-recap":
            result.append(block)
            continue

        rows = block.get("rows", [])
        header_rows = int(block.get("header_rows") or 0)
        starts = [
            index
            for index in range(header_rows, len(rows))
            if index > 0
            and rows[index].get("subtable_header")
            and (
                index == header_rows
                or not rows[index - 1].get("subtable_header")
            )
        ]
        if not starts:
            result.append(block)
            continue

        boundaries = [0, *starts, len(rows)]
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            section = dict(block)
            section["rows"] = rows[start:end]
            if index:
                count = 0
                while start + count < end and rows[start + count].get("subtable_header"):
                    count += 1
                section["header_rows"] = count
                section["suppress_heading"] = True
            if end < len(rows):
                section.pop("notes", None)
            result.append(section)
    return result
