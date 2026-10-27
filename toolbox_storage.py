"""Compatibility shim: the implementation lives in toolbox_shared.storage."""
from __future__ import annotations

import sys

from toolbox_shared import storage as _implementation

sys.modules[__name__] = _implementation
