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

    # 2. PostCompact fires next -> performs <5ms lock-free direct-read + emits EpochReset context.
    t0 = time.perf_counter()
    post_reply = context.codex_context_hook(
        str(journal_dir),
        {**compact_start_event, "hook_event_name": "PostCompact"},
    )
    hook_ms = (time.perf_counter() - t0) * 1000.0
    assert hook_ms < 50.0
    hook_out = post_reply["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PostCompact"
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

    # 3. Immediate SessionStart(compact) within same epoch & unchanged digest deduplicates prompt!
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
    # Tamper capture on disk -> SHA-256 check must fail closed to KeepRaw!
    (tmp_path / "decision.json").write_text('{"tampered": true}', encoding="utf-8")

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
