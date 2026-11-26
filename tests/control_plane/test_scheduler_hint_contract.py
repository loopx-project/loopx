"""Contract, typing, and boundary tests for Work Control scheduler hints."""

from __future__ import annotations

import ast
import base64
from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import tokenize
from types import MappingProxyType
import zlib

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.scheduler import scheduler_hint as sh
from loopx.control_plane.scheduler.arbitration import (
    SchedulerArbitration,
    SchedulerDisposition,
)
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _any_count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "Any" for alias in node.names)
        )
    )


def _comment_type_ignore_count(source: str) -> int:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return sum(
        1
        for tok in tokens
        if tok.type == tokenize.COMMENT and "type: ignore" in tok.string
    )


def test_scheduler_hint_static_hygiene_and_exports() -> None:
    sh_path = REPO_ROOT / "loopx/control_plane/scheduler/scheduler_hint.py"
    test_path = Path(__file__).resolve()

    sh_src = sh_path.read_text(encoding="utf-8")
    test_src = test_path.read_text(encoding="utf-8")

    assert len(sh_src.splitlines()) <= 550
    assert len(test_src.splitlines()) < 550
    for src in (sh_src, test_src):
        assert _any_count(src) == 0
        assert _comment_type_ignore_count(src) == 0

    assert sh.SchedulerTimestampFact is not None
    assert sh.SchedulerHostFollowupGoalRef is not None


def test_timestamp_facts_deduplicates_duplicate_and_empty_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 11, 26, 8, 0, tzinfo=UTC)
    monkeypatch.setattr(sh, "now_utc", lambda: fixed_now)

    real_parse = sh.parse_timestamp
    parse_calls: list[object] = []

    def counting_parse(value: object) -> datetime | None:
        parse_calls.append(value)
        return real_parse(value)

    monkeypatch.setattr(sh, "parse_timestamp", counting_parse)

    shared_ts = "2026-11-26T08:05:00Z"
    other_ts = "2026-11-26T08:10:00Z"
    payload: dict[str, object] = {
        "goal_id": "g-ts",
        "agent_identity": {"agent_id": "agent-ts"},
        "heartbeat_recommendation": {"recommended_mode": "quiet_wait"},
        "agent_todo_summary": {
            "frontier_deadline": {"next_due_at": shared_ts, "blocked_retry": {"due_at": shared_ts}},
            "blocked_retry_wait": {"due_at": shared_ts},
            "monitor_open_items": [
                {
                    "todo_id": "t1",
                    "next_due_at": shared_ts,
                    "expires_at": None,
                    "last_checked_at": "",
                },
                {
                    "todo_id": "t2",
                    "next_due_at": shared_ts,
                    "expires_at": other_ts,
                    "last_checked_at": shared_ts,
                },
            ],
            "current_agent_claimed_monitor_items": [
                {"todo_id": "t1", "next_due_at": shared_ts, "expires_at": other_ts}
            ],
        },
    }
    state: dict[str, object] = {
        "host_update_failure": {"failed_at": shared_ts},
        "host_update_failures": [{"failed_at": other_ts}, {"failed_at": None}],
    }
    captured_params: dict[str, object] = {}
    real_effect = sh.effect_runtime_result

    def capture_effect(
        method: str,
        request: Mapping[str, object],
        *,
        timeout: float = 30.0,
        retry_safe: bool = True,
        large_local_snapshot: bool = False,
    ) -> object:
        captured_params.update(request)
        return real_effect(
            method,
            request,
            timeout=timeout,
            retry_safe=retry_safe,
            large_local_snapshot=large_local_snapshot,
        )

    monkeypatch.setattr(sh, "effect_runtime_result", capture_effect)
    sh.build_scheduler_hint(
        payload,
        arbitration=SchedulerArbitration(
            SchedulerDisposition.QUIET_WAIT, "blocked_wait", "quiet_wait"
        ),
        codex_app_scheduler_state=state,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "codex_app_heartbeat"
        ),
    )

    assert parse_calls == [shared_ts, other_ts]
    timestamps = captured_params["timestamps"]
    assert timestamps == {
        shared_ts: {"iso": "2026-11-26T08:05:00+00:00", "utc": shared_ts, "after_seconds": 300.0},
        other_ts: {"iso": "2026-11-26T08:10:00+00:00", "utc": other_ts, "after_seconds": 600.0},
    }


