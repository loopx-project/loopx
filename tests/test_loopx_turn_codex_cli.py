from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.codex_cli import (
    CODEX_CLI_SESSION_SCHEMA_VERSION,
    _diagnostic_failure_category,
    _event_failure_categories,
    _event_failure_category,
    _prompt,
    _select_failure_category,
    codex_cli_result_schema,
    codex_cli_session_binding,
    load_codex_cli_session,
    run_codex_cli_host,
)
from loopx.control_plane.turn_driver.executor import BuiltInHostError
from loopx.control_plane.turn_driver.subagent_execution_topology import (
    OPAQUE_REF_PATTERN,
)


FAILURE_ENVELOPE_FIXTURES = (
    Path(__file__).parent / "fixtures" / "codex_failure_envelopes.json"
)


def _request(
    *,
    turn_key: str = "sha256:" + "a" * 64,
    session_action: str = "start_new",
) -> dict[str, object]:
    return {
        "schema_version": "loopx_turn_host_request_v0",
        "turn_key": turn_key,
        "route": "ready_for_host",
        "session": {
            "schema_version": "loopx_turn_session_binding_v0",
            "action": session_action,
        },
        "turn_envelope": {
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "action": {
                "selected_todo": {
                    "todo_id": "todo_fixture0001",
                    "text": "Advance one public fixture",
                }
            },
        },
        "result_contract": {
            "schema_version": "loopx_turn_result_v0",
            "completed_phases": ["host_execute", "typed_result"],
        },
    }


