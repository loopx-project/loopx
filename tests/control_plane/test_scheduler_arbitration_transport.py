"""Fused scheduler facts must retain complete reads at the ordinary wire boundary."""
from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.work_items import interaction_contract as interaction
from loopx.control_plane.scheduler.arbitration import (
    SchedulerArbitration,
    SchedulerDisposition,
    _scheduler_arbitration_facts,
)
from loopx.control_plane.work_items.interaction_contract import _interaction_reads_and_arbitration


def contract() -> dict:
    return {"schema_version": "loopx_interaction_contract_v0", "mode": "bounded_delivery",
        "user_channel": {"action_required": False},
        "agent_channel": {"must_attempt": True, "delivery_allowed": True, "quiet_noop_allowed": False},
        "cli_channel": {"spend_allowed_now": False, "spend_after_validation": False}}


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.5, "1800", []])
def test_scheduled_recheck_codec_rejects_nonpositive_or_coerced_seconds(invalid):
    with pytest.raises(TypeError, match="scheduled recheck"):
        SchedulerArbitration.from_projection({"mode": "agent_scope_wait", "errors": [],
            "disposition": "agent_scope_wait", "reason_code": "interaction_agent_scope_wait",
            "scheduled_recheck_after_seconds": invalid})


def encoded_size(value: dict) -> int:
    return len(json.dumps(value, separators=(",", ":")).encode())


def assert_private_files_removed(root: Path) -> None:
    assert list(root.glob("loopx-effect-*/params.json")) == []
    assert list(root.glob("loopx-effect-*/response.json")) == []


@pytest.fixture
def snapshot_directories(monkeypatch):
    created = []
    original = tempfile.TemporaryDirectory

    def observe(*args, **kwargs):
        directory = original(*args, **kwargs)
        if kwargs.get("prefix") == "loopx-effect-":
            created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(tempfile, "TemporaryDirectory", observe)
    return created


@pytest.mark.parametrize("params_bytes", [65535, 65536, 65537])
def test_compact_boundary_keeps_complete_quoted_reads_without_unneeded_snapshot(
    isolated_effect_runtime, monkeypatch, snapshot_directories, params_bytes: int,
) -> None:
    goal_id = "'" * 50_000 + "界😀"
    state_file = "/state/'\"\\界😀"
    todo_id = "todo_'\"\\界😀"
    read = {"command": "read-complete-source", "source": "existing", "evidence": "",
        "unknown": {"opaque": ["界😀\ud800", {"keep": True, "null": None}],
            "numbers": [1e20, -1e20, 1e-6, -0.0]}}
    payload = {"goal_id": goal_id, "goal_state_file": state_file, "should_run": True,
        "agent_todo_summary": {"goal_acceptance_contract": {"enabled": True}},
        "selected_todo": {"todo_id": todo_id}, "required_reads": [read]}
    observed = []
    original = interaction.effect_runtime_result

    def observe(method, params, **kwargs):
        observed.append((encoded_size(params), kwargs["large_local_snapshot"]))
        return original(method, params, **kwargs)

    monkeypatch.setattr(interaction, "effect_runtime_result", observe)
    _interaction_reads_and_arbitration(payload, contract(), runtime_root=None,
        registry_path=None, agent_scope_frontier_actions=())
    assert observed[-1][0] < params_bytes
    assert observed[-1][1] is False
    assert snapshot_directories == []
    read["evidence"] = "x" * (params_bytes - observed[-1][0])
    observed.clear()
    snapshot_directories.clear()

    reads, arbitration = _interaction_reads_and_arbitration(payload, contract(),
        runtime_root=None, registry_path=None, agent_scope_frontier_actions=())
    assert observed == [(params_bytes, params_bytes > 65536)]
    assert bool(snapshot_directories) is (params_bytes > 65536)
    assert all(not directory.exists() for directory in snapshot_directories)
    assert reads == [read, {
        "command": "cat -- " + shlex.quote(state_file), "source": "goal_state", "ordering": "before_work",
        "reason": "Read complete Goal intent, acceptance and stops. Exact Todo reads own task state/claims. Source failure or changed requirements require a fresh guard; summaries cannot replace this read.",
    }, {
        "command": "loopx --format json goal-acceptance inspect --goal-id " + shlex.quote(goal_id),
        "source": "goal_acceptance", "ordering": "before_work",
        "reason": "Read full configured objective, non-goals and criteria with scope/revision. Scoped acceptance does not replace Goal intent or prove completion; disabled/changed criteria require a fresh guard.",
    }, {
        "command": "loopx --format json todo list --goal-id " + shlex.quote(goal_id) + " --todo-id " + shlex.quote(todo_id),
        "source": "selected_todo", "ordering": "before_work",
        "reason": "Read full current requirements and status/claim. Require one matching active Todo; missing, ambiguous or changed work requires a fresh guard. A summary cannot replace this read.",
    }]
    assert arbitration == SchedulerArbitration(SchedulerDisposition.ACTIVE_WORK,
        "interaction_agent_attempt_required", "bounded_delivery")
    assert_private_files_removed(Path(tempfile.gettempdir()))


