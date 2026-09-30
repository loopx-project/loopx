"""A scan root that does not exist must not report a clean boundary.

`loopx check --scan-path <typo>` used to print `public boundary scan clean: 0 files`
and exit 0: the missing path contributed no files, so nothing distinguished a
typo from a genuinely clean tree. Reported paths stay readable and the scan
still covers the roots that do exist.
"""

from __future__ import annotations

import json
from pathlib import Path

from loopx.contract import check_contract, scan_public_boundary


def _registry(tmp_path: Path) -> Path:
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


def test_scan_reports_missing_scan_root(tmp_path: Path) -> None:
    absent = tmp_path / "does-not-exist"

    boundary = scan_public_boundary([absent])

    assert boundary["missing_scan_roots"] == [str(absent)]
    assert boundary["files"] == 0
    assert boundary["hits"] == []


def test_scan_still_covers_existing_root_beside_missing_root(tmp_path: Path) -> None:
    absent = tmp_path / "does-not-exist"
    leaky = tmp_path / "NOTES.md"
    leaky.write_text("internal host 10.0.0.7\n", encoding="utf-8")

    boundary = scan_public_boundary([absent, leaky])

    assert boundary["missing_scan_roots"] == [str(absent)]
    assert boundary["files"] == 1
    assert [hit.rsplit(": ", 1)[-1] for hit in boundary["hits"]] == ["private_ip"]


def test_check_contract_fails_closed_on_missing_scan_root(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)
    absent = tmp_path / "does-not-exist"

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=None,
        scan_roots=[absent],
        limit=5,
    )

    assert contract["ok"] is False
    assert contract["public_boundary_scan"]["missing_scan_roots"] == [str(absent)]
    assert contract["public_boundary_scan"]["ok"] is False
    missing_diagnostics = [
        diagnostic
        for diagnostic in contract["error_diagnostics"]
        if diagnostic["code"] == "public_boundary_scan_root_missing"
    ]
    assert [diagnostic["severity"] for diagnostic in missing_diagnostics] == ["error"]
    assert [diagnostic["scope"] for diagnostic in missing_diagnostics] == ["global"]
    assert [diagnostic["message"] for diagnostic in missing_diagnostics] == [
        f"scan root does not exist: {absent}"
    ]
    assert not [
        check
        for check in contract["checks"]
        if check.startswith("public boundary scan clean")
    ]


def test_check_contract_keeps_clean_report_for_existing_scan_root(
    tmp_path: Path,
) -> None:
    registry_path = _registry(tmp_path)
    public_file = tmp_path / "PUBLIC.md"
    public_file.write_text("# Public\n", encoding="utf-8")

    contract = check_contract(
        registry_path=registry_path,
        runtime_root_override=None,
        scan_roots=[public_file],
        limit=5,
    )

    assert contract["ok"] is True
    assert contract["public_boundary_scan"]["missing_scan_roots"] == []
    assert "public boundary scan clean: 1 files" in contract["checks"]
