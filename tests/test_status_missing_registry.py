"""A missing registry is an empty workspace only where the caller says so."""

from __future__ import annotations

from pathlib import Path

from loopx.status import collect_status


def _collect(tmp_path: Path, **overrides):
    return collect_status(
        registry_path=tmp_path / ".loopx" / "registry.json",
        runtime_root_override=str(tmp_path / "runtime"),
        scan_roots=[],
        limit=5,
        include_public_boundary_scan=False,
        **overrides,
    )


def test_missing_registry_is_a_contract_error_by_default(tmp_path: Path) -> None:
    payload = _collect(tmp_path)
    assert payload["ok"] is False
    assert payload["goal_count"] == 0
    assert "registry file does not exist" in payload["contract"]["errors"]


def test_missing_registry_is_a_warning_when_the_caller_allows_it(tmp_path: Path) -> None:
    payload = _collect(tmp_path, allow_missing_registry=True)
    assert payload["ok"] is True, payload["contract"]
    assert payload["contract"]["ok"] is True
    assert payload["contract"]["errors"] == []
    assert any(line.startswith("registry file does not exist") for line in payload["contract"]["warnings"])
    assert payload["goal_count"] == 0
    assert payload["run_history"]["goals"] == []
