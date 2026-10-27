"""Compatibility shim: the implementation lives in toolbox_shared.date_input."""
from __future__ import annotations

import sys

from toolbox_shared import date_input as _implementation

sys.modules[__name__] = _implementation
