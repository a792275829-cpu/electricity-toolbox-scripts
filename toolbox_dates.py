"""Compatibility shim: the implementation lives in toolbox_shared.dates."""
from __future__ import annotations

import sys

from toolbox_shared import dates as _implementation

sys.modules[__name__] = _implementation
