"""Compatibility shim: the implementation lives in toolbox_shared.native_compute."""
from __future__ import annotations

import sys

from toolbox_shared import native_compute as _implementation

sys.modules[__name__] = _implementation
