"""End-to-end File + SQLite verification for Stage-then-Swap EpochReset, Quiet-Turn, and telemetry."""
from __future__ import annotations

from contextlib import closing, redirect_stdout
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from loopx.capabilities.benchmark_toolkit.experiment_board import (
    BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
    normalize_benchmark_experiment_board_row,
)
from loopx.capabilities.benchmark_toolkit.study_projection import _effort_aggregate
from loopx.capabilities.benchmark_toolkit.token_capacity import (
    BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION,
    build_benchmark_token_capacity_policy,
    build_benchmark_token_capacity_status,
    configure_benchmark_token_capacity,
    observe_benchmark_token_usage,
    read_benchmark_token_capacity_state,
)
from loopx.cli import main as cli_main
from loopx.control_plane.turn_driver import codex_context
from loopx.control_plane.turn_driver.codex_cli import prepare_codex_cli_input
from loopx.control_plane.turn_driver.decision_capture import write_decision_capture
from loopx.control_plane.turn_driver.epoch_prefetch import resolve_or_execute_epoch_prefetch
from loopx.control_plane.turn_driver.journal_store import turn_journal_path
from benchmark.runtime.session_observation import collect_context_policy_telemetry
from loopx.todos import add_goal_todo, complete_goal_todo
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_driver import _write_live_fixture


def _invoke_cli(route: list[str], args: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main([*route, "--format", "json", *args])
    return code, json.loads(buf.getvalue())


def _write_generic_host_and_validator(tmp_path: Path, seen_log: Path) -> tuple[Path, Path]:
    runner = tmp_path / "generic_host_runner.py"
    runner.write_text(
        "import json, pathlib, sys\n"
        "request = json.load(sys.stdin)\n"
        f"log_path = pathlib.Path({str(seen_log)!r})\n"
        "existing = json.loads(log_path.read_text()) if log_path.exists() else []\n"
        "existing.append({'turn_key': request['turn_key'], 'epoch_prefetch': request.get('epoch_prefetch'), 'exploratory_context': request.get('exploratory_context')})\n"
        "log_path.write_text(json.dumps(existing))\n"
        "result = {\n"
        "  'schema_version': 'loopx_turn_result_v0',\n"
        "  'turn_key': request['turn_key'],\n"
        "  'result_kind': 'validated_progress',\n"
        "  'completed_phases': ['host_execute', 'typed_result'],\n"
        "  'classification': 'fixture_stage_swap',\n"
        "  'recommended_action': 'continue verified step',\n"
        "  'next_action': 'continue verified step',\n"
        "  'delivery_batch_scale': 'single_surface',\n"
        "  'delivery_outcome': 'outcome_progress',\n"
        "  'vision_unchanged_reason': 'Fixture objective remains active.',\n"
        "  'summary': 'Fixture host completed bounded work.',\n"
        "  'work_evidence': ['artifact:fixture-prefetch'],\n"
        "  'validation_evidence': ['check:fixture-validator'],\n"
        "}\n"
        "json.dump(result, sys.stdout)\n",
        encoding="utf-8",
    )
    validator = tmp_path / "validator.py"
    validator.write_text("import sys; sys.stdin.read()\n", encoding="utf-8")
    return runner, validator


def _switch_fixture_to_idle_monitor(registry: Path, runtime: Path) -> None:
    """Close the open executable fixture Todo and leave one non-due monitor_task."""
    add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Observe public fixture quietly.",
        task_class="continuous_monitor",
        claimed_by="codex-fixture",
        monitor_metadata={
            "target_key": "quiet-public-target",
            "cadence": "1h",
            "next_due_at": "2099-01-01T00:00:00+00:00",
            "watch_only": "true",
        },
    )
    complete_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        claimed_by="codex-fixture",
        evidence="Fixture executable step settled before quiet monitor wait.",
        no_followup=True,
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_default_off_turn_and_hook_have_zero_behavior_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    seen_log = tmp_path / "host_seen.json"
    runner, validator = _write_generic_host_and_validator(tmp_path, seen_log)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]

    code, payload = _invoke_cli(
        route,
        [
            "turn",
            "run-once",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--host",
            "generic-cli",
            "--project",
            str(project),
            "--host-adapter-request-schema",
            "loopx_turn_host_request_v1",
            "--host-adapter-command-json",
            json.dumps([sys.executable, str(runner)]),
            "--validation-command-json",
            json.dumps([sys.executable, str(validator)]),
            "--no-global-sync",
            "--execute",
        ],
    )
    assert code == 0, payload
    assert payload["status"] == "committed"
    assert "epoch_prefetch" not in payload
    assert "quiet_turn" not in payload
    assert not (runtime / "goals" / "loopx-turn-fixture" / "turn_prefetch_state.sqlite").exists()
    seen = json.loads(seen_log.read_text())
    assert len(seen) == 1 and seen[0]["epoch_prefetch"] is None

    # Codex context hook default-off check.
    ctx_dir = tmp_path / "codex-ctx"
    ctx_dir.mkdir(mode=0o700)
    capture_file = tmp_path / "capture.json"
    capture_sha = write_decision_capture(capture_file, {"summary": "keep default behavior"})
    codex_context._save(
        ctx_dir,
        {
            "session_id": "s-default",
            "cwd": str(tmp_path),
            "context_epoch": "epoch-1",
            "recovery": {"path": str(capture_file), "sha256": capture_sha, "command": f"read {capture_file}"},
        },
    )
    hook_res = codex_context.codex_context_hook(
        str(ctx_dir),
        {"session_id": "s-default", "cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "compact"},
    )
    assert "epochPrefetch" not in hook_res.get("hookSpecificOutput", {})
    assert not (ctx_dir / "epoch_prefetch.sqlite").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_turn_epoch_reset_prefetch_file_sqlite_once_only_replay_and_savings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    seen_log = tmp_path / "host_seen.json"
    runner, validator = _write_generic_host_and_validator(tmp_path, seen_log)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]

    # Create a large capture (>10KB noisy raw turns + compact selected summary) clearing token & cold-prefill gate.
    raw_history = [{"turn": idx, "log": f"verbose exploratory tool output row {idx} " * 4} for idx in range(60)]
    capture_payload = {
        "ok": True,
        "summary": {"next_probe": "verify solver parity", "blocker_count": 0},
        "exploratory_history": raw_history,
    }
    capture_file = tmp_path / "capture.json"
    capture_sha = write_decision_capture(capture_file, capture_payload)
    baseline_bytes = capture_file.stat().st_size
    assert baseline_bytes > 3000

    # Safe D8 context-read command selecting ONLY `/summary` via a single-shot call.
    prefetch_argv = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "quota",
        "context-read",
        "--capture-file",
        str(capture_file),
        "--capture-sha256",
        capture_sha,
        "--select",
        "/summary",
    ]
    call_counter = {"count": 0}
    real_exec = resolve_or_execute_epoch_prefetch
    from loopx.control_plane.turn_driver import epoch_prefetch as ep_mod

    real_subproc = ep_mod.run_bounded_prefetch_subprocess

    def counting_subproc(*args, **kwargs):
        call_counter["count"] += 1
        return real_subproc(*args, **kwargs)

    monkeypatch.setattr(ep_mod, "run_bounded_prefetch_subprocess", counting_subproc)

    epoch_cfg = {
        "enabled": True,
        "initiator": "turn_driver",
        "context_epoch": "epoch-swap-1",
        "prefetch_command": prefetch_argv,
        "timeout_ms": 10000,
        "max_output_bytes": 4096,
        "reclaimed_turns": 25,
        "dropped_failures": 1,
        "failure_facts": ["earlier solver iteration failed exit=1 before fix"],
        "open_obligations": ["preserve regression test coverage"],
        "source_ref": {"path": str(capture_file), "sha256": capture_sha},
        "baseline_bytes": baseline_bytes,
    }

    turn_args = [
        "turn",
        "run-once",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--turn-instance-id",
        "stage-swap-once",
        "--host",
        "generic-cli",
        "--project",
        str(project),
        "--host-adapter-request-schema",
        "loopx_turn_host_request_v1",
        "--host-adapter-command-json",
        json.dumps([sys.executable, str(runner)]),
        "--validation-command-json",
        json.dumps([sys.executable, str(validator)]),
        "--epoch-prefetch-json",
        json.dumps(epoch_cfg),
        "--no-global-sync",
        "--execute",
    ]
    code1, first = _invoke_cli(route, turn_args)
    assert code1 == 0, first
    assert first["status"] == "committed"
    assert first["replayed"] is False
    assert call_counter["count"] == 1

    ep = first["epoch_prefetch"]
    assert ep["operator"] == "EpochReset"
    assert ep["trust_level"] == "tool"
    assert ep["exit_code"] == 0
    assert ep["truncated"] is False
    assert ep["round_trips_saved"] == 1
    assert ep["bytes_saved"] > 0
    assert ep["hydrated_bytes"] < baseline_bytes
    assert "[Stage-then-Swap EpochReset Receipt | trust=tool]" in ep["hydrated_text"]
    assert "reclaimed_turns: 25" in ep["hydrated_text"]
    assert "dropped_failures: 1" in ep["hydrated_text"]
    assert "preserve regression test coverage" in ep["hydrated_text"]
    assert "verify solver parity" in ep["hydrated_text"]
    assert first["epoch_prefetch"]["replayed"] is False

    # Verify BOTH File (turn_journal + turn_prefetch file) AND SQLite hold the persisted completion.
    turn_key = first["resume_turn_key"]
    jpath = turn_journal_path(runtime, goal_id="loopx-turn-fixture", turn_key=turn_key)
    journal_data = json.loads(jpath.read_text(encoding="utf-8"))
    assert journal_data["epoch_prefetch"]["receipt_sha256"] == ep["receipt_sha256"]
    file_store_dir = runtime / "goals" / "loopx-turn-fixture" / "turn_prefetch"
    file_records = list(file_store_dir.glob("*.json"))
    assert len(file_records) == 1
    sqlite_db = runtime / "goals" / "loopx-turn-fixture" / "turn_prefetch_state.sqlite"
    assert sqlite_db.exists()
    with closing(sqlite3.connect(sqlite_db)) as conn:
        rows = conn.execute("SELECT operator, receipt_sha256 FROM epoch_prefetch_records").fetchall()
    assert rows == [("EpochReset", ep["receipt_sha256"])]

    # Replay the exact Turn via --resume-turn-key (without --turn-instance-id): prefetch_command MUST NEVER execute again.
    replay_args = [
        arg
        for idx, arg in enumerate(turn_args)
        if arg not in {"--turn-instance-id", "stage-swap-once"}
        and not (idx > 0 and turn_args[idx - 1] == "--turn-instance-id")
    ] + ["--resume-turn-key", turn_key]
    code2, replay = _invoke_cli(route, replay_args)
    assert code2 == 0, replay
    assert replay["replayed"] is True
    assert call_counter["count"] == 1
    assert replay["epoch_prefetch"]["replayed"] is True
    assert replay["epoch_prefetch"]["executed"] is False
    assert replay["epoch_prefetch"]["hydrated_text"] == ep["hydrated_text"]

    # Crash-recovery parity: remove File sidecar, verify SQLite supplies replay without re-executing!
    file_records[0].unlink()
    replayed_from_sqlite = real_exec(
        epoch_prefetch=epoch_cfg,
        scope_key=f"turn_driver:loopx-turn-fixture:{turn_key}",
        context_epoch="epoch-swap-1",
        file_store_dir=file_store_dir,
        sqlite_path=sqlite_db,
        reclaimed_turns=25,
        dropped_failures=1,
        failure_facts=epoch_cfg["failure_facts"],
        open_obligations=epoch_cfg["open_obligations"],
        source_ref=epoch_cfg["source_ref"],
        baseline_bytes=baseline_bytes,
    )
    assert replayed_from_sqlite is not None
    assert replayed_from_sqlite["replayed"] is True and replayed_from_sqlite["executed"] is False
    assert call_counter["count"] == 1
    assert len(list(file_store_dir.glob("*.json"))) == 1  # Healed back to File.

    # Telemetry aggregation on the recorded journal directory avoids double counting.
    telemetry = collect_context_policy_telemetry(
        runtime,
        goal_id="loopx-turn-fixture",
        usage={
            "completed_turns": 2,
            "request_coverage": {
                "status": "verified",
                "requests": [
                    {"input_tokens": 2000, "cached_input_tokens": 0, "cache_regime": "cold"},
                    {"input_tokens": 800, "cached_input_tokens": 600, "cache_regime": "warm"},
                ],
            },
        },
    )
    assert telemetry["operator_counts"] == {"KeepRaw": 0, "FoldToReceipt": 0, "EpochReset": 1}
    assert telemetry["prefetch_bytes"] == ep["retained_bytes"]
    assert telemetry["prefetch_bytes_saved"] == ep["bytes_saved"]
    assert telemetry["round_trips_saved"] == 1
    assert telemetry["prefix_kv"]["cold"] == {"requests": 1, "uncached_prefix_kv_tokens": 2000, "cached_prefix_kv_tokens": 0}
    assert telemetry["prefix_kv"]["warm"] == {"requests": 1, "uncached_prefix_kv_tokens": 200, "cached_prefix_kv_tokens": 600}


