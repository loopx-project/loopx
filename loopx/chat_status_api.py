"""Deprecated public alias for :mod:`loopx.chat.status_api`.

Keep historical imports and their module-global patch points for at least one
minor release after the M1 move. The release owner decides the exact expiry.
"""

import sys
import warnings

from .chat import status_api as _implementation

# Copying exports would leave functions bound to different globals. Both import
# paths must resolve to the same module, including after reload or pickle lookup.
if not getattr(_implementation, "_legacy_status_import_warned", False):
    warnings.warn(
        "loopx.chat_status_api is deprecated; use loopx.chat.status_api. "
        "The legacy import remains available for at least one minor release.",
        DeprecationWarning,
        stacklevel=2,
    )
    _implementation._legacy_status_import_warned = True
sys.modules[__name__] = _implementation
