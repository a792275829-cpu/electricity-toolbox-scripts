"""Compatibility shim: the implementation lives in toolbox_shared.province_excel."""
from __future__ import annotations

import sys

from toolbox_shared import province_excel as _implementation

sys.modules[__name__] = _implementation