@pytest.mark.parametrize("failure_mode", ["command_error", "timeout", "policy_violation", "passive_plugin", "reject_truncation"])
def test_codex_context_hook_epoch_prefetch_fails_closed_to_keep_raw_and_replays_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_mode: str,
) -> None:
    ctx_dir = tmp_path / "codex-ctx"
    ctx_dir.mkdir(mode=0o700)
    capture_file = tmp_path / "capture.json"
    capture_sha = write_decision_capture(capture_file, {"big_payload": "data " * 500})
    codex_context._save(
        ctx_dir,
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "context_epoch": "epoch-hook-1",
            "goal_id": "goal-a",
            "agent_id": "agent-a",
            "candidate": {"capture_bytes_verified": True},
            "recovery": {"path": str(capture_file), "sha256": capture_sha, "command": f"read {capture_file}"},
        },
    )
    codex_context.codex_context_hook(str(ctx_dir), {"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "PreCompact"})

    fifo_path = tmp_path / "hang.fifo"
    large_file = tmp_path / "large.txt"
    large_file.write_text("Z" * 1024, encoding="utf-8")

    cfg: dict = {
        "enabled": True,
        "initiator": "turn_driver",
        "timeout_ms": 2000,
        "max_output_bytes": 256,
        "failure_facts": ["validator exited 2 on module X"],
        "open_obligations": ["keep failure facts visible"],
    }
    if failure_mode == "command_error":
        cfg["prefetch_command"] = ["cat", str(tmp_path / "does-not-exist.txt")]
    elif failure_mode == "timeout":
        os.mkfifo(fifo_path)
        cfg["prefetch_command"] = ["cat", str(fifo_path)]
        cfg["timeout_ms"] = 60
    elif failure_mode == "policy_violation":
        cfg["prefetch_command"] = ["git", "push", "origin", "main"]
    elif failure_mode == "passive_plugin":
        cfg["initiator"] = "history_view_policy"
        cfg["prefetch_command"] = ["cat", str(large_file)]
    elif failure_mode == "reject_truncation":
        cfg["prefetch_command"] = ["cat", str(large_file)]
        cfg["reject_on_truncation"] = True
        cfg["baseline_bytes"] = 5000

    res1 = codex_context.codex_context_hook(
        str(ctx_dir),
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "hook_event_name": "SessionStart",
            "source": "compact",
            "epoch_prefetch": cfg,
        },
    )
    hook_out = res1["hookSpecificOutput"]
    ep = hook_out["epochPrefetch"]
    assert ep["operator"] == "KeepRaw"
    assert ep["status"] == "fallback_keep_raw"
    assert ep["round_trips_saved"] == 0
    assert ep["bytes_saved"] == 0
    assert "validator exited 2 on module X" in hook_out["additionalContext"]
    assert "keep failure facts visible" in hook_out["additionalContext"]
    assert f"read {capture_file}" in hook_out["additionalContext"]

    # Replay SessionStart(compact) MUST NOT re-execute prefetch_command.
    from loopx.control_plane.turn_driver import epoch_prefetch as ep_mod

    def fail_if_executed(*args, **kwargs):
        raise AssertionError("prefetch_command must never re-execute on replay")

    monkeypatch.setattr(ep_mod, "run_bounded_prefetch_subprocess", fail_if_executed)
    res2 = codex_context.codex_context_hook(
        str(ctx_dir),
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "hook_event_name": "SessionStart",
            "source": "compact",
            "epoch_prefetch": cfg,
        },
    )
    assert res2["hookSpecificOutput"]["epochPrefetch"]["replayed"] is True
    assert res2["hookSpecificOutput"]["epochPrefetch"]["executed"] is False
    if fifo_path.exists():
        fifo_path.unlink()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quiet_turn_zero_tax_skip_and_codex_cli_prompt_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    seen_log = tmp_path / "host_seen.json"
    runner, validator = _write_generic_host_and_validator(tmp_path, seen_log)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]

    # First Turn records current state digest; second with unchanged state skips with zero tax.
    digest = hashlib.sha256(b"steady-frontier-state").hexdigest()
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    def _plan(instance_id: str) -> dict:
        c, p = _invoke_cli(
            route,
            [
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--turn-instance-id",
                instance_id,
                "--host",
                "generic-cli",
                "--execution-mode",
                "isolated-headless",
                "--scheduler-owner",
                "outer_controller",
                "--include-transaction-detail",
            ],
        )
        assert c == 0, p
        return p

    plan = _plan("qt-1")

    host_calls = {"n": 0}

    def fake_host(req: dict):
        host_calls["n"] += 1
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": req["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "quiet_probe",
            "recommended_action": "wait",
            "next_action": "wait",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Unchanged state.",
            "summary": "Initial observation recorded.",
            "work_evidence": ["artifact:initial"],
            "validation_evidence": ["check:initial"],
        }

    def ok_noop(*args, **kwargs):
        return {"ok": True, "appended": True, "classification": "quiet_probe", "recommended_action": "wait"}

    # First Turn executes normally and persists state_digest.
    r1 = run_loopx_turn_once(
        plan,
        host_runner=fake_host,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn={
            "enabled": True,
            "quiet_noop_allowed": True,
            "current_state_digest": digest,
            "skip_host_when_unchanged": True,
        },
    )
    assert r1["status"] == "scheduler_action_required" and host_calls["n"] == 1
    assert r1["quiet_turn"]["skipped"] is False

    # Open executable Todo MUST NOT be skipped even if caller passes quiet_noop_allowed=True.
    plan_open_mandatory = _plan("qt-open-mandatory")
    r_open = run_loopx_turn_once(
        plan_open_mandatory,
        host_runner=fake_host,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn={
            "enabled": True,
            "quiet_noop_allowed": True,
            "current_state_digest": digest,
            "skip_host_when_unchanged": True,
        },
    )
    assert r_open["status"] == "scheduler_action_required" and host_calls["n"] == 2
    assert r_open["quiet_turn"]["skipped"] is False
    assert r_open["quiet_turn"]["quiet_noop_allowed"] is False

    # Once open work is settled and only a non-due monitor remains, unchanged state skips with zero tax.
    _switch_fixture_to_idle_monitor(registry, runtime)
    plan_second = _plan("qt-2")
    assert plan_second["route"]["kind"] == "wait"
    r2 = run_loopx_turn_once(
        plan_second,
        host_runner=fake_host,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn={
            "enabled": True,
            "quiet_noop_allowed": True,
            "current_state_digest": digest,
            "skip_host_when_unchanged": True,
        },
        epoch_prefetch={
            "enabled": True,
            "prefetch_command": ["rm", "-rf", "/should-never-run"],
        },
    )
    assert r2["status"] == "skipped"
    assert host_calls["n"] == 2  # Host never invoked for quiet-noop wait Turn!
    assert r2["quota_slot_spend_count"] == 0
    assert r2["quiet_turn"]["skipped"] is True
    assert r2["quiet_turn"]["zero_tax"] is True
    assert r2["quiet_turn"]["prefetch_skipped"] is True
    assert r2["quiet_turn"]["context_delivery_skipped"] is True

    # Also verify codex_cli prompt builder preserves complete contract with attached tool-trust EpochReset observation.
    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request

    cli_req = build_loopx_turn_host_request(plan)
    cli_req["epoch_prefetch"] = {
        "schema_version": "loopx_epoch_prefetch_receipt_v0",
        "operator": "EpochReset",
        "trust_level": "tool",
        "hydrated_text": "[Stage-then-Swap EpochReset Receipt | trust=tool]\n---\nprefetched probe",
    }
    prompt, schema = prepare_codex_cli_input(cli_req)
    assert "tool trust level only" in prompt
    assert "prefetched probe" in prompt
    assert schema["additionalProperties"] is False

    keep_raw_req = build_loopx_turn_host_request(plan)
    keep_raw_req["epoch_prefetch"] = {
        "schema_version": "loopx_epoch_prefetch_receipt_v0",
        "operator": "KeepRaw",
        "status": "fallback_keep_raw",
        "fallback_reason": "timeout",
        "trust_level": "tool",
        "preserved_failure_facts": ["earlier check failed exit=2"],
        "preserved_open_obligations": ["keep original evidence intact"],
    }
    keep_prompt, _ = prepare_codex_cli_input(keep_raw_req)
    assert "aborted atomically to KeepRaw" in keep_prompt
    assert "earlier check failed exit=2" in keep_prompt
    assert "keep original evidence intact" in keep_prompt


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_turn_run_once_fail_closed_keep_raw_preserves_obligations_in_file_and_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    seen_log = tmp_path / "host_seen.json"
    runner, validator = _write_generic_host_and_validator(tmp_path, seen_log)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]

    cfg = {
        "enabled": True,
        "initiator": "turn_driver",
        "context_epoch": "epoch-fail-1",
        "prefetch_command": ["cat", str(tmp_path / "missing-probe.txt")],
        "timeout_ms": 2000,
        "max_output_bytes": 1024,
        "reclaimed_turns": 5,
        "dropped_failures": 1,
        "failure_facts": ["original failure fact must stay preserved"],
        "open_obligations": ["original obligation must stay preserved"],
        "baseline_bytes": 4096,
    }
    args = [
        "turn",
        "run-once",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--turn-instance-id",
        "keep-raw-once",
        "--host",
        "generic-cli",
        "--project",
        str(project),
        "--host-adapter-request-schema",
        "loopx_turn_host_request_v1",
        "--host-adapter-command-json",
        json.dumps([sys.executable, str(runner)]),
        "--validation-command-json",
        json.dumps([sys.executable, str(validator)]),
        "--epoch-prefetch-json",
        json.dumps(cfg),
        "--no-global-sync",
        "--execute",
    ]
    code, res = _invoke_cli(route, args)
    assert code == 0, res
    assert res["status"] == "committed"
    ep = res["epoch_prefetch"]
    assert ep["operator"] == "KeepRaw"
    assert ep["fallback_reason"] == "command_failed"
    assert ep["preserves_raw_state"] is True
    assert ep["preserved_failure_facts"] == ["original failure fact must stay preserved"]
    assert any("todo_fixture0001" in item for item in ep["preserved_open_obligations"])
    assert "original obligation must stay preserved" in ep["preserved_open_obligations"]

    seen = json.loads(seen_log.read_text(encoding="utf-8"))
    assert len(seen) == 1
    assert seen[0]["epoch_prefetch"]["operator"] == "KeepRaw"
    assert seen[0]["epoch_prefetch"]["preserved_failure_facts"] == ["original failure fact must stay preserved"]

    sqlite_db = runtime / "goals" / "loopx-turn-fixture" / "turn_prefetch_state.sqlite"
    with closing(sqlite3.connect(sqlite_db)) as conn:
        rows = conn.execute("SELECT operator, receipt_sha256 FROM epoch_prefetch_records").fetchall()
    assert rows == [("KeepRaw", ep["receipt_sha256"])]


def test_benchmark_cost_schema_and_token_capacity_record_telemetry_without_legacy_drift(tmp_path: Path) -> None:
    now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    row = normalize_benchmark_experiment_board_row(
        {
            "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
            "benchmark_id": "fixture@1",
            "study_id": "pareto-study",
            "case_id": "case-1",
            "run_id": "run-epoch-1",
            "arm_id": "epoch-reset",
            "arm_role": "treatment",
            "comparison_anchor_run_id": "run-baseline-1",
            "attempt": 1,
            "status": "completed",
            "observed_at": now.isoformat(),
            "model_id": "solver-model-v1",
            "protocol_id": "proto-v1",
            "comparison_protocol_id": "proto-v1",
            "claim_scope": "matched_study",
            "primary_metric": "score",
            "metrics": {"score": {"value": 9, "total": 10, "higher_is_better": True}},
            "countability": {
                "integrity_qualified": True,
                "official_result_present": True,
                "score_countable": True,
            },
            "treatment_fidelity": "qualified",
            "effort": {
                "duration_ms": 1000,
                "agent_steps": 4,
                "goal_turns": 2,
                "token_count": 3000,
                "estimated_cost_usd": 0.12,
                "cached_prefix_kv_tokens": 1800,
                "uncached_prefix_kv_tokens": 1200,
                "cold_uncached_prefix_kv_tokens": 900,
                "cold_cached_prefix_kv_tokens": 0,
                "warm_uncached_prefix_kv_tokens": 300,
                "warm_cached_prefix_kv_tokens": 1800,
                "prefetch_latency_ms": 15,
                "prefetch_bytes": 240,
                "prefetch_bytes_saved": 2600,
                "quiet_turn_skips": 2,
                "operator_counts": {"KeepRaw": 1, "FoldToReceipt": 0, "EpochReset": 2},
            },
        }
    )
    assert row["effort"]["operator_counts"] == {"KeepRaw": 1, "FoldToReceipt": 0, "EpochReset": 2}
    agg = _effort_aggregate([row])
    assert agg["operator_counts"] == {"KeepRaw": 1, "FoldToReceipt": 0, "EpochReset": 2}
    assert agg["cold_uncached_prefix_kv_tokens"]["mean"] == 900.0
    assert agg["warm_cached_prefix_kv_tokens"]["mean"] == 1800.0

    # Token capacity telemetry records cold/warm KV & operator counts while keeping omit-when-absent parity.
    cap_path = tmp_path / "token-capacity.json"
    configure_benchmark_token_capacity(
        cap_path,
        build_benchmark_token_capacity_policy(quota_tokens_per_minute=1_000_000),
        execute=True,
        observed_at=now.isoformat(),
    )
    observe_benchmark_token_usage(
        cap_path,
        {
            "schema_version": BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION,
            "sample_id": "cold-epoch",
            "observed_at": now.isoformat(),
            "metering_state": "measured",
            "request_count": 1,
            "uncached_input_tokens": 900,
            "cached_input_tokens": 0,
            "output_tokens": 50,
            "cache_regime": "cold",
            "operator_counts": {"KeepRaw": 0, "FoldToReceipt": 0, "EpochReset": 1},
            "prefetch_latency_ms": 15,
            "prefetch_bytes": 240,
            "prefetch_bytes_saved": 2600,
        },
        execute=True,
        recorded_at=now.isoformat(),
    )
    observe_benchmark_token_usage(
        cap_path,
        {
            "schema_version": BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION,
            "sample_id": "warm-follow",
            "observed_at": now.isoformat(),
            "metering_state": "measured",
            "request_count": 1,
            "uncached_input_tokens": 300,
            "cached_input_tokens": 1800,
            "output_tokens": 40,
            "cache_regime": "warm",
            "operator_counts": {"KeepRaw": 1, "FoldToReceipt": 0, "EpochReset": 0},
            "quiet_turn_skips": 2,
        },
        execute=True,
        recorded_at=now.isoformat(),
    )
    status = build_benchmark_token_capacity_status(
        read_benchmark_token_capacity_state(cap_path), observed_at=now.isoformat()
    )
    usage = status["usage"]
    assert usage["prefix_kv"]["cold"] == {"request_count": 1, "uncached_prefix_kv_tokens": 900, "cached_prefix_kv_tokens": 0}
    assert usage["prefix_kv"]["warm"] == {"request_count": 1, "uncached_prefix_kv_tokens": 300, "cached_prefix_kv_tokens": 1800}
    assert usage["context_policy"]["operator_counts"] == {"KeepRaw": 1, "FoldToReceipt": 0, "EpochReset": 1}
    assert usage["context_policy"]["quiet_turn_skips"] == 2
    assert usage["context_policy"]["prefetch_latency_ms"] == 15
    assert usage["context_policy"]["prefetch_bytes"] == 240
    assert usage["context_policy"]["prefetch_bytes_saved"] == 2600


