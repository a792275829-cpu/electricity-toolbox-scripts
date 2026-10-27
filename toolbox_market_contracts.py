"""Compatibility shim: the implementation lives in toolbox_shared.market_contracts."""
from __future__ import annotations

import sys

from toolbox_shared import market_contracts as _implementation

sys.modules[__name__] = _implementation
