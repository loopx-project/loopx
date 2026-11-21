"""Builtin completion uses the real Todo validator and canonical lease fence."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
from loopx.cli import build_parser, main
from loopx.cli_commands import turn_run_once
from loopx.cli_commands.turn import handle_turn_command
from loopx.control_plane.todos.mutation_api import add_goal_todo
from loopx.control_plane.turn_driver.codex_cli import codex_cli_result_schema
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_codex_cli import _fake_codex
from tests.test_loopx_turn_driver import (
    _completion_host_and_validation_scripts,
    _turn_journal,
    _write_live_fixture,
)


def _cli(registry: Path, runtime: Path, *args: str) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                *args,
            ]
        )
    return code, json.loads(output.getvalue())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "scenario",
    [
        "completion",
        "terminal",
        "progress",
        "independent_validation",
        "declared_validation",
        "stale_lease",
        "stop_after_progress_validation",
        "stop_after_completion_validation",
    ],
)
def test_builtin_codex_completes_before_accountable_writeback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    scenario: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    terminal = scenario == "terminal"
    progress = scenario in {"progress", "stop_after_progress_validation"}
    stopped_after_validation = scenario in {
        "stop_after_progress_validation",
        "stop_after_completion_validation",
    }
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        provider=provider,
        handoff_mode="hard_lease",
        todo_metadata_extra="no_followup=true" if terminal else "",
    )
    added = (
        {"todo_id": "todo_fixture0001"}
        if terminal
        else add_goal_todo(
            registry_path=registry,
            goal_id="loopx-turn-fixture",
            role="agent",
            text="Deliver the independently checked artifact.",
            task_class="advancement_task",
            action_kind="fixture",
            claimed_by="codex-fixture",
            agent_id="codex-fixture",
            validation_command_json=None
            if progress
            else json.dumps(
                [
                    sys.executable,
                    "-c",
                    "raise SystemExit(1)"
                    if scenario == "declared_validation"
                    else "raise SystemExit(0)",
                ]
            ),
        )
    )
    todo_id = added["todo_id"]
    claim_code, claim = _cli(
        registry,
        runtime,
        "todo",
        "claim",
        "--goal-id",
        "loopx-turn-fixture",
        "--todo-id",
        todo_id,
        "--agent-id",
        "codex-fixture",
        "--claimed-by",
        "codex-fixture",
    )
    assert claim_code == 0, claim
    if terminal:
        code, current = _cli(
            registry, runtime, "todo", "list", "--goal-id", "loopx-turn-fixture"
        )
        assert code == 0, current
        code, declaration = _cli(
            registry,
            runtime,
            "todo",
            "update",
            "--goal-id",
            "loopx-turn-fixture",
            "--todo-id",
            todo_id,
            "--agent-id",
            "codex-fixture",
            "--claim-operation-id",
            claim["claim_operation_id"],
            "--validation-command-json",
            json.dumps([sys.executable, "-c", "raise SystemExit(0)"]),
            "--update-operation-id",
            "declare-terminal-validation",
            "--update-expected-provider-revision",
            current["authority_read"]["provider_revision"],
        )
        assert code == 0, declaration
    binary, host_log = _fake_codex(tmp_path)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(host_log))
    monkeypatch.setenv(
        "FAKE_CODEX_RESULT_FIELDS",
        json.dumps(
            {
                "result_kind": "validated_progress" if progress else "validated_completion",
            }
        ),
    )
    if scenario == "stale_lease":
        code, inspection = _cli(
            registry,
            runtime,
            "task-lease",
            "inspect",
            "--goal-id",
            "loopx-turn-fixture",
            "--todo-id",
            todo_id,
        )
        assert code == 0 and inspection["active"] is True, inspection
        lease = inspection["lease"]
        run_codex = turn_run_once.run_codex_cli_host

        def host(request, **kwargs):
            # Renew after launch capture. A newer generation of the same owner
            # still cannot authorize this result.
            code, renewed = _cli(
                registry,
                runtime,
                "task-lease",
                "renew",
                "--goal-id",
                "loopx-turn-fixture",
                "--todo-id",
                todo_id,
                "--owner",
                "codex-fixture",
                "--idempotency-key",
                lease["idempotency_key"],
                "--expected-version",
                str(lease["version"]),
            )
            assert code == 0, renewed
            return run_codex(request, **kwargs)

        monkeypatch.setattr(turn_run_once, "run_codex_cli_host", host)
    if stopped_after_validation:
        build_validator = turn_run_once.build_loopx_turn_command_validator

        def validate_then_stop(*args, **kwargs):
            validate = build_validator(*args, **kwargs)

            def checked(plan, result):
                observation = validate(plan, result)
                assert observation["status"] == "passed", observation
                lifecycle = ("goal-lifecycle", "--goal-id", "loopx-turn-fixture", "--operation", "stop")
                code, preview = _cli(registry, runtime, *lifecycle)
                assert code == 0 and not preview["written"], preview
                code, stopped = _cli(
                    registry, runtime, *lifecycle, "--actor-kind", "owner",
                    "--expected-state-fingerprint", preview["observed_state_fingerprint"], "--execute",
                )
                assert code == 0 and stopped["readback"]["verified"], stopped
                assert stopped["after_state"] == "stopped", stopped
                return observation

            return checked

        monkeypatch.setattr(turn_run_once, "build_loopx_turn_command_validator", validate_then_stop)
    argv = [
        "turn",
        "run-once",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--todo-id",
        todo_id,
        "--host",
        "codex-cli",
        "--project",
        str(project),
        "--codex-bin",
        str(binary),
        "--codex-model",
        "fixture-model",
        "--codex-reasoning-effort",
        "high",
        "--codex-sandbox",
        "read-only",
        "--validation-command-json",
        json.dumps(
            [
                sys.executable,
                "-c",
                "raise SystemExit(1)"
                if scenario == "independent_validation"
                else "raise SystemExit(0)",
            ]
        ),
        "--no-global-sync",
        "--execute",
    ]
    code, result = _cli(registry, runtime, *argv)
    journals = list((runtime / "goals" / "loopx-turn-fixture" / "turns").glob("*.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text())
    assert len(host_log.read_text().splitlines()) == 1
    if stopped_after_validation:
        assert code == 1 and result["error_code"] == "goal_stopped", result
        assert journal["completed_phases"] == ["host_execute", "typed_result", "validation"]
        read_code, readback = _cli(
            registry, runtime, "todo", "list", "--goal-id", "loopx-turn-fixture", "--todo-id", todo_id,
        )
        assert read_code == 0 and readback["todo"]["status"] == "open", readback
        return
    if scenario in {"independent_validation", "declared_validation", "stale_lease"}:
        assert result["status"] != "committed", result
        assert "quota_spend" not in journal["completed_phases"]
        assert "durable_writeback" not in journal["completed_phases"]
        if scenario == "stale_lease":
            recovery_args = argv[:-1]
            todo_option = recovery_args.index("--todo-id")
            del recovery_args[todo_option : todo_option + 2]
            code, retried = _cli(
                registry,
                runtime,
                *recovery_args,
                "--resume-turn-key",
                journal["turn_key"],
                "--execute",
            )
            assert retried.get("status") != "committed", retried
            assert len(host_log.read_text().splitlines()) == 1
        return
    assert code == 0 and result["status"] == "committed", result
    kinds = _effect_kinds(runtime, journal)
    if progress:
        assert "completion" not in journal["writeback"]
        assert kinds == ["refresh_state", "quota_spend"]
        read_code, readback = _cli(
            registry, runtime, "todo", "list", "--goal-id", "loopx-turn-fixture"
        )
        assert read_code == 0, readback
        assert (
            next(todo for todo in readback["todos"] if todo["todo_id"] == todo_id)[
                "status"
            ]
            == "open"
        )
    else:
        completion = {
            "todo_id": todo_id,
            "continuation": "no_followup" if terminal else "active_goal",
        }
        assert journal["writeback"]["completion"] == completion
        assert kinds == ["todo_complete", "refresh_state", "quota_spend"]
        if terminal:
            assert journal["terminal_closeout"]["completion"] == completion
    replay_args = argv[:-1]
    todo_option = replay_args.index("--todo-id")
    del replay_args[todo_option : todo_option + 2]
    code, replay = _cli(
        registry,
        runtime,
        *replay_args,
        "--resume-turn-key",
        journal["turn_key"],
        "--execute",
    )
    assert code == 0 and replay["replayed"] is True, replay
    assert len(host_log.read_text().splitlines()) == 1
    assert not any(replay["effects"].values())
    assert _effect_kinds(runtime, journal) == kinds


def _effect_kinds(runtime: Path, journal: dict) -> list[str]:
    identity = journal["plan"]["transaction"]
    run_id = identity.get("turn_instance_id", journal["turn_key"])
    path = runtime / "goals" / "loopx-turn-fixture" / "rollout-event-log.jsonl"
    rows = (json.loads(line) for line in path.read_text().splitlines())
    return [
        row["event_kind"]
        for row in rows
        if row.get("run_id") == run_id
        and row["event_kind"] in {"refresh_state", "quota_spend", "todo_complete"}
    ]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "interruption", [None, "after_debit", "lost_completion_receipt"]
)
def test_native_terminal_closeout_keeps_the_original_lease_and_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    interruption: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        provider=provider,
        handoff_mode="hard_lease",
        todo_metadata_extra="no_followup=true",
    )
    goal_id, agent_id, todo_id = (
        "loopx-turn-fixture",
        "codex-fixture",
        "todo_fixture0001",
    )
    code, claim = _cli(
        registry,
        runtime,
        "todo",
        "claim",
        "--goal-id",
        goal_id,
        "--todo-id",
        todo_id,
        "--agent-id",
        agent_id,
        "--claimed-by",
        agent_id,
    )
    assert code == 0, claim
    lease_argv = ("task-lease", "inspect", "--goal-id", goal_id, "--todo-id", todo_id)
    code, inspection = _cli(registry, runtime, *lease_argv)
    assert code == 0 and inspection["active"] is True, inspection
    lease = inspection["lease"]
    adapter = SimpleNamespace(
        controller=SimpleNamespace(
            registry_path=registry, coordination_runtime_root=runtime
        )
    )
    host_calls, lease_reads = [], []

    def resolve_lease():
        lease_reads.append(lease["version"])
        return StewardTurnHost._task_lease_proof(
            adapter, {"actor": {"goal_id": goal_id}, "todo_id": todo_id}, lease
        )

    def host(request, launch_lifetime):
        with launch_lifetime():
            host_calls.append(request["turn_key"])
            (project / "completion-artifact.txt").write_text(
                "completed", encoding="utf-8"
            )
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": request["turn_key"],
            "result_kind": "validated_completion",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "fixture_completion",
            "recommended_action": "Refresh the active Goal.",
            "next_action": "Read the fresh decision.",
            "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "The remaining Goal is unchanged.",
            "work_evidence": ["artifact:completion-artifact"],
            "validation_evidence": ["check:artifact-contents"],
        }

    _, validation = _completion_host_and_validation_scripts()
    base = [
        "turn",
        "run-once",
        "--host",
        "generic-cli",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--no-global-sync",
        "--validation-command-json",
        json.dumps([sys.executable, "-c", validation]),
        "--execute",
    ]

    def invoke(*extra: str):
        args = build_parser().parse_args([*base, *extra])
        args.host, args.session_scope = "codex-app-server", "agent"
        result: dict = {}
        code = handle_turn_command(
            args,
            registry_path=registry,
            runtime_root_arg=str(runtime),
            output_format=lambda *_args, **_kwargs: "json",
            print_payload=lambda value, *_args: result.update(value),
            native_host_runner=host,
            native_completion_lease_resolver=resolve_lease,
            native_session_binding_resolver=lambda _: {
                "schema_version": "loopx_turn_session_binding_v0",
                "goal_id": goal_id,
                "agent_id": agent_id,
            },
        )
        return code, result

    complete = turn_run_once.write_turn_validated_completion
    if interruption is not None:

        def lose_terminal_attempt(**kwargs):
            assert _effect_kinds(runtime, _turn_journal(runtime)) == [
                "refresh_state",
                "quota_spend",
            ]
            if interruption == "lost_completion_receipt":
                applied = complete(**kwargs)
                assert applied["ok"] and applied["completed"], applied
            raise OSError("injected terminal completion interruption")

        monkeypatch.setattr(
            turn_run_once, "write_turn_validated_completion", lose_terminal_attempt
        )
    code, result = invoke("--todo-id", todo_id)
    journal = _turn_journal(runtime)
    if interruption is not None:
        assert code == 1 and journal["status"] == "in_progress", result
        assert journal["completed_phases"][-1] == "quota_spend"
        assert _effect_kinds(runtime, journal) == ["refresh_state", "quota_spend"]
        assert _cli(registry, runtime, *lease_argv)[1]["active"] is (
            interruption == "after_debit"
        )
        monkeypatch.setattr(turn_run_once, "write_turn_validated_completion", complete)
        code, result = invoke(
            "--resume-turn-key", journal["turn_key"], "--retry-failed-turn"
        )
    assert code == 0 and result["status"] == "committed", result.get(
        "error"
    ) or result.get("reason")
    assert _turn_journal(runtime)["terminal_closeout"]["completion"] == {
        "todo_id": todo_id,
        "continuation": "no_followup",
    }
    assert len(lease_reads) == (2 if interruption == "after_debit" else 1)
    assert _cli(registry, runtime, *lease_argv)[1]["active"] is False
    assert _effect_kinds(runtime, journal) == [
        "refresh_state",
        "quota_spend",
        "todo_complete",
    ]
    code, replay = invoke("--resume-turn-key", journal["turn_key"])
    assert code == 0 and replay["replayed"] is True, replay
    assert not any(replay["effects"].values())
    assert host_calls == [journal["turn_key"]]
    assert _effect_kinds(runtime, journal) == [
        "refresh_state",
        "quota_spend",
        "todo_complete",
    ]


def test_builtin_codex_advertises_explicit_completion_without_promoting_progress() -> (
    None
):
    kinds = codex_cli_result_schema()["properties"]["result_kind"]["enum"]
    assert "validated_completion" in kinds and "validated_progress" in kinds


def test_resumed_turn_rebinds_updated_lease_once_without_rereading_after_retire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path, provider="sqlite", handoff_mode="hard_lease"
    )
    goal_id, agent_id, todo_id = "loopx-turn-fixture", "codex-fixture", "todo_fixture0001"
    # Keep a second open Todo so completion_writeback handles non-terminal completion.
    add_goal_todo(
        registry_path=registry,
        goal_id=goal_id,
        runtime_root_arg=str(runtime),
        role="agent",
        text="Follow-up verification task",
        task_class="advancement_task",
        action_kind="fixture",
    )
    code, claim = _cli(
        registry, runtime, "todo", "claim", "--goal-id", goal_id,
        "--todo-id", todo_id, "--agent-id", agent_id, "--claimed-by", agent_id,
    )
    assert code == 0, claim
    lease_argv = ("task-lease", "inspect", "--goal-id", goal_id, "--todo-id", todo_id)
    code, inspection = _cli(registry, runtime, *lease_argv)
    assert code == 0 and inspection["active"] is True, inspection
    lease = inspection["lease"]
    adapter = SimpleNamespace(
        controller=SimpleNamespace(registry_path=registry, coordination_runtime_root=runtime)
    )
    host_calls, lease_reads = [], []

    def resolve_lease():
        lease_reads.append(lease["lease_epoch"])
        return StewardTurnHost._task_lease_proof(
            adapter, {"actor": {"goal_id": goal_id}, "todo_id": todo_id}, lease
        )

    def host(request, launch_lifetime):
        with launch_lifetime():
            host_calls.append(request["turn_key"])
            (project / "completion-artifact.txt").write_text("completed", encoding="utf-8")
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": request["turn_key"],
            "result_kind": "validated_completion",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "fixture_completion",
            "recommended_action": "Continue with the remaining Todo.",
            "next_action": "Inspect the next Todo.",
            "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "The remaining Goal is unchanged.",
            "work_evidence": ["artifact:completion-artifact"],
            "validation_evidence": ["check:artifact-contents"],
        }

    _, validation = _completion_host_and_validation_scripts()
    base = [
        "turn", "run-once", "--host", "generic-cli", "--goal-id", goal_id,
        "--agent-id", agent_id, "--project", str(project), "--no-global-sync",
        "--validation-command-json", json.dumps([sys.executable, "-c", validation]),
        "--execute",
    ]

    def invoke(*extra: str):
        args = build_parser().parse_args([*base, *extra])
        args.host, args.session_scope = "codex-app-server", "agent"
        result: dict = {}
        exit_code = handle_turn_command(
            args,
            registry_path=registry,
            runtime_root_arg=str(runtime),
            output_format=lambda *_args, **_kwargs: "json",
            print_payload=lambda value, *_args: result.update(value),
            native_host_runner=host,
            native_completion_lease_resolver=resolve_lease,
            native_session_binding_resolver=lambda _: {
                "schema_version": "loopx_turn_session_binding_v0",
                "goal_id": goal_id,
                "agent_id": agent_id,
            },
        )
        return exit_code, result

    complete = turn_run_once.write_turn_validated_completion
    monkeypatch.setattr(
        turn_run_once,
        "write_turn_validated_completion",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("interrupted before completion")),
    )
    code, failed = invoke("--todo-id", todo_id)
    journal = _turn_journal(runtime)
    assert code == 1 and journal["status"] == "in_progress", failed
    assert lease_reads == [1]

    # Renew the active lease (epoch 2) before resuming from durable_writeback.
    code, released = _cli(
        registry, runtime, "task-lease", "release", "--goal-id", goal_id,
        "--todo-id", todo_id, "--owner", agent_id,
        "--idempotency-key", lease["idempotency_key"],
        "--expected-version", str(lease["version"]),
    )
    assert code == 0, released
    code, reclaimed = _cli(
        registry, runtime, "todo", "claim", "--goal-id", goal_id,
        "--todo-id", todo_id, "--agent-id", agent_id, "--claimed-by", agent_id,
        "--claim-operation-id", "todo-claim:renewed-epoch-2",
    )
    assert code == 0, reclaimed
    code, renewed_inspection = _cli(registry, runtime, *lease_argv)
    assert code == 0 and renewed_inspection["active"] is True
    lease = renewed_inspection["lease"]
    assert lease["lease_epoch"] == 2

    monkeypatch.setattr(turn_run_once, "write_turn_validated_completion", complete)
    code, resumed = invoke("--resume-turn-key", journal["turn_key"], "--retry-failed-turn")
    assert code == 0 and resumed["status"] == "committed", resumed
    assert lease_reads == [1, 2]
    assert _cli(registry, runtime, *lease_argv)[1]["active"] is False

    code, replay = invoke("--resume-turn-key", journal["turn_key"])
    assert code == 0 and replay["replayed"] is True, replay
    assert lease_reads == [1, 2]
    assert host_calls == [journal["turn_key"]]
