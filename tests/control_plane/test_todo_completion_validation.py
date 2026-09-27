from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import loopx.control_plane.effect_runtime as effect_runtime_module
import loopx.control_plane.todos.completion_validation as completion_validation_module
from loopx.control_plane.todos.completion_validation_projection import (
    completion_validation_declaration_sha256,
    project_completion_validation_authority,
)
from loopx.control_plane.todos.completion_validation import (
    resolve_private_completion_validation_declaration,
)
from loopx.control_plane.agents.workspace_guard import capture_delivery_workspace
from loopx.control_plane.todos.completion_validation_store import (
    completion_validation_declaration_path,
    persist_completion_validation_declaration,
    read_completion_validation_declaration,
)
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo, complete_goal_todo, update_goal_todo

GOAL_ID = "todo-completion-validation"
AGENT = "codex-author"

_PASS_COMMAND = f'{shlex.quote(sys.executable)} -c "raise SystemExit(0)"'
_FAIL_COMMAND = f'{shlex.quote(sys.executable)} -c "raise SystemExit(1)"'
_SLEEP_COMMAND = f'{shlex.quote(sys.executable)} -c "import time; time.sleep(30)"'


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-08-12T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, state


def _agent_todo(state: Path, todo_id: str) -> dict:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    return next(
        item
        for item in todos["agent_todos"]["items"]
        if item["todo_id"] == todo_id
    )


def _add_todo(
    registry: Path,
    *,
    validation_command: str | None = None,
    validation_command_json: str | None = None,
    validation_label: str | None = None,
    validation_timeout_seconds: int | None = None,
    task_repository: str | None = None,
) -> dict:
    return add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver one bounded change.",
        task_class="advancement_task",
        claimed_by=AGENT,
        validation_command=validation_command,
        validation_command_json=validation_command_json,
        validation_label=validation_label,
        validation_timeout_seconds=validation_timeout_seconds,
        task_repository=task_repository,
    )


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _cross_repository_worktree(
    tmp_path: Path,
    *,
    name: str,
) -> tuple[str, Path, dict[str, Any]]:
    repository = tmp_path / f"{name}-repository"
    repository.mkdir()
    _git("init", "-b", "main", cwd=repository)
    _git("config", "user.name", "LoopX Test", cwd=repository)
    _git("config", "user.email", "loopx-test@example.invalid", cwd=repository)
    remote = f"https://github.com/example/{name}.git"
    _git("remote", "add", "origin", remote, cwd=repository)
    (repository / f"{name}-only").write_text("ok\n", encoding="utf-8")
    _git("add", f"{name}-only", cwd=repository)
    _git("commit", "-m", "fixture", cwd=repository)
    worktree = tmp_path / f"{name}-worktree"
    _git("worktree", "add", "-b", f"test-{name}", str(worktree), cwd=repository)
    snapshot = capture_delivery_workspace(
        worktree,
        peer_independent_worktree_required=True,
        repository_source="test_settlement",
    )
    assert snapshot is not None
    task_repository = str(snapshot["task_repository"])
    return task_repository, worktree, snapshot


def _same_repository_worktrees(
    tmp_path: Path,
) -> tuple[Path, str, Path, dict[str, Any], Path]:
    registry, state = _write_fixture(tmp_path)
    repository = state.parent
    _git("init", "-b", "main", cwd=repository)
    _git("config", "user.name", "LoopX Test", cwd=repository)
    _git("config", "user.email", "loopx-test@example.invalid", cwd=repository)
    _git(
        "remote",
        "add",
        "origin",
        "https://github.com/example/shared.git",
        cwd=repository,
    )
    _git("add", state.name, cwd=repository)
    _git("commit", "-m", "fixture", cwd=repository)
    delivery_worktree = tmp_path / "shared-delivery-worktree"
    _git(
        "worktree",
        "add",
        "-b",
        "test-shared-delivery",
        str(delivery_worktree),
        cwd=repository,
    )
    (delivery_worktree / "delivery-only").write_text("ok\n", encoding="utf-8")
    _git("add", "delivery-only", cwd=delivery_worktree)
    _git("commit", "-m", "delivery fixture", cwd=delivery_worktree)
    receipt = capture_delivery_workspace(
        delivery_worktree,
        peer_independent_worktree_required=True,
        repository_source="test_settlement",
    )
    assert receipt is not None
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        str(receipt["workspace_revision_digest"]),
    )
    raw_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=delivery_worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert raw_revision not in json.dumps(receipt)
    other_worktree = tmp_path / "shared-other-worktree"
    _git(
        "worktree",
        "add",
        "-b",
        "test-shared-other",
        str(other_worktree),
        cwd=repository,
    )
    return (
        registry,
        str(receipt["task_repository"]),
        delivery_worktree,
        receipt,
        other_worktree,
    )