def _fake_codex(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "fake-codex"
    log_path = tmp_path / "codex-argv.jsonl"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import re
import subprocess
import sys
import time

args = sys.argv[1:]
prompt = sys.stdin.read()
log = pathlib.Path(os.environ["FAKE_CODEX_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
turn_key = re.search(r'"turn_key":"([^"]+)"', prompt).group(1)
print(json.dumps({
    "type": "thread.started",
    "thread_id": "session-fixture-0001",
    "raw_trajectory": "must-not-persist",
    "private_material": "must-not-persist"
}), flush=True)
if os.environ.get("FAKE_CODEX_FAIL") == "1":
    failure_event = os.environ.get("FAKE_CODEX_FAILURE_EVENT")
    failure_stderr = os.environ.get("FAKE_CODEX_FAILURE_STDERR")
    stderr_first = os.environ.get("FAKE_CODEX_FAILURE_STDERR_FIRST") == "1"
    if failure_stderr and stderr_first:
        print(failure_stderr, file=sys.stderr, flush=True)
        time.sleep(0.05)
    if failure_event:
        print(failure_event, flush=True)
    if failure_stderr and not stderr_first:
        print(failure_stderr, file=sys.stderr, flush=True)
    hold_pipe_seconds = os.environ.get("FAKE_CODEX_HOLD_PIPE_SECONDS")
    if hold_pipe_seconds:
        subprocess.Popen([
            sys.executable,
            "-c",
            f"import time; time.sleep({float(hold_pipe_seconds)!r})",
        ])
    if os.environ.get("FAKE_CODEX_FAILURE_CATEGORY") == "model":
        print("This model requires a newer version of Codex.", file=sys.stderr)
    if os.environ.get("FAKE_CODEX_FAILURE_CATEGORY") == "session":
        print("Session not found.", file=sys.stderr)
    if os.environ.get("FAKE_CODEX_FAILURE_CATEGORY") == "capacity-stderr":
        print("Selected model is at capacity. Please try a different model.", file=sys.stderr)
    if os.environ.get("FAKE_CODEX_FAILURE_CATEGORY") == "capacity-event":
        print(json.dumps({
            "type": "error",
            "message": "Selected model is at capacity. Please try a different model.",
            "private_material": "must-not-persist"
        }), flush=True)
    raise SystemExit(9)
if os.environ.get("FAKE_CODEX_SLEEP"):
    time.sleep(float(os.environ["FAKE_CODEX_SLEEP"]))
output_path = pathlib.Path(args[args.index("--output-last-message") + 1])
output_path.write_text(json.dumps({
    "schema_version": "loopx_turn_result_v0",
    "turn_key": turn_key,
    "result_kind": "validated_progress",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": "fixture_progress",
    "recommended_action": "Continue the public fixture",
    "next_action": "Run the next public fixture check",
    "delivery_batch_scale": "implementation",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": "The fixture objective remains unchanged.",
    "summary": "One public fixture advanced."
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, log_path


def _capture_fake_codex_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failure_category: str | None = None,
    failure_event: dict[str, object] | None = None,
    failure_stderr: str | None = None,
    stderr_first: bool = False,
) -> tuple[BuiltInHostError, Path]:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    if failure_category is not None:
        monkeypatch.setenv("FAKE_CODEX_FAILURE_CATEGORY", failure_category)
    if failure_event is not None:
        monkeypatch.setenv("FAKE_CODEX_FAILURE_EVENT", json.dumps(failure_event))
    if failure_stderr is not None:
        monkeypatch.setenv("FAKE_CODEX_FAILURE_STDERR", failure_stderr)
    if stderr_first:
        monkeypatch.setenv("FAKE_CODEX_FAILURE_STDERR_FIRST", "1")
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    request = _request()

    with pytest.raises(BuiltInHostError) as exc_info:
        run_codex_cli_host(
            request,
            runtime_root=runtime_root,
            project=project,
            codex_bin=str(executable),
            timeout_seconds=5,
        )
    return exc_info.value, runtime_root


def test_codex_cli_result_schema_requires_only_bounded_contract_fields() -> None:
    schema = codex_cli_result_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "raw_trajectory" not in schema["properties"]
    assert "stdout" not in schema["properties"]
    assert "child_execution_receipts" not in schema["properties"]
    request = _request()
    request["subagent_execution_topology"] = {
        "schema_version": "subagent_execution_topology_v0"
    }
    enabled_schema = codex_cli_result_schema(request)
    receipt_properties = enabled_schema["properties"]["child_execution_receipts"][
        "items"
    ]["properties"]
    assert "task_packet_digest" in receipt_properties
    assert receipt_properties["context_mode"]["enum"] == [
        "forked_snapshot",
        "fresh",
        "resume",
    ]
    assert receipt_properties["runtime_id"] == {
        "type": "string",
        "enum": ["codex-cli"],
    }
    assert (
        receipt_properties["evidence_refs"]["items"]["pattern"]
        == OPAQUE_REF_PATTERN
    )
    assert receipt_properties["evidence_refs"]["minItems"] == 1
    assert {
        field: schema["properties"][field]["maxLength"]
        for field in (
            "classification",
            "recommended_action",
            "next_action",
            "vision_unchanged_reason",
            "summary",
        )
    } == {
        "classification": 120,
        "recommended_action": 1_200,
        "next_action": 1_200,
        "vision_unchanged_reason": 240,
        "summary": 400,
    }


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        (
            "Selected model is at capacity. Please try a different model.",
            "provider_capacity",
        ),
        ("Server overloaded; retry later.", "provider_overloaded"),
        ("The service is overloaded.", "provider_overloaded"),
        ("Too many requests; retry later.", "rate_limited"),
        ("Quota exceeded. Check your plan and billing details.", "quota_exhausted"),
    ],
)
def test_codex_cli_diagnostic_fallback_keeps_failure_classes_distinct(
    diagnostic: str,
    expected: str,
) -> None:
    assert _diagnostic_failure_category(diagnostic) == expected


def test_codex_cli_failure_envelopes_match_real_protocol_shapes() -> None:
    fixture = json.loads(FAILURE_ENVELOPE_FIXTURES.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == "loopx_codex_failure_envelopes_v1"
    for case in fixture["cases"]:
        assert case["source"]
        assert _event_failure_category(case["event"]) == case["expected"], case[
            "name"
        ]


def test_codex_cli_unknown_structured_code_blocks_message_fallback() -> None:
    event = {
        "type": "turn.failed",
        "error": {
            "code": "unknown_provider_code",
            "message": "Selected model is at capacity.",
        },
    }

    assert _event_failure_category(event) == "unknown"


@pytest.mark.parametrize(
    ("outer_code", "inner_code"),
    [
        ("future_provider_failure", "rate_limit_exceeded"),
        ("rate_limit_exceeded", "future_provider_failure"),
    ],
)
def test_codex_cli_host_fails_closed_on_mixed_structured_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_code: str,
    inner_code: str,
) -> None:
    failure_event: dict[str, object] = {
        "type": "response.failed",
        "code": outer_code,
        "response": {"error": {"code": inner_code}},
    }

    assert _event_failure_categories(failure_event) == ("unknown", None)

    error, _runtime_root = _capture_fake_codex_failure(
        tmp_path,
        monkeypatch,
        failure_event=failure_event,
    )

    assert error.reason == "codex_cli_unknown"
    assert error.failure_kind == "unknown"
    assert error.recovery_kind is None


@pytest.mark.parametrize(
    "categories",
    [
        ["unknown", "rate_limited"],
        ["rate_limited", "unknown"],
    ],
)
def test_failure_category_reduction_is_order_independent(
    categories: list[str],
) -> None:
    assert _select_failure_category(categories) == "unknown"


def test_codex_cli_specific_quota_code_wins_over_http_429() -> None:
    event = {
        "type": "response.failed",
        "response": {
            "error": {
                "code": "insufficient_quota",
                "httpStatusCode": 429,
                "message": "Rate limit reached.",
            }
        },
    }

    assert _event_failure_category(event) == "quota_exhausted"


def test_codex_cli_prompt_isolates_subagent_instructions_to_enabled_request() -> None:
    request = _request()

    disabled_prompt = _prompt(request)
    assert "spawn_agent" not in disabled_prompt
    assert "subagent_execution_topology" not in disabled_prompt

    request["subagent_execution_topology"] = {
        "schema_version": "subagent_execution_topology_v0"
    }
    prompt = _prompt(request)
    assert "execute the separate host_adapter projection" in prompt
    assert "fresh maps to fork_context=false" in prompt
    assert "forked_snapshot maps to fork_context=true" in prompt
    assert "never infer native arguments inside the generic LoopX task packet" in prompt
    assert "set runtime_id to the stable host id codex-cli" in prompt
    assert "Never use an executable, workspace, session-file" in prompt
    assert "opaque evidence_refs such as artifact:child-result" in prompt


@pytest.mark.parametrize("sandbox", ["read-only", "workspace-write", "danger-full-access"])
def test_codex_cli_host_starts_then_resumes_opaque_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sandbox: str,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    first_request = _request()

    first = run_codex_cli_host(
        first_request,
        runtime_root=runtime_root,
        project=project,
        codex_bin=str(executable),
        sandbox=sandbox,
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
        timeout_seconds=5,
    )
    with pytest.raises(RuntimeError, match="binding changed after planning"):
        run_codex_cli_host(
            _request(turn_key="sha256:" + "c" * 64),
            runtime_root=runtime_root,
            project=project,
            codex_bin=str(executable),
            timeout_seconds=5,
        )
    second_request = _request(
        turn_key="sha256:" + "b" * 64,
        session_action="resume",
    )
    second = run_codex_cli_host(
        second_request,
        runtime_root=runtime_root,
        project=project,
        codex_bin=str(executable),
        sandbox=sandbox,
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
        timeout_seconds=5,
    )

    assert first["turn_key"] == first_request["turn_key"]
    assert second["turn_key"] == second_request["turn_key"]
    argv_rows = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "resume" not in argv_rows[0]
    assert "resume" in argv_rows[1]
    assert "session-fixture-0001" in argv_rows[1]
    for argv in argv_rows:
        assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
        config_values = [
            argv[index + 1]
            for index, value in enumerate(argv)
            if value == "-c"
        ]
        assert 'model_reasoning_effort="xhigh"' in config_values
    resume_argv = argv_rows[1]
    assert resume_argv[resume_argv.index("-c") + 1] == (
        f'sandbox_mode="{sandbox}"'
    )
    assert resume_argv[resume_argv.index("-C") + 1] == str(project)
    assert resume_argv.index("-C") < resume_argv.index("resume")

    envelope = first_request["turn_envelope"]
    assert isinstance(envelope, dict)
    binding = codex_cli_session_binding(runtime_root, envelope)
    assert binding == {
        "schema_version": "loopx_turn_session_binding_v0",
        "goal_id": "fixture-goal",
        "agent_id": "codex-fixture",
        "todo_id": "todo_fixture0001",
    }
    lineage = {key: binding[key] for key in ("goal_id", "agent_id", "todo_id")}
    session = load_codex_cli_session(runtime_root, lineage=lineage)
    assert session is not None
    assert session["schema_version"] == CODEX_CLI_SESSION_SCHEMA_VERSION
    assert set(session) == {
        "schema_version",
        "goal_id",
        "agent_id",
        "todo_id",
        "host",
        "session_id",
    }
    session_paths = list(runtime_root.glob("goals/*/turn-sessions/*.json"))
    assert len(session_paths) == 1
    assert stat.S_IMODE(session_paths[0].stat().st_mode) == 0o600
    persisted = session_paths[0].read_text(encoding="utf-8")
    assert "raw_trajectory" not in persisted
    assert "private_material" not in persisted


def test_codex_cli_host_rejects_unknown_reasoning_effort_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ValueError, match="unsupported reasoning effort"):
        run_codex_cli_host(
            _request(),
            runtime_root=tmp_path / "runtime",
            project=project,
            codex_bin=str(executable),
            reasoning_effort="turbo",
            timeout_seconds=5,
        )

    assert not log_path.exists()


def test_codex_cli_host_fresh_iteration_ignores_stored_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    run_codex_cli_host(
        _request(),
        runtime_root=runtime_root,
        project=project,
        codex_bin=str(executable),
        timeout_seconds=5,
    )

    fresh_request = _request(turn_key="sha256:" + "e" * 64)
    fresh_request["session"]["context_policy"] = {
        "schema_version": "loopx_iteration_context_policy_v0",
        "mode": "fresh",
        "scope": "iteration",
    }
    second = run_codex_cli_host(
        fresh_request,
        runtime_root=runtime_root,
        project=project,
        codex_bin=str(executable),
        timeout_seconds=5,
    )

    assert second["turn_key"] == fresh_request["turn_key"]
    argv_rows = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(argv_rows) == 2
    assert all("resume" not in argv for argv in argv_rows)


def test_codex_cli_host_ignores_legacy_session_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    request = _request()
    run_codex_cli_host(
        request,
        runtime_root=runtime_root,
        project=project,
        codex_bin=str(executable),
        timeout_seconds=5,
    )
    session_path = next(runtime_root.glob("goals/*/turn-sessions/*.json"))
    legacy = json.loads(session_path.read_text(encoding="utf-8"))
    legacy["schema_version"] = "loopx_codex_cli_session_v0"
    session_path.write_text(json.dumps(legacy), encoding="utf-8")

    envelope = request["turn_envelope"]
    assert isinstance(envelope, dict)
    assert codex_cli_session_binding(runtime_root, envelope) is None


def test_codex_cli_host_preserves_session_after_failed_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    request = _request()

    with pytest.raises(RuntimeError, match="codex_cli_exit_nonzero"):
        run_codex_cli_host(
            request,
            runtime_root=runtime_root,
            project=project,
            codex_bin=str(executable),
            timeout_seconds=5,
        )

    envelope = request["turn_envelope"]
    assert isinstance(envelope, dict)
    assert codex_cli_session_binding(runtime_root, envelope) is not None


def test_codex_cli_host_preserves_observed_session_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    monkeypatch.setenv("FAKE_CODEX_SLEEP", "2")
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    request = _request()

    with pytest.raises(RuntimeError, match="codex_cli_timeout"):
        run_codex_cli_host(
            request,
            runtime_root=runtime_root,
            project=project,
            codex_bin=str(executable),
            timeout_seconds=0.1,
        )

    envelope = request["turn_envelope"]
    assert isinstance(envelope, dict)
    assert codex_cli_session_binding(runtime_root, envelope) is not None


def test_codex_cli_host_classifies_failure_without_persisting_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    monkeypatch.setenv("FAKE_CODEX_FAILURE_CATEGORY", "model")
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(
        RuntimeError,
        match="codex_cli_model_requires_newer_codex",
    ):
        run_codex_cli_host(
            _request(),
            runtime_root=runtime_root,
            project=project,
            codex_bin=str(executable),
            timeout_seconds=5,
        )

    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in runtime_root.rglob("*.json")
    )
    assert "requires a newer version" not in persisted
    envelope = _request()["turn_envelope"]
    assert isinstance(envelope, dict)
    assert codex_cli_session_binding(runtime_root, envelope) is None

    monkeypatch.delenv("FAKE_CODEX_FAIL")
    monkeypatch.delenv("FAKE_CODEX_FAILURE_CATEGORY")
    recovered = run_codex_cli_host(
        _request(turn_key="sha256:" + "d" * 64),
        runtime_root=runtime_root,
        project=project,
        codex_bin=str(executable),
        timeout_seconds=5,
    )
    assert recovered["result_kind"] == "validated_progress"
    argv_rows = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "resume" not in argv_rows[1]


@pytest.mark.parametrize("source", ["capacity-stderr", "capacity-event"])
def test_codex_cli_host_classifies_provider_capacity_as_retryable_without_prose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    error, runtime_root = _capture_fake_codex_failure(
        tmp_path,
        monkeypatch,
        failure_category=source,
    )

    assert error.reason == "codex_cli_provider_capacity"
    assert error.failure_kind == "provider_capacity"
    assert error.recovery_kind == "resume_session"
    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in runtime_root.rglob("*.json")
    )
    assert "at capacity" not in persisted
    assert "private_material" not in persisted


