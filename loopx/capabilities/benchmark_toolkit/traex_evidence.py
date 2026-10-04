"""Convert private TraeX JSONL into ATIF and a public-safe route receipt."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .experiment_identity import experiment_token_text as _token
from ...registry import atomic_write_json

TRAE_BENCHMARK_EVIDENCE_SCHEMA_VERSION = "benchmark_trae_evidence_capture_v0"
BENCHMARK_MODEL_ROUTE_RECEIPT_SCHEMA_VERSION = "benchmark_model_route_receipt_v0"
ATIF_SCHEMA_VERSION = "ATIF-v1.7"



def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
            parts.append(str(item["text"]))
    return "\n".join(parts)


def _tool_arguments(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _runtime_route(events: Iterable[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    routes: list[tuple[str, str, str]] = []
    for event in events:
        if event.get("type") != "event_msg":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("type") != "token_count":
            continue
        context = payload.get("context")
        if not isinstance(context, Mapping):
            info = payload.get("info")
            context = info.get("context") if isinstance(info, Mapping) else None
        if not isinstance(context, Mapping):
            continue
        model = str(context.get("model") or "").strip()
        provider = str(context.get("modelProviderId") or "").strip()
        variant = str(context.get("modelBackendVariant") or "").strip()
        if model and provider:
            route = (model, provider, variant)
            if route not in routes:
                routes.append(route)
    return routes


def build_traex_model_route_receipt(
    events: Iterable[Mapping[str, Any]],
    *,
    requested_model: str,
    requested_provider: str = "trae",
) -> dict[str, Any]:
    """Reduce runtime route observations without copying prompts or paths."""

    model = _token(requested_model, field="requested_model")
    provider = _token(requested_provider, field="requested_provider")
    routes = _runtime_route(events)
    if not routes:
        status = "route_requested_not_runtime_audited"
        matched = False
    elif len(routes) != 1:
        status = "runtime_route_ambiguous"
        matched = False
    else:
        observed_model, observed_provider, _variant = routes[0]
        matched = (
            observed_model.casefold() == model.casefold()
            and observed_provider.casefold() == provider.casefold()
        )
        status = "runtime_route_verified" if matched else "runtime_route_mismatch"

    receipt: dict[str, Any] = {
        "schema_version": BENCHMARK_MODEL_ROUTE_RECEIPT_SCHEMA_VERSION,
        "runtime": "traex",
        "requested_model": model,
        "requested_provider": provider,
        "status": status,
        "runtime_audited": bool(routes),
        "matched": matched,
        "observed_route_count": len(routes),
        "raw_content_recorded": False,
        "input_path_recorded": False,
    }
    if len(routes) == 1:
        observed_model, observed_provider, observed_variant = routes[0]
        receipt["observed_model"] = _token(observed_model, field="observed_model")
        receipt["observed_provider"] = _token(
            observed_provider, field="observed_provider"
        )
        if observed_variant:
            receipt["observed_backend_variant"] = _token(
                observed_variant, field="observed_backend_variant"
            )
    return receipt


def _archived_items(events: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    response_items: list[Mapping[str, Any]] = []
    for event in events:
        if event.get("type") != "response_item":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("traex_archive_response_item_invalid")
        response_items.append(payload)
    if response_items:
        return response_items

    items: list[Mapping[str, Any]] = []
    for event in events:
        if event.get("type") != "history_mutation":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        operation = payload.get("operation")
        if operation not in {"append", "replace"}:
            raise ValueError("traex_history_mutation_operation_unsupported")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("traex_history_mutation_items_invalid")
        if not all(isinstance(item, Mapping) for item in raw_items):
            raise ValueError("traex_history_mutation_item_invalid")
        if operation == "replace":
            items = list(raw_items)
        else:
            items.extend(raw_items)
    return items


def _append_archived_steps(
    items: Iterable[Mapping[str, Any]],
    steps: list[dict[str, Any]],
) -> None:
    pending: dict[str, dict[str, Any]] = {}
    for item in items:
        item_type = item.get("type")
        if item_type in {"function_call", "custom_tool_call"}:
            call_id = str(item.get("call_id") or item.get("id") or "")
            if not call_id or call_id in pending:
                raise ValueError("traex_function_call_identity_invalid")
            arguments_field = (
                "input" if item_type == "custom_tool_call" else "arguments"
            )
            step: dict[str, Any] = {
                "step_id": str(len(steps) + 1),
                "source": "agent",
                "message": "",
                "tool_calls": [
                    {
                        "function_name": str(item.get("name") or "unknown"),
                        "arguments": _tool_arguments(item.get(arguments_field) or {}),
                    }
                ],
            }
            steps.append(step)
            pending[call_id] = step
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            call_id = str(item.get("call_id") or "")
            if call_id not in pending:
                raise ValueError("traex_function_call_output_unmatched")
            step = pending.pop(call_id)
            step["observation"] = item.get("output")
        elif item_type == "message":
            role = item.get("role")
            if role not in {"assistant", "developer", "system", "user"}:
                raise ValueError("traex_archive_message_role_unsupported")
            if role == "assistant":
                message = _text_content(item.get("content"))
                if message:
                    steps.append(
                        {
                            "step_id": str(len(steps) + 1),
                            "source": "agent",
                            "message": message,
                            "tool_calls": [],
                        }
                    )
        elif item_type != "reasoning":
            raise ValueError("traex_archive_action_unsupported")
    if pending:
        raise ValueError("traex_function_call_output_missing")


def _append_stdout_steps(
    events: Iterable[Mapping[str, Any]],
    steps: list[dict[str, Any]],
) -> None:
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping):
            raise ValueError("traex_stdout_item_invalid")
        item_type = item.get("type")
        if item_type == "command_execution":
            command = str(item.get("command") or "")
            if not command:
                raise ValueError("traex_stdout_command_missing")
            steps.append(
                {
                    "step_id": str(len(steps) + 1),
                    "source": "agent",
                    "message": "",
                    "tool_calls": [
                        {
                            "function_name": "exec_command",
                            "arguments": {"cmd": command},
                        }
                    ],
                    "observation": {
                        "output": item.get("aggregated_output"),
                        "exit_code": item.get("exit_code"),
                    },
                }
            )
        elif item_type == "file_change":
            changes = item.get("changes")
            if not isinstance(changes, list) or not all(
                isinstance(change, Mapping) for change in changes
            ):
                raise ValueError("traex_stdout_file_changes_invalid")
            steps.append(
                {
                    "step_id": str(len(steps) + 1),
                    "source": "agent",
                    "message": "",
                    "tool_calls": [
                        {
                            "function_name": "apply_patch",
                            "arguments": {"changes": changes},
                        }
                    ],
                    "observation": {"status": item.get("status")},
                }
            )
        elif item_type == "agent_message":
            message = str(item.get("text") or "")
            if message:
                steps.append(
                    {
                        "step_id": str(len(steps) + 1),
                        "source": "agent",
                        "message": message,
                        "tool_calls": [],
                    }
                )
        elif item_type not in {"error", "reasoning"}:
            raise ValueError("traex_stdout_action_unsupported")


def convert_traex_events_to_atif(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Convert TraeX stdout or archived events into a private ATIF trajectory."""

    event_list = list(events)
    steps: list[dict[str, Any]] = []
    archived_items = _archived_items(event_list)
    if archived_items:
        _append_archived_steps(archived_items, steps)
    else:
        _append_stdout_steps(event_list, steps)
    if not steps:
        raise ValueError("traex_trajectory_steps_missing")
    return {
        "schema_version": ATIF_SCHEMA_VERSION,
        "steps": steps,
    }


