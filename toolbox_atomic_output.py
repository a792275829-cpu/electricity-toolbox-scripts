"""Compatibility shim: the implementation lives in toolbox_shared.atomic_output."""
from __future__ import annotations

import sys

from toolbox_shared import atomic_output as _implementation

sys.modules[__name__] = _implementation
