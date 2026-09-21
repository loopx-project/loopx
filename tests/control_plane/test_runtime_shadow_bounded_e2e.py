"""Real public CLI and independent native TypeScript file-profile qualification.

The production writers, file provider and parsers run unchanged. Tests control
only process scheduling and deliberately edited source bytes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination.coordination_state_contract_generated import (
    TASK_LEASE_ACQUIRE_REQUEST_SCHEMA,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_runtime_shadow_source_snapshot,
)
from loopx.control_plane.work_items.task_lease_acquire_adapter import (
    task_lease_acquire_authority_facts,
)

REPO = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.stage2c_e2e


def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\ngoal_id: goal-a\nhandoff_mode: hard_lease\n---\n\n## Agent Todo\n\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "status": "active", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"],
            "runtime_shadow": {"schema_version": "loopx_coordination_runtime_shadow_config_v0", "enabled": False, "provider": "file_v0"}},
    }]}), encoding="utf-8")
    return registry, runtime, state


def cli(registry: Path, runtime: Path, *arguments: str, success: bool = True) -> dict:
    completed = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", *arguments], cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)}, capture_output=True, text=True,
        timeout=45, check=False)
    assert completed.stdout.strip(), completed.stderr
    payload = json.loads(completed.stdout)
    if success:
        assert completed.returncode == 0, completed.stderr + "\n" + json.dumps(payload, indent=2)
        assert payload.get("ok") is True, payload
    return payload


def enable(registry: Path) -> dict:
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"]["enabled"] = True
    registry.write_text(json.dumps(value), encoding="utf-8")
    return value["goals"][0]


def native(tmp_path: Path, module: str, function: str, request: dict) -> dict:
    path = tmp_path / "native-request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    script = (f"import {{ {function} }} from {json.dumps((REPO / module).as_uri())};"
        "import {readFile} from 'node:fs/promises';"
        f"process.stdout.write(JSON.stringify(await {function}(JSON.parse(await readFile(process.argv[1],'utf8')))));" )
    process = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script, str(path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=45, check=False)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def history(tmp_path: Path, runtime: Path) -> list[dict]:
    result = native(tmp_path, "loopx/control_plane/coordination/local_authority_shadow.ts", "readLocalAuthorityShadow", {
        "schema_version": "loopx_coordination_runtime_shadow_outbox_read_v0", "runtime_root": str(runtime),
        "goal_id": "goal-a", "scan_limit": 10000,
    })
    assert result["status"] == "loaded", result
    return result["proof"]["transactions"]


def acquire_native(tmp_path: Path, registry: Path, runtime: Path, todo_id: str) -> dict:
    return native(tmp_path, "loopx/control_plane/work_items/task_lease_acquire.ts", "executeTaskLeaseAcquire", {
        "schema_version": TASK_LEASE_ACQUIRE_REQUEST_SCHEMA, "runtime_root": str(runtime), "goal_id": "goal-a",
        "todo_id": todo_id, "owner": "agent-b", "idempotency_key": "native-" + todo_id, "ttl_seconds": 120,
        "write_scopes": [], "expected_version": None,
        "authority": task_lease_acquire_authority_facts(registry_path=registry, goal_id="goal-a", todo_id=todo_id),
        "runtime_shadow": {"schema_version": "loopx_coordination_runtime_shadow_binding_v0", "provider": "file_v0"},
    })


def test_source_snapshot_preserves_inventory_without_projecting_orphan_leases(
    tmp_path: Path,
) -> None:
    registry, runtime, state = workspace(tmp_path)
    directory = runtime / "goals" / "goal-a" / "task-leases"
    directory.mkdir(parents=True)
    # Imported baseline files use the snapshot contract's ASCII filename range.
    # Their order is independent of locale and filesystem enumeration order.
    for todo_id in ("todo_alpha", "todo_Zulu", "todo_Bravo"):
        (directory / f"{todo_id}.json").write_text(json.dumps({
            "schema_version": "task_lease_v0", "goal_id": "goal-a", "todo_id": todo_id,
            "owner": "agent-a", "status": "released", "version": 1,
            "write_scopes": ["src/"], "idempotency_key": f"baseline-{todo_id}",
        }), encoding="utf-8")
    goal = enable(registry)
    projection, snapshot = build_runtime_shadow_source_snapshot(
        goal=goal, runtime_root=runtime, state_path=state, registry_path=registry)
    expected = ["todo_Bravo", "todo_Zulu", "todo_alpha"]
    assert [entry["name"] for entry in snapshot["lease_inventory"]] == [f"{name}.json" for name in expected]
    assert projection["leases"] == []
    boot = cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
    assert boot["bootstrap"]["status"] == "applied", boot
    inspected = cli(registry, runtime, "coordination-shadow", "inspect", "--goal-id", "goal-a")
    assert inspected["inspection"]["status"] == "matched", inspected
    assert history(tmp_path, runtime)[0]["projection"]["leases"] == []


def test_public_cli_and_independent_native_writer_qualify_one_complete_lineage(tmp_path: Path) -> None:
    registry, runtime, state = workspace(tmp_path)
    cli(registry, runtime, "handoff-mode", "set", "--goal-id", "goal-a", "--mode", "soft_claim")
    archived = cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Previously completed task", "--claimed-by", "agent-a")
    cli(registry, runtime, "todo", "complete", "--goal-id", "goal-a", "--todo-id", archived["todo_id"],
        "--agent-id", "agent-a", "--evidence", "validation://completed", "--no-follow-up")
    cli(registry, runtime, "todo", "archive-completed", "--goal-id", "goal-a", "--max-active-done", "0", "--execute")
    cli(registry, runtime, "handoff-mode", "set", "--goal-id", "goal-a", "--mode", "hard_lease")
    first = cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Existing first task")
    second = cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Existing second task")
    baseline_lease = cli(registry, runtime, "task-lease", "acquire", "--goal-id", "goal-a", "--todo-id", first["todo_id"],
        "--owner", "agent-a", "--idempotency-key", "baseline-lease", "--ttl-seconds", "120")
    assert not (runtime / "authority-shadow" / "file-v0").exists()
    enable(registry)
    boot = cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
    assert boot["projection_summary"] == {"todo_count": 2, "lease_count": 1}
    assert boot["bootstrap"]["cursor"] == "1"
    native_request = {
        "schema_version": TASK_LEASE_ACQUIRE_REQUEST_SCHEMA, "runtime_root": str(runtime), "goal_id": "goal-a",
        "todo_id": second["todo_id"], "owner": "agent-b", "idempotency_key": "native-second", "ttl_seconds": 120,
        "write_scopes": [], "expected_version": None,
        "authority": task_lease_acquire_authority_facts(registry_path=registry, goal_id="goal-a", todo_id=second["todo_id"]),
        "runtime_shadow": {"schema_version": "loopx_coordination_runtime_shadow_binding_v0", "provider": "file_v0"},
    }
    acquired = native(tmp_path, "loopx/control_plane/work_items/task_lease_acquire.ts", "executeTaskLeaseAcquire", native_request)
    assert acquired["acquired"] is True, acquired
    additions = [cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", f"New captured task {index}") for index in range(2)]
    for addition in additions:
        assert addition["coordination_runtime_shadow"]["source_transaction_correlated"] is True
    inspect = cli(registry, runtime, "coordination-shadow", "inspect", "--goal-id", "goal-a")
    assert inspect["inspection"]["status"] == "matched", inspect
    qualified = cli(registry, runtime, "coordination-shadow", "qualify", "--goal-id", "goal-a", "--minimum-operations", "3",
        "--require-event-kind", "todo_add", "--require-event-kind", "task_lease_acquire")
    assert qualified["qualification"]["scope"] == "bounded"
    assert qualified["qualification"]["sustained_parity_verified"] is False
    assert qualified["qualification"]["sustained_parity_verdict"] == "not_evaluated"
    assert qualified["qualification"]["evidence"]["operation_count"] == 3
    assert qualified["qualification"]["cursor"] == "4"
    read = cli(registry, runtime, "coordination-shadow", "read-candidate", "--goal-id", "goal-a", "--todo-id", first["todo_id"])
    assert read["read_candidate"]["read_candidate_qualified"] is True
    assert read["read_candidate"]["decision_read_from_shadow"] is False
    proof = native(tmp_path, "loopx/control_plane/coordination/local_authority_shadow.ts", "readLocalAuthorityShadow", {
        "schema_version": "loopx_coordination_runtime_shadow_outbox_read_v0", "runtime_root": str(runtime),
        "goal_id": "goal-a", "scan_limit": 10000,
    })
    transactions = proof["proof"]["transactions"]
    assert len(transactions) == 4
    assert transactions[0]["receipts"] == []
    assert transactions[0]["projection"]["leases"] == [baseline_lease["lease"]]
    baseline_ids = [todo["todo_id"] for todo in transactions[0]["projection"]["todos"]]
    assert baseline_ids == sorted([first["todo_id"], second["todo_id"]])
    assert archived["todo_id"] not in baseline_ids
    assert "Previously completed task" in state.read_text()
    receipts = [transaction["receipts"][0] for transaction in transactions[1:]]
    assert {receipt["writer_runtime"] for receipt in receipts} == {"python", "typescript"}
    assert len({receipt["entry_id"] for receipt in receipts}) == 3
    assert all(receipt["capture_lineage_id"] == boot["bootstrap"]["capture_lineage_id"] for receipt in receipts)


def test_reviewed_promotion_survives_restart_and_enables_managed_codex_preflight(
    tmp_path: Path,
) -> None:
    registry, runtime, _state = workspace(tmp_path)
    registry_payload = json.loads(registry.read_text(encoding="utf-8"))
    registry_payload["goals"][0]["adapter"] = {
        "kind": "generic_project_goal_v0",
        "status": "connected",
    }
    registry_payload["goals"][0]["domain"] = "coordination-promotion"
    registry_payload["goals"][0]["quota"] = {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "allowed_slots": 10,
    }
    registry.write_text(
        json.dumps(registry_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    delegated = cli(
        registry,
        runtime,
        "todo",
        "add",
        "--goal-id",
        "goal-a",
        "--role",
        "agent",
        "--text",
        "Validate a managed worker only after reviewed promotion",
        "--task-class",
        "advancement_task",
        "--action-kind",
        "validate",
        "--claimed-by",
        "agent-b",
    )
    cli(
        registry,
        runtime,
        "task-lease",
        "acquire",
        "--goal-id",
        "goal-a",
        "--todo-id",
        delegated["todo_id"],
        "--owner",
        "agent-b",
        "--idempotency-key",
        "managed-worker-before-promotion",
        "--ttl-seconds",
        "600",
    )
    enable(registry)
    cli(
        registry,
        runtime,
        "coordination-shadow",
        "bootstrap",
        "--goal-id",
        "goal-a",
        "--execute",
    )
    cli(
        registry,
        runtime,
        "todo",
        "add",
        "--goal-id",
        "goal-a",
        "--role",
        "agent",
        "--text",
        "Capture one promotion qualification mutation",
        "--task-class",
        "advancement_task",
        "--action-kind",
        "capture",
        "--claimed-by",
        "agent-a",
    )
    common = (
        "coordination-shadow",
        "promote",
        "--goal-id",
        "goal-a",
        "--minimum-operations",
        "1",
        "--require-event-kind",
        "todo_add",
    )
    preview = cli(registry, runtime, *common)
    assert preview["promotion"]["status"] == "preview_ready"
    assert preview["promotion"]["promotion_ready"] is True
    assert not (runtime / "authority" / "file-v0").exists()
    assert not (runtime / ".local" / "manager-context" / "executions").exists()

    applied = cli(registry, runtime, *common, "--execute")
    assert applied["promotion"]["status"] == "applied"
    assert applied["promotion"]["legacy_writer_fenced"] is True

    # A fresh CLI process owns each call below. The first post-promotion
    # canonical mutation therefore proves that runtime-root identity and the
    # promoted authority survive process restart before any worker can launch.
    inspected = cli(registry, runtime, "goal-acceptance", "inspect", "--goal-id", "goal-a")
    document = tmp_path / "managed-acceptance.json"
    document.write_text(
        json.dumps(
            {
                "objective": "Validate managed worker acceptance after restart",
                "non_goals": ["Start the managed worker during inspection"],
                "criteria": [
                    {
                        "id": "ready",
                        "description": "The fixture validator succeeds",
                        "validation_argv": [sys.executable, "-c", "raise SystemExit(0)"],
                    }
                ],
                "bindings": [
                    {"todo_id": delegated["todo_id"], "criterion_ids": ["ready"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    configured = cli(
        registry,
        runtime,
        "goal-acceptance",
        "configure",
        "--goal-id",
        "goal-a",
        "--document",
        str(document),
        "--expected-provider-revision",
        inspected["provider_revision"],
        "--operation-id",
        "post-promotion-managed-acceptance",
        "--execute",
    )
    bound = next(
        item
        for item in configured["goal_acceptance_contract"]["tasks"]
        if item["todo_id"] == delegated["todo_id"]
    )
    assert bound == {
        "todo_id": delegated["todo_id"],
        "state": "ready",
        "criterion_ids": ["ready"],
        "reason": "The owner confirmed this work's current acceptance association.",
        "reason_code": "goal_acceptance_ready",
        "applicable": True,
    }

    worker = tmp_path / "managed-worker"
    worker.mkdir()
    config = tmp_path / "delegations.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "loopx_local_delegation_v0",
                "bindings": [
                    {
                        "id": "managed-sol",
                        "agent_id": "agent-b",
                        "todo_id": delegated["todo_id"],
                        "requesters": ["agent-a"],
                        "workspace": str(worker),
                        "timeout_seconds": 60,
                        "output_refs": ["output.json"],
                        "host_args": [
                            "--host",
                            "codex-cli",
                            "--codex-model",
                            "gpt-5.6-sol",
                            "--codex-reasoning-effort",
                            "xhigh",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    preflight = cli(
        registry,
        runtime,
        "delegation",
        "inspect",
        "--goal-id",
        "goal-a",
        "--agent-id",
        "agent-a",
        "--execution-config",
        str(config),
        "--binding-id",
        "managed-sol",
    )
    assert preflight["authority_state"] == "promoted"
    assert preflight["authority_ready"] is True
    assert preflight["acceptance_ready"] is True
    assert preflight["executor"]["profile"] == "gpt-5.6-sol@xhigh"
    assert preflight["state"] in {"launchable", "runtime_unverified"}
    assert not any(preflight["effects"].values())
    assert not (runtime / ".local" / "manager-context" / "executions").exists()
    assert not (runtime / "goals" / "goal-a" / "turns").exists()


def test_unrecorded_canonical_change_cannot_become_qualified_after_a_later_public_write(tmp_path: Path) -> None:
    registry, runtime, state = workspace(tmp_path)
    enable(registry)
    cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
    original = state.read_text()
    state.write_text(original.replace("handoff_mode: hard_lease", "handoff_mode: soft_claim"))
    added = cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "After unrecorded mutation")
    assert added["added"] is True
    qualified = cli(registry, runtime, "coordination-shadow", "qualify", "--goal-id", "goal-a", success=False)
    assert qualified["qualification"]["qualified"] is False
    assert list((runtime / "authority-shadow" / "outbox" / "goal-a" / "todos").glob("*.prepared.json"))
    state.write_text(original)
    again = cli(registry, runtime, "coordination-shadow", "qualify", "--goal-id", "goal-a", success=False)
    assert again["qualification"]["qualified"] is False


def test_snapshot_changed_between_python_builder_and_native_inspection_is_rejected(tmp_path: Path) -> None:
    registry, runtime, state = workspace(tmp_path)
    goal = enable(registry)
    cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
    projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime, state_path=state, registry_path=registry)
    state.write_text(state.read_text() + "\n## Notes\nProse changed after snapshot.\n")
    result = native(tmp_path, "loopx/control_plane/coordination/runtime_shadow.ts", "inspectCoordinationRuntimeShadow", {
        "schema_version": "loopx_coordination_runtime_shadow_inspect_v0", "runtime_root": str(runtime), "goal_id": "goal-a",
        "projection": projection, "source_snapshot": snapshot,
    })
    assert result["status"] == "failed"
    assert result["reason_code"] == "source_changed_retry"


def test_public_handoff_todo_and_monitor_successor_capture_each_primary_mutation(tmp_path: Path) -> None:
    registry, runtime, _state = workspace(tmp_path)
    enable(registry)
    cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
    cli(registry, runtime, "handoff-mode", "set", "--goal-id", "goal-a", "--mode", "soft_claim")
    assert len(history(tmp_path, runtime)) == 2
    cli(
        registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent",
        "--text", "Validate the retained projection", "--evidence", "validation://todo-add",
    )
    assert len(history(tmp_path, runtime)) == 3
    monitor = cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent",
        "--text", "Observe the public release", "--task-class", "continuous_monitor", "--action-kind", "monitor",
        "--claimed-by", "agent-a", "--target-key", "release:bounded", "--cadence", "30m",
        "--next-due-at", "2000-01-01T00:00:00+00:00", "--watch-only")
    assert len(history(tmp_path, runtime)) == 4
    result = cli(registry, runtime, "quota", "monitor-poll", "--goal-id", "goal-a", "--agent-id", "agent-a",
        "--runtime-profile", "generic_cli", "--available-capability", "network", "--todo-id", monitor["todo_id"],
        "--target-key", "release:bounded", "--result-hash", "release-v1", "--material-change",
        "--next-agent-todo", "Validate the released head", "--next-action-kind", "validate_release_head",
        "--next-task-repository", "git:github.com/huangruiteng/loopx", "--next-required-capability", "network",
        "--next-continuation-policy", "same_agent_non_delivery", "--next-claimed-by", "agent-a", "--execute")
    assert len(result["successor_todo_ids"]) == 1
    transactions = history(tmp_path, runtime)
    assert len(transactions) == 6  # Baseline, handoff, todo add, monitor add, observation update, successor add.
    receipts = [transaction["receipts"][0] for transaction in transactions[1:]]
    assert len({receipt["entry_id"] for receipt in receipts}) == 5
    assert [receipt["seq"] for receipt in receipts] == [1, 2, 3, 4, 5]
    assert {receipt["write_class"] for receipt in receipts} >= {"handoff_mode_set", "todo_add", "todo_update"}
    qualified = cli(registry, runtime, "coordination-shadow", "qualify", "--goal-id", "goal-a", "--minimum-operations", "5")
    assert qualified["qualification"]["qualified"] is True
    assert qualified["qualification"]["evidence"]["operation_count"] == 5


def test_disabling_configuration_cannot_cancel_an_active_capture_obligation(tmp_path: Path) -> None:
    registry, runtime, state = workspace(tmp_path)
    enable(registry)
    cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
    initial = state.read_bytes()
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["runtime_shadow"]["enabled"] = False
    registry.write_text(json.dumps(config))
    cli(registry, runtime, "handoff-mode", "set", "--goal-id", "goal-a", "--mode", "soft_claim")
    cli(registry, runtime, "handoff-mode", "set", "--goal-id", "goal-a", "--mode", "hard_lease")
    assert state.read_bytes() == initial
    enable(registry)
    # The identical final source must not erase the two intervening mutations.
    transactions = history(tmp_path, runtime)
    assert len(transactions) == 3
    assert [row["receipts"][0]["seq"] for row in transactions[1:]] == [1, 2]
    qualified = cli(registry, runtime, "coordination-shadow", "qualify", "--goal-id", "goal-a", "--minimum-operations", "2")
    assert qualified["qualification"]["qualified"] is True
    assert qualified["qualification"]["evidence"]["operation_count"] == 2


def test_native_lease_writer_cannot_reuse_a_sequence_when_its_cursor_is_missing(tmp_path: Path) -> None:
    registry, runtime, _state = workspace(tmp_path)
    first = cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "First lease task")
    second = cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Second lease task")
    enable(registry)
    cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
    cli(registry, runtime, "task-lease", "acquire", "--goal-id", "goal-a", "--todo-id", first["todo_id"],
        "--owner", "agent-a", "--idempotency-key", "first-lease", "--ttl-seconds", "120")
    assert len(history(tmp_path, runtime)) == 2
    cursor = runtime / "authority-shadow/outbox/goal-a/leases/drain-cursor.json"
    cursor.unlink()
    acquired = acquire_native(tmp_path, registry, runtime, second["todo_id"])
    assert acquired["acquired"] is True, acquired
    pending = list(cursor.parent.glob("*.prepared.json"))
    assert len(pending) == 1
    assert json.loads(pending[0].read_text())["seq"] == 2
    assert not cursor.exists()  # The writer recovers a sequence, never a cursor.
    cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Trigger bounded drain")
    transactions = history(tmp_path, runtime)
    lease_receipts = [row["receipts"][0] for row in transactions[1:] if row["receipts"][0]["partition"] == "leases"]
    assert [receipt["seq"] for receipt in lease_receipts] == [1, 2]
    assert len({receipt["entry_id"] for receipt in lease_receipts}) == 2
    assert cli(registry, runtime, "coordination-shadow", "qualify", "--goal-id", "goal-a")["qualification"]["qualified"] is True


def test_public_committed_primary_cannot_be_relabelled_abandoned_by_native_request(tmp_path: Path) -> None:
    from shadow_e2e_fixture import workspace as crash_workspace

    from loopx.control_plane.coordination import (
        local_authority_shadow_adapter as adapter,
    )
    from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox

    w = crash_workspace(tmp_path)
    w.crash("before_commit", "todo", "add", "--role", "agent", "--text", "A committed primary is never abandoned")
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    [entry] = outbox.list_entries(directory)
    request = adapter._commit_entry_request(runtime_root=w.runtime, goal_id=w.goal, entry=entry,
        resolution="abandoned", projection=None, digest=None)
    assert request["entry"]["committed_sha256"] is not None
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    primary = w.state.read_bytes()
    result = adapter.effect_runtime_result("coordination.runtime_shadow.commit_entry", request, timeout=15)
    assert result["outcome"] == "failed"
    assert result["reason_code"] == "outbox_resolution_marker_mismatch"
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert w.state.read_bytes() == primary
    assert w.drain()["ok"] is True
    view = adapter.read_local_authority_shadow(runtime_root=w.runtime, goal_id=w.goal, scan_limit=20)
    [transaction] = view["proof"]["transactions"][1:]
    assert transaction["receipts"][0]["resolution"] == "committed"
    assert transaction["receipts"][0]["no_op"] is False


def test_controller_cannot_bind_the_registered_source_to_an_override_runtime_root(tmp_path: Path) -> None:
    registry, runtime, state = workspace(tmp_path)
    enable(registry)
    original = state.read_bytes()
    override = tmp_path / "override-runtime"
    for registered_is_active in (False, True):
        if registered_is_active:
            cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
        rejected = cli(registry, override, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute", success=False)
        assert rejected["bootstrap"]["status"] == "failed"
        assert rejected["bootstrap"]["reason_code"] == "shadow_source_runtime_root_mismatch", rejected
        assert not (override / "authority-shadow/file-v0").exists()
        assert state.read_bytes() == original
        qualified = cli(registry, override, "coordination-shadow", "qualify", "--goal-id", "goal-a", success=False)
        assert qualified["qualification"]["qualified"] is False
        assert qualified["qualification"]["reason_code"] == "shadow_source_runtime_root_mismatch"


def test_controller_cannot_bind_an_alternate_state_file_before_or_after_bootstrap(tmp_path: Path) -> None:
    registry, runtime, state = workspace(tmp_path)
    enable(registry)
    alternate = tmp_path / "alternate-state.md"
    alternate.write_bytes(state.read_bytes())
    original = state.read_bytes()
    for active in (False, True):
        if active:
            cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a", "--execute")
        rejected = cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a",
            "--state-file", str(alternate), "--execute", success=False)
        assert rejected["bootstrap"]["status"] == "failed"
        expected = "management_operation_identity_mismatch" if active else "shadow_source_state_path_mismatch"
        assert rejected["bootstrap"]["reason_code"] == expected, rejected
        assert state.read_bytes() == alternate.read_bytes() == original
        qualified = cli(registry, runtime, "coordination-shadow", "qualify", "--goal-id", "goal-a",
            "--state-file", str(alternate), success=False)
        assert qualified["qualification"]["qualified"] is False
        assert qualified["qualification"]["reason_code"] == "shadow_source_state_path_mismatch"
        if not active:
            assert not (runtime / "authority-shadow/file-v0").exists()
