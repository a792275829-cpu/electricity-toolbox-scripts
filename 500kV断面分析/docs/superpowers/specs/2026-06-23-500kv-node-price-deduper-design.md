# 500kV Node Price Deduper Design

## Goal

Build a reusable local script with a light interaction interface that can process one or more real-time node price Excel files and output cleaned workbooks containing only deduplicated 500kV node prices.

## Input Shape

The confirmed sample workbook has a detail sheet with these columns:

- `节点名称`
- `数据项`
- 96 time columns from `00:00` through `23:45`
- An optional trailing empty column

It may also contain summary sheets such as `全省-实时节点电价查询(...)`, which are not part of the output.

## Processing Rules

1. Only sheets containing `节点名称`, `数据项`, and at least one time column are processed.
2. Only rows whose `节点名称` contains `500kV` case-insensitively are retained.
3. Output node names are normalized by removing trailing numbered branch markers such as `#1`, `#2`, `#1M`, and `#2M`.
4. Rows are deduplicated by normalized node name, `数据项`, and the complete time-price series.
5. Output workbooks contain only cleaned detail sheets. Empty summary or unrelated sheets are omitted.
6. Each input file is written to a sibling output file named `<original_stem>_500kV去重.xlsx`.

## Interface

The script exposes a Tkinter desktop window with:

- A file picker that accepts multiple `.xlsx` files.
- A process button.
- A result log showing each output path, kept row count, and removed row count.

The same processing functions are importable for tests and future automation.

## Error Handling

Unreadable files, files with no processable sheets, and save failures are reported in the interface log. One bad file does not stop the remaining selected files from being processed.

## Testing

Automated tests cover node-name normalization, 500kV filtering, deduplication, summary-sheet omission, and output column cleanup.
