"""Real local CLI: reread only the decision after a relevant source changes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, REPO_ROOT, _run_cli, _write_fixture, _spend_run_count,
)


def _missing(root: Path):
    project, runtime, registry = _write_fixture(root)
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID)
    for args in (
        ("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--vision-summary", "Validate the scoped change.", "--vision-acceptance", "Focused checks pass.",
         "--no-global-sync", "--suppress-external-sinks"),
        ("quota", "should-run", "--codex-app", *binding, "--scan-path", str(project)),
    ):
        rc, result = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, result
    delivery = ("refresh-state", *binding, "--classification", "validated_change",
                "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
                "--no-global-sync", "--suppress-external-sinks")
    rc, original = _run_cli(registry, runtime, *delivery, cwd=project)
    assert rc == 0 and original["vision_checkpoint"]["decision"] == "missing_required", original
    return project, runtime, registry, binding, delivery, original


def test_missing_replaced_stale_context_requires_reread_and_preserves_delivery(tmp_path):
    project, runtime, registry, binding, delivery, original = _missing(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    index = runtime / f"goals/{GOAL_ID}/runs/index.jsonl"
    before = index.read_bytes()
    original_bytes = Path(original["json_path"]).read_bytes()
    vision = ("--vision-unchanged-reason", "The current scope and acceptance still apply.")
    rc, missing = _run_cli(registry, runtime, *delivery, *vision, cwd=project)
    assert rc == 1 and missing["error_code"] == "checkpoint_read_context_required", missing
    rc, a = _run_cli(registry, runtime, "checkpoint-context", *binding, cwd=project)
    assert rc == 0, a
    rc, b = _run_cli(registry, runtime, "checkpoint-context", *binding, cwd=project)
    assert rc == 0 and b["read_context_id"] != a["read_context_id"], b
    rc, replaced = _run_cli(registry, runtime, *delivery, *vision,
        "--checkpoint-read-context", a["read_context_id"], cwd=project)
    assert rc == 1 and replaced["error_code"] == "checkpoint_read_context_unknown_or_replaced", replaced

    # A synthetic source edit represents a peer's completed update. The separate
    # concurrency test proves the cooperating writer cannot cross the commit guard.
    state.write_text(state.read_text(encoding="utf-8").replace(
        "Validate and settle the selected delivery.", "Validate the updated delivery scope."), encoding="utf-8")
    rc, stale = _run_cli(registry, runtime, *delivery, *vision,
        "--checkpoint-read-context", b["read_context_id"], cwd=project)
    assert rc == 1 and stale["error_code"] == "checkpoint_read_context_stale", stale
    assert "todo" in stale["checkpoint_read_context"]["changed_components"]
    assert index.read_bytes() == before
    assert Path(original["json_path"]).read_bytes() == original_bytes
    assert _spend_run_count(runtime) == 0

    rc, fresh = _run_cli(registry, runtime, "checkpoint-context", *binding, cwd=project)
    assert rc == 0 and "updated delivery scope" in fresh["basis"]["todo"]["text"], fresh
    supplement = (*delivery, *vision, "--checkpoint-read-context", fresh["read_context_id"])
    rc, result = _run_cli(registry, runtime, *supplement, cwd=project)
    assert rc == 0 and result["appended"] and result["vision_checkpoint"]["satisfied"], result
    assert result["vision_checkpoint"]["read_context"]["read_context_id"] == fresh["read_context_id"]
    after = index.read_bytes()
    state.write_text(state.read_text(encoding="utf-8") + "\n## Acceptance\n\nNew acceptance after commit.\n", encoding="utf-8")
    rc, replay = _run_cli(registry, runtime, *supplement, cwd=project)
    assert rc == 0 and replay["idempotent_replay"] and not replay["appended"], replay
    assert index.read_bytes() == after
    assert Path(original["json_path"]).read_bytes() == original_bytes
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_large_goal_context_keeps_complete_basis_and_checkpoint_only_recovery(
    tmp_path, monkeypatch, provider,
):
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.effect_runtime import MAX_REQUEST_BYTES

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, binding, delivery, original = _missing(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    if provider != "legacy":
        todo = {"schema_version": "todo_item_v0", "todo_id": TODO_ID, "index": 1,
                "role": "agent", "status": "open", "done": False,
                "text": "Validate and settle the selected delivery.",
                "task_class": "advancement_task", "archive_state": "active",
                "source_section": "Agent Todo"}
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=[todo])
        initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    # The original failure came from complete source prose, not from the
    # small prior Turn receipt. Keep the source intact and cross both 2 MiB
    # request and response boundaries through the real local CLI/runtime.
    large_prose = "This is retained Goal context.\n" * 115_000
    assert len(large_prose.encode()) > MAX_REQUEST_BYTES
    state.write_text(state.read_text(encoding="utf-8") + "\n## Context\n\n" + large_prose,
                     encoding="utf-8")
    rc, context = _run_cli(registry, runtime, "checkpoint-context", *binding, cwd=project)
    assert rc == 0, {key: value for key, value in context.items() if key != "basis"}
    assert context["basis"]["goal"]["prose"].endswith(large_prose.strip())
    assert context["basis"]["source"]["authority"] == (
        "legacy_markdown" if provider == "legacy" else f"{provider}_v0")
    state.write_text(state.read_text(encoding="utf-8") + "\nUpdated acceptance context.\n",
                     encoding="utf-8")
    supplement = (*delivery,
        "--vision-unchanged-reason", "The current scope and acceptance still apply.")
    rc, stale = _run_cli(registry, runtime, *supplement,
        "--checkpoint-read-context", context["read_context_id"], cwd=project)
    assert rc == 1 and stale["error_code"] == "checkpoint_read_context_stale", stale
    assert "goal" in stale["checkpoint_read_context"]["changed_components"]
    rc, context = _run_cli(registry, runtime, "checkpoint-context", *binding, cwd=project)
    assert rc == 0 and context["basis"]["goal"]["prose"].endswith(
        "Updated acceptance context."), context.get("error")
    rc, result = _run_cli(registry, runtime, *supplement,
        "--checkpoint-read-context", context["read_context_id"], cwd=project)
    assert rc == 0 and result["vision_checkpoint"]["satisfied"], result
    assert result["settlement_identity"] == original["settlement_identity"]
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_context_reads_real_canonical_todo_and_owner_acceptance(tmp_path, monkeypatch, provider):
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
    from loopx.control_plane.goals.checkpoint_context_io import _source_facts, _source_guard
    from loopx.control_plane.quota.settlement import SettlementIdentity
    import hashlib

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
        import tempfile
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    todo = {"schema_version": "todo_item_v0", "todo_id": TODO_ID, "index": 1,
            "role": "agent", "status": "done", "done": True, "text": "Canonical delivered result",
            "task_class": "advancement_task", "archive_state": "active", "source_section": "Agent Todo"}
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=[todo])
    document = {"objective": "Canonical owner objective", "non_goals": [], "bindings": [],
        "criteria": [{"id": "renders", "description": "The page renders", "validation_argv": ["true"],
                      "validation_timeout_seconds": 5, "validation_files": []}]}
    projection["goal_acceptance"] = {"schema_version": "loopx_goal_acceptance_v0", "enabled": True,
        "revision": 1, "digest": hashlib.sha256(canonical_bytes(document)).hexdigest(),
        "document": document, "bindings": [], "verification": None}
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    with _source_guard(runtime, GOAL_ID, state):
        facts = _source_facts(runtime, registry, state, identity)
    assert facts["todos"][0]["text"] == "Canonical delivered result"
    assert facts["acceptance"]["contract"]["objective"] == "Canonical owner objective"
    assert facts["source"]["authority"] == f"{provider}_v0"


def test_source_writers_remain_excluded_until_checkpoint_append(tmp_path, monkeypatch):
    from loopx import state_refresh
    from loopx.control_plane.coordination.shadow_management import shadow_maintenance_lock_target
    from loopx.control_plane.goals.checkpoint_context_io import read_checkpoint_context

    project, runtime, registry, _, _, original = _missing(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    context = read_checkpoint_context(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
    # Two independent processes exercise the same mutexes used by the actual
    # canonical acceptance writer and legacy state writer, while refresh is at
    # its real persistence seam (after validation and immediately before append).
    script = """
