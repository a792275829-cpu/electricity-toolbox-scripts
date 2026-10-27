"""Compatibility shim: the implementation lives in toolbox_shared.wps_config."""
from __future__ import annotations

import sys

from toolbox_shared import wps_config as _implementation

sys.modules[__name__] = _implementation