def test_transport_args_and_followup_key_presence_and_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Missing cli_args key leaves packet untouched; non-list raises RuntimeError.
    untouched = sh._encode_followup_args({"action": "wait"}, before=None)
    assert untouched == {"action": "wait"}
    with pytest.raises(RuntimeError, match="must be a list"):
        sh._encode_followup_args({"cli_args": "bad"}, before=None)
    with pytest.raises(RuntimeError, match="shape mismatch"):
        sh._encode_followup_args(
            {
                "cli_args": [
                    {
                        "kind": "scheduler_host_followup",
                        "host_facts": {},
                        "use_current_hint": 1,
                    }
                ]
            },
            before=None,
        )

    # None host_facts yields []; empty mapping {} is still Mapping and encodes chunked args.
    assert sh._scheduler_host_followup_transport_args(None, before=None, use_current_hint=False) == []
    empty_encoded = sh._scheduler_host_followup_transport_args(
        {},
        before={"should_run": True},
        use_current_hint=True,
        goal_ref={"goal_id": "g1", "goal_instance_id": "gi1"},
    )
    assert empty_encoded[0] == sh.SCHEDULER_HOST_FACTS_CHUNK_FLAG
    raw_b64 = "".join(empty_encoded[1::2])
    decoded = json.loads(zlib.decompress(base64.b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))))
    assert decoded["goal_ref"] == {"goal_id": "g1", "goal_instance_id": "gi1"}
    assert decoded["host_facts"] == {}
    assert decoded["use_current_hint"] is True

    # Non-native CLI args bypass the native bound; native args over limits fail closed.
    long_plain = ["x"] * 70
    assert sh._bounded_scheduler_followup_cli_args(long_plain, native_args=[]) == long_plain
    with pytest.raises(ValueError, match="exceed the transport bound"):
        sh._bounded_scheduler_followup_cli_args(long_plain, native_args=["--scheduler-host-facts-chunk"])
    with pytest.raises(ValueError, match="exceed the native CLI transport bound"):
        incompressible = "".join(
            hashlib.sha256(f"{i}".encode()).hexdigest() for i in range(64)
        )
        sh._scheduler_host_followup_transport_args(
            {"blob": incompressible},
            before=None,
            use_current_hint=False,
        )

    monkeypatch.setattr(sh, "loopx_cli_argv", lambda: ["/usr/bin/python3", "-m", "loopx.entrypoint"])
    routed = sh._encode_followup_args({"cli_args": ["quota", "should-run"]}, before=None)
    assert routed["cli_bin"] == "/usr/bin/python3"
    assert routed["cli_args"] == ["-m", "loopx.entrypoint", "quota", "should-run"]

    monkeypatch.setattr(
        sh,
        "effect_runtime_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(EffectRuntimeRejected("invalid hint")),
    )
    with pytest.raises(ValueError, match="invalid hint"):
        sh._project_hint("hint", {})

    monkeypatch.setattr(
        sh,
        "effect_runtime_result",
        lambda *args, **kwargs: {"schema_version": "wrong", "hint": {}},
    )
    with pytest.raises(RuntimeError, match="shape mismatch"):
        sh._project_hint("hint", {})


def test_projection_facts_empty_containers_and_ack_failure_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = sh._projection_facts(
        {
            "goal_id": "",
            "agent_identity": {"agent_id": ""},
            "capability_gate": {"available": "not-a-list"},
            "interaction_contract": None,
            "agent_todo_summary": {"monitor_open_items": "not-a-list"},
        }
    )
    assert facts["goal_id"] is None
    assert facts["agent_identity"] == {"agent_id": None}
    assert facts["capability_gate"] == {"available": []}
    assert facts["interaction_contract"] == {"agent_channel": {}}
    assert facts["agent_todo_summary"] == {
        "blocked_retry_wait": None,
        "frontier_deadline": None,
        "current_agent_claimed_monitor_items": [],
        "monitor_open_items": [],
    }
    assert sh._capability_values(None) is None
    assert sh._capability_values(frozenset({"a"})) == []
    assert sh._capability_values([" cap-a ", ""]) == ["cap-a", ""]

    captured: list[tuple[str, dict[str, object]]] = []

    def fake_effect(
        method: str, request: dict[str, object], **kwargs: object
    ) -> dict[str, object]:
        del method, kwargs
        captured.append((str(request.get("operation")), dict(request)))
        return {
            "schema_version": "scheduler_hint_projection_v0",
            "hint": {"cli_args": ["quota", "apply"], "operation": request.get("operation")},
        }

    monkeypatch.setattr(sh, "effect_runtime_result", fake_effect)
    monkeypatch.setattr(sh, "loopx_cli_argv", lambda: ["loopx"])

    ack = sh.build_app_automation_scheduler_ack_hint(
        goal_id=" g1 ",
        agent_id=" a1 ",
        applied_rrule="FREQ=HOURLY",
        reset_token=None,
        identity_signature=" sig ",
        available_capabilities=["cap"],
        host_match_observed=True,
        scheduler_host_facts={},
        goal_ref={"goal_id": "g1", "goal_instance_id": "gi1"},
    )
    fail = sh.build_app_automation_scheduler_failure_hint(
        goal_id="g1",
        agent_id="a1",
        failed_rrule="FREQ=MINUTELY",
        observed_host_rrule="FREQ=HOURLY",
        scheduler_before={"should_run": False},
    )
    assert ack["operation"] == "ack"
    assert fail["operation"] == "failure"
    assert captured[0][1]["goal_id"] == "g1"
    assert captured[0][1]["reset_token"] == ""
    assert captured[0][1]["host_match_observed"] is True
    assert captured[0][1]["available_capabilities"] == ["cap"]


def test_timestamp_facts_rejects_non_datetime_and_supports_mapping_proxy() -> None:
    with pytest.raises(TypeError, match="datetime"):
        sh._timestamp_facts([], "not-a-datetime")

    context_proxy = MappingProxyType(
        scheduler_execution_context_for_runtime_profile("generic_cli").projection()
    )
    hint = sh.build_scheduler_hint(
        {"goal_id": "g-proxy", "effective_action": "normal_run"},
        arbitration=SchedulerArbitration(
            SchedulerDisposition.ACTIVE_WORK, "active_work", "normal_run"
        ),
        scheduler_execution_context=context_proxy,
    )
    assert hint["action"] == "run_now"