def test_quiet_turn_failed_host_does_not_skip_next_turn_records_skip_telemetry_and_detects_conflict(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.turn_driver.epoch_prefetch import resolve_quiet_turn_metadata
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    project, runtime, registry = _write_live_fixture(tmp_path)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    digest = "9" * 64

    def _plan(turn_id: str) -> dict:
        c, p = _invoke_cli(
            route,
            [
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--turn-instance-id",
                turn_id,
                "--host",
                "generic-cli",
                "--execution-mode",
                "isolated-headless",
                "--scheduler-owner",
                "outer_controller",
                "--include-transaction-detail",
            ],
        )
        assert c == 0, p
        return p

    admit_calls = {"n": 0}
    host_calls = {"n": 0}
    should_fail = {"flag": True}

    def fake_admit(_payload: dict) -> dict:
        admit_calls["n"] += 1
        return {"admitted": True}

    def fake_host(req: dict) -> dict:
        host_calls["n"] += 1
        if should_fail["flag"]:
            raise RuntimeError("transient host failure")
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": req["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "quiet_probe",
            "recommended_action": "wait",
            "next_action": "wait",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Unchanged state.",
            "summary": "Recovered turn completed.",
            "work_evidence": ["artifact:recovered"],
            "validation_evidence": ["check:recovered"],
        }

    def ok_noop(*_args, **_kwargs) -> dict:
        return {"ok": True, "appended": True, "classification": "quiet_probe", "recommended_action": "wait"}

    qt_cfg = {
        "enabled": True,
        "quiet_noop_allowed": True,
        "current_state_digest": digest,
        "skip_host_when_unchanged": True,
    }

    # Turn 1 fails inside host_runner: digest MUST NOT be committed prematurely.
    r1 = run_loopx_turn_once(
        _plan("qt-fail-1"),
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r1["status"] == "failed" and r1["ok"] is False
    assert admit_calls["n"] == 1 and host_calls["n"] == 1

    # Turn 2 with identical digest executes cleanly (NOT skipped) and commits digest on settlement.
    should_fail["flag"] = False
    r2 = run_loopx_turn_once(
        _plan("qt-ok-2"),
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r2["ok"] is True
    assert admit_calls["n"] == 2 and host_calls["n"] == 2

    # Switch to non-due monitor wait Turn: unchanged digest now skips BEFORE admit_start() / host_runner.
    _switch_fixture_to_idle_monitor(registry, runtime)
    skip_plan = _plan("qt-skip-3")
    assert skip_plan["route"]["kind"] == "wait"
    r3 = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r3["status"] == "skipped"
    assert admit_calls["n"] == 2  # admit_start skipped!
    assert host_calls["n"] == 2
    tel = collect_context_policy_telemetry(runtime, goal_id="loopx-turn-fixture")
    assert tel["quiet_turn_skips"] == 1

    # Inject File vs SQLite conflict on quiet_turn_state -> must fail closed (state_changed=True, skipped=False).
    goal_root = runtime / "goals" / "loopx-turn-fixture"
    q_file = next((goal_root / "quiet_turn").glob("*.json"))
    q_payload = json.loads(q_file.read_text(encoding="utf-8"))
    q_payload["state_digest"] = "1" * 64
    q_file.write_text(json.dumps(q_payload), encoding="utf-8")
    conflict = resolve_quiet_turn_metadata(
        scope_key=q_payload["scope_key"],
        file_path=q_file,
        sqlite_path=goal_root / "turn_prefetch_state.sqlite",
        enabled=True,
        quiet_noop_allowed=True,
        current_state_digest=digest,
        record_if_changed=False,
    )
    assert conflict["skipped"] is False
    assert conflict["state_changed"] is True
    assert conflict["reason"] == "state_conflict"
    assert conflict["state_conflict"] is True


def test_committed_turn_replay_with_altered_epoch_prefetch_never_reexecutes_and_partial_page_keeps_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loopx.control_plane.turn_driver.epoch_prefetch as ep_mod
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    project, runtime, registry = _write_live_fixture(tmp_path)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    baseline_a = tmp_path / "baseline_a.json"
    baseline_a_raw = json.dumps(
        {
            "summary": "concise summary",
            "exploratory_history": [
                {"step": i, "detail": f"verbose raw exploratory log line {i} " * 5}
                for i in range(60)
            ],
        },
        separators=(",", ":"),
    )
    baseline_a.write_text(baseline_a_raw, encoding="utf-8")
    baseline_a_sha = hashlib.sha256(baseline_a_raw.encode("utf-8")).hexdigest()
    probe_a = tmp_path / "probe_a.json"
    probe_a.write_text(
        json.dumps(
            {
                "ok": True,
                "schema_version": "context_capture_selection_page_v1",
                "sha256": baseline_a_sha,
                "offset": 0,
                "next_offset": None,
                "total_bytes": len('{"summary":"concise summary"}'),
                "eof": True,
                "content": '{"summary":"concise summary"}',
            }
        ),
        encoding="utf-8",
    )
    probe_b = tmp_path / "probe_b.txt"
    probe_b.write_text("completely different command probe", encoding="utf-8")

    # 1) Partial page (eof=false) via real context-read --page-bytes MUST fail closed to KeepRaw.
    big_cap = tmp_path / "big_cap.json"
    big_raw = json.dumps({"big": "b" * 16000})
    big_cap.write_text(big_raw, encoding="utf-8")
    big_sha = hashlib.sha256(big_raw.encode("utf-8")).hexdigest()
    partial_receipt = resolve_or_execute_epoch_prefetch(
        epoch_prefetch={
            "enabled": True,
            "prefetch_command": [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "quota",
                "context-read",
                "--capture-file",
                str(big_cap),
                "--capture-sha256",
                big_sha,
                "--select",
                "/big",
                "--page-bytes",
                "1600",
            ],
            "timeout_ms": 10000,
            "max_output_bytes": 16384,
        },
        scope_key="partial:eof:test",
        context_epoch="epoch-partial-1",
        file_store_dir=tmp_path / "ep_partial",
        sqlite_path=tmp_path / "ep_partial.sqlite",
        source_ref={"path": str(big_cap), "sha256": big_sha},
        baseline_bytes=len(big_raw.encode("utf-8")),
        cwd=Path.cwd(),
    )
    assert partial_receipt is not None
    assert partial_receipt["operator"] == "KeepRaw"
    assert partial_receipt["fallback_reason"] == "incomplete_output"
    assert partial_receipt["truncated"] is True
    assert partial_receipt["preserves_raw_state"] is True
    assert partial_receipt["round_trips_saved"] == 0
    assert partial_receipt["bytes_saved"] == 0

    # 2) Replay of committed Turn with ALTERED --epoch-prefetch-json NEVER re-executes prefetch_command.
    c, plan = _invoke_cli(
        route,
        [
            "turn",
            "plan",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "replay-altered-1",
            "--host",
            "generic-cli",
            "--execution-mode",
            "isolated-headless",
            "--scheduler-owner",
            "outer_controller",
            "--include-transaction-detail",
        ],
    )
    assert c == 0, plan

    calls = {"n": 0}
    orig_runner = ep_mod.run_bounded_prefetch_subprocess

    def spy_runner(argv, *, timeout_ms, max_output_bytes, cwd=None):
        calls["n"] += 1
        return orig_runner(argv, timeout_ms=timeout_ms, max_output_bytes=max_output_bytes, cwd=cwd)

    monkeypatch.setattr(ep_mod, "run_bounded_prefetch_subprocess", spy_runner)

    def ok_host(req: dict) -> dict:
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": req["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "quiet_probe",
            "recommended_action": "wait",
            "next_action": "wait",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Completed.",
            "summary": "Committed.",
            "work_evidence": ["artifact:committed"],
            "validation_evidence": ["check:committed"],
        }

    def ok_noop(*_args, **_kwargs) -> dict:
        return {"ok": True, "appended": True, "classification": "quiet_probe", "recommended_action": "wait"}

    cfg_a = {
        "enabled": True,
        "context_epoch": "epoch-replay-lock",
        "prefetch_command": ["cat", str(probe_a)],
        "source_ref": {"path": str(baseline_a), "sha256": baseline_a_sha},
        "baseline_bytes": len(baseline_a_raw.encode("utf-8")),
        "timeout_ms": 2000,
        "max_output_bytes": 8192,
    }
    cfg_b = {
        "enabled": True,
        "context_epoch": "epoch-replay-lock",
        "prefetch_command": ["cat", str(probe_b)],
        "source_ref": {"path": str(baseline_a), "sha256": baseline_a_sha},
        "baseline_bytes": len(baseline_a_raw.encode("utf-8")),
        "timeout_ms": 2000,
        "max_output_bytes": 8192,
    }
    r1 = run_loopx_turn_once(
        plan,
        host_runner=ok_host,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        epoch_prefetch=cfg_a,
    )
    assert calls["n"] == 1
    assert r1["epoch_prefetch"]["operator"] == "EpochReset"
    r2 = run_loopx_turn_once(
        plan,
        host_runner=ok_host,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        epoch_prefetch=cfg_b,
    )
    assert calls["n"] == 1
    assert r2["epoch_prefetch"]["receipt_sha256"] == r1["epoch_prefetch"]["receipt_sha256"]

    # Also prove CLI return_existing (--resume-turn-key on committed Turn) with altered --epoch-prefetch-json.
    seen_log = tmp_path / "cli_seen.json"
    runner, validator = _write_generic_host_and_validator(tmp_path, seen_log)
    cli_args_a = [
        "turn",
        "run-once",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--turn-instance-id",
        "cli-replay-altered",
        "--host",
        "generic-cli",
        "--project",
        str(project),
        "--host-adapter-request-schema",
        "loopx_turn_host_request_v1",
        "--host-adapter-command-json",
        json.dumps([sys.executable, str(runner)]),
        "--validation-command-json",
        json.dumps([sys.executable, str(validator)]),
        "--epoch-prefetch-json",
        json.dumps(cfg_a),
        "--no-global-sync",
        "--execute",
    ]
    c_a, first_cli = _invoke_cli(route, cli_args_a)
    assert c_a == 0 and first_cli["status"] == "committed"
    assert calls["n"] == 2
    cli_args_b = [
        arg
        for idx, arg in enumerate(cli_args_a)
        if arg not in {"--turn-instance-id", "cli-replay-altered", "--epoch-prefetch-json", json.dumps(cfg_a)}
        and not (idx > 0 and cli_args_a[idx - 1] in {"--turn-instance-id", "--epoch-prefetch-json"})
    ] + ["--resume-turn-key", first_cli["resume_turn_key"], "--epoch-prefetch-json", json.dumps(cfg_b)]
    c_b, replay_cli = _invoke_cli(route, cli_args_b)
    assert c_b == 0 and replay_cli["replayed"] is True
    assert calls["n"] == 2  # Altered cfg_b MUST NOT re-execute!
    assert replay_cli["epoch_prefetch"]["executed"] is False
    assert replay_cli["epoch_prefetch"]["replayed"] is True
    assert replay_cli["epoch_prefetch"]["receipt_sha256"] == first_cli["epoch_prefetch"]["receipt_sha256"]


def test_inflight_quiet_turn_settlement_recovery_after_spend_failure_never_skips(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.turn_driver.epoch_prefetch import commit_quiet_turn_state
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    project, runtime, registry = _write_live_fixture(tmp_path)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    c, plan = _invoke_cli(
        route,
        [
            "turn",
            "plan",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "qt-inflight-settle",
            "--host",
            "generic-cli",
            "--execution-mode",
            "isolated-headless",
            "--scheduler-owner",
            "outer_controller",
            "--include-transaction-detail",
        ],
    )
    assert c == 0, plan

    digest = hashlib.sha256(b"same-digest-after-host").hexdigest()
    qt_cfg = {
        "enabled": True,
        "quiet_noop_allowed": True,
        "current_state_digest": digest,
    }
    scope_key = "turn_driver:loopx-turn-fixture:codex-fixture"
    goal_root = runtime / "goals" / "loopx-turn-fixture"
    host_calls = {"n": 0}
    spend_calls = {"n": 0}

    def fake_host(req: dict) -> dict:
        host_calls["n"] += 1
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": req["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "quiet_probe",
            "recommended_action": "wait",
            "next_action": "wait",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Unchanged state.",
            "summary": "Host completed before transient spend error.",
            "work_evidence": ["artifact:inflight"],
            "validation_evidence": ["check:inflight"],
        }

    def ok_noop(*_a: object, **_k: object) -> dict[str, object]:
        return {"ok": True, "appended": True, "classification": "quiet_probe", "recommended_action": "wait"}

    def flaky_spend(*_a: object, **_k: object) -> dict[str, object]:
        spend_calls["n"] += 1
        if spend_calls["n"] == 1:
            raise RuntimeError("transient spend interruption")
        return {"ok": True, "appended": True, "receipt": {"status": "committed"}}

    with pytest.raises(RuntimeError, match="transient spend interruption"):
        run_loopx_turn_once(
            plan,
            host_runner=fake_host,
            project=project,
            runtime_root=runtime,
            goal_id="loopx-turn-fixture",
            timeout_seconds=10.0,
            execute=True,
            task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
            writeback=ok_noop,
            spend=flaky_spend,
            scheduler=lambda *a, **k: {"ok": True, "acknowledged": True, "completed": True},
            quiet_turn=qt_cfg,
        )
    assert host_calls["n"] == 1
    assert spend_calls["n"] == 1

    # Even if quiet_turn_state already records the exact same state_digest on disk
    # while this Turn's journal sits in flight waiting on settlement (needs_host=False),
    # resuming the in-flight journal MUST finish spend() instead of skipping!
    commit_quiet_turn_state(
        scope_key=scope_key,
        file_path=goal_root / "quiet_turn" / f"{hashlib.sha256(scope_key.encode()).hexdigest()}.json",
        sqlite_path=goal_root / "turn_prefetch_state.sqlite",
        state_digest=digest,
        metadata={"enabled": True},
    )
    r2 = run_loopx_turn_once(
        plan,
        host_runner=fake_host,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=flaky_spend,
        spend_resolver=lambda _ref: {"kind": "absent"},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True, "completed": True},
        quiet_turn=qt_cfg,
    )
    assert host_calls["n"] == 1
    assert spend_calls["n"] == 2
    assert r2["status"] == "committed" and r2["ok"] is True
    tel = collect_context_policy_telemetry(runtime, goal_id="loopx-turn-fixture")
    assert tel["quiet_turn_skips"] == 0


