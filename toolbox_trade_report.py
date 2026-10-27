"""Compatibility shim: the implementation lives in toolbox_shared.trade_report."""
from __future__ import annotations

import sys

from toolbox_shared import trade_report as _implementation

sys.modules[__name__] = _implementation
