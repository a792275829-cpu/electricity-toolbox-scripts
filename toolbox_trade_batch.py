"""Compatibility shim: the implementation lives in toolbox_shared.trade_batch."""
from __future__ import annotations

import sys

from toolbox_shared import trade_batch as _implementation

sys.modules[__name__] = _implementation
