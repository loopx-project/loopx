"""Explore visual marker readback and idempotent delivery reconciliation."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..cli_transport import (
    CommandRunner,
    command_error,
    run_command,
)


VISUAL_READBACK_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.0, 2.0, 4.0)
VISUAL_READBACK_COMMAND_TIMEOUT_SECONDS = 10.0


def whiteboard_raw_texts(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    nodes = data.get("nodes") if isinstance(data, Mapping) else None
    texts: list[str] = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, Mapping):
            continue
        text_node = node.get("text")
        if isinstance(text_node, Mapping) and str(text_node.get("text") or "").strip():
            texts.append(str(text_node.get("text")))
    return texts


def structured_command_error(result: Mapping[str, Any]) -> Mapping[str, Any]:
    parsed = result.get("json")
    if not isinstance(parsed, Mapping):
        try:
            parsed = json.loads(str(result.get("stderr") or ""))
        except (TypeError, json.JSONDecodeError):
            parsed = None
    error = parsed.get("error") if isinstance(parsed, Mapping) else None
    return error if isinstance(error, Mapping) else {}


def is_retryable_marker_readback_error(
    *, error_code: Any, error_message: str
) -> bool:
    if error_code == 4003101 and "doc is applying" in error_message:
        return True
    # Lark can briefly return ``invalid arg`` from the raw-node query
    # immediately after accepting a whiteboard overwrite. This helper is
    # called only by that post-publish marker path, so it does not generalize
    # every API 2890002 response into a transient error.
    return error_code == 2890002 and "invalid arg" in error_message


def readback_visual_delivery_marker(
    *,
    cli_bin: str,
    identity: str,
    whiteboard_token: str,
    marker: str,
    runner: CommandRunner,
    retry_delays: Sequence[float] | None = None,
    command_timeout_seconds: float | None = VISUAL_READBACK_COMMAND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    command = [
        cli_bin,
        "whiteboard",
        "+query",
        "--as",
        identity,
        "--whiteboard-token",
        whiteboard_token,
        "--output_as",
        "raw",
        "--format",
        "json",
    ]
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    texts: list[str] = []
    marker_observed = False
    effective_retry_delays = tuple(
        VISUAL_READBACK_RETRY_DELAYS_SECONDS
        if retry_delays is None
        else retry_delays
    )
    for attempt_index in range(len(effective_retry_delays) + 1):
        result = run_command(
            command,
            execute=True,
            runner=runner,
            timeout_seconds=command_timeout_seconds,
        )
        texts = whiteboard_raw_texts(result.get("json"))
        marker_observed = marker in texts
        error = structured_command_error(result)
        error_code = error.get("code")
        is_retryable = (
            bool(result.get("timed_out"))
            or is_retryable_marker_readback_error(
                error_code=error_code,
                error_message=str(error.get("message") or ""),
            )
            or bool(result.get("ok") and not marker_observed)
        )
        attempts.append(
            {
                "attempt": attempt_index + 1,
                "ok": bool(result.get("ok")),
                "marker_observed": marker_observed,
                "error_code": error_code,
                "timed_out": bool(result.get("timed_out")),
                "retryable": is_retryable,
            }
        )
        # A host timeout already consumed the bounded wait for this batch.
        # Retrying here would multiply that wait by the visibility schedule
        # and can again stall the whole refresh.
        if marker_observed or result.get("timed_out") or not is_retryable:
            break
        if attempt_index < len(effective_retry_delays):
            time.sleep(effective_retry_delays[attempt_index])
    command_receipt = {
        key: result.get(key)
        for key in (
            "command",
            "executed",
            "ok",
            "returncode",
            "timed_out",
            "stderr",
        )
        if result.get(key) not in (None, "")
    }
    return {
        "ok": bool(result.get("ok") and marker_observed),
        "schema_version": "loopx_lark_explore_visual_readback_v0",
        "performed": True,
        "verified": marker_observed,
        "source": "whiteboard_raw_nodes",
        "expected_marker": marker,
        "observed_marker": marker if marker_observed else None,
        "remote_text_node_count": len(texts),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "retryable": bool(
            not marker_observed and attempts and attempts[-1].get("retryable")
        ),
        "host_wait_exhausted": bool(result.get("timed_out")),
        "command": command_receipt,
        "error": (
            None
            if result.get("ok") and marker_observed
            else command_error(result)
            if not result.get("ok")
            else "remote whiteboard raw nodes do not contain the expected delivery marker"
        ),
    }


def publish_visual_with_readback(
    *,
    cli_bin: str,
    identity: str,
    whiteboard_token: str,
    marker: str,
    command: list[str],
    published_source: str,
    source_path: Path,
    execute: bool,
    runner: CommandRunner,
    defer_readback: bool,
) -> dict[str, Any]:
    """Reconcile a deterministic marker, publish once, then verify delivery."""

    prepublish_readback = None
    reconciled_existing_delivery = False
    prepublish_readback_blocked = False
    if execute:
        prepublish_readback = readback_visual_delivery_marker(
            cli_bin=cli_bin,
            identity=identity,
            whiteboard_token=whiteboard_token,
            marker=marker,
            runner=runner,
            retry_delays=(),
        )
        prepublish_command = prepublish_readback.get("command")
        prepublish_command = (
            prepublish_command if isinstance(prepublish_command, Mapping) else {}
        )
        reconciled_existing_delivery = bool(prepublish_readback.get("verified"))
        prepublish_readback_blocked = bool(
            not reconciled_existing_delivery and not prepublish_command.get("ok")
        )
    if not execute:
        result = run_command(command, execute=False, runner=runner)
        publish_attempts = [result]
    elif reconciled_existing_delivery or prepublish_readback_blocked:
        result = run_command(command, execute=False, runner=runner)
        publish_attempts = []
    else:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(published_source, encoding="utf-8")
        try:
            result = run_command(
                command,
                execute=True,
                runner=runner,
                cwd=source_path.parent,
            )
            publish_attempts = [result]
            error = structured_command_error(result)
            if (
                not result.get("ok")
                and error.get("code") == 2891001
                and "not iterable" in str(error.get("message") or "")
            ):
                retry_command = list(command)
                token_index = retry_command.index("--idempotent-token") + 1
                retry_material = (
                    f"{retry_command[token_index]}:{time.time_ns()}".encode("utf-8")
                )
                retry_command[token_index] = (
                    "loopx-retry-" + hashlib.sha256(retry_material).hexdigest()[:32]
                )
                result = run_command(
                    retry_command,
                    execute=True,
                    runner=runner,
                    cwd=source_path.parent,
                )
                publish_attempts.append(result)
        finally:
            source_path.unlink(missing_ok=True)
    readback_deferred = bool(
        execute
        and not reconciled_existing_delivery
        and not prepublish_readback_blocked
        and result.get("ok")
        and marker
        and defer_readback
    )
    readback: dict[str, Any] = dict(prepublish_readback or {}) if (
        reconciled_existing_delivery or prepublish_readback_blocked
    ) else {
        "ok": not readback_deferred,
        "schema_version": "loopx_lark_explore_visual_readback_v0",
        "performed": False,
        "verified": False,
        "source": (
            "not_required"
            if not marker
            else "deferred_to_batch"
            if readback_deferred
            else "would_query_whiteboard_raw_nodes"
        ),
        "expected_marker": marker,
        "observed_marker": None,
        "retryable": readback_deferred,
        "error": None,
    }
    if (
        execute
        and not reconciled_existing_delivery
        and not prepublish_readback_blocked
        and result.get("ok")
        and marker
        and not defer_readback
    ):
        readback = readback_visual_delivery_marker(
            cli_bin=cli_bin,
            identity=identity,
            whiteboard_token=whiteboard_token,
            marker=marker,
            runner=runner,
        )
    delivery_ok = bool(
        not prepublish_readback_blocked
        and result.get("ok")
        and (not marker or readback.get("ok"))
    )
    retryable = bool(
        execute and result.get("ok") and marker and readback.get("retryable")
    )
    return {
        "ok": delivery_ok,
        "retryable": retryable,
        "command": result,
        "publish_attempts": publish_attempts,
        "readback": readback,
        "external_write_performed": any(
            bool(item.get("executed")) for item in publish_attempts
        ),
        "reconciled_existing_delivery": reconciled_existing_delivery,
        "prepublish_readback": prepublish_readback,
    }


def _merge_readback_attempts(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    attempts = [
        dict(item)
        for item in previous.get("attempts") or []
        if isinstance(item, Mapping)
    ]
    for item in current.get("attempts") or []:
        if isinstance(item, Mapping):
            attempts.append(dict(item, attempt=len(attempts) + 1))
    merged = dict(current)
    merged["attempts"] = attempts
    merged["attempt_count"] = len(attempts)
    return merged


def _apply_stage_readback(
    stage_result: dict[str, Any], readback: Mapping[str, Any]
) -> None:
    command = stage_result.get("command")
    command_ok = bool(isinstance(command, Mapping) and command.get("ok"))
    delivery_ok = bool(command_ok and readback.get("ok"))
    retryable = bool(command_ok and readback.get("retryable"))
    stage_result.update(
        {
            "ok": delivery_ok,
            "status": "published" if delivery_ok else "publish_unverified",
            "published": delivery_ok,
            "readback": dict(readback),
            "retryable": retryable,
            "required_action": (
                "retry Explore visual sync; post-publish marker readback did not settle"
                if retryable
                else None
            ),
            "error": None
            if delivery_ok
            else str(readback.get("error") or "visual marker readback failed"),
        }
    )


def defer_visual_stage_after_host_timeout(stage_result: dict[str, Any]) -> None:
    """Leave a retryable receipt without issuing another remote stage call."""

    readback = stage_result.get("readback")
    readback = readback if isinstance(readback, Mapping) else {}
    error = (
        "visual marker readback deferred after the bounded batch host wait "
        "was exhausted"
    )
    stage_result.update(
        {
            "ok": False,
            "status": "readback_deferred_after_timeout",
            "execute": True,
            "published": False,
            "external_write_performed": False,
            "retryable": True,
            "required_action": (
                "reconcile Explore visual markers after the bounded host "
                "readback timeout; do not repeat the full publish"
            ),
            "readback": {
                **readback,
                "ok": False,
                "performed": False,
                "verified": False,
                "source": "batch_host_wait_exhausted",
                "retryable": True,
                "host_wait_exhausted": True,
                "error": error,
            },
            "error": error,
        }
    )


def settle_visual_stage_readbacks(
    *,
    cli_bin: str,
    identity: str,
    stage_targets: Sequence[tuple[dict[str, Any], str]],
    runner: CommandRunner,
    retry_delays: Sequence[float] | None = None,
    command_timeout_seconds: float | None = VISUAL_READBACK_COMMAND_TIMEOUT_SECONDS,
) -> None:
    """Verify every published stage within one shared settling window."""

    effective_retry_delays = tuple(
        VISUAL_READBACK_RETRY_DELAYS_SECONDS
        if retry_delays is None
        else retry_delays
    )
    pending = list(stage_targets)
    for attempt_index in range(len(effective_retry_delays) + 1):
        next_pending: list[tuple[dict[str, Any], str]] = []
        for target_index, (stage_result, whiteboard_token) in enumerate(pending):
            previous = stage_result.get("readback")
            previous = previous if isinstance(previous, Mapping) else {}
            readback = readback_visual_delivery_marker(
                cli_bin=cli_bin,
                identity=identity,
                whiteboard_token=whiteboard_token,
                marker=str(previous.get("expected_marker") or ""),
                runner=runner,
                retry_delays=(),
                command_timeout_seconds=command_timeout_seconds,
            )
            merged = _merge_readback_attempts(previous, readback)
            _apply_stage_readback(stage_result, merged)
            if merged.get("retryable"):
                next_pending.append((stage_result, whiteboard_token))
            if merged.get("host_wait_exhausted"):
                for deferred_result, deferred_token in pending[target_index + 1 :]:
                    deferred_previous = deferred_result.get("readback")
                    deferred_previous = (
                        deferred_previous
                        if isinstance(deferred_previous, Mapping)
                        else {}
                    )
                    deferred = _merge_readback_attempts(
                        deferred_previous,
                        {
                            "ok": False,
                            "schema_version": "loopx_lark_explore_visual_readback_v0",
                            "performed": False,
                            "verified": False,
                            "source": "batch_host_wait_exhausted",
                            "expected_marker": str(
                                deferred_previous.get("expected_marker") or ""
                            ),
                            "observed_marker": None,
                            "remote_text_node_count": 0,
                            "attempts": [],
                            "retryable": True,
                            "host_wait_exhausted": True,
                            "error": (
                                "visual marker readback deferred after the bounded "
                                "batch host wait was exhausted"
                            ),
                        },
                    )
                    _apply_stage_readback(deferred_result, deferred)
                    next_pending.append((deferred_result, deferred_token))
                return
        pending = next_pending
        if not pending:
            break
        if attempt_index < len(effective_retry_delays):
            time.sleep(effective_retry_delays[attempt_index])
