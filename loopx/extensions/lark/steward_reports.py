"""Full-body report return through the existing original private reply owner.

Only the existing outbound pool calls this adapter. It never creates a model
Turn, source event, listener, recipient or report subscription.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from ...chat_store import _read_json
from ...file_lock import try_exclusive_file_lock
from ...file_lock import exclusive_file_lock
from ...registry import atomic_write_json
from ...capabilities.native_chat.steward_reports import StewardReports
from ...capabilities.native_chat.steward_turn import native_steward_client_id
from ...capabilities.periodic_report.cadence_journal import read_cadence_journal
from ...capabilities.periodic_report.private_generation import attempt_directory, validate_private_generation
from ...capabilities.periodic_report.incremental import commit_periodic_report_publication_cursor


class LarkStewardReports:
    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self.controller = transport.core.controller
        self.reports = StewardReports(self.controller)

    def _original_route(self, binding: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        route = self.transport.owner_attention._route(binding)
        native = self.transport.core.read_request(route["request_ref"])
        if native.get("session_id") != actor["session_id"] or route["source"]["source_ref"] != actor["source_ref"]:
            raise ValueError("the private source does not belong to the original reporting steward")
        return route

    def route_proof(self, binding: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        self._original_route(binding, actor)
        return {"binding_id": binding["binding_id"], "source_ref": actor["source_ref"],
            "session_id": actor["session_id"], "provider_ref": binding["provider_ref"],
            "operator_ref": binding["operator_ref"]}

    def pending(self) -> bool:
        return any(row.get("managed_actor") and self.reports.health(row["binding_id"]).get("enabled")
            for row in self.transport.bindings.read()["bindings"])

    def _committed_turn(self, actor: dict[str, Any], window: dict[str, Any]) -> dict[str, Any] | None:
        # Same stable client identity as the original native host. Reading a
        # retained completion/event never replays an unknown or failed Turn.
        client = native_steward_client_id(actor, "report." + window["window_id"])
        turn = self.controller.store.turn_for_client(actor["session_id"], client)
        if not turn:
            return None
        request = turn.get("loopx_request") or {}
        if (request.get("work_kind") != "report" or request.get("actor") != actor
                or request.get("report_window_id") != window["window_id"]
                or request.get("report_subscription_revision") != window["subscription_revision"]):
            raise ValueError("the original report Turn changed identity")
        native = (turn.get("response") or {}).get("native_loopx") or {}
        if native.get("status") == "committed":
            return turn
        # A host response can be lost after native settlement. The original
        # immutable result event is also sufficient; no second model is run.
        events = self.controller.store.events_after(actor["session_id"], turn["turn_id"], None)
        if any(row.get("kind") == "native_loopx.result" and (row.get("payload") or {}).get("status") == "committed"
                for row in events):
            return turn
        return None

    def reconcile(self) -> int:
        verified = 0
        for binding in self.transport.bindings.read()["bindings"]:
            actor = binding.get("managed_actor")
            if not actor:
                continue
            try:
                subscription = self.reports.subscription(binding["binding_id"])
                if subscription is None:
                    continue
                journal = read_cadence_journal(runtime_root=self.reports.root, goal_id=actor["goal_id"])
                if not journal or journal["publication"]:
                    continue
                window = journal["window"]
                folder = attempt_directory(self.reports.root, window)
                if not (folder / "private-generation.json").exists():
                    continue
                path = folder / "private-delivery.json"
                with try_exclusive_file_lock(folder / "delivery.lock", operation="return_private_periodic_report") as acquired:
                    if not acquired:
                        continue
                    if not self._committed_turn(actor, window):
                        continue
                    generation = validate_private_generation(folder)
                    def current():
                        return self.reports.delivery_current(binding["binding_id"], window)
                    if not current():
                        continue
                    if path.exists():
                        record = _read_json(path)
                        if record.get("publication_cursor"):
                            continue
                        route = self.transport.owner_attention._route(binding, record["original_route"])
                    else:
                        route = self._original_route(binding, actor)
                        original = _read_json(self.transport.root / (route["request_ref"] + ".json"))
                        # Preserve the original Core correlation and provider
                        # source; only this report's reply receipt is separate.
                        record = {k: deepcopy(v) for k, v in original.items()
                            if k not in {"deliveries", "stream", "recovery_streams", "context_recovery"}}
                        record.update(deliveries={}, original_route=route, report_window_id=window["window_id"],
                            report_generation_id=generation["generation_id"], delivery_status="pending")
                        atomic_write_json(path, record)
                    if (record["report_window_id"] != window["window_id"]
                            or record["report_generation_id"] != generation["generation_id"]
                            or record["original_route"] != route):
                        raise ValueError("the frozen report return changed its original audience")
                    body = (folder / "report.md").read_text(encoding="utf-8")
                    phase = "periodic_report." + window["window_id"]
                    delivered = self.transport._deliver(path, record, phase, body,
                        inbox=lambda: self.transport._inbox(record), authorization_check=current)
                    record["delivery_status"] = "delivered" if delivered else "unconfirmed"
                    atomic_write_json(path, record)
                    if not delivered or not current():
                        continue
                    proof = record["deliveries"][phase]
                    if proof.get("verified") is not True:
                        raise ValueError("the complete report reply has no verified provider receipt")
                    publication_id = "private-report-" + hashlib.sha256(json.dumps(
                        [window["window_id"], generation["generation_id"], proof],
                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    candidate = _read_json(folder / "publication-candidate.json")
                    with self.controller.lock, exclusive_file_lock(self.transport.bindings.path,
                            operation="commit_original_report_publication"):
                        if not current():
                            continue
                        cursor = commit_periodic_report_publication_cursor(runtime_root=self.reports.root,
                            candidate=candidate, publication_id=publication_id,
                            delivered_at=datetime.now(timezone.utc).isoformat(), covered_until=window["due_at"])
                    record.update(publication_cursor=cursor, delivery_status="delivered")
                    atomic_write_json(path, record)
                    verified += 1
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                # Existing generation, failed/unknown sends and source failures
                # remain visible. No alternate App, route or model is selected.
                continue
        return verified