def test_collect_context_policy_telemetry_prefers_usage_prefix_kv_and_compacted_cold(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "goals" / "g1").mkdir(parents=True)
    # 1) Prefers usage["context_policy_telemetry"]["prefix_kv"] over flat/request fallback
    usage_with_cpt = {
        "context_policy_telemetry": {
            "prefix_kv": {
                "cold": {"requests": 2, "uncached_prefix_kv_tokens": 500, "cached_prefix_kv_tokens": 100},
                "warm": {"requests": 3, "uncached_prefix_kv_tokens": 90, "cached_prefix_kv_tokens": 900},
            }
        },
        "request_coverage": {
            "requests": [{"input_tokens": 100, "cached_input_tokens": 80}],
        },
    }
    tel = collect_context_policy_telemetry(runtime, goal_id="g1", usage=usage_with_cpt)
    assert tel["prefix_kv"] == usage_with_cpt["context_policy_telemetry"]["prefix_kv"]

    # 2) Fallback on request_coverage classifies compacted=True as cold even when cached_input_tokens > 0
    usage_requests = {
        "request_coverage": {
            "requests": [
                {"input_tokens": 200, "cached_input_tokens": 150, "compacted": True},
                {"input_tokens": 120, "cached_input_tokens": 90, "compacted": False},
            ]
        }
    }
    tel2 = collect_context_policy_telemetry(runtime, goal_id="g1", usage=usage_requests)
    assert tel2["prefix_kv"]["cold"] == {
        "requests": 1,
        "uncached_prefix_kv_tokens": 50,
        "cached_prefix_kv_tokens": 150,
    }
    assert tel2["prefix_kv"]["warm"] == {
        "requests": 1,
        "uncached_prefix_kv_tokens": 30,
        "cached_prefix_kv_tokens": 90,
    }


def test_real_cli_context_read_nonzero_offset_tail_fails_closed_vs_zero_offset_select(
    tmp_path: Path,
) -> None:
    cap = tmp_path / "large_capture.json"
    cap_obj = {"summary": "concise executive summary", "noise": "n" * 14100}
    cap_raw = json.dumps(cap_obj, separators=(",", ":"))
    cap.write_text(cap_raw, encoding="utf-8")
    cap_sha = hashlib.sha256(cap_raw.encode("utf-8")).hexdigest()
    total_len = len(cap_raw.encode("utf-8"))
    assert total_len > 14095

    # 1) Exact A2 repro: real `loopx quota context-read --offset 14095 --page-bytes 512`
    # yields eof=true on the final 80B tail! Must STILL fail closed to KeepRaw.
    tail_receipt = resolve_or_execute_epoch_prefetch(
        epoch_prefetch={
            "enabled": True,
            "prefetch_command": [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "quota",
                "context-read",
                "--capture-file",
                str(cap),
                "--capture-sha256",
                cap_sha,
                "--offset",
                "14095",
                "--page-bytes",
                "512",
            ],
            "timeout_ms": 10000,
            "max_output_bytes": 16384,
        },
        scope_key="offset-tail-guard:tail",
        context_epoch="epoch-tail-1",
        file_store_dir=tmp_path / "ep_tail",
        sqlite_path=tmp_path / "ep_tail.sqlite",
        source_ref={"path": str(cap), "sha256": cap_sha},
        baseline_bytes=total_len,
        cwd=Path.cwd(),
    )
    assert tail_receipt is not None
    assert tail_receipt["operator"] == "KeepRaw"
    assert tail_receipt["fallback_reason"] == "incomplete_output"
    assert tail_receipt["preserves_raw_state"] is True
    assert tail_receipt["round_trips_saved"] == 0
    assert tail_receipt["bytes_saved"] == 0

    # 2) Paired --offset 0 --select /summary complete compact page commits EpochReset!
    zero_select_receipt = resolve_or_execute_epoch_prefetch(
        epoch_prefetch={
            "enabled": True,
            "prefetch_command": [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "quota",
                "context-read",
                "--capture-file",
                str(cap),
                "--capture-sha256",
                cap_sha,
                "--select",
                "/summary",
                "--offset",
                "0",
                "--page-bytes",
                "4096",
            ],
            "timeout_ms": 10000,
            "max_output_bytes": 16384,
        },
        scope_key="offset-tail-guard:select-zero",
        context_epoch="epoch-tail-2",
        file_store_dir=tmp_path / "ep_zero_select",
        sqlite_path=tmp_path / "ep_zero_select.sqlite",
        source_ref={"path": str(cap), "sha256": cap_sha},
        baseline_bytes=total_len,
        cwd=Path.cwd(),
    )
    assert zero_select_receipt is not None
    assert zero_select_receipt["operator"] == "EpochReset"
    assert zero_select_receipt["status"] == "committed"
    assert zero_select_receipt["round_trips_saved"] == 1
    assert zero_select_receipt["bytes_saved"] > 10_000

    # 3) Paired --offset 0 with mismatched source_ref sha256 fails closed to KeepRaw.
    wrong_sha = ("0" if cap_sha[0] != "0" else "1") + cap_sha[1:]
    mismatch_receipt = resolve_or_execute_epoch_prefetch(
        epoch_prefetch={
            "enabled": True,
            "prefetch_command": [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "quota",
                "context-read",
                "--capture-file",
                str(cap),
                "--capture-sha256",
                cap_sha,
                "--select",
                "/summary",
                "--offset",
                "0",
                "--page-bytes",
                "4096",
            ],
            "timeout_ms": 10000,
            "max_output_bytes": 16384,
        },
        scope_key="offset-tail-guard:digest-mismatch",
        context_epoch="epoch-tail-3",
        file_store_dir=tmp_path / "ep_digest_mismatch",
        sqlite_path=tmp_path / "ep_digest_mismatch.sqlite",
        source_ref={"path": str(cap), "sha256": wrong_sha},
        baseline_bytes=total_len,
        cwd=Path.cwd(),
    )
    assert mismatch_receipt is not None
    assert mismatch_receipt["operator"] == "KeepRaw"
    assert mismatch_receipt["fallback_reason"] == "source_digest_mismatch"
    assert mismatch_receipt["round_trips_saved"] == 0
    assert mismatch_receipt["bytes_saved"] == 0


def test_codex_cli_prompt_single_emission_and_rendered_prompt_net_byte_gate(tmp_path: Path) -> None:
    """PR #9213 P2: single-emission in _render_prompt + real rendered-prompt net byte check/gate."""
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.epoch_prefetch import apply_turn_epoch_prefetch_to_request
    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request

    _project, runtime, registry = _write_live_fixture(tmp_path, provider="file")
    c, plan = _invoke_cli(
        ["--registry", str(registry), "--runtime-root", str(runtime)],
        [
            "turn", "plan",
            "--goal-id", "loopx-turn-fixture",
            "--agent-id", "codex-fixture",
            "--turn-instance-id", "prompt-gate-1",
            "--host", "generic-cli",
            "--execution-mode", "isolated-headless",
            "--scheduler-owner", "outer_controller",
            "--include-transaction-detail",
        ],
    )
    assert c == 0, plan

    base_req = build_loopx_turn_host_request(plan)
    base_req["exploratory_context"] = [
        {"turn": i, "summary": f"verbose exploratory tool output row {i} " * 5}
        for i in range(40)
    ]
    baseline_prompt = _prompt(base_req)
    baseline_prompt_bytes = len(baseline_prompt.encode("utf-8"))
    assert baseline_prompt_bytes > 10_000

    hydrated = "[Stage-then-Swap EpochReset Receipt | trust=tool]\ncontext_epoch: e-prompt\n--- prefetched_output ---\nSUMMARY_OK"
    receipt = {
        "schema_version": "loopx_epoch_prefetch_receipt_v0",
        "operator": "EpochReset",
        "status": "committed",
        "trust_level": "tool",
        "context_epoch": "e-prompt",
        "source_ref": None,
        "header_fields": {"failure_facts": ["earlier test failed"], "open_obligations": ["keep obligation"]},
        "hydrated_text": hydrated,
        "baseline_bytes": 8000,
        "bytes_saved": 7061,
        "net_savings_bytes": 7061,
        "round_trips_saved": 1,
        "replayed": False,
    }

    # 1. Default append mode (replace_exploratory_context=False) MUST NOT grow prompt while claiming savings!
    appended = apply_turn_epoch_prefetch_to_request(base_req, receipt, replace_exploratory_context=False)
    appended_ep = appended["epoch_prefetch"]
    assert appended_ep["operator"] == "KeepRaw"
    assert appended_ep["fallback_reason"] == "negative_net_savings"
    assert appended_ep["bytes_saved"] == 0
    assert appended_ep["round_trips_saved"] == 0
    assert appended["exploratory_context"] == base_req["exploratory_context"]
    appended_prompt = _prompt(appended)
    assert hydrated not in appended_prompt
    assert appended_prompt.count("earlier test failed") == 1

    # 2. Explicit replace_exploratory_context=True shrinks rendered prompt and emits hydrated_text ONCE.
    replaced = apply_turn_epoch_prefetch_to_request(base_req, receipt, replace_exploratory_context=True)
    replaced_ep = replaced["epoch_prefetch"]
    replaced_prompt = _prompt(replaced)
    replaced_prompt_bytes = len(replaced_prompt.encode("utf-8"))
    real_saved = baseline_prompt_bytes - replaced_prompt_bytes
    assert replaced_ep["operator"] == "EpochReset"
    assert replaced_prompt.count(hydrated) == 1
    assert real_saved > 0
    assert replaced_ep["bytes_saved"] == min(7061, real_saved)
    assert replaced_ep["rendered_prompt_baseline_bytes"] == baseline_prompt_bytes
    assert replaced_ep["rendered_prompt_delivered_bytes"] == replaced_prompt_bytes


