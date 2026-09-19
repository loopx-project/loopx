from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx import dsh_goal_mode
from loopx.control_plane.quota.turn_envelope import (
    turn_envelope_action_signature_document,
)
from loopx.control_plane.turn_driver.host_failure import (
    BuiltInHostError,
    build_host_failure_record,
)
from loopx.dsh_goal_mode import host_failure_map, turn_host_adapter

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPAT_LAUNCHER = REPO_ROOT / "scripts" / "dsh_turn_host_adapter.py"
TURN_KEY = "sha256:" + "0" * 64


def _signed_request(
    *,
    primary_action: str = "Do the signed thing.",
    goal_id: str = "g",
    agent_id: str = "a",
    todo_id: str | None = None,
) -> dict:
    request = {
        "schema_version": dsh_goal_mode.LOOPX_TURN_HOST_REQUEST_SCHEMA,
        "turn_key": TURN_KEY,
        "route": "primary_delivery",
        "session": {"goal_id": goal_id, "agent_id": agent_id},
        "turn_envelope": {
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "action": {
                "recommended_action": "legacy action must not win",
                "primary_action": primary_action,
                "must_attempt": True,
            },
            "required_reads": [
                {
                    "kind": "command",
                    "command": "git status --short",
                    "reason": "establish the workspace baseline",
                }
            ],
            "boundary": {
                "write_scope": ["docs/**", "tests/**"],
                "workspace_guard": {
                    "schema_version": "workspace_guard_v0",
                    "status": "ready",
                    "action": "continue",
                },
            },
            "action_signature": {
                "schema_version": "loopx_action_signature_v0",
                "matches": True,
            },
        },
        "result_contract": {
            "schema_version": dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA,
            "completed_phases": list(dsh_goal_mode.COMPLETED_PHASES),
        },
    }
    if todo_id is not None:
        request["turn_envelope"]["action"]["selected_todo"] = {"todo_id": todo_id}
    signature = turn_envelope_action_signature_document(
        request["turn_envelope"]
    )
    signature_hash = "sha256:" + sha256(
        json.dumps(
            signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    request["turn_envelope"]["action_signature"].update(
        {
            "source_hash": signature_hash,
            "envelope_hash": signature_hash,
        }
    )
    return request


def _lineage_request(
    *, goal_id: object = None, agent_id: object = None, todo_id: object = None
) -> dict:
    envelope: dict[str, object] = {"action": {}}
    if goal_id is not None:
        envelope["goal_id"] = goal_id
    if agent_id is not None:
        envelope["agent_id"] = agent_id
    if todo_id is not None:
        envelope["action"] = {"selected_todo": {"todo_id": todo_id}}
    return {"turn_envelope": envelope}


def test_dsh_session_id_uses_a_versioned_lineage_digest() -> None:
    # Hyphen-delimited fields are ambiguous: both inputs produced
    # "goal-a-worker-todo_b-todo_abc" under the historical naming scheme.
    first = _lineage_request(
        goal_id="goal-a", agent_id="worker", todo_id="todo_b-todo_abc"
    )
    second = _lineage_request(
        goal_id="goal-a-worker", agent_id="todo_b", todo_id="todo_abc"
    )

    first_id = turn_host_adapter._derive_session_id(first, TURN_KEY)
    second_id = turn_host_adapter._derive_session_id(second, TURN_KEY)
    expected = "dsh-lineage-v1-" + sha256(
        json.dumps(
            ["goal-a", "worker", "todo_b-todo_abc"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert first_id.startswith("dsh-lineage-v1-")
    assert second_id.startswith("dsh-lineage-v1-")
    assert first_id == expected
    assert first_id != second_id
    assert first_id == turn_host_adapter._derive_session_id(first, TURN_KEY)


def test_dsh_session_id_preserves_missing_lineage_component_positions() -> None:
    missing_agent = _lineage_request(goal_id="goal", todo_id="todo")
    missing_todo = _lineage_request(goal_id="goal", agent_id="todo")

    assert turn_host_adapter._derive_session_id(
        missing_agent, TURN_KEY
    ) != turn_host_adapter._derive_session_id(missing_todo, TURN_KEY)
    assert turn_host_adapter._derive_session_id(
        _lineage_request(), TURN_KEY
    ) == "dsh-" + "0" * 24
    assert turn_host_adapter._derive_session_id(
        _lineage_request(goal_id=False, agent_id=0, todo_id=""), TURN_KEY
    ) == "dsh-" + "0" * 24


def test_dsh_fresh_iteration_session_id_is_scoped_to_the_turn_key() -> None:
    request = _lineage_request(goal_id="goal", agent_id="agent", todo_id="todo")
    request["session"] = {
        "context_policy": {
            "schema_version": "loopx_iteration_context_policy_v0",
            "mode": "fresh",
            "scope": "iteration",
        }
    }
    first_key = "sha256:" + "1" * 64
    second_key = "sha256:" + "2" * 64

    first_id = turn_host_adapter._derive_session_id(request, first_key)
    assert first_id.startswith("dsh-iteration-v1-")
    assert first_id == turn_host_adapter._derive_session_id(request, first_key)
    assert first_id != turn_host_adapter._derive_session_id(request, second_key)


def test_dsh_host_passes_lineage_session_id_to_the_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests = [
        _signed_request(
            goal_id="goal-a", agent_id="worker", todo_id="todo_b-todo_abc"
        ),
        _signed_request(
            goal_id="goal-a-worker", agent_id="todo_b", todo_id="todo_abc"
        ),
        _signed_request(
            goal_id="goal-a", agent_id="worker", todo_id="todo_b-todo_abc"
        ),
    ]
    session_ids: list[str] = []

    def run_fake_dsh_turn(**kwargs: object) -> str:
        session_ids.append(str(kwargs["session_id"]))
        return '{"result_kind":"wait"}'

    monkeypatch.setattr(turn_host_adapter, "run_dsh_turn", run_fake_dsh_turn)
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path)
    for request in requests:
        turn_host_adapter.run_dsh_host(request, config=config)

    assert session_ids[0] != session_ids[1]
    assert session_ids[0] == session_ids[2]


def test_dsh_goal_mode_is_a_first_class_subpackage() -> None:
    # The subpackage owns the adapter constants and API surface, following the
    # pi/opencode/kunluncode goal-mode packaging pattern.
    assert dsh_goal_mode.ADAPTER_MODULE == "loopx.dsh_goal_mode"
    assert dsh_goal_mode.LOOPX_TURN_HOST_REQUEST_SCHEMA == "loopx_turn_host_request_v0"
    assert dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA == "loopx_turn_result_v0"
    assert callable(dsh_goal_mode.main)
    for name in (
        "build_result",
        "extract_action_text",
        "extract_turn_authority",
        "load_dsh_runner",
        "parse_model_json",
        "render_prompt",
        "run_dsh_turn",
    ):
        assert callable(getattr(dsh_goal_mode, name)), name


def test_legacy_script_is_a_compat_shim_for_the_subpackage() -> None:
    # The historical loose script must keep importing (examples add scripts/
    # to sys.path) and resolve to the very same implementation objects.
    spec = importlib.util.spec_from_file_location(
        "dsh_turn_host_adapter_compat", COMPAT_LAUNCHER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dsh_goal_mode.__all__:
        if name in {"ADAPTER_MODULE", "COMPAT_LAUNCHER"}:
            continue
        assert getattr(module, name) is getattr(dsh_goal_mode, name), name


def test_signed_primary_action_is_the_bounded_task_body() -> None:
    request = _signed_request(primary_action="signed primary action")
    assert turn_host_adapter.extract_action_text(request) == "signed primary action"


def test_unsigned_action_fails_closed() -> None:
    request = _signed_request()
    request["turn_envelope"]["action_signature"]["matches"] = False
    try:
        turn_host_adapter.extract_action_text(request)
    except ValueError as exc:
        assert "action signature" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("unsigned TurnEnvelope action must fail closed")


def test_prompt_requests_one_typed_public_safe_json_result() -> None:
    request = _signed_request()
    authority = turn_host_adapter.extract_turn_authority(request)
    prompt = turn_host_adapter.render_prompt(authority)
    assert "primary_action" in prompt
    assert "result_kind" in prompt
    assert "validated_progress" in prompt
    assert "iteration_failed" in prompt
    # Boundary discipline stays in the prompt text.
    assert "write_scope" in prompt
    assert "credentials" in prompt


def test_parse_model_json_tolerates_prose_and_fences() -> None:
    payload = {"result_kind": "wait", "summary": "nothing to do"}
    assert turn_host_adapter.parse_model_json(json.dumps(payload)) == payload
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    assert turn_host_adapter.parse_model_json(fenced) == payload
    prose = f"Here is the result you asked for:\n{json.dumps(payload)}\nDone."
    assert turn_host_adapter.parse_model_json(prose) == payload
    assert turn_host_adapter.parse_model_json("") is None
    assert turn_host_adapter.parse_model_json("no json at all") is None


def test_build_result_fails_closed_without_a_typed_candidate() -> None:
    request = _signed_request()
    result = turn_host_adapter.build_result(request, None)
    assert result["schema_version"] == dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA
    assert result["turn_key"] == TURN_KEY
    assert result["result_kind"] == "wait"
    assert result["completed_phases"] == ["host_execute", "typed_result"]
    assert result["classification"] == "no_typed_host_result"


def test_build_result_rejects_unsupported_result_kinds() -> None:
    request = _signed_request()
    result = turn_host_adapter.build_result(
        request, {"result_kind": "goal_complete", "summary": "host says done"}
    )
    assert result["result_kind"] == "wait"
    assert result["classification"] == "unsupported_host_result_kind"


def test_build_result_preserves_iteration_failed_as_a_typed_stop() -> None:
    request = _signed_request()
    result = turn_host_adapter.build_result(
        request,
        {
            "result_kind": "iteration_failed",
            "classification": "iteration check failed",
            "summary": "the bounded attempt did not validate",
            "next_action": "start a new controller-authorized iteration",
        },
    )

    assert result["result_kind"] == "iteration_failed"
    assert result["classification"] == "iteration check failed"
    assert "delivery_outcome" not in result


def test_build_result_shapes_material_results_with_required_fields() -> None:
    request = _signed_request()
    result = turn_host_adapter.build_result(
        request,
        {
            "result_kind": "validated_progress",
            "classification": "docs updated",
            "summary": "one change",
            "next_action": "review the diff",
        },
    )
    assert result["result_kind"] == "validated_progress"
    assert result["delivery_batch_scale"] == "single_surface"
    assert result["delivery_outcome"] == "outcome_progress"
    # Sparse material blocks get bounded fallbacks, never empty authority text.
    assert result["recommended_action"]
    assert result["vision_unchanged_reason"]


def test_adapter_runs_hermetically_through_the_module_entry() -> None:
    # `python -m loopx.dsh_goal_mode` must drive the same stdin/stdout
    # contract as the legacy script, using a fake dsh runner.
    runner = Path(__file__).parent / "dsh_goal_mode_fake_runner.py"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.dsh_goal_mode",
            "--dsh-runner",
            str(runner),
        ],
        input=json.dumps(_signed_request()),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["schema_version"] == dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA
    assert result["result_kind"] == "validated_progress"
    assert result["classification"] == "fake dsh typed result"


def test_classify_dsh_failure_maps_each_known_signal() -> None:
    class _StatusError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__("provider rejected the request")
            self.status_code = status_code

    cases = [
        (_StatusError(429), "rate_limited"),
        (_StatusError(402), "quota_exhausted"),
        (_StatusError(401), "auth_failed"),
        (_StatusError(503), "provider_overloaded"),
        (RuntimeError("selected model is at capacity"), "provider_capacity"),
        (TimeoutError("request stalled"), "executor_timeout"),
        (ConnectionResetError("peer went away"), "transport_lost"),
        (RuntimeError("something else entirely"), "unknown"),
    ]
    for exc, expected in cases:
        assert host_failure_map.classify_dsh_failure(exc) == expected, expected


def test_classify_known_code_wins_over_http_status() -> None:
    # loopx-turn-v0: a known error.code wins over HTTP status, so a quota
    # exhaustion stays non-retryable even when transported as HTTP 429.
    class _StatusFirst(Exception):
        status_code = 429
        code = "insufficient_balance"

    class _CodeFirst(Exception):
        code = "QUOTA_EXCEEDED"
        status_code = 429

    assert (
        host_failure_map.classify_dsh_failure(_StatusFirst("x")) == "quota_exhausted"
    )
    assert (
        host_failure_map.classify_dsh_failure(_CodeFirst("x")) == "quota_exhausted"
    )


def test_classify_server_code_is_a_stable_provider_overload_signal() -> None:
    class _ServerError(Exception):
        code = "SERVER"

    assert (
        host_failure_map.classify_dsh_failure(_ServerError("x"))
        == "provider_overloaded"
    )


def test_classify_conflicting_known_codes_fold_closed() -> None:
    class _TwoCodes(Exception):
        code = "QUOTA_EXCEEDED"
        error_code = "RATE_LIMIT"

    assert host_failure_map.classify_dsh_failure(_TwoCodes("x")) == "unknown"


def test_classify_transport_closed_exception_type() -> None:
    class TransportClosedError(Exception):
        pass

    assert (
        host_failure_map.classify_dsh_failure(TransportClosedError("gone"))
        == "transport_lost"
    )


def test_classify_terminal_reason_uses_structured_error() -> None:
    reason = {
        "kind": "error",
        "error": {"code": "RATE_LIMIT", "status": 429, "message": "slow down"},
    }
    assert host_failure_map.classify_dsh_terminal_reason(reason) == "rate_limited"
    assert host_failure_map.classify_dsh_terminal_reason({"kind": "error"}) == (
        "unknown"
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("TRANSPORT", "transport_lost"),
        ("MISSING_CREDENTIAL", "auth_failed"),
        ("INVALID_CREDENTIAL", "auth_failed"),
        ("QUOTA", "quota_exhausted"),
        ("TIMEOUT", "executor_timeout"),
        ("EMPTY_RESPONSE", "transport_lost"),
        ("INVALID_REQUEST", "contract_rejected"),
        ("CONTEXT_WINDOW_EXCEEDED", "contract_rejected"),
        ("UNSUPPORTED_CONTENT", "contract_rejected"),
        ("UNSUPPORTED_REASONING_EFFORT", "contract_rejected"),
        ("REQUEST_EXTENSION", "contract_rejected"),
        ("STREAM_CLOSED", "contract_rejected"),
        ("MALFORMED_RESPONSE", "contract_rejected"),
        ("ABORTED", "unknown"),
    ],
)
def test_classify_official_dsh_terminal_codes(code: str, expected: str) -> None:
    reason = {
        "kind": "error",
        "error": {"code": code, "message": "provider-controlled detail"},
    }
    assert host_failure_map.classify_dsh_terminal_reason(reason) == expected


def test_server_code_without_status_does_not_need_prose_fallback() -> None:
    reason = {
        "kind": "error",
        "error": {
            "code": "SERVER",
            "message": "selected model is at capacity",
        },
    }
    assert (
        host_failure_map.classify_dsh_terminal_reason(reason)
        == "provider_overloaded"
    )


def test_classify_unknown_structured_code_never_falls_back_to_prose() -> None:
    class _UnknownCode(Exception):
        code = "mystery_condition"

    exc = _UnknownCode("rate limit exceeded while the model is at capacity")
    assert host_failure_map.classify_dsh_failure(exc) == "unknown"


def test_classify_dsh_failure_folds_conflicting_prose_closed() -> None:
    exc = RuntimeError("rate limit reached while the model is at capacity")
    assert host_failure_map.classify_dsh_failure(exc) == "unknown"


def test_run_dsh_host_returns_the_typed_result_in_process(tmp_path: Path) -> None:
    runner = Path(__file__).parent / "dsh_goal_mode_fake_runner.py"
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path, dsh_runner=runner)
    result = turn_host_adapter.run_dsh_host(_signed_request(), config=config)
    assert result["schema_version"] == dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA
    assert result["result_kind"] == "validated_progress"
    assert result["completed_phases"] == list(dsh_goal_mode.COMPLETED_PHASES)
    assert (tmp_path / ".local" / ".dsh-sessions").is_dir()


def test_run_dsh_host_maps_terminal_provider_failure_without_an_exception(
    tmp_path: Path,
) -> None:
    # The SDK reports provider failures as RunResult(finish_reason="error"),
    # not as a raised exception; the adapter must still surface a typed kind.
    runner = Path(__file__).parent / "dsh_goal_mode_capacity_runner.py"
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path, dsh_runner=runner)
    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)
    assert excinfo.value.reason == "dsh_execution_failed"
    assert excinfo.value.failure_kind == "provider_capacity"
    record = build_host_failure_record(excinfo.value.failure_kind, attempt=1)
    assert record["retryable"] is True
    assert record["retry"]["backoff_seconds"] == 30
    assert "selected model is at capacity" not in json.dumps(record)
    assert "selected model is at capacity" not in str(excinfo.value)