@pytest.mark.parametrize(
    ("event", "expected_reason", "expected_kind", "expected_recovery"),
    [
        (
            {
                "type": "turn.failed",
                "error": {"codexErrorInfo": "serverOverloaded"},
            },
            "codex_cli_provider_overloaded",
            "provider_overloaded",
            "resume_session",
        ),
        (
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Quota exceeded.",
                    }
                },
            },
            "codex_cli_rate_limited",
            "rate_limited",
            "resume_session",
        ),
        (
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "insufficient_quota",
                        "message": "Rate limit reached.",
                    }
                },
            },
            "codex_cli_quota_exhausted",
            "quota_exhausted",
            None,
        ),
        (
            {
                "type": "error",
                "error": {"httpStatusCode": 429, "message": "opaque"},
            },
            "codex_cli_rate_limited",
            "rate_limited",
            "resume_session",
        ),
        (
            {
                "type": "turn.failed",
                "error": {
                    "code": "future_provider_failure",
                    "message": "Selected model is at capacity.",
                },
            },
            "codex_cli_unknown",
            "unknown",
            None,
        ),
    ],
)
def test_codex_cli_host_preserves_structured_failure_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, object],
    expected_reason: str,
    expected_kind: str,
    expected_recovery: str | None,
) -> None:
    error, _runtime_root = _capture_fake_codex_failure(
        tmp_path,
        monkeypatch,
        failure_event=event,
    )

    assert error.reason == expected_reason
    assert error.failure_kind == expected_kind
    assert error.recovery_kind == expected_recovery


