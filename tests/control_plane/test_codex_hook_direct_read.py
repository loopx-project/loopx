"""Tests for Phase-2 lock-free Codex compact hook direct-read, wake dedup, and auto fact extraction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
from loopx.control_plane.turn_driver import codex_context as context
from loopx.control_plane.turn_driver import codex_hook_direct_read as direct_read


def _write_private_json(path: Path, payload: dict[str, object]) -> tuple[str, bytes]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest(), raw


def _seed_session_with_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture_payload: dict[str, object],
    session_id: str = "compact-session-1",
    compact_direct_read_policy: dict[str, object] | None = None,
) -> tuple[Path, dict[str, object], str]:
    journal_dir = tmp_path / "native-context"
    journal_dir.mkdir(mode=0o700, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv("CODEX_THREAD_ID", session_id)

    capture_path = tmp_path / "decision.json"
    digest, _ = _write_private_json(capture_path, capture_payload)
    state: dict[str, object] = {
        "session_id": session_id,
        "cwd": str(workspace),
        "context_epoch": "epoch-1",
        "recovery": {
            "path": str(capture_path),
            "sha256": digest,
            "goal_id": str(capture_payload.get("goal_id") or "goal-fixture"),
            "agent_id": "agent-1",
            "command": (
                f"loopx --format json quota context-read --capture-file {capture_path} "
                f"--capture-sha256 {digest}"
            ),
        },
        "candidate": {
            "basis": "native_hook_assumption",
            "capture_bytes_verified": True,
            "capture_sha256": digest,
        },
    }
    if compact_direct_read_policy is not None:
        state["compact_direct_read_policy"] = compact_direct_read_policy
    context._save(journal_dir, state)
    event = {
        "session_id": session_id,
        "cwd": str(workspace),
        "hook_event_name": "SessionStart",
        "source": "compact",
    }
    return journal_dir, event, digest


def test_lock_free_direct_read_verifies_sha256_eof_pagination_and_latency_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = tmp_path / "capture.json"
    payload = {
        "goal_id": "edgebench-fixture",
        "selected_todo": {
            "todo_id": "todo_alpha",
            "title": "Verify 中文 UTF-8 pagination across lock-free boundaries 🚀",
            "requirements": [f"Requirement item {idx}: preserve exact invariants." for idx in range(18)],
        },
        "special/key~tilde": {"nested": [10, 20, 30]},
    }
    digest, _ = _write_private_json(capture, payload)

    import fcntl
    flock_calls: list[object] = []
    monkeypatch.setattr(fcntl, "flock", lambda *a, **kw: flock_calls.append(a))

    started = time.perf_counter()
    ok, result, decoded = direct_read.read_json_pointers_paginated(
        capture,
        digest,
        pointers=["/selected_todo", "/special~1key~0tilde/nested/1"],
        page_bytes=128,
        max_output_bytes=16384,
        require_all_pointers=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert flock_calls == []
    assert ok is True, result
    assert decoded == payload
    assert result["lock_free"] is True
    assert result["sha256_verified"] is True
    assert result["eof"] is True
    assert result["pages_read"] >= 4
    assert result["read_latency_ms"] < 5.0
    assert elapsed_ms < 5.0
    rendered = json.loads(result["rendered_text"])
    assert rendered["selections"] == [
        {"pointer": "/selected_todo", "value": payload["selected_todo"]},
        {"pointer": "/special~1key~0tilde/nested/1", "value": 20},
    ]

    # Exceeding max_output_bytes must fail closed rather than emit eof=false partial output.
    too_small_ok, too_small, _ = direct_read.read_json_pointers_paginated(
        capture,
        digest,
        pointers=["/selected_todo"],
        page_bytes=128,
        max_output_bytes=512,
    )
    assert too_small_ok is False
    assert too_small["eof"] is False
    assert too_small["truncated"] is True
    assert too_small["reason"] == "selection_exceeds_max_output_bytes"

    # Tampered bytes must fail SHA-256 verification cleanly.
    capture.write_bytes(b'{"tampered": true}')
    tamper_ok, tamper_res, _ = direct_read.read_json_pointers_paginated(
        capture, digest, pointers=["/selected_todo"],
    )
    assert tamper_ok is False
    assert tamper_res["sha256_verified"] is False
    assert tamper_res["eof"] is False


def test_compact_hook_direct_read_survives_startup_lock_timeout_and_deduplicates_wakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "goal_id": "edgebench-research-20261004",
        "selected_todo": {
            "todo_id": "todo_9351cbfeac8fc866a135596b",
            "title": "Lock-free compact hook direct-read",
            "requirements": ["Verify sha256 and eof=true", "Preserve real failure facts"],
        },
        "plan_summary": {
            "requirements": "Keep default-off parity and zero PreToolUse friction.",
            "stops": "Never fabricate verification receipts.",
        },
        "interaction_contract": {
            "cli_channel": {
                "settlement_plan": {"identity": {"turn_instance_id": "turn-42"}}
            }
        },
    }
    journal_dir, compact_start_event, digest = _seed_session_with_capture(
        tmp_path,
        monkeypatch,
        capture_payload=payload,
        compact_direct_read_policy={"enabled": True, "page_bytes": 256},
    )

    # Simulate Node TS effect_runtime hitting 11.5s startup_lock_timeout under load.
    def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise EffectRuntimeStartupError(
            "startup lock timed out", diagnostic_code="startup_lock_timeout",
        )

    monkeypatch.setattr(
        "loopx.control_plane.quota.turn_envelope.update_native_context_lifecycle",
        _boom,
    )

    # 1. PreCompact rotates epoch / invalidates candidate fail-closed without discarding recovery
    #    and without emitting or consuming post-compact wake prompts.
    pre_reply = context.codex_context_hook(
        str(journal_dir),
        {**compact_start_event, "hook_event_name": "PreCompact"},
    )
    assert pre_reply == {}
    after_pre = context._state(journal_dir)
    assert "candidate" not in after_pre
    assert after_pre["context_epoch"] != "epoch-1"
    assert after_pre["recovery"]["sha256"] == digest
    assert after_pre.get("baseline_invalidated_fail_closed") is True

    # 2. PostCompact stays non-consuming ({}) while keeping baseline invalidated and
    #    never setting compact_hook_wake or counting a direct-read wake.
    post_reply = context.codex_context_hook(
        str(journal_dir),
        {**compact_start_event, "hook_event_name": "PostCompact"},
    )
    assert post_reply == {}
    after_post = context._state(journal_dir)
    assert "compact_hook_wake" not in after_post
    assert "compact_hook_metrics" not in after_post
    assert after_post["recovery"]["sha256"] == digest

    # 3. First SessionStart(compact) performs <5ms lock-free direct-read + emits EpochReset context.
    t0 = time.perf_counter()
    start_reply = context.codex_context_hook(str(journal_dir), compact_start_event)
    hook_ms = (time.perf_counter() - t0) * 1000.0
    assert hook_ms < 50.0
    hook_out = start_reply["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "SessionStart"
    meta = hook_out["compactDirectRead"]
    assert meta["operator"] == "EpochReset"
    assert meta["sha256_verified"] is True
    assert meta["eof"] is True
    assert meta["deduplicated"] is False
    assert meta["open_obligations_count"] >= 3
    text = hook_out["additionalContext"]
    assert "operator=EpochReset | sha256_verified=true | eof=true" in text
    assert "todo_9351cbfeac8fc866a135596b" in text
    assert "Keep default-off parity" in text
    assert "turn-42" in text
    assert context._state(journal_dir)["compact_hook_wake"]["emitted_hook"] == "SessionStart"

    # 4. Immediate duplicate SessionStart(compact) within same epoch & unchanged digest deduplicates prompt.
    dup_reply = context.codex_context_hook(str(journal_dir), compact_start_event)
    dup_out = dup_reply["hookSpecificOutput"]
    assert "additionalContext" not in dup_out
    assert dup_out["compactDirectRead"]["deduplicated"] is True
    assert dup_out["compactDirectRead"]["suppressed_duplicate_prompt"] is True

    metrics = context._state(journal_dir)["compact_hook_metrics"]
    assert metrics["direct_reads"] == 1
    assert metrics["deduplicated_wakes"] == 1

    # 4. If a new failure lands in run_history.json or a new compaction occurs, wake emits fresh context!
    workspace = Path(str(compact_start_event["cwd"]))
    (workspace / "run_history.json").write_text(
        json.dumps({"entries": [{"round": "agent-new", "status": "failed", "valid": False}]}),
        encoding="utf-8",
    )
    fresh_reply = context.codex_context_hook(str(journal_dir), compact_start_event)
    fresh_out = fresh_reply["hookSpecificOutput"]
    assert fresh_out["compactDirectRead"]["deduplicated"] is False
    assert "agent-new" in fresh_out["additionalContext"]


def test_auto_extracts_failure_facts_and_open_obligations_and_strips_private_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    goal_id = "edgebench-research-20261004"
    explore_dir = runtime_root / "goals" / goal_id
    explore_dir.mkdir(parents=True, exist_ok=True)
    (explore_dir / "explore-result-log.jsonl").write_text(
        "\n".join([
            json.dumps({
                "event_kind": "node",
                "result_id": "node_resolved",
                "status": "blocked",
                "title": "Earlier blocker",
                "blocked_reason": "superseded later",
            }),
            json.dumps({
                "event_kind": "node",
                "result_id": "node_resolved",
                "status": "resolved",
                "title": "Earlier blocker",
            }),
            json.dumps({
                "event_kind": "node",
                "result_id": "node_cxx_oom",
                "status": "dead_end",
                "title": "Unbounded 50-case lock retry",
                "blocked_reason": "hit /Users/alice/private/secret.json startup_lock_timeout at 11.5s",
            }),
            json.dumps({
                "event_kind": "finding",
                "result_id": "find_shell",
                "status": "refuted",
                "title": "Shell context-read inside hook",
                "summary": "spawns cli process under C++ contention api_key=sk-secret-123",
            }),
        ]) + "\n",
        encoding="utf-8",
    )

    payload = {
        "goal_id": goal_id,
        "selected_todo": {
            "todo_id": "todo_phase2",
            "title": "Replace hook subprocess reads",
            "requirements": "Reach eof=true without subprocesses",
        },
        "work_contract": {"summary": "Must never fake passing tests"},
    }
    journal_dir, compact_event, _ = _seed_session_with_capture(
        tmp_path,
        monkeypatch,
        capture_payload=payload,
        compact_direct_read_policy={"enabled": True, "runtime_root": str(runtime_root)},
    )
    workspace = Path(str(compact_event["cwd"]))
    (workspace / "run_history.json").write_text(
        json.dumps({
            "run_id": "ad-c1",
            "entries": [
                {"round": "agent-1", "status": "completed", "valid": True, "score": 1.0, "pass_rate": 1.0},
                {"round": "agent-2", "status": "error", "valid": False, "score": 0.0,
                 "error": "segfault at /Users/alice/work/cand.cpp"},
                {"round": "agent-3", "status": "completed", "valid": True, "score": 0.5, "pass_rate": 0.6},
            ],
        }),
        encoding="utf-8",
    )

    # Event itself carries ZERO explicit failure_facts or open_obligations!
    reply = context.codex_context_hook(str(journal_dir), compact_event)
    hook_out = reply["hookSpecificOutput"]
    text = hook_out["additionalContext"]
    meta = hook_out["compactDirectRead"]

    assert meta["operator"] == "EpochReset"
    assert meta["failure_facts_count"] == 4
    assert meta["open_obligations_count"] >= 3
    assert set(meta["sources_inspected"]) == {"run_history.json", "explore-result-log.jsonl"}
    assert "agent-2" in text and "agent-3" in text
    assert "node_cxx_oom" in text and "find_shell" in text
    assert "node_resolved" not in text
    assert "todo_phase2" in text and "Must never fake passing tests" in text
    assert "/Users/alice" not in text
    assert "sk-secret-123" not in text
    assert "<redacted-path>" in text
    assert "<redacted-secret>" in text


def test_retires_subprocess_context_read_and_heavy_shell_prefetch_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "goal_id": "goal-fixture",
        "selected_todo": {"todo_id": "todo_x", "title": "Inspect bounded subset"},
        "plan_summary": {"requirements": "Keep original capture intact"},
        "extra_unused_field": "B" * 4000,
    }
    journal_dir, compact_event, _ = _seed_session_with_capture(
        tmp_path,
        monkeypatch,
        capture_payload=payload,
        compact_direct_read_policy={"enabled": True},
    )

    def _forbid_subprocess(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("compact direct-read must never spawn subprocesses")

    monkeypatch.setattr(subprocess, "Popen", _forbid_subprocess)
    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)

    reply = context.codex_context_hook(
        str(journal_dir),
        {
            **compact_event,
            "prefetch_command": [
                "loopx --format json quota context-read --select /selected_todo --select /plan_summary",
                "pytest -q && make all",
                "make all",
            ],
        },
    )
    hook_out = reply["hookSpecificOutput"]
    meta = hook_out["compactDirectRead"]
    text = hook_out["additionalContext"]
    assert meta["operator"] == "EpochReset"
    assert meta["retired_shell_prefetch"] is True
    assert "retired_subprocess:loopx_quota_context_read" in meta["retired_shell_commands"]
    assert "retired_shell_chain:pytest" in meta["retired_shell_commands"]
    assert "retired_heavy_external_shell:make" in meta["retired_shell_commands"]
    assert "todo_x" in text
    assert "extra_unused_field" not in text


def test_corrupt_or_missing_capture_fails_closed_to_keep_raw_preserving_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "goal_id": "goal-fixture",
        "selected_todo": {"todo_id": "todo_keep", "title": "Fail closed cleanly"},
    }
    journal_dir, compact_event, _ = _seed_session_with_capture(
        tmp_path,
        monkeypatch,
        capture_payload=payload,
        compact_direct_read_policy={"enabled": True},
    )
    Path(str(compact_event["cwd"])).joinpath("run_history.json").write_text(
        json.dumps({"entries": [{"round": "r1", "status": "timed_out", "valid": False}]}),
        encoding="utf-8",
    )
    # Tamper capture on disk -> PostCompact stays inert ({}) and SessionStart(compact)
    # fails closed to KeepRaw without setting compact_hook_wake (so retries still guide recovery).
    (tmp_path / "decision.json").write_text('{"tampered": true}', encoding="utf-8")
    assert context.codex_context_hook(
        str(journal_dir), {**compact_event, "hook_event_name": "PostCompact"},
    ) == {}

    reply = context.codex_context_hook(
        str(journal_dir),
        {**compact_event, "open_obligations": ["Preserve failure r1"]},
    )
    hook_out = reply["hookSpecificOutput"]
    meta = hook_out["compactDirectRead"]
    assert meta["operator"] == "KeepRaw"
    assert meta["sha256_verified"] is False
    assert meta["eof"] is False
    assert meta["fallback_reason"] == "capture_verification_failed"
    assert "operator=KeepRaw" in hook_out["additionalContext"]
    assert "r1" in hook_out["additionalContext"]
    assert "Preserve failure r1" in hook_out["additionalContext"]
    assert "compact_hook_wake" not in context._state(journal_dir)

    retry_reply = context.codex_context_hook(
        str(journal_dir),
        {**compact_event, "open_obligations": ["Preserve failure r1"]},
    )
    retry_out = retry_reply["hookSpecificOutput"]
    assert retry_out["compactDirectRead"]["deduplicated"] is False
    assert "operator=KeepRaw" in retry_out["additionalContext"]


def test_default_off_parity_and_zero_post_tool_friction_and_cli_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "goal_id": "goal-fixture",
        "selected_todo": {"todo_id": "todo_off", "title": "Default off parity"},
    }
    journal_dir, compact_event, digest = _seed_session_with_capture(
        tmp_path, monkeypatch, capture_payload=payload, compact_direct_read_policy=None,
    )
    monkeypatch.delenv("LOOPX_CODEX_HOOK_DIRECT_READ", raising=False)

    # Default-off configuration omits --compact-direct-read cleanly.
    cfg_default = context.codex_context_configuration(
        str(journal_dir),
        goal_id="goal-fixture",
        agent_id="agent-1",
        cli_argv=["loopx"],
        scenario="codex_benchmark_heartbeat",
    )
    assert "PreToolUse" not in cfg_default["hooks"]
    assert "--compact-direct-read" not in cfg_default["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    cfg_opt_in = context.codex_context_configuration(
        str(journal_dir),
        goal_id="goal-fixture",
        agent_id="agent-1",
        cli_argv=["loopx"],
        scenario="codex_benchmark_heartbeat",
        compact_direct_read=True,
    )
    assert "PreToolUse" not in cfg_opt_in["hooks"]
    assert "--compact-direct-read" in cfg_opt_in["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "--compact-direct-read" in cfg_opt_in["hooks"]["PostCompact"][0]["hooks"][0]["command"]

    # Default-off PostCompact stays inert ({}).
    assert context.codex_context_hook(
        str(journal_dir), {**compact_event, "hook_event_name": "PostCompact"},
    ) == {}
    assert "compactDirectRead" not in str(context._state(journal_dir))

    # PostToolUse ordinary tool output never triggers direct-read or fact extraction even when opted in.
    assert context.codex_context_hook(
        str(journal_dir),
        {**compact_event, "hook_event_name": "PostToolUse", "compact_direct_read": True, "tool_response": "ok"},
    ) == {}

    # Real CLI subprocess end-to-end invocation with --compact-direct-read!
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    completed = subprocess.run(
        [
            sys.executable, "-m", "loopx.entrypoint",
            "--registry", str(registry),
            "--runtime-root", str(runtime),
            "--format", "json",
            "quota", "context-hook",
            "--context-delivery", "on",
            "--codex-app",
            "--compact-direct-read",
            "--codex-context-dir", str(journal_dir),
        ],
        input=json.dumps(compact_event),
        capture_output=True,
        text=True,
        timeout=20,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == 0, completed.stderr
    cli_out = json.loads(completed.stdout)["hookSpecificOutput"]
    assert cli_out["compactDirectRead"]["operator"] == "EpochReset"
    assert cli_out["compactDirectRead"]["source_sha256"] == digest
    assert "todo_off" in cli_out["additionalContext"]
    assert not registry.exists() and not runtime.exists()


def test_quota_context_config_and_hook_opt_ins_quiet_turn_and_epoch_prefetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify --quiet-turn & --epoch-prefetch-json stay off by default and activate cleanly."""
    import os
    from loopx.cli import main

    journal_dir = tmp_path / "ctx"
    journal_dir.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")

    def _run_cli(argv: list[str]) -> tuple[int, dict]:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--format", "json", "--registry", str(tmp_path / "r.json"), "--runtime-root", str(tmp_path / "rt"), *argv])
        return code, json.loads(buf.getvalue())

    c_default, p_default = _run_cli([
        "quota", "context-config",
        "--goal-id", "g1", "--agent-id", "a1",
        "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(journal_dir),
    ])
    assert c_default == 0 and p_default["ok"] is True, p_default
    default_cmd = p_default["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "--compact-direct-read" not in default_cmd
    assert "--quiet-turn" not in default_cmd
    assert "--epoch-prefetch-json" not in default_cmd

    prefetch_cfg = {"enabled": True, "prefetch_command": ["git", "status", "--short"]}
    c_opt, p_opt = _run_cli([
        "quota", "context-config",
        "--goal-id", "g1", "--agent-id", "a1",
        "--context-delivery", "on", "--codex-app",
        "--compact-direct-read", "--quiet-turn",
        "--epoch-prefetch-json", json.dumps(prefetch_cfg),
        "--codex-context-dir", str(journal_dir),
    ])
    assert c_opt == 0 and p_opt["ok"] is True, p_opt
    opt_cmd = p_opt["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "--compact-direct-read" in opt_cmd
    assert "--quiet-turn" in opt_cmd
    assert "--epoch-prefetch-json" in opt_cmd

    # Reject context-hook flags on unrelated quota subcommands cleanly.
    c_bad, p_bad = _run_cli(["quota", "should-run", "--goal-id", "g1", "--quiet-turn"])
    assert c_bad == 1 and "context-config or context-hook" in p_bad["reason"]
    c_bad2, p_bad2 = _run_cli(["quota", "should-run", "--goal-id", "g1", "--epoch-prefetch-json", '{"enabled":true}'])
    assert c_bad2 == 1 and "context-config or context-hook" in p_bad2["reason"]

    # Real CLI subprocess quota context-hook with --quiet-turn on unchanged digest.
    capture = tmp_path / "capture.json"
    capture.write_bytes(b'{"goal_id":"g1","selected_todo":{"todo_id":"todo_1","status":"open"}}')
    digest = hashlib.sha256(capture.read_bytes()).hexdigest()
    from loopx.control_plane.turn_driver import codex_context as ctx
    ctx._save(journal_dir, {
        "session_id": "s-qt",
        "cwd": str(tmp_path),
        "context_epoch": "epoch-1",
        "candidate": {"epoch": "epoch-1"},
        "recovery": {"path": str(capture), "sha256": digest, "command": "loopx quota context-read"},
    })
    worktree_root = str(Path(__file__).resolve().parents[2])
    env_clean = {k: v for k, v in os.environ.items() if k != "LOOPX_CODEX_HOOK_DIRECT_READ"}
    env_clean["HOME"] = str(tmp_path)
    env_clean["PYTHONPATH"] = worktree_root
    hook_cmd = [
        sys.executable, "-m", "loopx.entrypoint",
        "--registry", str(tmp_path / "absent.json"),
        "--runtime-root", str(tmp_path / "absent-rt"),
        "--format", "json", "quota", "context-hook",
        "--context-delivery", "on", "--codex-app", "--quiet-turn",
        "--codex-context-dir", str(journal_dir),
    ]
    evt_hook = {"session_id": "s-qt", "cwd": str(tmp_path), "hook_event_name": "PostCompact"}
    subprocess.run(hook_cmd, input=json.dumps(evt_hook), capture_output=True, text=True, env=env_clean, check=True)
    r2 = subprocess.run(hook_cmd, input=json.dumps(evt_hook), capture_output=True, text=True, env=env_clean, check=True)
    second_out = json.loads(r2.stdout)
    assert second_out.get("hookSpecificOutput", {}).get("quietTurn", {}).get("skipped") is True

    # Real CLI subprocess quota context-hook with --epoch-prefetch-json on compact recovery.
    prefetch_repo = tmp_path / "prefetch-repo"
    prefetch_repo.mkdir()
    subprocess.run(["git", "init"], cwd=prefetch_repo, capture_output=True, check=True)
    large_capture = tmp_path / "large-capture.json"
    large_capture.write_text(json.dumps({
        "goal_id": "g1",
        "selected_todo": {"todo_id": "todo_1", "status": "open"},
        "padding": "x" * 2048,
    }))
    large_digest = hashlib.sha256(large_capture.read_bytes()).hexdigest()
    prefetch_dir = tmp_path / "ctx-prefetch"
    prefetch_dir.mkdir(mode=0o700)
    ctx._save(prefetch_dir, {
        "session_id": "s-ep",
        "cwd": str(prefetch_repo),
        "context_epoch": "epoch-9",
        "recovery": {"path": str(large_capture), "sha256": large_digest, "command": "loopx quota context-read"},
    })
    ep_cfg = {
        "enabled": True,
        "prefetch_command": ["git", "status", "--short"],
        "baseline_bytes": large_capture.stat().st_size,
        "min_net_savings_bytes": 1,
    }
    ep_cmd = [
        sys.executable, "-m", "loopx.entrypoint",
        "--registry", str(tmp_path / "absent2.json"),
        "--runtime-root", str(tmp_path / "absent2-rt"),
        "--format", "json", "quota", "context-hook",
        "--context-delivery", "on", "--codex-app",
        "--epoch-prefetch-json", json.dumps(ep_cfg),
        "--codex-context-dir", str(prefetch_dir),
    ]
    ep_evt = {"session_id": "s-ep", "cwd": str(prefetch_repo), "hook_event_name": "SessionStart", "source": "compact"}
    r_ep = subprocess.run(
        ep_cmd, input=json.dumps(ep_evt), capture_output=True, text=True,
        env=env_clean, cwd=prefetch_repo, check=True,
    )
    ep_out = json.loads(r_ep.stdout)["hookSpecificOutput"]
    assert ep_out["epochPrefetch"]["operator"] == "EpochReset"
    assert ep_out["epochPrefetch"]["bytes_saved"] > 0


def test_combined_compact_direct_read_and_epoch_prefetch_generated_hook_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated combined H1 hook executes both direct read and EpochReset prefetch, never silently dropping either."""
    import os
    import shlex
    from loopx.cli import main
    from loopx.control_plane.turn_driver import codex_context as ctx

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    capture = tmp_path / "combo-capture.json"
    capture.write_text(json.dumps({
        "goal_id": "g-combo",
        "selected_todo": {"todo_id": "todo_combo", "title": "Preserve direct read and prefetch"},
        "padding": "y" * 4096,
    }), encoding="utf-8")
    capture.chmod(0o600)
    digest = hashlib.sha256(capture.read_bytes()).hexdigest()

    journal_dir = tmp_path / "ctx-combo"
    journal_dir.mkdir(mode=0o700)
    ctx._save(journal_dir, {
        "session_id": "s-combo",
        "cwd": str(repo),
        "context_epoch": "epoch-combo-1",
        "goal_id": "g-combo",
        "agent_id": "a1",
        "recovery": {"path": str(capture), "sha256": digest, "command": f"loopx quota context-read --capture-file {capture}"},
    })

    ep_cfg = {
        "enabled": True,
        "prefetch_command": ["git", "status", "--short"],
        "baseline_bytes": capture.stat().st_size,
        "min_net_savings_bytes": 1,
    }
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main([
            "--format", "json",
            "--registry", str(tmp_path / "r.json"),
            "--runtime-root", str(tmp_path / "rt"),
            "quota", "context-config",
            "--goal-id", "g-combo", "--agent-id", "a1",
            "--context-delivery", "on", "--codex-app",
            "--compact-direct-read", "--quiet-turn",
            "--epoch-prefetch-json", json.dumps(ep_cfg),
            "--codex-context-dir", str(journal_dir),
        ])
    assert rc == 0
    hooks = json.loads(buf.getvalue())["native_config"]["hooks"]
    cmd_tokens = shlex.split(hooks["SessionStart"][0]["hooks"][0]["command"])
    worktree_root = str(Path(__file__).resolve().parents[2])
    hook_cmd = [
        sys.executable, "-m", "loopx.entrypoint",
        "--registry", str(tmp_path / "absent-combo.json"),
        "--runtime-root", str(tmp_path / "absent-combo-rt"),
        *cmd_tokens[cmd_tokens.index("--format"):],
    ]
    env_clean = {k: v for k, v in os.environ.items() if k != "LOOPX_CODEX_HOOK_DIRECT_READ"}
    env_clean["HOME"] = str(tmp_path)
    env_clean["PYTHONPATH"] = worktree_root

    # 1. PostCompact stays inert ({}) without setting compact_hook_wake or running prefetch.
    r_post = subprocess.run(
        hook_cmd,
        input=json.dumps({"session_id": "s-combo", "cwd": str(repo), "hook_event_name": "PostCompact"}),
        capture_output=True, text=True, env=env_clean, cwd=repo, check=True,
    )
    assert json.loads(r_post.stdout) == {}
    assert "compact_hook_wake" not in ctx._state(journal_dir)

    # 2. First SessionStart(compact) runs BOTH: non-deduped compactDirectRead AND real EpochReset prefetch!
    start_evt = {"session_id": "s-combo", "cwd": str(repo), "hook_event_name": "SessionStart", "source": "compact"}
    r_start = subprocess.run(
        hook_cmd, input=json.dumps(start_evt), capture_output=True, text=True,
        env=env_clean, cwd=repo, check=True,
    )
    start_out = json.loads(r_start.stdout)["hookSpecificOutput"]
    assert start_out["compactDirectRead"]["operator"] == "EpochReset"
    assert start_out["compactDirectRead"]["deduplicated"] is False
    assert start_out["compactDirectRead"]["composed_with_epoch_prefetch"] is True
    assert start_out["epochPrefetch"]["configured"] is True
    assert start_out["epochPrefetch"]["superseded"] is False
    assert start_out["epochPrefetch"]["composed_with_compact_direct_read"] is True
    assert start_out["epochPrefetch"]["operator"] == "EpochReset"
    assert start_out["epochPrefetch"]["executed"] is True
    assert start_out["epochPrefetch"]["replayed"] is False
    assert start_out["epochPrefetch"]["bytes_saved"] > 0
    assert "todo_combo" in start_out["additionalContext"]
    assert "dirty.txt" in start_out["additionalContext"]

    # 3. Replay on same epoch deduplicates compactDirectRead and NEVER re-executes prefetch_command.
    r_replay = subprocess.run(
        hook_cmd, input=json.dumps(start_evt), capture_output=True, text=True,
        env=env_clean, cwd=repo, check=True,
    )
    replay_out = json.loads(r_replay.stdout)["hookSpecificOutput"]
    assert replay_out["compactDirectRead"]["deduplicated"] is True
    assert replay_out["epochPrefetch"]["replayed"] is True
    assert replay_out["epochPrefetch"]["executed"] is False
    assert replay_out["epochPrefetch"]["superseded"] is False

    # 3b. Direct SessionStart(compact) without prior PostCompact includes BOTH pointer payload and EpochReset output.
    ctx._save(journal_dir, {
        "session_id": "s-combo",
        "cwd": str(repo),
        "context_epoch": "epoch-combo-direct-start",
        "goal_id": "g-combo",
        "agent_id": "a1",
        "recovery": {"path": str(capture), "sha256": digest, "command": f"loopx quota context-read --capture-file {capture}"},
    })
    r_direct_start = subprocess.run(
        hook_cmd, input=json.dumps(start_evt), capture_output=True, text=True,
        env=env_clean, cwd=repo, check=True,
    )
    direct_start_out = json.loads(r_direct_start.stdout)["hookSpecificOutput"]
    assert direct_start_out["compactDirectRead"]["operator"] == "EpochReset"
    assert direct_start_out["compactDirectRead"]["deduplicated"] is False
    assert direct_start_out["epochPrefetch"]["operator"] == "EpochReset"
    assert direct_start_out["epochPrefetch"]["executed"] is True
    assert direct_start_out["epochPrefetch"]["superseded"] is False
    assert "todo_combo" in direct_start_out["additionalContext"]
    assert "dirty.txt" in direct_start_out["additionalContext"]

    # 4. Failing epoch prefetch falls back cleanly to KeepRaw while preserving direct-read output.
    ctx._save(journal_dir, {
        "session_id": "s-combo",
        "cwd": str(repo),
        "context_epoch": "epoch-combo-fail",
        "goal_id": "g-combo",
        "agent_id": "a1",
        "recovery": {"path": str(capture), "sha256": digest, "command": f"loopx quota context-read --capture-file {capture}"},
    })
    fail_ep = {"enabled": True, "prefetch_command": ["cat", str(repo / "missing-file.txt")], "baseline_bytes": 4096}
    fail_cmd = list(hook_cmd)
    fail_cmd[fail_cmd.index("--epoch-prefetch-json") + 1] = json.dumps(fail_ep)
    r_fail = subprocess.run(
        fail_cmd, input=json.dumps(start_evt), capture_output=True, text=True,
        env=env_clean, cwd=repo, check=True,
    )
    fail_out = json.loads(r_fail.stdout)["hookSpecificOutput"]
    assert fail_out["compactDirectRead"]["operator"] == "EpochReset"
    assert fail_out["epochPrefetch"]["configured"] is True
    assert fail_out["epochPrefetch"]["superseded"] is False
    assert fail_out["epochPrefetch"]["operator"] == "KeepRaw"
    assert "todo_combo" in fail_out["additionalContext"]
    assert "Prefetch fallback notice (KeepRaw)" in fail_out["additionalContext"]


def test_read_json_pointers_paginated_fails_closed_over_pointer_cap(tmp_path: Path) -> None:
    capture = tmp_path / "capture-overcap.json"
    payload = {f"k{idx}": idx for idx in range(direct_read.MAX_POINTERS_PER_HOOK)}
    digest, _ = _write_private_json(capture, payload)

    # 24 valid pointers + 25th nonexistent pointer MUST fail closed rather than silently
    # truncating to 24 and reporting ok=True, eof=True.
    requested = [f"/k{idx}" for idx in range(direct_read.MAX_POINTERS_PER_HOOK)] + ["/nonexistent_25th"]
    ok, result, decoded = direct_read.read_json_pointers_paginated(
        capture,
        digest,
        pointers=requested,
        require_all_pointers=True,
    )
    assert ok is False
    assert decoded is None
    assert result["ok"] is False
    assert result["eof"] is False
    assert result["reason"] == "too_many_json_pointers"
    assert result["requested_count"] == direct_read.MAX_POINTERS_PER_HOOK + 1


def test_epoch_rotation_passes_effective_state_and_commits_prefetch_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 1: Rotated context_epoch on updated state (not stale pre-lifecycle state) governs prefetch key and replay."""
    from loopx.control_plane.turn_driver import codex_context as ctx
    from loopx.control_plane.turn_driver import epoch_prefetch as ep_mod

    repo = tmp_path / "repo-rot"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    (repo / "base.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "changed.txt").write_text("dirty\n", encoding="utf-8")

    cap = tmp_path / "rot-cap.json"
    cap.write_text(json.dumps({"goal_id": "g-rot", "selected_todo": {"todo_id": "t-rot"}, "pad": "x" * 4096}), encoding="utf-8")
    cap.chmod(0o600)
    dig = hashlib.sha256(cap.read_bytes()).hexdigest()

    journal_dir = tmp_path / "ctx-rot"
    journal_dir.mkdir(mode=0o700)
    ctx._save(journal_dir, {
        "session_id": "s-rot",
        "cwd": str(repo),
        "context_epoch": "epoch-1",
        "goal_id": "g-rot",
        "agent_id": "a-rot",
        "recovery": {"path": str(cap), "sha256": dig, "command": f"loopx quota context-read --capture-file {cap}"},
    })

    # Rotate epoch inside _reduce_hook_lifecycle on call 1 (simulating same-turn lifecycle epoch rotation).
    real_reduce = ctx._reduce_hook_lifecycle
    rotations = {"n": 0}

    def rotating_reduce(*args, **kwargs):
        res = real_reduce(*args, **kwargs)
        rotations["n"] += 1
        if rotations["n"] == 1:
            res = {**res, "context_epoch": "epoch-2", "goal_id": "g-rot-updated"}
        return res

    monkeypatch.setattr(ctx, "_reduce_hook_lifecycle", rotating_reduce)

    exec_calls = {"count": 0}
    real_subproc = ep_mod.run_bounded_prefetch_subprocess

    def counting_subproc(*args, **kwargs):
        exec_calls["count"] += 1
        return real_subproc(*args, **kwargs)

    monkeypatch.setattr(ep_mod, "run_bounded_prefetch_subprocess", counting_subproc)

    evt = {
        "session_id": "s-rot",
        "cwd": str(repo),
        "hook_event_name": "SessionStart",
        "source": "compact",
        "epoch_prefetch": {
            "enabled": True,
            "prefetch_command": ["git", "status", "--short"],
            "baseline_bytes": cap.stat().st_size,
            "min_net_savings_bytes": 1,
        },
    }
    first = ctx.codex_context_hook(str(journal_dir), evt)
    ep1 = first["hookSpecificOutput"]["epochPrefetch"]
    assert ep1["operator"] == "EpochReset"
    assert ep1["context_epoch"] == "epoch-2"
    assert ep1["executed"] is True
    assert ep1["replayed"] is False
    assert exec_calls["count"] == 1

    persisted = ctx._state(journal_dir)
    assert persisted["context_epoch"] == "epoch-2"
    assert persisted["epoch_prefetch"]["context_epoch"] == "epoch-2"

    # Identical retry MUST replay the committed epoch-2 receipt rather than re-executing prefetch_command.
    second = ctx.codex_context_hook(str(journal_dir), evt)
    ep2 = second["hookSpecificOutput"]["epochPrefetch"]
    assert ep2["operator"] == "EpochReset"
    assert ep2["context_epoch"] == "epoch-2"
    assert ep2["executed"] is False
    assert ep2["replayed"] is True
    assert exec_calls["count"] == 1

    # Even if a direct caller passes stale state["context_epoch"], resolve_codex_context_epoch_prefetch_hook
    # reads locked disk current_state["context_epoch"] ("epoch-2") under the file lock.
    direct_res = ep_mod.resolve_codex_context_epoch_prefetch_hook(
        directory=str(journal_dir),
        hook_name="SessionStart",
        state={"context_epoch": "epoch-1", "goal_id": "stale-g", "agent_id": "a-rot"},
        event=evt,
        recovery={"path": str(cap), "sha256": dig, "command": f"loopx quota context-read --capture-file {cap}"},
        lock_ctx=ctx._locked,
        read_state_fn=ctx._state,
        save_state_fn=ctx._save,
        continuation_text="read capture",
        raw_guidance_text="guidance",
    )
    assert direct_res is not None
    direct_ep = direct_res["hookSpecificOutput"]["epochPrefetch"]
    assert direct_ep["context_epoch"] == "epoch-2"
    assert direct_ep["replayed"] is True
    assert direct_ep["executed"] is False
    assert exec_calls["count"] == 1


def test_combined_hook_enforces_delivered_utf8_byte_budget_and_four_arm_comparison(
    tmp_path: Path,
) -> None:
    """Fix 2: Composition enforces combined delivered UTF-8 byte budget + compares all 4 H1 arms."""
    from loopx.control_plane.turn_driver import codex_context as ctx

    repo = tmp_path / "repo-budget"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    (repo / "base.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    # 1. Small capture (cap_bytes ~ 387): combined hook MUST fail closed epochPrefetch instead of inflating
    #    additionalContext by 9x while falsely claiming EpochReset + positive bytes_saved.
    small_cap = tmp_path / "small-cap.json"
    small_cap.write_text(json.dumps({
        "goal_id": "g-small",
        "selected_todo": {"todo_id": "t-small", "title": "Small capture"},
        "padding": "p" * 290,
    }), encoding="utf-8")
    small_cap.chmod(0o600)
    small_bytes = small_cap.stat().st_size
    assert 350 <= small_bytes <= 430, small_bytes
    small_dig = hashlib.sha256(small_cap.read_bytes()).hexdigest()

    def _run_small(mode_name: str, extra_evt: dict) -> dict:
        d = tmp_path / f"ctx-small-{mode_name}"
        d.mkdir(mode=0o700)
        ctx._save(d, {
            "session_id": f"s-{mode_name}",
            "cwd": str(repo),
            "context_epoch": "epoch-small-1",
            "goal_id": "g-small",
            "agent_id": "a1",
            "recovery": {"path": str(small_cap), "sha256": small_dig, "command": f"loopx quota context-read --capture-file {small_cap}"},
        })
        return ctx.codex_context_hook(str(d), {
            "session_id": f"s-{mode_name}",
            "cwd": str(repo),
            "hook_event_name": "SessionStart",
            "source": "compact",
            **extra_evt,
        })["hookSpecificOutput"]

    small_direct_only = _run_small("direct", {"compact_direct_read": {"enabled": True}})
    small_combo = _run_small("combo", {
        "compact_direct_read": {"enabled": True},
        "epoch_prefetch": {
            "enabled": True,
            "prefetch_command": ["git", "status", "--short"],
            "baseline_bytes": small_bytes,
            "min_net_savings_bytes": 1,
        },
    })
    assert small_combo["epochPrefetch"]["operator"] == "KeepRaw"
    assert small_combo["epochPrefetch"]["fallback_reason"] == "composed_budget_exceeded"
    assert small_combo["epochPrefetch"]["superseded"] is True
    assert small_combo["epochPrefetch"]["bytes_saved"] == 0
    assert small_combo["epochPrefetch"]["round_trips_saved"] == 0
    assert len(small_combo["additionalContext"].encode("utf-8")) == len(
        small_direct_only["additionalContext"].encode("utf-8")
    )

    # 2. Explicit tight max_output_bytes on a large capture bounds total delivered UTF-8 bytes strictly <= budget.
    big_cap = tmp_path / "big-cap.json"
    big_cap.write_text(json.dumps({
        "goal_id": "g-big",
        "selected_todo": {"todo_id": "t-big", "title": "Realistic large capture"},
        "padding": "z" * 8192,
    }), encoding="utf-8")
    big_cap.chmod(0o600)
    big_bytes = big_cap.stat().st_size
    big_dig = hashlib.sha256(big_cap.read_bytes()).hexdigest()

    tight_dir = tmp_path / "ctx-tight"
    tight_dir.mkdir(mode=0o700)
    ctx._save(tight_dir, {
        "session_id": "s-tight",
        "cwd": str(repo),
        "context_epoch": "epoch-tight-1",
        "goal_id": "g-big",
        "agent_id": "a1",
        "recovery": {"path": str(big_cap), "sha256": big_dig, "command": f"loopx quota context-read --capture-file {big_cap}"},
    })
    tight_cap_bytes = 1850
    tight_out = ctx.codex_context_hook(str(tight_dir), {
        "session_id": "s-tight",
        "cwd": str(repo),
        "hook_event_name": "SessionStart",
        "source": "compact",
        "compact_direct_read": {"enabled": True, "max_output_bytes": tight_cap_bytes},
        "epoch_prefetch": {
            "enabled": True,
            "prefetch_command": ["git", "status", "--short"],
            "baseline_bytes": big_bytes,
            "min_net_savings_bytes": 1,
        },
    })["hookSpecificOutput"]
    assert len(tight_out["additionalContext"].encode("utf-8")) <= tight_cap_bytes
    assert tight_out["epochPrefetch"]["operator"] == "KeepRaw"
    assert tight_out["epochPrefetch"]["fallback_reason"] == "composed_budget_exceeded"
    assert tight_out["epochPrefetch"]["bytes_saved"] == 0

    # 3. 4-arm comparison (off, direct_read, epoch_prefetch, combined) on an 8KB capture.
    def _run_big(arm: str, extra_evt: dict) -> tuple[dict, int]:
        d = tmp_path / f"ctx-arm-{arm}"
        d.mkdir(mode=0o700)
        ctx._save(d, {
            "session_id": f"s-arm-{arm}",
            "cwd": str(repo),
            "context_epoch": f"epoch-arm-{arm}",
            "goal_id": "g-big",
            "agent_id": "a1",
            "recovery": {"path": str(big_cap), "sha256": big_dig, "command": f"loopx quota context-read --capture-file {big_cap}"},
        })
        out = ctx.codex_context_hook(str(d), {
            "session_id": f"s-arm-{arm}",
            "cwd": str(repo),
            "hook_event_name": "SessionStart",
            "source": "compact",
            **extra_evt,
        })["hookSpecificOutput"]
        return out, len(out["additionalContext"].encode("utf-8"))

    arm_off, bytes_off = _run_big("off", {})
    arm_direct, bytes_direct = _run_big("direct", {"compact_direct_read": {"enabled": True}})
    ep_cfg_big = {
        "enabled": True,
        "prefetch_command": ["git", "status", "--short"],
        "baseline_bytes": big_bytes,
        "min_net_savings_bytes": 1,
    }
    arm_ep, bytes_ep = _run_big("prefetch", {"epoch_prefetch": ep_cfg_big})
    arm_combo, bytes_combo = _run_big("combined", {
        "compact_direct_read": {"enabled": True},
        "epoch_prefetch": ep_cfg_big,
    })

    assert "compactDirectRead" not in arm_off and "epochPrefetch" not in arm_off
    assert arm_direct["compactDirectRead"]["operator"] == "EpochReset"
    assert "epochPrefetch" not in arm_direct
    assert arm_ep["epochPrefetch"]["operator"] == "EpochReset"
    assert arm_ep["epochPrefetch"]["bytes_saved"] <= big_bytes - bytes_ep
    assert arm_combo["compactDirectRead"]["operator"] == "EpochReset"
    assert arm_combo["epochPrefetch"]["operator"] == "EpochReset"
    assert arm_combo["epochPrefetch"]["superseded"] is False
    assert bytes_direct < big_bytes and bytes_ep < big_bytes and bytes_combo < big_bytes
    assert bytes_combo < bytes_direct + bytes_ep
    assert arm_combo["epochPrefetch"]["bytes_saved"] <= big_bytes - bytes_combo
    assert arm_combo["epochPrefetch"]["bytes_saved"] > 0
    assert bytes_off > 0
