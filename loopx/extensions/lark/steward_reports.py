"""Full-body report return through the existing original private reply owner.

Only the existing outbound pool calls this adapter. It never creates a model
Turn, source event, listener, recipient or report subscription.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Protocol

from ...chat_store import _read_json
from ...file_lock import exclusive_file_lock, try_exclusive_file_lock
from ...registry import atomic_write_json
from ...capabilities.native_chat.steward_monitor import steward_monitor_delivery_dir
from ...capabilities.native_chat.steward_reports import StewardReports
from ...capabilities.native_chat.steward_turn import report_turn
from ...capabilities.native_chat.steward_turn_common import (
    StewardBindingsProtocol,
    StewardControllerProtocol,
    as_dict,
    as_dict_list,
    as_mapping,
    as_optional_dict,
)
from ...capabilities.periodic_report.cadence_journal import read_cadence_journal
from ...capabilities.periodic_report.incremental import commit_periodic_report_publication_cursor
from ...capabilities.periodic_report.private_generation import attempt_directory, validate_private_generation


class _LarkOwnerAttentionProtocol(Protocol):
    def _route(
        self,
        binding: Mapping[str, object],
        saved: object = None,
        *,
        session_id: str,
        source_ref: str,
    ) -> dict[str, object]: ...


class _LarkCoreProtocol(Protocol):
    @property
    def controller(self) -> StewardControllerProtocol: ...

    def read_request(self, request_ref: str) -> dict[str, object]: ...


class _LarkTransportProtocol(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def core(self) -> _LarkCoreProtocol: ...

    @property
    def bindings(self) -> StewardBindingsProtocol: ...

    @property
    def owner_attention(self) -> _LarkOwnerAttentionProtocol: ...

    def _inbox(self, record: Mapping[str, object]) -> object: ...

    def _deliver(
        self,
        path: Path,
        record: dict[str, object],
        phase: str,
        body: str,
        *,
        inbox: Callable[[], object],
        authorization_check: Callable[[], bool],
        reply_placement: str | None = None,
        finalize_source: bool = True,
    ) -> bool: ...


class LarkStewardReports:
    def __init__(self, transport: _LarkTransportProtocol) -> None:
        self.transport: _LarkTransportProtocol = transport
        self.controller: StewardControllerProtocol = transport.core.controller
        self.reports: StewardReports = StewardReports(self.controller)

    def _original_route(
        self,
        binding: Mapping[str, object],
        actor: Mapping[str, object],
        saved: object = None,
    ) -> dict[str, object]:
        route = as_dict(
            self.transport.owner_attention._route(
                binding,
                saved,
                session_id=str(actor["session_id"]),
                source_ref=str(actor["source_ref"]),
            )
        )
        native = as_mapping(self.transport.core.read_request(str(route["request_ref"])))
        if (
            native.get("session_id") != actor["session_id"]
            or as_mapping(route.get("source")).get("source_ref") != actor["source_ref"]
        ):
            raise ValueError("the private source does not belong to the original reporting steward")
        return route

    def route_proof(self, binding: Mapping[str, object], actor: Mapping[str, object]) -> dict[str, object]:
        self._original_route(binding, actor)
        return {
            "binding_id": binding["binding_id"],
            "source_ref": actor["source_ref"],
            "session_id": actor["session_id"],
            "provider_ref": binding["provider_ref"],
            "operator_ref": binding["operator_ref"],
        }

    def _pending_monitor_report_paths(self, actor: Mapping[str, object]) -> list[Path]:
        goal_id = str(actor.get("goal_id") or "").strip()
        if not goal_id:
            return []
        try:
            delivery_dir = steward_monitor_delivery_dir(self.reports.root, goal_id)
        except ValueError:
            return []
        if not delivery_dir.is_dir():
            return []
        pending: list[Path] = []
        for path in sorted(delivery_dir.glob("*.json")):
            try:
                item = _read_json(path)
            except (OSError, ValueError):
                continue
            if (
                isinstance(item, dict)
                and item.get("schema_version") == "steward_monitor_report_delivery_v0"
                and item.get("goal_id") == goal_id
                and item.get("agent_id") == actor.get("agent_id")
                and item.get("session_id") == actor.get("session_id")
                and item.get("delivery_status") in {"pending", "unconfirmed"}
                and str(item.get("markdown") or "").strip()
            ):
                pending.append(path)
        return pending

    def pending(self) -> bool:
        return any(
            row.get("managed_actor")
            and (
                self.reports.health(str(row["binding_id"])).get("enabled")
                or self._pending_monitor_report_paths(as_mapping(row["managed_actor"]))
            )
            for row in as_dict_list(self.transport.bindings.read().get("bindings"))
        )

    def _committed_turn(self, actor: Mapping[str, object], window: Mapping[str, object]) -> dict[str, object] | None:
        # Same stable client identity as the original native host. Reading a
        # retained completion/event never replays an unknown or failed Turn.
        turn = report_turn(self.controller.store, actor, window)
        if not turn:
            return None
        request = as_mapping(turn.get("loopx_request"))
        if (
            request.get("work_kind") != "report"
            or request.get("actor") != actor
            or request.get("report_window_id") != window["window_id"]
            or request.get("report_subscription_revision") != window["subscription_revision"]
        ):
            raise ValueError("the original report Turn changed identity")
        native = as_mapping(as_mapping(turn.get("response")).get("native_loopx"))
        if native.get("status") == "committed":
            return turn
        # A host response can be lost after native settlement. The original
        # immutable result event is also sufficient; no second model is run.
        events = self.controller.store.events_after(str(actor["session_id"]), str(turn["turn_id"]), None)
        if any(
            row.get("kind") == "native_loopx.result" and as_mapping(row.get("payload")).get("status") == "committed"
            for row in events
        ):
            return turn
        return None

    def _reconcile_monitor_reports(self, binding: Mapping[str, object], actor: dict[str, object]) -> int:
        verified = 0
        session_id = str(actor["session_id"])
        binding_id = str(binding["binding_id"])
        for path in self._pending_monitor_report_paths(actor):
            try:
                with try_exclusive_file_lock(path.with_suffix(".lock"), operation="return_steward_monitor_report") as acquired:
                    if not acquired or not path.exists():
                        continue
                    item = _read_json(path)
                    if (
                        not isinstance(item, dict)
                        or item.get("delivery_status") not in {"pending", "unconfirmed"}
                        or item.get("goal_id") != actor.get("goal_id")
                        or item.get("agent_id") != actor.get("agent_id")
                        or item.get("session_id") != session_id
                        or not str(item.get("markdown") or "").strip()
                    ):
                        continue
                    turn_id = str(item.get("turn_id") or "").strip()
                    if not turn_id:
                        continue
                    turn = self.controller.store.load_turn(session_id, turn_id)
                    if not turn:
                        continue
                    req = as_mapping(turn.get("loopx_request"))
                    if (
                        turn.get("origin") != "native_loopx"
                        or turn.get("loopx_execution") is not True
                        or req.get("actor") != actor
                    ):
                        raise ValueError("the monitor report Turn changed identity")
                    native = as_mapping(as_mapping(turn.get("response")).get("native_loopx"))
                    committed = native.get("status") == "committed" or any(
                        row.get("kind") == "native_loopx.result"
                        and isinstance(row.get("payload"), dict)
                        and as_mapping(row.get("payload")).get("status") == "committed"
                        for row in self.controller.store.events_after(session_id, turn_id, None)
                    )
                    if not committed:
                        continue

                    def current(b_id: str = binding_id, expected_actor: dict[str, object] = actor) -> bool:
                        try:
                            selected = self.reports.selected(b_id)
                        except (OSError, ValueError, KeyError, TypeError):
                            return False
                        return bool(as_mapping(selected.get("binding")).get("enabled") is True and selected.get("actor") == expected_actor)

                    if not current():
                        continue
                    saved_route = item.get("original_route")
                    route = self._original_route(binding, actor, saved_route)
                    if saved_route is not None and saved_route != route:
                        raise ValueError("the frozen monitor report changed its original audience")
                    if "source" not in item or "event" not in item or "original_route" not in item:
                        original = _read_json(self.transport.root / (str(route["request_ref"]) + ".json"))
                        base = {
                            k: deepcopy(v)
                            for k, v in original.items()
                            if k not in {"deliveries", "stream", "recovery_streams", "context_recovery"}
                        }
                        record: dict[str, object] = {**base, **item}
                        record["deliveries"] = dict(as_mapping(item.get("deliveries")))
                        record["original_route"] = route
                        atomic_write_json(path, record)
                    else:
                        record = item
                    phase = f"steward_monitor_report.{turn_id}"

                    def make_inbox(rec: dict[str, object] = record) -> object:
                        return self.transport._inbox(rec)

                    delivered = self.transport._deliver(
                        path,
                        record,
                        phase,
                        str(record["markdown"]),
                        inbox=make_inbox,
                        authorization_check=current,
                        reply_placement="chat_root",
                        finalize_source=False,
                    )
                    record["delivery_status"] = "delivered" if delivered else "unconfirmed"
                    atomic_write_json(path, record)
                    if not delivered or not current():
                        continue
                    proof = as_mapping(as_mapping(record.get("deliveries")).get(phase))
                    if proof.get("verified") is not True:
                        raise ValueError("the monitor report reply has no verified provider receipt")
                    verified += 1
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                continue
        return verified

    def reconcile(self) -> int:
        verified = 0
        for binding in as_dict_list(self.transport.bindings.read().get("bindings")):
            actor = as_optional_dict(binding.get("managed_actor"))
            if not actor:
                continue
            verified += self._reconcile_monitor_reports(binding, actor)
            try:
                binding_id = str(binding["binding_id"])
                subscription = self.reports.subscription(binding_id)
                if subscription is None:
                    continue
                journal = as_optional_dict(read_cadence_journal(runtime_root=self.reports.root, goal_id=str(actor["goal_id"])))
                if not journal or journal.get("publication"):
                    continue
                window = as_dict(journal["window"])
                window_id = str(window["window_id"])
                folder = attempt_directory(self.reports.root, window)
                if not (folder / "private-generation.json").exists():
                    continue
                path = folder / "private-delivery.json"
                with try_exclusive_file_lock(folder / "delivery.lock", operation="return_private_periodic_report") as acquired:
                    if not acquired:
                        continue
                    if not self._committed_turn(actor, window):
                        continue
                    generation = as_mapping(validate_private_generation(folder))

                    def current(b_id: str = binding_id, win: dict[str, object] = window) -> bool:
                        return bool(self.reports.delivery_current(b_id, win))

                    if not current():
                        continue
                    if path.exists():
                        record: dict[str, object] = _read_json(path)
                        if record.get("publication_cursor"):
                            continue
                        route = self._original_route(binding, actor, record["original_route"])
                    else:
                        route = self._original_route(binding, actor)
                        original = _read_json(self.transport.root / (str(route["request_ref"]) + ".json"))
                        # Preserve the original Core correlation and provider
                        # source; only this report's reply receipt is separate.
                        record = {
                            k: deepcopy(v)
                            for k, v in original.items()
                            if k not in {"deliveries", "stream", "recovery_streams", "context_recovery"}
                        }
                        record.update(
                            deliveries={},
                            original_route=route,
                            report_window_id=window_id,
                            report_generation_id=generation["generation_id"],
                            delivery_status="pending",
                            reply_placement="chat_root",
                        )
                        atomic_write_json(path, record)
                    if (
                        record["report_window_id"] != window_id
                        or record["report_generation_id"] != generation["generation_id"]
                        or record["original_route"] != route
                    ):
                        raise ValueError("the frozen report return changed its original audience")
                    body = (folder / "report.md").read_text(encoding="utf-8")
                    # A route's captured message proves the original audience;
                    # it is not the parent of a newly scheduled result. Missing
                    # placement denotes a retained pre-upgrade delivery intent.
                    placement_raw = record.get("reply_placement")
                    if placement_raw not in (None, "chat_root"):
                        raise ValueError("the frozen private report placement changed")
                    placement = str(placement_raw) if placement_raw is not None else None
                    phase = "periodic_report." + window_id

                    def make_inbox(rec: dict[str, object] = record) -> object:
                        return self.transport._inbox(rec)

                    delivered = self.transport._deliver(
                        path,
                        record,
                        phase,
                        body,
                        inbox=make_inbox,
                        authorization_check=current,
                        reply_placement=placement,
                    )
                    record["delivery_status"] = "delivered" if delivered else "unconfirmed"
                    atomic_write_json(path, record)
                    if not delivered or not current():
                        continue
                    proof = as_mapping(as_mapping(record.get("deliveries")).get(phase))
                    if proof.get("verified") is not True:
                        raise ValueError("the complete report reply has no verified provider receipt")
                    publication_id = "private-report-" + hashlib.sha256(
                        json.dumps(
                            [window_id, generation["generation_id"], proof],
                            sort_keys=True,
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest()
                    candidate = _read_json(folder / "publication-candidate.json")
                    with self.controller.lock, exclusive_file_lock(
                        self.transport.bindings.path,
                        operation="commit_original_report_publication",
                    ):
                        if not current():
                            continue
                        cursor = commit_periodic_report_publication_cursor(
                            runtime_root=self.reports.root,
                            candidate=candidate,
                            publication_id=publication_id,
                            delivered_at=datetime.now(timezone.utc).isoformat(),
                            covered_until=str(window["due_at"]),
                        )
                    record.update(publication_cursor=cursor, delivery_status="delivered")
                    atomic_write_json(path, record)
                    verified += 1
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                # Existing generation, failed/unknown sends and source failures
                # remain visible. No alternate App, route or model is selected.
                continue
        return verified
