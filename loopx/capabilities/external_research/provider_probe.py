"""Automatic local external-evidence method discovery and readiness probes.

Deterministic local checks only: probes never grant owner-held permissions or
fabricated execution receipts, and honor network-disabled sandbox environments.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import shutil
import ssl
from typing import Any

from ...control_plane.agents.capability_probe import (
    host_network_sandbox_disabled,
    probe_github_cli_evidence_ready,
)
from ...extensions import public_github_research


def probe_ego_browser_ready(
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, bool, str | None]:
    """Return `(installed, ready, unavailable_reason)` for local Ego browser evidence."""
    environment = os.environ if env is None else env
    path_env = environment.get("PATH")
    home_raw = str(environment.get("HOME") or "").strip()
    home = Path(home_raw).expanduser() if home_raw else Path.home()
    ego_cli = shutil.which("ego-browser", path=path_env)
    if not ego_cli:
        candidate = home / ".local" / "bin" / "ego-browser"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            ego_cli = str(candidate)
    app_installed = (
        Path("/Applications/Ego Lite.app").exists()
        or (home / "Applications" / "Ego Lite.app").exists()
    )
    installed = bool(ego_cli or app_installed)
    if not installed:
        return False, False, "ego_browser_not_installed"
    if host_network_sandbox_disabled(environment):
        return True, False, "sandbox_network_disabled"
    if not ego_cli:
        return True, False, "ego_browser_cli_not_on_path"
    return True, True, None


def _all_pinned_public_github_sources(source_refs: Sequence[str]) -> bool:
    if not source_refs or len(source_refs) > 8:
        return False
    try:
        for ref in source_refs:
            public_github_research._source(str(ref))
    except ValueError:
        return False
    return True


def probe_local_evidence_providers(
    *,
    source_refs: Sequence[str] = (),
    for_plan: bool = False,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Auto-detect bundled and host-local external-evidence methods."""
    environment = os.environ if env is None else env
    sandbox_disabled = host_network_sandbox_disabled(environment)
    tls_available = ssl.OPENSSL_VERSION is not None

    providers: list[dict[str, Any]] = []

    if for_plan and _all_pinned_public_github_sources(source_refs):
        if sandbox_disabled:
            providers.append(
                {
                    "provider_id": public_github_research.PROVIDER_ID,
                    "provider_kind": "method",
                    "protocol": "external_evidence_research_v0",
                    "declared": True,
                    "installed": True,
                    "enabled": True,
                    "ready": False,
                    "unavailable_reason": "sandbox_network_disabled",
                }
            )
        else:
            providers.append(public_github_research.inspect_provider(list(source_refs)))
    else:
        if sandbox_disabled:
            gh_method_ready = False
            gh_method_reason: str | None = "sandbox_network_disabled"
        elif not tls_available:
            gh_method_ready = False
            gh_method_reason = "tls_runtime_unavailable"
        elif for_plan:
            gh_method_ready = False
            gh_method_reason = "pinned_github_source_refs_required"
        else:
            gh_method_ready = True
            gh_method_reason = None
        providers.append(
            {
                "provider_id": public_github_research.PROVIDER_ID,
                "provider_kind": "method",
                "protocol": "external_evidence_research_v0",
                "declared": True,
                "installed": True,
                "enabled": True,
                "ready": gh_method_ready,
                "unavailable_reason": gh_method_reason,
            }
        )

    static_ready = bool(tls_available and not sandbox_disabled)
    static_reason = (
        "sandbox_network_disabled"
        if sandbox_disabled
        else (None if tls_available else "tls_runtime_unavailable")
    )
    providers.append(
        {
            "provider_id": "method:public-source-reader",
            "provider_kind": "method",
            "protocol": "external_evidence_research_v0",
            "declared": True,
            "installed": True,
            "enabled": True,
            "ready": static_ready,
            "unavailable_reason": static_reason,
        }
    )

    gh_installed = bool(shutil.which("gh", path=environment.get("PATH")))
    gh_ready, gh_reason = probe_github_cli_evidence_ready(env=environment)
    providers.append(
        {
            "provider_id": "method:gh-cli",
            "provider_kind": "method",
            "protocol": "external_evidence_research_v0",
            "declared": True,
            "installed": gh_installed,
            "enabled": gh_installed,
            "ready": gh_ready,
            "unavailable_reason": None
            if gh_ready
            else (gh_reason or "gh_cli_unavailable"),
        }
    )

    ego_installed, ego_ready, ego_reason = probe_ego_browser_ready(env=environment)
    providers.append(
        {
            "provider_id": "method:ego-browser",
            "provider_kind": "method",
            "protocol": "external_evidence_research_v0",
            "declared": True,
            "installed": ego_installed,
            "enabled": ego_installed,
            "ready": ego_ready,
            "unavailable_reason": None
            if ego_ready
            else (ego_reason or "ego_browser_unavailable"),
        }
    )

    return providers
