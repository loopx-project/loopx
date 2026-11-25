"""Fail-closed duplicate-modelId quota and cross-file caller typing contract tests."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from loopx.capabilities.native_chat import antigravity_adapter as provider
from loopx.capabilities.native_chat.antigravity_adapter import (
    AntigravityAdapter,
    discover_antigravity_language_server,
    offered_antigravity_model_options,
    probe_antigravity_endpoint,
    probe_cascade_idle,
    resolve_antigravity_model_placeholder,
)
from loopx.capabilities.native_chat.antigravity_contracts import (
    AntigravityCompletedResultDict,
    AntigravityEndpointDict,
    AntigravityInterruptedAnswerDict,
    AntigravityModelOptionDict,
    AntigravityRpcResponseDict,
)
from loopx.chat_agent import CodexChatAgentError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_FILES: tuple[Path, ...] = (
    _REPO_ROOT / "loopx/capabilities/native_chat/antigravity_contracts.py",
    _REPO_ROOT / "loopx/capabilities/native_chat/antigravity_discovery.py",
    _REPO_ROOT / "loopx/capabilities/native_chat/antigravity_cascade.py",
    _REPO_ROOT / "loopx/capabilities/native_chat/antigravity_observations.py",
    _REPO_ROOT / "loopx/capabilities/native_chat/antigravity_adapter.py",
    _REPO_ROOT / "tests/test_antigravity_adapter_contract.py",
)

_CALLER_ERROR_CAPS: dict[str, int] = {
    "loopx/capabilities/native_chat/antigravity_contracts.py": 0,
    "loopx/capabilities/native_chat/antigravity_discovery.py": 0,
    "loopx/capabilities/native_chat/antigravity_cascade.py": 0,
    "loopx/capabilities/native_chat/antigravity_observations.py": 0,
    "loopx/capabilities/native_chat/antigravity_adapter.py": 0,
    "tests/test_antigravity_adapter_contract.py": 0,
    "loopx/agy_goal_mode/host_runtime.py": 1,
    "loopx/capabilities/native_chat/model_settings.py": 2,
    "loopx/capabilities/native_chat/report_failure_proof.py": 6,
    "loopx/capabilities/native_chat/external_conversations.py": 37,
    "loopx/capabilities/native_chat/codex_adapter.py": 23,
    "loopx/capabilities/native_chat/manager_context.py": 0,
    "loopx/capabilities/native_chat/manager_input.py": 0,
    "loopx/capabilities/native_chat/steward_turn.py": 59,
    "loopx/chat/coordination.py": 1,
    "loopx/chat/endpoint_catalog.py": 0,
    "loopx/chat/session_open.py": 27,
    "loopx/chat_manager.py": 4,
    "loopx/chat_runtime.py": 27,
}

_SUMMARY_SUCCESS_RE = re.compile(r"^Success: no issues found in (\d+) source files?$")
_SUMMARY_FOUND_RE = re.compile(
    r"^Found \d+ errors? in \d+ files? \(checked (\d+) source files?\)$"
)


def _parse_mypy_contract_output(
    proc: subprocess.CompletedProcess[str], *, paths: Sequence[str]
) -> dict[str, list[str]]:
    """Require complete mypy execution and parse per-file diagnostics."""
    if proc.returncode not in (0, 1):
        raise AssertionError(
            f"mypy probe aborted with rc={proc.returncode}: "
            f"stderr={proc.stderr!r}, stdout={proc.stdout!r}"
        )
    if proc.stderr.strip():
        raise AssertionError(f"mypy probe produced non-empty stderr: {proc.stderr!r}")

    lines = [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("mypy probe returned empty stdout without summary line")

    summary = lines[-1]
    checked: int | None = None
    for pattern in (_SUMMARY_SUCCESS_RE, _SUMMARY_FOUND_RE):
        match = pattern.match(summary)
        if match is not None:
            checked = int(match.group(1))
            break
    if checked is None:
        raise AssertionError(f"mypy probe exited before summary line: {summary!r}")
    if checked != len(paths):
        raise AssertionError(
            f"mypy probe checked {checked} files, expected {len(paths)}: {summary!r}"
        )

    errors_by_file: dict[str, list[str]] = {entry: [] for entry in paths}
    for line in lines[:-1]:
        if ": error:" not in line:
            continue
        file_part = line.split(":", 1)[0]
        if file_part not in errors_by_file:
            raise AssertionError(f"unexpected diagnostic file {file_part!r}: {line!r}")
        errors_by_file[file_part].append(line)
    return errors_by_file


def _run_mypy_contract_probe(
    paths: Sequence[str], *, extra_env: Mapping[str, str] | None = None
) -> dict[str, list[str]]:
    """Run fail-closed mypy check over callers and fail on partial/aborted runs."""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--follow-imports=silent",
            *paths,
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return _parse_mypy_contract_output(proc, paths=paths)


def _sample_contract_consumers() -> None:
    """Type-checked consumer exercise covering all 5 typed Antigravity contracts."""
    endpoint: AntigravityEndpointDict | None = discover_antigravity_language_server()
    if endpoint is not None:
        _url: str = endpoint["url"]
        _tok: str = endpoint["csrf_token"]
        _rpc: AntigravityRpcResponseDict = provider._rpc_post(
            _url, _tok, "GetCascadeTrajectory", {"cascadeId": "c1"}, timeout=2.0
        )
        _traj_cid = (_rpc.get("trajectory") or {}).get("cascadeId")
        _steps = _rpc["steps"]
        _copied = {**_rpc["trajectory"], "steps": _steps}
        assert _copied.get("cascadeId") == _traj_cid or True
    options: list[AntigravityModelOptionDict] = offered_antigravity_model_options()
    for item in options:
        _efforts = [
            effort["reasoning_effort"]
            for effort in (item.get("supported_reasoning_efforts") or [])
        ]
        assert isinstance(_efforts, list)
    completed: AntigravityCompletedResultDict | None = AntigravityAdapter.completed_result_observation(
        cascade_id="c1",
        request_ref="f" * 24,
        message="msg",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:05Z",
        work_dir=_REPO_ROOT,
    )
    if completed is not None:
        _msg: str = completed["response"]["message"]
        assert isinstance(_msg, str)
    interrupted: AntigravityInterruptedAnswerDict | None = (
        AntigravityAdapter.interrupted_answer_observation(
            cascade_id="c1",
            request_ref="f" * 24,
            message="msg",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:00:05Z",
            input_anchor={"thread_id": "c1", "start_step_index": 0, "input_sha256": "0" * 64},
        )
    )
    if interrupted is not None:
        _first = interrupted["visible_results"][0]
        assert isinstance(_first, dict)


@pytest.mark.parametrize(
    "bad_alias",
    ["malformed-string", 123, ["MODEL_PLACEHOLDER_M417"]],
)
def test_duplicate_model_id_with_malformed_alias_fails_closed_on_quota(
    monkeypatch: pytest.MonkeyPatch,
    bad_alias: object,
) -> None:
    """Conflicting duplicate modelId rows must return None even with non-dict modelOrAlias."""
    future_reset = "2030-01-01T00:00:00Z"
    conflicting_rows: list[dict[str, object]] = [
        {
            "modelId": "gemini-4-argon-high",
            "modelOrAlias": bad_alias,
            "quotaInfo": {"remainingFraction": 0.0, "resetTime": future_reset},
        },
        {
            "modelId": "gemini-4-argon-high",
            "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"},
            "quotaInfo": {"remainingFraction": 0.8, "resetTime": future_reset},
        },
    ]

    monkeypatch.setattr(
        provider,
        "discover_antigravity_language_server",
        lambda **_kw: {"url": "http://127.0.0.1:4321", "csrf_token": "tok"},
    )
    monkeypatch.setattr(
        provider,
        "_rpc_post",
        lambda _url, _tok, method, _payload, **_kw: (
            {
                "userStatus": {
                    "email": "synthetic@invalid.example",
                    "cascadeModelConfigData": {"clientModelConfigs": conflicting_rows},
                }
            }
            if method == "GetUserStatus"
            else {}
        ),
    )

    assert AntigravityAdapter.endpoint_model_capacity(model_id="gemini-4-argon-high") is None

    # Policy-off / unambiguous single valid row continues returning exact capacity.
    monkeypatch.setattr(
        provider,
        "_rpc_post",
        lambda _url, _tok, method, _payload, **_kw: (
            {
                "userStatus": {
                    "email": "synthetic@invalid.example",
                    "cascadeModelConfigData": {"clientModelConfigs": [conflicting_rows[1]]},
                }
            }
            if method == "GetUserStatus"
            else {}
        ),
    )
    cap = AntigravityAdapter.endpoint_model_capacity(model_id="gemini-4-argon-high")
    assert cap is not None
    assert cap["remaining_fraction"] == pytest.approx(0.8)
    assert cap["normal_completion"] is True

    # Placeholder resolution still rejects malformed non-dict modelOrAlias rows.
    with pytest.raises(CodexChatAgentError) as raised:
        resolve_antigravity_model_placeholder(
            "gemini-4-argon-high",
            dynamic_configs=[{"modelId": "gemini-4-argon-high", "modelOrAlias": bad_alias}],
        )
    assert raised.value.error_code == "unsupported_configuration"
    assert resolve_antigravity_model_placeholder(
        "gemini-4-argon-high",
        dynamic_configs=[conflicting_rows[1]],
    ) == ("gemini-4-argon-high", "MODEL_PLACEHOLDER_M417")


def test_mypy_contract_probe_rejects_early_exit_or_aborted_run(tmp_path: Path) -> None:
    sample_paths = ("a.py", "b.py")
    with pytest.raises(AssertionError, match="aborted with rc=2"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(["mypy"], 2, stdout="", stderr=""),
            paths=sample_paths,
        )
    with pytest.raises(AssertionError, match="non-empty stderr"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(
                ["mypy"],
                1,
                stdout="Success: no issues found in 2 source files\n",
                stderr="MYPYPATH error",
            ),
            paths=sample_paths,
        )
    with pytest.raises(AssertionError, match="before summary line"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(
                ["mypy"],
                1,
                stdout="Found 1 error in 1 file (errors prevented further checking)\n",
                stderr="",
            ),
            paths=sample_paths,
        )
    with pytest.raises(AssertionError, match="checked 1 files, expected 2"):
        _parse_mypy_contract_output(
            subprocess.CompletedProcess(
                ["mypy"],
                0,
                stdout="Success: no issues found in 1 source file\n",
                stderr="",
            ),
            paths=sample_paths,
        )

    broken_stubs = tmp_path / "stubs"
    (broken_stubs / "loopx").mkdir(parents=True)
    (broken_stubs / "loopx" / "__init__.pyi").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="aborted with rc=2"):
        _run_mypy_contract_probe(
            ["loopx/capabilities/native_chat/antigravity_adapter.py"],
            extra_env={"MYPYPATH": str(broken_stubs)},
        )


def test_antigravity_adapter_preserves_caller_typing_contracts() -> None:
    paths = tuple(_CALLER_ERROR_CAPS)
    errors_by_file = _run_mypy_contract_probe(paths)
    for file_path, max_errors in _CALLER_ERROR_CAPS.items():
        observed = errors_by_file[file_path]
        assert len(observed) <= max_errors, (
            f"{file_path} exceeded caller mypy budget {max_errors} with {len(observed)}: {observed}"
        )

    for guarded_caller in (
        "loopx/agy_goal_mode/host_runtime.py",
        "loopx/capabilities/native_chat/model_settings.py",
        "loopx/capabilities/native_chat/report_failure_proof.py",
    ):
        err_text = "\n".join(errors_by_file[guarded_caller])
        for forbidden in (
            "antigravity_adapter",
            "_rpc_post",
            "discover_antigravity_language_server",
            "offered_antigravity_model_options",
            "_read_trajectory_steps",
            "_local_url",
            "\"object\" has no attribute \"get\"",
            "\"object\" has no attribute \"__iter__\"",
            "Incompatible types in assignment",
        ):
            assert forbidden not in err_text, (
                f"{guarded_caller} regressed on {forbidden!r}:\n{err_text}"
            )


def test_antigravity_adapter_surface_parity_zero_any_and_size() -> None:
    for file_path in _MODULE_FILES:
        src = file_path.read_text(encoding="utf-8")
        lines = src.splitlines()
        assert len(lines) < 1000, f"{file_path} exceeds 1000 LOC ({len(lines)})"
        assert not any(re.search(r"#\s*" + "type:" + r"\s*ignore", line) for line in lines), (
            f"{file_path} contains type ignore comment"
        )
        tree = ast.parse(src)
        nofollow_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "O_" + "NOFOLLOW"
        ]
        assert not nofollow_nodes, f"{file_path} contains bare os.O_NOFOLLOW attribute access"
        any_nodes = [
            node
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
        ]
        assert not any_nodes, f"{file_path} still references typing.Any ({len(any_nodes)} nodes)"

    required_exports = (
        "AntigravityActivity",
        "AntigravityAdapter",
        "ANTIGRAVITY_DEFAULT_MODEL",
        "ANTIGRAVITY_DEFAULT_REASONING_EFFORT",
        "ANTIGRAVITY_IDLE_TIMEOUT_SEC",
        "ANTIGRAVITY_LARGE_CASCADE_POLL_THRESHOLD",
        "ANTIGRAVITY_MAX_CASCADE_STEPS",
        "ANTIGRAVITY_MODEL_OPTIONS",
        "ANTIGRAVITY_MODEL_PLACEHOLDERS",
        "ANTIGRAVITY_ROLLOVER_MAX_ITEM_CHARS",
        "ANTIGRAVITY_ROLLOVER_MAX_MESSAGES",
        "ANTIGRAVITY_ROLLOVER_MAX_TOTAL_CHARS",
        "discover_antigravity_language_server",
        "offered_antigravity_model_options",
        "probe_antigravity_endpoint",
        "probe_cascade_idle",
        "resolve_antigravity_model_placeholder",
        "_rpc_post",
        "_local_url",
        "_read_trajectory_steps",
        "_turn_progress_snapshot",
        "parse_agent_response",
        "manager_input_matches",
        "os",
        "subprocess",
        "time",
    )
    for export in required_exports:
        assert hasattr(provider, export), f"missing compatibility export: {export}"
    assert discover_antigravity_language_server is provider.discover_antigravity_language_server
    assert offered_antigravity_model_options is provider.offered_antigravity_model_options
    assert probe_antigravity_endpoint is provider.probe_antigravity_endpoint
    assert probe_cascade_idle is provider.probe_cascade_idle