def _quiet_turn_fixture_callbacks(seen_hosts: list[str] | None = None, admit_counter: dict[str, int] | None = None):
    def fake_admit(_payload: dict) -> dict:
        if admit_counter is not None:
            admit_counter["n"] += 1
        return {"admitted": True}

    def fake_host(req: dict) -> dict:
        if seen_hosts is not None:
            seen_hosts.append(req["turn_key"])
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": req["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "quiet_probe",
            "recommended_action": "wait",
            "next_action": "wait",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Unchanged state.",
            "summary": "Fixture host turn completed.",
            "work_evidence": ["artifact:turn-ok"],
            "validation_evidence": ["check:turn-ok"],
        }

    def ok_noop(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"ok": True, "appended": True, "classification": "quiet_probe", "recommended_action": "wait"}

    return fake_admit, fake_host, ok_noop


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quiet_turn_never_skips_mandatory_todo_or_breaks_default_off_wait_across_file_and_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    def _plan(turn_id: str) -> dict:
        c, p = _invoke_cli(
            route,
            [
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--turn-instance-id",
                turn_id,
                "--host",
                "generic-cli",
                "--execution-mode",
                "isolated-headless",
                "--scheduler-owner",
                "outer_controller",
                "--include-transaction-detail",
            ],
        )
        assert c == 0, p
        return p

    digest = "a" * 64
    seen_hosts: list[str] = []
    admit_calls = {"n": 0}
    fake_admit, fake_host, ok_noop = _quiet_turn_fixture_callbacks(seen_hosts, admit_calls)
    qt_cfg = {
        "enabled": True,
        "quiet_noop_allowed": True,
        "current_state_digest": digest,
        "skip_host_when_unchanged": True,
    }

    # Seed baseline state_digest via a settled Turn 1.
    r1 = run_loopx_turn_once(
        _plan("seed-1"),
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r1["ok"] is True and admit_calls["n"] == 1 and len(seen_hosts) == 1

    # Turn 2 still has open Todo (must_attempt=True, quiet_noop_allowed=False) -> CANNOT skip!
    open_plan = _plan("mandatory-open-2")
    assert open_plan["work_contract"]["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert open_plan["work_contract"]["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False
    r2 = run_loopx_turn_once(
        open_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r2["status"] == "scheduler_action_required"
    assert r2["quiet_turn"]["skipped"] is False
    assert r2["quiet_turn"]["quiet_noop_allowed"] is False
    assert admit_calls["n"] == 2 and len(seen_hosts) == 2

    # Legacy v0 envelope with must_attempt=True, quiet_noop_allowed=False MUST also refuse skipping.
    import copy
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope

    open_plan_v0 = _plan("v0-mandatory-1")
    v0_mandatory_plan = copy.deepcopy(open_plan_v0)
    v0_mandatory_plan["schema_version"] = "loopx_turn_plan_v0"
    v0_mandatory_plan["turn_envelope"] = {
        **build_turn_envelope(open_plan_v0["work_contract"]),
        "interaction_contract": {
            "agent_channel": {"must_attempt": True, "delivery_allowed": True, "quiet_noop_allowed": False}
        },
    }
    for key in ("work_contract", "source_decision_hash", "detail_ref"):
        v0_mandatory_plan.pop(key, None)
    r_v0 = run_loopx_turn_once(
        v0_mandatory_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        require_decision_capture=False,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r_v0["status"] == "scheduler_action_required"
    assert r_v0["quiet_turn"]["skipped"] is False
    assert admit_calls["n"] == 3 and len(seen_hosts) == 3

    # Switch to non-due monitor wait Turn -> default-off (quiet_turn=False) preserves exact ValueError parity!
    _switch_fixture_to_idle_monitor(registry, runtime)
    wait_plan = _plan("idle-wait-1")
    assert wait_plan["route"]["kind"] == "wait"
    assert wait_plan["work_contract"]["interaction_contract"]["agent_channel"]["must_attempt"] is False
    assert wait_plan["work_contract"]["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is True
    with pytest.raises(ValueError, match="LoopX Turn route is not host executable"):
        run_loopx_turn_once(
            wait_plan,
            host_runner=fake_host,
            admit_start=fake_admit,
            project=project,
            runtime_root=runtime,
            goal_id="loopx-turn-fixture",
            timeout_seconds=10.0,
            execute=True,
            task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
            writeback=ok_noop,
            spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
            scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
            quiet_turn=False,
        )
    assert admit_calls["n"] == 3 and len(seen_hosts) == 3


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quiet_turn_skip_replay_idempotency_and_post_skip_host_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    def _plan(turn_id: str) -> dict:
        c, p = _invoke_cli(
            route,
            [
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--turn-instance-id",
                turn_id,
                "--host",
                "generic-cli",
                "--execution-mode",
                "isolated-headless",
                "--scheduler-owner",
                "outer_controller",
                "--include-transaction-detail",
            ],
        )
        assert c == 0, p
        return p

    digest_v1 = "b" * 64
    digest_v2 = "c" * 64
    seen_hosts: list[str] = []
    admit_calls = {"n": 0}
    fake_admit, fake_host, ok_noop = _quiet_turn_fixture_callbacks(seen_hosts, admit_calls)
    qt_v1 = {
        "enabled": True,
        "current_state_digest": digest_v1,
        "skip_host_when_unchanged": True,
    }

    # Commit initial digest on Turn 1, then move to non-due monitor wait Turn.
    r1 = run_loopx_turn_once(
        _plan("seed-skip-1"),
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_v1,
    )
    assert r1["ok"] is True and admit_calls["n"] == 1 and len(seen_hosts) == 1

    _switch_fixture_to_idle_monitor(registry, runtime)
    skip_plan = _plan("quiet-skip-once")
    turn_key = skip_plan["transaction"]["turn_key"]

    r_skip = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_v1,
    )
    assert r_skip["status"] == "skipped" and r_skip["replayed"] is False
    assert admit_calls["n"] == 1 and len(seen_hosts) == 1

    goal_root = runtime / "goals" / "loopx-turn-fixture"
    skip_file = goal_root / "quiet_turn" / "skips" / f"{hashlib.sha256(turn_key.encode('utf-8')).hexdigest()}.json"
    sqlite_path = goal_root / "turn_prefetch_state.sqlite"
    orig_file_bytes = skip_file.read_bytes()
    orig_file_mtime_ns = skip_file.stat().st_mtime_ns
    with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as conn:
        sqlite_rows_before = conn.execute(
            "SELECT turn_key, scope_key, state_digest, receipt_json, recorded_at FROM quiet_turn_skips ORDER BY turn_key"
        ).fetchall()
    assert len(sqlite_rows_before) == 1

    # Retry 1: exact same Turn identity -> terminal replay, File & SQLite skip receipts UNTOUCHED!
    r_retry_same = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_v1,
    )
    assert r_retry_same["status"] == "skipped" and r_retry_same["replayed"] is True
    assert r_retry_same["quiet_turn"]["replayed"] is True
    assert admit_calls["n"] == 1 and len(seen_hosts) == 1
    assert skip_file.read_bytes() == orig_file_bytes
    assert skip_file.stat().st_mtime_ns == orig_file_mtime_ns
    with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as conn:
        assert conn.execute(
            "SELECT turn_key, scope_key, state_digest, receipt_json, recorded_at FROM quiet_turn_skips ORDER BY turn_key"
        ).fetchall() == sqlite_rows_before

    # Retry 2: same Turn identity after state_digest changes -> MUST still replay skip & block admission/host!
    r_retry_changed_digest = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn={"enabled": True, "current_state_digest": digest_v2, "state_changed": True, "skip_host_when_unchanged": True},
    )
    assert r_retry_changed_digest["status"] == "skipped" and r_retry_changed_digest["replayed"] is True
    assert r_retry_changed_digest["quiet_turn"]["state_digest"] == digest_v1
    assert admit_calls["n"] == 1 and len(seen_hosts) == 1

    # Retry 3: same Turn identity with quiet_turn=False -> MUST still block host invocation & journal creation!
    r_retry_disabled = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=False,
    )
    assert r_retry_disabled["status"] == "skipped" and r_retry_disabled["replayed"] is True
    assert admit_calls["n"] == 1 and len(seen_hosts) == 1
    assert not turn_journal_path(runtime, goal_id="loopx-turn-fixture", turn_key=turn_key).exists()
    assert skip_file.read_bytes() == orig_file_bytes
    assert skip_file.stat().st_mtime_ns == orig_file_mtime_ns
    assert collect_context_policy_telemetry(runtime, goal_id="loopx-turn-fixture")["quiet_turn_skips"] == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quiet_turn_skip_single_store_crash_healing_and_fail_closed_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    def _plan(turn_id: str) -> dict:
        c, p = _invoke_cli(
            route,
            [
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--turn-instance-id",
                turn_id,
                "--host",
                "generic-cli",
                "--execution-mode",
                "isolated-headless",
                "--scheduler-owner",
                "outer_controller",
                "--include-transaction-detail",
            ],
        )
        assert c == 0, p
        return p

    digest = "d" * 64
    seen_hosts: list[str] = []
    admit_calls = {"n": 0}
    fake_admit, fake_host, ok_noop = _quiet_turn_fixture_callbacks(seen_hosts, admit_calls)
    qt_cfg = {"enabled": True, "current_state_digest": digest, "skip_host_when_unchanged": True}

    run_loopx_turn_once(
        _plan("heal-seed"),
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    _switch_fixture_to_idle_monitor(registry, runtime)
    skip_plan = _plan("heal-skip")
    turn_key = skip_plan["transaction"]["turn_key"]
    r_skip = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r_skip["status"] == "skipped" and admit_calls["n"] == 1 and len(seen_hosts) == 1

    goal_root = runtime / "goals" / "loopx-turn-fixture"
    skip_file = goal_root / "quiet_turn" / "skips" / f"{hashlib.sha256(turn_key.encode('utf-8')).hexdigest()}.json"
    sqlite_path = goal_root / "turn_prefetch_state.sqlite"
    orig_bytes = skip_file.read_bytes()
    orig_payload = json.loads(orig_bytes.decode("utf-8"))

    # 1. Drop SQLite row only -> replay crash-heals SQLite while leaving File untouched.
    with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as conn:
        conn.execute("DELETE FROM quiet_turn_skips WHERE turn_key = ?", (turn_key,))
        conn.commit()
    mtime_before_heal = skip_file.stat().st_mtime_ns
    r_heal_sql = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=False,
    )
    assert r_heal_sql["status"] == "skipped" and r_heal_sql["replayed"] is True
    assert skip_file.stat().st_mtime_ns == mtime_before_heal
    with closing(sqlite3.connect(sqlite_path, timeout=5.0)) as conn:
        healed_row = conn.execute(
            "SELECT recorded_at, receipt_json FROM quiet_turn_skips WHERE turn_key = ?",
            (turn_key,),
        ).fetchone()
    assert healed_row is not None and healed_row[0] == orig_payload["recorded_at"]

    # 2. Delete File skip receipt only -> replay crash-heals File with identical recorded_at & skips host!
    skip_file.unlink()
    r_heal_file = run_loopx_turn_once(
        skip_plan,
        host_runner=fake_host,
        admit_start=fake_admit,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=ok_noop,
        spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
        quiet_turn=qt_cfg,
    )
    assert r_heal_file["status"] == "skipped" and r_heal_file["replayed"] is True
    assert json.loads(skip_file.read_text(encoding="utf-8")) == orig_payload
    assert admit_calls["n"] == 1 and len(seen_hosts) == 1

    # 3. Conflict between File and SQLite skip receipts MUST fail closed without invoking Host.
    tampered = dict(orig_payload)
    tampered["state_digest"] = "0" * 64
    skip_file.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="Conflicting File and SQLite Quiet-Turn skip receipts"):
        run_loopx_turn_once(
            skip_plan,
            host_runner=fake_host,
            admit_start=fake_admit,
            project=project,
            runtime_root=runtime,
            goal_id="loopx-turn-fixture",
            timeout_seconds=10.0,
            execute=True,
            task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
            writeback=ok_noop,
            spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
            scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
            quiet_turn=qt_cfg,
        )
    assert admit_calls["n"] == 1 and len(seen_hosts) == 1

    # 4. Corrupt File skip receipt MUST fail closed without falling back to Host.
    skip_file.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="Corrupt File Quiet-Turn skip receipt"):
        run_loopx_turn_once(
            skip_plan,
            host_runner=fake_host,
            admit_start=fake_admit,
            project=project,
            runtime_root=runtime,
            goal_id="loopx-turn-fixture",
            timeout_seconds=10.0,
            execute=True,
            task_validator=lambda p, r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
            writeback=ok_noop,
            spend=lambda *a, **k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
            scheduler=lambda *a, **k: {"ok": True, "acknowledged": True},
            quiet_turn=qt_cfg,
        )
    assert admit_calls["n"] == 1 and len(seen_hosts) == 1


