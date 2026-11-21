"""Builtin completion uses the real Todo validator and canonical lease fence."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
from loopx.cli import build_parser, main
from loopx.cli_commands import turn_run_once
from loopx.cli_commands.turn import handle_turn_command
from loopx.control_plane.todos.mutation_api import add_goal_todo
from loopx.control_plane.work_items.task_lease import (
    release_task_lease,
    renew_task_lease,
)
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
@pytest.mark.parametrize("exact_ref", [False, True])
@pytest.mark.parametrize(
    "interruption", [None, "after_debit", "lost_completion_receipt"]
)
def test_native_terminal_closeout_keeps_the_original_lease_and_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    exact_ref: bool,
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
    goal_ref = None
    if exact_ref:
        goal_ref = {"goal_id": goal_id, "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        registration = json.loads(registry.read_text())
        registration["goals"][0]["goal_instance_id"] = goal_ref["goal_instance_id"]
        registry.write_text(json.dumps(registration) + "\n")
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
    host_calls, lease_reads, host_exceptions = [], [], []

    def resolve_lease():
        lease_reads.append(lease["version"])
        return StewardTurnHost._task_lease_proof(
            adapter, {"actor": {"goal_id": goal_id}, "todo_id": todo_id}, lease
        )

    def host(request, launch_lifetime):
        try:
            with launch_lifetime():
                host_calls.append(request["turn_key"])
                (project / "completion-artifact.txt").write_text(
                    "completed", encoding="utf-8"
                )
        except Exception as exc:
            host_exceptions.append({
                "type": type(exc).__name__,
                "message": str(exc),
                "code": getattr(exc, "diagnostic_code", None) or getattr(exc, "code", None),
                "payload": getattr(exc, "payload", None),
                "frames": [{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                           for frame in traceback.extract_tb(exc.__traceback__)],
            })
            raise
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

    def invoke(*extra: str, current_ref=None):
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
            native_goal_ref=current_ref if current_ref is not None else goal_ref,
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
    assert journal["plan"].get("goal_ref") == goal_ref, result
    event_path = runtime / "goals" / goal_id / "rollout-event-log.jsonl"
    run_id = journal["plan"]["transaction"]["turn_instance_id"]

    def original_receipt_lines():
        return [line for line in event_path.read_bytes().splitlines(keepends=True)
                if (row := json.loads(line)).get("event_kind") == "quota_should_run"
                and row.get("run_id") == run_id and row.get("agent_id") == agent_id]

    receipt_bytes = original_receipt_lines()
    assert receipt_bytes
    if interruption is not None:
        assert code == 1 and journal["status"] == "in_progress", result
        assert journal["completed_phases"][-1] == "quota_spend"
        assert _effect_kinds(runtime, journal) == ["refresh_state", "quota_spend"]
        assert _cli(registry, runtime, *lease_argv)[1]["active"] is (
            interruption == "after_debit"
        )
        monkeypatch.setattr(turn_run_once, "write_turn_validated_completion", complete)
        code, result = invoke(
            "--resume-turn-key", journal["turn_key"], "--retry-failed-turn",
            current_ref={"goal_id": goal_id, "goal_instance_id": "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
            if exact_ref else None,
        )
    assert code == 0 and result["status"] == "committed", json.dumps(
        {"execution": result, "host_exceptions": host_exceptions},
        ensure_ascii=False, indent=2, default=str)
    final_journal = _turn_journal(runtime)
    assert final_journal["plan"].get("goal_ref") == goal_ref
    assert original_receipt_lines() == receipt_bytes
    assert final_journal["terminal_closeout"]["completion"] == {
        "todo_id": todo_id,
        "continuation": "no_followup",
    }
    assert lease_reads and set(lease_reads) == {lease["version"]}
    if interruption is None:
        assert len(lease_reads) == 1
    else:
        # Resume rechecks the original lease at each remaining effect boundary.
        # Correctness is the retained proof and once-only effects below, rather
        # than the old resolver implementation's exact number of reads.
        assert len(lease_reads) >= 2
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
    assert _turn_journal(runtime)["plan"].get("goal_ref") == goal_ref
    assert original_receipt_lines() == receipt_bytes
    assert _effect_kinds(runtime, journal) == [
        "refresh_state",
        "quota_spend",
        "todo_complete",
    ]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "scenario",
    [
        "init_replacement",
        "completion_replacement",
        "writeback_replacement",
        "renewed_retired_writeback",
        "accepted_writeback_spend",
        "resolver_value_error",
        "resolver_runtime_error",
        "resolver_timeout_error",
    ],
)
def test_native_resume_qualifies_fresh_authority_without_replacing_prepared_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    scenario: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path, provider=provider, handoff_mode="hard_lease",
    )
    goal_id, agent_id, todo_id = "loopx-turn-fixture", "codex-fixture", "todo_fixture0001"
    claim_args = ("todo", "claim", "--goal-id", goal_id, "--todo-id", todo_id,
                  "--agent-id", agent_id, "--claimed-by", agent_id)
    code, claimed = _cli(registry, runtime, *claim_args)
    assert code == 0, claimed

    def inspect_lease():
        code, inspected = _cli(registry, runtime, "task-lease", "inspect",
                               "--goal-id", goal_id, "--todo-id", todo_id)
        assert code == 0, inspected
        return inspected["lease"]

    original_lease = inspect_lease()
    resolver_lease = original_lease
    adapter = SimpleNamespace(controller=SimpleNamespace(
        registry_path=registry, coordination_runtime_root=runtime,
    ))
    host_calls, resolver_errors, completion_calls, refresh_requests = [], [], [], []
    resolver_calls = []
    resolver_fault = None
    progress = scenario == "writeback_replacement"
    accounting_only = scenario == "accepted_writeback_spend"

    def resolve_lease():
        resolver_calls.append(resolver_lease["version"])
        if resolver_fault is not None:
            raise resolver_fault
        try:
            return StewardTurnHost._task_lease_proof(
                adapter, {"actor": {"goal_id": goal_id}, "todo_id": todo_id}, resolver_lease,
            )
        except ValueError as exc:
            resolver_errors.append(str(exc))
            raise

    def host(request, launch_lifetime):
        with launch_lifetime():
            host_calls.append(request["turn_key"])
            (project / "completion-artifact.txt").write_text("completed", encoding="utf-8")
        return {
            "schema_version": "loopx_turn_result_v0", "turn_key": request["turn_key"],
            "result_kind": "validated_progress" if progress else "validated_completion",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "fixture_completion", "recommended_action": "Refresh the active Goal.",
            "next_action": "Read the fresh decision.", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "vision_unchanged_reason": "The remaining Goal is unchanged.",
            "work_evidence": ["artifact:completion-artifact"], "validation_evidence": ["check:artifact-contents"],
        }

    _, validation = _completion_host_and_validation_scripts()
    base = ["turn", "run-once", "--host", "generic-cli", "--goal-id", goal_id,
            "--agent-id", agent_id, "--project", str(project), "--no-global-sync",
            "--validation-command-json", json.dumps([sys.executable, "-c", validation]), "--execute"]

    def invoke(*extra):
        args = build_parser().parse_args([*base, *extra])
        args.host, args.session_scope = "codex-app-server", "agent"
        result = {}
        code = handle_turn_command(
            args, registry_path=registry, runtime_root_arg=str(runtime),
            output_format=lambda *_a, **_kw: "json", print_payload=lambda value, *_a: result.update(value),
            native_host_runner=host, native_completion_lease_resolver=resolve_lease,
            native_session_binding_resolver=lambda _: {
                "schema_version": "loopx_turn_session_binding_v0", "goal_id": goal_id, "agent_id": agent_id,
            },
        )
        return code, result

    def pause_before_commit(*_args, **_kwargs):
        raise OSError("fixture pause before native settlement")

    with monkeypatch.context() as paused:
        pause_target = ("spend_quota_slot" if accounting_only else
                        "refresh_state_run" if progress else "write_turn_validated_completion")
        paused.setattr(turn_run_once, pause_target,
                       pause_before_commit)
        code, failed = invoke("--todo-id", todo_id)
    assert code == 1 and failed["error"] == "fixture pause before native settlement", failed
    journal = _turn_journal(runtime)
    assert journal["host_attempt_count"] == 1 and journal["host_result"]["result_kind"] == (
        "validated_progress" if progress else "validated_completion"
    )
    frozen_result = journal["host_result"]
    transaction = journal["plan"]["transaction"]
    run_id = transaction.get("turn_instance_id", journal["turn_key"])
    event_path = runtime / "goals" / goal_id / "rollout-event-log.jsonl"
    index_path = runtime / "goals" / goal_id / "runs" / "index.jsonl"

    def original_receipt_lines():
        return [line for line in event_path.read_bytes().splitlines(keepends=True)
                if (row := json.loads(line)).get("event_kind") == "quota_should_run"
                and row.get("run_id") == run_id and row.get("agent_id") == agent_id]

    receipt_bytes = original_receipt_lines()
    assert receipt_bytes and _effect_kinds(runtime, journal) == (
        ["todo_complete", "refresh_state"] if accounting_only else []
    )
    original_readback = turn_run_once.read_heartbeat_settlement(
        runtime, goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        turn_instance_id=run_id, registry_path=registry,
    )
    assert original_readback is not None and original_readback.prepared_lease is not None
    original_prepared = {
        "prepared_lease_key_sha256": sha256(original_lease["idempotency_key"].encode()).hexdigest(),
        "prepared_lease_version": original_lease["version"],
        "prepared_lease_epoch": original_lease["lease_epoch"],
    }
    assert original_readback.prepared_lease == original_prepared
    resolver_calls_before_resume = list(resolver_calls)
    if accounting_only:
        # This is accepted business writeback followed by independent accounting.
        # The real active-proof resolver cannot offer the already retired lease.
        retired = inspect_lease()
        assert retired["status"] == "released"
        code, current_todo = _cli(registry, runtime, "todo", "list", "--goal-id", goal_id, "--todo-id", todo_id)
        assert code == 0 and current_todo["todo"]["todo_id"] == todo_id and current_todo["todo"]["status"] == "done", current_todo
        assert original_readback.completion_event is not None
        assert original_readback.writeback.failure is None and original_readback.writeback_run is not None
        assert original_readback.spend_event is None and original_readback.spend_run is None
        assert original_readback.progress is not None and original_readback.progress["state"] == "spend_required"
        assert journal["completed_phases"][-1] == "durable_writeback"
        inspection = turn_run_once.inspect_loopx_turn_journal(
            runtime, goal_id=goal_id, agent_id=agent_id, turn_key=journal["turn_key"], retry_failed=True,
        )
        recovery = inspection["recovery_decision"]
        assert recovery["action"] == "continue" and recovery["can_continue"] is True
        assert recovery["resume_from"] == "quota_spend" and recovery["reinvoke_host"] is False
        with pytest.raises(ValueError, match="original task execution lease changed"):
            StewardTurnHost._task_lease_proof(
                adapter, {"actor": {"goal_id": goal_id}, "todo_id": todo_id}, resolver_lease,
            )
    actual_qualification = turn_run_once.read_turn_execution_lease_qualification
    qualification_requests, qualifications = [], []

    def observe_qualification(**kwargs):
        qualification_requests.append(kwargs["requested_proof"])
        observed = actual_qualification(**kwargs)
        qualifications.append(observed.resume_lease_qualification)
        return observed

    monkeypatch.setattr(turn_run_once, "read_turn_execution_lease_qualification", observe_qualification)
    replacement = None

    def replace_execution(*, offer_current=True):
        nonlocal replacement, resolver_lease
        current = inspect_lease()
        released = release_task_lease(
            registry_path=registry, runtime_root=runtime, goal_id=goal_id, todo_id=todo_id,
            owner=agent_id, idempotency_key=current["idempotency_key"], expected_version=current["version"],
        )
        assert released["ok"] is True, released
        code, reclaimed = _cli(registry, runtime, *claim_args,
                               "--claim-operation-id", "native-resume-replacement")
        assert code == 0, reclaimed
        replacement = inspect_lease()
        assert replacement["status"] == "active" and replacement["owner"] == agent_id
        assert replacement["lease_epoch"] > original_lease["lease_epoch"]
        assert replacement["version"] > original_lease["version"]
        assert replacement["idempotency_key"] != original_lease["idempotency_key"]
        if offer_current:
            resolver_lease = replacement

    if scenario == "init_replacement":
        replace_execution()
    elif scenario == "resolver_value_error":
        # The real resolver still holds P1 and sees canonical active P2. Its
        # ValueError is not proof that the original completion retired P1.
        replace_execution(offer_current=False)
    elif scenario == "resolver_runtime_error":
        resolver_fault = RuntimeError("fixture resolver unavailable")
    elif scenario == "resolver_timeout_error":
        resolver_fault = TimeoutError("fixture resolver timeout")
    elif scenario == "renewed_retired_writeback":
        renewed = renew_task_lease(
            registry_path=registry, runtime_root=runtime, goal_id=goal_id, todo_id=todo_id,
            owner=agent_id, idempotency_key=original_lease["idempotency_key"],
            expected_version=original_lease["version"],
        )
        assert renewed["ok"] is True, renewed
        resolver_lease = inspect_lease()
        assert resolver_lease["idempotency_key"] == original_lease["idempotency_key"]
        assert resolver_lease["lease_epoch"] == original_lease["lease_epoch"]
        assert resolver_lease["version"] > original_lease["version"]

    actual_resolvers = turn_run_once.build_turn_settlement_resolvers

    def guarded_resolvers(**kwargs):
        resolvers = actual_resolvers(**kwargs)
        if scenario == "writeback_replacement":
            # Init already read P1. Ordinary writeback must fetch authority
            # again instead of using that successful qualification.
            assert qualifications and qualifications[-1]["source"] == "active"
            replace_execution()
        if scenario != "completion_replacement":
            return resolvers
        completion_readback, *remaining = resolvers

        def replace_after_completion_readback():
            observed = completion_readback()
            assert observed is None
            assert qualifications and qualifications[-1]["source"] == "active"
            replace_execution()
            return observed

        return (replace_after_completion_readback, *remaining)

    monkeypatch.setattr(turn_run_once, "build_turn_settlement_resolvers", guarded_resolvers)
    actual_completion, actual_refresh = turn_run_once.write_turn_validated_completion, turn_run_once.refresh_state_run
    lose_refresh = scenario == "renewed_retired_writeback"

    def record_completion(**kwargs):
        completion_calls.append(kwargs)
        return actual_completion(**kwargs)

    def record_refresh(**kwargs):
        nonlocal lose_refresh
        refresh_requests.append(kwargs)
        if lose_refresh:
            lose_refresh = False
            raise OSError("fixture refresh interruption after canonical completion")
        return actual_refresh(**kwargs)

    monkeypatch.setattr(turn_run_once, "write_turn_validated_completion", record_completion)
    monkeypatch.setattr(turn_run_once, "refresh_state_run", record_refresh)
    spend_requests = []
    actual_spend = turn_run_once.spend_quota_slot

    def record_spend(*args, **kwargs):
        spend_requests.append(kwargs)
        return actual_spend(*args, **kwargs)

    monkeypatch.setattr(turn_run_once, "spend_quota_slot", record_spend)
    resume = ("--resume-turn-key", journal["turn_key"], "--retry-failed-turn")
    code, resumed = invoke(*resume)
    if accounting_only:
        assert code == 0 and resumed["status"] == "committed", resumed
        assert resumed["effects"]["host_invoked"] is False and resumed["effects"]["state_written"] is False
        assert resolver_calls == resolver_calls_before_resume and resolver_errors == []
        assert qualification_requests == [] and completion_calls == [] and refresh_requests == []
        assert len(spend_requests) == 1 and original_readback.identity.value is not None
        assert spend_requests[0]["effect_ref"] == f"{original_readback.identity.value.effect_id}#quota_spend"
        assert _effect_kinds(runtime, journal) == ["todo_complete", "refresh_state", "quota_spend"]
        assert sum(json.loads(line).get("event_kind") == "quota_spend"
                   for line in event_path.read_bytes().splitlines()) == 1
        assert inspect_lease() == retired
    elif scenario == "renewed_retired_writeback":
        assert code == 1 and resumed["error"] == "fixture refresh interruption after canonical completion", resumed
        assert _effect_kinds(runtime, journal) == ["todo_complete"]
        assert not index_path.exists() or not index_path.read_text().strip()
        retired = inspect_lease()
        assert retired["status"] == "released" and retired["lease_epoch"] == original_lease["lease_epoch"]
        assert retired["idempotency_key"] == original_lease["idempotency_key"]
        assert len(completion_calls) == 1
        assert completion_calls[0]["task_lease_expected_version"] == resolver_lease["version"]
        assert completion_calls[0]["task_lease_idempotency_key"] == original_lease["idempotency_key"]
        assert any(row["source"] == "retired" for row in qualifications)
        code, resumed = invoke(*resume)
        assert code == 0 and resumed["status"] == "committed", resumed
        assert resumed["effects"]["host_invoked"] is False and resolver_errors
        assert len(completion_calls) == 1 and len(refresh_requests) == 2
        assert refresh_requests[-1]["task_lease_proof"] == {
            "idempotency_key": retired["idempotency_key"], "expected_version": retired["version"],
        }
        assert _effect_kinds(runtime, journal) == ["todo_complete", "refresh_state", "quota_spend"]
        assert sum(json.loads(line).get("event_kind") == "quota_spend"
                   for line in event_path.read_bytes().splitlines()) == 1
    else:
        assert code == 1, resumed
        if scenario in {"resolver_runtime_error", "resolver_timeout_error"}:
            assert resumed["error"] == str(resolver_fault)
            assert resumed.get("error_code") != "refresh_task_lease_unavailable"
            assert qualification_requests == []
        else:
            assert resumed["error_code"] == "refresh_task_lease_unavailable", resumed
            assert qualification_requests[-1] == (None if scenario == "resolver_value_error" else {
                "idempotency_key": replacement["idempotency_key"], "expected_version": replacement["version"],
            })
            assert inspect_lease() == replacement
        assert bool(resolver_errors) is (scenario == "resolver_value_error")
        assert completion_calls == [] and refresh_requests == [] and spend_requests == []
        assert _effect_kinds(runtime, journal) == []
        assert not any(json.loads(line).get("event_kind") == "quota_spend"
                       for line in event_path.read_bytes().splitlines())
        assert not index_path.exists() or not index_path.read_text().strip()
        code, current_todo = _cli(registry, runtime, "todo", "list", "--goal-id", goal_id, "--todo-id", todo_id)
        assert code == 0 and current_todo["todo"]["todo_id"] == todo_id and current_todo["todo"]["status"] == "open", current_todo
    assert host_calls == [journal["turn_key"]]
    assert _turn_journal(runtime)["host_attempt_count"] == 1
    assert _turn_journal(runtime)["host_result"] == frozen_result
    assert original_receipt_lines() == receipt_bytes
    assert all(row["state"] == "ready" and row["original_prepared"] == original_prepared
               for row in qualifications)
    final_readback = turn_run_once.read_heartbeat_settlement(
        runtime, goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        turn_instance_id=run_id, registry_path=registry,
    )
    assert final_readback is not None and final_readback.prepared_lease == original_prepared
    if accounting_only:
        assert final_readback.writeback_run == original_readback.writeback_run
        assert final_readback.spend_event is not None and final_readback.spend_run is not None


def test_builtin_codex_advertises_explicit_completion_without_promoting_progress() -> (
    None
):
    kinds = codex_cli_result_schema()["properties"]["result_kind"]["enum"]
    assert "validated_completion" in kinds and "validated_progress" in kinds


def _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, *, exact_ref=False, mirror=False):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider, handoff_mode="hard_lease")
    goal, agent, todo = "loopx-turn-fixture", "codex-fixture", "todo_fixture0001"
    registration = json.loads(registry.read_text())
    registration["registry_role"] = "project-local"
    goal_ref = None
    if exact_ref:
        goal_ref = {"goal_id": goal, "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        registration["goals"][0]["goal_instance_id"] = goal_ref["goal_instance_id"]
    registry.write_text(json.dumps(registration) + "\n")
    source_registry = registry
    if mirror:
        registry = registry.with_name("mirror-registry.json")
        registration["registry_role"] = "global-local"
        registration["goals"][0]["source_registry"] = str(source_registry.resolve())
        registry.write_text(json.dumps(registration) + "\n")
    claim_args = ("todo", "claim", "--goal-id", goal, "--todo-id", todo,
                  "--agent-id", agent, "--claimed-by", agent)
    code, claimed = _cli(registry, runtime, *claim_args)
    assert code == 0, claimed
    code, inspected = _cli(registry, runtime, "task-lease", "inspect", "--goal-id", goal, "--todo-id", todo)
    assert code == 0, inspected
    lease = inspected["lease"]
    log = runtime / "goals" / goal / "rollout-event-log.jsonl"
    host_receipts = []

    def host(request, launch_lifetime):
        with launch_lifetime():
            receipts = [json.loads(line) for line in log.read_bytes().splitlines()
                        if json.loads(line).get("event_kind") == "quota_should_run"]
            host_receipts.append(receipts[-1])
            raise OSError("fixture stopped after observing native admission")

    def invoke(turn, *, override_ref=None, resume_turn_key=None):
        args = build_parser().parse_args([
            "turn", "run-once", "--host", "generic-cli", "--goal-id", goal,
            "--agent-id", agent, "--project", str(project),
            *(["--resume-turn-key", resume_turn_key, "--retry-failed-turn"] if resume_turn_key
              else ["--todo-id", todo, "--turn-instance-id", turn]),
            "--no-global-sync", "--execute",
        ])
        args.host, args.session_scope = "codex-app-server", "agent"
        result = {}
        code = handle_turn_command(args, registry_path=registry, runtime_root_arg=str(runtime),
            output_format=lambda *_a, **_kw: "json", print_payload=lambda value, *_a: result.update(value),
            native_host_runner=host, native_session_binding_resolver=lambda _: {
                "schema_version": "loopx_turn_session_binding_v0", "goal_id": goal, "agent_id": agent,
            }, native_goal_ref=override_ref if override_ref is not None else goal_ref)
        return code, result

    return {"project": project, "runtime": runtime, "registry": registry, "goal": goal, "agent": agent,
            "todo": todo, "lease": lease, "log": log, "host_receipts": host_receipts,
            "claim_args": claim_args, "invoke": invoke, "goal_ref": goal_ref,
            "source_registry": source_registry}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_replan_metadata_read_borrows_real_scope_without_promoting_aliases(
    tmp_path, monkeypatch, provider,
):
    from loopx.control_plane.quota.accounting_admission import (
        _borrow_native_read_admission, quota_accounting_admission,
    )
    from loopx.control_plane.work_items import replan_history_codec as codec

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, exact_ref=True, mirror=True)
    read_context = codec.effective_turn_cadence_context({"id": "loopx-turn-fixture",
        "execution_profile": {"replan_after_effective_turns": 3}}, context["runtime"])
    assert read_context["goal_ref"] is None and read_context["registry_path"] is None
    rows = []
    # These are read-only history facts. They are never written as Runs or receipts.
    for label, ref, numbers in [
        ("foreign", {"goal_id": "loopx-turn-fixture",
                     "goal_instance_id": "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, [6, 5]),
        ("alias", None, [4, 3]),
        ("current", {"goal_id": "loopx-turn-fixture",
                     "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, [2, 1]),
    ]:
        for number in numbers:
            rows.append({"agent_id": "codex-fixture", "classification": "bounded_delivery",
                "generated_at": f"2026-09-22T00:00:{number:02d}Z", "turn_instance_id": f"history-{label}-{number}",
                "progress_observation": {"schema_version": "typed_progress_observation_v0",
                    "result_class": "unchanged", "surface_id": f"{label}-fixture-surface",
                    "evidence_ids": [f"evidence-{label}"]},
                **({"goal_ref": ref} if ref is not None else {})})
    actual_effect = codec.effect_runtime_result
    requests = []

    def observe_projection(method, params, **kwargs):
        result = actual_effect(method, params, **kwargs)
        if method == "work_item.replan_history.project":
            requests.append(json.loads(json.dumps(params)))
        return result

    monkeypatch.setattr(codec, "effect_runtime_result", observe_projection)
    receipt_bytes, registry_bytes = context["log"].read_bytes(), context["registry"].read_bytes()

    def project(cadence=read_context):
        return codec.project_replan_history(rows, operation="progress", agent_id="codex-fixture",
            effective_turn_cadence=cadence)

    unheld = project()
    assert unheld["kind"] == "typed_progress_repeat" and unheld["run_count"] == 2
    assert unheld["progress_baseline"]["surface_id"] == "alias-fixture-surface"
    assert "goal_ref" not in requests[-1]["settlement_source"]
    assert "source_admission" not in requests[-1]["settlement_source"]
    with quota_accounting_admission(runtime_root=context["runtime"], registry_path=context["registry"],
            goal_id=context["goal"], goal_ref=None, native_agent_id=context["agent"],
            operation="fixture-alias-metadata-read"):
        assert _borrow_native_read_admission(runtime_root=context["runtime"], registry_path=context["registry"],
            goal_id=context["goal"], goal_ref=None, agent_id=context["agent"]) is None
        # Alias reads keep their original explicit registry context and use
        # the existing accounting fallback rather than the new exact helper.
        assert project({**read_context, "registry_path": context["registry"]}) == unheld
        assert "goal_ref" not in requests[-1]["settlement_source"]
        assert requests[-1]["settlement_source"]["source_admission"]["planned_goal_ref"] is None
        assert requests[-1]["settlement_source"]["borrow_source_admission"] is False
    with quota_accounting_admission(runtime_root=context["runtime"], registry_path=context["registry"],
            goal_id=context["goal"], goal_ref=context["goal_ref"], native_agent_id=context["agent"],
            native_publication=True, operation="fixture-exact-metadata-read") as held:
        original_held = json.loads(json.dumps(held))
        current = project()
        assert current["kind"] == "typed_progress_repeat" and current["run_count"] == 2
        assert current["progress_baseline"]["surface_id"] == "current-fixture-surface"
        source = requests[-1]["settlement_source"]
        admission = source["source_admission"]
        assert source["borrow_source_admission"] is True
        assert source["goal_ref"] == admission["planned_goal_ref"] == {
            "goal_id": "loopx-turn-fixture", "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        assert admission["agent_id"] == "codex-fixture" and "profile_id" not in admission
        assert admission["runtime_root"] == str(context["runtime"].resolve())
        assert admission["registry_sources"] == [
            {"path": str(path.resolve()), "sha256": sha256(path.read_bytes()).hexdigest()}
            for path in [context["source_registry"], context["registry"]]]
        assert [lock["role"] for lock in admission["locks"]] == [
            "run_index", "registry_source", "registry_source"]
        assert held == original_held
        assert any(lock["role"] == "authority_maintenance" for lock in held["locks"])
    assert read_context["goal_ref"] is None and read_context["source_admission"] is None
    assert context["log"].read_bytes() == receipt_bytes
    assert context["registry"].read_bytes() == registry_bytes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_replan_read_scope_rejects_foreign_context_and_generic_writer(tmp_path, monkeypatch, provider):
    from loopx.control_plane.quota.accounting_admission import (
        _borrow_native_read_admission, quota_accounting_admission,
    )
    from loopx.control_plane.work_items import replan_history_codec as codec

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, exact_ref=True)
    read_context = codec.effective_turn_cadence_context({"id": "loopx-turn-fixture",
        "execution_profile": {"replan_after_effective_turns": 3}}, context["runtime"],
        registry_path=context["registry"], goal_ref={"goal_id": "loopx-turn-fixture",
            "goal_instance_id": "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"})
    before = context["log"].read_bytes()
    with quota_accounting_admission(runtime_root=context["runtime"], registry_path=context["registry"],
            goal_id=context["goal"], goal_ref=context["goal_ref"], native_agent_id=context["agent"],
            operation="fixture-exact-read-scope"):
        with pytest.raises(RuntimeError, match="does not match its held resource scope"):
            codec.project_replan_history(operation="progress", agent_id=context["agent"],
                effective_turn_cadence=read_context)
        common = {"runtime_root": context["runtime"], "registry_path": context["registry"],
            "goal_id": context["goal"], "goal_ref": context["goal_ref"], "agent_id": context["agent"]}
        for changed in [{"runtime_root": tmp_path / "foreign-runtime"},
                        {"registry_path": tmp_path / "foreign-registry.json"},
                        {"goal_id": "foreign-goal"}, {"agent_id": "foreign-agent"}]:
            with pytest.raises((RuntimeError, ValueError)):
                _borrow_native_read_admission(**{**common, **changed})
        # The new read seam must not change ordinary writer borrowing, even
        # when that writer does not acquire an additional legacy index lock.
        with pytest.raises(RuntimeError, match="does not match its held resource scope"):
            with quota_accounting_admission(runtime_root=context["runtime"], registry_path=context["registry"],
                    goal_id=context["goal"], goal_ref=None, operation="fixture-native-writer",
                    lock_legacy_index=False):
                pytest.fail("a generic writer cannot replace the held exact scope with an alias")
    assert context["log"].read_bytes() == before
    assert context["host_receipts"] == []


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["missing_scope", "altered_witness"])
def test_native_writeback_guard_rejects_resource_loss_after_real_qualification(
    tmp_path, monkeypatch, provider, fault,
):
    from loopx.control_plane.quota import accounting_admission, execution_preparation
    from loopx.file_lock import cross_runtime_lock_witness

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, exact_ref=True)
    turn = "native-writeback-resource-loss"
    code, failed = context["invoke"](turn)
    assert code == 1 and len(context["host_receipts"]) == 1, failed
    receipt_bytes, registry_bytes = context["log"].read_bytes(), context["registry"].read_bytes()
    original_lease = deepcopy(context["lease"])
    proof = {"idempotency_key": original_lease["idempotency_key"], "expected_version": original_lease["version"]}
    actual_qualifier = execution_preparation.read_turn_execution_lease_qualification
    ready_qualifications, writer_calls = [], []
    missing_context_token = None

    def lose_resources_after_qualification(**kwargs):
        nonlocal missing_context_token
        qualified = actual_qualifier(**kwargs)
        assert qualified.resume_lease_qualification["state"] == "ready"
        ready_qualifications.append(qualified)
        if fault == "missing_scope":
            missing_context_token = accounting_admission._held_native_admission.set(None)
        else:
            # Alter only the offered copy, after TS has read the genuine scope.
            kwargs["source_admission"]["registry_sources"][0]["sha256"] = "0" * 64
            kwargs["source_admission"]["locks"][0]["token"] = "post-qualification-foreign-index-token"
        return qualified

    with accounting_admission.quota_accounting_admission(
            runtime_root=context["runtime"], registry_path=context["registry"],
            goal_id=context["goal"], goal_ref=context["goal_ref"], native_agent_id=context["agent"],
            operation="fixture-post-qualification-resource-loss") as held:
        original_held = deepcopy(held)
        offered = deepcopy(held)
        settlement = turn_run_once.read_heartbeat_settlement(
            context["runtime"], goal_id=context["goal"], agent_id=context["agent"], todo_id=context["todo"],
            turn_instance_id=turn, registry_path=context["registry"], goal_ref=context["goal_ref"],
            source_admission=held, borrow_source_admission=True)
        assert settlement is not None and settlement.prepared_lease is not None
        assert settlement.identity.value is not None
        assert settlement.prepared_lease == {
            "prepared_lease_key_sha256": sha256(original_lease["idempotency_key"].encode()).hexdigest(),
            "prepared_lease_version": original_lease["version"],
            "prepared_lease_epoch": original_lease["lease_epoch"]}
        assert settlement.heartbeat_receipt["goal_ref"] == context["goal_ref"]
        monkeypatch.setattr(execution_preparation, "read_turn_execution_lease_qualification",
            lose_resources_after_qualification)
        try:
            with pytest.raises(execution_preparation.RefreshTaskLeaseUnavailable,
                    match="resources are no longer held") as rejected:
                with execution_preparation.guard_first_writeback_execution(
                        runtime_root=context["runtime"], registry_path=context["registry"],
                        goal_id=context["goal"], todo_id=context["todo"], agent_id=context["agent"],
                        requested_proof=proof, settlement=settlement, locked_goal=None,
                        goal_ref=context["goal_ref"], source_admission=offered):
                    writer_calls.append("entered")
        finally:
            if missing_context_token is not None:
                accounting_admission._held_native_admission.reset(missing_context_token)
        assert len(ready_qualifications) == 1 and writer_calls == []
        assert rejected.value.code == "refresh_task_lease_unavailable"
        assert "lost its enclosing resource scope" in str(rejected.value.__cause__)
        assert ready_qualifications[0].prepared_lease == settlement.prepared_lease
        assert held == original_held
        assert accounting_admission._held_native_admission.get()[1] == original_held
        for witness in held["locks"]:
            live = cross_runtime_lock_witness(Path(witness["target"]))
            assert {key: live[key] for key in ("target", "pid", "token")} == {
                key: witness[key] for key in ("target", "pid", "token")}
    assert context["log"].read_bytes() == receipt_bytes
    assert context["registry"].read_bytes() == registry_bytes
    code, inspected = _cli(context["registry"], context["runtime"], "task-lease", "inspect",
        "--goal-id", context["goal"], "--todo-id", context["todo"])
    assert code == 0 and inspected["lease"] == original_lease, inspected


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mirror", [False, True])
def test_native_next_action_uses_original_exact_resources(tmp_path, monkeypatch, provider, mirror):
    from loopx.state_refresh import refresh_state_run
    from loopx.control_plane.quota.accounting_admission import quota_accounting_admission
    from loopx.control_plane.quota.execution_preparation import read_turn_execution_lease_qualification
    from loopx.control_plane.work_items.refresh_recommendation import load_refresh_planning_source
    from loopx.control_plane.work_items.recommendation_source_io import (
        load_recommendation_source_goal, project_agent_next_actions,
        recommendation_runs, recommendation_source_context,
    )

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, exact_ref=True, mirror=mirror)
    turn = "native-next-action-original-turn"
    code, failed = context["invoke"](turn)
    assert code == 1 and len(context["host_receipts"]) == 1, failed
    original_journal = _turn_journal(context["runtime"])
    source_registry, goal = load_recommendation_source_goal(context["registry"], context["goal"])
    assert source_registry == context["source_registry"].resolve() and goal is not None
    state = Path(goal["repo"]) / goal["state_file"]
    state_bytes, registry_bytes = state.read_bytes(), context["registry"].read_bytes()
    lease = deepcopy(context["lease"])
    proof = {"idempotency_key": lease["idempotency_key"], "expected_version": lease["version"]}

    def routes():
        planning = load_refresh_planning_source(context["runtime"], context["goal"], state,
            require_display=True, registry_path=source_registry)
        source = recommendation_source_context(goal, planning.state_text,
            source_registry=source_registry, todo_fields=planning.todo_fields)
        return project_agent_next_actions(goal, planning.todo_fields, source=source,
            runs=recommendation_runs(context["runtime"], context["goal"]))

    action = next(row for row in routes() if row["agent_id"] == "codex-fixture")
    assert action["todo_id"] == "todo_fixture0001"
    basis = action["next_action_basis"]
    before_receipt = [line for line in context["log"].read_bytes().splitlines(keepends=True)
        if json.loads(line).get("event_kind") == "quota_should_run"]
    step = "Inspect the recorded Host interruption before retrying the original work."
    with quota_accounting_admission(runtime_root=context["runtime"], registry_path=context["registry"],
            goal_id=context["goal"], goal_ref=context["goal_ref"], native_agent_id=context["agent"],
            operation="fixture-native-next-action") as held:
        settlement = turn_run_once.read_heartbeat_settlement(context["runtime"], goal_id=context["goal"],
            agent_id=context["agent"], todo_id=context["todo"], turn_instance_id=turn,
            registry_path=context["registry"], goal_ref=context["goal_ref"],
            source_admission=held, borrow_source_admission=True)
        assert settlement is not None and settlement.identity.value is not None
        qualified = read_turn_execution_lease_qualification(runtime_root=context["runtime"],
            registry_path=context["registry"], identity=settlement.identity.value,
            requested_proof=proof, goal_ref=context["goal_ref"], source_admission=held)
        assert qualified.resume_lease_qualification["state"] == "ready"
        written = refresh_state_run(registry_path=context["registry"], runtime_root_override=str(context["runtime"]),
            goal_id=context["goal"], project=None, state_file=None, classification="fixture_host_interruption",
            recommended_action=None, agent_id=context["agent"], todo_id=context["todo"], turn_instance_id=turn,
            next_action=step, next_action_basis=basis, delivery_batch_scale="single_surface",
            delivery_outcome="outcome_gap", delivery_boundary="semantic_closeout",
            progress_observation={"schema_version": "typed_progress_observation_v0", "result_class": "blocked",
                "work_item_id": context["todo"], "blocker_id": "fixture-host-interruption",
                "evidence_ids": ["turn:" + original_journal["turn_key"].removeprefix("sha256:")]},
            vision_unchanged_reason="The original work remains open; only its interrupted Turn is closing.",
            task_lease_proof=proof, goal_ref=context["goal_ref"], dry_run=False, sync_global=False)
    assert written["ok"] is True and written["appended"] is True, written
    assert written["recommended_action"] == step
    assert written["recommended_action_resolution"]["recommended_action_source"] == "agent_lane_step"
    assert written["recommended_action_resolution"]["todo_id"] == context["todo"]
    record = json.loads(Path(written["json_path"]).read_text())
    assert record["goal_ref"] == context["goal_ref"] and record["agent_id"] == "codex-fixture"
    assert record["progress_observation"]["result_class"] == "blocked"
    assert record["blocked_retry"]["schema_version"] == "quota_blocked_retry_v0"
    current = next(row for row in routes() if row["agent_id"] == "codex-fixture")
    assert current["todo_id"] == "todo_fixture0001" and current["next_step"] == step
    assert current["next_action_basis"] != basis
    later = turn_run_once.read_heartbeat_settlement(context["runtime"], goal_id=context["goal"],
        agent_id=context["agent"], todo_id=context["todo"], turn_instance_id=turn,
        registry_path=context["registry"], goal_ref=context["goal_ref"])
    assert later is not None and later.prepared_lease == settlement.prepared_lease
    assert later.writeback_run is not None and later.writeback_run["goal_ref"] == context["goal_ref"]
    assert later.spend_event is None and later.completion_event is None
    assert [line for line in context["log"].read_bytes().splitlines(keepends=True)
        if json.loads(line).get("event_kind") == "quota_should_run"] == before_receipt
    assert state.read_bytes() == state_bytes and context["registry"].read_bytes() == registry_bytes
    code, inspected = _cli(context["registry"], context["runtime"], "task-lease", "inspect",
        "--goal-id", context["goal"], "--todo-id", context["todo"])
    assert code == 0 and inspected["lease"] == lease, inspected


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("exact_ref", [False, True])
def test_fresh_native_records_modern_prepared_admission_before_host(tmp_path, monkeypatch, provider, exact_ref):
    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, exact_ref=exact_ref)
    turn = "fresh-native-admission"
    code, result = context["invoke"](turn)
    assert code == 1 and result["ok"] is False, result  # Host deliberately stops after admission.
    assert len(context["host_receipts"]) == 1, (code, result)
    receipt = context["host_receipts"][0]
    details, lease = receipt["details"], context["lease"]
    assert (receipt["goal_id"], receipt["agent_id"], receipt["run_id"]) == (
        context["goal"], context["agent"], turn)
    assert details["todo_id"] == details["selected_todo_id"] == context["todo"]
    assert {field: details[field] for field in (
        "ok", "should_run", "must_attempt_work", "delivery_allowed", "quiet_noop_allowed", "closeout_required",
    )} == {"ok": True, "should_run": True, "must_attempt_work": True,
           "delivery_allowed": True, "quiet_noop_allowed": False, "closeout_required": True}
    assert details["prepared_lease_key_sha256"] == sha256(lease["idempotency_key"].encode()).hexdigest()
    assert details["prepared_lease_version"] == lease["version"]
    assert details["prepared_lease_epoch"] == lease["lease_epoch"]
    assert "causality" not in receipt
    assert receipt.get("goal_ref") == context["goal_ref"]
    if exact_ref:
        assert receipt["goal_ref"] == {
            "goal_id": "loopx-turn-fixture", "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    original = context["log"].read_bytes()
    renewed = renew_task_lease(registry_path=context["registry"], runtime_root=context["runtime"],
        goal_id=context["goal"], todo_id=context["todo"], owner=context["agent"],
        idempotency_key=lease["idempotency_key"], expected_version=lease["version"], ttl_seconds=600)
    assert renewed["ok"] is True, renewed
    assert context["log"].read_bytes() == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mirror", [False, True])
def test_fresh_native_exact_journal_admission_preserves_original_ref_on_resume(
    tmp_path, monkeypatch, provider, mirror,
):
    from loopx.control_plane.turn_driver import turn_journal_runtime

    context = _fresh_native_admission_fixture(
        tmp_path, monkeypatch, provider, exact_ref=True, mirror=mirror)
    actual_effect = turn_journal_runtime.effect_runtime_result
    journal_writes = []

    def observe_journal_write(method, params, **kwargs):
        result = actual_effect(method, params, **kwargs)
        if method == "turn_journal.write":
            journal_writes.append((json.loads(json.dumps(params)), json.loads(json.dumps(result))))
        return result

    monkeypatch.setattr(turn_journal_runtime, "effect_runtime_result", observe_journal_write)
    turn = "fresh-native-exact-journal-resume"
    code, result = context["invoke"](turn)
    assert code == 1 and result["ok"] is False, result
    assert len(context["host_receipts"]) == 1, (code, result)
    journal = _turn_journal(context["runtime"])
    assert journal["host_attempt_count"] == 1
    capture_path = context["runtime"] / "goals" / "loopx-turn-fixture" / "turns" / "decisions" / (
        journal["turn_key"].removeprefix("sha256:") + ".json")
    capture_bytes = capture_path.read_bytes()
    original_receipt = context["log"].read_bytes()
    fresh_write_count = len(journal_writes)
    assert fresh_write_count > 0

    code, result = context["invoke"](turn, resume_turn_key=journal["turn_key"], override_ref={
        "goal_id": "loopx-turn-fixture", "goal_instance_id": "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    })
    assert code == 1 and result["ok"] is False, result
    assert len(context["host_receipts"]) == 2, (code, result)
    resumed = _turn_journal(context["runtime"])
    assert resumed["host_attempt_count"] == 2
    assert resumed["turn_key"] == journal["turn_key"]
    assert resumed["plan"] == journal["plan"]
    assert capture_path.read_bytes() == capture_bytes
    assert len(journal_writes) > fresh_write_count
    assert context["log"].read_bytes() == original_receipt
    expected_ref = {"goal_id": "loopx-turn-fixture",
        "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    assert resumed["plan"]["goal_ref"] == expected_ref
    source_paths = ([context["source_registry"], context["registry"]] if mirror
                    else [context["registry"]])
    for request, committed in journal_writes:
        snapshot, admission = request["journal"], request["source_admission"]
        assert committed["ok"] is True
        assert snapshot["goal_id"] == "loopx-turn-fixture"
        assert snapshot["plan"]["goal_ref"] == admission["planned_goal_ref"] == expected_ref
        assert snapshot["plan"]["work_contract"]["agent_identity"]["agent_id"] == admission["agent_id"] == "codex-fixture"
        assert admission["schema_version"] == "loopx_quota_native_admission_v0"
        assert "profile_id" not in admission
        assert admission["runtime_root"] == str(context["runtime"].resolve())
        assert admission["registry_path"] == str(context["registry"].resolve())
        assert admission["registry_sources"] == [
            {"path": str(path.resolve()), "sha256": sha256(path.read_bytes()).hexdigest()}
            for path in source_paths]
        assert [lock["role"] for lock in admission["locks"]] == [
            "run_index", *(["registry_source"] * len(source_paths))]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("exact_ref", [False, True])
def test_fresh_native_mirror_qualifies_and_reads_back_real_prepared_receipt(
    tmp_path, monkeypatch, provider, exact_ref,
):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.quota import settlement_cli
    from loopx.control_plane.quota.settlement import SettlementIdentity
    from loopx import rollout_event_log

    context = _fresh_native_admission_fixture(
        tmp_path, monkeypatch, provider, exact_ref=exact_ref, mirror=True)
    actual_details = settlement_cli.quota_rollout_details
    actual_effect = effect_runtime.effect_runtime_result
    actual_append = rollout_event_log._append_rollout_event_line
    producer_facts, publication_reads, appended_lines = [], [], []

    def observe_producer(*args, **kwargs):
        facts = actual_details(*args, **kwargs)
        producer_facts.append(json.loads(json.dumps(facts)))
        return facts

    def observe_publication(method, params, **kwargs):
        result = actual_effect(method, params, **kwargs)
        if method == "quota.settlement.read" and "details" in params.get("native_work_admission", {}):
            publication_reads.append((json.loads(json.dumps(params)), json.loads(json.dumps(result))))
        return result

    def observe_append(path, event):
        result = actual_append(path, event)
        appended_lines.append(path.read_bytes().splitlines(keepends=True)[-1])
        return result

    monkeypatch.setattr(settlement_cli, "quota_rollout_details", observe_producer)
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", observe_publication)
    monkeypatch.setattr(rollout_event_log, "_append_rollout_event_line", observe_append)
    turn = "fresh-native-mirror-admission"
    code, result = context["invoke"](turn)

    assert code == 1 and result["ok"] is False, result  # Host deliberately stops after admission.
    assert len(context["host_receipts"]) == 1, (code, result)
    receipt = context["host_receipts"][0]
    assert len(producer_facts) == 1
    assert len(publication_reads) == 2
    assert [result["native_work_admission"]["append"] for _, result in publication_reads] == [True, False]
    source_paths = [context["source_registry"], context["registry"]]
    for request, result in publication_reads:
        admission = request["source_admission"]
        assert request["borrow_source_admission"] is True
        assert request.get("goal_ref") == admission["planned_goal_ref"] == context["goal_ref"]
        assert admission["registry_path"] == str(context["registry"].resolve())
        assert admission["agent_id"] == context["agent"]
        assert admission["runtime_root"] == str(context["runtime"].resolve())
        assert admission["registry_sources"] == [
            {"path": str(path.resolve()), "sha256": sha256(path.read_bytes()).hexdigest()}
            for path in source_paths
        ]
        assert [lock["role"] for lock in admission["locks"]] == [
            "run_index", "registry_source", "registry_source", "authority_maintenance", "heartbeat_receipt"]
        assert [lock["target"] for lock in admission["locks"][1:3]] == [
            str(path.resolve()) for path in source_paths]
        assert result["native_work_admission"]["state"] == "ready"
    qualified = publication_reads[0][1]["native_work_admission"]
    readback = publication_reads[1][1]
    assert receipt == readback["heartbeat_receipt"]
    # The physical rollout carrier represents these absent diagnostics as
    # null; the qualified logical facts retain the producer's empty strings.
    empty_diagnostics = ("applied_rrule", "replan_obligation_id", "semantic_replan_obligation_id",
        "slots", "successor_todo_ids", "target_key")
    for field in empty_diagnostics:
        assert qualified["details"][field] == ""
        assert receipt["details"][field] is None
    assert receipt["details"] == {**qualified["details"],
        "applied_rrule": None, "replan_obligation_id": None, "semantic_replan_obligation_id": None,
        "slots": None, "successor_todo_ids": None, "target_key": None}
    assert (receipt["goal_id"], receipt["agent_id"], receipt["run_id"], receipt.get("goal_ref")) == (
        context["goal"], context["agent"], turn, context["goal_ref"])
    expected_prepared = {
        "prepared_lease_key_sha256": sha256(context["lease"]["idempotency_key"].encode()).hexdigest(),
        "prepared_lease_version": context["lease"]["version"],
        "prepared_lease_epoch": context["lease"]["lease_epoch"],
    }
    expected_work = {**expected_prepared, "todo_id": context["todo"], "selected_todo_id": context["todo"],
        "ok": True, "should_run": True, "must_attempt_work": True, "delivery_allowed": True,
        "quiet_noop_allowed": False, "closeout_required": True, "prepared_lease_required": True,
        "admission_resume_when": None, "admission_resume_ready": None}
    for details in (producer_facts[0], qualified["details"], receipt["details"]):
        for field, expected in expected_work.items():
            assert details[field] == expected and type(details[field]) is type(expected)
    expected_identity = SettlementIdentity(goal_id=context["goal"], agent_id=context["agent"],
        todo_id=context["todo"], turn_instance_id=turn)
    for details in (qualified["details"], receipt["details"]):
        assert details["turn_instance_id"] == turn
        assert details["settlement_effect_id"] == expected_identity.effect_id
    for disposition in (qualified, readback["native_work_admission"]):
        assert disposition["original_prepared"] == expected_prepared
        assert disposition["current_task_lease_proof"] == {
            "idempotency_key": context["lease"]["idempotency_key"], "expected_version": context["lease"]["version"]}
    assert len(appended_lines) == 1 and json.loads(appended_lines[0]) == receipt
    physical_bytes = context["log"].read_bytes()
    assert physical_bytes.endswith(appended_lines[0])
    later = turn_run_once.read_heartbeat_settlement(context["runtime"], goal_id=context["goal"],
        agent_id=context["agent"], todo_id=context["todo"], turn_instance_id=turn,
        registry_path=context["registry"], goal_ref=context["goal_ref"])
    assert later is not None and later.heartbeat_receipt == receipt
    assert later.prepared_lease == expected_prepared
    assert context["log"].read_bytes() == physical_bytes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_native_mirror_source_chain_rejects_before_host_or_publication(tmp_path, monkeypatch, provider):
    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, mirror=True)
    source = context["source_registry"]
    registration = json.loads(source.read_text())
    third_registry = source.with_name("third-registry.json")
    third_registry.write_text(json.dumps(registration) + "\n")
    registration["goals"][0]["source_registry"] = str(third_registry.resolve())
    source.write_text(json.dumps(registration) + "\n")
    before = context["log"].read_bytes()

    code, refused = context["invoke"]("fresh-native-mirror-source-chain")

    assert code == 1 and refused["ok"] is False, refused
    assert context["host_receipts"] == []
    assert context["log"].read_bytes() == before
    assert refused["error_code"] == "goal_authority_unavailable"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("foreign_resource", ["registry_source", "authority_maintenance"])
def test_fresh_native_mirror_foreign_witness_rejects_before_receipt_append(
    tmp_path, monkeypatch, provider, foreign_resource,
):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.projects.registry_codec import project_registry_transaction
    from loopx.file_lock import cross_runtime_lock_witness, exclusive_mutation_file_lock

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, mirror=True)
    foreign_registry = tmp_path / "foreign-registry.json"
    foreign_registry.write_bytes(context["source_registry"].read_bytes())
    foreign_maintenance = tmp_path / "foreign-maintenance"
    actual_effect = effect_runtime.effect_runtime_result
    attempted_publications = []
    before = context["log"].read_bytes()

    def offer_foreign_witness(method, params, **kwargs):
        if method != "quota.settlement.read" or "details" not in params.get("native_work_admission", {}):
            return actual_effect(method, params, **kwargs)
        request = json.loads(json.dumps(params))
        admission = request["source_admission"]
        # These witnesses come from a current real lock and real registry bytes.
        # They identify another resource and cannot replace the owning route/M.
        with contextlib.ExitStack() as locks:
            if foreign_resource == "registry_source":
                transaction = locks.enter_context(project_registry_transaction(
                    foreign_registry, operation="fixture_foreign_native_registry"))
                admission["registry_sources"][0] = transaction.source_read_witness()
                admission["locks"][1] = {"role": "registry_source",
                    **cross_runtime_lock_witness(foreign_registry)}
            else:
                locks.enter_context(exclusive_mutation_file_lock(
                    foreign_maintenance, timeout_seconds=0, operation="fixture_foreign_native_maintenance"))
                admission["locks"][3] = {"role": "authority_maintenance",
                    **cross_runtime_lock_witness(foreign_maintenance)}
            attempted_publications.append(request)
            return actual_effect(method, request, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", offer_foreign_witness)
    code, refused = context["invoke"]("fresh-native-mirror-foreign-witness")

    assert len(attempted_publications) == 1
    assert code == 1 and refused["ok"] is False, refused
    assert context["host_receipts"] == []
    assert context["log"].read_bytes() == before
    original = refused["original_ts_refusal"]
    assert original["code"] == refused["error_code"] == (
        "goal_instance_conflict" if foreign_resource == "registry_source" else "quota_source_admission_invalid")
    assert original["kind"] == (
        "conflict" if foreign_resource == "registry_source" else "request_rejected")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_native_rejects_replacement_before_admission_append(tmp_path, monkeypatch, provider):
    from loopx.control_plane.quota import settlement_cli

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider)
    actual_details = settlement_cli.quota_rollout_details
    replaced = False

    def replace_after_facts(*args, **kwargs):
        nonlocal replaced
        facts = actual_details(*args, **kwargs)
        if not replaced:
            replaced = True
            lease = context["lease"]
            released = release_task_lease(registry_path=context["registry"], runtime_root=context["runtime"],
                goal_id=context["goal"], todo_id=context["todo"], owner=context["agent"],
                idempotency_key=lease["idempotency_key"], expected_version=lease["version"])
            assert released["ok"] is True, released
            code, claimed = _cli(context["registry"], context["runtime"], *context["claim_args"],
                "--claim-operation-id", "replacement-before-native-admission")
            assert code == 0, claimed
        return facts

    monkeypatch.setattr(settlement_cli, "quota_rollout_details", replace_after_facts)
    code, refused = context["invoke"]("fresh-native-replacement")
    assert replaced and code == 1, refused
    assert context["host_receipts"] == []
    assert not any(json.loads(line).get("event_kind") == "quota_should_run"
                   for line in context["log"].read_bytes().splitlines())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("failure", ["append", "readback"])
def test_fresh_native_publication_failure_never_invokes_host(tmp_path, monkeypatch, provider, failure):
    from loopx import rollout_event_log
    from loopx.control_plane import effect_runtime

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider)
    admission_reads = 0
    actual_effect = effect_runtime.effect_runtime_result

    def fail_readback(method, params, **kwargs):
        nonlocal admission_reads
        if method == "quota.settlement.read" and "details" in params.get("native_work_admission", {}):
            admission_reads += 1
            if admission_reads == 2:
                raise RuntimeError("fixture native admission readback unavailable")
        return actual_effect(method, params, **kwargs)

    def fail_append(*_args, **_kwargs):
        raise OSError("fixture native admission append unavailable")

    if failure == "append":
        monkeypatch.setattr(rollout_event_log, "_append_rollout_event_line", fail_append)
    else:
        monkeypatch.setattr(effect_runtime, "effect_runtime_result", fail_readback)
    code, refused = context["invoke"]("fresh-native-publication-failure")
    assert code == 1 and context["host_receipts"] == [], refused
    assert refused["ok"] is False
    receipts = [json.loads(line) for line in context["log"].read_bytes().splitlines()
                if json.loads(line).get("event_kind") == "quota_should_run"]
    assert len(receipts) == (0 if failure == "append" else 1)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_native_cannot_open_another_turn_over_original_todo_debt(tmp_path, monkeypatch, provider):
    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider)
    context["invoke"]("fresh-native-original-debt")
    assert len(context["host_receipts"]) == 1
    original = context["log"].read_bytes()
    code, refused = context["invoke"]("fresh-native-second-turn")
    assert code == 1, refused
    assert len(context["host_receipts"]) == 1
    assert context["log"].read_bytes() == original
    code, inspected = _cli(context["registry"], context["runtime"], "task-lease", "inspect",
        "--goal-id", context["goal"], "--todo-id", context["todo"])
    assert code == 0 and inspected["lease"] == context["lease"], inspected


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("metadata_present", [False, True])
def test_fresh_native_exact_ref_requires_registered_incarnation(tmp_path, monkeypatch, provider, metadata_present):
    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, exact_ref=metadata_present)
    before = context["log"].read_bytes()
    code, refused = context["invoke"]("fresh-native-wrong-incarnation", override_ref={
        "goal_id": context["goal"], "goal_instance_id": "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    })
    assert code == 1 and context["host_receipts"] == [], refused
    assert context["log"].read_bytes() == before
    assert refused["original_ts_refusal"]["code"] == refused["error_code"] == "goal_instance_conflict"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_native_malformed_exact_ref_rejects_before_admission(tmp_path, monkeypatch, provider):
    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider, exact_ref=True)
    before = context["log"].read_bytes()
    code, refused = context["invoke"]("fresh-native-malformed-incarnation", override_ref={
        "goal_id": context["goal"], "goal_instance_id": "native-admission-fixture-instance",
    })

    assert code == 1 and refused["ok"] is False, refused
    assert refused["error"] == "goal_instance_id must be a Goal instance identifier"
    assert context["host_receipts"] == []
    assert context["log"].read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_native_prior_debt_refusal_retains_typed_packet_before_preparer(tmp_path, monkeypatch, provider):
    from loopx.cli_commands.quota_should_run import check_fresh_native_heartbeat_prior_closeout
    from loopx.control_plane.quota.execution_preparation import RefreshTaskLeaseUnavailable
    from loopx.control_plane.quota.settlement import SettlementIdentity

    context = _fresh_native_admission_fixture(tmp_path, monkeypatch, provider)
    context["invoke"]("fresh-native-original-debt")
    assert len(context["host_receipts"]) == 1
    before = context["log"].read_bytes()
    with pytest.raises(RefreshTaskLeaseUnavailable) as refused:
        check_fresh_native_heartbeat_prior_closeout(
            identity=SettlementIdentity(goal_id=context["goal"], agent_id=context["agent"],
                todo_id=context["todo"], turn_instance_id="fresh-native-second-turn"),
            runtime_root=context["runtime"], registry_path=context["registry"], goal_ref=None)
    original = refused.value.payload["original_ts_refusal"]
    assert original["kind"] == "request_rejected"
    assert original["code"] == "refresh_task_lease_unavailable"
    assert original["message"] == str(refused.value.__cause__)
    assert context["log"].read_bytes() == before
