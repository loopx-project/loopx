from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import loopx.control_plane.capability_hooks as capability_hooks
from loopx.cli_commands.post_writeback import (
    dispatch_committed_cli_post_writeback_hooks,
)
from loopx.control_plane.capability_hooks import (
    POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION,
    POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
    POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
    PostWritebackHookRegistration,
    dispatch_post_writeback_hooks,
)
from loopx.file_lock import exclusive_file_lock
from loopx.capabilities.periodic_report.incremental import (
    build_periodic_report_publication_candidate,
    commit_periodic_report_publication_cursor,
)
from loopx.capabilities.periodic_report.post_writeback_hook import (
    build_periodic_report_post_writeback_projection,
    evaluate_periodic_report_trigger_evaluation_intent,
    periodic_report_post_writeback_hook,
    periodic_report_post_writeback_hooks_for_goal,
)


def _projection_goal_fixture(
    tmp_path: Path,
    *,
    state_text: str,
    runs: list[dict[str, object]],
) -> tuple[Path, Path]:
    state_path = tmp_path / "goal.md"
    state_path.write_text(state_text, encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal-1",
                        "repo": str(tmp_path),
                        "state_file": "goal.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runtime" / "goals" / "goal-1" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(run) + "\n" for run in runs),
        encoding="utf-8",
    )
    return tmp_path / "runtime", registry_path


def _successor_ack_run(
    *,
    vision_agent_id: str = "agent-1",
    ack_agent_id: str | None = None,
    frontier: str = "frontier-2",
    obligation: str = "replan-2",
) -> dict[str, object]:
    ack: dict[str, object] = {
        "recorded": True,
        "frontier_identity": frontier,
        "semantic_delta": {
            "accepted": True,
            "outcomes": ["fresh_vision_path_outcome"],
            "trigger_kinds": ["vision_successor_required"],
            "obligation_id": obligation,
        },
    }
    if ack_agent_id is not None:
        ack["agent_id"] = ack_agent_id
    return {
        "generated_at": "2026-08-30T11:00:00Z",
        "goal_id": "goal-1",
        "agent_vision": {
            "schema_version": "goal_vision_replan_contract_v0",
            "agent_id": vision_agent_id,
            "state": "active",
            "vision_patch": {"acceptance_summary": "Next family is bounded."},
        },
        "autonomous_replan_ack": ack,
    }


def _closed_vision_run(
    *,
    agent_id: str = "agent-1",
    summary: str = "First family accepted.",
) -> dict[str, object]:
    return {
        "generated_at": "2026-08-30T10:00:00Z",
        "goal_id": "goal-1",
        "agent_vision": {
            "schema_version": "goal_vision_replan_contract_v0",
            "agent_id": agent_id,
            "state": "vision_closed",
            "vision_patch": {"acceptance_summary": summary},
        },
        "vision_checkpoint": {
            "schema_version": "vision_checkpoint_v0",
            "satisfied": True,
            "decision": "patched",
            "triggers": [
                {
                    "kind": "material_delivery_outcome",
                    "delivery_outcome": "outcome_progress",
                }
            ],
        },
    }


def _input() -> dict[str, object]:
    return {
        "schema_version": POST_WRITEBACK_HOOK_INPUT_SCHEMA_VERSION,
        "receipt": {
            "schema_version": "loopx_rollout_event_v0",
            "event_id": "evt-stage-1",
            "event_kind": "refresh_state",
            "status": "appended",
            "recorded_at": "2026-08-30T01:00:00+08:00",
            "durable": True,
        },
        "identity": {
            "goal_id": "goal-1",
            "agent_id": "agent-1",
            "todo_id": "todo-1",
            "turn_instance_id": "turn-1",
            "effect_id": "goal-1:agent-1:todo-1:turn-1",
        },
        "state_version": "vision-revision-2",
        "projection": {
            "stage_completion": {
                "schema_version": "periodic_report_stage_completion_receipt_v0",
                "stage_identity": "stage-123",
            }
        },
    }


def _source() -> dict[str, object]:
    return {
        "schema_version": "loopx_post_writeback_hook_source_v0",
        "event_kind": "refresh_state",
        "status": "committed",
        "durable": True,
        "identity": {
            "goal_id": "goal-1",
            "agent_id": "agent-1",
            "todo_id": "todo-1",
            "turn_instance_id": "turn-1",
            "effect_id": "goal-1:agent-1:todo-1:turn-1",
        },
        "state_version": "vision-revision-2",
        "committed_at": "2026-08-30T01:00:00+08:00",
        "projection": {
            "stage_completion": {
                "schema_version": "periodic_report_stage_completion_receipt_v0",
                "stage_identity": "stage-123",
            }
        },
    }


def _mutated_source(mutation: str) -> dict[str, object]:
    """Apply one bounded input-construction defect to a valid source."""

    source = dict(_source())
    identity = dict(source["identity"])  # type: ignore[typeddict-item]
    source["identity"] = identity
    if mutation in {
        "goal_id",
        "agent_id",
        "turn_instance_id",
        "effect_id",
    }:
        identity[mutation] = ""  # type: ignore[typeddict-item]
    elif mutation in {"event_kind", "state_version", "committed_at"}:
        source[mutation] = ""
    elif mutation == "todo_id_not_a_string":
        identity["todo_id"] = 123  # type: ignore[typeddict-item]
    elif mutation == "identity_not_an_object":
        source["identity"] = 123
    elif mutation == "durable_not_a_boolean":
        source["durable"] = "committed"
    elif mutation == "unknown_source_field":
        source["extra_field"] = True
    elif mutation == "empty_agent_id_and_state_version":
        identity["agent_id"] = ""  # type: ignore[typeddict-item]
        source["state_version"] = ""
    else:  # pragma: no cover - guards against typo'd mutation ids.
        raise AssertionError(f"unknown source mutation: {mutation}")
    return source


# The TypeScript source decoder owns every field rule; these parameters are
# the full legacy caller inventory of input-construction defects (the four
# required identity fields plus event_kind/state_version/committed_at all
# lived in one composition rejection before #3847). Todo-less writebacks are
# intentionally valid, while a non-string Todo identity remains a decoder
# rejection. Those verdicts live here and in the typed decoder, never in a
# second Python validator.
_SOURCE_REJECTION_MUTATIONS = [
    "goal_id",
    "agent_id",
    "turn_instance_id",
    "effect_id",
    "event_kind",
    "state_version",
    "committed_at",
    "todo_id_not_a_string",
    "identity_not_an_object",
    "durable_not_a_boolean",
    "unknown_source_field",
    "empty_agent_id_and_state_version",
]


@pytest.mark.parametrize("mutation", _SOURCE_REJECTION_MUTATIONS)
def test_incomplete_source_is_one_typed_composition_rejection(
    tmp_path: Path, mutation: str
) -> None:
    """Every decode-boundary rejection maps to the legacy ValueError."""

    producer_calls: list[int] = []
    with pytest.raises(ValueError, match="require committed"):
        dispatch_post_writeback_hooks(
            [_hook(producer_calls=producer_calls)],
            source=_mutated_source(mutation),  # type: ignore[arg-type]
            runtime_root=tmp_path,
        )
    assert producer_calls == []
    receipt_dir = tmp_path / "goals" / "goal-1" / "post_writeback_hooks"
    assert not receipt_dir.is_dir() or not list(receipt_dir.glob("*.json"))


def test_valid_source_dispatches_the_provider_and_writes_a_receipt(
    tmp_path: Path,
) -> None:
    producer_calls: list[int] = []
    dispatch = dispatch_post_writeback_hooks(
        [_hook(producer_calls=producer_calls)],
        source=_source(),  # type: ignore[arg-type]
        runtime_root=tmp_path,
    )
    assert dispatch["failures"] == []
    assert dispatch["intent_count"] == 1
    assert producer_calls == [1]
    receipt_dir = tmp_path / "goals" / "goal-1" / "post_writeback_hooks"
    assert list(receipt_dir.glob("*.json"))


def test_zero_registrations_never_reaches_the_runtime() -> None:
    """Zero hooks dispatch vacuously without touching the decoder."""

    dispatch = dispatch_post_writeback_hooks(
        [],
        source=_mutated_source("agent_id"),  # type: ignore[arg-type]
    )
    assert dispatch["failures"] == []
    assert dispatch["invoked_count"] == 0
    assert dispatch["intent_count"] == 0
    assert dispatch["primary_writeback_preserved"] is True


