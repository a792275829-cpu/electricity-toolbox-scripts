"""Compatibility shim: the implementation lives in toolbox_shared.choices."""
from __future__ import annotations

import sys

from toolbox_shared import choices as _implementation

sys.modules[__name__] = _implementation