def test_stage_then_swap_token_gate_blocks_byte_shrink_when_tokens_grow_or_fail_cold_prefill(
    tmp_path: Path,
) -> None:
    """PR #9213 P2 #1: gate EpochReset on verifiable token net savings minus cold-prefill amortization."""
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.epoch_prefetch import (
        apply_turn_epoch_prefetch_to_request,
        count_verifiable_tokens,
    )
    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request

    project, runtime, registry = _write_live_fixture(tmp_path, provider="file")
    seen_log = tmp_path / "cf_seen.json"
    runner, validator = _write_generic_host_and_validator(tmp_path, seen_log)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    c, plan = _invoke_cli(
        route,
        [
            "turn",
            "plan",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "token-gate-cf-1",
            "--host",
            "generic-cli",
            "--execution-mode",
            "isolated-headless",
            "--scheduler-owner",
            "outer_controller",
            "--include-transaction-detail",
        ],
    )
    assert c == 0, plan

    # 1. Exact counterfactual: low-token-density exploratory_context + high-token-density prefetched observation.
    # Rendered prompt UTF-8 bytes DROP (~25.5 KB -> ~22.6 KB) while o200k_base tokens GROW (~5,380 -> ~5,830)!
    raw_req = build_loopx_turn_host_request(plan)
    raw_req["exploratory_context"] = {"notes": "exploration " * 360}
    raw_prompt = _prompt(raw_req)
    raw_bytes = len(raw_prompt.encode("utf-8"))
    raw_tok_pair = count_verifiable_tokens(raw_prompt)
    assert raw_tok_pair is not None
    raw_tokens, tok_meta = raw_tok_pair
    assert tok_meta["encoding"] == "o200k_base"
    assert 25_000 <= raw_bytes <= 27_500 and 5_200 <= raw_tokens <= 6_000

    dense_obs = " ".join(f"{i % 10}{(i * 7) % 10}!" for i in range(230))
    dense_hydrated = (
        "[Stage-then-Swap EpochReset Receipt | trust=tool]\n"
        "context_epoch: epoch-cf-1\n--- prefetched_output ---\n" + dense_obs
    )
    dense_receipt = {
        "schema_version": "loopx_epoch_prefetch_receipt_v0",
        "operator": "EpochReset",
        "status": "committed",
        "trust_level": "tool",
        "context_epoch": "epoch-cf-1",
        "source_ref": None,
        "header_fields": {"failure_facts": ["earlier check failed"], "open_obligations": ["keep raw"]},
        "hydrated_text": dense_hydrated,
        "baseline_bytes": 4300,
        "bytes_saved": 2500,
        "net_savings_bytes": 2500,
        "round_trips_saved": 1,
        "replayed": False,
    }
    blocked = apply_turn_epoch_prefetch_to_request(
        raw_req, dense_receipt, replace_exploratory_context=True,
    )
    blocked_ep = blocked["epoch_prefetch"]
    assert blocked_ep["operator"] == "KeepRaw"
    assert blocked_ep["fallback_reason"] == "negative_net_savings"
    assert blocked_ep["bytes_saved"] == 0
    assert blocked_ep["round_trips_saved"] == 0
    assert blocked_ep["rendered_prompt_baseline_bytes"] == raw_bytes
    assert blocked_ep["rendered_prompt_baseline_tokens"] == raw_tokens
    assert blocked_ep["net_savings_tokens"] < 0
    assert blocked_ep["amortized_net_savings_tokens"] < 0
    assert blocked["exploratory_context"] == raw_req["exploratory_context"]
    blocked_delivered_prompt = _prompt(blocked)
    assert dense_hydrated not in blocked_delivered_prompt
    blocked_delivered_bytes = len(blocked_delivered_prompt.encode("utf-8"))
    blocked_delivered_tok_pair = count_verifiable_tokens(blocked_delivered_prompt)
    assert blocked_delivered_tok_pair is not None
    assert blocked_ep["rendered_prompt_delivered_bytes"] == blocked_delivered_bytes
    assert blocked_ep["rendered_prompt_delta_bytes"] == blocked_delivered_bytes - raw_bytes
    assert blocked_ep["rendered_prompt_delivered_tokens"] == blocked_delivered_tok_pair[0]
    assert blocked_ep["rendered_prompt_delta_tokens"] == blocked_delivered_tok_pair[0] - raw_tokens

    # Custom host renderer that includes audit telemetry converges on stabilized KeepRaw bytes and tokens.
    def _custom_keep_renderer(req: dict) -> str:
        return json.dumps(req, sort_keys=True, separators=(",", ":"))

    custom_blocked = apply_turn_epoch_prefetch_to_request(
        raw_req,
        dense_receipt,
        replace_exploratory_context=True,
        host_input_renderer=_custom_keep_renderer,
    )
    custom_ep = custom_blocked["epoch_prefetch"]
    custom_prompt = _custom_keep_renderer(custom_blocked)
    assert custom_ep["operator"] == "KeepRaw"
    assert custom_ep["rendered_prompt_delivered_bytes"] == len(custom_prompt.encode("utf-8"))
    assert custom_ep["rendered_prompt_delivered_tokens"] == count_verifiable_tokens(custom_prompt)[0]

    # 2. Marginal positive token savings (~120 tokens) that fail cold-prefill amortization (~265 tokens) -> KeepRaw!
    marginal_obs = " ".join(f"item{i}" for i in range(110))
    marginal_receipt = {
        **dense_receipt,
        "hydrated_text": (
            "[Stage-then-Swap EpochReset Receipt | trust=tool]\n"
            "context_epoch: epoch-cf-marginal\n--- prefetched_output ---\n" + marginal_obs
        ),
    }
    marginal_res = apply_turn_epoch_prefetch_to_request(
        raw_req, marginal_receipt, replace_exploratory_context=True,
    )
    marginal_ep = marginal_res["epoch_prefetch"]
    assert marginal_ep["operator"] == "KeepRaw"
    assert marginal_ep["fallback_reason"] == "negative_net_savings"
    assert 0 < marginal_ep["net_savings_tokens"] < marginal_ep["cold_prefill_amortization_tokens"]
    assert marginal_ep["amortized_net_savings_tokens"] < marginal_ep["min_net_savings_tokens"]

    # 3. Missing/failing token estimator OR unverified external baseline (bytes-only without source_ref) -> KeepRaw!
    failing_est = apply_turn_epoch_prefetch_to_request(
        raw_req,
        dense_receipt,
        replace_exploratory_context=True,
        token_counter=lambda _text: -1,
    )
    assert failing_est["epoch_prefetch"]["operator"] == "KeepRaw"
    assert failing_est["epoch_prefetch"]["fallback_reason"] == "token_estimation_unavailable"
    assert failing_est["epoch_prefetch"]["rendered_prompt_delivered_bytes"] == len(
        _prompt(failing_est).encode("utf-8")
    )
    assert "rendered_prompt_delivered_tokens" not in failing_est["epoch_prefetch"]
    assert "rendered_prompt_delta_tokens" not in failing_est["epoch_prefetch"]

    no_source_req = build_loopx_turn_host_request(plan)
    unverified_ext = apply_turn_epoch_prefetch_to_request(
        no_source_req,
        {
            **dense_receipt,
            "hydrated_text": "[Stage-then-Swap EpochReset Receipt | trust=tool]\ncontext_epoch: e-ext\n--- prefetched_output ---\nOK",
            "baseline_bytes": 16000,
        },
        replace_exploratory_context=False,
    )
    assert unverified_ext["epoch_prefetch"]["operator"] == "KeepRaw"
    assert unverified_ext["epoch_prefetch"]["fallback_reason"] == "token_estimation_unavailable"

    # 4. Genuine net-token-saving contrast meeting threshold + cold-prefill amortization -> EpochReset!
    concise_hydrated = (
        "[Stage-then-Swap EpochReset Receipt | trust=tool]\n"
        "context_epoch: epoch-cf-ok\n--- prefetched_output ---\nSUMMARY_OK"
    )
    genuine_receipt = {
        **dense_receipt,
        "hydrated_text": concise_hydrated,
    }
    # Use a wider low-density exploratory section (~1,000 tokens) so net savings clear cold-prefill amortization (~255).
    wide_req = build_loopx_turn_host_request(plan)
    wide_req["exploratory_context"] = [
        {"turn": i, "summary": f"verbose exploratory tool output row {i} " * 4}
        for i in range(30)
    ]
    genuine = apply_turn_epoch_prefetch_to_request(
        wide_req, genuine_receipt, replace_exploratory_context=True,
    )
    genuine_ep = genuine["epoch_prefetch"]
    assert genuine_ep["operator"] == "EpochReset"
    assert genuine_ep["bytes_saved"] > 0
    assert genuine_ep["net_savings_tokens"] > 500
    assert genuine_ep["cold_prefill_amortization_tokens"] > 0
    assert genuine_ep["amortized_net_savings_tokens"] >= genuine_ep["min_net_savings_tokens"]
    assert genuine_ep["tokenizer"]["encoding"] == "o200k_base"
    delivered_prompt = _prompt(genuine)
    assert len(delivered_prompt.encode("utf-8")) == genuine_ep["rendered_prompt_delivered_bytes"]
    assert count_verifiable_tokens(delivered_prompt)[0] == genuine_ep["rendered_prompt_delivered_tokens"]

    # 5. End-to-end real CLI run where capture shrinks bytes (~4.6 KB -> ~1.5 KB) yet dense probe grows tokens!
    cap_low_density = tmp_path / "low_density_capture.json"
    cap_low_raw = json.dumps({"summary": "exploration " * 380}, separators=(",", ":"))
    cap_low_density.write_text(cap_low_raw, encoding="utf-8")
    cap_low_sha = hashlib.sha256(cap_low_raw.encode("utf-8")).hexdigest()
    dense_page = tmp_path / "dense_page.json"
    dense_page.write_text(
        json.dumps(
            {
                "ok": True,
                "schema_version": "context_capture_selection_page_v1",
                "sha256": cap_low_sha,
                "offset": 0,
                "next_offset": None,
                "total_bytes": len(dense_obs.encode("utf-8")),
                "eof": True,
                "content": dense_obs,
            }
        ),
        encoding="utf-8",
    )
    code_cli, res_cli = _invoke_cli(
        route,
        [
            "turn",
            "run-once",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "cli-token-gate-cf",
            "--host",
            "generic-cli",
            "--project",
            str(project),
            "--host-adapter-request-schema",
            "loopx_turn_host_request_v1",
            "--host-adapter-command-json",
            json.dumps([sys.executable, str(runner)]),
            "--validation-command-json",
            json.dumps([sys.executable, str(validator)]),
            "--epoch-prefetch-json",
            json.dumps(
                {
                    "enabled": True,
                    "context_epoch": "epoch-cli-cf",
                    "prefetch_command": ["cat", str(dense_page)],
                    "source_ref": {"path": str(cap_low_density), "sha256": cap_low_sha},
                    "baseline_bytes": len(cap_low_raw.encode("utf-8")),
                    "timeout_ms": 2000,
                    "max_output_bytes": 4096,
                }
            ),
            "--no-global-sync",
            "--execute",
        ],
    )
    assert code_cli == 0, res_cli
    assert res_cli["epoch_prefetch"]["operator"] == "KeepRaw"
    assert res_cli["epoch_prefetch"]["bytes_saved"] == 0
    assert res_cli["epoch_prefetch"]["round_trips_saved"] == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_quiet_turn_wait_route_persists_skip_replays_and_preserves_default_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    """PR #9213 P2 #2: pure CLI `--quiet-turn` on idle non-due monitor skips, persists, replays, and preserves default-off."""
    import time as _time

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    _switch_fixture_to_idle_monitor(registry, runtime)
    seen_log = tmp_path / "cli_quiet_host_seen.json"
    runner, validator = _write_generic_host_and_validator(tmp_path, seen_log)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]

    def _cli_run_once(turn_instance_id: str, *, quiet: bool, host: str = "generic-cli") -> tuple[int, dict]:
        args = [
            "turn",
            "run-once",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            turn_instance_id,
            "--host",
            host,
            "--project",
            str(project),
            "--no-global-sync",
            "--execute",
        ]
        if host == "generic-cli":
            args.extend([
                "--host-adapter-request-schema",
                "loopx_turn_host_request_v1",
                "--host-adapter-command-json",
                json.dumps([sys.executable, str(runner)]),
                "--validation-command-json",
                json.dumps([sys.executable, str(validator)]),
            ])
        if quiet:
            args.append("--quiet-turn")
        return _invoke_cli(route, args)

    # 1. Default-off (without --quiet-turn) preserves exact existing refusal on wait route.
    c_off, out_off = _cli_run_once("cli-wait-default-off", quiet=False)
    assert c_off == 1
    assert out_off.get("ok") is False
    assert "LoopX Turn route is not host executable" in str(out_off.get("error") or out_off.get("reason"))
    assert not seen_log.exists()

    # 2. First pure CLI --quiet-turn observation establishes stable authoritative baseline digest cleanly.
    c_seed, out_seed = _cli_run_once("cli-wait-baseline", quiet=True)
    assert c_seed == 0, out_seed
    assert out_seed["ok"] is True
    assert out_seed["status"] == "stopped"
    assert out_seed["quota_slot_spend_count"] == 0
    assert out_seed["effects"]["host_invoked"] is False
    assert out_seed["quiet_turn"]["skipped"] is False
    assert out_seed["quiet_turn"]["state_changed"] is True
    assert not seen_log.exists()

    # Cross a wall-clock second to verify volatile projection_envelope timestamps never break unchanged state digest!
    _time.sleep(1.1)

    # 3. Run 1 via pure CLI --quiet-turn on unchanged idle monitor plan: skips Host & persists skip receipt!
    c_run1, out_run1 = _cli_run_once("cli-wait-skip-1", quiet=True)
    assert c_run1 == 0, out_run1
    assert out_run1["ok"] is True
    assert out_run1["status"] == "skipped"
    assert out_run1["replayed"] is False
    assert out_run1["quota_slot_spend_count"] == 0
    assert out_run1["effects"]["host_invoked"] is False
    assert out_run1["quiet_turn"]["skipped"] is True
    assert out_run1["quiet_turn"]["zero_tax"] is True
    assert not seen_log.exists()

    turn_key = out_run1["resume_turn_key"]
    goal_root = runtime / "goals" / "loopx-turn-fixture"
    skip_file = goal_root / "quiet_turn" / "skips" / f"{hashlib.sha256(turn_key.encode('utf-8')).hexdigest()}.json"
    sqlite_db = goal_root / "turn_prefetch_state.sqlite"
    assert skip_file.is_file()
    orig_skip_bytes = skip_file.read_bytes()
    orig_skip_mtime = skip_file.stat().st_mtime_ns
    assert sqlite_db.is_file()
    with closing(sqlite3.connect(sqlite_db, timeout=5.0)) as conn:
        rows_after_run1 = conn.execute(
            "SELECT turn_key, scope_key, state_digest, receipt_json, recorded_at FROM quiet_turn_skips ORDER BY turn_key"
        ).fetchall()
    assert len(rows_after_run1) == 1
    assert not turn_journal_path(runtime, goal_id="loopx-turn-fixture", turn_key=turn_key).exists()

    # 4. Run 2 via pure CLI --quiet-turn (same turn identity): replays skipped with replayed=True!
    c_run2, out_run2 = _cli_run_once("cli-wait-skip-1", quiet=True)
    assert c_run2 == 0, out_run2
    assert out_run2["ok"] is True
    assert out_run2["status"] == "skipped"
    assert out_run2["replayed"] is True
    assert out_run2["quiet_turn"]["skipped"] is True
    assert out_run2["quiet_turn"]["replayed"] is True
    assert out_run2["effects"]["host_invoked"] is False
    assert skip_file.read_bytes() == orig_skip_bytes
    assert skip_file.stat().st_mtime_ns == orig_skip_mtime
    assert not seen_log.exists()

    # 5. Also verify default codex-cli host path (no --host-adapter-command-json) baseline -> skip -> replay!
    c_codex_base, out_codex_base = _cli_run_once("cli-wait-codex-base", quiet=True, host="codex-cli")
    assert c_codex_base == 0, out_codex_base
    assert out_codex_base["status"] == "stopped"
    assert out_codex_base["quiet_turn"]["skipped"] is False
    c_codex, out_codex = _cli_run_once("cli-wait-codex-1", quiet=True, host="codex-cli")
    assert c_codex == 0, out_codex
    assert out_codex["status"] == "skipped"
    assert out_codex["replayed"] is False
    c_codex2, out_codex2 = _cli_run_once("cli-wait-codex-1", quiet=True, host="codex-cli")
    assert c_codex2 == 0, out_codex2
    assert out_codex2["status"] == "skipped"
    assert out_codex2["replayed"] is True

    # 6. Default-off on a fresh turn identity WITHOUT --quiet-turn STILL refuses cleanly!
    c_off2, out_off2 = _cli_run_once("cli-wait-default-off-2", quiet=False)
    assert c_off2 == 1
    assert out_off2.get("ok") is False
    assert "LoopX Turn route is not host executable" in str(out_off2.get("error") or out_off2.get("reason"))

    # 7. Real state change (adding an open advancement Todo) breaks unchanged skip & invokes Host!
    add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Actionable task unblocks active work.",
        task_class="advancement_task",
        claimed_by="codex-fixture",
    )
    c_work, out_work = _cli_run_once("cli-wait-unblocked", quiet=True)
    assert c_work == 0, out_work
    assert out_work["status"] == "committed"
    assert out_work["quiet_turn"]["skipped"] is False
    assert out_work["quiet_turn"]["quiet_noop_allowed"] is False
    assert seen_log.is_file() and len(json.loads(seen_log.read_text(encoding="utf-8"))) == 1


