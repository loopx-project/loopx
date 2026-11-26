"""Hook-boundary handler for receipt-gated Todo semantic-unit anchor and fold swaps.

Semantic validation, anti-whitewashing receipt construction, net token gating,
and KeepRaw rollback stay owned by TypeScript (`quota/stage_then_swap.ts`).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import json

from ..effect_runtime import EffectRuntimeRejected, EffectRuntimeRemoteError, EffectRuntimeStartupError
from ..quota.turn_envelope import (
    build_semantic_unit_fold_swap,
    plan_stage_then_swap_prefetch,
    record_semantic_unit_anchor,
)
from .epoch_prefetch import (
    DEFAULT_TOKENIZER_ENCODING,
    count_verifiable_tokens,
    run_bounded_prefetch_subprocess as run_bounded_prefetch_subprocess,
)


def _as_object_dict(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str)]


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _semantic_unit_fold_replay_key(
    fold_cfg: Mapping[str, object],
    context_epoch: str,
) -> str | None:
    todo_id = fold_cfg.get("todo_id")
    turn_id = fold_cfg.get("turn_instance_id")
    receipt = fold_cfg.get("completion_receipt")
    receipt_id = receipt.get("completion_receipt_id") if isinstance(receipt, Mapping) else None
    if (
        isinstance(todo_id, str)
        and todo_id
        and isinstance(turn_id, str)
        and turn_id
        and isinstance(receipt_id, str)
        and receipt_id
        and context_epoch
    ):
        return f"{todo_id}:{turn_id}:{receipt_id}:{context_epoch}"
    return None


def _run_semantic_unit_obs_command(
    fold_cfg: Mapping[str, object],
    *,
    anchor: Mapping[str, object] | None,
    todo_id: str,
    turn_instance_id: str,
    context_epoch: str,
    prefix_digest: str | None,
    quiescent_boundary: bool,
    cwd: object,
) -> tuple[dict[str, object] | None, bool]:
    raw_obs = fold_cfg.get("obs_next_cmd")
    if not isinstance(raw_obs, Mapping):
        return None, False
    obs: dict[str, object] = {str(k): v for k, v in raw_obs.items()}
    if "output" in obs and "exit_code" in obs:
        return obs, False
    cmd = obs.get("prefetch_command") if obs.get("prefetch_command") is not None else obs.get("command")
    receipt = fold_cfg.get("completion_receipt")
    pre_valid = (
        cmd is not None
        and quiescent_boundary
        and isinstance(anchor, Mapping)
        and anchor.get("todo_id") == todo_id
        and anchor.get("turn_instance_id") == turn_instance_id
        and anchor.get("context_epoch") == context_epoch
        and anchor.get("prefix_digest") == prefix_digest
        and isinstance(receipt, Mapping)
        and receipt.get("todo_id") == todo_id
        and receipt.get("exit_code") == 0
    )
    if not pre_valid:
        return obs, False
    safety = plan_stage_then_swap_prefetch(
        epoch_prefetch={
            "enabled": True,
            "initiator": obs.get("initiator") or "turn_driver",
            "prefetch_command": cmd,
            "timeout_ms": _opt_int(obs.get("timeout_ms")) or 5000,
            "max_output_bytes": _opt_int(obs.get("max_output_bytes")) or 16384,
            "reject_on_truncation": obs.get("reject_on_truncation", True),
        }
    )
    if not safety.get("eligible"):
        return obs, False
    argv = list(safety["safety"]["argv"])
    run_result = run_bounded_prefetch_subprocess(
        argv,
        timeout_ms=int(safety["timeout_ms"]),
        max_output_bytes=int(safety["max_output_bytes"]),
        cwd=str(cwd) if isinstance(cwd, str) and cwd else None,
    )
    obs["prefetch_command"] = argv
    obs["exit_code"] = run_result["exit_code"]
    obs["timed_out"] = run_result["timed_out"]
    obs["truncated"] = run_result["truncated"]
    obs["output"] = run_result.get("prefetched_output", "")
    obs["latency_ms"] = run_result["latency_ms"]
    return obs, True


def _format_semantic_unit_hook_output(
    hook_name: str,
    receipt: Mapping[str, object],
    *,
    anchor: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if receipt.get("operator") == "FoldToReceipt" and isinstance(receipt.get("replacement_text"), str):
        additional_context = str(receipt["replacement_text"])
    else:
        facts = json.dumps(receipt.get("preserved_failure_facts", []), ensure_ascii=False)
        obligations = json.dumps(receipt.get("preserved_open_obligations", []), ensure_ascii=False)
        reason_note = str(
            receipt.get("error_observation") or "Semantic-unit fold kept raw context intact (KeepRaw)."
        )
        additional_context = (
            f"{reason_note}\n"
            f"Preserved failure facts: {facts}\n"
            f"Preserved open obligations: {obligations}"
        )
    hook_out: dict[str, object] = {
        "hookEventName": hook_name,
        "additionalContext": additional_context,
        "semanticUnitFold": {str(k): v for k, v in receipt.items()},
    }
    if anchor is not None:
        hook_out["semanticUnitAnchor"] = {str(k): v for k, v in anchor.items()}
    return {"hookSpecificOutput": hook_out}


def _record_semantic_unit_anchor_if_present(
    state: Mapping[str, object],
    updated: dict[str, object],
    event: Mapping[str, object],
) -> dict[str, object] | None:
    raw_req = event.get("semantic_unit_anchor")
    if not (isinstance(raw_req, Mapping) and raw_req.get("enabled", True) is not False):
        return None
    target_epoch = str(raw_req.get("context_epoch") or updated.get("context_epoch") or state.get("context_epoch") or "")
    if not target_epoch or target_epoch != str(updated.get("context_epoch") or ""):
        return None
    step_idx = _opt_int(raw_req.get("anchor_step_index"))
    try:
        recorded = record_semantic_unit_anchor(
            todo_id=str(raw_req.get("todo_id") or ""),
            turn_instance_id=str(raw_req.get("turn_instance_id") or ""),
            anchor_step_index=step_idx if step_idx is not None else -1,
            prefix_digest=str(raw_req.get("prefix_digest") or ""),
            context_epoch=target_epoch,
            anchor_surface_seq=_opt_int(raw_req.get("anchor_surface_seq")),
            goal_id=raw_req.get("goal_id") if isinstance(raw_req.get("goal_id"), str) else None,
            agent_id=raw_req.get("agent_id") if isinstance(raw_req.get("agent_id"), str) else None,
        )
    except (EffectRuntimeRejected, EffectRuntimeRemoteError, EffectRuntimeStartupError, ValueError, TypeError):
        return None
    anchors = _as_object_dict(updated.get("semantic_unit_anchors"))
    anchors[str(recorded["todo_id"])] = recorded
    updated["semantic_unit_anchors"] = anchors
    return recorded


def _lookup_replayed_fold(
    state: Mapping[str, object],
    updated: Mapping[str, object],
    replay_key: str | None,
) -> dict[str, object] | None:
    if not replay_key:
        return None
    for container in (updated.get("semantic_unit_folds"), state.get("semantic_unit_folds")):
        if isinstance(container, Mapping) and isinstance(container.get(replay_key), Mapping):
            return {**_as_object_dict(container[replay_key]), "replayed": True, "executed": False}
    return None


def _resolve_semantic_unit_anchor(
    fold_req: Mapping[str, object],
    state: Mapping[str, object],
    updated: Mapping[str, object],
    todo_id: str,
) -> dict[str, object] | None:
    if isinstance(fold_req.get("anchor"), Mapping):
        return _as_object_dict(fold_req["anchor"])
    same_epoch = str(updated.get("context_epoch") or "") == str(state.get("context_epoch") or "")
    anchors_map = (
        updated.get("semantic_unit_anchors")
        if isinstance(updated.get("semantic_unit_anchors"), Mapping)
        else (state.get("semantic_unit_anchors") if same_epoch else None)
    )
    stored = anchors_map.get(todo_id) if isinstance(anchors_map, Mapping) and todo_id else None
    return _as_object_dict(stored) if isinstance(stored, Mapping) else None


def _evaluate_semantic_unit_fold(
    *,
    fold_req: Mapping[str, object],
    anchor: Mapping[str, object] | None,
    todo_id: str,
    turn_instance_id: str,
    context_epoch: str,
    quiescent: bool,
    cwd: object,
    token_counter: Callable[[str], int] | None,
) -> dict[str, object]:
    raw_prefix = fold_req.get("prefix_digest")
    prefix_digest = raw_prefix if isinstance(raw_prefix, str) else None
    obs_payload, obs_executed = _run_semantic_unit_obs_command(
        fold_req,
        anchor=anchor,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
        context_epoch=context_epoch,
        prefix_digest=prefix_digest,
        quiescent_boundary=quiescent,
        cwd=cwd,
    )
    encoding_name = str(fold_req.get("tokenizer_encoding") or DEFAULT_TOKENIZER_ENCODING)
    baseline_tokens = _opt_int(fold_req.get("baseline_tokens"))
    baseline_bytes = _opt_int(fold_req.get("baseline_bytes"))
    tokenizer_meta: dict[str, object] | None = (
        _as_object_dict(fold_req.get("tokenizer")) if isinstance(fold_req.get("tokenizer"), Mapping) else None
    )
    baseline_text = fold_req.get("baseline_text")
    if baseline_tokens is None and isinstance(baseline_text, str):
        measured_base = count_verifiable_tokens(baseline_text, encoding_name=encoding_name, token_counter=token_counter)
        if measured_base is not None:
            baseline_tokens = measured_base[0]
            if tokenizer_meta is None:
                tokenizer_meta = _as_object_dict(measured_base[1])
        if baseline_bytes is None:
            baseline_bytes = len(baseline_text.encode("utf-8"))

    replacement_tokens = _opt_int(fold_req.get("replacement_tokens"))
    raw_receipt = fold_req.get("completion_receipt")
    def call_fold(
        *,
        rep_tokens: int | None,
        min_sav: int | None,
        cold_amort: int | None,
        tok_meta: dict[str, object] | None,
    ) -> dict[str, object]:
        return build_semantic_unit_fold_swap(
            enabled=True,
            anchor=anchor,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            context_epoch=context_epoch,
            prefix_digest=prefix_digest,
            now_step_index=_opt_int(fold_req.get("now_step_index")),
            now_surface_seq=_opt_int(fold_req.get("now_surface_seq")),
            completion_receipt=raw_receipt if isinstance(raw_receipt, Mapping) else None,
            baseline_tokens=baseline_tokens,
            replacement_tokens=rep_tokens,
            baseline_bytes=baseline_bytes,
            min_net_savings_tokens=min_sav,
            cold_prefill_amortization_tokens=cold_amort,
            tokenizer_encoding=encoding_name,
            tokenizer=tok_meta,
            obs_next_cmd=obs_payload,
            surface_prefix_intact=fold_req.get("surface_prefix_intact", True) is not False,
            tool_pairing_valid=fold_req.get("tool_pairing_valid", True) is not False,
            quiescent_boundary=quiescent,
            failure_facts=_as_str_list(fold_req.get("failure_facts")),
            open_obligations=_as_str_list(fold_req.get("open_obligations")),
        )

    if replacement_tokens is None and baseline_tokens is not None and tokenizer_meta is not None:
        preview = call_fold(rep_tokens=1, min_sav=1, cold_amort=0, tok_meta=tokenizer_meta)
        preview_text = preview.get("replacement_text")
        measured_rep = (
            count_verifiable_tokens(preview_text, encoding_name=encoding_name, token_counter=token_counter)
            if isinstance(preview_text, str)
            else None
        )
        if measured_rep is not None:
            replacement_tokens = measured_rep[0]
            tokenizer_meta = _as_object_dict(measured_rep[1])
        elif isinstance(preview_text, str):
            tokenizer_meta = None

    receipt = call_fold(
        rep_tokens=replacement_tokens,
        min_sav=_opt_int(fold_req.get("min_net_savings_tokens")),
        cold_amort=_opt_int(fold_req.get("cold_prefill_amortization_tokens")),
        tok_meta=tokenizer_meta,
    )
    return {**_as_object_dict(receipt), "replayed": False, "executed": obs_executed}


def apply_codex_context_semantic_unit_hook(
    *,
    hook_name: str,
    state: Mapping[str, object],
    updated: dict[str, object],
    event: Mapping[str, object],
    token_counter: Callable[[str], int] | None = None,
) -> dict[str, object] | None:
    """Emit or consume default-off Todo semantic-unit anchors and fold receipts on quiescent hooks."""
    recorded_anchor = _record_semantic_unit_anchor_if_present(state, updated, event)
    raw_fold_req = event.get("semantic_unit_fold")
    if not (isinstance(raw_fold_req, Mapping) and raw_fold_req.get("enabled") is True):
        return (
            {"hookSpecificOutput": {"hookEventName": hook_name, "semanticUnitAnchor": recorded_anchor}}
            if recorded_anchor is not None
            else None
        )
    fold_req = _as_object_dict(raw_fold_req)
    context_epoch = str(fold_req.get("context_epoch") or updated.get("context_epoch") or state.get("context_epoch") or "")
    todo_id = str(fold_req.get("todo_id") or "")
    replay_key = _semantic_unit_fold_replay_key(fold_req, context_epoch)
    replayed = _lookup_replayed_fold(state, updated, replay_key)
    if replayed is not None:
        return _format_semantic_unit_hook_output(hook_name, replayed, anchor=recorded_anchor)
    quiescent = (
        not bool(event.get("tool_in_flight"))
        and not bool(event.get("unpaired_tool_calls"))
        and not bool(state.get("pending"))
        and not bool(updated.get("pending"))
        and fold_req.get("quiescent_boundary", True) is not False
    )
    receipt = _evaluate_semantic_unit_fold(
        fold_req=fold_req,
        anchor=_resolve_semantic_unit_anchor(fold_req, state, updated, todo_id),
        todo_id=todo_id,
        turn_instance_id=str(fold_req.get("turn_instance_id") or ""),
        context_epoch=context_epoch,
        quiescent=quiescent,
        cwd=event.get("cwd"),
        token_counter=token_counter,
    )
    if replay_key and receipt.get("operator") == "FoldToReceipt":
        folds = _as_object_dict(updated.get("semantic_unit_folds"))
        folds[replay_key] = receipt
        updated["semantic_unit_folds"] = folds
    return _format_semantic_unit_hook_output(hook_name, receipt, anchor=recorded_anchor)


def semantic_unit_event_active(event: Mapping[str, object]) -> bool:
    """Return True only when an opt-in Todo semantic-unit anchor or fold hook is active."""
    fold = event.get("semantic_unit_fold")
    return isinstance(event.get("semantic_unit_anchor"), Mapping) or (
        isinstance(fold, Mapping) and fold.get("enabled") is True
    )


def run_locked_semantic_unit_hook(
    *,
    hook_name: object,
    session: str,
    state: Mapping[str, object],
    updated: dict[str, object],
    event: Mapping[str, object],
) -> dict[str, object] | None:
    """Run opt-in Todo semantic-unit hook persistence cleanly inside the codex_context lock."""
    if not (
        semantic_unit_event_active(event)
        and isinstance(hook_name, str)
        and hook_name in {"PreCompact", "PostCompact", "SessionStart", "PostToolUse"}
        and updated.get("session_id") == session
        and updated.get("cwd") == event.get("cwd")
    ):
        return None
    return apply_codex_context_semantic_unit_hook(
        hook_name=hook_name,
        state=state,
        updated=updated,
        event=event,
    )


def merge_semantic_unit_hook_output(
    target: dict[str, object],
    semantic_output: Mapping[str, object] | None,
) -> dict[str, object]:
    """Attach semanticUnitAnchor / semanticUnitFold metadata onto an existing hook output."""
    if semantic_output is None:
        return target
    fold_out = semantic_output.get("hookSpecificOutput")
    target_out = target.get("hookSpecificOutput")
    if isinstance(fold_out, Mapping) and isinstance(target_out, dict):
        for key in ("semanticUnitAnchor", "semanticUnitFold"):
            if key in fold_out:
                target_out[key] = fold_out[key]
    return target
