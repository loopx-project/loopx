from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.content_ops.reference import (
    content_reference_operation, inspect_reference_materials, write_reference_artifact,
)
from loopx.configuration_catalog import build_goal_configuration_catalog
from loopx.capabilities.configuration_inspection import project_goal_configuration


def test_real_runtime_inventory_preserves_unknown_and_bytes(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"entries": [{"id": "demo", "title": "Demo", "author": "Example",
        "source_url": "https://example.org/demo", "captured_at": "2026-09-01T10:00:00Z", "card": "cards/demo.md"}]}))
    before = catalog.read_bytes()
    result = inspect_reference_materials(library_path=catalog, goal_id="fixture", store_id="fixture-library", observed_at="2026-09-01T11:00:00Z")
    assert result["source_item_count"] == 1
    assert result["legacy_unknown_lifecycle_count"] == 1
    assert result["inventory"]["item_count"] == 0
    assert result["inventory"]["backup_verified"] is False
    assert result["apply_available"] is False
    assert catalog.read_bytes() == before
    with pytest.raises(FileExistsError):
        write_reference_artifact(catalog, result)
    artifact = tmp_path / "inspection.private.json"
    write_reference_artifact(artifact, result)
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert json.loads(artifact.read_text()) == result


def test_failure_is_actionable_and_discovery_does_not_create_enablement() -> None:
    with pytest.raises(ValueError, match="library.entries"):
        content_reference_operation("search", {"library": {}})
    catalog = build_goal_configuration_catalog(goal_id="fixture", settings={}, feature_summary={},
        default_multi_subagent_max_children=2, explore_harness_profiles=())
    feature = next(value for value in catalog["capability_catalog"]["capabilities"] if value["capability_id"] == "content_ops")
    assert feature["configuration_editor"]["editable"] is False
    assert feature["effective_configuration"]["source"] == "not_configured"
    native = next(value for value in catalog["features"] if value["feature_id"] == "content_ops")
    assert "current" not in native and "default" not in native
    merged = project_goal_configuration({"goal_id": "fixture", "configuration_catalog": catalog}, machine_namespaces=[])
    assert "content_ops" in merged["available_capabilities"]


def test_real_cli_exit_and_private_failure(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"entries": []}')
    command = [sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())", "--format", "json", "content-ops", "reference", "search", "--library-json", str(catalog)]
    success = subprocess.run(command, capture_output=True, text=True, check=False)
    assert success.returncode == 0 and json.loads(success.stdout)["ok"] is True
    catalog.write_text('{}')
    failure = subprocess.run(command, capture_output=True, text=True, check=False)
    assert failure.returncode == 1 and json.loads(failure.stdout)["ok"] is False


def test_real_cli_correction_preserves_nested_backing(tmp_path: Path) -> None:
    reference = {"id": "demo", "title": "Demo", "author": "Example", "source_url": "https://example.org/demo",
        "source_revision": "read:2", "captured_at": "2026-09-01T10:00:00Z",
        "style": {"opening": "New opening", "tone": "New tone"}, "structure": ["result"],
        "caveats": ["Synthetic fixture"], "reuse_boundary": "Structure only", "reading_boundary": "Metadata only"}
    old = {**reference, "source_revision": "read:1", "style": {"opening": "Old", "tone": "Old", "legacy_pattern": {"retain": ["sentinel"]}}}
    catalog, correction, output = (tmp_path / name for name in ("catalog.json", "correction.json", "output.private.json"))
    catalog.write_text(json.dumps({"entries": [old]}))
    before = catalog.read_bytes()
    correction.write_text(json.dumps({"reference": reference, "expected_source_revision": "read:1"}))
    command = [sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())", "--format", "json",
        "content-ops", "reference", "capture", "--library-json", str(catalog), "--input-json", str(correction), "--output-json", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    style = json.loads(output.read_text())["library"]["entries"][0]["style"]
    assert style == {**old["style"], **reference["style"]}
    assert catalog.read_bytes() == before


def test_real_cli_metrics_backing_and_blank_filter(tmp_path: Path) -> None:
    reference = {"id": "demo", "title": "Demo", "author": "Example", "source_url": "https://example.org/demo",
        "source_revision": "read:2", "captured_at": "2026-09-01T10:00:00Z",
        "style": {"opening": "Opening", "tone": "Tone"}, "structure": ["result"],
        "caveats": ["Synthetic fixture"], "reuse_boundary": "Structure only", "reading_boundary": "Metadata only",
        "metrics": {"observed_at": "2026-09-01T10:00:00Z", "counts": {"views": 120}}}
    old = {**reference, "source_revision": "read:1", "metrics": {**reference["metrics"], "counts": {"views": 100, "likes": 9}, "source_evidence": {"ref": "evidence:original"}}}
    catalog, correction, output = (tmp_path / name for name in ("catalog.json", "correction.json", "output.private.json"))
    catalog.write_text(json.dumps({"entries": [old]}))
    before = catalog.read_bytes()
    correction.write_text(json.dumps({"reference": reference, "expected_source_revision": "read:1"}))
    base = [sys.executable, "-c", "from loopx.entrypoint import main; raise SystemExit(main())", "--format", "json", "content-ops", "reference"]
    result = subprocess.run(base + ["capture", "--library-json", str(catalog), "--input-json", str(correction), "--output-json", str(output)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(output.read_text())["library"]["entries"][0]["metrics"] == {**old["metrics"], **reference["metrics"]}
    assert catalog.read_bytes() == before
    search = subprocess.run(base + ["search", "--library-json", str(catalog), "--query", " ", "--structure", "\t "], capture_output=True, text=True, check=False)
    assert search.returncode == 0, search.stdout + search.stderr
    assert len(json.loads(search.stdout)["references"]) == 1
