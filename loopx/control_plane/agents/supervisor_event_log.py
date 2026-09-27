"""Private experimental supervisor log, independent of Todo state authority.

Python owns locked JSONL publication using the existing durable file primitives.
TypeScript owns append identity and sequence admission. Only proposals and host
receipts are records here; there is no replay into Goal state or Markdown.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..effect_runtime import effect_runtime_result
from ..runtime.time import now_utc_iso
from ..todos.active_state_editing import atomic_write_state_text, verify_state_text_durable

SUPERVISOR_EVENT_SCHEMA = "supervisor_log_event_v0"
LOCAL_PRIVATE_PRIVACY = "local_private"
SUPERVISOR_PROPOSED = "supervisor_proposed"
SUPERVISOR_RECEIPT_RECORDED = "supervisor_receipt_recorded"


class SupervisorEventError(ValueError):
    """Invalid supervisor log; never treat unreadable history as empty."""


class SupervisorEventConflictError(SupervisorEventError):
    """An existing identity names different semantic content."""


class SupervisorEventCommitUnknownError(SupervisorEventError):
    """Publication may have landed; inspect before repeating business work."""


def event_identity(event: dict[str, Any]) -> dict[str, str]:
    semantic = {key: value for key, value in event.items()
        if key not in {"recorded_at", "append_sequence"}}
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"event_id": event["event_id"], "fingerprint": hashlib.sha256(encoded.encode()).hexdigest()}


def make_supervisor_event(*, event_id: str, goal_id: str, event_type: str,
    refs: dict[str, Any], payload: dict[str, Any], recorded_at: str | None = None,
) -> dict[str, Any]:
    return {"schema_version": SUPERVISOR_EVENT_SCHEMA, "event_id": event_id,
        "goal_id": goal_id, "event_type": event_type, "recorded_at": recorded_at or now_utc_iso(),
        "privacy": LOCAL_PRIVATE_PRIVACY, "refs": refs, "payload": payload}


def _validate_event(event: Any, *, stored: bool) -> dict[str, Any]:
    if not isinstance(event, dict) or event.get("schema_version") != SUPERVISOR_EVENT_SCHEMA:
        raise SupervisorEventError("unsupported experimental supervisor log schema")
    if event.get("event_type") not in {SUPERVISOR_PROPOSED, SUPERVISOR_RECEIPT_RECORDED}:
        raise SupervisorEventError("supervisor log accepts only proposals and receipts")
    if event.get("privacy") != LOCAL_PRIVATE_PRIVACY:
        raise SupervisorEventError("supervisor log must remain local_private")
    for field in ("event_id", "goal_id", "recorded_at"):
        if not isinstance(event.get(field), str) or not event[field].strip():
            raise SupervisorEventError(f"supervisor event requires {field}")
    for field in ("refs", "payload"):
        if not isinstance(event.get(field), dict):
            raise SupervisorEventError(f"supervisor event {field} must be an object")
    if stored:
        seq = event.get("append_sequence")
        if type(seq) is not int or not 0 < seq <= 2**53 - 1:
            raise SupervisorEventError("supervisor sequence must be a positive safe integer")
    return dict(event)


@dataclass
class SupervisorEventStore:
    path: Path

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        ids: set[str] = set()
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = _validate_event(json.loads(line), stored=True)
            except (ValueError, TypeError) as exc:
                raise SupervisorEventError(f"invalid supervisor log row {line_number}: {exc}") from exc
            if event["event_id"] in ids or event["append_sequence"] != len(events) + 1:
                raise SupervisorEventError("supervisor log identities/sequences are inconsistent")
            if events and event["goal_id"] != events[0]["goal_id"]:
                raise SupervisorEventError("supervisor log mixes Goals")
            ids.add(event["event_id"])
            events.append(event)
        return events

    def record_locked(self, event: dict[str, Any], *, execute: bool,
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Caller holds the log lock across read, decision checks and this write."""
        requested = _validate_event(event, stored=False)
        if events and requested["goal_id"] != events[0]["goal_id"]:
            raise SupervisorEventConflictError("supervisor log belongs to another Goal")
        prior = next((item for item in events if item["event_id"] == requested["event_id"]), None)
        plan = effect_runtime_result("agent.supervisor.plan_append", {
            "schema_version": "loopx_supervisor_event_append_plan_v0",
            "last_sequence": len(events),
            "existing": {**event_identity(prior), "append_sequence": prior["append_sequence"]} if prior else None,
            "event": event_identity(requested),
        })
        if plan.get("status") != "planned":
            raise SupervisorEventConflictError(str(plan.get("reason_code") or "invalid supervisor append plan"))
        requested["append_sequence"] = plan["append_sequence"]
        if not execute:
            return prior or requested, False
        try:
            if prior is not None:
                verify_state_text_durable(self.path, self.path.read_bytes().decode("utf-8"))
                return prior, False
            text = "".join(json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n"
                for item in [*events, requested])
            atomic_write_state_text(self.path, text)
        except OSError as error:
            raise SupervisorEventCommitUnknownError(
                "supervisor publication uncertain; inspect the original decision/receipt before retrying"
            ) from error
        return requested, True