def test_codex_cli_host_prefers_structured_code_across_output_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = {
        "type": "response.failed",
        "response": {
            "error": {
                "code": "insufficient_quota",
                "message": "opaque structured failure",
            }
        },
    }
    error, _runtime_root = _capture_fake_codex_failure(
        tmp_path,
        monkeypatch,
        failure_event=event,
        failure_stderr="Too many requests; retry later.",
        stderr_first=True,
    )

    assert error.reason == "codex_cli_quota_exhausted"
    assert error.failure_kind == "quota_exhausted"
    assert error.recovery_kind is None


def test_codex_cli_host_fails_closed_when_output_observation_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.turn_driver.codex_cli.OUTPUT_DRAIN_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setenv("FAKE_CODEX_HOLD_PIPE_SECONDS", "0.2")
    error, _runtime_root = _capture_fake_codex_failure(
        tmp_path,
        monkeypatch,
        failure_event={
            "type": "response.failed",
            "response": {"error": {"code": "rate_limit_exceeded"}},
        },
    )

    assert error.reason == "codex_cli_unknown"
    assert error.failure_kind == "unknown"
    assert error.recovery_kind is None


def test_codex_cli_host_discards_missing_resume_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, log_path = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log_path))
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    first_request = _request()
    run_codex_cli_host(
        first_request,
        runtime_root=runtime_root,
        project=project,
        codex_bin=str(executable),
        timeout_seconds=5,
    )

    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    monkeypatch.setenv("FAKE_CODEX_FAILURE_CATEGORY", "session")
    with pytest.raises(RuntimeError, match="codex_cli_session_missing"):
        run_codex_cli_host(
            _request(
                turn_key="sha256:" + "f" * 64,
                session_action="resume",
            ),
            runtime_root=runtime_root,
            project=project,
            codex_bin=str(executable),
            timeout_seconds=5,
        )

    envelope = first_request["turn_envelope"]
    assert isinstance(envelope, dict)
    assert codex_cli_session_binding(runtime_root, envelope) is None


