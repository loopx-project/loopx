from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...control_plane.digest_envelope import sha256_envelope
from ...extensions.hook_adapters import discover_extension_hook_adapters
from ...extensions.runtime import default_extension_state_file
from ...file_lock import exclusive_file_lock
from ...history import load_registry
from ...registry import atomic_write_json, find_registry_goal
from .machine_defaults import resolve_goal_periodic_report_subscription
from .machine_store import read_periodic_report_machine_defaults
from .presets import build_periodic_report_preset_activation


REQUEST_ACTION_SCHEMA = "periodic_report_request_action_v0"
REQUEST_JOURNAL_ENTRY_SCHEMA = "periodic_report_request_journal_entry_v0"
REQUEST_RECEIPT_SCHEMA = "periodic_report_request_receipt_v0"
SOURCE_BINDING_RECEIPT_SCHEMA = "periodic_report_source_binding_receipt_v0"
SOURCE_SETTLEMENT_RECEIPT_SCHEMA = "periodic_report_source_settlement_receipt_v0"
SOURCE_SETTLEMENT_TERMINAL_STATUS = "terminal_failure"
REQUEST_HOOK_ID = "periodic_report.request"
REQUEST_BIND_PORT = "periodic_report.request.bind_source"
REQUEST_SETTLE_PORT = "periodic_report.request.settle_source"
REQUEST_ADAPTER_PHASE = "capability_action"
_REQUEST_FILE_RE = re.compile(r"^prq_[0-9a-f]{64}\.json$")
_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")

SourceBinder = Callable[..., Mapping[str, Any]]
SourceSettler = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class PeriodicReportRequestAdapter:
    adapter_id: str
    bind_source: SourceBinder
    settle_source: SourceSettler


@dataclass(frozen=True, slots=True)
class PeriodicReportRequestPorts:
    adapters: Mapping[str, PeriodicReportRequestAdapter]

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.adapters))

    def select_source_adapter(
        self, adapter_id: str | None
    ) -> PeriodicReportRequestAdapter:
        selected_id = str(adapter_id or "").strip()
        if selected_id:
            selected = self.adapters.get(selected_id)
            if selected is None:
                raise ValueError(
                    "periodic-report request source adapter is unavailable: "
                    f"{selected_id}"
                )
            return selected
        if len(self.adapters) == 1:
            return next(iter(self.adapters.values()))
        if not self.adapters:
            raise ValueError("periodic-report request source adapter is unavailable")
        raise ValueError(
            "periodic-report request source adapter is ambiguous; "
            "select one with --source-adapter-id"
        )

    def settlement_adapter(
        self, adapter_id: object
    ) -> PeriodicReportRequestAdapter | None:
        owner_id = str(adapter_id or "").strip()
        return self.adapters.get(owner_id) if owner_id else None


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_envelope(encoded)