def test_run_dsh_host_maps_official_transport_terminal_failure(
    tmp_path: Path,
) -> None:
    runner = Path(__file__).parent / "dsh_goal_mode_transport_runner.py"
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path, dsh_runner=runner)
    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)
    assert excinfo.value.failure_kind == "transport_lost"
    record = build_host_failure_record(excinfo.value.failure_kind, attempt=1)
    assert record["retryable"] is True
    assert record["retry"]["backoff_seconds"] == 10


def test_run_dsh_host_maps_raised_capacity_prose_to_a_typed_retryable_kind(
    tmp_path: Path,
) -> None:
    runner = Path(__file__).parent / "dsh_goal_mode_raising_runner.py"
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path, dsh_runner=runner)
    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)
    assert excinfo.value.failure_kind == "provider_capacity"
    assert (
        build_host_failure_record("provider_capacity", attempt=1)["retryable"]
        is True
    )


def test_build_sdk_config_targets_the_current_sdk_surface(tmp_path: Path) -> None:
    dsh_home = tmp_path / "dsh-home"
    cordis = tmp_path / "config" / ".." / "cordis.yml"
    config = turn_host_adapter.build_sdk_config(
        provider="deepseek-official",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        workspace=tmp_path,
        dsh_home=dsh_home,
        max_tokens=1024,
        cordis=cordis,
        runtime_bin="/opt/dsh/bin/dsh",
        request_timeout_seconds=90.0,
    )
    assert set(config) <= {
        "provider",
        "model",
        "reasoning_effort",
        "cwd",
        "dsh_home",
        "max_tokens",
        "patches",
        "dsh_bin",
        "request_timeout_seconds",
    }
    assert config["reasoning_effort"] == "high"
    assert config["dsh_home"] == str(dsh_home)
    assert config["dsh_bin"] == "/opt/dsh/bin/dsh"
    assert config["patches"] == (str(cordis.expanduser().resolve()),)
    for legacy_field in ("session_root", "cordis", "runtime_bin"):
        assert legacy_field not in config