def _write_raw_stdin_generic_host_and_validator(tmp_path: Path, seen_log: Path) -> tuple[Path, Path]:
    runner = tmp_path / "raw_stdin_generic_host_runner.py"
    runner.write_text(
        "import json, pathlib, sys\n"
        "raw_stdin = sys.stdin.read()\n"
        "request = json.loads(raw_stdin)\n"
        f"log_path = pathlib.Path({str(seen_log)!r})\n"
        "existing = json.loads(log_path.read_text()) if log_path.exists() else []\n"
        "existing.append({\n"
        "  'turn_key': request['turn_key'],\n"
        "  'raw_stdin': raw_stdin,\n"
        "  'stdin_utf8_bytes': len(raw_stdin.encode('utf-8')),\n"
        "  'epoch_prefetch': request.get('epoch_prefetch'),\n"
        "  'exploratory_context': request.get('exploratory_context'),\n"
        "})\n"
        "log_path.write_text(json.dumps(existing))\n"
        "result = {\n"
        "  'schema_version': 'loopx_turn_result_v0',\n"
        "  'turn_key': request['turn_key'],\n"
        "  'result_kind': 'validated_progress',\n"
        "  'completed_phases': ['host_execute', 'typed_result'],\n"
        "  'classification': 'fixture_stage_swap',\n"
        "  'recommended_action': 'continue verified step',\n"
        "  'next_action': 'continue verified step',\n"
        "  'delivery_batch_scale': 'single_surface',\n"
        "  'delivery_outcome': 'outcome_progress',\n"
        "  'vision_unchanged_reason': 'Fixture objective remains active.',\n"
        "  'summary': 'Fixture host completed bounded work.',\n"
        "  'work_evidence': ['artifact:fixture-prefetch'],\n"
        "  'validation_evidence': ['check:fixture-validator'],\n"
        "}\n"
        "json.dump(result, sys.stdout)\n",
        encoding="utf-8",
    )
    validator = tmp_path / "validator.py"
    validator.write_text("import sys; sys.stdin.read()\n", encoding="utf-8")
    return runner, validator


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_generic_cli_stdin_gate_rejects_codex_passing_counterexample_and_commits_positive_savings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    """PR #9213 review 6042617569: gate EpochReset on actual generic-cli stdin JSON, not Codex prompt tokens."""
    from loopx.control_plane.turn_driver.epoch_prefetch import (
        apply_turn_epoch_prefetch_to_request,
        count_verifiable_tokens,
    )
    from loopx.control_plane.turn_driver.executor import (
        build_loopx_turn_host_request,
        serialize_generic_cli_host_input,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    facts = [f"prior failure trace {i}: exit=1 module=solver check=parity" for i in range(8)]
    obligations = [f"preserve acceptance gate {i}: check regression parity and readback" for i in range(8)]
    cap_obj = {
        "ok": True,
        "summary": {"next_probe": "verify solver parity", "status": "ok"},
        "history": [
            {"step": i, "detail": "verbose exploratory observation record payload " * 4}
            for i in range(154)
        ],
    }
    cap_raw = json.dumps(cap_obj, separators=(",", ":"))
    cap_file = tmp_path / "counterexample_capture.json"
    cap_file.write_text(cap_raw, encoding="utf-8")
    cap_sha = hashlib.sha256(cap_raw.encode("utf-8")).hexdigest()
    cap_tokens_pair = count_verifiable_tokens(cap_raw, encoding_name="o200k_base")
    assert cap_tokens_pair is not None
    cap_tokens, _ = cap_tokens_pair

    def _run_in_isolated_fixture(
        lane_name: str, epoch_cfg: dict | None = None,
    ) -> tuple[dict, dict, Path, Path, Path]:
        lane_dir = tmp_path / lane_name
        lane_dir.mkdir(parents=True)
        project, runtime, registry = _write_live_fixture(lane_dir, provider=provider)
        seen_log = lane_dir / "raw_stdin_seen.json"
        runner, validator = _write_raw_stdin_generic_host_and_validator(lane_dir, seen_log)
        route = ["--registry", str(registry), "--runtime-root", str(runtime)]
        cmd = [
            "turn",
            "run-once",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "generic-stdin-turn",
            "--host",
            "generic-cli",
            "--project",
            str(project),
            "--host-adapter-request-schema",
            "loopx_turn_host_request_v1",
            "--host-adapter-command-json",
            json.dumps([sys.executable, str(runner)]),
            "--validation-command-json",
            json.dumps([sys.executable, str(validator)]),
            "--no-global-sync",
            "--execute",
        ]
        if epoch_cfg is not None:
            cmd.extend(["--epoch-prefetch-json", json.dumps(epoch_cfg)])
        code, res = _invoke_cli(route, cmd)
        assert code == 0, res
        seen = json.loads(seen_log.read_text(encoding="utf-8"))
        assert len(seen) == 1
        return res, seen[0], project, runtime, registry

    # 1. Baseline uncompacted generic-cli stdin payload (without --epoch-prefetch-json).
    baseline_res, baseline_host_entry, _, _, _ = _run_in_isolated_fixture("lane_baseline")
    assert baseline_res["status"] == "committed"
    baseline_stdin = baseline_host_entry["raw_stdin"]
    baseline_tok_pair = count_verifiable_tokens(baseline_stdin, encoding_name="o200k_base")
    assert baseline_tok_pair is not None
    baseline_stdin_tokens, _ = baseline_tok_pair

    # 2. Reviewer's counterexample: min_net_savings_tokens=3241, cold_prefill_amortization_tokens=128.
    # Codex prompt clears >= 3241 (~3425 tokens), while actual generic-cli stdin JSON falls short (< 3241)!
    prefetch_argv = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "quota",
        "context-read",
        "--capture-file",
        str(cap_file),
        "--capture-sha256",
        cap_sha,
        "--select",
        "/summary",
    ]
    counterexample_cfg = {
        "enabled": True,
        "initiator": "turn_driver",
        "context_epoch": "epoch-ce-3241",
        "prefetch_command": prefetch_argv,
        "timeout_ms": 10000,
        "max_output_bytes": 8192,
        "reclaimed_turns": 18,
        "dropped_failures": len(facts),
        "failure_facts": facts,
        "open_obligations": obligations,
        "source_ref": {"path": str(cap_file), "sha256": cap_sha},
        "baseline_bytes": len(cap_raw.encode("utf-8")),
        "min_net_savings_tokens": 3241,
        "cold_prefill_amortization_tokens": 128,
    }
    ce_res, ce_host_entry, project, runtime, registry = _run_in_isolated_fixture(
        "lane_counterexample", counterexample_cfg,
    )
    assert ce_res["status"] == "committed"
    ce_ep = ce_res["epoch_prefetch"]
    assert ce_ep["operator"] == "KeepRaw"
    assert ce_ep["fallback_reason"] == "negative_net_savings"
    assert ce_ep["preserves_raw_state"] is True
    assert ce_ep["bytes_saved"] == 0
    assert ce_ep["round_trips_saved"] == 0
    assert ce_ep["cold_prefill_amortization_tokens"] == 128
    assert ce_ep["min_net_savings_tokens"] == 3241
    assert ce_ep["amortized_net_savings_tokens"] < 3241

    assert ce_host_entry["epoch_prefetch"]["operator"] == "KeepRaw"
    assert ce_host_entry["epoch_prefetch"]["fallback_reason"] == "negative_net_savings"
    assert ce_host_entry["epoch_prefetch"].get("hydrated_text") is None
    assert ce_host_entry["epoch_prefetch"]["preserved_failure_facts"] == facts
    assert "hydrated_text" not in ce_host_entry["raw_stdin"]
    assert "[Stage-then-Swap EpochReset Receipt" not in ce_host_entry["raw_stdin"]

    ce_stdin = ce_host_entry["raw_stdin"]
    ce_stdin_bytes = len(ce_stdin.encode("utf-8"))
    assert ce_host_entry["stdin_utf8_bytes"] == ce_stdin_bytes
    ce_tok_pair = count_verifiable_tokens(ce_stdin, encoding_name="o200k_base")
    assert ce_tok_pair is not None
    ce_stdin_tokens, _ = ce_tok_pair
    assert ce_ep["rendered_prompt_delivered_bytes"] == ce_stdin_bytes
    assert ce_ep["rendered_prompt_delivered_tokens"] == ce_stdin_tokens
    assert ce_ep["rendered_prompt_delta_bytes"] == ce_stdin_bytes - ce_ep["rendered_prompt_baseline_bytes"]
    assert ce_ep["rendered_prompt_delta_tokens"] == ce_stdin_tokens - ce_ep["rendered_prompt_baseline_tokens"]

    ce_journal = json.loads(
        turn_journal_path(
            runtime, goal_id="loopx-turn-fixture", turn_key=ce_res["resume_turn_key"],
        ).read_text(encoding="utf-8")
    )
    assert ce_journal["epoch_prefetch"]["rendered_prompt_delivered_bytes"] == ce_stdin_bytes
    assert ce_journal["epoch_prefetch"]["rendered_prompt_delivered_tokens"] == ce_stdin_tokens
    assert ce_journal["epoch_prefetch"]["rendered_prompt_delta_bytes"] == (
        ce_stdin_bytes - ce_ep["rendered_prompt_baseline_bytes"]
    )
    assert ce_journal["epoch_prefetch"]["rendered_prompt_delta_tokens"] == (
        ce_stdin_tokens - ce_ep["rendered_prompt_baseline_tokens"]
    )
    assert ce_journal["epoch_prefetch"]["amortized_net_savings_tokens"] == ce_ep["amortized_net_savings_tokens"]

    ce_lane_dir = runtime.parent
    c_replay, ce_replay = _invoke_cli(
        ["--registry", str(registry), "--runtime-root", str(runtime)],
        [
            "turn",
            "run-once",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--resume-turn-key",
            ce_res["resume_turn_key"],
            "--host",
            "generic-cli",
            "--project",
            str(project),
            "--host-adapter-request-schema",
            "loopx_turn_host_request_v1",
            "--host-adapter-command-json",
            json.dumps([sys.executable, str(ce_lane_dir / "raw_stdin_generic_host_runner.py")]),
            "--validation-command-json",
            json.dumps([sys.executable, str(ce_lane_dir / "validator.py")]),
            "--epoch-prefetch-json",
            json.dumps(counterexample_cfg),
            "--no-global-sync",
            "--execute",
        ],
    )
    assert c_replay == 0, ce_replay
    assert ce_replay["epoch_prefetch"]["replayed"] is True
    assert ce_replay["epoch_prefetch"]["executed"] is False
    assert ce_replay["epoch_prefetch"]["operator"] == "KeepRaw"
    assert ce_replay["epoch_prefetch"]["rendered_prompt_delivered_bytes"] == ce_stdin_bytes
    assert ce_replay["epoch_prefetch"]["rendered_prompt_delivered_tokens"] == ce_stdin_tokens
    assert ce_replay["epoch_prefetch"]["rendered_prompt_delta_bytes"] == (
        ce_stdin_bytes - ce_replay["epoch_prefetch"]["rendered_prompt_baseline_bytes"]
    )
    assert ce_replay["epoch_prefetch"]["rendered_prompt_delta_tokens"] == (
        ce_stdin_tokens - ce_replay["epoch_prefetch"]["rendered_prompt_baseline_tokens"]
    )
    assert len(json.loads((ce_lane_dir / "raw_stdin_seen.json").read_text(encoding="utf-8"))) == 1

    # Confirm the exact same plan + capture + policy clears >= 3241 when gated for codex-cli!
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    c_plan, plan_obj = _invoke_cli(
        route,
        [
            "turn",
            "plan",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "generic-stdin-turn",
            "--host",
            "generic-cli",
            "--execution-mode",
            "isolated-headless",
            "--scheduler-owner",
            "outer_controller",
            "--include-transaction-detail",
        ],
    )
    assert c_plan == 0, plan_obj
    req_obj = build_loopx_turn_host_request(plan_obj)
    epoch_receipt_ce = resolve_or_execute_epoch_prefetch(
        epoch_prefetch=counterexample_cfg,
        scope_key="counterexample:compare",
        context_epoch="epoch-ce-3241",
        file_store_dir=tmp_path / "ce_cmp_f",
        sqlite_path=tmp_path / "ce_cmp.sqlite",
        reclaimed_turns=18,
        dropped_failures=len(facts),
        failure_facts=facts,
        open_obligations=obligations,
        source_ref={"path": str(cap_file), "sha256": cap_sha},
        baseline_bytes=len(cap_raw.encode("utf-8")),
        cwd=project,
    )
    assert epoch_receipt_ce is not None and epoch_receipt_ce["operator"] == "EpochReset"
    codex_gate = apply_turn_epoch_prefetch_to_request(
        req_obj,
        epoch_receipt_ce,
        epoch_prefetch=counterexample_cfg,
        host="codex-cli",
    )
    assert codex_gate["epoch_prefetch"]["operator"] == "EpochReset"
    assert codex_gate["epoch_prefetch"]["amortized_net_savings_tokens"] >= 3241

    # 3. Positive generic-cli case clearing actual stdin JSON threshold commits EpochReset & matches stdin bytes/tokens.
    positive_cfg = {
        **counterexample_cfg,
        "context_epoch": "epoch-ce-positive",
        "min_net_savings_tokens": 2800,
    }
    pos_res, pos_host_entry, _, pos_runtime, _ = _run_in_isolated_fixture(
        "lane_positive", positive_cfg,
    )
    assert pos_res["status"] == "committed"
    pos_ep = pos_res["epoch_prefetch"]
    assert pos_ep["operator"] == "EpochReset"
    assert pos_ep["bytes_saved"] > 0
    assert pos_ep["round_trips_saved"] == 1
    assert pos_ep["cold_prefill_amortization_tokens"] == 128
    assert pos_ep["amortized_net_savings_tokens"] >= 2800

    pos_stdin = pos_host_entry["raw_stdin"]
    pos_tok_pair = count_verifiable_tokens(pos_stdin, encoding_name="o200k_base")
    assert pos_tok_pair is not None
    pos_stdin_tokens, _ = pos_tok_pair
    assert pos_host_entry["epoch_prefetch"]["operator"] == "EpochReset"
    assert pos_host_entry["stdin_utf8_bytes"] == pos_ep["rendered_prompt_delivered_bytes"]
    assert pos_stdin_tokens == pos_ep["rendered_prompt_delivered_tokens"]
    assert baseline_host_entry["stdin_utf8_bytes"] == pos_ep["rendered_prompt_baseline_bytes"]
    assert abs(baseline_stdin_tokens - pos_ep["rendered_prompt_baseline_tokens"]) < 64
    pos_journal = json.loads(
        turn_journal_path(
            pos_runtime, goal_id="loopx-turn-fixture", turn_key=pos_res["resume_turn_key"],
        ).read_text(encoding="utf-8")
    )
    pos_baseline_stdin = serialize_generic_cli_host_input(
        build_loopx_turn_host_request(pos_journal["plan"])
    )
    pos_baseline_tok_pair = count_verifiable_tokens(pos_baseline_stdin, encoding_name="o200k_base")
    assert pos_baseline_tok_pair is not None
    pos_baseline_tokens, _ = pos_baseline_tok_pair
    assert len(pos_baseline_stdin.encode("utf-8")) == pos_ep["rendered_prompt_baseline_bytes"]
    assert pos_baseline_tokens == pos_ep["rendered_prompt_baseline_tokens"]
    measured_amortized = cap_tokens - (pos_stdin_tokens - pos_baseline_tokens) - 128
    assert measured_amortized == pos_ep["amortized_net_savings_tokens"]
    assert measured_amortized >= 2800

    # 4. Inline exploratory_context replacement for generic-cli stabilizes digit counts & matches exact serializer.
    inline_req = dict(req_obj)
    inline_req["exploratory_context"] = [
        {"turn": i, "summary": f"verbose inline generic exploratory output row {i} " * 5}
        for i in range(40)
    ]
    inline_receipt = {
        **epoch_receipt_ce,
        "source_ref": None,
        "hydrated_text": "[Stage-then-Swap EpochReset Receipt | trust=tool]\ncontext_epoch: e-inline-gen\n--- prefetched_output ---\nOK",
        "header_fields": {"failure_facts": ["check failed"], "open_obligations": ["preserve test"]},
        "bytes_saved": 10_500,
        "net_savings_bytes": 10_500,
    }
    inline_res = apply_turn_epoch_prefetch_to_request(
        inline_req,
        inline_receipt,
        replace_exploratory_context=True,
        epoch_prefetch={"enabled": True, "min_net_savings_tokens": 200, "cold_prefill_amortization_tokens": 128},
        host="generic-cli",
    )
    inline_ep = inline_res["epoch_prefetch"]
    assert inline_ep["operator"] == "EpochReset"
    delivered_inline_stdin = serialize_generic_cli_host_input(inline_res)
    assert len(delivered_inline_stdin.encode("utf-8")) == inline_ep["rendered_prompt_delivered_bytes"]
    assert count_verifiable_tokens(delivered_inline_stdin)[0] == inline_ep["rendered_prompt_delivered_tokens"]