def _timestamp(value: object, label: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_ref(value: object) -> str:
    ref = str(value or "").strip()
    if not ref or len(ref) > 256 or "\x00" in ref or "\n" in ref or "\r" in ref:
        raise ValueError("periodic-report source-ref must be a bounded opaque value")
    return ref


def _request_id(
    *, goal_id: str, agent_id: str, source_adapter_id: str, source_ref: str
) -> str:
    digest = hashlib.sha256(
        f"{goal_id}\0{agent_id}\0{source_adapter_id}\0{source_ref}".encode("utf-8")
    ).hexdigest()
    return f"prq_{digest}"


def _request_dir(runtime_root: Path, goal_id: str) -> Path:
    return runtime_root / "goals" / goal_id / "periodic_report_requests"


def _request_path(runtime_root: Path, goal_id: str, request_id: str) -> Path:
    if not _REQUEST_FILE_RE.fullmatch(f"{request_id}.json"):
        raise ValueError("periodic-report request id is invalid")
    return _request_dir(runtime_root, goal_id) / f"{request_id}.json"


def discover_periodic_report_request_ports(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    extension_state_file: Path | None = None,
) -> PeriodicReportRequestPorts:
    discovery = discover_extension_hook_adapters(
        state_file=(extension_state_file or default_extension_state_file(runtime_root)),
        phase=REQUEST_ADAPTER_PHASE,
        capability_id="periodic-report",
        target_hook_id=REQUEST_HOOK_ID,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    adapter_ports: dict[str, dict[str, SourceBinder | SourceSettler]] = {}
    ambiguous_adapter_ids: set[str] = set()
    for binding in discovery.ports:
        ports = adapter_ports.setdefault(binding.adapter_id, {})
        if binding.port_name in ports:
            ambiguous_adapter_ids.add(binding.adapter_id)
        ports[binding.port_name] = binding.handler
    complete = {
        adapter_id: PeriodicReportRequestAdapter(
            adapter_id=adapter_id,
            bind_source=ports[REQUEST_BIND_PORT],
            settle_source=ports[REQUEST_SETTLE_PORT],
        )
        for adapter_id, ports in sorted(adapter_ports.items())
        if adapter_id not in ambiguous_adapter_ids
        and set(ports) == {REQUEST_BIND_PORT, REQUEST_SETTLE_PORT}
    }
    return PeriodicReportRequestPorts(adapters=complete)


def _normalize_source_receipt(
    value: object,
    *,
    goal_id: str,
    agent_id: str,
    source_ref: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("periodic-report source binding returned no receipt")
    expected = {
        "schema_version",
        "provider",
        "goal_id",
        "agent_id",
        "source_ref",
        "source_digest",
        "observed_at",
        "requester_kind",
        "addressing_source",
        "binding_revision",
        "raw_content_returned",
        "external_writes_performed",
    }
    if set(value) != expected:
        raise ValueError("periodic-report source binding receipt fields are invalid")
    if (
        value.get("schema_version") != SOURCE_BINDING_RECEIPT_SCHEMA
        or value.get("goal_id") != goal_id
        or value.get("agent_id") != agent_id
        or value.get("source_ref") != source_ref
        or value.get("requester_kind") != "user"
        or value.get("addressing_source") not in {"provider_mention", "verified_reply"}
        or value.get("raw_content_returned") is not False
        or value.get("external_writes_performed") is not False
    ):
        raise ValueError("periodic-report source binding receipt is invalid")
    for key in ("provider", "source_digest", "binding_revision"):
        if not str(value.get(key) or "").strip():
            raise ValueError(f"periodic-report source binding receipt requires {key}")
    normalized = dict(value)
    normalized["observed_at"] = _timestamp(value.get("observed_at"), "observed_at")
    expected_digest = _digest(
        {
            "provider": normalized["provider"],
            "goal_id": goal_id,
            "agent_id": agent_id,
            "source_ref": source_ref,
            "observed_at": normalized["observed_at"],
            "requester_kind": "user",
            "addressing_source": normalized["addressing_source"],
            "binding_revision": normalized["binding_revision"],
        }
    )
    if normalized["source_digest"] != expected_digest:
        raise ValueError("periodic-report source binding digest is invalid")
    return normalized


def _request_profile(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str
) -> tuple[dict[str, str], dict[str, Any]]:
    registry = load_registry(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if not isinstance(goal, Mapping):
        raise ValueError("periodic-report Goal is not registered")
    if agent_id not in registered_agent_ids_for_goal(goal):
        raise ValueError("periodic-report request Agent is not registered")
    subscription = resolve_goal_periodic_report_subscription(
        goal,
        read_periodic_report_machine_defaults(runtime_root),
    )
    if subscription.get("enabled") is not True:
        raise ValueError("periodic-report subscription is disabled")
    activation = build_periodic_report_preset_activation(
        str(subscription.get("profile_preset") or "")
    )
    profile = activation.get("profile")
    if activation.get("active") is not True or not isinstance(profile, Mapping):
        raise ValueError("periodic-report profile is inactive")
    trigger_policy = profile.get("trigger_policy")
    if not isinstance(trigger_policy, Mapping) or "manual" not in set(
        trigger_policy.get("enabled_kinds") or []
    ):
        raise ValueError("periodic-report profile does not allow typed requests")
    return (
        {
            "profile_id": str(profile.get("profile_id") or ""),
            "profile_version": str(profile.get("profile_version") or ""),
            "profile_digest": str(activation.get("profile_digest") or ""),
        },
        dict(trigger_policy),
    )


def _load_request_entry(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != REQUEST_JOURNAL_ENTRY_SCHEMA
        or value.get("status") not in {"pending", "settled", "settlement_failed"}
        or value.get("request_id") != path.stem
        or not isinstance(value.get("source_receipt"), Mapping)
        or not isinstance(value.get("profile_ref"), Mapping)
        or not isinstance(value.get("trigger_policy"), Mapping)
        or (
            value.get("status") != "pending"
            and not isinstance(value.get("settlement"), Mapping)
        )
    ):
        return None
    return value


def _matching_request_entries(
    *, runtime_root: Path, goal_id: str, agent_id: str, source_ref: str
) -> list[dict[str, Any]]:
    directory = _request_dir(runtime_root, goal_id)
    if not directory.is_dir():
        return []
    matches: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not _REQUEST_FILE_RE.fullmatch(path.name):
            continue
        entry = _load_request_entry(path)
        source_receipt = entry.get("source_receipt") if entry is not None else None
        if (
            entry is not None
            and entry.get("goal_id") == goal_id
            and entry.get("agent_id") == agent_id
            and isinstance(source_receipt, Mapping)
            and source_receipt.get("source_ref") == source_ref
        ):
            matches.append(entry)
    return matches


def _validate_request_replay(
    entry: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    source_adapter_id: str,
    source_ref: str,
    request_id: str,
) -> None:
    source_receipt = entry.get("source_receipt")
    if (
        entry.get("request_id") != request_id
        or entry.get("goal_id") != goal_id
        or entry.get("agent_id") != agent_id
        or entry.get("adapter_id") != source_adapter_id
        or not isinstance(source_receipt, Mapping)
        or source_receipt.get("source_ref") != source_ref
    ):
        raise ValueError("periodic-report request journal identity drifted")


def record_periodic_report_request(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    source_ref: str,
    request_ports: PeriodicReportRequestPorts | None,
    source_adapter_id: str | None,
    execute: bool,
) -> dict[str, Any]:
    if not _IDENTITY_RE.fullmatch(goal_id) or not _IDENTITY_RE.fullmatch(agent_id):
        raise ValueError("periodic-report request Goal/Agent identity is invalid")
    opaque_ref = _source_ref(source_ref)
    selected_adapter_id = str(source_adapter_id or "").strip()
    existing: dict[str, Any] | None = None
    if not selected_adapter_id:
        replay_candidates = _matching_request_entries(
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            source_ref=opaque_ref,
        )
        if len(replay_candidates) > 1:
            raise ValueError(
                "periodic-report request source adapter is ambiguous; "
                "select one with --source-adapter-id"
            )
        if replay_candidates:
            existing = replay_candidates[0]
            selected_adapter_id = str(existing.get("adapter_id") or "").strip()
        elif request_ports is not None:
            selected_adapter_id = request_ports.select_source_adapter(None).adapter_id
    if not selected_adapter_id:
        raise ValueError("periodic-report request source adapter is unavailable")
    request_id = _request_id(
        goal_id=goal_id,
        agent_id=agent_id,
        source_adapter_id=selected_adapter_id,
        source_ref=opaque_ref,
    )
    path = _request_path(runtime_root, goal_id, request_id)
    existing = existing or (_load_request_entry(path) if path.is_file() else None)
    if path.is_file() and existing is None:
        raise ValueError("periodic-report request journal entry is invalid")
    if existing is not None:
        _validate_request_replay(
            existing,
            goal_id=goal_id,
            agent_id=agent_id,
            source_adapter_id=selected_adapter_id,
            source_ref=opaque_ref,
            request_id=request_id,
        )
        return {
            "ok": True,
            "schema_version": REQUEST_RECEIPT_SCHEMA,
            "status": "already_requested",
            "request_id": request_id,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "journal_status": existing["status"],
            "write_performed": False,
            "raw_content_returned": False,
            "external_writes_performed": False,
        }
    if request_ports is None:
        raise ValueError("periodic-report request source adapter is unavailable")
    source_adapter = request_ports.select_source_adapter(selected_adapter_id)
    profile_ref, trigger_policy = _request_profile(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    source_receipt = _normalize_source_receipt(
        source_adapter.bind_source(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            source_ref=opaque_ref,
        ),
        goal_id=goal_id,
        agent_id=agent_id,
        source_ref=opaque_ref,
    )
    entry = {
        "schema_version": REQUEST_JOURNAL_ENTRY_SCHEMA,
        "request_id": request_id,
        "status": "pending",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "requested_at": source_receipt["observed_at"],
        "adapter_id": source_adapter.adapter_id,
        "source_receipt": source_receipt,
        "profile_ref": profile_ref,
        "trigger_policy": trigger_policy,
    }
    write_performed = False
    journal_status = "pending"
    if execute:
        with exclusive_file_lock(path, operation="periodic_report_request"):
            concurrent = _load_request_entry(path) if path.is_file() else None
            if path.is_file() and concurrent is None:
                raise ValueError("periodic-report request journal entry is invalid")
            if concurrent is None:
                atomic_write_json(path, entry)
                write_performed = True
            else:
                _validate_request_replay(
                    concurrent,
                    goal_id=goal_id,
                    agent_id=agent_id,
                    source_adapter_id=selected_adapter_id,
                    source_ref=opaque_ref,
                    request_id=request_id,
                )
                journal_status = str(concurrent["status"])
    return {
        "ok": True,
        "schema_version": REQUEST_RECEIPT_SCHEMA,
        "status": (
            "preview"
            if not execute
            else ("accepted" if write_performed else "already_requested")
        ),
        "request_id": request_id,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "journal_status": journal_status,
        "write_performed": write_performed,
        "raw_content_returned": False,
        "external_writes_performed": False,
    }


def periodic_report_request_intents(
    *, runtime_root: Path, goal_id: str, agent_id: str
) -> list[dict[str, Any]]:
    directory = _request_dir(runtime_root, goal_id)
    if not directory.is_dir():
        return []
    intents: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not _REQUEST_FILE_RE.fullmatch(path.name):
            continue
        entry = _load_request_entry(path)
        if (
            entry is None
            or entry.get("status") != "pending"
            or entry.get("goal_id") != goal_id
            or entry.get("agent_id") != agent_id
        ):
            continue
        source = entry["source_receipt"]
        request = {
            "schema_version": REQUEST_ACTION_SCHEMA,
            "request_id": entry["request_id"],
            "goal_id": goal_id,
            "agent_id": agent_id,
            "requested_at": entry["requested_at"],
            "source_digest": source["source_digest"],
            "requester_kind": source["requester_kind"],
            "addressing_source": source["addressing_source"],
        }
        intents.append(
            {
                "schema_version": "loopx_capability_intent_v0",
                "intent_kind": "periodic_report.trigger_evaluation",
                "idempotency_key": f"periodic-report:request:{entry['request_id']}",
                "source_receipt_id": entry["request_id"],
                "payload": {
                    "schema_version": "periodic_report_trigger_evaluation_intent_v0",
                    "report_request": request,
                    "profile_ref": dict(entry["profile_ref"]),
                    "trigger_policy": dict(entry["trigger_policy"]),
                    "generation_authorized": False,
                    "external_delivery_authorized": False,
                },
                "requested_write_scope": [],
            }
        )
    return intents


def request_entry_for_intent(
    *, runtime_root: Path, goal_id: str, agent_id: str, intent: Mapping[str, Any]
) -> dict[str, Any] | None:
    payload = intent.get("payload")
    request = payload.get("report_request") if isinstance(payload, Mapping) else None
    if not isinstance(request, Mapping):
        return None
    request_id = str(request.get("request_id") or "")
    try:
        path = _request_path(runtime_root, goal_id, request_id)
    except ValueError:
        return None
    entry = _load_request_entry(path)
    if (
        entry is None
        or entry.get("goal_id") != goal_id
        or entry.get("agent_id") != agent_id
        or entry.get("status") != "pending"
    ):
        return None
    return entry


def settle_periodic_report_request(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    intent: Mapping[str, Any],
    request_ports: PeriodicReportRequestPorts | None,
    execute: bool,
) -> dict[str, Any]:
    entry = request_entry_for_intent(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        intent=intent,
    )
    if entry is None:
        return {
            "ok": True,
            "schema_version": SOURCE_SETTLEMENT_RECEIPT_SCHEMA,
            "status": "not_applicable",
            "write_performed": False,
            "external_writes_performed": False,
        }
    source_adapter = (
        request_ports.settlement_adapter(entry.get("adapter_id"))
        if request_ports is not None
        else None
    )
    if source_adapter is None:
        return {
            "ok": False,
            "schema_version": SOURCE_SETTLEMENT_RECEIPT_SCHEMA,
            "status": "adapter_unavailable",
            "write_performed": False,
            "external_writes_performed": False,
        }
    result = source_adapter.settle_source(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        source_receipt=dict(entry["source_receipt"]),
        execute=execute,
    )
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != SOURCE_SETTLEMENT_RECEIPT_SCHEMA
        or result.get("status")
        not in {"preview", "failed", "settled", SOURCE_SETTLEMENT_TERMINAL_STATUS}
        or (
            result.get("status") in {"preview", "settled"}
            and result.get("ok") is not True
        )
        or (
            result.get("status") in {"failed", SOURCE_SETTLEMENT_TERMINAL_STATUS}
            and result.get("ok") is not False
        )
        or result.get("external_writes_performed") is not False
        or result.get("raw_content_returned") is not False
        or (
            result.get("status") == SOURCE_SETTLEMENT_TERMINAL_STATUS
            and not _IDENTITY_RE.fullmatch(str(result.get("failure_code") or ""))
        )
    ):
        raise ValueError("periodic-report source settlement receipt is invalid")
    normalized = dict(result)
    if (
        execute
        and (
            (normalized.get("ok") is True and normalized.get("status") == "settled")
            or (
                normalized.get("ok") is False
                and normalized.get("status") == SOURCE_SETTLEMENT_TERMINAL_STATUS
            )
        )
    ):
        request_id = str(entry["request_id"])
        path = _request_path(runtime_root, goal_id, request_id)
        with exclusive_file_lock(path, operation="periodic_report_request_settlement"):
            current = _load_request_entry(path)
            if current is not None and current.get("status") == "pending":
                current["status"] = (
                    "settled"
                    if normalized.get("status") == "settled"
                    else "settlement_failed"
                )
                current["settlement"] = {
                    key: value
                    for key, value in normalized.items()
                    if key not in {"source_ref", "content"}
                }
                atomic_write_json(path, current)
        normalized["write_performed"] = True
    return normalized


__all__ = [
    "PeriodicReportRequestAdapter",
    "PeriodicReportRequestPorts",
    "REQUEST_ACTION_SCHEMA",
    "REQUEST_ADAPTER_PHASE",
    "REQUEST_BIND_PORT",
    "REQUEST_HOOK_ID",
    "REQUEST_JOURNAL_ENTRY_SCHEMA",
    "REQUEST_RECEIPT_SCHEMA",
    "REQUEST_SETTLE_PORT",
    "SOURCE_BINDING_RECEIPT_SCHEMA",
    "SOURCE_SETTLEMENT_RECEIPT_SCHEMA",
    "SOURCE_SETTLEMENT_TERMINAL_STATUS",
    "discover_periodic_report_request_ports",
    "periodic_report_request_intents",
    "record_periodic_report_request",
    "request_entry_for_intent",
    "settle_periodic_report_request",
]
