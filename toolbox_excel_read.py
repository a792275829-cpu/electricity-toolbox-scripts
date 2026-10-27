"""Compatibility shim: the implementation lives in toolbox_shared.excel_read."""
from __future__ import annotations

import sys

from toolbox_shared import excel_read as _implementation

sys.modules[__name__] = _implementation