def _record_completion_runtime_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    calls: list[str] = []
    original_request = effect_runtime_module._request_with_info

    def recording_request(*args, **kwargs):  # type: ignore[no-untyped-def]
        method = kwargs.get("method")
        if isinstance(method, str) and method.startswith("todo.completion"):
            calls.append(method)
        return original_request(*args, **kwargs)

    monkeypatch.setattr(effect_runtime_module, "_request_with_info", recording_request)
    return calls


def test_validation_command_declared_and_passing_commits_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command=_PASS_COMMAND,
        validation_label="caller-declared smoke",
    )
    # Spy on the executor so the test fails if the gate is silently skipped.
    original_runner = completion_validation_module.run_caller_validation
    calls = {"count": 0}

    def counting_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        return original_runner(*args, **kwargs)

    monkeypatch.setattr(completion_validation_module, "run_caller_validation", counting_runner)
    transaction_calls = _record_completion_runtime_calls(monkeypatch)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="validated completion",
    )
    assert calls["count"] == 1  # the gate actually ran the declared command
    assert [
        method for method in transaction_calls
        if method == "todo.completion.reduce"
    ] == [
        "todo.completion.reduce",
        "todo.completion.reduce",
    ]
    assert "todo.completion_policy.resolve" not in transaction_calls
    assert result["ok"] is True
    assert result["changed"] is True
    assert "validation_blocked_completion" not in result
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_cross_repository_validation_runs_in_recorded_clean_worktree(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    task_repository, worktree, workspace_receipt = _cross_repository_worktree(
        tmp_path,
        name="repo-b",
    )
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('repo-b-only').is_file()",
            ]
        ),
        validation_label="repository B smoke",
        task_repository=task_repository,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="validated in repository B",
        completion_delivery_workspace=workspace_receipt,
        completion_validation_workspace_path=worktree,
    )

    assert result["ok"] is True
    assert result["changed"] is True
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_same_repository_validation_prefers_recorded_exact_worktree(
    tmp_path: Path,
) -> None:
    registry, task_repository, worktree, workspace_receipt, _other = (
        _same_repository_worktrees(tmp_path)
    )
    state = registry.parent / "repo" / "ACTIVE_GOAL_STATE.md"
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('delivery-only').is_file()",
            ]
        ),
        task_repository=task_repository,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="validated in the exact same-repository worktree",
        completion_delivery_workspace=workspace_receipt,
        completion_validation_workspace_path=worktree,
    )

    assert result["ok"] is True
    assert result["changed"] is True
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_same_repository_validation_rejects_different_revision_without_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, task_repository, _worktree, workspace_receipt, other_worktree = (
        _same_repository_worktrees(tmp_path)
    )
    state = registry.parent / "repo" / "ACTIVE_GOAL_STATE.md"
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps([sys.executable, "-c", "pass"]),
        task_repository=task_repository,
    )
    calls = {"count": 0}

    def forbidden_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        raise AssertionError("validation command must not run")

    monkeypatch.setattr(
        completion_validation_module,
        "run_caller_validation",
        forbidden_runner,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="wrong same-repository revision claim",
        completion_delivery_workspace=workspace_receipt,
        completion_validation_workspace_path=other_worktree,
    )

    assert calls["count"] == 0
    assert result["validation_blocked_completion"] is True
    assert result["validation"]["status"] == "workspace_repository_mismatch"
    assert str(tmp_path) not in json.dumps(result["validation"])
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_cross_repository_validation_without_recorded_workspace_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    task_repository, worktree, _workspace_receipt = _cross_repository_worktree(
        tmp_path,
        name="repo-b",
    )
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps([sys.executable, "-c", "pass"]),
        task_repository=task_repository,
    )
    calls = {"count": 0}

    def forbidden_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        raise AssertionError("validation command must not run")

    monkeypatch.setattr(completion_validation_module, "run_caller_validation", forbidden_runner)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="unverified claim",
        completion_validation_workspace_path=worktree,
    )

    assert calls["count"] == 0
    assert result["validation_blocked_completion"] is True
    assert result["validation"]["status"] == "workspace_receipt_unavailable"
    assert str(tmp_path) not in json.dumps(result["validation"])
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_cross_repository_validation_rejects_foreign_worktree_without_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    task_repository, _worktree, workspace_receipt = _cross_repository_worktree(
        tmp_path,
        name="repo-b",
    )
    _foreign_repository, foreign_worktree, _foreign_receipt = (
        _cross_repository_worktree(tmp_path, name="repo-c")
    )
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps([sys.executable, "-c", "pass"]),
        task_repository=task_repository,
    )
    calls = {"count": 0}

    def forbidden_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        raise AssertionError("validation command must not run")

    monkeypatch.setattr(completion_validation_module, "run_caller_validation", forbidden_runner)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="wrong repository claim",
        completion_delivery_workspace=workspace_receipt,
        completion_validation_workspace_path=foreign_worktree,
    )

    assert calls["count"] == 0
    assert result["validation_blocked_completion"] is True
    assert result["validation"]["status"] == "workspace_repository_mismatch"
    assert str(tmp_path) not in json.dumps(result["validation"])
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_cross_repository_validation_rejects_dirty_worktree_without_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    task_repository, worktree, workspace_receipt = _cross_repository_worktree(
        tmp_path,
        name="repo-b",
    )
    (worktree / "uncommitted-artifact").write_text("dirty\n", encoding="utf-8")
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps([sys.executable, "-c", "pass"]),
        task_repository=task_repository,
    )
    calls = {"count": 0}

    def forbidden_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        raise AssertionError("validation command must not run")

    monkeypatch.setattr(completion_validation_module, "run_caller_validation", forbidden_runner)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="dirty worktree claim",
        completion_delivery_workspace=workspace_receipt,
        completion_validation_workspace_path=worktree,
    )

    assert calls["count"] == 0
    assert result["validation_blocked_completion"] is True
    assert result["validation"]["status"] == "workspace_dirty"
    assert str(tmp_path) not in json.dumps(result["validation"])
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_malformed_delivery_workspace_receipt_returns_typed_failure_without_running_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command=_PASS_COMMAND,
        task_repository="git:github.com/example/delivery",
    )
    calls = {"count": 0}

    def unexpected_validation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["count"] += 1
        raise AssertionError("malformed workspace receipts must block before validation")

    monkeypatch.setattr(
        completion_validation_module,
        "run_caller_validation",
        unexpected_validation,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="malformed workspace receipt",
        completion_delivery_workspace={
            "schema_version": "delivery_workspace_v1",
            "workspace_identity": "git:github.com/example/delivery",
            "identity_kind": "unsupported_kind",
            "task_repository": "git:github.com/example/delivery",
            "repository_source": "turn.delivery_workspace",
            "workspace_kind": "independent_git_worktree",
            "peer_independent_worktree_required": True,
        },
    )

    assert calls["count"] == 0
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    assert result["validation"]["status"] == "workspace_receipt_invalid"
    assert result["validation"]["local_path_captured"] is False
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_missing_validation_executable_returns_typed_receipt(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    missing_executable = tmp_path / "private" / "nonexistent-binary"
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps([str(missing_executable)]),
    )
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "command_not_run"
    assert str(missing_executable) not in json.dumps(receipt)
    assert receipt["local_path_captured"] is False
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_malformed_validation_command_returns_typed_receipt(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    # Unbalanced quote -> shlex.split raises ValueError -> typed receipt.
    todo = _add_todo(registry, validation_command="echo 'unbalanced")
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "command_malformed"
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_validation_command_declared_and_failing_blocks_completion(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command=_FAIL_COMMAND,
        validation_label="caller-declared smoke",
    )
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    # Completion is blocked: nothing committed, evidence stays only a claim.
    assert result["ok"] is False
    assert result["completed"] is False
    assert result["changed"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["exit_code"] == 1
    assert receipt["command_label"] == "caller-declared smoke"
    # Privacy invariant preserved.
    assert receipt["stdout_captured"] is False
    assert receipt["stderr_captured"] is False
    assert receipt["local_path_captured"] is False
    # State is unchanged: the todo is still open.
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_no_validation_command_keeps_fast_path_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry)  # no validation_command declared
    transaction_calls = _record_completion_runtime_calls(monkeypatch)
    note = "post-merge note parity"
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="plain completion",
        note=note,
    )
    assert result["ok"] is True
    assert result["changed"] is True
    assert [
        method for method in transaction_calls
        if method == "todo.completion.reduce"
    ] == ["todo.completion.reduce"]
    assert "todo.completion_policy.resolve" not in transaction_calls
    assert "validation_blocked_completion" not in result
    persisted = _agent_todo(state, str(todo["todo_id"]))
    assert persisted["status"] == "done"
    assert persisted["note"] == note


