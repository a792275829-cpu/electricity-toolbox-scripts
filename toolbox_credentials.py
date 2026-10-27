"""Compatibility shim: the implementation lives in toolbox_shared.credentials."""
from __future__ import annotations

import sys

from toolbox_shared import credentials as _implementation

sys.modules[__name__] = _implementation
