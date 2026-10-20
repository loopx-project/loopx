"""Read-only planning cannot create launch locks or bypass current authority."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
    FirstPartyHostRuntimeRejected,
    capture_first_party_host_goal_ref,
)
from tests.control_plane.test_first_party_host_runtime import INSTANCE_A, _write_source_registry
from tests.test_loopx_turn_driver import _write_live_fixture


def _snapshot(root: Path) -> dict[str, bytes | None]:
    # Retain every path, including empty directories and registry lock files.
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def _legacy_registry(root: Path) -> Path:
    registry = root / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": "0.2", "goals": [{"id": "release", "status": "active"}],
    }) + "\n", encoding="utf-8")
    return registry


def test_read_only_plan_detects_a_source_profile_published_after_capture(tmp_path: Path) -> None:
    registry = _legacy_registry(tmp_path)
    captured = capture_first_party_host_goal_ref(registry_path=registry, goal_id="release")
    assert captured is None
    # Publish a valid source profile between the CLI's two authority reads.
    # The existing source owner constructs the replacement before publication.
    replacement = tmp_path / "replacement.json"
    _write_source_registry(replacement, INSTANCE_A)
    replacement.replace(registry)
    before = _snapshot(tmp_path)

    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry, goal_id="release", planned_goal_ref=captured,
        capture_launch_authority=False,
    )

    assert admission.enabled is True
    assert admission.planned_goal_ref is None
    assert _snapshot(tmp_path) == before
    with pytest.raises(FirstPartyHostRuntimeRejected) as rejected:
        admission.require_current()
    assert rejected.value.code == "goal_instance_id_missing"


def test_read_only_legacy_admission_cannot_grant_host_launch(tmp_path: Path) -> None:
    registry = _legacy_registry(tmp_path)
    before = _snapshot(tmp_path)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry, goal_id="release", planned_goal_ref=None,
        capture_launch_authority=False,
    )
    assert admission.enabled is False
    assert admission.legacy_registry_witness is None
    assert _snapshot(tmp_path) == before

    with pytest.raises(FirstPartyHostRuntimeRejected) as rejected:
        with admission.launch_lifetime(operation="read_only_plan_cannot_launch"):
            pytest.fail("a read-only plan without its original witness cannot launch a Host")
    assert rejected.value.code == "goal_authority_unavailable"


def test_run_once_preview_preserves_every_fixture_path_and_file(tmp_path: Path) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    before = _snapshot(tmp_path)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main([
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--host", "generic-cli", "--goal-id", "loopx-turn-fixture",
            "--agent-id", "codex-fixture", "--project", str(project), "--scan-root", str(project),
            "--host-command-json", json.dumps([
                sys.executable, "-c", "raise AssertionError('preview invoked a Host')",
            ]),
            "--validation-command-json", json.dumps([
                sys.executable, "-c", "raise AssertionError('preview ran validation')",
            ]),
        ])

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["effects"]["state_written"] is False
    assert payload["effects"]["host_invoked"] is False
    assert _snapshot(tmp_path) == before
