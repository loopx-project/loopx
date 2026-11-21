"""Builtin completion uses the real Todo validator and canonical lease fence."""

from __future__ import annotations

import contextlib
import io
import json
import sys
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