def test_validation_receipt_cannot_commit_a_changed_completion_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command=_PASS_COMMAND,
        validation_label="caller-declared smoke",
    )
    original_validation = (
        completion_validation_module._run_declared_completion_validation
    )

    def validate_then_drift(**kwargs):  # type: ignore[no-untyped-def]
        receipt = original_validation(**kwargs)
        source = state.read_text(encoding="utf-8")
        state.write_text(
            source.replace(
                "validation_label=caller-declared%20smoke",
                "validation_label=drifted%20smoke",
            ),
            encoding="utf-8",
        )
        return receipt

    monkeypatch.setattr(
        completion_validation_module,
        "_run_declared_completion_validation",
        validate_then_drift,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="stale validation receipt",
    )

    assert result["ok"] is False
    assert result["completion_source_drift"] is True
    assert result["changed"] is False
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_validation_receipt_cannot_commit_changed_completion_policy_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command=_PASS_COMMAND,
        validation_label="caller-declared smoke",
    )
    original_validation = (
        completion_validation_module._run_declared_completion_validation
    )

    def validate_then_change_registry(**kwargs):  # type: ignore[no-untyped-def]
        receipt = original_validation(**kwargs)
        payload = json.loads(registry.read_text(encoding="utf-8"))
        payload["goals"][0]["coordination"]["registered_agents"].append(
            "codex-new-peer"
        )
        registry.write_text(json.dumps(payload), encoding="utf-8")
        return receipt

    monkeypatch.setattr(
        completion_validation_module,
        "_run_declared_completion_validation",
        validate_then_change_registry,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="stale registry projection",
    )

    assert result["ok"] is False
    assert result["completion_source_drift"] is True
    assert result["changed"] is False
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_validation_timeout_blocks_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        completion_validation_module, "_COMPLETION_VALIDATION_TIMEOUT_SECONDS", 0.5
    )
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry, validation_command=_SLEEP_COMMAND)
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "timeout"
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_terminal_replay_short_circuits_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry, validation_command=_PASS_COMMAND)

    original_runner = completion_validation_module.run_caller_validation
    calls = {"count": 0}

    def counting_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        return original_runner(*args, **kwargs)

    monkeypatch.setattr(completion_validation_module, "run_caller_validation", counting_runner)

    first = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="validated completion",
    )
    assert first["ok"] is True
    assert calls["count"] == 1  # validation ran once on the real completion

    replay = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="duplicate completion",
    )
    # Replay short-circuits before the validation gate; the command is not re-run.
    assert calls["count"] == 1
    assert replay["ok"] is True