def test_bridge_projects_source_rejection_as_hook_attributed_composition_failures(
    tmp_path: Path,
) -> None:
    """The CLI bridge keeps the legacy single-failure projection."""

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {"common_runtime_root": str(runtime_root), "goals": [{"id": "goal-1"}]}
        ),
        encoding="utf-8",
    )
    producer_calls: list[int] = []
    dispatch = dispatch_committed_cli_post_writeback_hooks(
        payload={"ok": True, "completed": True},
        registry_path=registry_path,
        runtime_root_arg=None,
        goal_id="goal-1",
        event_kind="refresh_state",
        identity={
            "agent_id": "",
            "todo_id": "todo-1",
            "turn_instance_id": "turn-1",
            "effect_id": "goal-1:agent-1:todo-1:turn-1",
        },
        state_version="2026-09-06T00:00:00Z",
        committed_at="2026-09-06T00:00:00Z",
        hooks=(
            _hook(
                hook_id="periodic_report.stage_completion",
                key="periodic-report:stage-123",
                producer_calls=producer_calls,
            ),
            _hook(
                hook_id="periodic_report.vision_completion",
                key="periodic-report:stage-456",
                producer_calls=producer_calls,
            ),
        ),
        projection_builder=lambda **_kwargs: {
            "stage_completion": {
                "schema_version": "periodic_report_stage_completion_receipt_v0",
                "stage_identity": "stage-123",
            }
        },
    )
    assert dispatch["failures"] == [
        {
            "hook_id": "periodic_report.stage_completion",
            "capability_id": "periodic-report",
            "error_code": "source_projection_failed",
        },
        {
            "hook_id": "periodic_report.vision_completion",
            "capability_id": "periodic-report",
            "error_code": "source_projection_failed",
        },
    ]
    assert dispatch["primary_writeback_preserved"] is True
    assert dispatch["external_writes_performed"] is False
    assert producer_calls == []
    receipt_dir = runtime_root / "goals" / "goal-1" / "post_writeback_hooks"
    assert not receipt_dir.is_dir() or not list(receipt_dir.glob("*.json"))


def _hook(
    *,
    hook_id: str = "periodic_report.stage_completion",
    key: str = "periodic-report:stage-123",
    producer_calls: list[int] | None = None,
) -> PostWritebackHookRegistration:
    def producer(value: object) -> dict[str, object]:
        if producer_calls is not None:
            producer_calls.append(1)
        assert isinstance(value, dict)
        receipt = value["receipt"]
        assert isinstance(receipt, dict)
        return {
            "schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": hook_id,
            "capability_id": "periodic-report",
            "phase": "post_writeback",
            "status": "intent",
            "intent": {
                "schema_version": "loopx_capability_intent_v0",
                "intent_kind": "periodic_report.trigger_evaluation",
                "idempotency_key": key,
                "source_receipt_id": receipt["event_id"],
                "payload": {"stage_identity": "stage-123"},
                "requested_write_scope": [],
            },
        }

    return PostWritebackHookRegistration(
        hook_id=hook_id,
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=producer,
    )


def _named_hook(
    *,
    hook_id: str,
    key: str,
    payload: dict[str, object],
    before_return: Callable[[], None] | None = None,
    max_result_bytes: int = 16 * 1024,
) -> PostWritebackHookRegistration:
    def producer(value: object) -> dict[str, object]:
        if before_return is not None:
            before_return()
        assert isinstance(value, dict)
        receipt = value["receipt"]
        assert isinstance(receipt, dict)
        return {
            "schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": hook_id,
            "capability_id": "periodic-report",
            "phase": "post_writeback",
            "status": "intent",
            "intent": {
                "schema_version": "loopx_capability_intent_v0",
                "intent_kind": "periodic_report.trigger_evaluation",
                "idempotency_key": key,
                "source_receipt_id": receipt["event_id"],
                "payload": payload,
                "requested_write_scope": [],
            },
        }

    return PostWritebackHookRegistration(
        hook_id=hook_id,
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=producer,
        max_result_bytes=max_result_bytes,
    )


def test_post_writeback_dispatch_returns_one_effect_free_intent() -> None:
    dispatch = dispatch_post_writeback_hooks([_hook()], hook_input=_input())

    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == []
    assert dispatch["primary_writeback_preserved"] is True
    assert dispatch["external_writes_performed"] is False


@pytest.mark.parametrize("todo_id", [None, ""], ids=["null", "empty-string"])
def test_post_writeback_dispatch_accepts_todoless_replan_identity(
    todo_id: object,
) -> None:
    source = _source()
    identity = source["identity"]
    assert isinstance(identity, dict)
    identity["todo_id"] = todo_id

    dispatch = dispatch_post_writeback_hooks([_hook()], source=source)

    assert dispatch["invoked_count"] == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == []


def test_post_writeback_runtime_failure_identifies_rejected_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        capability_hooks,
        "effect_runtime_result",
        Mock(
            side_effect=capability_hooks.EffectRuntimeRejected(
                "transaction request is invalid",
                diagnostic_code="invalid_request",
            )
        ),
    )

    dispatch = dispatch_post_writeback_hooks([_hook()], source=_source())

    assert dispatch["invoked_count"] == 0
    assert dispatch["failures"][0]["error_code"] == "runtime_result_invalid"
    assert dispatch["runtime_failure"] == {
        "schema_version": "loopx_post_writeback_runtime_failure_v0",
        "phase": "preflight",
        "error_kind": "request_rejected",
        "diagnostic_code": "invalid_request",
    }


def test_post_writeback_legacy_lock_uses_the_admitted_goal_path(tmp_path: Path) -> None:
    hook_input = _input()
    identity = hook_input["identity"]
    assert isinstance(identity, dict)
    identity["goal_id"] = " goal-1 "

    dispatch = dispatch_post_writeback_hooks(
        [_hook()],
        hook_input=hook_input,
        runtime_root=tmp_path,
    )

    assert dispatch["invoked_count"] == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == []
    assert (
        len(
            list(
                (tmp_path / "goals" / "goal-1" / "post_writeback_hooks").glob("*.json")
            )
        )
        == 1
    )


