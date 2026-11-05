"""Report date read from the D workbook, shared by the page and service worker.

Kept free of Tk imports because ``workflows`` also uses it while running a
headless service task.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path


def report_dates_module():
    from .runtime import load_module
    return load_module(
        "toolbox_market_report_dates",
        Path(__file__).resolve().parents[2]
        / "南方分公司电力市场日报/gd_power_report/report_dates.py",
    )


def report_date_from_day_report(path: str | Path) -> date:
    return report_dates_module().infer_report_date(Path(path))
