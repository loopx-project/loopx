from __future__ import annotations

import argparse

from ..thread_agent_binding import HOST_THREAD_ID_ENV, ambient_host_thread_id

__all__ = ["HOST_THREAD_ID_ENV", "ambient_host_thread_id", "current_host_thread_id"]


def current_host_thread_id(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "thread_id", None)
    if explicit:
        return str(explicit)
    return ambient_host_thread_id(str(getattr(args, "host_surface", None) or ""))
