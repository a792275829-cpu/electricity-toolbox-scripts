"""Compatibility shim: the implementation lives in toolbox_shared.auth_loader."""
from __future__ import annotations

import sys

from toolbox_shared import auth_loader as _implementation

sys.modules[__name__] = _implementation