from pathlib import Path
import sys
from loopx.file_lock import exclusive_cross_runtime_file_lock, LockAcquireTimeoutError
try:
    with exclusive_cross_runtime_file_lock(Path(sys.argv[1]), timeout_seconds=0):
        print('acquired')
except LockAcquireTimeoutError:
    print('held')
"""

    control = REPO_ROOT / "loopx/control_plane"
    node_script = (
        f"import {{withFileMutationLock}} from {json.dumps((control / 'effect_runtime_io.ts').as_uri())};"
        f"import {{EffectRuntimeLockTimeoutError}} from {json.dumps((control / 'effect_runtime_errors.ts').as_uri())};"
        f"import {{shadowMaintenanceLockPath}} from {json.dumps((control / 'coordination/shadow_management.ts').as_uri())};"
        "try {await withFileMutationLock(shadowMaintenanceLockPath(process.argv[1], process.argv[2]),"
        "async()=>process.stdout.write('acquired'),0);} catch(error) {"
        "if(!(error instanceof EffectRuntimeLockTimeoutError))throw error;process.stdout.write('held');}"
    )

    def probe(target):
        command = ([sys.executable, "-c", script, str(target)] if target == state else
                   ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module",
                    "-e", node_script, str(runtime), GOAL_ID])
        result = subprocess.run(command,
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    targets = (state, shadow_maintenance_lock_target(runtime, GOAL_ID))
    reserve = state_refresh.reserve_unique_run_paths
    inspected = []

    def before_append(*args, **kwargs):
        for target in targets:
            assert probe(target) == "held"
        inspected.append(True)
        return reserve(*args, **kwargs)

    monkeypatch.setattr(state_refresh, "reserve_unique_run_paths", before_append)
    result = state_refresh.refresh_state_run(registry_path=registry, runtime_root_override=str(runtime),
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID,
        project=None, state_file=None, classification="validated_change", recommended_action=None,
        delivery_batch_scale="implementation", delivery_outcome="outcome_progress",
        vision_unchanged_reason="The current basis remains applicable.",
        checkpoint_read_context_id=context["read_context_id"], dry_run=False, sync_global=False,
        external_delivery={"suppress": True, "resume_key": None})
    assert result["appended"] and result["vision_checkpoint"]["satisfied"]
    assert inspected == [True]
    for target in targets:
        assert probe(target) == "acquired"
    assert result["settlement_identity"] == original["settlement_identity"]
    assert _spend_run_count(runtime) == 0