def test_per_todo_validation_timeout_overrides_default(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    # A 1s per-todo timeout cuts the sleeping command off long before the 20s
    # module default, and the typed receipt reports the declared value.
    todo = _add_todo(
        registry,
        validation_command=_SLEEP_COMMAND,
        validation_timeout_seconds=1,
    )
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "timeout"
    assert "timed out after 1s" in receipt["summary"]
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_validation_timeout_out_of_range_rejected(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="1 and 29"):
        _add_todo(
            registry,
            validation_command=_PASS_COMMAND,
            validation_timeout_seconds=30,
        )


def test_validation_timeout_requires_validation_command(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="requires --validation-command"):
        _add_todo(registry, validation_timeout_seconds=5)


def test_validation_command_json_passing_commits_completion(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    pass_argv = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
    todo = _add_todo(
        registry,
        validation_command_json=pass_argv,
        validation_label="argv-form smoke",
    )
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="validated completion",
    )
    assert result["ok"] is True
    assert "validation_blocked_completion" not in result
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_validation_command_json_failing_blocks_completion(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    fail_argv = json.dumps([sys.executable, "-c", "raise SystemExit(1)"])
    todo = _add_todo(registry, validation_command_json=fail_argv)
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["exit_code"] == 1
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_validation_command_forms_mutually_exclusive(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        _add_todo(
            registry,
            validation_command=_PASS_COMMAND,
            validation_command_json=json.dumps([sys.executable, "-c", "pass"]),
        )


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        '{"not":"a list"}',
        "[]",
        json.dumps([sys.executable, 123]),
        json.dumps([sys.executable, ""]),
    ],
)
def test_validation_command_json_must_be_nonempty_string_array(
    tmp_path: Path, payload: str
) -> None:
    registry, _state = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="must be a JSON string array"):
        _add_todo(registry, validation_command_json=payload)