def test_build_sdk_config_omits_an_unset_reasoning_effort(tmp_path: Path) -> None:
    # A caller that explicitly passes no effort must not have one invented for
    # it: the SDK distinguishes "provider default" from a named effort.
    config = turn_host_adapter.build_sdk_config(
        provider="deepseek-official",
        model="deepseek-v4-flash",
        reasoning_effort=None,
        workspace=tmp_path,
        dsh_home=tmp_path / "dsh-home",
        max_tokens=None,
        cordis=None,
        runtime_bin=None,
        request_timeout_seconds=None,
    )
    assert "reasoning_effort" not in config


def test_build_sdk_config_carries_a_caller_pinned_runtime_environment(
    tmp_path: Path,
) -> None:
    # A Chat channel pins its own segments read-only through the runtime
    # environment; the adapter forwards it instead of reinterpreting it.
    config = turn_host_adapter.build_sdk_config(
        provider="deepseek-official",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        workspace=tmp_path,
        dsh_home=tmp_path / "dsh-home",
        max_tokens=None,
        cordis=None,
        runtime_bin=None,
        request_timeout_seconds=None,
        env={"DSH_PERMISSION_MODE": "read-only"},
    )
    assert config["env"] == {"DSH_PERMISSION_MODE": "read-only"}

    unpinned = turn_host_adapter.build_sdk_config(
        provider="deepseek-official",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        workspace=tmp_path,
        dsh_home=tmp_path / "dsh-home",
        max_tokens=None,
        cordis=None,
        runtime_bin=None,
        request_timeout_seconds=None,
    )
    assert "env" not in unpinned


