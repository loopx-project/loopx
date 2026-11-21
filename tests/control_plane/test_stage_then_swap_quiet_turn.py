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

    # Create a large capture (>4KB noisy raw turns + compact selected summary).
    raw_history = [{"turn": idx, "log": f"verbose exploratory tool output row {idx} " * 4} for idx in range(25)]
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

    # Second Turn with unchanged state digest skips BEFORE host/prefetch/quota spend.
    plan_second = _plan("qt-2")
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
    assert host_calls["n"] == 1  # Host never invoked!
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