def test_unmeasurable_and_unsupported_hosts_fail_closed_to_keep_raw(tmp_path: Path) -> None:
    """PR #9213: non-Codex hosts never silently fall back to Codex prompt formatting."""
    from loopx.control_plane.turn_driver.epoch_prefetch import (
        apply_turn_epoch_prefetch_to_request,
        count_verifiable_tokens,
    )
    from loopx.control_plane.turn_driver.executor import (
        build_loopx_turn_host_request,
        run_loopx_turn_once,
    )
    from loopx.control_plane.turn_driver.host_candidate import render_dsh_host_prompt

    project, runtime, registry = _write_live_fixture(tmp_path, provider="file")
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    c, plan = _invoke_cli(
        route,
        [
            "turn",
            "plan",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "unmeasurable-host-1",
            "--host",
            "codex-cli",
            "--execution-mode",
            "isolated-headless",
            "--include-transaction-detail",
        ],
    )
    assert c == 0, plan
    base_req = build_loopx_turn_host_request(plan)
    base_req["exploratory_context"] = {"notes": "verbose exploration " * 200}

    receipt = {
        "schema_version": "loopx_epoch_prefetch_receipt_v0",
        "operator": "EpochReset",
        "status": "committed",
        "trust_level": "tool",
        "context_epoch": "epoch-unmeasurable",
        "source_ref": None,
        "header_fields": {"failure_facts": ["preserved fact"], "open_obligations": ["preserved obligation"]},
        "hydrated_text": "[Stage-then-Swap EpochReset Receipt | trust=tool]\ncontext_epoch: epoch-unmeasurable\n--- prefetched_output ---\nOK",
        "baseline_bytes": 4000,
        "bytes_saved": 3500,
        "net_savings_bytes": 3500,
        "round_trips_saved": 1,
        "replayed": False,
    }

    for host_name in ("codex-app-server", "claude-code", "unknown-custom-host", ""):
        res = apply_turn_epoch_prefetch_to_request(
            base_req,
            receipt,
            replace_exploratory_context=True,
            host=host_name,
        )
        ep = res["epoch_prefetch"]
        assert ep["operator"] == "KeepRaw", host_name
        assert ep["fallback_reason"] == "host_input_unmeasurable", host_name
        assert ep["preserves_raw_state"] is True
        assert ep["bytes_saved"] == 0
        assert ep["round_trips_saved"] == 0
        assert res["exploratory_context"] == base_req["exploratory_context"]

    explicit_none = apply_turn_epoch_prefetch_to_request(
        base_req,
        receipt,
        replace_exploratory_context=True,
        host="codex-cli",
        host_input_renderer=None,
    )
    assert explicit_none["epoch_prefetch"]["operator"] == "KeepRaw"
    assert explicit_none["epoch_prefetch"]["fallback_reason"] == "host_input_unmeasurable"

    # DSH host with work_contract uses render_dsh_host_prompt and matches byte/token counts; legacy without work_contract fails closed.
    dsh_req = dict(base_req)
    dsh_req["exploratory_context"] = [
        {"turn": i, "summary": f"verbose exploratory dsh context observation row {i} " * 5}
        for i in range(40)
    ]
    dsh_res = apply_turn_epoch_prefetch_to_request(
        dsh_req,
        receipt,
        replace_exploratory_context=True,
        host="dsh",
    )
    assert dsh_res["epoch_prefetch"]["operator"] == "EpochReset"
    dsh_prompt = render_dsh_host_prompt(dsh_res)
    assert dsh_prompt is not None
    assert len(dsh_prompt.encode("utf-8")) == dsh_res["epoch_prefetch"]["rendered_prompt_delivered_bytes"]
    assert count_verifiable_tokens(dsh_prompt)[0] == dsh_res["epoch_prefetch"]["rendered_prompt_delivered_tokens"]

    legacy_req = {k: v for k, v in base_req.items() if k != "work_contract"}
    assert render_dsh_host_prompt(legacy_req) is None
    legacy_dsh = apply_turn_epoch_prefetch_to_request(
        legacy_req,
        receipt,
        replace_exploratory_context=True,
        host="dsh",
    )
    assert legacy_dsh["epoch_prefetch"]["operator"] == "KeepRaw"
    assert legacy_dsh["epoch_prefetch"]["fallback_reason"] == "host_input_unmeasurable"

    # End-to-end Turn executor with codex-app-server (unmeasurable native host) fails closed to KeepRaw.
    cap_file = tmp_path / "unmeasurable_cap.json"
    cap_raw = json.dumps({"summary": "ok", "history": ["verbose row " * 5 for _ in range(120)]}, separators=(",", ":"))
    cap_file.write_text(cap_raw, encoding="utf-8")
    cap_sha = hashlib.sha256(cap_raw.encode("utf-8")).hexdigest()
    page_file = tmp_path / "unmeasurable_page.json"
    page_file.write_text(
        json.dumps(
            {
                "ok": True,
                "schema_version": "context_capture_selection_page_v1",
                "sha256": cap_sha,
                "offset": 0,
                "next_offset": None,
                "total_bytes": 16,
                "eof": True,
                "content": '{"summary":"ok"}',
            }
        ),
        encoding="utf-8",
    )
    app_server_plan = {
        **plan,
        "host": {**plan["host"], "kind": "codex-app-server"},
    }
    seen_host_requests: list[dict] = []

    def native_like_runner(req: dict) -> dict:
        seen_host_requests.append(dict(req))
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": req["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "fixture_unmeasurable",
            "recommended_action": "continue",
            "next_action": "continue",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Unchanged.",
            "summary": "Done.",
            "work_evidence": ["artifact:ok"],
            "validation_evidence": ["check:ok"],
        }

    run_res = run_loopx_turn_once(
        app_server_plan,
        host_runner=native_like_runner,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        task_validator=lambda _p, _r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        writeback=lambda *_a, **_k: {"ok": True, "appended": True, "classification": "ok", "recommended_action": "wait"},
        spend=lambda *_a, **_k: {"ok": True, "appended": True, "receipt": {"status": "committed"}},
        scheduler=lambda *_a, **_k: {"ok": True, "completed": True, "acknowledged": True},
        epoch_prefetch={
            "enabled": True,
            "context_epoch": "epoch-app-server",
            "prefetch_command": ["cat", str(page_file)],
            "source_ref": {"path": str(cap_file), "sha256": cap_sha},
            "baseline_bytes": len(cap_raw.encode("utf-8")),
            "timeout_ms": 2000,
            "max_output_bytes": 4096,
        },
    )
    assert run_res["status"] == "committed"
    assert run_res["epoch_prefetch"]["operator"] == "KeepRaw"
    assert run_res["epoch_prefetch"]["fallback_reason"] == "host_input_unmeasurable"
    assert run_res["epoch_prefetch"]["bytes_saved"] == 0
    assert run_res["epoch_prefetch"]["round_trips_saved"] == 0
    assert "rendered_prompt_delivered_bytes" not in run_res["epoch_prefetch"]
    assert "rendered_prompt_delivered_tokens" not in run_res["epoch_prefetch"]
    assert len(seen_host_requests) == 1
    assert seen_host_requests[0]["epoch_prefetch"]["operator"] == "KeepRaw"