def test_host_config_resolves_the_shared_managed_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOOPX_TURN_MODEL", raising=False)
    monkeypatch.delenv("DSH_MODEL", raising=False)
    profile = turn_host_adapter.DshHostConfig(workspace=tmp_path).resolved_profile()
    assert (
        profile["provider"],
        profile["model"],
        profile["reasoning_effort"],
    ) == (
        turn_host_adapter.DEFAULT_PROVIDER,
        turn_host_adapter.DEFAULT_MODEL,
        turn_host_adapter.DEFAULT_REASONING_EFFORT,
    )
    assert profile["model_source"] == "product_default"

    monkeypatch.setenv("LOOPX_TURN_MODEL", "fixture-model")
    overridden = turn_host_adapter.DshHostConfig(
        workspace=tmp_path, model="argument-model"
    ).resolved_profile()
    assert overridden["model"] == "argument-model"
    assert overridden["model_source"] == "explicit_argument"
    # An explicit field wins alone: the others still follow the environment.
    assert overridden["reasoning_effort"] == turn_host_adapter.DEFAULT_REASONING_EFFORT


def test_dsh_home_resolution_prefers_config_then_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    configured = tmp_path / "configured-home"
    environment = tmp_path / "environment-home"
    monkeypatch.setenv("DSH_HOME", str(environment))

    assert turn_host_adapter.resolve_dsh_home(workspace, configured) == configured
    assert turn_host_adapter.resolve_dsh_home(workspace, None) == environment

    monkeypatch.delenv("DSH_HOME")
    assert turn_host_adapter.resolve_dsh_home(workspace, None) == (
        workspace / ".local" / ".dsh-sessions"
    )


