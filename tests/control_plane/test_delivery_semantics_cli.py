"""Exercise delivery authoring and readback through a disposable real CLI store."""

import json
import os
from pathlib import Path
import subprocess
import sys

from canonical_authority_fixture import initialize_canonical_source_fixture

from loopx.control_plane.work_items.work_lane_context import outcome_followthrough_hint
from loopx.status import compact_post_handoff_run


ROOT = Path(__file__).resolve().parents[2]


def build_outcome_followthrough_hint(run):
    return outcome_followthrough_hint({"handoff_readiness": {"post_handoff_latest_run": run}})


def test_refresh_history_preserves_labels_without_inventing_delivery(tmp_path: Path) -> None:
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "# Active Goal State\n\n## Next Action\n\n- Validate the network parser.\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    registry.write_text(json.dumps({
        "schema_version": "0.1",
        "common_runtime_root": str(runtime),
        "goals": [{
            "id": "delivery-semantics",
            "status": "active-read-only",
            "repo": str(tmp_path),
            "state_file": state.name,
            "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
            "authority_sources": [],
        }],
    }), encoding="utf-8")
    initialize_canonical_source_fixture(runtime, "delivery-semantics", state_path=state, provider="sqlite")

    def cli(*args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", *args],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    for label, outcome in (
        ("unblocked after dependency update", None),
        ("implemented network protocol parser", None),
        ("implemented network protocol parser", "surface_only"),
        ("unblocked after dependency update", "outcome_progress"),
    ):
        args = ["refresh-state", "--goal-id", "delivery-semantics",
                "--classification", label, "--no-global-sync"]
        if outcome:
            args.extend(["--delivery-outcome", outcome, "--delivery-batch-scale", "implementation"])
        written = cli(*args)
        assert written["appended"] is True
        history = cli("history", "--goal-id", "delivery-semantics", "--limit", "1")
        runs = history["runs"]
        assert len(runs) == 1
        run = runs[0]
        assert run["classification"] == label
        compact = compact_post_handoff_run(run)
        if outcome is None:
            assert compact["delivery_turn_kind"] == "unknown"
            assert compact["delivery_batch_scale"] == "unknown"
            assert build_outcome_followthrough_hint(compact) is None
        elif outcome == "surface_only":
            assert build_outcome_followthrough_hint(compact)["required"] is True
        else:
            assert compact["delivery_turn_kind"] == "compact_evidence"
            assert build_outcome_followthrough_hint(compact) is None

    # The real authoring entrypoint rejects a contradictory claim before any
    # state/history changes, including in dry-run mode.
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    for mode in ([], ["--dry-run"]):
        rejected = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", "refresh-state",
             "--goal-id", "delivery-semantics", "--classification", "typed claim",
             "--delivery-outcome", "primary_goal_outcome", "--todo-id", "todo_delivery",
             "--progress-result-class", "blocked", "--progress-blocker-id", "blocker-a",
             "--progress-evidence-id", "evidence-a", "--no-global-sync", *mode],
            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert rejected.returncode != 0
        assert "primary_outcome_with_blocker" in rejected.stdout + rejected.stderr
        assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