@pytest.mark.parametrize(("malformed", "error_type"), [
    ("unsupported", TypeError), ("circular", ValueError), ("deep", effect_runtime.EffectRuntimeStartupError),
])
def test_failed_size_probe_preserves_real_rpc_serialization_error(
    isolated_effect_runtime, monkeypatch, snapshot_directories, malformed: str, error_type: type[Exception],
) -> None:
    metadata = object()
    if malformed == "circular":
        metadata = []
        metadata.append(metadata)
    elif malformed == "deep":
        metadata = []
        # CPython 3.13's C encoder accepts 2,000 levels without a recursion error.
        for _ in range(100_000):
            metadata = [metadata]
    modes = []
    original = interaction.effect_runtime_result

    def observe(method, params, **kwargs):
        modes.append(kwargs["large_local_snapshot"])
        return original(method, params, **kwargs)

    monkeypatch.setattr(interaction, "effect_runtime_result", observe)
    pid = effect_runtime.effect_runtime_result("runtime.ping", {})["pid"]
    with pytest.raises(error_type) as error:
        _interaction_reads_and_arbitration({"required_reads": [{"command": "read-source", "metadata": metadata}]},
            contract(), runtime_root=None, registry_path=None, agent_scope_frontier_actions=())
    if malformed == "deep":
        assert error.value.diagnostic_code == "runtime_request_failed"
        assert isinstance(error.value.__cause__, RecursionError)
    assert modes == [True]
    assert snapshot_directories == []
    assert_private_files_removed(Path(tempfile.gettempdir()))
    assert effect_runtime.effect_runtime_result("runtime.ping", {})["pid"] == pid