def test_terminal_error_reason_extraction() -> None:
    success = {"final_response": "{}", "finish_reason": "stop", "events": []}
    assert turn_host_adapter.terminal_error_reason(success) is None
    bare_error = {"final_response": "", "finish_reason": "error", "events": []}
    assert turn_host_adapter.terminal_error_reason(bare_error) == {"kind": "error"}
    contradictory = {
        "final_response": "",
        "finish_reason": "error",
        "events": [
            {
                "type": "turn/end",
                "data": {"reason": {"kind": "stop", "status": 200}},
            }
        ],
    }
    with pytest.raises(turn_host_adapter.DshHostResultError):
        turn_host_adapter.normalize_runner_outcome(contradictory)
    assert turn_host_adapter.normalize_runner_outcome("just text") == {
        "final_response": "just text",
        "finish_reason": None,
        "events": [],
    }

    sdk_result = SimpleNamespace(
        final_response='{"result_kind":"wait"}',
        finish_reason="completed",
        events=[
            {
                "type": "turn/end",
                "data": {"reason": {"kind": "completed"}},
            }
        ],
    )
    normalized = turn_host_adapter.normalize_runner_outcome(sdk_result)
    assert normalized["finish_reason"] == "completed"


@pytest.mark.parametrize(
    ("final_response", "expected"),
    [("", "no_final"), ("  ", "no_final"), ('{"result_kind":', "partial")],
)
def test_terminal_output_budget_state_rejects_every_max_token_fragment(
    final_response: str,
    expected: str,
) -> None:
    assert (
        turn_host_adapter.terminal_output_budget_state(
            {
                "final_response": final_response,
                "finish_reason": "max-tokens",
                "events": [],
            }
        )
        == expected
    )


