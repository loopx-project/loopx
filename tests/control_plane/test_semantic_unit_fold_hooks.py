"""Tests for S1-A Todo semantic-unit anchor, FoldToReceipt, KeepRaw rollback, and codex_context hooks."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from loopx.control_plane.quota.turn_envelope import (
    build_semantic_unit_fold_swap,
    record_semantic_unit_anchor,
)
from loopx.control_plane.turn_driver import codex_context


def _write_source_file(path: Path, text: str) -> dict[str, str]:
    import hashlib

    path.write_text(text, encoding="utf-8")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_turn_envelope_semantic_unit_anchor_and_fold_contract(tmp_path: Path) -> None:
    prefix_digest = "a" * 64
    source_ref = _write_source_file(tmp_path / "todo-capture.json", '{"todo":"ok"}\n')
    anchor = record_semantic_unit_anchor(
        todo_id="todo_alpha",
        turn_instance_id="turn_alpha",
        anchor_step_index=3,
        anchor_surface_seq=8,
        prefix_digest=prefix_digest,
        context_epoch="epoch-alpha",
    )
    assert anchor["schema_version"] == "loopx_semantic_unit_anchor_v0"
    assert anchor["todo_id"] == "todo_alpha"

    receipt = {
        "todo_id": "todo_alpha",
        "completion_receipt_id": "cqr_alpha",
        "exit_code": 0,
        "sha256": "b" * 64,
        "failure_facts": ["earlier lint failure fixed"],
        "open_obligations": ["confirm caller parity"],
        "source_ref": source_ref,
    }
    committed = build_semantic_unit_fold_swap(
        enabled=True,
        anchor=anchor,
        todo_id="todo_alpha",
        turn_instance_id="turn_alpha",
        context_epoch="epoch-alpha",
        prefix_digest=prefix_digest,
        now_step_index=10,
        now_surface_seq=22,
        completion_receipt=receipt,
        baseline_tokens=1600,
        replacement_tokens=140,
        tokenizer={"name": "tiktoken", "encoding": "o200k_base"},
        obs_next_cmd={"command": ["cat", str(tmp_path / "todo-capture.json")], "exit_code": 0, "output": '{"todo":"ok"}'},
    )
    assert committed["operator"] == "FoldToReceipt"
    assert committed["reclaim_range"] == [3, 10]
    assert committed["preserves_prefix_kv"] is True
    assert committed["amortized_net_savings_tokens"] == 1453

    rolled_back = build_semantic_unit_fold_swap(
        enabled=True,
        anchor=anchor,
        todo_id="todo_alpha",
        turn_instance_id="turn_alpha",
        context_epoch="epoch-alpha",
        prefix_digest=prefix_digest,
        now_step_index=10,
        now_surface_seq=22,
        completion_receipt=None,
        baseline_tokens=1600,
        replacement_tokens=140,
        tokenizer={"name": "tiktoken", "encoding": "o200k_base"},
        failure_facts=["keep raw fact"],
        open_obligations=["keep raw obligation"],
    )
    assert rolled_back["operator"] == "KeepRaw"
    assert rolled_back["fallback_reason"] == "missing_completion_receipt"
    assert rolled_back["preserves_raw_state"] is True
    assert rolled_back["preserved_failure_facts"] == ["keep raw fact"]


@pytest.mark.parametrize("hook_name", ["PostToolUse", "PreCompact", "PostCompact", "SessionStart"])
def test_codex_context_hook_semantic_unit_fold_emits_consumes_and_replays_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hook_name: str,
) -> None:
    ctx_dir = tmp_path / f"codex-{hook_name}"
    ctx_dir.mkdir(mode=0o700)
    # Ordinary PostToolUse without semantic-unit payloads stays a zero-overhead no-op.
    assert codex_context.codex_context_hook(
        str(ctx_dir),
        {"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "PostToolUse", "tool_name": "bash"},
    ) == {}

    codex_context._save(
        ctx_dir,
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "context_epoch": "epoch-hook-unit",
        },
    )
    prefix_digest = "e" * 64
    source_ref = _write_source_file(tmp_path / "unit-result.json", '{"status":"done"}\n')
    obs_target = tmp_path / "next-cmd.txt"
    obs_target.write_text("NEXT_OBS=ready\n", encoding="utf-8")

    # Anchor on PostToolUse boundary.
    anchor_res = codex_context.codex_context_hook(
        str(ctx_dir),
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "hook_event_name": "PostToolUse",
            "semantic_unit_anchor": {
                "todo_id": "todo_hook_1",
                "turn_instance_id": "turn_hook_1",
                "anchor_step_index": 2,
                "anchor_surface_seq": 5,
                "prefix_digest": prefix_digest,
            },
        },
    )
    assert anchor_res["hookSpecificOutput"]["semanticUnitAnchor"]["todo_id"] == "todo_hook_1"

    from loopx.control_plane.turn_driver import semantic_unit_hook as hook_mod

    exec_calls: list[list[str]] = []
    orig_exec = hook_mod.run_bounded_prefetch_subprocess

    def spy_exec(argv: list[str], *, timeout_ms: int, max_output_bytes: int, cwd: str | None = None) -> Mapping[str, object]:
        exec_calls.append(list(argv))
        return orig_exec(argv, timeout_ms=timeout_ms, max_output_bytes=max_output_bytes, cwd=cwd)

    monkeypatch.setattr(hook_mod, "run_bounded_prefetch_subprocess", spy_exec)

    fold_event = {
        "session_id": "s1",
        "cwd": str(tmp_path),
        "hook_event_name": hook_name,
        **({"source": "compact"} if hook_name == "SessionStart" else {}),
        "semantic_unit_fold": {
            "enabled": True,
            "todo_id": "todo_hook_1",
            "turn_instance_id": "turn_hook_1",
            "prefix_digest": prefix_digest,
            "now_step_index": 9,
            "now_surface_seq": 19,
            "baseline_text": "exploratory turn step data " * 600,
            "completion_receipt": {
                "todo_id": "todo_hook_1",
                "completion_receipt_id": "cqr_hook_1",
                "exit_code": 0,
                "sha256": "f" * 64,
                "failure_facts": ["no silent fallback"],
                "open_obligations": ["run final canary"],
                "source_ref": source_ref,
            },
            "obs_next_cmd": {"command": ["cat", str(obs_target)], "max_output_bytes": 4096},
        },
    }
    res1 = codex_context.codex_context_hook(str(ctx_dir), fold_event)
    fold_out1 = res1["hookSpecificOutput"]["semanticUnitFold"]
    assert fold_out1["operator"] == "FoldToReceipt"
    assert fold_out1["status"] == "committed"
    assert fold_out1["preserves_prefix_kv"] is True
    assert fold_out1["reclaim_range"] == [2, 9]
    assert fold_out1["executed"] is True
    assert fold_out1["replayed"] is False
    assert "NEXT_OBS=ready" in res1["hookSpecificOutput"]["additionalContext"]
    assert len(exec_calls) == 1

    # Duplicate hook delivery MUST replay committed receipt without re-spawning obs_next_cmd.
    res2 = codex_context.codex_context_hook(str(ctx_dir), fold_event)
    fold_out2 = res2["hookSpecificOutput"]["semanticUnitFold"]
    assert fold_out2["operator"] == "FoldToReceipt"
    assert fold_out2["replayed"] is True
    assert fold_out2["executed"] is False
    assert len(exec_calls) == 1


@pytest.mark.parametrize(
    "case_name",
    ["missing_receipt", "non_quiescent", "command_error", "negative_savings", "prefix_mismatch"],
)
def test_codex_context_hook_semantic_unit_fails_closed_keep_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_name: str,
) -> None:
    ctx_dir = tmp_path / f"codex-fail-{case_name}"
    ctx_dir.mkdir(mode=0o700)
    codex_context._save(
        ctx_dir,
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "context_epoch": "epoch-fail",
        },
    )
    prefix_digest = "1" * 64
    source_ref = _write_source_file(tmp_path / "fail-source.json", '{"status":"err"}\n')
    codex_context.codex_context_hook(
        str(ctx_dir),
        {
            "session_id": "s1",
            "cwd": str(tmp_path),
            "hook_event_name": "PostToolUse",
            "semantic_unit_anchor": {
                "todo_id": "todo_fail_1",
                "turn_instance_id": "turn_fail_1",
                "anchor_step_index": 1,
                "anchor_surface_seq": 3,
                "prefix_digest": prefix_digest,
            },
        },
    )

    from loopx.control_plane.turn_driver import semantic_unit_hook as hook_mod

    obs_spawns: list[list[str]] = []
    orig_exec = hook_mod.run_bounded_prefetch_subprocess

    def spy_exec(argv: list[str], *, timeout_ms: int, max_output_bytes: int, cwd: str | None = None) -> Mapping[str, object]:
        obs_spawns.append(list(argv))
        return orig_exec(argv, timeout_ms=timeout_ms, max_output_bytes=max_output_bytes, cwd=cwd)

    monkeypatch.setattr(hook_mod, "run_bounded_prefetch_subprocess", spy_exec)

    receipt: dict[str, object] = {
        "todo_id": "todo_fail_1",
        "completion_receipt_id": "cqr_fail_1",
        "exit_code": 0,
        "sha256": "2" * 64,
        "failure_facts": ["retained failure fact"],
        "open_obligations": ["retained obligation"],
        "source_ref": source_ref,
    }
    baseline_text = "exploratory token bloat " * 600
    obs_cmd: dict[str, object] = {"command": ["cat", str(tmp_path / "fail-source.json")]}
    fold_spec: dict[str, object] = {
        "enabled": True,
        "todo_id": "todo_fail_1",
        "turn_instance_id": "turn_fail_1",
        "prefix_digest": prefix_digest,
        "now_step_index": 5,
        "now_surface_seq": 9,
        "baseline_text": baseline_text,
        "completion_receipt": receipt,
        "obs_next_cmd": obs_cmd,
        "failure_facts": ["retained failure fact"],
        "open_obligations": ["retained obligation"],
    }
    event: dict[str, object] = {
        "session_id": "s1",
        "cwd": str(tmp_path),
        "hook_event_name": "PreCompact",
        "semantic_unit_fold": fold_spec,
    }
    if case_name == "missing_receipt":
        fold_spec["completion_receipt"] = None
    elif case_name == "non_quiescent":
        event["unpaired_tool_calls"] = True
    elif case_name == "command_error":
        obs_cmd["command"] = ["cat", str(tmp_path / "missing-obs-file.txt")]
    elif case_name == "negative_savings":
        fold_spec["baseline_text"] = "short"
    elif case_name == "prefix_mismatch":
        fold_spec["prefix_digest"] = "9" * 64

    res = codex_context.codex_context_hook(str(ctx_dir), event)
    fold_out = res["hookSpecificOutput"]["semanticUnitFold"]
    assert fold_out["operator"] == "KeepRaw"
    assert fold_out["status"] == "fallback_keep_raw"
    assert fold_out["preserves_raw_state"] is True
    assert fold_out["preserves_prefix_kv"] is True
    assert fold_out["reclaim_range"] is None
    assert "retained failure fact" in res["hookSpecificOutput"]["additionalContext"]
    assert "retained obligation" in res["hookSpecificOutput"]["additionalContext"]
    if case_name in {"missing_receipt", "non_quiescent", "prefix_mismatch"}:
        assert obs_spawns == [], f"pre-check must prevent obs_next_cmd execution for {case_name}"
    saved_state = codex_context._state(ctx_dir)
    assert "todo_fail_1" in saved_state["semantic_unit_anchors"]
    assert not saved_state.get("semantic_unit_folds")
