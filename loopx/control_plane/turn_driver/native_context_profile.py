"""Native context transport defaults, independent of capture and quota IO."""
from __future__ import annotations

import os


# Use the configured output profile for every native caller, including App
# shells that do not inherit the generated environment override. Smaller
# explicit reader budgets remain available for same-capture recovery.
NATIVE_CONTEXT_PAGE_BYTES = 64000
NATIVE_CONTEXT_PAGE_OUTPUT_BYTES = 64000
# Native openings cross a tool boundary, whether attached or linked. When
# that tool's cap is unknown, avoid the bulk-reader profile.
NATIVE_CONTEXT_FIRST_READ_FALLBACK_BYTES = 12000
# A Markdown Inbox capture must remain readable in shells that do not
# declare the larger native profile, including with multilingual briefs.
NATIVE_INBOX_MARKDOWN_OUTPUT_BYTES = 5000
# Keep the configuration declaration usable by older installed readers too.
NATIVE_CONTEXT_LARGE_OUTPUT_BYTES = 64000
NATIVE_CONTEXT_HISTORY_TOKENS = 20000
NATIVE_CONTEXT_OUTPUT_ENV = "LOOPX_CONTEXT_PAGE_OUTPUT_BYTES"


def private_capture_supported() -> bool:
    """Whether this host has the POSIX identity needed for private capture IO."""
    return os.name == "posix" and callable(getattr(os, "getuid", None))


def native_context_page_budget() -> tuple[int, int]:
    return NATIVE_CONTEXT_PAGE_BYTES, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES
