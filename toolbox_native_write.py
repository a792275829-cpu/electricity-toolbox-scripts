"""Compatibility shim: the implementation lives in toolbox_shared.native_write."""
from __future__ import annotations

import sys

from toolbox_shared import native_write as _implementation

sys.modules[__name__] = _implementation