def test_public_e2e_smoke_runs_n_transactions_on_one_session() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "examples" / "loopx-turn-codex-cli-e2e-smoke.py"),
            "--turn-count",
            "3",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["requested_turn_count"] == 3
    assert payload["observed_turn_count"] == 3
    assert payload["committed_turn_count"] == 3
    assert [turn["turn_number"] for turn in payload["turns"]] == [1, 2, 3]
    assert payload["session_actions"] == ["start_new", "resume", "resume"]
    assert payload["session_resumed"] is True
    assert all(turn["marker_valid"] for turn in payload["turns"])
    assert payload["marker_valid"] is True
    assert payload["quota_slot_spend_count"] == 3
    assert payload["replay_effects"] == {
        "host_invoked": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
        "state_written": False,
    }


def test_checkpointed_write_approval_is_scoped_and_absent_by_default():
    request = _request()
    assert "It satisfies the write approval requirement" not in _prompt(request)
    request["turn_envelope"]["boundary"] = {
        "requires_parent_approval": ["write", "publish", "production-action"],
        "checkpointed_boundary_authority": {
            "schema_version": "checkpointed_boundary_authority_v0",
            "active_count": 1,
            "active_write_scope": ["src/**"],
        },
    }
    prompt = _prompt(request)
    assert "only within its active_write_scope" in prompt
    assert "publish, and production actions retain their gates" in prompt