@pytest.mark.parametrize(
    ("final_response", "expected_reason"),
    [
        ("", "dsh_output_budget_exhausted_no_final"),
        ('{"result_kind":"validated_progress"}', "dsh_output_budget_exhausted_partial"),
    ],
)
def test_run_dsh_host_maps_max_tokens_to_non_retryable_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    final_response: str,
    expected_reason: str,
) -> None:
    monkeypatch.setattr(
        turn_host_adapter,
        "run_dsh_turn",
        lambda **_kwargs: {
            "final_response": final_response,
            "finish_reason": "max-tokens",
            "events": [
                {
                    "type": "turn/end",
                    "data": {"reason": {"kind": "max-tokens"}},
                }
            ],
        },
    )
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path)

    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)

    assert excinfo.value.reason == expected_reason
    assert excinfo.value.failure_kind == "output_budget_exhausted"
    assert build_host_failure_record(excinfo.value.failure_kind, attempt=1) == {
        "schema_version": "loopx_turn_host_failure_v0",
        "kind": "output_budget_exhausted",
        "attempt": 1,
        "retryable": False,
    }


def test_generic_adapter_returns_typed_iteration_failure_on_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        turn_host_adapter,
        "run_dsh_turn",
        lambda **_kwargs: {
            "final_response": "",
            "finish_reason": "max-tokens",
            "events": [],
        },
    )
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path)
    request = _signed_request()

    result = turn_host_adapter._execute_turn_host_request(
        request,
        turn_host_adapter.extract_turn_authority(request),
        config=config,
        terminal_errors_as_host_failure=False,
    )

    assert result["result_kind"] == "iteration_failed"
    assert result["classification"] == "dsh_output_budget_exhausted_no_final"
    assert "blindly rerun" in result["next_action"]


