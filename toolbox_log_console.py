"""Compatibility shim: the implementation lives in toolbox_shared.log_console."""
from __future__ import annotations

import sys

from toolbox_shared import log_console as _implementation

sys.modules[__name__] = _implementation
