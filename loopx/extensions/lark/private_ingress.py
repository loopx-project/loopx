"""Read-only source coverage for already-bound owner conversations.

Provider history is evidence, never delayed Turn authority. The existing live
consumer may reconnect once for a new unanswered gap; no history row is admitted,
replied to, or used to resume an existing Core effect here.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ...file_lock import try_exclusive_file_lock
from .conversation_identity import identity_ref
from .goal_channel_transport import call, json_payload, lark_args
from .group_history import _provider_page
from .private_json import write_private_json_atomic


class IngressObservationStatus(str, Enum):
    """Lark observation vocabulary; it grants no Core action or permission."""

    UNOBSERVED = "unobserved"
    VERIFIED = "verified"
    GAP = "gap"
    UNAVAILABLE = "unavailable"


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def _observation(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or not isinstance(state.get("gaps", {}), dict)
            or any(not isinstance(gap, dict) or type(gap.get("reply_observed")) is not bool
                   for gap in state.get("gaps", {}).values())):
        raise ValueError("private source observation is invalid")
    if state.get("status") not in {item.value for item in IngressObservationStatus}:
        raise ValueError("private source observation status is invalid")
    for field in ("last_checked_at", "last_complete_at"):
        value = state.get(field)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("private source observation time is invalid")
    if (state.get("last_complete_at") or 0) > (state.get("last_checked_at") or 0):
        raise ValueError("private source observation window is invalid")
    return state


class PrivateIngressObserver:
    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self.root = transport.root.parent / "lark-private-ingress"

    def _path(self, binding_id: str) -> Path:
        return self.root / f"{identity_ref(binding_id)}.json"

    def health(self, binding_id: str) -> dict[str, Any]:
        path = self._path(binding_id)
        try:
            state = _observation(path)
        except (OSError, ValueError):
            state = {"status": IngressObservationStatus.UNAVAILABLE.value}
        gaps = state.get("gaps", {})
        status = state.get("status", IngressObservationStatus.UNOBSERVED.value)
        if state.get("last_checked_at") is not None and time.time() - state["last_checked_at"] > 180:
            status = IngressObservationStatus.UNAVAILABLE.value
        return {
            "status": status,
            "last_checked_at": state.get("last_checked_at"),
            "last_complete_at": state.get("last_complete_at"),
            "source_gap_count": len(gaps),
            "without_reply_count": sum(not gap["reply_observed"] for gap in gaps.values()),
        }

    def observe(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[str]:
        """Return private profile names needing their existing live reconnect."""
        transport = self.transport
        profiles = transport.profiles()
        bindings = transport.bindings.read()["bindings"]
        records = None
        reconnect = []
        for binding in bindings:
            if cancelled():
                break
            # Group topic semantics/permissions have a separate history owner.
            if binding.get("enabled") is not True or binding.get("audience", "owner") != "owner":
                continue
            if profiles.get(binding["transport_ref"], {}).get("binding_id") != binding["binding_id"]:
                continue  # Another provider's binding is not a Lark source.
            path = self._path(binding["binding_id"])
            try:
                _observation(path)
            except (OSError, ValueError, TypeError):
                # Preserve an unreadable receipt for recovery, rather than
                # resetting its dedupe fence and reconnecting it again.
                continue
            with try_exclusive_file_lock(
                path, agent_id="loopx-lark", operation="observe_private_ingress",
            ) as ownership:
                if ownership is None:
                    # Another observer already owns this binding's receipt.
                    # Skipping the read-modify-write keeps its committed gap
                    # instead of overwriting it with an older window.
                    continue
                try:
                    state = _observation(path)
                    if records is None:
                        records = self._records()
                    if self._observe(binding, records, state, cancelled):
                        reconnect.append(binding["transport_ref"])
                    write_private_json_atomic(path, state)
                except (OSError, ValueError, KeyError, TypeError):
                    # Never publish provider exceptions or turn failed reads
                    # into empty history. Retain the bound and every gap.
                    try:
                        state = _observation(path)
                    except (OSError, ValueError, TypeError):
                        continue
                    state.update(status=IngressObservationStatus.UNAVAILABLE.value,
                                 last_checked_at=time.time())
                    write_private_json_atomic(path, state)
        return reconnect

    def _records(self) -> list[dict[str, Any]]:
        records = [json.loads(path.read_text(encoding="utf-8")) for path in self.transport.root.glob("*.json")]
        if any(not isinstance(row, dict) or not isinstance(row.get("event"), dict)
               or not isinstance(row.get("binding_id"), str) for row in records):
            raise ValueError("private capture read is incomplete")
        return records

    def _observe(self, binding: dict[str, Any], records: list[dict[str, Any]],
                 state: dict[str, Any], cancelled: Callable[[], bool]) -> bool:
        transport = self.transport
        profile = binding["transport_ref"]
        bound = [row for row in records if row.get("binding_id") == binding["binding_id"]
                 and row.get("profile") == profile and row.get("event", {}).get("chat_type") == "p2p"]
        if not bound:
            raise ValueError("no original owner source has been captured")
        # An admitted original supplies the destination. No contact search,
        # guessed private chat, global profile or arbitrary caller URL is used.
        carrier = bound[-1]
        selected = transport.bindings.resolve(binding_id=binding["binding_id"],
            include_observation=True, **carrier["source"])
        if selected["binding"] != binding:
            raise ValueError("conversation binding changed")
        event = carrier["event"]
        chat, owner = event["chat_id"], event["sender_id"]
        if any(row["event"]["chat_id"] != chat or row["event"]["sender_id"] != owner for row in bound):
            raise ValueError("original owner source changed")
        # Authority is freshly verified above, including grant changes. The
        # coverage cursor belongs to the App/owner/source, not mutable settings.
        fingerprint = identity_ref(binding["binding_id"], binding.get("provider_ref", ""),
                                   binding.get("operator_ref", ""), chat, owner)
        if state.get("source_fingerprint") not in (None, fingerprint):
            raise ValueError("history observation binding changed")
        now = time.time()
        end = now - 5  # Leave live stream/admission a short race window.
        # Only the first observation is capped to one day. With a cursor, resume
        # exactly there and advance at most one day per pass, so a longer
        # outage is covered in contiguous segments instead of being skipped.
        cursor = state.get("last_complete_at")
        start = (float(cursor) - 60) if cursor is not None else (now - 86400)
        window_end = min(end, start + 86400)
        if start >= window_end:
            return False
        messages: list[Mapping[str, Any]] = []
        token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(20):
            if cancelled():
                raise OSError("source observation stopped")
            args = ["im", "+chat-messages-list", "--as", "bot", "--chat-id", chat,
                    "--start", _iso(start), "--end", _iso(window_end), "--order", "asc",
                    "--page-size", "50", "--no-reactions", "--format", "json"]
            if token:
                args += ["--page-token", token]
            # Background readonly IO has a short budget. Keep the established
            # transport error envelope without shortening effectful replies.
            result = call(lambda argv, cwd, _timeout: transport.runner(argv, cwd, 5),
                          lark_args(cli_bin=transport.cli_bin, profile=profile, tail=args))
            if result.get("returncode") != 0:
                raise OSError("source history unavailable")
            page, more, token = _provider_page(json_payload(result))
            if any(row.get("chat_id") != chat for row in page):
                raise ValueError("provider history destination changed")
            messages.extend(page)
            if not more:
                break
            if token in seen_tokens:
                raise ValueError("source history cursor repeated")
            seen_tokens.add(str(token))
        else:
            raise ValueError("bounded history observation remains incomplete")
        current = next((row for row in transport.bindings.read()["bindings"]
                        if row["binding_id"] == binding["binding_id"]), None)
        if current != binding:
            raise ValueError("conversation binding changed during history read")
        observation = selected["observation"]
        bot_ids = {observation["bot_app_id"], observation["bot_open_id"]} - {""}
        replies = {str(row.get("reply_to") or row.get("parent_id") or "") for row in messages
                   if row.get("sender", {}).get("sender_type") in {"app", "bot"}
                   and row.get("sender", {}).get("id") in bot_ids}
        # Live admission can complete while the provider read is in flight.
        # Re-read journals before declaring a gap, not the earlier snapshot.
        captured = set()
        for row in self._records():
            if row.get("binding_id") == binding["binding_id"] and row.get("profile") == profile:
                captured.add(row["event"]["message_id"])
        gaps = {ref: dict(gap) for ref, gap in state.get("gaps", {}).items() if ref not in captured}
        prior_gaps = set(gaps)
        for row in messages:
            sender = row.get("sender", {})
            if (sender.get("sender_type") != "user" or sender.get("id") != owner
                    or row.get("deleted") is True or row["message_id"] in captured):
                continue
            gaps.setdefault(row["message_id"], {"reply_observed": False})
        for ref, gap in gaps.items():
            gap["reply_observed"] = gap["reply_observed"] or ref in replies
        reconnect = any(not gaps[ref]["reply_observed"] for ref in gaps.keys() - prior_gaps)
        # Only source identifiers and content-free observations persist. A
        # source missing from a later window is never silently forgotten.
        behind = window_end < end - 1
        state.update(source_fingerprint=fingerprint, last_checked_at=now,
                     last_complete_at=window_end,
                     window_start=start, window_end=window_end, gaps=gaps,
                     status=(IngressObservationStatus.GAP if gaps
                             else IngressObservationStatus.UNAVAILABLE if behind
                             else IngressObservationStatus.VERIFIED).value)
        return reconnect