def test_dsh_host_config_has_a_bounded_product_default(tmp_path: Path) -> None:
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path)

    assert config.max_tokens == 16_384


@pytest.mark.parametrize("invalid", [0, -1, True])
def test_dsh_host_rejects_invalid_output_token_limit_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid: object,
) -> None:
    invoked = False

    def runner(**_kwargs: object) -> dict[str, object]:
        nonlocal invoked
        invoked = True
        return {"final_response": "", "finish_reason": "completed", "events": []}

    monkeypatch.setattr(turn_host_adapter, "run_dsh_turn", runner)
    config = turn_host_adapter.DshHostConfig(
        workspace=tmp_path,
        max_tokens=invalid,  # type: ignore[arg-type]
    )

    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)

    assert excinfo.value.reason == "dsh_output_token_limit_rejected"
    assert excinfo.value.failure_kind == "contract_rejected"
    assert invoked is False


@pytest.mark.parametrize(
    "outcome",
    [
        {},
        {"final_response": 42, "finish_reason": "completed", "events": []},
        {"final_response": "{}", "finish_reason": 42, "events": []},
        {"final_response": "{}", "finish_reason": None, "events": "bad"},
        {"final_response": "{}", "finish_reason": "error", "events": [None]},
        {
            "final_response": "{}",
            "finish_reason": "error",
            "events": [{"type": "turn/end", "data": {}}],
        },
        {
            "final_response": "{}",
            "finish_reason": "error",
            "events": [
                {"type": "turn/end", "data": {"reason": {"kind": 42}}}
            ],
        },
    ],
)
def test_normalize_runner_outcome_rejects_invalid_shapes(outcome: object) -> None:
    with pytest.raises(turn_host_adapter.DshHostResultError):
        turn_host_adapter.normalize_runner_outcome(outcome)


def test_run_dsh_host_rejects_untyped_requests_as_contract_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path)
    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host({"schema_version": "nope"}, config=config)
    assert excinfo.value.failure_kind == "contract_rejected"

    tampered = _signed_request()
    tampered["turn_envelope"]["action"]["primary_action"] = "tampered"
    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(tampered, config=config)
    assert excinfo.value.failure_kind == "contract_rejected"

    monkeypatch.setattr(turn_host_adapter, "run_dsh_turn", lambda **_kwargs: {})
    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)
    assert excinfo.value.reason == "dsh_host_result_rejected"
    assert excinfo.value.failure_kind == "contract_rejected"

    monkeypatch.setattr(
        turn_host_adapter,
        "run_dsh_turn",
        lambda **_kwargs: '{"result_kind":"wait"}',
    )

    def _raise_result_shape_error(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("result shaping failed")

    monkeypatch.setattr(turn_host_adapter, "build_result", _raise_result_shape_error)
    with pytest.raises(BuiltInHostError) as excinfo:
        turn_host_adapter.run_dsh_host(_signed_request(), config=config)
    assert excinfo.value.reason == "dsh_host_result_rejected"
    assert excinfo.value.failure_kind == "contract_rejected"


def test_legacy_launcher_runs_the_same_contract() -> None:
    runner = Path(__file__).parent / "dsh_goal_mode_fake_runner.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(COMPAT_LAUNCHER),
            "--dsh-runner",
            str(runner),
        ],
        input=json.dumps(_signed_request()),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["schema_version"] == dsh_goal_mode.LOOPX_TURN_RESULT_SCHEMA
    assert result["result_kind"] == "validated_progress"


def test_subprocess_terminal_error_keeps_the_legacy_wait_contract() -> None:
    runner = Path(__file__).parent / "dsh_goal_mode_capacity_runner.py"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.dsh_goal_mode",
            "--dsh-runner",
            str(runner),
        ],
        input=json.dumps(_signed_request()),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["result_kind"] == "wait"
    assert result["classification"] == "no_typed_host_result"
    assert "dsh execution failed" not in completed.stderr