def test_validation_timeout_works_with_command_json(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    sleep_argv = json.dumps([sys.executable, "-c", "import time; time.sleep(30)"])
    todo = _add_todo(
        registry,
        validation_command_json=sleep_argv,
        validation_timeout_seconds=1,
    )
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    receipt = result["validation"]
    assert receipt["status"] == "timeout"
    assert "timed out after 1s" in receipt["summary"]
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_corrupted_argv_declaration_fails_closed(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(
        registry,
        validation_command_json=json.dumps(
            [sys.executable, "-c", "raise SystemExit(0)"]
        ),
    )
    # Corrupt the persisted argv declaration in place; completion must run the
    # gate and fail closed as a malformed command, never silently skip it.
    text = state.read_text(encoding="utf-8")
    corrupted, substitutions = re.subn(
        r"validation_command_argv=\S+",
        "validation_command_argv=%5Bbroken",
        text,
    )
    assert substitutions == 1
    state.write_text(corrupted, encoding="utf-8")
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        agent_id=AGENT,
        evidence="claim of completion",
    )
    assert result["ok"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["status"] == "declaration_invalid"
    assert "validation_command_argv" in receipt["summary"]
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_empty_argv_declaration_reports_neutral_message(
    tmp_path: Path,
) -> None:
    # An argv declaration collapsing to [] (e.g. corrupted on disk) surfaces
    # the form-neutral empty-command error inside the malformed receipt.
    registry, _state = _write_fixture(tmp_path)
    receipt = completion_validation_module._run_declared_completion_validation(
        validation_command=None,
        validation_argv=[],
        validation_label=None,
        validation_timeout_seconds=None,
        registry_path=registry,
        goal_id=GOAL_ID,
    )
    assert receipt is not None
    assert receipt["status"] == "command_malformed"
    assert "validation command must not be empty" in receipt["summary"]


def _user_todo(state: Path, todo_id: str) -> dict:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    return next(
        item
        for item in todos["user_todos"]["items"]
        if item["todo_id"] == todo_id
    )


def _spy_validation_runner(monkeypatch: pytest.MonkeyPatch) -> dict:
    original_runner = completion_validation_module.run_caller_validation
    calls = {"count": 0}

    def counting_runner(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        return original_runner(*args, **kwargs)

    monkeypatch.setattr(
        completion_validation_module, "run_caller_validation", counting_runner
    )
    return calls


def test_user_todo_update_done_runs_declared_validation_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Operator-recorded outcome.",
        task_class="user_action",
        validation_command=_FAIL_COMMAND,
        validation_label="caller-declared smoke",
    )
    calls = _spy_validation_runner(monkeypatch)
    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        status="done",
        evidence="claim of completion",
    )
    # The declared command ran once and blocked the update: nothing committed.
    assert calls["count"] == 1
    assert result["ok"] is False
    assert result["changed"] is False
    assert result["validation_blocked_completion"] is True
    receipt = result["validation"]
    assert receipt["passed"] is False
    assert receipt["exit_code"] == 1
    assert receipt["stdout_captured"] is False
    assert _user_todo(state, str(todo["todo_id"]))["status"] == "open"


def test_user_todo_update_done_without_declaration_keeps_fast_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Operator-recorded outcome.",
        task_class="user_action",
    )
    calls = _spy_validation_runner(monkeypatch)
    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        status="done",
    )
    assert calls["count"] == 0
    assert result["ok"] is True
    assert "validation_blocked_completion" not in result
    assert _user_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_repeated_done_update_does_not_rerun_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Operator-recorded outcome.",
        task_class="user_action",
        validation_command=_PASS_COMMAND,
    )
    calls = _spy_validation_runner(monkeypatch)
    first = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        status="done",
        evidence="validated completion",
    )
    assert first["ok"] is True
    assert calls["count"] == 1  # the passing command ran once via the update gate
    assert _user_todo(state, str(todo["todo_id"]))["status"] == "done"

    second = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        status="done",
        note="re-acknowledged",
    )
    # Already-completed short-circuit: the command is not re-run.
    assert second["ok"] is True
    assert calls["count"] == 1
    assert _user_todo(state, str(todo["todo_id"]))["status"] == "done"


