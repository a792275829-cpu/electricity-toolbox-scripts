"""Compatibility shim: the implementation lives in toolbox_shared.workbook_commit."""
from __future__ import annotations

import sys

from toolbox_shared import workbook_commit as _implementation

sys.modules[__name__] = _implementation
