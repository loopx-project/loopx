"""Tests for S1-A Todo semantic-unit anchor, FoldToReceipt, KeepRaw rollback, and real Turn wiring."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import redirect_stdout
import io
import json
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.quota.turn_envelope import (
    build_semantic_unit_fold_swap,
    record_semantic_unit_anchor,
)
from loopx.control_plane.turn_driver import codex_context
from loopx.control_plane.turn_driver.codex_cli import _prompt as codex_cli_prompt
from loopx.control_plane.turn_driver.epoch_prefetch import (
    _HOST_WIRE_EXCLUDED_PREFETCH_FIELDS,
)
from loopx.control_plane.turn_driver.executor import (
    run_loopx_turn_once,
    serialize_generic_cli_host_input,
)
from loopx.control_plane.turn_driver.host_candidate import render_dsh_host_prompt
from loopx.control_plane.turn_driver.todo_unit_settlement import (
    _HOST_WIRE_EXCLUDED_FOLD_TEXT_FIELDS,
)
from loopx.rollout_event_log import rollout_event_log_path
from loopx.todos import add_goal_todo, complete_goal_todo
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_driver import _write_live_fixture


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


def _invoke_cli_json(route: list[str], args: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main([*route, "--format", "json", *args])
    return code, json.loads(buf.getvalue())


def _plan_turn(route: list[str], turn_instance_id: str, *, host: str = "codex-cli") -> dict:
    code, payload = _invoke_cli_json(
        route,
        [
            "turn",
            "plan",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            turn_instance_id,
            "--host",
            host,
            "--execution-mode",
            "isolated-headless",
            "--scheduler-owner",
            "outer_controller",
            "--include-transaction-detail",
        ],
    )
    assert code == 0, payload
    return payload


def _completion_callbacks(todo_id: str, observed_requests: list[dict], *, result_kind: str = "validated_completion"):
    def host_runner(req: dict) -> dict:
        observed_requests.append(dict(req))
        is_complete = result_kind == "validated_completion"
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": req["turn_key"],
            "result_kind": result_kind,
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "todo_fold_fixture",
            "recommended_action": "continue next todo" if is_complete else "keep working",
            "next_action": "continue next todo" if is_complete else "keep working",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "primary_goal_outcome" if is_complete else "outcome_progress",
            "vision_unchanged_reason": "Goal acceptance remains unchanged.",
            "summary": f"Turn executed for {todo_id} with {result_kind}.",
            "work_evidence": [f"artifact:{todo_id}"],
            "validation_evidence": [f"check:{todo_id}"],
        }

    def completion_writeback(result: dict, *, effect_ref: str) -> dict:
        return {
            "ok": True,
            "appended": True,
            "effect_ref": effect_ref,
            "completion": {
                "event_id": f"ev-{result['turn_key']}",
                "todo_id": todo_id,
                "continuation": "active_goal",
            },
        }

    def completion_intent(*_a, **_k) -> dict:
        return {"todo_id": todo_id, "continuation": "active_goal"}

    def writeback(result: dict, *, effect_ref: str) -> dict:
        return {"ok": True, "appended": True, "effect_ref": effect_ref, "turn_key": result["turn_key"]}

    def spend(*_a, **_k) -> dict:
        return {"ok": True, "appended": True, "receipt": {"status": "committed"}}

    def scheduler(*_a, **_k) -> dict:
        return {"ok": True, "completed": True, "acknowledged": True}

    def noop(*_a, **_k) -> dict:
        return {"ok": True, "appended": True}

    return {
        "host_runner": host_runner,
        "task_validator": lambda _p, _r: {"status": "passed", "validator_kind": "callback", "summary": "ok"},
        "writeback": writeback,
        "completion_writeback": completion_writeback,
        "completion_intent": completion_intent,
        "terminal_closeout": noop,
        "spend": spend,
        "scheduler": scheduler,
    }


def test_quota_receipt_cli_include_settlement_default_off_and_verified_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path, provider="file")
    monkeypatch.chdir(tmp_path)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    code_sr, payload_sr = _invoke_cli_json(
        route,
        [
            "quota",
            "should-run",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "turn-qr-1",
            "--todo-id",
            "todo_fixture0001",
        ],
    )
    assert code_sr == 0, payload_sr

    # Default `quota receipt` stays untouched (no settlement projection fields).
    c_def, p_def = _invoke_cli_json(
        route,
        ["quota", "receipt", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture", "--turn-instance-id", "turn-qr-1"],
    )
    assert c_def == 0
    assert "settlement_verified" not in p_def
    assert "completion_receipt" not in p_def

    # Before completion + spend commit, `--include-settlement` fails closed.
    c_un, p_un = _invoke_cli_json(
        route,
        [
            "quota",
            "receipt",
            "--include-settlement",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "turn-qr-1",
        ],
    )
    assert c_un == 0
    assert p_un["settlement_verified"] is False
    assert p_un["completion_receipt"] is None

    # After canonical refresh-state settlement + todo completion, `--include-settlement` verifies receipt.
    c_ws, p_ws = _invoke_cli_json(
        route,
        [
            "refresh-state",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--delivery-workspace-path",
            str(project),
            "--todo-id",
            "todo_fixture0001",
            "--turn-instance-id",
            "turn-qr-1",
            "--settle-turn",
            "--classification",
            "validated_progress",
            "--recommended-action",
            "continue_goal",
            "--delivery-batch-scale",
            "single_surface",
            "--delivery-outcome",
            "outcome_progress",
            "--vision-unchanged-reason",
            "same",
            "--no-global-sync",
            "--suppress-external-sinks",
        ],
    )
    assert c_ws == 0, p_ws
    assert p_ws["ok"] is True

    c_done, p_done = _invoke_cli_json(
        route,
        [
            "todo",
            "complete",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--todo-id",
            "todo_fixture0001",
            "--turn-instance-id",
            "turn-qr-1",
            "--claimed-by",
            "codex-fixture",
            "--evidence",
            "Completed fixture todo.",
            "--no-follow-up",
        ],
    )
    assert c_done == 0, p_done
    assert p_done["ok"] is True

    log_path = rollout_event_log_path(runtime, "loopx-turn-fixture")
    c_ok, p_ok = _invoke_cli_json(
        route,
        [
            "quota",
            "receipt",
            "--include-settlement",
            "--goal-id",
            "loopx-turn-fixture",
            "--agent-id",
            "codex-fixture",
            "--turn-instance-id",
            "turn-qr-1",
        ],
    )
    assert c_ok == 0, p_ok
    assert p_ok["settlement_verified"] is True, p_ok
    assert p_ok["completion_receipt"]["todo_id"] == "todo_fixture0001"
    assert p_ok["completion_receipt"]["completion_receipt_id"]
    assert p_ok["completion_receipt"]["exit_code"] == 0
    assert p_ok["completion_receipt"]["source_ref"]["path"] == str(log_path)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_run_loopx_turn_once_multi_todo_fold_and_next_turn_prompt_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    next_obs = tmp_path / "next-todo-obs.txt"
    next_obs.write_text("NEXT_TODO_PREFETCH_OK=true\n", encoding="utf-8")

    from loopx.control_plane.turn_driver import semantic_unit_hook as hook_mod

    obs_calls: list[list[str]] = []
    orig_exec = hook_mod.run_bounded_prefetch_subprocess

    def spy_exec(argv: list[str], *, timeout_ms: int, max_output_bytes: int, cwd: str | None = None) -> Mapping[str, object]:
        obs_calls.append(list(argv))
        return orig_exec(argv, timeout_ms=timeout_ms, max_output_bytes=max_output_bytes, cwd=cwd)

    monkeypatch.setattr(hook_mod, "run_bounded_prefetch_subprocess", spy_exec)

    shared_parent_prefix = {"goal_policy": "preserve parent KV prefix Q verbatim", "repo": "loopx"}
    bloat_suffix = "verbose exploratory grep output for todo_fixture0001 " * 260
    turn1_exploratory = [
        {"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix},
    ]

    # Turn 1 executes Todo 1 (`todo_fixture0001`) and settles `validated_completion`.
    plan1 = _plan_turn(route, "turn-fold-1")
    seen1: list[dict] = []
    cb1 = _completion_callbacks("todo_fixture0001", seen1, result_kind="validated_completion")
    fold_cfg_1 = {
        "enabled": True,
        "todo_id": "todo_fixture0001",
        "context_epoch": "epoch-multi-todo-1",
        "parent_prefix": shared_parent_prefix,
        "exploratory_context": turn1_exploratory,
        "baseline_text": bloat_suffix,
        "obs_next_cmd": {"command": ["cat", str(next_obs)], "max_output_bytes": 4096},
        "failure_facts": ["earlier smoke caught stale assumption"],
        "open_obligations": ["run todo_two verification after swap"],
    }
    res1 = run_loopx_turn_once(
        plan1,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold=fold_cfg_1,
        **cb1,
    )
    assert res1["ok"] is True
    assert res1["status"] == "committed"
    fold1 = res1["semantic_unit_fold"]
    assert fold1["operator"] == "FoldToReceipt"
    assert fold1["status"] == "committed"
    assert fold1["preserves_prefix_kv"] is True
    assert fold1["executed"] is True
    assert len(obs_calls) == 1

    # Add Todo 2 and settle Todo 1 in Core so `turn plan` selects Todo 2.
    added = add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Execute downstream verification todo_two.",
        priority="P1",
    )
    todo2_id = str(added["todo"]["todo_id"])
    complete_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        evidence="Todo 1 verified.",
    )

    plan2 = _plan_turn(route, "turn-fold-2")
    turn2_exploratory = [
        {"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix},
        {"todo_id": todo2_id, "step": 0, "output": "fresh todo2 kickoff note"},
    ]
    seen2: list[dict] = []
    cb2 = _completion_callbacks(todo2_id, seen2, result_kind="validated_progress")
    fold_cfg_2 = {
        "enabled": True,
        "todo_id": todo2_id,
        "context_epoch": "epoch-multi-todo-1",
        "parent_prefix": shared_parent_prefix,
        "exploratory_context": turn2_exploratory,
        "min_net_savings_tokens": 64,
    }
    res2 = run_loopx_turn_once(
        plan2,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold=fold_cfg_2,
        **cb2,
    )
    assert res2["ok"] is True
    assert len(seen2) == 1
    # Replay/Turn-2 preparation MUST NOT re-spawn Todo 1's obs_next_cmd.
    assert len(obs_calls) == 1

    delivered_req2 = seen2[0]
    assert delivered_req2["parent_prefix"] == shared_parent_prefix
    assert delivered_req2["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    assert delivered_req2["semantic_unit_fold"]["preserves_prefix_kv"] is True
    assert delivered_req2["semantic_unit_fold"]["rendered_prompt_delivered_tokens"] < delivered_req2["semantic_unit_fold"]["rendered_prompt_baseline_tokens"]

    # Raw Todo 1 bloat was genuinely replaced; parent prefix, Todo 2 note, receipt mu, and obs_next_cmd are present.
    delivered_exp = delivered_req2["exploratory_context"]
    assert all(not (isinstance(row, Mapping) and row.get("output") == bloat_suffix) for row in delivered_exp)
    assert any(isinstance(row, Mapping) and row.get("todo_id") == todo2_id for row in delivered_exp)
    assert any(isinstance(row, Mapping) and row.get("replaced_by_semantic_unit_fold") is True and row.get("todo_id") == "todo_fixture0001" for row in delivered_exp)

    rendered_prompt2 = codex_cli_prompt(delivered_req2)
    assert "verbose exploratory grep output for todo_fixture0001" not in rendered_prompt2
    assert "[Semantic-Unit Fold Receipt mu | trust=tool]" in rendered_prompt2
    assert "NEXT_TODO_PREFETCH_OK=true" in rendered_prompt2
    assert "preserve parent KV prefix Q verbatim" in rendered_prompt2
    assert "fresh todo2 kickoff note" in rendered_prompt2
    assert "Semantic-Unit Fold Receipt mu" in serialize_generic_cli_host_input(delivered_req2)


@pytest.mark.parametrize(
    "failure_case",
    ["progress_not_settled", "obs_command_error", "prefix_drift", "recovery_conflict", "negative_savings"],
)
def test_run_loopx_turn_once_todo_fold_fails_closed_keep_raw(
    tmp_path: Path, failure_case: str,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path, provider="file")
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    obs_ok = tmp_path / "obs-ok.txt"
    obs_ok.write_text("OBS_OK=1\n", encoding="utf-8")

    parent_prefix = {"goal_policy": "stable Q"}
    raw_bloat = "raw exploratory step record to keep intact " * 220
    turn1_exp = [{"todo_id": "todo_fixture0001", "step": 1, "output": raw_bloat}]

    plan1 = _plan_turn(route, "turn-fail-1")
    seen1: list[dict] = []
    turn1_result_kind = "validated_progress" if failure_case == "progress_not_settled" else "validated_completion"
    cb1 = _completion_callbacks("todo_fixture0001", seen1, result_kind=turn1_result_kind)
    fold_cfg_1: dict[str, object] = {
        "enabled": True,
        "todo_id": "todo_fixture0001",
        "context_epoch": "epoch-fail-turn",
        "parent_prefix": parent_prefix,
        "exploratory_context": turn1_exp,
        "baseline_text": "tiny" if failure_case == "negative_savings" else raw_bloat,
        "obs_next_cmd": {"command": ["cat", str(tmp_path / "missing.txt" if failure_case == "obs_command_error" else obs_ok)]},
        "failure_facts": ["keep raw failure fact"],
        "open_obligations": ["keep raw open obligation"],
    }
    res1 = run_loopx_turn_once(
        plan1,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold=fold_cfg_1,
        **cb1,
    )
    assert res1["ok"] is True

    if failure_case in {"obs_command_error", "negative_savings"}:
        assert res1["semantic_unit_fold"]["operator"] == "KeepRaw"
        assert res1["semantic_unit_fold"]["preserves_raw_state"] is True
    elif failure_case == "progress_not_settled":
        assert "semantic_unit_fold" not in res1

    added = add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Next todo after fail-closed check.",
        priority="P1",
    )
    todo2_id = str(added["todo"]["todo_id"])
    complete_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        evidence="Todo 1 closed before Turn 2.",
    )

    plan2 = _plan_turn(route, "turn-fail-2")
    if failure_case == "recovery_conflict":
        plan2["unsettled_host_turn_recovery"] = {"state": "attention_required"}
    seen2: list[dict] = []
    cb2 = _completion_callbacks(todo2_id, seen2, result_kind="validated_progress")
    res2 = run_loopx_turn_once(
        plan2,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": todo2_id,
            "context_epoch": "epoch-fail-turn",
            "parent_prefix": {"goal_policy": "mutated Q!"} if failure_case == "prefix_drift" else parent_prefix,
            "exploratory_context": turn1_exp,
        },
        **cb2,
    )
    assert res2["ok"] is True
    assert len(seen2) == 1
    delivered2 = seen2[0]
    # Every fail-closed case MUST preserve raw exploratory_context intact!
    assert delivered2["exploratory_context"] == turn1_exp
    if failure_case != "progress_not_settled":
        fold2 = delivered2["semantic_unit_fold"]
        assert fold2["operator"] == "KeepRaw"
        assert fold2["preserves_raw_state"] is True
        assert "keep raw failure fact" in fold2["preserved_failure_facts"]
        assert "keep raw open obligation" in fold2["preserved_open_obligations"]
        assert "KeepRaw" in codex_cli_prompt(delivered2)
    else:
        assert "semantic_unit_fold" not in delivered2


def test_run_loopx_turn_once_todo_fold_default_off_zero_drift(tmp_path: Path) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path, provider="file")
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    plan = _plan_turn(route, "turn-off-1")
    seen: list[dict] = []
    cb = _completion_callbacks("todo_fixture0001", seen, result_kind="validated_completion")
    res = run_loopx_turn_once(
        plan,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        **cb,
    )
    assert res["ok"] is True
    assert "semantic_unit_fold" not in res
    assert "semantic_unit_fold" not in seen[0]
    assert not (runtime / "goals" / "loopx-turn-fixture" / "semantic_unit_fold").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_run_loopx_turn_once_next_turn_enforces_effective_min_net_savings_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    next_obs = tmp_path / "obs-savings.txt"
    next_obs.write_text("NEXT_OBS_THRESHOLD_OK=true\n", encoding="utf-8")

    from loopx.control_plane.turn_driver import semantic_unit_hook as hook_mod

    obs_calls: list[list[str]] = []
    orig_exec = hook_mod.run_bounded_prefetch_subprocess

    def spy_exec(
        argv: list[str], *, timeout_ms: int, max_output_bytes: int, cwd: str | None = None,
    ) -> Mapping[str, object]:
        obs_calls.append(list(argv))
        return orig_exec(argv, timeout_ms=timeout_ms, max_output_bytes=max_output_bytes, cwd=cwd)

    monkeypatch.setattr(hook_mod, "run_bounded_prefetch_subprocess", spy_exec)

    shared_parent_prefix = {"goal_policy": "preserve Q across min_net_savings threshold checks", "repo": "loopx"}
    bloat_suffix = "verbose exploratory grep output for todo_fixture0001 " * 260
    turn1_exp = [{"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix}]

    plan1 = _plan_turn(route, "turn-thresh-1")
    seen1: list[dict] = []
    cb1 = _completion_callbacks("todo_fixture0001", seen1, result_kind="validated_completion")
    res1 = run_loopx_turn_once(
        plan1,
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": "todo_fixture0001",
            "context_epoch": "epoch-thresh-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": turn1_exp,
            "baseline_text": bloat_suffix,
            "obs_next_cmd": {"command": ["cat", str(next_obs)], "max_output_bytes": 4096},
            "min_net_savings_tokens": 1600,
        },
        **cb1,
    )
    assert res1["ok"] is True
    assert res1["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    assert len(obs_calls) == 1

    added = add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Execute downstream verification under explicit threshold.",
        priority="P1",
    )
    todo2_id = str(added["todo"]["todo_id"])
    complete_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        evidence="Todo 1 verified before Turn 2 savings gate checks.",
    )

    turn2_exp = [
        {"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix},
        {"todo_id": todo2_id, "step": 0, "output": "fresh todo2 kickoff note"},
    ]

    def _run_turn2(turn_id: str, **extra_fold: object) -> tuple[dict, dict]:
        seen: list[dict] = []
        cb = _completion_callbacks(todo2_id, seen, result_kind="validated_progress")
        plan = _plan_turn(route, turn_id)
        fold_opt: dict[str, object] = {
            "enabled": True,
            "todo_id": todo2_id,
            "context_epoch": "epoch-thresh-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": turn2_exp,
            **extra_fold,
        }
        res = run_loopx_turn_once(
            plan,
            project=project,
            runtime_root=runtime,
            goal_id="loopx-turn-fixture",
            timeout_seconds=10.0,
            execute=True,
            semantic_unit_fold=fold_opt,
            **cb,
        )
        assert res["ok"] is True
        assert len(seen) == 1
        assert len(obs_calls) == 1
        return res, seen[0]

    # 1. Explicit min_net_savings_tokens=64 commits FoldToReceipt and replaces Todo 1 bloat.
    _, req_64 = _run_turn2("turn-thresh-01", min_net_savings_tokens=64)
    fold_64 = req_64["semantic_unit_fold"]
    assert fold_64["operator"] == "FoldToReceipt"
    assert req_64["exploratory_context"] != turn2_exp
    assert "verbose exploratory grep output for todo_fixture0001" not in codex_cli_prompt(req_64)
    measured_amort = int(fold_64["cold_prefill_amortization_tokens"])
    measured_amortized = int(fold_64["amortized_net_savings_tokens"])
    assert 64 < measured_amortized < 10000

    # 2. Explicit min_net_savings_tokens=10000 (> ~1.2k amortized) fails closed KeepRaw & retains Todo 1 bloat.
    _, req_10k = _run_turn2("turn-thresh-02", min_net_savings_tokens=10000)
    assert req_10k["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_10k["semantic_unit_fold"]["fallback_reason"] == "negative_net_savings"
    assert req_10k["semantic_unit_fold"]["preserves_raw_state"] is True
    assert req_10k["exploratory_context"] == turn2_exp
    assert "verbose exploratory grep output for todo_fixture0001" in codex_cli_prompt(req_10k)

    # 3. Exact 1-token amortized equality boundary (pinned amortization avoids multi-turn quota float drift).
    _, req_eq = _run_turn2(
        "turn-thresh-03",
        min_net_savings_tokens=measured_amortized,
        cold_prefill_amortization_tokens=measured_amort,
    )
    assert req_eq["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    assert int(req_eq["semantic_unit_fold"]["amortized_net_savings_tokens"]) == measured_amortized
    assert "verbose exploratory grep output for todo_fixture0001" not in codex_cli_prompt(req_eq)

    _, req_above = _run_turn2(
        "turn-thresh-04",
        min_net_savings_tokens=measured_amortized + 1,
        cold_prefill_amortization_tokens=measured_amort,
    )
    assert req_above["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_above["semantic_unit_fold"]["fallback_reason"] == "negative_net_savings"
    assert req_above["exploratory_context"] == turn2_exp
    assert "verbose exploratory grep output for todo_fixture0001" in codex_cli_prompt(req_above)

    # 3b. Auto-amortization tight bracket below and above measured savings.
    _, req_auto_below = _run_turn2("turn-thresh-09", min_net_savings_tokens=measured_amortized - 10)
    assert req_auto_below["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    _, req_auto_above = _run_turn2("turn-thresh-10", min_net_savings_tokens=measured_amortized + 10)
    assert req_auto_above["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_auto_above["semantic_unit_fold"]["fallback_reason"] == "negative_net_savings"
    assert req_auto_above["exploratory_context"] == turn2_exp

    # 4. Omitting min_net_savings_tokens in Turn 2 inherits Turn 1's effective 1600-token threshold -> KeepRaw.
    _, req_inherited = _run_turn2("turn-thresh-05")
    assert req_inherited["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_inherited["semantic_unit_fold"]["min_net_savings_tokens"] == 1600
    assert req_inherited["exploratory_context"] == turn2_exp

    # 5. Zero-amortization raw-savings boundary (cold_prefill_amortization_tokens=0): == raw_net passes, +1 keeps raw.
    _, req_zero_amort = _run_turn2(
        "turn-thresh-06", min_net_savings_tokens=64, cold_prefill_amortization_tokens=0,
    )
    fold_zero = req_zero_amort["semantic_unit_fold"]
    assert fold_zero["operator"] == "FoldToReceipt"
    raw_net = int(fold_zero["net_savings_tokens"])
    assert int(fold_zero["amortized_net_savings_tokens"]) == raw_net

    _, req_raw_eq = _run_turn2(
        "turn-thresh-07", min_net_savings_tokens=raw_net, cold_prefill_amortization_tokens=0,
    )
    assert req_raw_eq["semantic_unit_fold"]["operator"] == "FoldToReceipt"

    _, req_raw_above = _run_turn2(
        "turn-thresh-08", min_net_savings_tokens=raw_net + 1, cold_prefill_amortization_tokens=0,
    )
    assert req_raw_above["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_raw_above["semantic_unit_fold"]["fallback_reason"] == "negative_net_savings"
    assert req_raw_above["exploratory_context"] == turn2_exp


def _extract_dsh_turn_request_payload(prompt: str) -> dict[str, object]:
    marker = "Turn request:\n"
    idx = prompt.index(marker) + len(marker)
    json_line = prompt[idx:].splitlines()[0]
    payload = json.loads(json_line)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_run_loopx_turn_once_dsh_host_strips_internal_fold_telemetry_and_preserves_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    from loopx.control_plane.turn_driver import host_binding

    monkeypatch.setattr(host_binding, "dsh_runtime_importable", lambda *_a, **_kw: True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-placeholder-not-a-secret")
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    next_obs = tmp_path / "obs-dsh.txt"
    next_obs.write_text("DSH_OBS_OK=true\n", encoding="utf-8")

    shared_parent_prefix = {"goal_policy": "preserve DSH host prefix stability", "repo": "loopx"}
    bloat_suffix = "verbose exploratory grep output for todo_fixture0001 " * 260
    turn1_exp = [{"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix}]

    seen1: list[dict] = []
    cb1 = _completion_callbacks("todo_fixture0001", seen1, result_kind="validated_completion")
    res1 = run_loopx_turn_once(
        _plan_turn(route, "turn-dsh-1", host="dsh"),
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": "todo_fixture0001",
            "context_epoch": "epoch-dsh-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": turn1_exp,
            "baseline_text": bloat_suffix,
            "obs_next_cmd": {"command": ["cat", str(next_obs)], "max_output_bytes": 4096},
            "min_net_savings_tokens": 64,
        },
        **cb1,
    )
    assert res1["ok"] is True
    assert res1["semantic_unit_fold"]["operator"] == "FoldToReceipt"

    added = add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Execute DSH Turn 2 exact-threshold verification.",
        priority="P1",
    )
    todo2_id = str(added["todo"]["todo_id"])
    complete_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        evidence="Todo 1 verified for DSH exact-threshold test.",
    )

    turn2_exp = [
        {"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix},
        {"todo_id": todo2_id, "step": 0, "output": "fresh todo2 kickoff note"},
    ]

    def _run_dsh_turn2(turn_id: str, **extra_fold: object) -> tuple[dict, str]:
        seen: list[dict] = []
        cb = _completion_callbacks(todo2_id, seen, result_kind="validated_progress")
        res = run_loopx_turn_once(
            _plan_turn(route, turn_id, host="dsh"),
            project=project,
            runtime_root=runtime,
            goal_id="loopx-turn-fixture",
            timeout_seconds=10.0,
            execute=True,
            semantic_unit_fold={
                "enabled": True,
                "todo_id": todo2_id,
                "context_epoch": "epoch-dsh-1",
                "parent_prefix": shared_parent_prefix,
                "exploratory_context": turn2_exp,
                **extra_fold,
            },
            **cb,
        )
        assert res["ok"] is True
        assert len(seen) == 1
        dsh_prompt = render_dsh_host_prompt(seen[0])
        assert dsh_prompt is not None
        rendered_payload = _extract_dsh_turn_request_payload(dsh_prompt)
        rendered_fold = rendered_payload.get("semantic_unit_fold")
        assert isinstance(rendered_fold, dict)
        forbidden = (
            set(_HOST_WIRE_EXCLUDED_PREFETCH_FIELDS)
            | set(_HOST_WIRE_EXCLUDED_FOLD_TEXT_FIELDS)
            | {"persistence", "anchor_recorded"}
        )
        assert forbidden.isdisjoint(rendered_fold.keys())
        return seen[0], dsh_prompt

    req_64, prompt_64 = _run_dsh_turn2("turn-dsh-01", min_net_savings_tokens=64)
    fold_64 = req_64["semantic_unit_fold"]
    assert fold_64["operator"] == "FoldToReceipt"
    assert "verbose exploratory grep output for todo_fixture0001" not in prompt_64
    assert "Todo Semantic-Unit Fold Receipt:" in prompt_64
    for leaked_key in (
        "net_savings_tokens",
        "rendered_prompt_baseline_tokens",
        "rendered_prompt_candidate_tokens",
        "rendered_prompt_savings_tokens",
        "rendered_prompt_amortized_savings_tokens",
        "tokenizer",
    ):
        assert f'"{leaked_key}"' not in prompt_64

    measured_amort = int(fold_64["cold_prefill_amortization_tokens"])
    measured_amortized = int(fold_64["amortized_net_savings_tokens"])
    assert 64 < measured_amortized < 10000

    req_eq, prompt_eq = _run_dsh_turn2(
        "turn-dsh-02",
        min_net_savings_tokens=measured_amortized,
        cold_prefill_amortization_tokens=measured_amort,
    )
    assert req_eq["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    assert int(req_eq["semantic_unit_fold"]["amortized_net_savings_tokens"]) == measured_amortized
    assert "verbose exploratory grep output for todo_fixture0001" not in prompt_eq

    req_above, prompt_above = _run_dsh_turn2(
        "turn-dsh-03",
        min_net_savings_tokens=measured_amortized + 1,
        cold_prefill_amortization_tokens=measured_amort,
    )
    assert req_above["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_above["semantic_unit_fold"]["fallback_reason"] == "negative_net_savings"
    assert req_above["exploratory_context"] == turn2_exp
    assert "verbose exploratory grep output for todo_fixture0001" in prompt_above

    req_10k, prompt_10k = _run_dsh_turn2("turn-dsh-04", min_net_savings_tokens=10000)
    assert req_10k["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_10k["semantic_unit_fold"]["fallback_reason"] == "negative_net_savings"
    assert "verbose exploratory grep output for todo_fixture0001" in prompt_10k


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_run_loopx_turn_once_preserves_unbound_exploratory_items_across_todo_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    next_obs = tmp_path / "obs-unbound.txt"
    next_obs.write_text("UNBOUND_OBS_OK=true\n", encoding="utf-8")

    shared_parent_prefix = {"goal_policy": "preserve unbound Turn 2 records", "repo": "loopx"}
    bloat_suffix = "verbose exploratory grep output for todo_fixture0001 " * 260
    turn1_exp = [{"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix}]

    seen1: list[dict] = []
    cb1 = _completion_callbacks("todo_fixture0001", seen1, result_kind="validated_completion")
    res1 = run_loopx_turn_once(
        _plan_turn(route, "turn-unbound-1"),
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": "todo_fixture0001",
            "context_epoch": "epoch-unbound-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": turn1_exp,
            "baseline_text": bloat_suffix,
            "obs_next_cmd": {"command": ["cat", str(next_obs)], "max_output_bytes": 4096},
            "min_net_savings_tokens": 64,
        },
        **cb1,
    )
    assert res1["ok"] is True
    assert res1["semantic_unit_fold"]["operator"] == "FoldToReceipt"

    added = add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Verify Todo 2 while retaining newly introduced unbound exploratory records.",
        priority="P1",
    )
    todo2_id = str(added["todo"]["todo_id"])
    complete_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        evidence="Todo 1 verified before mixed unbound Turn 2 check.",
    )

    unbound_dict = {"step": 2, "output": "NEW_UNBOUND_EVIDENCE_KEEP_INTACT"}
    blank_todo_dict = {"todo_id": "   ", "step": 3, "output": "BLANK_TODO_ID_KEEP_INTACT"}
    unbound_str = "PLAIN_TEXT_UNBOUND_ENTRY_KEEP_INTACT"
    todo2_item = {"todo_id": todo2_id, "step": 0, "output": "TODO2_BOUND_EVIDENCE_KEEP_INTACT"}
    mixed_exp = [
        {"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix},
        unbound_dict,
        blank_todo_dict,
        unbound_str,
        todo2_item,
    ]

    seen2: list[dict] = []
    cb2 = _completion_callbacks(todo2_id, seen2, result_kind="validated_progress")
    res2 = run_loopx_turn_once(
        _plan_turn(route, "turn-unbound-2"),
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": todo2_id,
            "context_epoch": "epoch-unbound-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": mixed_exp,
            "min_net_savings_tokens": 64,
        },
        **cb2,
    )
    assert res2["ok"] is True
    assert len(seen2) == 1
    req2 = seen2[0]
    assert req2["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    delivered_exp = req2["exploratory_context"]
    assert unbound_dict in delivered_exp
    assert blank_todo_dict in delivered_exp
    assert unbound_str in delivered_exp
    assert todo2_item in delivered_exp
    assert all(
        not (isinstance(row, Mapping) and row.get("output") == bloat_suffix)
        for row in delivered_exp
    )
    rendered2 = codex_cli_prompt(req2)
    assert "NEW_UNBOUND_EVIDENCE_KEEP_INTACT" in rendered2
    assert "BLANK_TODO_ID_KEEP_INTACT" in rendered2
    assert "PLAIN_TEXT_UNBOUND_ENTRY_KEEP_INTACT" in rendered2
    assert "TODO2_BOUND_EVIDENCE_KEEP_INTACT" in rendered2
    assert "verbose exploratory grep output for todo_fixture0001" not in rendered2

    # Unbound entry preceding Todo A shifts parent prefix -> KeepRaw preserves all records verbatim.
    seen3: list[dict] = []
    cb3 = _completion_callbacks(todo2_id, seen3, result_kind="validated_progress")
    preceding_exp = [unbound_dict, {"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix}, todo2_item]
    res3 = run_loopx_turn_once(
        _plan_turn(route, "turn-unbound-3"),
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": todo2_id,
            "context_epoch": "epoch-unbound-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": preceding_exp,
            "min_net_savings_tokens": 64,
        },
        **cb3,
    )
    assert res3["ok"] is True
    assert seen3[0]["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert seen3[0]["semantic_unit_fold"]["fallback_reason"] == "prefix_digest_mismatch"
    assert seen3[0]["exploratory_context"] == preceding_exp

    # Single untagged mapping or raw string without todo_id cannot be dropped as Todo A evidence.
    seen4: list[dict] = []
    cb4 = _completion_callbacks(todo2_id, seen4, result_kind="validated_progress")
    res4 = run_loopx_turn_once(
        _plan_turn(route, "turn-unbound-4"),
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": todo2_id,
            "context_epoch": "epoch-unbound-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": unbound_dict,
            "min_net_savings_tokens": 64,
        },
        **cb4,
    )
    assert res4["ok"] is True
    assert seen4[0]["exploratory_context"] == unbound_dict


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_run_loopx_turn_once_constrains_fold_to_covered_turn1_records_across_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    route = ["--registry", str(registry), "--runtime-root", str(runtime)]
    next_obs = tmp_path / "obs-covered.txt"
    next_obs.write_text("COVERED_SPAN_OBS_OK=true\n", encoding="utf-8")

    shared_parent_prefix = {"goal_policy": "constrain replacement to covered span", "repo": "loopx"}
    bloat_suffix = "verbose exploratory grep output for todo_fixture0001 " * 260
    covered_a1 = {"todo_id": "todo_fixture0001", "step": 1, "output": bloat_suffix}
    turn1_exp = [covered_a1]

    seen1: list[dict] = []
    cb1 = _completion_callbacks("todo_fixture0001", seen1, result_kind="validated_completion")
    res1 = run_loopx_turn_once(
        _plan_turn(route, "turn-covered-1"),
        project=project,
        runtime_root=runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": "todo_fixture0001",
            "context_epoch": "epoch-covered-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": turn1_exp,
            "baseline_text": bloat_suffix,
            "obs_next_cmd": {"command": ["cat", str(next_obs)], "max_output_bytes": 4096},
            "min_net_savings_tokens": 64,
        },
        **cb1,
    )
    assert res1["ok"] is True
    assert res1["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    assert len(res1["semantic_unit_fold"]["covered_exploratory_digests"]) == 1

    added = add_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Verify Todo 2 retains late/rewritten/unverified Todo A observations.",
        priority="P1",
    )
    todo2_id = str(added["todo"]["todo_id"])
    complete_goal_todo(
        registry_path=registry,
        runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        evidence="Todo 1 verified before covered-span Turn 2 checks.",
    )

    todo2_item = {"todo_id": todo2_id, "step": 0, "output": "TODO2_BOUND_EVIDENCE_KEEP"}
    unbound_item = {"step": 2, "output": "UNBOUND_RECORD_KEEP"}
    late_a_item = {"todo_id": "todo_fixture0001", "step": 2, "output": "POST_SETTLEMENT_A_OBSERVATION " * 140}
    rewritten_a_item = {"todo_id": "todo_fixture0001", "step": 1, "output": "REWRITTEN_A_OBSERVATION " * 140}

    def _run_case(turn_id: str, exploratory: object) -> dict:
        seen: list[dict] = []
        cb = _completion_callbacks(todo2_id, seen, result_kind="validated_progress")
        res = run_loopx_turn_once(
            _plan_turn(route, turn_id),
            project=project,
            runtime_root=runtime,
            goal_id="loopx-turn-fixture",
            timeout_seconds=10.0,
            execute=True,
            semantic_unit_fold={
                "enabled": True,
                "todo_id": todo2_id,
                "context_epoch": "epoch-covered-1",
                "parent_prefix": shared_parent_prefix,
                "exploratory_context": exploratory,
                "min_net_savings_tokens": 64,
            },
            **cb,
        )
        assert res["ok"] is True
        assert len(seen) == 1
        assert "covered_exploratory_digests" not in codex_cli_prompt(seen[0])
        return seen[0]

    # 1. Post-settlement A item alone (original covered A1 absent): prefix/tokens pass, receipt does NOT cover late_a_item -> KeepRaw.
    req_late_only = _run_case("turn-covered-2a", [late_a_item, todo2_item, unbound_item])
    assert req_late_only["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_late_only["semantic_unit_fold"]["fallback_reason"] == "source_digest_mismatch"
    assert req_late_only["semantic_unit_fold"]["preserves_raw_state"] is True
    assert req_late_only["exploratory_context"] == [late_a_item, todo2_item, unbound_item]
    assert "POST_SETTLEMENT_A_OBSERVATION" in codex_cli_prompt(req_late_only)

    # 2. Rewritten A item: digest mismatches Turn 1 snapshot -> KeepRaw preserves rewritten A, B, unbound verbatim.
    req_rewritten = _run_case("turn-covered-2b", [rewritten_a_item, todo2_item, unbound_item])
    assert req_rewritten["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert req_rewritten["semantic_unit_fold"]["fallback_reason"] == "source_digest_mismatch"
    assert req_rewritten["exploratory_context"] == [rewritten_a_item, todo2_item, unbound_item]

    # 3. Covered A1 intact + post-settlement late A2 + B + unbound -> replaces ONLY covered A1; preserves late_a_item, B, unbound!
    req_covered_plus_late = _run_case("turn-covered-2c", [covered_a1, late_a_item, todo2_item, unbound_item])
    assert req_covered_plus_late["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    delivered_plus_late = req_covered_plus_late["exploratory_context"]
    assert late_a_item in delivered_plus_late
    assert todo2_item in delivered_plus_late
    assert unbound_item in delivered_plus_late
    assert covered_a1 not in delivered_plus_late
    prompt_plus_late = codex_cli_prompt(req_covered_plus_late)
    assert "POST_SETTLEMENT_A_OBSERVATION" in prompt_plus_late
    assert "TODO2_BOUND_EVIDENCE_KEEP" in prompt_plus_late
    assert "UNBOUND_RECORD_KEEP" in prompt_plus_late
    assert "verbose exploratory grep output for todo_fixture0001" not in prompt_plus_late

    # 4. Turn 1 settled without any inline exploratory_context -> Turn 2 A observation has uncovered scope -> KeepRaw!
    sub_root = tmp_path / "uncovered-scope"
    sub_root.mkdir()
    if provider == "sqlite":
        isolate_sqlite_runtime(sub_root, monkeypatch)
    sub_proj, sub_runtime, sub_registry = _write_live_fixture(sub_root, provider=provider)
    sub_route = ["--registry", str(sub_registry), "--runtime-root", str(sub_runtime)]
    sub_seen1: list[dict] = []
    sub_res1 = run_loopx_turn_once(
        _plan_turn(sub_route, "turn-uncov-1"),
        project=sub_proj,
        runtime_root=sub_runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": "todo_fixture0001",
            "context_epoch": "epoch-uncov-1",
            "parent_prefix": shared_parent_prefix,
            "baseline_text": bloat_suffix,
            "obs_next_cmd": {"command": ["cat", str(next_obs)], "max_output_bytes": 4096},
            "min_net_savings_tokens": 64,
        },
        **_completion_callbacks("todo_fixture0001", sub_seen1, result_kind="validated_completion"),
    )
    assert sub_res1["ok"] is True
    assert sub_res1["semantic_unit_fold"]["operator"] == "FoldToReceipt"
    assert sub_res1["semantic_unit_fold"]["covered_exploratory_digests"] == []
    sub_added = add_goal_todo(
        registry_path=sub_registry,
        runtime_root_arg=str(sub_runtime),
        goal_id="loopx-turn-fixture",
        role="agent",
        text="Todo 2 under empty Turn 1 baseline.",
        priority="P1",
    )
    sub_todo2 = str(sub_added["todo"]["todo_id"])
    complete_goal_todo(
        registry_path=sub_registry,
        runtime_root_arg=str(sub_runtime),
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        agent_id="codex-fixture",
        evidence="Todo 1 verified without inline exploratory items.",
    )
    sub_seen2: list[dict] = []
    sub_res2 = run_loopx_turn_once(
        _plan_turn(sub_route, "turn-uncov-2"),
        project=sub_proj,
        runtime_root=sub_runtime,
        goal_id="loopx-turn-fixture",
        timeout_seconds=10.0,
        execute=True,
        semantic_unit_fold={
            "enabled": True,
            "todo_id": sub_todo2,
            "context_epoch": "epoch-uncov-1",
            "parent_prefix": shared_parent_prefix,
            "exploratory_context": [late_a_item, {"todo_id": sub_todo2, "output": "B_KEEP"}, unbound_item],
            "min_net_savings_tokens": 64,
        },
        **_completion_callbacks(sub_todo2, sub_seen2, result_kind="validated_progress"),
    )
    assert sub_res2["ok"] is True
    assert sub_seen2[0]["semantic_unit_fold"]["operator"] == "KeepRaw"
    assert sub_seen2[0]["semantic_unit_fold"]["fallback_reason"] == "source_digest_mismatch"
    assert sub_seen2[0]["exploratory_context"] == [late_a_item, {"todo_id": sub_todo2, "output": "B_KEEP"}, unbound_item]
