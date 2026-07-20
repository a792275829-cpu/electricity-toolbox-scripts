# 500kV Node Price Deduper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable local Excel deduplication tool for 500kV node price files.

**Architecture:** `src/deduplicate_500kv_prices.py` owns workbook parsing, filtering, normalization, deduplication, and the Tkinter UI. `tests/test_deduplicate_500kv_prices.py` creates small workbooks and verifies the business rules without using the large sample file.

**Tech Stack:** Python 3, openpyxl, Tkinter, unittest.

---

### Task 1: Core Behavior Tests

**Files:**
- Create: `tests/test_deduplicate_500kv_prices.py`

- [x] **Step 1: Write failing tests**

Create tests for name normalization and workbook processing.

- [x] **Step 2: Run tests to verify failure**

Run: `../.venv/bin/python -m unittest tests/test_deduplicate_500kv_prices.py -v`

Expected: fails because `src.deduplicate_500kv_prices` is not implemented yet.

### Task 2: Core Processing Module and UI

**Files:**
- Create: `src/deduplicate_500kv_prices.py`

- [x] **Step 1: Implement minimal processing code**

Implement `normalize_node_name`, `process_workbook`, `process_many`, and a Tkinter `main`.

- [x] **Step 2: Run tests to verify pass**

Run: `../.venv/bin/python -m unittest tests/test_deduplicate_500kv_prices.py -v`

Expected: all tests pass.

### Task 3: Sample Workbook Verification

**Files:**
- No source changes required unless verification exposes a defect.

- [x] **Step 1: Process the provided sample workbook**

Run the script against a local `实时节点电价查询.xlsx` sample.

- [x] **Step 2: Inspect output workbook**

Confirm output has only cleaned 500kV rows and no summary sheet.
