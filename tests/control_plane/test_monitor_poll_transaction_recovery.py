"""Monitor recovery preserves original receipts without admitting new work."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import (
    CanonicalAuthorityRequired,
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    canonical_bytes,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.coordination.local_authority_shadow_history import outbox_root
from loopx.control_plane.scheduler.monitor_poll_writeback import (
    write_monitor_poll_todo_state,
)
from loopx.control_plane.testing.canary_harness import read_run_index, write_fixture_registry
from monitor_poll_canonical_fixture import _cli, _poll_events


GOAL = "synthetic-monitor-recovery"
AGENT = "codex-quality-qualification"
OPERATION = "historical-monitor-effect"
MONITOR_ID = "todo_historical_monitor"
AGENT_SUCCESSOR = "todo_historical_agent"
USER_SUCCESSOR = "todo_historical_user"
AGENT_TASK = "Validate observed synthetic head"
USER_TASK = "Review observed synthetic head"


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = project / ".codex" / "goals" / GOAL / "ACTIVE_GOAL_STATE.md"
    registry = project / ".loopx" / "registry.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\n---\n\n# Active Goal State\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL,
        domain="loopx-platform",
        adapter_kind="harness_self_improvement",
        registered_agents=[AGENT],
        quota_allowed_slots=None,
    )
    return registry, runtime, state


def _request() -> tuple[dict, dict]:
    observation = {
        "todo_id": MONITOR_ID,
        "target_key": "synthetic-target",
        "result_hash": "synthetic-head-1",
        "material_change": True,
        "generated_at": "2026-09-01T00:00:00Z",
        "cadence": None,
        "next_due_at": None,
        "reason_summary": None,
    }
    intent = {
        "next_agent_todo": AGENT_TASK,
        "next_action_kind": "validate",
        "next_task_repository": None,
        "next_required_capabilities": [],
        "next_continuation_policy": None,
        "next_target_key": None,
        "next_claimed_by": None,
        "next_user_todo": USER_TASK,
        "next_user_task_class": "user_action",
    }
    return observation, intent


def _historical_receipt() -> dict:
    observation, intent = _request()
    request_sha256 = hashlib.sha256(
        canonical_bytes(
            {
                "goal_id": GOAL,
                "observation": observation,
                "intent": intent,
                "actor_agent_id": AGENT,
                "dry_run": False,
            }
        )
    ).hexdigest()
    next_todos = [
        {"todo_id": AGENT_SUCCESSOR, "role": "agent", "todo": AGENT_TASK},
        {"todo_id": USER_SUCCESSOR, "role": "user", "todo": USER_TASK},
    ]
    successor_receipts = [
        {"todo_id": AGENT_SUCCESSOR, "role": "agent"},
        {"todo_id": USER_SUCCESSOR, "role": "user"},
    ]
    return {
        "schema_version": "loopx_monitor_batch_receipt_v0",
        "goal_id": GOAL,
        "operation_id": OPERATION,
        "request_sha256": request_sha256,
        "writeback": {
            "schema_version": "monitor_poll_todo_writeback_v0",
            "dry_run": False,
            "goal_id": GOAL,
            "todo_id": MONITOR_ID,
            "monitor_effect_id": OPERATION,
            "result_hash": observation["result_hash"],
            "material_change": True,
            "last_checked_at": observation["generated_at"],
            "todo_update": {"ok": True},
            "next_todos": next_todos,
            "successor_receipts": successor_receipts,
            "provider_replayed": False,
        },
    }


def _seed_receipt(state: Path, receipt: object) -> None:
    encoded = base64.b64encode(
        json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    digest = hashlib.sha256(OPERATION.encode("utf-8")).hexdigest()
    with state.open("a", encoding="utf-8") as handle:
        handle.write(f"\n<!-- loopx:monitor-batch:{digest} {encoded} -->\n")


def _promote(runtime: Path, state: Path, provider: str = "file") -> None:
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL,
        todos=[],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime,
        GOAL,
        projection,
        state_path=state,
        provider=provider,
    )


def _replay(registry: Path, runtime: Path, **overrides: object) -> dict:
    observation, intent = _request()
    arguments = {
        "registry_path": registry,
        "runtime_root": runtime,
        "goal_id": GOAL,
        "generated_at": observation["generated_at"],
        "execute": True,
        "monitor_effect_id": OPERATION,
        "todo_id": observation["todo_id"],
        "target_key": observation["target_key"],
        "result_hash": observation["result_hash"],
        "material_change": True,
        "cadence": None,
        "next_due_at": None,
        "reason_summary": None,
        "next_agent_todo": intent["next_agent_todo"],
        "next_action_kind": intent["next_action_kind"],
        "next_task_repository": None,
        "next_required_capabilities": [],
        "next_continuation_policy": None,
        "next_target_key": None,
        "next_claimed_by": None,
        "next_user_todo": intent["next_user_todo"],
        "next_user_task_class": intent["next_user_task_class"],
        "agent_id": AGENT,
    }
    arguments.update(overrides)
    result = write_monitor_poll_todo_state(**arguments)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("provider", ["unpromoted", "file", "sqlite"])
def test_original_receipt_replays_before_fresh_admission_without_effects(
    tmp_path: Path, monkeypatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, _historical_receipt())
    if provider != "unpromoted":
        from loopx.todos import add_goal_todo

        _promote(runtime, state, provider)
        add_goal_todo(registry_path=registry, goal_id=GOAL, role="agent",
                      text="Preserve work accepted after import", operation_id="later-canonical-work")
    outbox = outbox_root(runtime, GOAL) / "unreconciled-original.json"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_bytes(b'{"historical":"requires explicit reconciliation"}\n')
    outbox_before = outbox.read_bytes()
    state_before = state.read_bytes()
    provider_before = read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL,
    ) if provider != "unpromoted" else None

    replayed = _replay(registry, runtime)

    assert replayed["provider_replayed"] is True
    assert [item["todo_id"] for item in replayed["next_todos"]] == [
        AGENT_SUCCESSOR,
        USER_SUCCESSOR,
    ]
    assert state.read_bytes() == state_before
    assert outbox.read_bytes() == outbox_before
    if provider_before is not None:
        assert read_canonical_todos_if_promoted(
            runtime_root=runtime,
            goal_id=GOAL,
        ) == provider_before
    else:
        assert not (runtime / "authority").exists()


@pytest.mark.parametrize("promoted", [False, True])
@pytest.mark.parametrize("change", [
    {"next_user_todo": "Request a different review"},
    {"agent_id": "another-agent"},
    {"result_hash": "a-different-observation"},
    {"next_agent_priority": "P0"},
    {"next_user_priority": "P3"},
])
def test_historical_receipt_rejects_changed_identity(tmp_path: Path, promoted, change) -> None:
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, _historical_receipt())
    if promoted:
        _promote(runtime, state)
    state_before = state.read_bytes()
    provider_before = read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL,
    ) if promoted else None

    with pytest.raises(ValueError, match="request mismatch"):
        _replay(registry, runtime, **change)

    assert state.read_bytes() == state_before
    if promoted:
        assert read_canonical_todos_if_promoted(
            runtime_root=runtime,
            goal_id=GOAL,
        ) == provider_before
    else:
        assert not (runtime / "authority").exists()


@pytest.mark.parametrize("promoted", [False, True])
@pytest.mark.parametrize("damage", ["not_object", "duplicate", "truncated"])
def test_malformed_historical_receipt_is_not_treated_as_absent(
    tmp_path: Path, promoted, damage,
) -> None:
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, None if damage == "not_object" else _historical_receipt())
    if damage == "duplicate":
        _seed_receipt(state, _historical_receipt())
    elif damage == "truncated":
        state.write_text(state.read_text().replace(" -->", " --"))
    if promoted:
        _promote(runtime, state)
    state_before = state.read_bytes()

    with pytest.raises(ValueError, match="receipt must be an object|duplicate|malformed"):
        _replay(registry, runtime)

    assert state.read_bytes() == state_before


def test_unpromoted_goal_without_original_receipt_cannot_start_monitor_work(
    tmp_path: Path,
) -> None:
    registry, runtime, state = _fixture(tmp_path)
    state_before = state.read_bytes()

    with pytest.raises(CanonicalAuthorityRequired):
        _replay(registry, runtime)

    assert state.read_bytes() == state_before
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_historical_receipt_cannot_mask_selected_provider_loss(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, _historical_receipt())
    _promote(runtime, state, provider)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    backend = runtime / "authority" / before["source_authority"].replace("_", "-")
    offline = backend.with_name(backend.name + "-offline")
    display = state.read_bytes()
    backend.rename(offline)
    try:
        with pytest.raises(LocalCoordinationAuthorityUnavailable):
            _replay(registry, runtime)
        assert not backend.exists() and state.read_bytes() == display
    finally:
        offline.rename(backend)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before


def _restore_1x_poll(tmp_path: Path):
    # Fixed output of the ordinary CLI at 19c725e3f, interrupted after its
    # Markdown business write and before quota settlement. Only filesystem
    # roots are rebound. The quota fingerprint includes that physical root,
    # so re-encode its frozen input with the original Python JSON contract;
    # the current transaction implementation is not the expected-value oracle.
    frozen = Path(__file__).parent / "fixtures" / "retained_monitor_receipts_1x.json"
    history = json.loads(frozen.read_text().replace("$FIXTURE_ROOT", str(tmp_path)))
    registry, runtime, state = _fixture(tmp_path)
    receipt = history["business_receipt"]
    digest = hashlib.sha256(receipt["operation_id"].encode()).hexdigest()
    encoded = base64.b64encode(json.dumps(receipt).encode()).decode()
    marker = f"<!-- loopx:monitor-batch:{digest} {encoded} -->\n"
    state.write_text(history["source_state"] + "\n" + marker)
    runs = runtime / "goals" / GOAL / "runs"
    pending = runs / ".transactions" / "quota-monitor-poll" / f"{digest[:24]}.json"
    pending.parent.mkdir(parents=True)
    history["pending"]["request_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(history["request_fingerprint_input"], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    pending.write_text(json.dumps(history["pending"]))
    (runs.parent / "rollout-event-log.jsonl").write_text(json.dumps(history["heartbeat"]) + "\n")
    return registry, runtime, state, pending, history["poll"], marker


@pytest.mark.parametrize("provider", ["unpromoted", "file", "sqlite"])
def test_original_1x_cli_poll_settles_once_without_repeating_business(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, pending, poll, _marker = _restore_1x_poll(tmp_path)
    display, reservation = state.read_bytes(), pending.read_bytes()
    if provider != "unpromoted":
        _promote(runtime, state, provider)
    provider_before = (read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
                       if provider != "unpromoted" else None)
    changed = list(poll)
    changed[changed.index("--result-hash") + 1] = "changed-original-observation"
    rc, rejected = _cli(registry, runtime, *changed)
    assert rc != 0 and rejected["error_code"] == "heartbeat_receipt_identity_conflict", rejected
    assert state.read_bytes() == display and pending.read_bytes() == reservation
    assert _poll_events(runtime) == []

    rc, recovered = _cli(registry, runtime, *poll)
    assert rc == 0 and recovered["appended"] is True, recovered
    # The index is the commit proof; current quota may retain a prepared WAL.
    assert state.read_bytes() == display
    if provider_before is not None:
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == provider_before
    else:
        assert not (runtime / "authority").exists()
    events = _poll_events(runtime)
    assert len(events) == 1
    assert events[0]["todo_id"] == "todo_7f667db6fd16"
    assert events[0]["agent_id"] == AGENT
    committed = pending.read_bytes()
    rc, replay = _cli(registry, runtime, *poll)
    assert rc == 0 and replay["replayed"] is True, replay
    assert state.read_bytes() == display and pending.read_bytes() == committed
    assert _poll_events(runtime) == events
    if provider_before is not None:
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == provider_before
    else:
        assert not (runtime / "authority").exists()
    assert not any(row.get("classification") == "quota_slot_spent" for row in read_run_index(runtime, GOAL))
    if provider == "unpromoted":
        rc, ordinary = _cli(registry, runtime, "todo", "list", "--goal-id", GOAL)
        assert rc != 0 and ordinary["error_code"] == "canonical_authority_required", ordinary
        assert state.read_bytes() == display and pending.read_bytes() == committed
        assert _poll_events(runtime) == events and not (runtime / "authority").exists()


@pytest.mark.parametrize("execute", [False, True])
def test_fresh_unpromoted_monitor_preview_and_execute_refuse_without_effects(tmp_path, execute):
    registry, runtime, state, pending, poll, marker = _restore_1x_poll(tmp_path)
    pending.unlink()
    state.write_text(re.sub(r"next_due_at=\S+", "next_due_at=2000-01-01T00%3A00%3A00Z",
                           state.read_text().replace(marker, "")))
    display = state.read_bytes()
    args = poll if execute else [arg for arg in poll if arg != "--execute"]
    rc, refused = _cli(registry, runtime, *args)
    assert rc != 0 and refused["error_code"] == "canonical_authority_required", refused
    assert state.read_bytes() == display and not pending.exists()
    assert read_run_index(runtime, GOAL) == []
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize(("option", "value"), [
    ("--goal-id", "another-goal"),
    ("--agent-id", "another-agent"),
    ("--todo-id", "todo_another_monitor"),
    ("--turn-instance-id", "another-turn"),
    ("--target-key", "another-target"),
    ("--result-hash", "another-observation"),
    ("--next-agent-todo", "Unrelated new work"),
])
def test_original_1x_cli_rejects_changed_identity_or_intent(tmp_path, option, value):
    registry, runtime, state, pending, poll, _marker = _restore_1x_poll(tmp_path)
    changed = list(poll)
    if option in changed:
        changed[changed.index(option) + 1] = value
    else:
        changed.extend([option, value])
    display, reservation = state.read_bytes(), pending.read_bytes()

    rc, rejected = _cli(registry, runtime, *changed)

    assert rc != 0 and rejected["ok"] is False, rejected
    assert state.read_bytes() == display and pending.read_bytes() == reservation
    assert read_run_index(runtime, GOAL) == []
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize("damage", [
    "missing_quota", "missing_business", "duplicate_business", "corrupt_generation", "corrupt_quota",
    "wrong_business_goal", "wrong_business_effect", "wrong_business_result", "ambiguous_prepared",
])
def test_original_1x_cli_refuses_incomplete_or_corrupt_history_without_fresh_work(tmp_path, damage):
    registry, runtime, state, pending, poll, marker = _restore_1x_poll(tmp_path)
    if damage == "missing_quota":
        pending.unlink()
    elif damage == "missing_business":
        state.write_text(state.read_text().replace(marker, ""))
    elif damage == "duplicate_business":
        state.write_text(state.read_text() + marker)
    elif damage in {"corrupt_generation", "wrong_business_goal", "wrong_business_effect", "wrong_business_result"}:
        encoded = marker.split(" ")[2]
        receipt = json.loads(base64.b64decode(encoded))
        if damage == "corrupt_generation":
            receipt["writeback"]["material_change_generation"] = True
        elif damage == "wrong_business_goal":
            receipt["goal_id"] = "another-goal"
        elif damage == "wrong_business_effect":
            receipt["operation_id"] = "another-effect"
        else:
            receipt["writeback"]["result_hash"] = "another-result"
        state.write_text(state.read_text().replace(encoded, base64.b64encode(json.dumps(receipt).encode()).decode()))
    elif damage == "ambiguous_prepared":
        receipt = json.loads(pending.read_text())
        receipt["status"] = "prepared"
        pending.write_text(json.dumps(receipt))
    else:
        pending.write_text("{invalid receipt")
    display = state.read_bytes()
    reservations = {p.name: p.read_bytes() for p in pending.parent.glob("*.json")}
    rc, refused = _cli(registry, runtime, *poll)
    assert rc != 0, refused
    if damage in {"missing_quota", "missing_business"}:
        assert refused["error_code"] == "canonical_authority_required", refused
    assert state.read_bytes() == display
    assert {p.name: p.read_bytes() for p in pending.parent.glob("*.json")} == reservations
    assert read_run_index(runtime, GOAL) == []
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_1x_cli_refuses_unavailable_selected_provider(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, pending, poll, _marker = _restore_1x_poll(tmp_path)
    _promote(runtime, state, provider)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    backend = runtime / "authority" / before["source_authority"].replace("_", "-")
    offline = backend.with_name(backend.name + "-offline")
    display, reservation = state.read_bytes(), pending.read_bytes()
    backend.rename(offline)
    try:
        rc, refused = _cli(registry, runtime, *poll)
        assert rc != 0 and refused["legacy_fallback_used"] is False, refused
        assert not backend.exists() and state.read_bytes() == display
        assert pending.read_bytes() == reservation and _poll_events(runtime) == []
    finally:
        offline.rename(backend)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("gate_scope_guard", [False, True], ids=["v2", "v3"])
def test_canonical_original_monitor_replays_with_unreadable_display_but_never_creates_work(
    tmp_path, monkeypatch, provider, gate_scope_guard,
):
    from loopx.control_plane.scheduler import provider_monitor_poll

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _fixture(tmp_path)
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, handoff_mode="soft_claim", todos=[{
            "schema_version": "todo_item_v0", "todo_id": MONITOR_ID, "role": "agent",
            "text": "Observe the public source", "status": "open", "done": False,
            "archive_state": "active", "source_section": "Agent Todo",
            "task_class": "continuous_monitor", "claimed_by": AGENT,
            "target_key": "synthetic-target", "cadence": "1h",
        }]), state_path=state, provider=provider)
    calls = []
    native = provider_monitor_poll.effect_runtime_result

    def record(method, params, **kwargs):
        calls.append(params["schema_version"])
        return native(method, params, **kwargs)

    monkeypatch.setattr(provider_monitor_poll, "effect_runtime_result", record)
    first = _replay(registry, runtime, gate_scope_guard=gate_scope_guard)
    assert first["provider_replayed"] is False
    assert calls == ["loopx_coordination_monitor_poll_request_v3" if gate_scope_guard
                     else "loopx_coordination_monitor_poll_request_v2"]
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    state.write_bytes(b"\xffunreadable display")
    display = state.read_bytes()
    recovered = _replay(registry, runtime, gate_scope_guard=gate_scope_guard)
    assert recovered["provider_replayed"] is True
    assert calls[-1] == "loopx_coordination_monitor_poll_request_v4"
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before
    assert state.read_bytes() == display
    with pytest.raises(UnicodeError):
        _replay(registry, runtime, monitor_effect_id="new-work-is-not-recovery", gate_scope_guard=gate_scope_guard)
    with pytest.raises(ValueError):
        _replay(registry, runtime, next_user_todo="Changed original intent", gate_scope_guard=gate_scope_guard)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before
    assert state.read_bytes() == display