def _read_jsonl(source: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"traex_jsonl_line_invalid:{line_number}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"traex_jsonl_event_invalid:{line_number}")
            events.append(event)
    if not events:
        raise ValueError("traex_jsonl_empty")
    return events


def _thread_identities(events: Iterable[Mapping[str, Any]]) -> set[str]:
    identities: set[str] = set()
    for event in events:
        if event.get("type") == "thread.started":
            identity = str(event.get("thread_id") or "").strip()
        elif event.get("type") == "session_meta":
            payload = event.get("payload")
            identity = (
                str(payload.get("id") or "").strip()
                if isinstance(payload, Mapping)
                else ""
            )
        else:
            continue
        if identity:
            identities.add(identity)
    return identities


def _verify_route_source_binding(
    source_events: Iterable[Mapping[str, Any]],
    route_events: Iterable[Mapping[str, Any]],
) -> None:
    source_identities = _thread_identities(source_events)
    route_identities = _thread_identities(route_events)
    if (
        len(source_identities) != 1
        or len(route_identities) != 1
        or source_identities != route_identities
    ):
        raise ValueError("traex_route_source_identity_mismatch")


def capture_traex_benchmark_evidence(
    *,
    source_jsonl: str | Path,
    atif_output: str | Path,
    route_receipt_output: str | Path,
    requested_model: str,
    requested_provider: str = "trae",
    route_source_jsonl: str | Path | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Write private ATIF plus a public-safe model route receipt."""

    source = Path(source_jsonl).expanduser()
    events = _read_jsonl(source)
    route_source = (
        Path(route_source_jsonl).expanduser()
        if route_source_jsonl is not None
        else source
    )
    atif = Path(atif_output).expanduser()
    route_path = Path(route_receipt_output).expanduser()
    inputs = {source.resolve(), route_source.resolve()}
    outputs = {atif.resolve(), route_path.resolve()}
    if len(outputs) != 2 or inputs & outputs:
        raise ValueError("traex_evidence_paths_overlap")
    route_events = events if route_source == source else _read_jsonl(route_source)
    if route_source != source:
        _verify_route_source_binding(events, route_events)
    trajectory = convert_traex_events_to_atif(events)
    route_receipt = build_traex_model_route_receipt(
        route_events,
        requested_model=requested_model,
        requested_provider=requested_provider,
    )
    if execute:
        atomic_write_json(atif, trajectory)
        atomic_write_json(route_path, route_receipt)

    return {
        "ok": True,
        "schema_version": TRAE_BENCHMARK_EVIDENCE_SCHEMA_VERSION,
        "status": "captured" if execute else "previewed",
        "source_runtime": "traex",
        "event_count": len(events),
        "route_event_count": len(route_events),
        "route_source_bound": True,
        "step_count": len(trajectory["steps"]),
        "tool_call_count": sum(
            len(step.get("tool_calls") or []) for step in trajectory["steps"]
        ),
        "trajectory_sha256": hashlib.sha256(
            _canonical_json(trajectory).encode("utf-8")
        ).hexdigest(),
        "private_atif_written": execute,
        "route_receipt_written": execute,
        "write_performed": execute,
        "model_route": route_receipt,
        "public_boundary": {
            "raw_content_recorded": False,
            "input_path_recorded": False,
            "output_path_recorded": False,
        },
    }


__all__ = [
    "ATIF_SCHEMA_VERSION",
    "BENCHMARK_MODEL_ROUTE_RECEIPT_SCHEMA_VERSION",
    "TRAE_BENCHMARK_EVIDENCE_SCHEMA_VERSION",
    "build_traex_model_route_receipt",
    "capture_traex_benchmark_evidence",
    "convert_traex_events_to_atif",
]
