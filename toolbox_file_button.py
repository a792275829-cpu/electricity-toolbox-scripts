"""Compatibility shim: the implementation lives in toolbox_shared.file_button."""
from __future__ import annotations

import sys

from toolbox_shared import file_button as _implementation

sys.modules[__name__] = _implementation
