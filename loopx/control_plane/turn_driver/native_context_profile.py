"""Native context transport defaults, independent of capture and quota IO."""
from __future__ import annotations


# Use the configured output profile for every native caller, including App
# shells that do not inherit the generated environment override. Smaller
# explicit reader budgets remain available for same-capture recovery.
NATIVE_CONTEXT_PAGE_BYTES = 64000
NATIVE_CONTEXT_PAGE_OUTPUT_BYTES = 64000
# Keep the configuration declaration usable by older installed readers too.
NATIVE_CONTEXT_LARGE_OUTPUT_BYTES = 64000
NATIVE_CONTEXT_HISTORY_TOKENS = 20000
NATIVE_CONTEXT_OUTPUT_ENV = "LOOPX_CONTEXT_PAGE_OUTPUT_BYTES"


def native_context_page_budget() -> tuple[int, int]:
    return NATIVE_CONTEXT_PAGE_BYTES, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES
