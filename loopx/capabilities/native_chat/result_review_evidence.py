"""Canonical result-review facts; eligibility remains in the typed Core owner."""
from copy import deepcopy
from datetime import datetime
from typing import Any


def related_review_basis(value: dict[str, Any]) -> dict[str, Any]:
    """Host event-cache revisions are not result evidence or JS-safe integers.

    Preserve every semantic field, including response, source, state and
    budget; exclude only ChatSessionStore's derived filesystem cache stamp
    at its two known Turn locations, including persisted older reservations.
    """
    basis = deepcopy(value)
    basis["turn"].pop("event_compaction_revision", None)
    basis["request"]["context_recovery"]["original_turn"].pop("event_compaction_revision", None)
    return basis


def collect_related_result_review(core: Any, row: dict[str, Any], original: dict[str, Any], *,
                           discovery_reads: Any | None = None) -> dict[str, Any] | None:
    """Collect same-question evidence, never equate distinct source identities.

    Core qualifies the complete correlation. Discovery shares only a local
    history index; admission and dispatch read the actual records afresh.
    """
    store = core.controller.store
    messages = (discovery_reads.matching_user_messages(row["session_id"], row["message"])
        if discovery_reads is not None else [item for item in store.messages(row["session_id"], strict=True)
            if item.get("role") == "user" and item.get("text") == row["message"]])
    pinned = ((row.get("context_recovery") or {}).get("related_result_review") or {}).get("request", {}).get("request_ref")
    for message in sorted(messages, key=lambda item: item["created_at"], reverse=True):
        if (message.get("turn_id") == original["turn_id"]
                or datetime.fromisoformat(message["created_at"]) <= datetime.fromisoformat(original["completed_at"])):
            continue
        reviewed = store.load_turn(row["session_id"], message.get("turn_id", ""))
        if not reviewed or reviewed.get("status") != "completed":
            continue
        reservation = core.recovery_context({"session_id": row["session_id"]}, reviewed)
        if not reservation or not reservation.get("unfinished_work") or reservation.get("legacy_result_review"):
            continue
        if pinned is not None and reservation["request_ref"] != pinned:
            continue
        sibling = core.read_request(reservation["request_ref"])
        if (sibling["request_ref"] == row["request_ref"] or sibling.get("message") != row["message"]
                or sibling.get("binding_id") != row["binding_id"] or sibling.get("source") != row["source"]
                or sibling.get("turn_id") != reviewed["turn_id"] or not sibling.get("delivery_verified")):
            continue
        events = store.events_after(row["session_id"], reviewed["turn_id"], None)
        events += store.events_after(row["session_id"], reservation["failed_turn_id"], None)
        return related_review_basis({"request": {key: sibling.get(key) for key in ("request_ref", "binding_id", "source", "session_id",
                    "message", "origin", "command", "status", "turn_id", "delivery_verified", "context_recovery")},
            "turn": reviewed, "steering_observed": any(event.get("kind") == "turn.steered" for event in events)})
    return None

