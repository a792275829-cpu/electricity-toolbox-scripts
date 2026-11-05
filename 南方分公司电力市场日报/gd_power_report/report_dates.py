"""Report date read from the D workbook, shared by the CLI and the toolbox page.

Workbook layout and date-cell order stay owned by ``company_clearing_xlsx``;
this module only exposes that single reader so the GUI and the service worker
do not carry a second date-inference implementation.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPORT_ROOT = Path(__file__).resolve().parents[1]
if str(REPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPORT_ROOT))

from gd_power_report.company_clearing_xlsx import infer_report_date

__all__ = ["infer_report_date"]