def test_post_writeback_dispatch_isolates_failures_and_duplicate_hooks() -> None:
    failed = PostWritebackHookRegistration(
        hook_id="periodic_report.failed",
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=lambda _value: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    dispatch = dispatch_post_writeback_hooks(
        [_hook(), _hook(), failed],
        hook_input=_input(),
    )

    assert dispatch["intent_count"] == 1
    assert {item["error_code"] for item in dispatch["failures"]} == {
        "duplicate_hook_id",
        "producer_failed",
    }


def test_post_writeback_dispatch_isolates_non_json_registration_transport(
    tmp_path: Path,
) -> None:
    invalid = PostWritebackHookRegistration(
        hook_id="periodic_report.invalid_contract_transport",
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=lambda _value: {},
        policy_version=object(),  # type: ignore[arg-type]
    )

    dispatch = dispatch_post_writeback_hooks(
        [invalid, _hook()],
        hook_input=_input(),
        runtime_root=tmp_path,
    )

    assert dispatch["invoked_count"] == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == [
        {
            "hook_id": "periodic_report.invalid_contract_transport",
            "capability_id": "periodic-report",
            "error_code": "registration_or_input_rejected",
        }
    ]


def test_post_writeback_dispatch_bounds_invalid_registration_identity_transport(
    tmp_path: Path,
) -> None:
    oversized = PostWritebackHookRegistration(
        hook_id="x" * 1_800_000,
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=lambda _value: {},
    )
    valid = _named_hook(
        hook_id="unknown",
        key="periodic-report:bounded-placeholder",
        payload={"stage_identity": "bounded-placeholder"},
    )

    dispatch = dispatch_post_writeback_hooks(
        [oversized, valid],
        hook_input=_input(),
        runtime_root=tmp_path,
    )

    assert dispatch["invoked_count"] == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["intents"][0]["idempotency_key"] == (
        "periodic-report:bounded-placeholder"
    )
    assert dispatch["failures"] == [
        {
            "hook_id": "unknown",
            "capability_id": "periodic-report",
            "error_code": "registration_or_input_rejected",
        }
    ]


def test_post_writeback_dispatch_isolates_non_json_provider_results() -> None:
    invalid = PostWritebackHookRegistration(
        hook_id="periodic_report.invalid_transport",
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=lambda _value: {"opaque": object()},
    )

    dispatch = dispatch_post_writeback_hooks(
        [invalid, _hook()],
        hook_input=_input(),
    )

    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == [
        {
            "hook_id": "periodic_report.invalid_transport",
            "capability_id": "periodic-report",
            "error_code": "contract_rejected",
        }
    ]


def test_post_writeback_dispatch_isolates_oversized_provider_result(
    tmp_path: Path,
) -> None:
    oversized = PostWritebackHookRegistration(
        hook_id="periodic_report.oversized_transport",
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=lambda _value: {"payload": "x" * 2_000_000},
        max_result_bytes=1024,
    )

    dispatch = dispatch_post_writeback_hooks(
        [oversized, _hook()],
        hook_input=_input(),
        runtime_root=tmp_path,
    )

    assert dispatch["invoked_count"] == 2
    assert dispatch["intent_count"] == 1
    assert len(dispatch["failures"]) == 1
    failure = dispatch["failures"][0]
    assert failure["hook_id"] == "periodic_report.oversized_transport"
    assert failure["capability_id"] == "periodic-report"
    assert failure["error_code"] == "contract_rejected"
    assert failure["durable_receipt_ref"].startswith("post-writeback-hook:pwh_")
    receipts = {
        receipt["hook_id"]: receipt
        for path in (tmp_path / "goals" / "goal-1" / "post_writeback_hooks").glob(
            "*.json"
        )
        for receipt in [json.loads(path.read_text(encoding="utf-8"))]
    }
    assert receipts["periodic_report.oversized_transport"]["status"] == (
        "retryable_failure"
    )
    assert receipts["periodic_report.stage_completion"]["status"] == ("intent_recorded")


def test_post_writeback_python_transport_does_not_own_ts_result_budget(
    tmp_path: Path,
) -> None:
    hook = _named_hook(
        hook_id="periodic_report.float_lexemes",
        key="periodic-report:float-lexemes",
        payload={"values": [1.0] * 17_000},
        max_result_bytes=65_536,
    )

    dispatch = dispatch_post_writeback_hooks(
        [hook],
        hook_input=_input(),
        runtime_root=tmp_path,
    )

    assert dispatch["invoked_count"] == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == []


def test_post_writeback_dispatch_isolates_recursive_provider_transport(
    tmp_path: Path,
) -> None:
    def recursive_result(_value: object) -> dict[str, object]:
        root: dict[str, object] = {}
        cursor = root
        for _ in range(1_100):
            child: dict[str, object] = {}
            cursor["child"] = child
            cursor = child
        return root

    recursive = PostWritebackHookRegistration(
        hook_id="periodic_report.recursive_transport",
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=recursive_result,
    )

    dispatch = dispatch_post_writeback_hooks(
        [recursive, _hook()],
        hook_input=_input(),
        runtime_root=tmp_path,
    )

    assert dispatch["invoked_count"] == 2
    assert dispatch["intent_count"] == 1
    assert len(dispatch["failures"]) == 1
    assert dispatch["failures"][0]["hook_id"] == ("periodic_report.recursive_transport")
    assert dispatch["failures"][0]["error_code"] == "contract_rejected"


def test_post_writeback_transport_pressure_isolates_largest_results(
    tmp_path: Path,
) -> None:
    hooks = [
        _named_hook(
            hook_id=f"periodic_report.transport_{index}",
            key=f"periodic-report:transport-{index}",
            payload={"data": "\u007f" * 64_000},
            max_result_bytes=65_536,
        )
        for index in range(6)
    ]

    dispatch = dispatch_post_writeback_hooks(
        hooks,
        hook_input=_input(),
        runtime_root=tmp_path,
    )

    assert dispatch["invoked_count"] == 6
    assert dispatch["intent_count"] == 4
    assert len(dispatch["failures"]) == 2
    assert {failure["error_code"] for failure in dispatch["failures"]} == {
        "contract_rejected"
    }
    receipts = list(
        (tmp_path / "goals" / "goal-1" / "post_writeback_hooks").glob("*.json")
    )
    assert len(receipts) == 6


def test_post_writeback_transport_baseline_is_admitted_before_providers(
    tmp_path: Path,
) -> None:
    calls = 0

    def producer(_value: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {}

    hook = PostWritebackHookRegistration(
        hook_id="periodic_report.transport_baseline",
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=producer,
    )
    source = _source()
    projection = source["projection"]
    assert isinstance(projection, dict)
    projection["unread_numeric_lexemes"] = [-0.0] * 360_000

    dispatch = dispatch_post_writeback_hooks(
        [hook],
        source=source,
        runtime_root=tmp_path,
    )

    assert calls == 0
    assert dispatch["invoked_count"] == 0
    assert {item["error_code"] for item in dispatch["failures"]} == {
        "runtime_result_invalid"
    }
    assert not list((tmp_path / "goals").rglob("*.json"))


def test_post_writeback_dispatch_rejects_non_durable_input_before_provider() -> None:
    called = False

    def producer(_value: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    hook = PostWritebackHookRegistration(
        hook_id="periodic_report.stage_completion",
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=producer,
    )
    pending = _input()
    pending["receipt"] = {**pending["receipt"], "durable": False}  # type: ignore[arg-type]

    dispatch = dispatch_post_writeback_hooks([hook], hook_input=pending)

    assert called is False
    assert dispatch["intent_count"] == 0
    assert dispatch["failures"][0]["error_code"] == "registration_or_input_rejected"


def test_post_writeback_legacy_input_is_exact_and_preserves_event_id(
    tmp_path: Path,
) -> None:
    input_data = _input()
    first = dispatch_post_writeback_hooks(
        [_hook()],
        hook_input=input_data,
        runtime_root=tmp_path,
    )

    assert first["intents"][0]["source_receipt_id"] == "evt-stage-1"
    receipt_path = next(
        (tmp_path / "goals" / "goal-1" / "post_writeback_hooks").glob("*.json")
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["source_receipt_id"] == "evt-stage-1"

    invalid = {**input_data, "unexpected": True}
    rejected = dispatch_post_writeback_hooks([_hook()], hook_input=invalid)
    assert rejected["invoked_count"] == 0
    assert rejected["failures"][0]["error_code"] == ("registration_or_input_rejected")


def test_periodic_report_hook_emits_only_an_approval_neutral_trigger_intent() -> None:
    hook_input = _input()
    hook_input["projection"] = {
        **hook_input["projection"],  # type: ignore[dict-item]
        "project_progress": {
            "schema_version": "periodic_report_project_progress_projection_v0",
            "goal_id": "goal-1",
            "observed_at": "2026-08-30T09:00:00Z",
            "language": "zh-CN",
            "items": [
                {
                    "item_id": "completed_1",
                    "title": "Frozen stage outcome",
                    "summary": "The stage snapshot is captured at writeback.",
                    "content_kind": "outcome",
                    "value_rank": 10,
                    "source_ref": "todo:todo-1",
                }
            ],
        },
    }
    dispatch = dispatch_post_writeback_hooks(
        [periodic_report_post_writeback_hook()],
        hook_input=hook_input,
    )

    intent = dispatch["intents"][0]
    assert intent["intent_kind"] == "periodic_report.trigger_evaluation"
    assert intent["requested_write_scope"] == []
    assert intent["payload"]["generation_authorized"] is False
    assert intent["payload"]["external_delivery_authorized"] is False
    assert intent["payload"]["project_progress"]["items"][0]["title"] == (
        "Frozen stage outcome"
    )


def test_periodic_report_hook_accepts_durable_todo_completion() -> None:
    hook_input = _input()
    hook_input["receipt"] = {
        **hook_input["receipt"],  # type: ignore[dict-item]
        "event_kind": "todo_complete",
        "status": "committed",
    }

    dispatch = dispatch_post_writeback_hooks(
        [periodic_report_post_writeback_hook()],
        hook_input=hook_input,
    )

    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == []


def test_post_writeback_sidecar_replay_skips_provider(tmp_path) -> None:
    calls = 0
    base = _hook()

    def producer(value: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return dict(base.producer(value))  # type: ignore[arg-type]

    hook = PostWritebackHookRegistration(
        hook_id=base.hook_id,
        capability_id=base.capability_id,
        event_kinds=base.event_kinds,
        intent_kinds=base.intent_kinds,
        requested_read_scope=base.requested_read_scope,
        producer=producer,
    )
    first = dispatch_post_writeback_hooks(
        [hook], hook_input=_input(), runtime_root=tmp_path
    )
    replay = dispatch_post_writeback_hooks(
        [hook], hook_input=_input(), runtime_root=tmp_path
    )

    assert calls == 1
    assert first["intent_count"] == replay["intent_count"] == 1
    assert replay["invoked_count"] == 0
    assert replay["replayed_hooks"] == ["periodic_report.stage_completion"]
    assert replay["intents"] == first["intents"]


def test_post_writeback_batches_runtime_calls_for_fresh_and_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    real_effect_runtime_result = capability_hooks.effect_runtime_result

    def tracked_runtime_result(
        method: str,
        params: object,
        **kwargs: object,
    ) -> object:
        calls.append(method)
        return real_effect_runtime_result(method, params, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        capability_hooks,
        "effect_runtime_result",
        tracked_runtime_result,
    )
    second_hook_id = "periodic_report.second"

    def second_producer(value: object) -> dict[str, object]:
        assert isinstance(value, dict)
        receipt = value["receipt"]
        assert isinstance(receipt, dict)
        return {
            "schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": second_hook_id,
            "capability_id": "periodic-report",
            "phase": "post_writeback",
            "status": "intent",
            "intent": {
                "schema_version": "loopx_capability_intent_v0",
                "intent_kind": "periodic_report.trigger_evaluation",
                "idempotency_key": "periodic-report:second",
                "source_receipt_id": receipt["event_id"],
                "payload": {"stage_identity": "stage-456"},
                "requested_write_scope": [],
            },
        }

    second = PostWritebackHookRegistration(
        hook_id=second_hook_id,
        capability_id="periodic-report",
        event_kinds=("refresh_state",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=second_producer,
    )
    hooks = [_hook(), second]
    first = dispatch_post_writeback_hooks(
        hooks,
        hook_input=_input(),
        runtime_root=tmp_path,
    )
    assert first["intent_count"] == 2
    assert calls == [
        "capability_hook.post_writeback.transaction",
        "capability_hook.post_writeback.transaction",
    ]

    calls.clear()
    replay = dispatch_post_writeback_hooks(
        hooks,
        hook_input=_input(),
        runtime_root=tmp_path,
    )
    assert replay["replayed_hooks"] == [
        "periodic_report.second",
        "periodic_report.stage_completion",
    ]
    assert calls == ["capability_hook.post_writeback.transaction"]


def test_post_writeback_retryable_failure_recovers_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    runtime_phases: list[object] = []
    base = _hook()
    real_effect_runtime_result = capability_hooks.effect_runtime_result

    def tracked_runtime_result(
        method: str,
        params: object,
        **kwargs: object,
    ) -> object:
        if method == "capability_hook.post_writeback.transaction":
            runtime_phases.append(
                params.get("phase") if isinstance(params, dict) else None
            )
        return real_effect_runtime_result(method, params, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        capability_hooks,
        "effect_runtime_result",
        tracked_runtime_result,
    )

    def producer(value: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return dict(base.producer(value))  # type: ignore[arg-type]

    hook = PostWritebackHookRegistration(
        hook_id=base.hook_id,
        capability_id=base.capability_id,
        event_kinds=base.event_kinds,
        intent_kinds=base.intent_kinds,
        requested_read_scope=base.requested_read_scope,
        producer=producer,
    )
    first = dispatch_post_writeback_hooks(
        [hook],
        hook_input=_input(),
        runtime_root=tmp_path,
    )

    assert first["intent_count"] == 0
    assert first["failures"][0]["error_code"] == "producer_failed"
    assert first["failures"][0]["durable_receipt_ref"].startswith(
        "post-writeback-hook:pwh_"
    )
    receipt_path = next(
        (tmp_path / "goals" / "goal-1" / "post_writeback_hooks").glob("*.json")
    )
    failed_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert failed_receipt["status"] == "retryable_failure"
    assert failed_receipt["attempt_count"] == 1
    assert runtime_phases == ["preflight", "finalize"]

    runtime_phases.clear()
    recovered = dispatch_post_writeback_hooks(
        [hook], hook_input=_input(), runtime_root=tmp_path
    )
    assert runtime_phases == ["preflight", "finalize"]
    runtime_phases.clear()
    replay = dispatch_post_writeback_hooks(
        [hook], hook_input=_input(), runtime_root=tmp_path
    )
    assert runtime_phases == ["preflight"]

    assert calls == 2
    assert recovered["intent_count"] == replay["intent_count"] == 1
    assert recovered["retried_hooks"] == ["periodic_report.stage_completion"]
    assert replay["replayed_hooks"] == ["periodic_report.stage_completion"]
    recovered_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert recovered_receipt["status"] == "intent_recorded"
    assert recovered_receipt["attempt_count"] == 2


def test_post_writeback_policy_version_rotates_replay_identity(tmp_path) -> None:
    calls = 0
    base = _hook()

    def producer(value: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return dict(base.producer(value))  # type: ignore[arg-type]

    for policy_version in ("v0", "v1"):
        hook = PostWritebackHookRegistration(
            hook_id=base.hook_id,
            capability_id=base.capability_id,
            event_kinds=base.event_kinds,
            intent_kinds=base.intent_kinds,
            requested_read_scope=base.requested_read_scope,
            producer=producer,
            policy_version=policy_version,
        )
        dispatch = dispatch_post_writeback_hooks(
            [hook], hook_input=_input(), runtime_root=tmp_path
        )
        assert dispatch["invoked_count"] == 1

    assert calls == 2
    assert (
        len(
            list(
                (tmp_path / "goals" / "goal-1" / "post_writeback_hooks").glob("*.json")
            )
        )
        == 2
    )


def test_periodic_report_hook_requires_explicit_goal_profile_opt_in(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal-1",
                        "control_plane": {
                            "periodic_report": {
                                "enabled": False,
                                "profile_preset": "weekly",
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert (
        periodic_report_post_writeback_hooks_for_goal(
            registry_path=registry_path,
            runtime_root=tmp_path / "runtime",
            goal_id="goal-1",
        )
        == ()
    )

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["control_plane"]["periodic_report"].update(
        {"enabled": True, "route_ref": "loopx-concierge"}
    )
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    hooks = periodic_report_post_writeback_hooks_for_goal(
        registry_path=registry_path, runtime_root=tmp_path / "runtime", goal_id="goal-1"
    )
    assert len(hooks) == 1
    assert hooks[0].hook_id == "periodic_report.runtime_trigger"

    hook_input = _input()
    hook_input["projection"] = {
        "stage_completion": {
            "schema_version": "periodic_report_stage_completion_receipt_v0",
            "stage_identity": "stage-123",
            "agent_id": "agent-1",
            "closed_vision_revision": "2026-08-30T10:00:00Z",
            "frontier_identity": "frontier-2",
            "transition": "successor_frontier_settled",
            "completed_at": "2026-08-30T11:00:00Z",
            "acceptance": "validated",
            "outcome_checkpoint_satisfied": True,
            "durable_writeback_required": True,
            "evidence_refs": ["frontier-2"],
        }
    }
    dispatch = dispatch_post_writeback_hooks(hooks, hook_input=hook_input)
    decision = evaluate_periodic_report_trigger_evaluation_intent(
        dispatch["intents"][0]
    )

    assert decision["eligible"] is True
    assert decision["selected_trigger_kind"] == "bounded_segment_milestone"
    assert decision["boundary"]["external_writes_performed"] is False


def test_periodic_report_hook_uses_live_machine_default_without_goal_mutation(
    tmp_path,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_payload = {"goals": [{"id": "goal-1"}]}
    registry_path.write_text(json.dumps(registry_payload), encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    defaults = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "periodic_report": {
                "schema_version": "periodic_report_machine_defaults_v0",
                "enabled": True,
                "inheritance": "live_machine_default",
                "profile_preset": "weekly-progress",
                "route_ref": "loopx-concierge",
                "timezone": "Asia/Shanghai",
            }
        },
    }
    from loopx.capabilities.periodic_report.machine_store import (
        configure_periodic_report_machine_defaults,
    )

    preview = configure_periodic_report_machine_defaults(
        runtime_root=runtime_root,
        machine_defaults=defaults,
    )
    configure_periodic_report_machine_defaults(
        runtime_root=runtime_root,
        machine_defaults=defaults,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )

    hooks = periodic_report_post_writeback_hooks_for_goal(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
    )

    assert len(hooks) == 1
    assert json.loads(registry_path.read_text(encoding="utf-8")) == registry_payload


def _write_unreadable_periodic_report_machine_store(runtime_root: Path) -> None:
    store = runtime_root / "machine" / "configuration.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "schema_version": "loopx_machine_configuration_v0",
                "namespaces": {
                    "periodic_report": {
                        "schema_version": "periodic_report_machine_defaults_v0",
                        "enabled": "true",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_periodic_report_hook_degrades_to_no_hooks_when_machine_store_is_unreadable(
    tmp_path,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"goals": [{"id": "goal-1"}]}),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    _write_unreadable_periodic_report_machine_store(runtime_root)

    with pytest.warns(UserWarning, match="periodic_report.enabled must be a boolean"):
        assert (
            periodic_report_post_writeback_hooks_for_goal(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id="goal-1",
            )
            == ()
        )


def test_periodic_report_hook_degrades_to_no_hooks_on_legacy_machine_namespace(
    tmp_path,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"goals": [{"id": "goal-1"}]}),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    store = runtime_root / "machine" / "configuration.json"
    store.parent.mkdir(parents=True)
    store.write_text(
        json.dumps(
            {
                "schema_version": "loopx_machine_configuration_v0",
                "namespaces": {
                    "periodic_report": {
                        "schema_version": "periodic_report_machine_defaults_v0",
                        "enabled": True,
                        "delivery_style": "weekly",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="periodic_report contains unsupported fields"):
        assert (
            periodic_report_post_writeback_hooks_for_goal(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id="goal-1",
            )
            == ()
        )


def test_periodic_report_hook_reports_invalid_goal_overrides_like_canonical_resolver(
    tmp_path,
) -> None:
    from loopx.capabilities.periodic_report.machine_defaults import (
        resolve_goal_periodic_report_subscription,
    )

    invalid_overrides = [
        (
            {"enabled": True},
            "goal periodic_report.profile_preset is required",
        ),
        (
            {
                "enabled": "true",
                "profile_preset": "weekly-progress",
                "route_ref": "loopx-concierge",
            },
            "goal periodic_report.enabled must be a boolean",
        ),
    ]
    for override, expected_error in invalid_overrides:
        goal = {"id": "goal-1", "control_plane": {"periodic_report": override}}
        with pytest.raises((TypeError, ValueError), match=expected_error):
            resolve_goal_periodic_report_subscription(goal)

        registry_path = tmp_path / "registry.json"
        registry_path.write_text(
            json.dumps({"goals": [goal]}),
            encoding="utf-8",
        )
        with pytest.warns(UserWarning, match=expected_error):
            assert (
                periodic_report_post_writeback_hooks_for_goal(
                    registry_path=registry_path,
                    runtime_root=tmp_path / "runtime",
                    goal_id="goal-1",
                )
                == ()
            )


def test_todo_complete_survives_unreadable_periodic_report_machine_store(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loopx.cli import main as cli_main

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    state = tmp_path / "STATE.md"
    state.write_text(
        "# Goal\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] demo work item\n"
        "  <!-- loopx:todo status=open task_class=advancement_task "
        "claimed_by=agent-a todo_id=todo_dem0item -->\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal-1",
                        "repo": str(tmp_path),
                        "state_file": "STATE.md",
                        "agents": [{"id": "agent-a"}],
                    }
                ],
                "common_runtime_root": str(runtime_root),
            }
        ),
        encoding="utf-8",
    )
    _write_unreadable_periodic_report_machine_store(runtime_root)

    with pytest.warns(UserWarning, match="periodic_report.enabled must be a boolean"):
        exit_code = cli_main(
            [
                "--registry",
                str(registry_path),
                "--format",
                "json",
                "todo",
                "complete",
                "--goal-id",
                "goal-1",
                "--todo-id",
                "todo_dem0item",
                "--agent-id",
                "agent-a",
                "--evidence",
                "demo",
            ]
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["completed"] is True


def test_periodic_report_projection_reduces_durable_successor_transition(
    tmp_path,
) -> None:
    runtime_root, registry_path = _projection_goal_fixture(
        tmp_path,
        state_text="""# Goal

## User Todo

## Agent Todo

- [ ] Analyze the next bounded family.
  <!-- loopx:todo todo_id=todo-next status=open task_class=advancement_task claimed_by=agent-1 -->
""",
        runs=[
            _successor_ack_run(),
            _closed_vision_run(),
        ],
    )

    projection = build_periodic_report_post_writeback_projection(
        payload={"state": {"path": str(tmp_path / "goal.md")}},
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-1",
    )

    receipt = projection["stage_completion"]
    assert receipt["transition"] == "successor_frontier_settled"
    assert receipt["frontier_identity"] == "frontier-2"
    assert projection["project_progress"]["observed_at"] == receipt["completed_at"]


def test_periodic_report_projection_reduces_terminal_after_todo_completion(
    tmp_path,
) -> None:
    runtime_root, registry_path = _projection_goal_fixture(
        tmp_path,
        state_text="""# Goal

## User Todo

## Agent Todo

- [x] Complete the bounded analysis.
  <!-- loopx:todo todo_id=todo_analysis status=done task_class=advancement_task claimed_by=agent-1 no_followup=true updated_at=2026-08-30T10:30:00Z -->
- [ ] Watch for later external changes.
  <!-- loopx:todo todo_id=todo_watch status=open task_class=continuous_monitor claimed_by=agent-1 watch_only=true next_due_at=2026-09-06T10:30:00Z -->
""",
        runs=[
            {
                "generated_at": "2026-08-30T10:00:00Z",
                "goal_id": "goal-1",
                "agent_id": "agent-1",
                "agent_vision": {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "agent_id": "agent-1",
                    "state": "vision_closed",
                    "vision_patch": {"acceptance_summary": "Analysis accepted."},
                },
                "vision_checkpoint": {
                    "schema_version": "vision_checkpoint_v0",
                    "satisfied": True,
                    "decision": "patched",
                    "triggers": [
                        {
                            "kind": "material_delivery_outcome",
                            "delivery_outcome": "primary_goal_outcome",
                        }
                    ],
                },
            },
        ],
    )

    projection = build_periodic_report_post_writeback_projection(
        payload={"state_file": str(tmp_path / "goal.md")},
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-1",
    )

    receipt = projection["stage_completion"]
    assert receipt["transition"] == "goal_terminal"
    assert receipt["frontier_identity"] == "validated-goal-terminal"


def test_periodic_report_projection_evaluates_turn_capabilities_absent_and_present(
    tmp_path: Path,
) -> None:
    runtime_root, registry_path = _projection_goal_fixture(
        tmp_path,
        state_text="""# Goal

## User Todo

## Agent Todo

- [ ] Resume the follow-up once network capacity returns.
  <!-- loopx:todo todo_id=todo_capacity status=open task_class=advancement_task claimed_by=agent-1 action_kind=gated_work resume_when=capacity_available:network -->
- [ ] Analyze the next bounded family.
  <!-- loopx:todo todo_id=todo_next status=open task_class=advancement_task claimed_by=agent-1 -->
""",
        runs=[
            _successor_ack_run(),
            _closed_vision_run(),
        ],
    )

    projection_absent = build_periodic_report_post_writeback_projection(
        payload={"state_file": str(tmp_path / "goal.md")},
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-1",
    )
    assert projection_absent.get("stage_completion") is not None
    next_actions_absent = [
        item["source_ref"]
        for item in projection_absent["project_progress"]["items"]
        if item.get("content_kind") == "next_action"
    ]
    assert next_actions_absent == ["todo:todo_next"]

    projection_present = build_periodic_report_post_writeback_projection(
        payload={
            "state_file": str(tmp_path / "goal.md"),
            "available_capabilities": ["network"],
        },
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-1",
    )
    assert projection_present.get("stage_completion") is not None
    next_actions_present = [
        item["source_ref"]
        for item in projection_present["project_progress"]["items"]
        if item.get("content_kind") == "next_action"
    ]
    assert next_actions_present == ["todo:todo_capacity"]


def _published_report_goal_fixtures(
    tmp_path,
    *,
    runs,
) -> tuple[Path, Path, Path]:
    """Materialize the state file, registry, and runs index for goal-1.

    The projection-under-test only needs one open advancement todo on the
    active state; the runs rows below drive the stage-completion receipt.
    """

    state_path = tmp_path / "goal.md"
    state_path.write_text(
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [ ] Pick the next bounded slice of follow-up work.\n"
        "  <!-- loopx:todo todo_id=todo-followup status=open "
        "task_class=advancement_task claimed_by=agent-1 -->\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {"id": "goal-1", "repo": str(tmp_path), "state_file": "goal.md"}
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    (runtime_root / "goals" / "goal-1" / "runs").mkdir(parents=True)
    return runtime_root, registry_path, state_path


def test_periodic_report_hook_accepts_projection_after_a_published_report(
    tmp_path,
) -> None:
    runs = [
        {
            "generated_at": "2026-08-30T11:00:00Z",
            "goal_id": "goal-1",
            "agent_vision": {
                "schema_version": "goal_vision_replan_contract_v0",
                "agent_id": "agent-1",
                "state": "active",
                "vision_patch": {"acceptance_summary": "Follow-up slice is scoped."},
            },
            "autonomous_replan_ack": {
                "recorded": True,
                "frontier_identity": "frontier-followup",
                "semantic_delta": {
                    "accepted": True,
                    "outcomes": ["fresh_vision_path_outcome"],
                    "trigger_kinds": ["vision_successor_required"],
                    "obligation_id": "replan-2",
                },
            },
        },
        {
            "generated_at": "2026-08-30T10:00:00Z",
            "goal_id": "goal-1",
            "agent_vision": {
                "schema_version": "goal_vision_replan_contract_v0",
                "agent_id": "agent-1",
                "state": "vision_closed",
                "vision_patch": {
                    "acceptance_summary": "Initial slice accepted and reported."
                },
            },
            "vision_checkpoint": {
                "schema_version": "vision_checkpoint_v0",
                "satisfied": True,
                "decision": "patched",
                "triggers": [
                    {
                        "kind": "material_delivery_outcome",
                        "delivery_outcome": "outcome_progress",
                    }
                ],
            },
        },
    ]
    runtime_root, registry_path, state_path = _published_report_goal_fixtures(
        tmp_path, runs=runs
    )
    ((runtime_root / "goals" / "goal-1" / "runs") / "index.jsonl").write_text(
        "".join(json.dumps(run) + "\n" for run in runs), encoding="utf-8"
    )
    candidate = build_periodic_report_publication_candidate(
        goal_id="goal-1",
        agent_id="agent-1",
        generation_id="report_generation_first",
        trigger_receipt={"coalesced_trigger_ids": ["trigger_first"]},
        facts=[],
        baseline=None,
    )
    commit_periodic_report_publication_cursor(
        runtime_root=runtime_root,
        candidate=candidate,
        publication_id="goal-channel:first",
        delivered_at="2026-08-30T12:00:00Z",
        covered_until="2026-08-30T11:00:00Z",
    )

    projection = build_periodic_report_post_writeback_projection(
        payload={"state": {"path": str(state_path)}},
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-1",
    )

    assert projection["last_report"]["covered_trigger_ids"] == ["trigger_first"]

    hook_input = _input()
    hook_input["projection"] = projection  # type: ignore[assignment]
    dispatch = dispatch_post_writeback_hooks(
        [periodic_report_post_writeback_hook()],
        hook_input=hook_input,
    )

    assert dispatch["failures"] == []
    assert dispatch["intent_count"] == 1
    intent = dispatch["intents"][0]
    assert intent["payload"]["last_report"]["covered_trigger_ids"] == ["trigger_first"]


def test_post_writeback_concurrent_exact_dispatch_single_flight(tmp_path) -> None:
    calls = 0
    lock = threading.Lock()
    base = _hook()

    def producer(value: object) -> dict[str, object]:
        nonlocal calls
        time.sleep(0.05)
        with lock:
            calls += 1
        return dict(base.producer(value))  # type: ignore[arg-type]

    hook = PostWritebackHookRegistration(
        hook_id=base.hook_id,
        capability_id=base.capability_id,
        event_kinds=base.event_kinds,
        intent_kinds=base.intent_kinds,
        requested_read_scope=base.requested_read_scope,
        producer=producer,
    )
    barrier = threading.Barrier(2)
    results: list[dict[str, object]] = []

    def worker() -> None:
        barrier.wait()
        res = dispatch_post_writeback_hooks(
            [hook], hook_input=_input(), runtime_root=tmp_path
        )
        results.append(res)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert calls == 1
    assert len(results) == 2
    invoked_counts = [res["invoked_count"] for res in results]
    assert sorted(invoked_counts) == [0, 1]
    replayed_results = [res for res in results if res["invoked_count"] == 0]
    assert replayed_results[0]["replayed_hooks"] == ["periodic_report.stage_completion"]
    assert results[0]["intents"] == results[1]["intents"]


def test_post_writeback_overlapping_batches_single_flight_shared_provider(
    tmp_path: Path,
) -> None:
    counts = {"shared": 0, "a": 0, "b": 0}
    count_lock = threading.Lock()

    def counted(name: str, *, delay: float = 0.0) -> Callable[[], None]:
        def record() -> None:
            with count_lock:
                counts[name] += 1
            if delay:
                time.sleep(delay)

        return record

    shared = _named_hook(
        hook_id="periodic_report.shared",
        key="periodic-report:shared",
        payload={"stage_identity": "shared"},
        before_return=counted("shared", delay=0.1),
    )
    hook_a = _named_hook(
        hook_id="periodic_report.a",
        key="periodic-report:a",
        payload={"stage_identity": "a"},
        before_return=counted("a"),
    )
    hook_b = _named_hook(
        hook_id="periodic_report.b",
        key="periodic-report:b",
        payload={"stage_identity": "b"},
        before_return=counted("b"),
    )
    barrier = threading.Barrier(2)
    results: list[dict[str, object]] = []

    def worker(hooks: list[PostWritebackHookRegistration]) -> None:
        barrier.wait()
        results.append(
            dispatch_post_writeback_hooks(
                hooks,
                hook_input=_input(),
                runtime_root=tmp_path,
            )
        )

    first = threading.Thread(target=worker, args=([shared, hook_a],))
    second = threading.Thread(target=worker, args=([shared, hook_b],))
    first.start()
    second.start()
    first.join(timeout=10.0)
    second.join(timeout=10.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert counts == {"shared": 1, "a": 1, "b": 1}
    assert len(results) == 2
    assert [result["intent_count"] for result in results] == [2, 2]
    assert [result["failures"] for result in results] == [[], []]


def test_post_writeback_slow_earlier_provider_does_not_prelock_later_hook(
    tmp_path: Path,
) -> None:
    slow_started = threading.Event()
    release_slow = threading.Event()
    counts = {"slow": 0, "shared": 0}
    count_lock = threading.Lock()

    def block_slow() -> None:
        with count_lock:
            counts["slow"] += 1
        slow_started.set()
        assert release_slow.wait(timeout=5.0)

    def count_shared() -> None:
        with count_lock:
            counts["shared"] += 1

    slow = _named_hook(
        hook_id="periodic_report.aaa_slow",
        key="periodic-report:slow",
        payload={"stage_identity": "slow"},
        before_return=block_slow,
    )
    shared = _named_hook(
        hook_id="periodic_report.bbb_shared",
        key="periodic-report:shared",
        payload={"stage_identity": "shared"},
        before_return=count_shared,
    )
    batch_results: list[dict[str, object]] = []

    def run_batch() -> None:
        batch_results.append(
            dispatch_post_writeback_hooks(
                [slow, shared],
                hook_input=_input(),
                runtime_root=tmp_path,
            )
        )

    thread = threading.Thread(target=run_batch)
    thread.start()
    assert slow_started.wait(timeout=5.0)
    try:
        shared_result = dispatch_post_writeback_hooks(
            [shared],
            hook_input=_input(),
            runtime_root=tmp_path,
            lease_timeout_seconds=0.05,
        )
    finally:
        release_slow.set()
    thread.join(timeout=10.0)

    assert not thread.is_alive()
    assert counts == {"slow": 1, "shared": 1}
    assert shared_result["invoked_count"] == 1
    assert shared_result["intent_count"] == 1
    assert shared_result["failures"] == []
    assert len(batch_results) == 1
    assert batch_results[0]["invoked_count"] == 1
    assert batch_results[0]["intent_count"] == 2
    assert batch_results[0]["failures"] == []
    assert batch_results[0]["replayed_hooks"] == [shared.hook_id]


def test_post_writeback_rolling_upgrade_guard_preserves_legacy_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook_input = _input()
    new_hook = _hook(key="new-winner-key")
    preflight = capability_hooks.effect_runtime_result(
        "capability_hook.post_writeback.transaction",
        {
            "schema_version": "loopx_post_writeback_hook_transaction_request_v0",
            "phase": "preflight",
            "runtime_root": str(tmp_path.resolve()),
            "source": None,
            "hook_input": hook_input,
            "registrations": [new_hook.contract()],
            "transaction_id": None,
            "provider_outcomes": [],
        },
    )
    assert isinstance(preflight, dict)
    provider_plan = preflight["provider_plan"]
    assert isinstance(provider_plan, list)
    plan = provider_plan[0]
    assert isinstance(plan, dict)
    admitted_input = plan["hook_input"]
    assert isinstance(admitted_input, dict)
    admitted_receipt = admitted_input["receipt"]
    assert isinstance(admitted_receipt, dict)
    receipt_path = (
        tmp_path
        / "goals"
        / "goal-1"
        / "post_writeback_hooks"
        / f"{plan['dispatch_id']}.json"
    )
    old_result = dict(_hook(key="old-winner-key").producer(admitted_input))
    old_receipt = {
        "schema_version": POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
        "dispatch_id": plan["dispatch_id"],
        "hook_id": plan["hook_id"],
        "capability_id": plan["capability_id"],
        "source_receipt_id": admitted_receipt["event_id"],
        "status": "intent_recorded",
        "intent": old_result["intent"],
        "error_code": None,
        "attempt_count": 1,
        "recorded_at": admitted_receipt["recorded_at"],
    }

    preflight_seen = threading.Event()
    legacy_guard_started = threading.Event()
    real_runtime_result = capability_hooks.effect_runtime_result
    real_file_lock = capability_hooks.exclusive_file_lock

    def tracked_runtime_result(
        method: str,
        params: object,
        **kwargs: object,
    ) -> object:
        result = real_runtime_result(method, params, **kwargs)  # type: ignore[arg-type]
        if isinstance(params, dict) and params.get("phase") == "preflight":
            preflight_seen.set()
        return result

    def tracked_file_lock(path: Path, **kwargs: object) -> object:
        if path == receipt_path:
            legacy_guard_started.set()
        return real_file_lock(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        capability_hooks,
        "effect_runtime_result",
        tracked_runtime_result,
    )
    monkeypatch.setattr(
        capability_hooks,
        "exclusive_file_lock",
        tracked_file_lock,
    )

    results: list[dict[str, object]] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(
                dispatch_post_writeback_hooks(
                    [new_hook],
                    hook_input=hook_input,
                    runtime_root=tmp_path,
                )
            )
        except Exception as exc:  # pragma: no cover - asserted below.
            errors.append(exc)

    with exclusive_file_lock(receipt_path, operation="legacy_writer_test"):
        thread = threading.Thread(target=worker)
        thread.start()
        assert preflight_seen.wait(timeout=5.0)
        assert legacy_guard_started.wait(timeout=5.0)
        assert thread.is_alive()
        receipt_path.write_text(json.dumps(old_receipt), encoding="utf-8")

    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert errors == []
    assert len(results) == 1
    dispatch = results[0]
    assert dispatch["invoked_count"] == 0
    assert dispatch["intent_count"] == 1
    assert dispatch["failures"] == []
    assert dispatch["replayed_hooks"] == ["periodic_report.stage_completion"]
    assert dispatch["intents"][0]["idempotency_key"] == "old-winner-key"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == old_receipt


def test_post_writeback_legacy_lock_timeout_isolates_other_hooks(
    tmp_path: Path,
) -> None:
    calls = {"a": 0, "b": 0}

    def count(name: str) -> Callable[[], None]:
        def record() -> None:
            calls[name] += 1

        return record

    hook_a = _named_hook(
        hook_id="periodic_report.locked_a",
        key="periodic-report:locked-a",
        payload={"stage_identity": "a"},
        before_return=count("a"),
    )
    hook_b = _named_hook(
        hook_id="periodic_report.free_b",
        key="periodic-report:free-b",
        payload={"stage_identity": "b"},
        before_return=count("b"),
    )
    input_data = _input()
    preflight = capability_hooks.effect_runtime_result(
        "capability_hook.post_writeback.transaction",
        {
            "schema_version": "loopx_post_writeback_hook_transaction_request_v0",
            "phase": "preflight",
            "runtime_root": str(tmp_path.resolve()),
            "source": None,
            "hook_input": input_data,
            "registrations": [hook_a.contract(), hook_b.contract()],
            "transaction_id": None,
            "provider_outcomes": [],
        },
    )
    assert isinstance(preflight, dict)
    plans = preflight["provider_plan"]
    assert isinstance(plans, list)
    locked_plan = next(
        plan
        for plan in plans
        if isinstance(plan, dict) and plan.get("hook_id") == hook_a.hook_id
    )
    locked_receipt = (
        tmp_path
        / "goals"
        / "goal-1"
        / "post_writeback_hooks"
        / f"{locked_plan['dispatch_id']}.json"
    )

    with exclusive_file_lock(locked_receipt, operation="legacy_writer_test"):
        dispatch = dispatch_post_writeback_hooks(
            [hook_a, hook_b],
            hook_input=input_data,
            runtime_root=tmp_path,
            lease_timeout_seconds=0.05,
        )

    assert calls == {"a": 0, "b": 1}
    assert dispatch["invoked_count"] == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["intents"][0]["idempotency_key"] == "periodic-report:free-b"
    assert len(dispatch["failures"]) == 1
    assert dispatch["failures"][0]["hook_id"] == hook_a.hook_id
    assert dispatch["failures"][0]["error_code"] == "lock_acquire_timeout"
    receipts = list(
        (tmp_path / "goals" / "goal-1" / "post_writeback_hooks").glob("*.json")
    )
    assert len(receipts) == 1


@pytest.mark.parametrize(
    "controlled_clock", [False, True], ids=["real-clock", "budget"]
)
def test_post_writeback_legacy_locks_share_one_batch_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    controlled_clock: bool,
) -> None:
    calls = {"free": 0}
    locked_hooks = [
        _named_hook(
            hook_id=f"periodic_report.locked_{suffix}",
            key=f"periodic-report:locked-{suffix}",
            payload={"stage_identity": suffix},
        )
        for suffix in ("a", "b", "c")
    ]
    free_hook = _named_hook(
        hook_id="periodic_report.free_z",
        key="periodic-report:free-z",
        payload={"stage_identity": "z"},
        before_return=lambda: calls.__setitem__("free", calls["free"] + 1),
    )
    hooks = [*locked_hooks, free_hook]
    input_data = _input()
    preflight = capability_hooks.effect_runtime_result(
        "capability_hook.post_writeback.transaction",
        {
            "schema_version": "loopx_post_writeback_hook_transaction_request_v0",
            "phase": "preflight",
            "runtime_root": str(tmp_path.resolve()),
            "source": None,
            "hook_input": input_data,
            "registrations": [hook.contract() for hook in hooks],
            "transaction_id": None,
            "provider_outcomes": [],
        },
    )
    assert isinstance(preflight, dict)
    plans = preflight["provider_plan"]
    assert isinstance(plans, list)
    locked_hook_ids = {hook.hook_id for hook in locked_hooks}
    locked_receipts = [
        tmp_path
        / "goals"
        / "goal-1"
        / "post_writeback_hooks"
        / f"{plan['dispatch_id']}.json"
        for plan in plans
        if isinstance(plan, dict) and plan.get("hook_id") in locked_hook_ids
    ]
    assert len(locked_receipts) == 3

    if controlled_clock:
        # A 50ms budget loses 20ms before the first guard, then overruns.
        # Patch only this adapter's clock, not the real file-lock/runtime clock.
        ticks = iter([100.0, 100.02, 100.075, 100.075, 100.075])
        monkeypatch.setattr(
            capability_hooks, "time", SimpleNamespace(monotonic=lambda: next(ticks))
        )
        lock_spy = Mock(wraps=exclusive_file_lock)
        monkeypatch.setattr(capability_hooks, "exclusive_file_lock", lock_spy)

    with ExitStack() as held_locks:
        for receipt in locked_receipts:
            held_locks.enter_context(
                exclusive_file_lock(receipt, operation="legacy_writer_test")
            )
        started = time.monotonic()
        dispatch = dispatch_post_writeback_hooks(
            hooks,
            hook_input=input_data,
            runtime_root=tmp_path,
            lease_timeout_seconds=0.05,
        )
        elapsed = time.monotonic() - started

    if controlled_clock:
        # Neither restart a full timeout per lock nor pass a negative timeout;
        # the free sibling must still get a nonblocking acquisition attempt.
        assert [
            call.kwargs["timeout_seconds"] for call in lock_spy.call_args_list
        ] == pytest.approx([0.03, 0.0, 0.0, 0.0])
    else:
        # Coarse integration guard only: dispatch also includes runtime and I/O
        # overhead. The controlled-clock case enforces the precise budget rule.
        assert elapsed < 2.0
    assert calls == {"free": 1}
    assert dispatch["invoked_count"] == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["intents"][0]["idempotency_key"] == "periodic-report:free-z"
    assert [failure["hook_id"] for failure in dispatch["failures"]] == [
        hook.hook_id for hook in locked_hooks
    ]
    assert {failure["error_code"] for failure in dispatch["failures"]} == {
        "lock_acquire_timeout"
    }


def test_post_writeback_ts_cas_lock_timeout_preserves_free_sibling_result(
    tmp_path: Path,
) -> None:
    calls = {"a": 0, "b": 0}

    def count(name: str) -> Callable[[], None]:
        def record() -> None:
            calls[name] += 1

        return record

    hook_a = _named_hook(
        hook_id="periodic_report.a_locked",
        key="periodic-report:locked-a",
        payload={"stage_identity": "a"},
        before_return=count("a"),
    )
    hook_b = _named_hook(
        hook_id="periodic_report.b_free",
        key="periodic-report:free-b",
        payload={"stage_identity": "b"},
        before_return=count("b"),
    )
    input_data = _input()
    preflight = capability_hooks.effect_runtime_result(
        "capability_hook.post_writeback.transaction",
        {
            "schema_version": "loopx_post_writeback_hook_transaction_request_v0",
            "phase": "preflight",
            "runtime_root": str(tmp_path.resolve()),
            "source": None,
            "hook_input": input_data,
            "registrations": [hook_a.contract(), hook_b.contract()],
            "transaction_id": None,
            "provider_outcomes": [],
        },
    )
    assert isinstance(preflight, dict)
    plans = preflight["provider_plan"]
    assert isinstance(plans, list)
    locked_plan = next(
        plan
        for plan in plans
        if isinstance(plan, dict) and plan.get("hook_id") == hook_a.hook_id
    )
    receipt_dir = tmp_path / "goals" / "goal-1" / "post_writeback_hooks"
    receipt_dir.mkdir(parents=True)
    locked_receipt = receipt_dir / f"{locked_plan['dispatch_id']}.json"
    ts_lock = Path(f"{locked_receipt}.ts-effect.lock")
    ts_lock.write_text(
        json.dumps({"pid": os.getpid(), "token": "python-live-owner"}),
        encoding="utf-8",
    )

    started = time.monotonic()
    try:
        dispatch = dispatch_post_writeback_hooks(
            [hook_a, hook_b],
            hook_input=input_data,
            runtime_root=tmp_path,
        )
    finally:
        ts_lock.unlink(missing_ok=True)
    elapsed = time.monotonic() - started

    assert elapsed < 4.0
    assert calls == {"a": 1, "b": 1}
    assert dispatch["invoked_count"] == 2
    assert dispatch["intent_count"] == 1
    assert dispatch["intents"][0]["idempotency_key"] == "periodic-report:free-b"
    assert dispatch["failures"] == [
        {
            "hook_id": hook_a.hook_id,
            "capability_id": "periodic-report",
            "error_code": "journal_write_failed",
        }
    ]
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in receipt_dir.glob("*.json")
    ]
    assert [receipt["hook_id"] for receipt in receipts] == [hook_b.hook_id]


def test_periodic_report_projection_isolates_other_agent_ack_and_claimed_todos(
    tmp_path,
) -> None:
    runtime_root, registry_path = _projection_goal_fixture(
        tmp_path,
        state_text="""# Goal

## User Todo

## Agent Todo

- [ ] Analyze the next bounded family for Agent B.
  <!-- loopx:todo todo_id=todo-b status=open task_class=advancement_task claimed_by=agent-b -->
""",
        runs=[
            _successor_ack_run(
                vision_agent_id="agent-a",
                ack_agent_id="agent-b",
                frontier="frontier-b",
                obligation="replan-b",
            ),
            _closed_vision_run(agent_id="agent-a", summary="Agent A first vision."),
        ],
    )
    state_path = tmp_path / "goal.md"

    # Agent B's ACK and Agent B's claimed Todo must NOT settle Agent A's stage.
    projection_a = build_periodic_report_post_writeback_projection(
        payload={"state": {"path": str(state_path)}},
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-a",
    )
    assert "stage_completion" not in projection_a

    # Now make the Todo unclaimed and provide Agent A's own ACK in run history
    agent_a_ack = {
        **_successor_ack_run(
            vision_agent_id="agent-a",
            ack_agent_id="agent-a",
            frontier="frontier-a",
            obligation="replan-a",
        ),
        "generated_at": "2026-08-30T11:30:00Z",
    }
    runs_dir = runtime_root / "goals" / "goal-1" / "runs"
    prior_runs = [
        json.loads(row)
        for row in (runs_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(run) + "\n" for run in [agent_a_ack, *prior_runs]),
        encoding="utf-8",
    )
    state_path.write_text(
        """# Goal

## User Todo

## Agent Todo

- [ ] Analyze the next bounded family.
  <!-- loopx:todo todo_id=todo-unclaimed status=open task_class=advancement_task -->
""",
        encoding="utf-8",
    )

    projection_a_unclaimed = build_periodic_report_post_writeback_projection(
        payload={"state": {"path": str(state_path)}},
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-a",
    )
    receipt = projection_a_unclaimed["stage_completion"]
    assert receipt["transition"] == "successor_frontier_settled"
    assert receipt["agent_id"] == "agent-a"
    assert receipt["frontier_identity"] == "frontier-a"


def test_post_writeback_concurrent_exact_dispatch_lease_timeout_isolated(
    tmp_path: Path,
) -> None:
    calls = 0
    lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()
    base = _hook()

    def producer(value: object) -> dict[str, object]:
        nonlocal calls
        with lock:
            calls += 1
        started.set()
        assert release.wait(timeout=5.0)
        return dict(base.producer(value))  # type: ignore[arg-type]

    hook = PostWritebackHookRegistration(
        hook_id=base.hook_id,
        capability_id=base.capability_id,
        event_kinds=base.event_kinds,
        intent_kinds=base.intent_kinds,
        requested_read_scope=base.requested_read_scope,
        producer=producer,
    )
    results: dict[str, dict[str, object]] = {}

    def worker_slow() -> None:
        results["slow"] = dispatch_post_writeback_hooks(
            [hook], hook_input=_input(), runtime_root=tmp_path
        )

    t_slow = threading.Thread(target=worker_slow)
    t_slow.start()

    assert started.wait(timeout=5.0)

    # Second caller attempts exact dispatch with small lease timeout while producer is holding lease
    try:
        result_timeout = dispatch_post_writeback_hooks(
            [hook],
            hook_input=_input(),
            runtime_root=tmp_path,
            lease_timeout_seconds=0.05,
        )
    finally:
        release.set()

    t_slow.join(timeout=5.0)
    assert not t_slow.is_alive()
    result_slow = results["slow"]

    # Contention timeout is isolated: does not raise, preserves primary writeback, returns typed failure
    assert result_timeout["primary_writeback_preserved"] is True
    assert result_timeout["intent_count"] == 0
    assert result_timeout["invoked_count"] == 0
    assert len(result_timeout["failures"]) == 1
    assert result_timeout["failures"][0]["error_code"] == "lock_acquire_timeout"
    assert (
        result_timeout["failures"][0]["hook_id"] == "periodic_report.stage_completion"
    )

    # Slow worker completes successfully and records intent
    assert result_slow["primary_writeback_preserved"] is True
    assert result_slow["intent_count"] == 1
    assert result_slow["invoked_count"] == 1
    assert result_slow["failures"] == []

    # Total producer invocations is exactly 1
    assert calls == 1

    # Subsequent dispatch replays cleanly from terminal receipt without invoking producer
    replay = dispatch_post_writeback_hooks(
        [hook], hook_input=_input(), runtime_root=tmp_path
    )
    assert replay["intent_count"] == 1
    assert replay["invoked_count"] == 0
    assert replay["replayed_hooks"] == ["periodic_report.stage_completion"]
    assert replay["intents"] == result_slow["intents"]
    assert calls == 1


def test_post_writeback_lease_timeout_isolated_across_processes(
    tmp_path: Path,
) -> None:
    hook = _hook()
    input_data = _input()
    preflight = capability_hooks.effect_runtime_result(
        "capability_hook.post_writeback.transaction",
        {
            "schema_version": "loopx_post_writeback_hook_transaction_request_v0",
            "phase": "preflight",
            "runtime_root": str(tmp_path.resolve()),
            "source": None,
            "hook_input": input_data,
            "registrations": [hook.contract()],
            "transaction_id": None,
            "provider_outcomes": [],
        },
    )
    assert isinstance(preflight, dict)
    provider_plan = preflight["provider_plan"]
    assert isinstance(provider_plan, list)
    plan = provider_plan[0]
    assert isinstance(plan, dict)
    lock_target = (
        tmp_path
        / "goals"
        / "goal-1"
        / "post_writeback_hooks"
        / f"{plan['dispatch_id']}.json"
    )

    script = """
import sys
import time
from pathlib import Path
from loopx.file_lock import exclusive_file_lock

with exclusive_file_lock(
    Path(sys.argv[1]),
    operation="post_writeback_hook_transaction_test",
):
    print("READY", flush=True)
    time.sleep(10)
"""
    env = dict(os.environ)
    if "PYTHONPATH" not in env:
        env["PYTHONPATH"] = str(Path.cwd())

    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"

        result = dispatch_post_writeback_hooks(
            [hook],
            hook_input=input_data,
            runtime_root=tmp_path,
            lease_timeout_seconds=0.05,
        )

        assert result["primary_writeback_preserved"] is True
        assert result["intent_count"] == 0
        assert result["invoked_count"] == 0
        assert len(result["failures"]) == 1
        assert result["failures"][0]["error_code"] == "lock_acquire_timeout"
        assert result["failures"][0]["hook_id"] == "periodic_report.stage_completion"
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _split_root_registry(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime_registry = tmp_path / "runtime-registry"
    runtime_override = tmp_path / "runtime-override"
    runtime_registry.mkdir()
    runtime_override.mkdir()
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_registry),
                "goals": [{"id": "goal-1"}],
            }
        ),
        encoding="utf-8",
    )
    return registry_path, runtime_registry, runtime_override


def test_todo_complete_composition_passes_effective_runtime_root_to_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The todo complete hook composition reads machine defaults under the override."""

    import argparse

    from loopx.cli_runtime import dispatch_common_command

    registry_path, _runtime_registry, runtime_override = _split_root_registry(tmp_path)
    captured: dict[str, object] = {}

    def capture_hooks(
        *, registry_path: Path, goal_id: str, runtime_root: Path | None
    ) -> tuple[PostWritebackHookRegistration, ...]:
        captured["runtime_root"] = runtime_root
        return ()

    monkeypatch.setattr(
        "loopx.capabilities.periodic_report.post_writeback_hook"
        ".periodic_report_post_writeback_hooks_for_goal",
        capture_hooks,
    )
    monkeypatch.setattr(
        "loopx.cli_commands.todo.handle_todo_command",
        lambda *_args, **_kwargs: 0,
    )
    args = argparse.Namespace(
        command="todo",
        todo_command="complete",
        goal_id="goal-1",
        runtime_root=str(runtime_override),
    )

    result = dispatch_common_command(
        args,
        registry_path=registry_path,
        allow_missing_registry=False,
    )

    assert result == 0
    assert captured["runtime_root"] == runtime_override


def test_refresh_state_composition_passes_effective_runtime_root_to_hooks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The refresh-state hook composition reads machine defaults under the override."""

    from loopx import cli as cli_module

    registry_path, _runtime_registry, runtime_override = _split_root_registry(tmp_path)
    captured: dict[str, object] = {}

    def capture_hooks(
        *, registry_path: Path, goal_id: str, runtime_root: Path | None
    ) -> tuple[PostWritebackHookRegistration, ...]:
        captured["runtime_root"] = runtime_root
        return ()

    monkeypatch.setattr(
        cli_module,
        "periodic_report_post_writeback_hooks_for_goal",
        capture_hooks,
    )
    monkeypatch.setattr(
        cli_module,
        "handle_project_lifecycle_command",
        lambda *_args, **_kwargs: 7,
    )

    exit_code = cli_module.main(
        [
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_override),
            "refresh-state",
            "--goal-id",
            "goal-1",
        ]
    )

    assert exit_code == 7
    assert captured["runtime_root"] == runtime_override


def test_periodic_report_projection_carries_peer_lane_progress(
    tmp_path,
) -> None:
    runtime_root, registry_path = _projection_goal_fixture(
        tmp_path,
        state_text="""# Goal

## User Todo

## Agent Todo

- [x] Land the reporting lane's change.
  <!-- loopx:todo status=done task_class=advancement_task claimed_by=agent-1 updated_at=2026-08-30T10:10:00Z -->
- [x] Land a peer lane's change.
  <!-- loopx:todo status=done task_class=advancement_task claimed_by=agent-2 updated_at=2026-08-30T10:20:00Z -->
- [ ] Continue the reporting lane's next step.
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-1 -->
""",
        runs=[
            _successor_ack_run(),
            _closed_vision_run(),
        ],
    )

    projection = build_periodic_report_post_writeback_projection(
        payload={"state": {"path": str(tmp_path / "goal.md")}},
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-1",
    )

    items = projection["project_progress"]["items"]
    assert [(item["content_kind"], item["title"]) for item in items] == [
        ("outcome", "Land the reporting lane's change."),
        ("outcome", "Land a peer lane's change."),
        ("next_action", "Next action"),
    ]


def test_periodic_report_projection_drops_facts_a_peer_already_delivered(
    tmp_path,
) -> None:
    state_text = """# Goal

## User Todo

## Agent Todo

- [x] Land the first bounded change.
  <!-- loopx:todo status=done task_class=advancement_task claimed_by=agent-1 updated_at=2026-08-30T10:10:00Z -->
- [x] Land the second bounded change.
  <!-- loopx:todo status=done task_class=advancement_task claimed_by=agent-1 updated_at=2026-08-30T10:20:00Z -->
- [ ] Continue the next bounded change.
  <!-- loopx:todo status=open task_class=advancement_task claimed_by=agent-1 -->
"""
    runtime_root, registry_path = _projection_goal_fixture(
        tmp_path,
        state_text=state_text,
        runs=[
            _successor_ack_run(),
            _closed_vision_run(),
        ],
    )

    def projection() -> dict[str, object]:
        return build_periodic_report_post_writeback_projection(
            payload={"state": {"path": str(tmp_path / "goal.md")}},
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id="goal-1",
            agent_id="agent-1",
        )

    first = projection()
    facts = first["project_progress"]["items"]
    assert len(facts) == 3

    candidate = build_periodic_report_publication_candidate(
        goal_id="goal-1",
        agent_id="agent-2",
        generation_id="generation-peer-delivery",
        trigger_receipt={"coalesced_trigger_ids": ["trigger-peer-delivery"]},
        facts=facts,
        baseline=None,
    )
    commit_periodic_report_publication_cursor(
        runtime_root=runtime_root,
        candidate=candidate,
        publication_id="goal-channel:peer-delivery",
        delivered_at="2026-08-30T12:00:00Z",
        covered_until="2026-08-30T11:00:00Z",
    )

    assert "project_progress" not in projection()
