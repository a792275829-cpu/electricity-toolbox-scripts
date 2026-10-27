"""Compatibility shim: the implementation lives in toolbox_shared.redaction."""
from __future__ import annotations

import sys

from toolbox_shared import redaction as _implementation

sys.modules[__name__] = _implementation