def test_agent_todo_update_done_keeps_guard_error_without_running_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_todo(registry, validation_command=_FAIL_COMMAND)
    calls = _spy_validation_runner(monkeypatch)
    with pytest.raises(ValueError, match="must use complete_goal_todo"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=str(todo["todo_id"]),
            status="done",
        )
    # The gate never runs for agent sections; the pre-existing guard fires.
    assert calls["count"] == 0
    assert _agent_todo(state, str(todo["todo_id"]))["status"] == "open"








def _set_registry_event_log(registry: Path, event_log: Path) -> None:
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["goals"][0]["event_log"] = str(event_log)
    registry.write_text(json.dumps(data), encoding="utf-8")




def test_private_validation_store_is_owner_only_and_detects_tampering(
    tmp_path: Path,
) -> None:
    declaration = {
        "validation_command": None,
        "validation_command_argv": [sys.executable, "-c", "pass"],
        "validation_label": "private validation",
        "validation_timeout_seconds": 5,
    }
    digest = persist_completion_validation_declaration(
        runtime_root=tmp_path,
        goal_id=GOAL_ID,
        todo_id="todo_private_validation",
        declaration=declaration,
    )
    path = completion_validation_declaration_path(
        runtime_root=tmp_path,
        goal_id=GOAL_ID,
        todo_id="todo_private_validation",
    )

    assert path.stat().st_mode & 0o777 == 0o600
    assert read_completion_validation_declaration(
        runtime_root=tmp_path,
        goal_id=GOAL_ID,
        todo_id="todo_private_validation",
    ) == declaration
    assert digest == completion_validation_declaration_sha256(declaration)

    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["declaration"]["validation_label"] = "tampered validation"
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        read_completion_validation_declaration(
            runtime_root=tmp_path,
            goal_id=GOAL_ID,
            todo_id="todo_private_validation",
        )


def test_canonical_validation_marker_fails_closed_without_private_declaration(
    tmp_path: Path,
) -> None:
    canonical = project_completion_validation_authority(
        {"validation_command_argv": [sys.executable, "-c", "pass"]}
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": 1, "goals": [{"id": GOAL_ID}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="declaration is unavailable"):
        resolve_private_completion_validation_declaration(
            canonical_todo=canonical,
            state_file=tmp_path / "missing.md",
            runtime_root=tmp_path / "runtime",
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id="todo_missing_private_validation",
            role="agent",
            persist_if_resolved=True,
        )


def test_canonical_validation_digest_rejects_different_private_declaration(
    tmp_path: Path,
) -> None:
    todo_id = "todo_mismatched_private_validation"
    canonical = project_completion_validation_authority(
        {"validation_command_argv": [sys.executable, "-c", "pass"]}
    )
    persist_completion_validation_declaration(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo_id,
        declaration={
            "validation_command": None,
            "validation_command_argv": [sys.executable, "-c", "raise SystemExit(1)"],
            "validation_label": None,
            "validation_timeout_seconds": None,
        },
    )

    with pytest.raises(ValueError, match="does not match canonical Todo digest"):
        resolve_private_completion_validation_declaration(
            canonical_todo=canonical,
            state_file=tmp_path / "missing.md",
            runtime_root=tmp_path / "runtime",
            registry_path=tmp_path / "missing-registry.json",
            goal_id=GOAL_ID,
            todo_id=todo_id,
            role="agent",
            persist_if_resolved=False,
        )