@pytest.mark.parametrize("reject", [False, True], ids=["complete", "rejected"])
def test_near_limit_read_survives_fused_arbitration_and_cleans_private_files(
    isolated_effect_runtime, reject: bool,
) -> None:
    root = Path(tempfile.gettempdir())
    read = {"command": "bad\0command" if reject else "read-complete-source",
        "source": "existing", "evidence": ""}
    # Match the legacy carrier and envelope sizes; nonce values themselves do not matter.
    params = {"required_reads": [read], "goal_id": None, "goal_state_file": None,
        "goal_acceptance_enabled": False, "selected_todo": {"todo_id": None},
        "task_orchestration_contract": {"schema_version": None, "mode": None, "primary_todo_id": None},
        "should_run": False, "delivery_allowed": True, "selection_required": False,
        "has_replan": False, "settlement_only": False, "effective_action": None,
        "command_prefix": "loopx"}
    envelope = {"schema_version": effect_runtime.EFFECT_RUNTIME_REQUEST_SCHEMA_VERSION,
        "token": "t" * 43, "request_id": "0" * 36,
        "method": "work_item.interaction_reads.project", "params": params}
    read["evidence"] = "x" * (effect_runtime.MAX_REQUEST_BYTES - encoded_size(envelope) - 128)
    assert encoded_size(envelope) + 1 < effect_runtime.MAX_REQUEST_BYTES
    fused = {**params, "arbitration_facts": _scheduler_arbitration_facts(contract())}
    assert encoded_size({**envelope, "params": fused}) > effect_runtime.MAX_REQUEST_BYTES
    try:
        if reject:
            with pytest.raises(effect_runtime.EffectRuntimeRejected, match="required read command contains NUL"):
                _interaction_reads_and_arbitration({"required_reads": [read]}, contract(),
                    runtime_root=None, registry_path=None, agent_scope_frontier_actions=())
        else:
            reads, arbitration = _interaction_reads_and_arbitration({"required_reads": [read]}, contract(),
                runtime_root=None, registry_path=None, agent_scope_frontier_actions=())
            assert reads == [read]
            assert arbitration == SchedulerArbitration(SchedulerDisposition.ACTIVE_WORK,
                "interaction_agent_attempt_required", "bounded_delivery")
    finally:
        assert_private_files_removed(root)
    assert effect_runtime.effect_runtime_result("runtime.ping", {})["ready"] is True


@pytest.mark.parametrize("reject", [False, True], ids=["complete", "rejected"])
def test_small_request_retains_large_generated_read_reply_and_cleans_private_files(
    isolated_effect_runtime, reject: bool,
) -> None:
    root = Path(tempfile.gettempdir())
    runtime_root = "/" + "r" * (effect_runtime.MAX_RESPONSE_BYTES // 2)
    prefix = "loopx --runtime-root " + runtime_root
    payload = {"goal_id": "goal-wire", "should_run": True,
        "agent_todo_summary": {"goal_acceptance_contract": {"enabled": True}},
        "selected_todo": {"todo_id": "todo_selected"},
        "required_reads": [{"command": "bad\0command"}] if reject else []}
    # The input carries this prefix once; generated acceptance/Todo reads each retain it.
    assert encoded_size({**payload, "command_prefix": prefix,
        "arbitration_facts": _scheduler_arbitration_facts(contract())}) < effect_runtime.MAX_REQUEST_BYTES - 4096
    expected = [{
        "command": prefix + " --format json goal-acceptance inspect --goal-id goal-wire",
        "source": "goal_acceptance", "ordering": "before_work",
        "reason": "Read full configured objective, non-goals and criteria with scope/revision. Scoped acceptance does not replace Goal intent or prove completion; disabled/changed criteria require a fresh guard.",
    }, {
        "command": prefix + " --format json todo list --goal-id goal-wire --todo-id todo_selected",
        "source": "selected_todo", "ordering": "before_work",
        "reason": "Read full current requirements and status/claim. Require one matching active Todo; missing, ambiguous or changed work requires a fresh guard. A summary cannot replace this read.",
    }]
    assert encoded_size({"required_reads": expected}) > effect_runtime.MAX_RESPONSE_BYTES
    try:
        if reject:
            with pytest.raises(effect_runtime.EffectRuntimeRejected, match="required read command contains NUL"):
                _interaction_reads_and_arbitration(payload, contract(), runtime_root=runtime_root,
                    registry_path=None, agent_scope_frontier_actions=())
        else:
            reads, arbitration = _interaction_reads_and_arbitration(payload, contract(), runtime_root=runtime_root,
                registry_path=None, agent_scope_frontier_actions=())
            assert reads == expected
            assert arbitration == SchedulerArbitration(SchedulerDisposition.ACTIVE_WORK,
                "interaction_agent_attempt_required", "bounded_delivery")
    finally:
        assert_private_files_removed(root)
    assert effect_runtime.effect_runtime_result("runtime.ping", {})["ready"] is True
