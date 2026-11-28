"""Internal host activation projections and adapters."""

from __future__ import annotations

from importlib import resources


def read_host_asset(package: str | None, filename: str) -> str:
    """Load one packaged text asset relative to a host adapter subpackage."""
    if not package:
        raise RuntimeError(f"host asset package is unavailable for {filename}")
    return resources.files(package).joinpath(filename).read_text(encoding="utf-8")
