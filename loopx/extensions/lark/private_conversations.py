"""Private-message transport for native, Core-bound project Chat.

Only provider provenance, Inbox presentation and delivery receipts live here.
Core owns audience grants, canonical Sessions, durable Turns and stop/recovery.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from itertools import tee
from pathlib import Path
from threading import Lock
from typing import Any

from ...capabilities.native_chat.external_conversations import (
    ChatExternalConversations,
    QUOTA_BACKLOG_BATCH_SIZE,
    STEWARD_BINDING_ADOPTION_PENDING,
)
from ...capabilities.native_chat.conversation_bindings import ChatConversationBindings
from ...capabilities.native_chat.file_discovery import FileChatDiscovery, file_change_fingerprint
from ...chat_attachments import CHAT_IMAGE_MAX_COUNT, normalize_chat_image_attachments
from ...chat_store import CHAT_MESSAGE_SCHEMA_VERSION, ChatSessionStore, TERMINAL_TURN_STATES, _atomic_write_json, _read_json
from ...control_plane.collaboration import conversation_reply_context
from ...control_plane.effect_runtime import effect_runtime_request_scope
from ...file_lock import exclusive_file_lock
from ...presentation.markdown import markdown_scalar
from ...presentation.renderers.conversation_status_markdown import render_conversation_status
from .conversation_identity import identity_ref, lark_private_source
from .event_inbox import acknowledge_lark_event_inbox, ingest_lark_event_inbox
from .event_collector_runtime import enrich_lark_event_reply_context
from .goal_channel_transport import call, json_payload, lark_args, lark_provider_mention_identities, record_event_timing
from .identity_shapes import LARK_APP_ID_PATTERN as APP_ID_PATTERN, LARK_MESSAGE_ID_PATTERN
from .inbox_reply import _message, reply_lark_event_inbox, update_lark_inbox_reply, verify_lark_inbox_reply
from .manager_context import manager_failure_reply
from .inbox_reactions import (_reconcile_outbound_input_reactions,
                             mark_lark_event_inbox_processing, mark_lark_event_inbox_received)
from .outbound import normalize_lark_outbound_text, present_lark_conversation_text as _presentation_text
from .private_images import private_message_caption, private_message_images
from .private_delivery_discovery import PrivateDeliveryDiscovery, answer_media_dispatch, historical_image_reply_matches, historical_image_source_matches, prepared_steward_binding_waits, resumable_answer_image_session
from .private_ingress import PrivateSourceUnavailable, private_source_text, steward_source_preflight
from .private_ingress import project_private_reply_history as project_private_reply_history
from . import private_steward_triage as triage
from .return_files import uploaded_chat_images, verify_chat_images
from .private_progress import (ACTIVITY_UPDATE_INTERVAL_SEC, UPDATE_INTERVAL_SEC, legacy_recovery_repairs,
                               presentation_phase, presentation_stream, project_progress)

# Conservative transport budget, not a claim about the provider's maximum.
# Reserve edits for a final/closing notice; continue long Turns on a new draft.
PROGRESS_EDIT_BUDGET = 12
CHAT_CONTROL_COMMANDS = {"/status": "status", "/help": "help", "/new": "new", "/stop": "stop"}
ANSWER_IMAGE_RETRY_SECONDS = 300
_UNSUPPORTED_MEDIA_LABELS = {"audio": "音频", "file": "文件", "media": "视频"}
_MARKDOWN_CODE_SPAN = re.compile(r"((?<!\x60)(\x60+)(?!\x60).*?(?<!\x60)\2(?!\x60))")
_ANSWER_IMAGE = re.compile(r"""(?<!\\)!\[([^\]\n]*)\]\(\s*(?:<([^<>\n]+)>|([^\s<>()]+))(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'))?\s*\)""")
_SOURCE_URL = re.compile(r"https://[^\s<>()\[\]\"，。；！？]+")


def _private_answer_images(text: str) -> tuple[str, list[str]]:
    """Remove image previews from provider prose, leaving the canonical answer alone."""
    lines, targets = [], []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(\x60{3,}|~{3,})(.*)$", line)
        if marker and fence is None:
            fence = marker[1]
        elif marker and fence and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
            fence = None
        elif not fence and not line.startswith(("    ", "\t")):
            def link(match: re.Match[str]) -> str:
                url = match[2] or match[3]
                if url not in targets:
                    targets.append(url)
                caption = match[1] or f"图片 {targets.index(url) + 1}"
                return f"[{caption}](<{url}>)" if url.startswith("https://") and " " in url else (
                    f"[{caption}]({url})" if url.startswith("https://") else caption)

            start, chunks = 0, []
            for span in _MARKDOWN_CODE_SPAN.finditer(line):
                chunks.extend((_ANSWER_IMAGE.sub(link, line[start:span.start()]), span[0]))
                start = span.end()
            line = "".join((*chunks, _ANSWER_IMAGE.sub(link, line[start:])))
        lines.append(line)
    return "".join(lines), targets


def _private_answer_image(url: str, *, allowed_urls: set[str], deadline: float, index: int) -> dict[str, Any]:
    # Reuse anonymous HTTPS address pinning, redirect checks and byte limits.
    # No local file, signed-in browser or model tool authority is involved.
    from urllib.parse import urlsplit

    from ..public_source_reader import fetch

    host = urlsplit(url).hostname
    same_host_urls = {candidate for candidate in allowed_urls if urlsplit(candidate).hostname == host}
    _, mime, data = fetch(url, deadline=deadline, allowed_urls=same_host_urls)
    signatures = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/gif": b"GIF8"}
    if not ((mime in signatures and data.startswith(signatures[mime]))
            or (mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
        raise ValueError("unsupported original image")
    return {"id": f"reply-image-{index}", "name": f"original-{index}.{mime.split('/')[1]}",
            "mime_type": mime, "size": len(data), "data_url": f"data:{mime};base64," + base64.b64encode(data).decode()}


def _allowed_answer_image_urls(source: str, targets: list[str], *, deadline: float) -> set[str]:
    """Authorize exact owner URLs and images listed by the original same-host article."""
    from urllib.parse import urlsplit

    from ..public_source_reader import article, public_url

    def candidate(url: str) -> str | None:
        try:
            return public_url(url) if not urlsplit(url).query else None
        except ValueError:
            return None

    owner_links = {url for link in _SOURCE_URL.findall(source) if (url := candidate(link))}
    wanted = {candidate(target) for target in targets}
    if None in wanted or not owner_links:
        return set()
    if wanted <= owner_links:
        return owner_links
    if len(owner_links) != 1:
        return set()
    [original] = owner_links
    host = urlsplit(original).hostname
    if any(urlsplit(url).hostname != host for url in wanted - owner_links):
        return set()
    observed = article(original, deadline=deadline, allowed_urls={original})
    if observed.get("ok") is not True:
        return set()
    listed = {url for item in observed["images"]
              if (url := candidate(item["url"])) and urlsplit(url).hostname == host}
    allowed_urls = owner_links | listed
    return allowed_urls if wanted <= allowed_urls else set()


def _read_answer_images(source: str, targets: list[str], cached: list | None,
                        delivery: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (delivery.get("started") and not delivery.get("attempt")) or (delivery.get("attempt") and cached is None):
        raise ValueError("image delivery acknowledgement or frozen bytes are unavailable")
    deadline = time.monotonic() + 15
    allowed_urls = _allowed_answer_image_urls(source, targets, deadline=deadline)
    if not allowed_urls:
        raise ValueError("image was not referenced by the original source")
    images = normalize_chat_image_attachments([
        _private_answer_image(url, allowed_urls=allowed_urls, deadline=deadline, index=index)
        for index, url in enumerate(targets, 1)])
    # Older queued bytes were fetched before redirect authorization. Keep
    # any recorded provider intent tied to the same bytes.
    if cached is not None and cached != images:
        raise ValueError("unverified cached image bytes changed")
    return images


def _is_answer_image_followup(record: Mapping[str, Any]) -> bool:
    if record.get("status") != "accepted" or not isinstance(record.get("answer_images"), dict):
        return False
    terminal = record.get("deliveries", {}).get(presentation_phase(record, "terminal"), {})
    return terminal.get("verified") is True


def _answer_image_retry_waits(record: Mapping[str, Any], now: float) -> bool:
    if not _is_answer_image_followup(record):
        return False
    state = record["answer_images"]
    retry_at = state.get("next_check_at")
    return state.get("status") != "delivered" and type(retry_at) in (int, float) and retry_at > now


def _unsupported_media_label(row: Mapping[str, Any]) -> str | None:
    event = row.get("event")
    kind = event.get("message_type") if isinstance(event, Mapping) else None
    if (isinstance(kind, str) and row.get("schema_version") == "lark_private_chat_delivery_v0"
            and row.get("status") in ("command_completed", "delivered")
            and row.get("response_code") == "unsupported_attachment" and "turn_id" in row and row["turn_id"] is None):
        return _UNSUPPORTED_MEDIA_LABELS.get(kind)
    return None


def _history_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing history time")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("unqualified history time")
    return stamp.astimezone(timezone.utc)


class PrivateUnsupportedMediaHistory:
    """Read Lark presentation history without constructing a transport worker."""

    def __init__(self) -> None:
        self._discovery = FileChatDiscovery()
        self._location_lock = Lock()
        self._location_cache: tuple[Path, tuple[int, ...], dict[str, list[Path]]] | None = None

    def _locations(self, root: Path, sid: str) -> list[Path]:
        complete = True

        def read_receipt(path: Path) -> dict[str, Any]:
            try:
                return _read_json(path)
            except UnicodeDecodeError:
                return {}

        def locate(_path: Path, row: dict[str, Any]) -> dict[str, Any] | None:
            nonlocal complete
            # Keep valid locators, but retry unreadable receipts on the next scan.
            if row.get("schema_version") != "lark_private_chat_delivery_v0":
                complete = False
                return None
            return {"media_session": row.get("session_id") if _unsupported_media_label(row) else None}

        with self._location_lock:
            try:
                stamp = file_change_fingerprint(root)
            except OSError:
                return []
            cached = self._location_cache
            if stamp is not None and cached is not None and cached[:2] == (root, stamp):
                return cached[2].get(sid, [])
            paths = (path for path in root.glob("*.json") if re.fullmatch(r"[a-f0-9]{24}", path.stem))
            locations: dict[str, list[Path]] = {}
            for path, view in self._discovery.rows(root, paths, read=read_receipt, project=locate):
                session_id = view.get("media_session")
                if isinstance(session_id, str):
                    locations.setdefault(session_id, []).append(path)
            # All production journal publications are atomic replacements.
            # Cache only the locator list, never a selected receipt or grant.
            try:
                if complete and stamp is not None and file_change_fingerprint(root) == stamp:
                    self._location_cache = (root, stamp, locations)
            except OSError:
                pass
            return locations.get(sid, [])

    def read(self, store: ChatSessionStore, bindings: ChatConversationBindings | None,
             session: dict[str, Any]) -> list[dict[str, Any]]:
        """Show original, non-executed Lark media without replay or provider IO."""
        saved = session.get("steward_context") or session.get("project_context") or {}
        if (bindings is None or saved.get("audience") != "bound_owner"
                or session.get("session_mode") != "managed_runtime"):
            return []
        sid = session["session_id"]
        try:
            state = bindings.read()
            matches = [row for row in state["bindings"] if row["binding_id"] == saved.get("binding_id")]
        except (KeyError, TypeError, ValueError, OSError):
            return []
        if len(matches) != 1:
            return []
        binding, = matches
        prefix = "manager.external.native" if binding.get("context_kind") == "steward" else "project.external"
        if (binding.get("enabled") is not True or binding.get("audience", "owner") != "owner"
                or any(not saved.get(k) or binding.get(k) != saved[k]
                       for k in ("provider_ref", "operator_ref", "project_ref", "grant"))
                or not saved.get("source_ref")
                or session.get("channel_id") != f"{prefix}.{binding['binding_id']}.{saved['source_ref']}"):
            return []

        markers = []
        for path in self._locations(store.root / "lark-private-deliveries", sid):
            try:
                # The locator is only a hint. Join the two exact receipts and
                # recompute the private principal; don't trust a cached grant.
                record = _read_json(path)
                label = _unsupported_media_label(record)
                event = record["event"]
                source = lark_private_source(provider_ref=saved["provider_ref"], event=event)
                native = _read_json(store.root / "external-requests" / path.name)
                expected = {"request_ref": path.stem, "binding_id": binding["binding_id"],
                            "session_id": sid, "source": source, "response_code": "unsupported_attachment"}
                if (not label or record.get("profile") != binding["transport_ref"]
                        or source["private_human_message"] is not True or source["source_ref"] != saved["source_ref"]
                        or source["sender_ref"] != saved["operator_ref"]
                        or identity_ref(saved["provider_ref"], event["message_id"]) != path.stem
                        or any(row.get(k) != v for row in (record, native) for k, v in expected.items())
                        or native.get("schema_version") != "loopx_chat_external_request_v0"
                        or native.get("origin") not in (None, "lark")
                        or native.get("status") != "command_completed" or native.get("command") != "unsupported"
                        or "turn_id" not in native or native["turn_id"] is not None):
                    continue
                epoch = str(event.get("create_time") or "")
                stamp = (datetime.fromtimestamp(int(epoch) / 1000, timezone.utc)
                         if re.fullmatch(r"[0-9]{13}", epoch) else _history_timestamp(native.get("created_at")))
                mid = f"lark.unsupported.{path.stem}"
                markers.append({"schema_version": CHAT_MESSAGE_SCHEMA_VERSION,
                    "message_id": mid, "turn_id": None, "role": "error", "origin": "lark",
                    "text": f"飞书{label} · 未提交执行。请在原飞书会话查看。",
                    "created_at": stamp.isoformat(timespec="microseconds")})
            except (KeyError, ValueError, TypeError, OSError):
                continue

        try:
            if not markers or bindings.read() != state:
                return []
        except (ValueError, OSError):
            return []
        return sorted(markers, key=lambda row: (row["created_at"], row["message_id"]))

    @staticmethod
    def summary(summary: dict[str, Any], markers: list[dict[str, Any]]) -> dict[str, Any]:
        """Invalidate an App's quiet history cache even when no Turn was created."""
        revision = summary.get("transcript_revision")
        if not revision or not markers:
            return summary
        digest = hashlib.sha256(json.dumps(markers, sort_keys=True, ensure_ascii=False,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        return {**summary, "transcript_revision": f"{revision}.lark.{digest}"}

    @staticmethod
    def insert(messages: list[dict[str, Any]], markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not markers:
            return messages
        # Keep canonical append order and insert presentation rows by original
        # time before paging. A late insertion must invalidate the old prefix.
        ids = {row.get("message_id") for row in messages}
        pending = [(_history_timestamp(row["created_at"]), row) for row in reversed(markers)
                   if row["message_id"] not in ids]
        projected = []
        for message in messages:
            try:
                stamp = _history_timestamp(message.get("created_at"))
            except ValueError:
                stamp = None
            while pending and stamp is not None and pending[-1][0] < stamp:
                projected.append(pending.pop()[1])
            projected.append(message)
        return projected + [row[1] for row in reversed(pending)]


class _ControlReadFallback(Exception):
    """A valid source needs ordinary rendering, before any Core write."""


class LarkPrivateConversations:
    def __init__(self, *, controller: Any, runtime_root: Path, runner: Any, cli_bin: str,
                 reaction_feedback: bool = True) -> None:
        self.core = ChatExternalConversations(controller)
        self.bindings = self.core.bindings
        self.runtime_root, self.runner, self.cli_bin = runtime_root, runner, cli_bin
        self.reaction_feedback = reaction_feedback
        self.steward_callback_readiness: dict[tuple[str, str], Callable[[], bool]] = {}
        self.root = controller.store.root / "lark-private-deliveries"
        self._context_recovery_probe_at: dict[Path, float] = {}
        self._historical_image_probe_at: dict[Path, float] = {}
        self._delivery_discovery = PrivateDeliveryDiscovery(self.root)
        self._body_discovery = PrivateDeliveryDiscovery(self.root)
        self._web_projection_scan_at = 0.0
        self._web_projection_snapshots: dict[str, tuple[int, ...]] = {}
        from .owner_attention import LarkOwnerAttention
        self.owner_attention = LarkOwnerAttention(self)
        from .private_ingress import PrivateIngressObserver
        self.ingress = PrivateIngressObserver(self)
        from ...capabilities.native_chat.steward_managed_mode import StewardManagedMode
        self.managed_steward = StewardManagedMode(controller, self.bindings)
        from .steward_reports import LarkStewardReports
        self.steward_reports = LarkStewardReports(self)
        controller.steward_report_provider = self.steward_reports

    def profiles(self) -> dict[str, dict[str, str]]:
        """Discover current App configuration, never authorize a conversation.

        A network/token probe failing is not a durable disconnect. The local
        profile inventory supplies the actual App lease identity; Core still
        freshly verifies App/owner/source/grants at admission and delivery.
        """
        bindings = self.bindings.read()["bindings"]
        if not bindings:
            return {}
        apps = self.configured_profile_apps()
        profiles = {}
        for row in bindings:
            app_id = apps.get(row["transport_ref"], "")
            if row.get("enabled") is not True or not app_id or identity_ref(app_id) != row["provider_ref"]:
                continue
            profiles[row["transport_ref"]] = {"cli_bin": self.cli_bin, "provider_ref": row["provider_ref"],
                "bot_app_id": app_id, "audience": row.get("audience", "owner"), "context_kind": row["context_kind"],
                "binding_id": row["binding_id"], "consumer_ref": hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:32]}
        return profiles

    def configured_profile_apps(self, *, cli_bin: str | None = None) -> dict[str, str]:
        """Read local App identities for listener leases, without granting access."""
        result = call(self.runner, [cli_bin or self.cli_bin, "profile", "list"])
        try:
            configured = json.loads(str(result.get("stdout") or ""))
        except json.JSONDecodeError as exc:
            raise OSError("Lark profile configuration could not be read") from exc
        if result.get("returncode") != 0 or not isinstance(configured, list):
            # An unreadable inventory is unknown, not an empty configuration.
            # The existing stream watcher retains its route on read failures.
            raise OSError("Lark profile configuration could not be read")
        apps = {str(row.get("name") or ""): str(row.get("appId") or "")
                for row in configured if isinstance(row, Mapping)}
        # lark-cli also accepts an App ID as --profile. Legacy shared group
        # listeners retain that selector; discover the same App without
        # renaming its binding or opening a competing consumer. Explicit
        # profile names take precedence over the App-ID fallback.
        for app_id in list(apps.values()):
            apps.setdefault(app_id, app_id)
        return {profile: app_id for profile, app_id in apps.items() if APP_ID_PATTERN.fullmatch(app_id)}

    @effect_runtime_request_scope()
    def health(self) -> dict[str, dict[str, int]]:
        health: dict[str, dict[str, int]] = {}
        quota_pending = self.core.quota_backlog_observer()
        completed: list[tuple[dict[str, Any], dict[str, Any]]] = []

        def count(row: dict[str, Any]) -> None:
            entry = health[row["binding_id"]]
            entry["pending_count"] += 1
            entry["recovery_count"] += any(phase.get("started") and not phase.get("verified")
                                           for phase in row["deliveries"].values())

        def observe_completed() -> None:
            flags = quota_pending([native for _, native in completed])
            for (row, _), pending in zip(completed, flags, strict=True):
                if pending:
                    count(row)
            completed.clear()

        def delivered_requests() -> Iterator[dict[str, Any]]:
            for path in [*self.root.glob("*.json"), *self.root.glob("web-projections/*.json")]:
                row = _read_json(path)
                health.setdefault(row["binding_id"], {"pending_count": 0, "recovery_count": 0})
                if row["status"] != "delivered":
                    if triage.pending(row):
                        count(row)
                elif (row.get("historical_answer_images") or {}).get("status") == "pending":
                    count(row)
                elif path.parent == self.root and row.get("session_id") and row.get("turn_id"):
                    yield row

        # Pair the original receipts with the current Core correlation and Turn.
        receipts, refs = tee(delivered_requests())
        natives = self.core.read_backlog_requests(row["request_ref"] for row in refs)
        for row, native in zip(receipts, natives, strict=True):
            if (native.get("status") == "accepted" and native.get("command") is None
                    and all(native.get(key) == row.get(key) for key in
                            ("binding_id", "source", "session_id", "turn_id"))):
                completed.append((row, native))
                if len(completed) >= QUOTA_BACKLOG_BATCH_SIZE:
                    observe_completed()
        observe_completed()
        return health

    def _binding(self, profile: str) -> dict[str, Any]:
        return next(row for row in self.bindings.read()["bindings"] if row["transport_ref"] == profile)

    def _group_source_message(self, profile: str, message_id: str) -> Mapping[str, Any] | None:
        # The display-oriented mget projection omits reply ancestry. Read the
        # original provider envelope for group provenance, including root @.
        result = call(self.runner, lark_args(cli_bin=self.cli_bin, profile=profile,
            tail=["api", "GET", f"/open-apis/im/v1/messages/{message_id}", "--as", "bot"]))
        payload = json_payload(result)
        if result.get("returncode") != 0 or payload.get("code", 0) != 0 or payload.get("ok") is False:
            return None
        return _message(payload, message_id)

    def _original_message_pair(self, profile: str, message_id: str,
                               other_id: str) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
        # Read both admitted IDs at once. Callers verify the returned envelopes
        # and the current Core selection before using either as authority.
        result = call(self.runner, lark_args(cli_bin=self.cli_bin, profile=profile,
            tail=["api", "GET", "/open-apis/im/v1/messages/mget", "--params",
                  json.dumps({"message_ids": [message_id, other_id]}), "--as", "bot"]))
        payload = json_payload(result)
        if result.get("returncode") != 0 or payload.get("code", 0) != 0 or payload.get("ok") is False:
            return None, None
        data = payload.get("data")
        items = data.get("items") if isinstance(data, Mapping) else None
        if (not isinstance(items, list) or len(items) != 2
                or any(not isinstance(item, Mapping) or not isinstance(item.get("message_id"), str) for item in items)
                or {item.get("message_id") for item in items} != {message_id, other_id}):
            return None, None
        messages = {item["message_id"]: item for item in items}
        return messages[message_id], messages[other_id]

    def _completed_stop_root(self, record: Mapping[str, Any], binding: Mapping[str, Any]) -> str | None:
        """Read scheduling only; fresh Core resolution still owns authorization."""
        event = record["event"]
        root = str(event.get("root_id") or event["message_id"])
        path = self.core.root / f"{record['request_ref']}.json"
        native = _read_json(path) if path.exists() else {}
        if (binding.get("enabled") is True and binding.get("audience") == "group"
                and binding.get("binding_id") == record["binding_id"]
                and binding.get("transport_ref") == record["profile"]
                and root != event["message_id"] and native.get("command") == "stop"
                and native.get("status") == "command_completed" and native.get("target_recorded") is True
                and native.get("binding_id") == record["binding_id"]
                and native.get("source") == record["source"] == lark_private_source(
                    provider_ref=binding["provider_ref"], event=event)
                and identity_ref(binding["provider_ref"], event["message_id"])
                == record["request_ref"] == native.get("request_ref")):
            return root
        return None

    def _source_message(self, record: dict[str, Any], *, selected: dict[str, Any] | None = None,
                        render_content: bool = True,
                        sender_identity: dict[str, str] | None = None,
                        source_pair: tuple[Mapping[str, Any] | None, Mapping[str, Any] | None] | None = None,
                        ) -> Mapping[str, Any] | None:
        """Read the exact source under this App, then recheck the Core audience.

        A p2p source read proves the destination without requiring group-member
        scopes. A caller cannot opt into this path with an HTTP boolean.
        """
        event = record["event"]
        result: Mapping[str, Any]
        message: Mapping[str, Any] | None
        try:
            prefetched = None
            read_binding: Mapping[str, Any] | None = None
            if selected is None:
                # Only an existing, locally enabled binding and a completed,
                # target-frozen Core stop can schedule these original two IDs.
                # Buffer readonly IO while Core freshly verifies identity and
                # authority; neither result is usable until both have joined.
                if not render_content:
                    read_binding = next((row for row in self.bindings.read()["bindings"]
                                         if row.get("binding_id") == record["binding_id"]), {})
                root = self._completed_stop_root(record, read_binding) if read_binding else None
                if root:
                    with ThreadPoolExecutor(max_workers=1) as reads:
                        envelopes = reads.submit(self._original_message_pair,
                                                 record["profile"], event["message_id"], root)
                        selected = self.bindings.resolve(binding_id=record["binding_id"],
                                                         include_observation=True, **record["source"])
                        prefetched = envelopes.result()
                    current = next((row for row in self.bindings.read()["bindings"]
                                    if row.get("binding_id") == record["binding_id"]), None)
                    if current != read_binding or current != selected["binding"]:
                        return None  # Revocation/replacement while reads ran is not a delivery grant.
                else:
                    selected = self.bindings.resolve(binding_id=record["binding_id"],
                                                     include_observation=sender_identity is not None, **record["source"])
            native_path = self.core.root / f"{record['request_ref']}.json"
            native = _read_json(native_path) if native_path.exists() else {}
            if native.get("agent_target"):
                self.bindings.resolve_agent_target(selected, native["agent_target"])
            def rendered() -> Mapping[str, Any]:
                return call(self.runner, lark_args(cli_bin=self.cli_bin, profile=record["profile"],
                    tail=["im", "+messages-mget", "--message-ids", event["message_id"],
                          "--as", "bot", "--no-reactions", "--format", "json"]))

            group = selected["binding"].get("audience") == "group"
            # Resolve has just verified this App for this invocation. Reuse
            # that observation for mention checks, never across verifications.
            observation = selected.get("observation")
            if sender_identity is not None and observation is not None:
                sender_identity["bot_display_name"] = str(observation.get("bot_display_name") or "")
            stop_root: Mapping[str, Any] | None = None
            paired_root = False
            if group:
                # Canonical envelopes alone supply ancestry and @. Outbound
                # provenance needs no display rendering; ordinary admission
                # retains that projection for text and media inputs.
                if render_content:
                    # Admission already verified this exact App. Reuse only
                    # that call's ephemeral observation; Core still rechecks
                    # under its fences, and outbound verification stays fresh.
                    # An untrusted event hint only changes read scheduling.
                    # Only authenticated provider text can select a control;
                    # ordinary input still needs its display projection.
                    control_hint = (event.get("message_type") == "text"
                                    and isinstance(event.get("content"), str)
                                    and event.get("content") in CHAT_CONTROL_COMMANDS)
                    with ThreadPoolExecutor(max_workers=3) as reads:
                        current = reads.submit(self._group_source_message, record["profile"], event["message_id"])
                        display = None if control_hint else reads.submit(rendered)
                        identity = (reads.submit(self.bindings.observe, record["profile"])
                                    if observation is None else None)
                        canonical = current.result()
                        if identity is not None:
                            observation = identity.result()
                        command_input = (self._group_text_command_input(canonical, observation)
                            if control_hint and canonical is not None
                            and observation is not None
                            and canonical.get("msg_type", canonical.get("message_type")) == "text" else None)
                        if (command_input in CHAT_CONTROL_COMMANDS
                                and canonical is not None
                                and (not native or native.get("message") == command_input)):
                            result, message = {"returncode": 0}, {**canonical, "content": command_input}
                        else:
                            # Older native requests may have frozen rendered
                            # text. Keep their exact replay representation.
                            result = display.result() if display is not None else rendered()
                            message = _message(json_payload(result), event["message_id"])
                else:
                    root_id = str(event.get("root_id") or event["message_id"])
                    # Batch a frozen stop's IDs or a caller's admitted-topic
                    # pair; the current selection must validate either result.
                    completed_stop = self._completed_stop_root(record, selected["binding"]) == root_id
                    paired_root = completed_stop or source_pair is not None
                    # Delivery reuses only its own binding observation. An
                    # explicitly supplied selection without one still needs a
                    # fresh identity; join that read before ancestry and @.
                    def sources() -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
                        if source_pair is not None:
                            return source_pair
                        if completed_stop:
                            if prefetched is not None and read_binding == selected["binding"]:
                                return prefetched
                            return self._original_message_pair(record["profile"], event["message_id"], root_id)
                        return self._group_source_message(record["profile"], event["message_id"]), None

                    if observation is not None:
                        canonical, stop_root = sources()
                    else:
                        with ThreadPoolExecutor(max_workers=2) as reads:
                            envelopes = reads.submit(sources)
                            identity = reads.submit(self.bindings.observe, record["profile"])
                            (canonical, stop_root), observation = envelopes.result(), identity.result()
                    result, message = {"returncode": 0 if canonical else 1}, canonical
            else:
                result = rendered()
                message = _message(json_payload(result), event["message_id"])
            sender = message.get("sender") if isinstance(message, Mapping) else None
            if (result.get("returncode") == 0 and message is not None
                    and message.get("chat_id") == event["chat_id"] and isinstance(sender, Mapping)
                    and sender.get("id") == event["sender_id"] and sender.get("sender_type") == "user"):
                if group:
                    canonical_sender = canonical.get("sender") if isinstance(canonical, Mapping) else None
                    if (canonical is None or canonical.get("chat_id") != event["chat_id"]
                            or not isinstance(canonical_sender, Mapping)
                            or canonical_sender.get("id") != sender.get("id")
                            or canonical_sender.get("sender_type") != "user"
                            or canonical.get("msg_type", canonical.get("message_type"))
                            != message.get("msg_type", message.get("message_type"))):
                        return None
                    # Keep the existing text/media rendering, but only the
                    # lossless envelope supplies identity, ancestry and @.
                    if not render_content and canonical.get("msg_type", canonical.get("message_type")) != event.get("message_type"):
                        return None
                    message = {**canonical, **({"content": message.get("content")} if render_content else {})}
                    # Provider readback, never event text, fixes the topic root.
                    root = str(message.get("root_id") or message["message_id"])
                    # The compact event projection emits null for absent
                    # ancestry. Readback can enrich the thread id later; that is
                    # not a conflict unless the event supplied another id.
                    # Parent/root ancestry must still agree exactly.
                    parent = str(message.get("parent_id") or "")
                    event_parent = str(event.get("parent_id") or "")
                    event_thread = str(event.get("thread_id") or "")
                    if (root != str(event.get("root_id") or event["message_id"])
                            or (parent and not message.get("root_id"))
                            or parent != event_parent
                            or (event_thread and event_thread != str(message.get("thread_id") or ""))):
                        return None
                    original = (message if root == event["message_id"] else stop_root if paired_root else
                                self._group_source_message(record["profile"], root))
                    from .event_inbox import lark_event_mentions_bot
                    if observation is None:
                        observation = self.bindings.observe(record["profile"])
                    if (not original or original.get("chat_id") != event["chat_id"]
                            or original.get("sender", {}).get("sender_type") != "user"
                            or original.get("parent_id") or original.get("root_id") not in (None, "", root)
                            or not isinstance(original.get("mentions"), list)
                            or not lark_event_mentions_bot(original, bot_display_name=observation["bot_display_name"],
                                                           bot_app_id=observation["bot_app_id"],
                                                           bot_open_id=observation["bot_open_id"],
                                                           allow_text_fallback=False)):
                        return None
                    if message.get("msg_type", message.get("message_type")) == "text":
                        message["_command_input"] = self._group_text_command_input(message, observation)
                        if not message["_command_input"]:
                            return None  # A display rendering cannot supply missing canonical text.
                # Outbound source reads must join before consuming this
                # operation's proof. Admission retains Core's fenced rejection
                # and its existing response when a binding changes mid-read.
                if not render_content:
                    current = next((row for row in self.bindings.read()["bindings"]
                                    if row.get("binding_id") == record["binding_id"]), None)
                    if current != selected["binding"]:
                        return None
                if sender_identity is not None:
                    if observation is None:
                        return None
                return message
        except (KeyError, ValueError, OSError):
            pass
        return None

    def _source_verified(self, record: dict[str, Any], *, sender_identity: dict[str, str] | None = None,
                         expected_text: str | None = None) -> bool:
        message = self._source_message(record, render_content=False, sender_identity=sender_identity)
        return message is not None and (expected_text is None or (
            message.get("deleted") is not True and message.get("msg_type", message.get("message_type")) == "text"
            and private_source_text(message) == expected_text))

    def _source_authority(self, record: dict[str, Any], profile: str, chat_id: str,
                          message_id: str, *, expected_text: str | None = None) -> tuple[str | None, bool]:
        """Verify one exact outbound operation; retain no identity or grant."""
        if (profile != record["profile"] or chat_id != record["event"]["chat_id"]
                or message_id != record["event"]["message_id"]):
            return None, False
        identity: dict[str, str] = {}
        record_event_timing(profile, message_id, "outbound_source_check_started")
        verified = self._source_verified(record, sender_identity=identity,
            **({"expected_text": expected_text} if expected_text is not None else {}))
        record_event_timing(profile, message_id, "outbound_source_check_returned")
        return identity.get("bot_display_name"), verified

    @staticmethod
    def _group_control_hint(event: Mapping[str, Any]) -> str | None:
        # Rendered event mentions only schedule readonly source IO. The raw
        # provider text and current App identity still authorize the command.
        text = event.get("content")
        if not isinstance(text, str):
            return None
        text = text.strip()
        if text in CHAT_CONTROL_COMMANDS:
            return text
        mentions = event.get("mentions")
        if not isinstance(mentions, list):
            return None
        candidates = set()
        for mention in mentions:
            if not isinstance(mention, Mapping):
                continue
            name = mention.get("name")
            for prefix in (mention.get("key"), "@" + name if isinstance(name, str) else None):
                if (isinstance(prefix, str) and prefix and text.startswith(prefix)
                        and text[len(prefix):len(prefix) + 1].isspace()):
                    command = text[len(prefix):].strip()
                    if command in CHAT_CONTROL_COMMANDS:
                        candidates.add(command)
        return next(iter(candidates)) if len(candidates) == 1 else None

    def _admitted_control_root(self, record: dict[str, Any], binding: dict[str, Any]) -> str | None:
        """Schedule readonly IO only for a topic already admitted by this Core."""
        event = record["event"]
        root = event.get("root_id")
        if (binding.get("enabled") is not True or binding.get("audience") != "group"
                or event.get("message_type") != "text" or not isinstance(event.get("content"), str)
                or self._group_control_hint(event) is None
                or not isinstance(root, str) or not root or root == event["message_id"]):
            return None
        try:
            original_ref = identity_ref(binding["provider_ref"], root)
            original = _read_json(self.core.root / f"{original_ref}.json")
            existing = self.core.root / f"{record['request_ref']}.json"
            if existing.exists() and _read_json(existing).get("message") != event["content"]:
                return None  # Keep older journals' exact rendered representation.
            if (original.get("request_ref") == original_ref
                    and original.get("binding_id") == binding["binding_id"]
                    and original.get("status") == "accepted" and original.get("command") is None
                    and original.get("session_id")
                    and original.get("source", {}).get("group_human_message") is True
                    and original["source"].get("source_ref") == record["source"]["source_ref"]):
                return root
        except (KeyError, ValueError, OSError):
            pass
        return None

    def _admit_group_control(self, path: Path, record: dict[str, Any], binding: dict[str, Any],
                            root: str, *, defer_background_stop: bool) -> dict[str, Any] | None:
        command_input = self._group_control_hint(record["event"])
        if command_input is None:
            return None
        checked = False
        source_verified = False
        # Read only the source and its locally admitted root. Fresh Core
        # identity resolution must follow this IO, inside both fences: an App
        # change while the provider reads run must not inherit an earlier check.
        try:
            record_event_timing(record["profile"], record["event"]["message_id"], "control_source_read_started")
            envelopes = self._original_message_pair(record["profile"], record["event"]["message_id"], root)
            record_event_timing(record["profile"], record["event"]["message_id"], "control_source_read_returned")
        except (KeyError, ValueError, OSError):
            return {"status": "source_verification_failed"}
        if any(message is None for message in envelopes):
            return {"status": "source_verification_failed"}

        def preflight(selected: dict[str, Any]) -> None:
            nonlocal checked, source_verified
            record_event_timing(record["profile"], record["event"]["message_id"], "control_preflight_started")
            checked = True
            if selected["binding"] != binding:
                raise ValueError("conversation binding changed during source read")
            message = self._source_message(record, selected=selected, render_content=False,
                                           source_pair=envelopes)
            if message is None:
                raise ValueError("canonical control source verification failed")
            if message.get("_command_input") != command_input:
                raise _ControlReadFallback
            source_verified = True
            _atomic_write_json(path, record)
            record_event_timing(record["profile"], record["event"]["message_id"], "control_preflight_returned")

        try:
            record_event_timing(record["profile"], record["event"]["message_id"], "core_admission_started")
            admitted = self.core.admit(binding_id=binding["binding_id"], source=record["source"],
                request_ref=record["request_ref"], message=record["event"]["content"],
                command=CHAT_CONTROL_COMMANDS[command_input],
                defer_background_stop=defer_background_stop, source_preflight=preflight)
            record_event_timing(record["profile"], record["event"]["message_id"], "core_admission_returned")
        except _ControlReadFallback:
            return None
        except ValueError:
            if not checked:
                return {"status": "audience_rejected"}
            if not source_verified:
                return {"status": "source_verification_failed"}
            return self._reject_command(path, record, binding)
        return self._record_admission(path, record, admitted)

    @staticmethod
    def _reject_command(path: Path, record: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
        record.update(status="rejected", response=("群助手仅处理当前项目和话题，不能选择个人 Agent 会话；/status 查看状态，/help 查看用法。" if binding.get("audience") == "group" else "操作或原授权不可用；Agent 请先用 /agents 查看确切命令，/project 返回项目对话。新委托请使用 /delegate --tokens N 具体目标，确认或取消请使用原预览中的完整命令。"))
        _atomic_write_json(path, record)
        return {"status": "command_rejected"}

    @staticmethod
    def _record_admission(path: Path, record: dict[str, Any], admitted: dict[str, Any]) -> dict[str, Any]:
        # Keep the recoverable capture while Core holds the original request.
        record.update(status="captured" if admitted["status"] == "prepared" else admitted["status"],
                      session_id=admitted["session_id"], turn_id=admitted["turn_id"],
                      response=admitted.get("response"), response_code=admitted.get("response_code"))
        if admitted.get("context_recovery"):
            record["context_recovery"] = admitted["context_recovery"]
        _atomic_write_json(path, record)
        if admitted["status"] == "prepared" and admitted.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING:
            return {"status": "admission_deferred"}
        status = "durably_accepted" if admitted["status"] == "accepted" else (
            "queue_rejected" if admitted["status"] == "rejected" else "command_recorded")
        return {"status": status}

    @staticmethod
    def _group_text_command_input(message: Mapping[str, Any], observation: Mapping[str, Any]) -> str | None:
        # Display readback replaces native mention keys with names. Parse only
        # the lossless provider text, so names and quoted mentions cannot grant
        # a control operation. Model input keeps its original rendered text.
        try:
            text = json.loads(message["body"]["content"])["text"]
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(text, str):
            return None
        text = text.strip()
        mentions = message.get("mentions")
        if not isinstance(mentions, list):
            return text
        leading = [mention for mention in mentions if isinstance(mention, Mapping)
                   and isinstance(mention.get("key"), str) and mention["key"]
                   and text.startswith(mention["key"])
                   and text[len(mention["key"]):len(mention["key"]) + 1].isspace()]
        if len(leading) == 1:
            identities = {observation["bot_app_id"], observation["bot_open_id"]} - {""}
            if lark_provider_mention_identities(leading[0]).intersection(identities):
                return text[len(leading[0]["key"]):].strip()
        return text

    def _inbox(self, record: dict[str, Any], *, observation: dict[str, Any] | None = None,
               reuse_display_name: bool = False) -> Path:
        scope = record["source"]["source_ref"]
        if not isinstance(scope, str) or not re.fullmatch(r"[a-f0-9]{24}", scope):
            raise ValueError("invalid private source scope")
        path = self.runtime_root / ".loopx" / "config" / "private-chat" / f"{scope}.json"
        if observation is None and reuse_display_name and path.exists():
            try:
                previous = _read_json(path)
                reply = previous.get("reply") or {}
                name = reply.get("bot_display_name")
                if (previous.get("schema_version") == "lark_event_inbox_config_v0"
                        and reply.get("sender_profile") == record["profile"]
                        and reply.get("chat_id") == record["event"]["chat_id"]
                        and isinstance(name, str) and name):
                    observation = {"bot_display_name": name}
            except (OSError, ValueError, AttributeError):
                pass
        if observation is None:
            observation = self.bindings.observe(record["profile"])
        _atomic_write_json(path, {
            "schema_version": "lark_event_inbox_config_v0", "enabled": True,
            "inbox_dir": f".loopx/inbox/private-chat/{scope}", "capture_scope": "configured_chat_all",
            "reply": {"enabled": True, "sender_profile": record["profile"], "sender_identity": "bot",
                "bot_display_name": observation["bot_display_name"], "chat_id": record["event"]["chat_id"],
                "placement_policy": "source_thread" if record["source"].get("group_ref") else "source_context", "received_reaction_emoji": "Get" if self.reaction_feedback else "",
                "received_reaction_policy": "retain", "processing_reaction_emoji": "OnIt" if self.reaction_feedback else ""},
        })
        event = record["event"]
        # Some unsupported attachment events have no rendered content. Keep a
        # source-backed presentation placeholder so the explicit notice can reply.
        content = event.get("content") or "附件（内容尚不支持）"
        captured = ingest_lark_event_inbox(project=self.runtime_root, config_path=path,
            events=[{**event, "content": content, "schema_version": "lark_event_inbox_event_v0"}], execute=True)
        if captured["invalid_count"]:
            raise ValueError("private source could not be captured")
        return path

    def _reply_context_message(self, path: Path, record: dict[str, Any], *,
                               selected: dict[str, Any] | None, source_message: Mapping[str, Any],
                               text: str) -> str:
        """Freeze new ordinary reply context before the existing Core admission.

        Core remains the owner of accepted requests. In particular, an older
        request's exact message must survive an adapter upgrade unchanged.
        """
        if "reply_context_message" in record:
            if record["reply_context_source_text"] != text:
                raise ValueError("reply source content changed")
            return str(record["reply_context_message"])
        event = record["event"]
        if ((self.core.root / f"{record['request_ref']}.json").exists()
                or not (event.get("parent_id") or (event.get("root_id")
                        and event["root_id"] != event["message_id"]))):
            return text
        # Group admission already verified the lossless envelope. Formatted
        # private mget may omit ancestry, so read its exact provider envelope;
        # never treat event-supplied reference text as a provider observation.
        if selected is None:
            raise ValueError("reply binding observation unavailable")
        current = (source_message if selected["binding"].get("audience") == "group"
                   else self._group_source_message(record["profile"], event["message_id"]))
        sender = current.get("sender") if current else None
        if (not current or current.get("message_id") != event["message_id"]
                or current.get("chat_id") != event["chat_id"]
                or not isinstance(sender, Mapping) or sender.get("id") != event["sender_id"]
                or sender.get("sender_type") != "user"
                or current.get("msg_type", current.get("message_type")) != event["message_type"]
                or current.get("deleted") is True
                or (event.get("thread_id") and current.get("thread_id") != event["thread_id"])
                or any(str(current.get(key) or "") != str(event.get(key) or "")
                       for key in ("parent_id", "root_id"))):
            raise ValueError("reply source ancestry unavailable or changed")
        def read(args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            result = call(self.runner, list(args))
            return subprocess.CompletedProcess(args, int(result["returncode"]),
                str(result.get("stdout") or ""), str(result.get("stderr") or ""))

        context = enrich_lark_event_reply_context(event, runner=read,
            command_prefix=[self.cli_bin], profile=record["profile"],
            profile_app_id=selected["observation"]["bot_app_id"],
            configured_chat_id=event["chat_id"], current_message=current)
        projected = conversation_reply_context({**context, "source_conversation_id": event["chat_id"]})
        prefix = str(projected["context_text"])
        message = f"{prefix}\n\nCurrent user message:\n{text}" if prefix else text
        # A compact private event can omit its thread ID. Retain the verified
        # identity for redelivery even when the provider has no thread excerpt.
        thread_id = context.get("thread_id")
        if (event["chat_type"] == "p2p" and event.get("parent_id") and not event.get("thread_id")
                and context.get("reply_context_verified") is True
                and isinstance(thread_id, str) and 0 < len(thread_id) <= 200):
            record["event"] = {**event, "thread_id": thread_id}
        record.update(reply_context_message=message, reply_context_source_text=text)
        _atomic_write_json(path, record)
        return message

    @effect_runtime_request_scope()
    def admit(self, profile: str, event: dict[str, Any], *,
              defer_background_stop: bool = False, history_review: bool = False,
              expected_binding_id: str | None = None) -> dict[str, Any]:
        record_event_timing(profile, str(event.get("message_id") or ""), "admission_started")
        try:
            binding = self._binding(profile)
            if expected_binding_id is not None and binding["binding_id"] != expected_binding_id:
                return {"status": "audience_rejected"}
            source = lark_private_source(provider_ref=binding["provider_ref"], event=event)
            if history_review and (binding.get("context_kind") != "steward"
                    or binding.get("audience", "owner") != "owner" or event.get("chat_type") != "p2p"):
                return {"status": "history_answer_held"}
            request = identity_ref(binding["provider_ref"], event["message_id"])
            control_root = self._admitted_control_root({"request_ref": request, "source": source,
                                                       "event": event}, binding)
        except (KeyError, StopIteration, ValueError):
            return {"status": "audience_rejected"}
        request = identity_ref(binding["provider_ref"], event["message_id"])
        path = self.root / f"{request}.json"
        record_event_timing(profile, event["message_id"], "binding_hint_ready")
        with exclusive_file_lock(path, operation="capture_private_chat_request"):
            record_event_timing(profile, event["message_id"], "capture_lock_acquired")
            # Retain a candidate from the locally bound owner before provider
            # identity IO can fail. This private receipt grants no execution:
            # recovery still verifies the App, current owner, source and Core
            # grant before creating a Turn. Groups and other senders keep their
            # existing pre-capture privacy boundary.
            if (not path.exists() and binding.get("enabled") is True
                    and binding.get("audience", "owner") == "owner"
                    and source.get("private_human_message") is True
                    and source["sender_ref"] == binding["operator_ref"]):
                _atomic_write_json(path, {"schema_version": "lark_private_chat_delivery_v0",
                    "request_ref": request, "profile": profile, "binding_id": binding["binding_id"],
                    "source": source, "event": event, "deliveries": {}, "status": "captured",
                    **({"capture_origin": "provider_history"} if history_review else {})})
            try:
                selected = (None if control_root else self.bindings.resolve(
                    binding_id=binding["binding_id"], **source, include_observation=True))
            except (KeyError, StopIteration, ValueError):
                return {"status": "audience_rejected"}
            if path.exists():
                record = _read_json(path)
                if history_review and record.get("capture_origin") != "provider_history":
                    return {"status": "history_answer_held"}
                # event_id can change on redelivery; canonical message content
                # and audience cannot change underneath the stable message id.
                # A compact private redelivery may omit a thread ID already
                # verified and frozen with the original reply context.
                saved_event = record["event"]
                omitted_private_thread = (event["chat_type"] == "p2p" and not event.get("thread_id")
                                          and "reply_context_message" in record)
                if any(saved_event.get(key) != event.get(key) for key in (
                        "message_id", "sender_id", "chat_id", "content", "message_type",
                        "chat_type", "sender_type", "root_id", "parent_id")) or (
                        not omitted_private_thread and saved_event.get("thread_id") != event.get("thread_id")):
                    return {"status": "source_conflict"}
            else:
                record = {"schema_version": "lark_private_chat_delivery_v0", "request_ref": request,
                    "profile": profile, "binding_id": binding["binding_id"], "source": source,
                    "event": event, "deliveries": {}, "status": "captured",
                    **({"capture_origin": "provider_history"} if history_review else {})}
                if binding.get("audience") != "group":
                    _atomic_write_json(path, record)
            # Verified transport-only replies have no Core Turn to recover.
            if record["status"] == "delivered" and not {"session_id", "turn_id"}.intersection(record):
                return {"status": "triage_delivered" if "triage" in record else "command_rejected"}
            if control_root:
                result = self._admit_group_control(path, record, binding, control_root,
                                                  defer_background_stop=defer_background_stop)
                if result is not None:
                    return result
                try:
                    selected = self.bindings.resolve(binding_id=binding["binding_id"], **source,
                                                     include_observation=True)
                except ValueError:
                    return {"status": "audience_rejected"}
            # Reuse this admission preflight only; Core rechecks under its
            # source fence, and outbound writes always perform a fresh check.
            source_message = self._source_message(record, selected=selected)
            if source_message is None:
                return {"status": "source_verification_failed"}
            if (record.get("capture_origin") == "provider_history"
                    and source_message.get("content") != event.get("content")):
                return {"status": "history_answer_held"}
            if binding.get("audience") == "group":
                _atomic_write_json(path, record)
            message_type = str(event.get("message_type") or "")
            text = ""
            attachments: list[dict[str, Any]] = []
            if message_type == "text":
                # lark-cli renders event and mget content as plain text. Read
                # the full canonical message; do not decode a rendered event.
                canonical_type = source_message.get("msg_type", source_message.get("message_type"))
                if canonical_type != "text":
                    return {"status": "source_conflict"}
                text = private_source_text(source_message)
                if not text or not text.strip():
                    return {"status": "invalid_text" if text is None else "empty_text"}
                captured = triage.capture_message(self, path, record, selected, text, history_review=history_review)
                if captured is not None:
                    return captured
            elif message_type in {"image", "post"}:
                if source_message.get("msg_type", source_message.get("message_type")) != message_type:
                    return {"status": "source_conflict"}
                content = source_message.get("content")
                if not isinstance(content, str):
                    return {"status": "source_verification_failed"}
                if record.get("source_content", content) != content:
                    return {"status": "source_conflict"}
                record["source_content"] = content
                if "attachments" not in record and not record.get("attachment_notice"):
                    try:
                        text, attachments = private_message_images(content=content, message_type=message_type,
                            message_id=event["message_id"], profile=profile, cli_bin=self.cli_bin, runner=self.runner)
                        record.update(message=text, attachments=attachments)
                    except ValueError as exc:
                        record["attachment_notice"] = str(exc)
                    _atomic_write_json(path, record)
                text, attachments = record.get("message", ""), record.get("attachments", [])
            command_input = private_message_caption(record["source_content"]) if attachments else text.strip()
            if binding.get("audience") == "group" and message_type == "text":
                command_input = source_message["_command_input"]
            command = CHAT_CONTROL_COMMANDS.get(command_input)
            if command_input == "/agents":
                command = "agents"
            elif command_input == "/project":
                command = "select_project"
            elif command_input == "/agent" or command_input.startswith("/agent "):
                command = "select_agent"
            if binding["context_kind"] == "steward":
                for prefix, selected_command in [("/delegate", "commission"), ("/委托", "commission"),
                                                  ("/confirm", "confirm_commission"), ("/cancel", "cancel_commission"),
                                                  ("/stop-commission", "stop_commission"), ("/resume-commission", "resume_commission")]:
                    if command_input == prefix or command_input.startswith(prefix + " "):
                        command = selected_command
                        break
            if message_type not in {"text", "image", "post"} or record.get("attachment_notice"):
                command = "unsupported"
            if command is None:
                try:
                    text = self._reply_context_message(path, record, selected=selected,
                        source_message=source_message, text=text)
                except (KeyError, ValueError, OSError):
                    return {"status": "source_verification_failed"}
            try:
                record_event_timing(profile, event["message_id"], "core_admission_started")
                if record.get("capture_origin") == "provider_history":
                    if message_type != "text" or command is not None or attachments:
                        return {"status": "history_answer_held"}
                    canonical = self._group_source_message(profile, event["message_id"])
                    def raw_source(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
                        sender = value.get("sender") if value else None
                        body = value.get("body") if value else None
                        if (not value or value.get("message_id") != event["message_id"]
                                or value.get("chat_id") != event["chat_id"] or not isinstance(sender, Mapping)
                                or sender.get("id") != event["sender_id"] or sender.get("sender_type") != "user"
                                or value.get("msg_type", value.get("message_type")) != "text"
                                or value.get("deleted") is True or not str(value.get("create_time", "")).isdigit()
                                or not isinstance(body, Mapping) or not isinstance(body.get("content"), str)):
                            return None
                        return {key: value.get(key) for key in
                            ("message_id", "chat_id", "sender", "create_time", "body", "parent_id", "root_id", "thread_id")}
                    frozen = raw_source(canonical)
                    if frozen is None or record.get("history_source", frozen) != frozen:
                        return {"status": "history_answer_held"}
                    record["history_source"] = frozen
                    _atomic_write_json(path, record)
                    def verify_history(fresh: dict[str, Any]) -> None:
                        # Only these two source reads run under Core's source
                        # fence. Bound and overlap them so an old question does
                        # not hold new live input behind a transport timeout.
                        def read(tail: list[str]) -> Mapping[str, Any] | None:
                            value = call(lambda argv, cwd, _timeout: self.runner(argv, cwd, 5),
                                lark_args(cli_bin=self.cli_bin, profile=profile, tail=tail))
                            payload = json_payload(value)
                            if (value.get("returncode") != 0 or payload.get("ok") is False
                                    or payload.get("code", 0) != 0 or payload.get("identity") not in (None, "bot")):
                                return None
                            return _message(payload, event["message_id"])
                        with ThreadPoolExecutor(max_workers=2) as reads:
                            rendered = reads.submit(read, ["im", "+messages-mget", "--message-ids",
                                event["message_id"], "--as", "bot", "--no-reactions", "--format", "json"])
                            raw = reads.submit(read, ["api", "GET", f"/open-apis/im/v1/messages/{event['message_id']}", "--as", "bot"])
                            reread, raw_read = rendered.result(), raw.result()
                        if (reread is None or reread.get("content") != source_message.get("content")
                                or raw_source(raw_read) != frozen or fresh["binding"] != selected["binding"]):
                            raise ValueError("historical source changed")
                    from datetime import datetime, timezone
                    created = datetime.fromtimestamp(int(frozen["create_time"]) / 1000, timezone.utc).isoformat()
                    admitted = self.core.admit_history_answer(binding_id=binding["binding_id"], source=source,
                        request_ref=request, message=text, source_created_at=created, source_preflight=verify_history)
                    if admitted["status"] == "prepared":
                        record["history_answer_hold"] = admitted.get("history_answer_hold", "original_reservation_unavailable")
                        _atomic_write_json(path, record)
                        return {"status": "history_answer_held"}
                else:
                    preflight = (steward_source_preflight(selected["binding"], source_message,
                        lambda fresh: self._source_message(record, selected=fresh)) if command is None else None)
                    admitted = self.core.admit(binding_id=binding["binding_id"], source=source,
                        request_ref=request, message=text, command=command, attachments=attachments,
                        defer_background_stop=defer_background_stop, source_preflight=preflight)
                record_event_timing(profile, event["message_id"], "core_admission_returned")
            except PrivateSourceUnavailable:
                return {"status": "source_verification_failed"}
            except ValueError:
                if record.get("capture_origin") == "provider_history":
                    return {"status": "history_answer_held"}
                return self._reject_command(path, record, binding)
            except RuntimeError as exc:
                if str(exc) != "session_queue_full":
                    raise
                record.update(status="rejected", response="队列已满，本条没有被受理；请稍后重试。")
                _atomic_write_json(path, record)
                return {"status": "queue_rejected"}
            return self._record_admission(path, record, admitted)

    def _reply_runner(self, args: Sequence[str]) -> Any:
        return self.runner([self.cli_bin, *args[1:]], None, 30)

    def return_inbox(self, *, route: dict[str, Any], session: dict[str, Any],
                     turn: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        """Resolve an original worker return through its admitted private source.

        Core revalidates the saved steward context. The two correlation records
        must still name the exact canonical Session/Turn; neither the current
        conversation nor a worker-supplied destination can replace that source.
        Manager-context retains ownership of the return attempt and recovery.
        """
        saved = session["steward_context"]
        selected = self.bindings.session_context(saved)
        client = str(turn.get("client_turn_id") or "")
        match = re.fullmatch(r"external-([a-f0-9]{24})(?:-.+)?", client)
        if match is None:
            raise ValueError("original private request unavailable")
        request = match[1]
        record = _read_json(self.root / f"{request}.json")
        native = self.core.read_request(request)
        if client != f"external-{request}":
            # A suffix locates the journal, but proves no continuation. Reuse
            # Core's recorded recovery identity instead of maintaining another
            # grammar that can strand quota/routing/work continuations.
            recovery = self.core.recovery_context(session, turn)
            if (recovery is None or recovery.get("request_ref") != request
                    or record.get("context_recovery") != native.get("context_recovery")):
                raise ValueError("original private recovery unavailable")
        message_id = str(record.get("event", {}).get("message_id") or "")
        if (route["source_id"] not in (request, f"lark:{message_id}")
                or identity_ref(selected["binding"]["provider_ref"], message_id) != request
                or session.get("goal_id") != "loopx-manager"
                or session.get("channel_id") != selected["channel_id"]
                or route["goal_id"] not in selected["context"]["goal_ids"]
                or route["session_id"] != session["session_id"]
                or turn.get("session_id") != session["session_id"]
                or record.get("profile") != selected["binding"]["transport_ref"]
                or record.get("request_ref") != request
                or record.get("source") != lark_private_source(
                    provider_ref=selected["binding"]["provider_ref"], event=record["event"])
                or record["source"]["source_ref"] != saved["source_ref"]
                or any(row.get("binding_id") != saved["binding_id"]
                       or row.get("source") != record["source"]
                       or row.get("session_id") != session["session_id"]
                       or row.get("turn_id") != turn["turn_id"]
                       for row in (record, native))):
            raise ValueError("original private return source changed")
        return self._inbox(record), record

    def _feedback(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path], processing: bool = False) -> None:
        """Render observed Core admission/execution through the shared Inbox owner.

        Presentation failures never reject a persisted Turn. Existing reaction
        journals prevent uncertain provider writes from being repeated on replay.
        """
        if not self.reaction_feedback or record.get("context_recovery"):
            # The original failure already acknowledged this Inbox source.
            # Preserve its reaction journal; a continuation uses its own draft
            # rather than trying to reopen the completed processing reaction.
            return
        phase = "processing" if processing else "received"
        feedback = record.setdefault("feedback", {})
        if feedback.get(phase, {}).get("ok"):
            return
        config = inbox()
        if processing:
            result = mark_lark_event_inbox_processing(project=self.runtime_root, config_path=config,
                message_id=record["event"]["message_id"], execute=True, runner=self._reply_runner)
        else:
            result = mark_lark_event_inbox_received(project=self.runtime_root, config_path=config,
                event=record["event"], runner=self._reply_runner)
        feedback[phase] = result
        _atomic_write_json(path, record)

    def _deliver(self, path: Path, record: dict[str, Any], phase: str, text: str, *, inbox: Callable[[], Path],
                 edit_only: bool = False, authorization_check: Callable[[], bool] | None = None,
                 reply_placement: str | None = None, delivery_id: str | None = None,
                 finalize_source: bool = True, image_attachments: list[dict[str, Any]] | None = None,
                 historical_source_check: Callable[[], bool] | None = None) -> bool:
        if not text:
            return False
        if reply_placement is not None and (record["event"].get("chat_type") != "p2p"
                or record["source"].get("group_ref")):
            raise ValueError("a new private result requires its captured private source")
        if authorization_check is not None and not authorization_check():
            return False
        kind = phase.split(".", 1)[0]
        if historical_source_check is not None and phase != "answer_images.historical":
            raise ValueError("historical source verification requires a media repair")
        phase = presentation_phase(record, phase)
        expected_text = record["event"]["content"] if kind == "answer_images" else None
        phase_state = record["deliveries"].get(phase, {})
        if phase_state.get("verified"):
            return True
        # Repair presentation before recording the exact provider intent. Core's
        # answer stays unchanged. An older frozen intent may contain raw CRLF;
        # preserve it when it still matches Core, letting Inbox verify its wire.
        if phase_state.get("text") != text:
            text = _presentation_text(text)
        if phase_state.get("text") not in (None, text):
            raise ValueError("delivery content changed after an attempt")
        if image_attachments and (authorization_check is None or edit_only
                or not ((kind == "web" and reply_placement == "chat_root")
                        or (kind == "answer_images" and record["event"].get("chat_type") == "p2p"
                            and record["source"].get("private_human_message") is True))):
            raise ValueError("Image projection requires its original private outbox")
        record_event_timing(record["profile"], record["event"]["message_id"], "delivery_started")
        config = inbox()
        record_event_timing(record["profile"], record["event"]["message_id"], "delivery_inbox_ready")
        def verified_source() -> bool:
            if historical_source_check is not None:
                return historical_source_check()
            name, verified = self._source_authority(record, record["profile"],
                record["event"]["chat_id"], record["event"]["message_id"], expected_text=expected_text)
            return bool(name) and verified

        def image_authorized() -> bool:
            # A provider read can overlap Stop, Close, or an edit to the old reply.
            return authorization_check is not None and (
                verified_source() and authorization_check() if kind == "answer_images"
                else authorization_check() and verified_source())

        image_keys = ()
        if image_attachments:
            resource_scope = (f"{record['binding_id']}:{record['request_ref']}:{phase}"
                              if kind == "answer_images" else record["binding_id"])
            image_keys = uploaded_chat_images(root=self.runtime_root, profile=record["profile"],
                provider_ref=resource_scope, attachments=image_attachments, private_transport=self,
                before_upload=image_authorized, verify_only=bool(phase_state.get("started") or phase_state.get("attempt")),
                dispatch_guard=partial(answer_media_dispatch, self.core, record) if kind == "answer_images" else None)
            if phase_state.get("image_keys", list(image_keys)) != list(image_keys):
                raise ValueError("App image delivery resources changed after an attempt")
            phase_state["image_keys"] = list(image_keys)
            record["deliveries"][phase] = phase_state
            _atomic_write_json(path, record)
        kwargs = dict(project=self.runtime_root, config_path=config,
            message_id=record["event"]["message_id"], text=text, runner=self._reply_runner,
            **({"image_keys": image_keys} if image_keys else {}),
            **({"reply_placement": reply_placement} if reply_placement is not None else {}),
            **({"delivery_id": delivery_id} if delivery_id is not None else {}),
            finalize_reactions=finalize_source and not edit_only and kind not in {"admission", "progress", "periodic_report"} and not (kind == "terminal" and record.get("commission_resources")),
            source_authority_verifier=lambda profile, chat, message: (
                self._source_authority(record, profile, chat, message, expected_text=expected_text)
                if authorization_check is None or authorization_check() else (None, False)))
        progress = record["deliveries"].get(presentation_phase(record, "progress")) or {}
        stream = presentation_stream(record)
        edit = phase_state.get("edit_from")
        if not edit_only and kind in {"terminal", "commission_result"} and progress.get("attempt") and not stream.get("closed") and not stream.get("edit_limit_reached") and not phase_state.get("started"):
            # Freeze the known message proof before the first final edit. Later
            # recovery can replace the same ID, never append another answer.
            edit = {"text": _presentation_text(stream.get("confirmed_text") or progress["text"]),
                    "attempt": stream.get("confirmed_attempt") or progress["attempt"]}
            phase_state["edit_from"] = edit
        if edit_only and not edit:
            return False
        if edit and reply_placement is not None:
            raise ValueError("a new private message cannot replace an existing source reply")

        def before_send(_intent: str) -> dict[str, bool]:
            # Recheck source and grant after preview, including resumed parts.
            if authorization_check is not None and not authorization_check():
                return {"continue_delivery": False}
            if not verified_source() or (authorization_check is not None and not authorization_check()):
                return {"continue_delivery": False}
            phase_state.update(text=text, started=True)
            record["deliveries"][phase] = phase_state
            _atomic_write_json(path, record)
            return {"continue_delivery": True}

        if edit:
            def edit_attempt(value: Any) -> None:
                phase_state["attempt"] = dict(value)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)

            result = update_lark_inbox_reply(**kwargs, previous_text=edit["text"], attempt=edit["attempt"],
                before_send=before_send, delivery_attempt_recorder=edit_attempt,
                require_previous_text_match=edit_only)
            if not edit_only and result.get("blocker") == "provider_update_edit_limit":
                # A confirmed edit rejection plus fresh non-matching readback
                # proves this final did not reach the draft. Preserve its proof
                # and use the existing source-bound, idempotent final sender.
                # Recovery of older, already-started final edits takes this
                # same path; an ambiguous failure must never take it.
                stream["edit_limit_reached"] = True
                phase_state["rejected_update"] = {"blocker": result["blocker"],
                    "edit_from": edit, "attempt": phase_state.get("attempt")}
                for key in ("edit_from", "attempt", "started"):
                    phase_state.pop(key, None)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)
                return self._deliver(path, record, phase, text, inbox=inbox, finalize_source=finalize_source)
            if not edit_only and result.get("blocker") in {"provider_update_too_large", "provider_update_format_unsupported"}:
                # Do not truncate long/mention-bearing final answers to fit a
                # draft. Close it, then use the established full final sender.
                if not self._close_progress(path, record, inbox=inbox) and not stream.get("edit_limit_reached"):
                    return False
                phase_state.pop("edit_from", None)
                record["deliveries"][phase] = phase_state
                _atomic_write_json(path, record)
                return self._deliver(path, record, phase, text, inbox=inbox, finalize_source=finalize_source)
        elif phase_state.get("attempt"):
            result = verify_lark_inbox_reply(**kwargs, attempt=phase_state["attempt"], resume_parts=True,
                                            before_send=before_send)
        elif phase_state.get("started"):
            # A crash between provider write and its receipt is ambiguous. Keep
            # the original source recoverable; never resend blindly.
            phase_state["blocker"] = "provider_delivery_receipt_unknown"
            record["deliveries"][phase] = phase_state
            _atomic_write_json(path, record)
            return False
        else:
            def attempt(value: Any) -> None:
                phase_state["attempt"] = dict(value)
                _atomic_write_json(path, record)

            result = reply_lark_event_inbox(**kwargs, execute=True, before_send=before_send,
                                           delivery_attempt_recorder=attempt, short_message_limit=None,
                                           content_format="markdown",
                                           overlap_source_preflight=bool(kind == "terminal" and self._completed_stop_root(
                                               record, next((row for row in self.bindings.read()["bindings"]
                                                             if row.get("binding_id") == record["binding_id"]), {}))))
        # A verified intermediate reply is not completion. A final reply with
        # cleanup owed recovers its original attempt; it must never be resent.
        if image_attachments and result.get("reply_verified") is True and result.get("ok") is True:
            from .post_delivery import attachment_reply
            image_text, image_attempt = attachment_reply(project=self.runtime_root, config_path=config,
                attempt=phase_state["attempt"], text=text)
            verified = verify_chat_images(profile=record["profile"], message_id=image_attempt["message_ref"],
                text=image_text, attachments=image_attachments, keys=image_keys, private_transport=self,
                before_read=image_authorized)
            result.update(verified)
            phase_state["images_verified"] = verified.get("reply_verified") is True
        phase_state["verified"] = result.get("reply_verified") is True and result.get("ok") is True
        phase_state["blocker"] = result.get("blocker")
        record["deliveries"][phase] = phase_state
        if not edit_only and record.get("context_recovery") and kind == "terminal" and phase_state["verified"]:
            stream["closed"] = True
        _atomic_write_json(path, record)
        record_event_timing(record["profile"], record["event"]["message_id"], "delivery_returned")
        return bool(phase_state["verified"])

    def _deliver_completed_answer(self, path: Path, record: dict[str, Any], text: str, *,
                                  inbox: Callable[[], Path]) -> bool:
        phase = presentation_phase(record, "terminal")
        media_phase = "answer_images." + phase
        old = record["deliveries"].get(phase) or {}
        if old.get("started") and not record.get("answer_images"):
            return self._deliver(path, record, "terminal", text, inbox=inbox)
        if record["event"].get("chat_type") != "p2p" or record["source"].get("private_human_message") is not True:
            return self._deliver(path, record, "terminal", text, inbox=inbox)
        body, targets = _private_answer_images(text)
        if not targets:
            return self._deliver(path, record, "terminal", text, inbox=inbox)

        def authorized() -> bool:
            native = self.core.read_request(record["request_ref"])
            same_request = all(native.get(key) == record[key] for key in ("binding_id", "source", "session_id", "turn_id"))
            if native.get("status") != "accepted" or not same_request:
                return False
            turn = self.core.controller.store.load_turn(native["session_id"], native["turn_id"])
            return resumable_answer_image_session(self.core, native, turn) is not None

        state = record.setdefault("answer_images", {})
        # Hold the source and the exact completed body fixed across retries.
        digest = hashlib.sha256(text.encode()).hexdigest()
        if state.setdefault("answer_sha256", digest) != digest:
            return False
        if old.get("verified") is not True:
            state.setdefault("status", "pending")
            _atomic_write_json(path, record)
            self._deliver(path, record, "terminal", body + "\n\n原图会另发；有链接的图名可打开原始来源。",
                          inbox=inbox, finalize_source=False)
            # Release the body worker before the pending image work.
            return False
        if state.get("status") == "delivered" and record["deliveries"].get(media_phase, {}).get("images_verified") is True:
            return True
        now = datetime.now(timezone.utc).timestamp()
        if _answer_image_retry_waits(record, now):
            return False
        state.update(status="pending", next_check_at=now + ANSWER_IMAGE_RETRY_SECONDS)
        _atomic_write_json(path, record)
        try:
            event = record["event"]
            origin = event.get("content")
            if (len(targets) > CHAT_IMAGE_MAX_COUNT or event.get("message_type") != "text"
                    or not isinstance(origin, str) or not authorized() or not self._source_authority(
                        record, record["profile"], event["chat_id"], event["message_id"], expected_text=origin)[1]):
                raise ValueError("answer image source unavailable or not authorized")
            images = state.get("attachments")
            if images is None or state.get("source_urls_verified") is not True:
                images = state["attachments"] = _read_answer_images(origin, targets, images,
                                                                    record["deliveries"].get(media_phase, {}))
                state["source_urls_verified"] = True
                _atomic_write_json(path, record)
            if self._deliver(path, record, media_phase, "原图（按正文顺序）", inbox=inbox,
                             authorization_check=authorized, finalize_source=False,
                             image_attachments=images):
                if self.reaction_feedback and not _reconcile_outbound_input_reactions(
                        project=self.runtime_root, config_path=inbox(), message_id=record["event"]["message_id"],
                        processing=False, complete=True, before_write=authorized, runner=self._reply_runner).get("ok"):
                    return False
                state.update(status="delivered")
                state.pop("attachments", None)
                _atomic_write_json(path, record)
                return True
        except (KeyError, ValueError, OSError, http.client.HTTPException):
            pass
        state["status"] = "unverified"
        _atomic_write_json(path, record)
        self._deliver(path, record, "answer_image_notice." + phase,
                      "正文已送达，图片尚未核验送达。有链接的图名可打开原始来源。",
                      inbox=inbox, authorization_check=authorized, finalize_source=False)
        return False

    def _historical_image_source(self, record: Mapping[str, Any], plan: Mapping[str, Any]) -> dict:
        event = record["event"]
        terminal = record["deliveries"]["terminal"]
        if (record["status"] != "delivered" or record.get("context_recovery")
                or event.get("chat_type") != "p2p" or event.get("message_type") != "text"
                or record["source"].get("private_human_message") is not True
                or record["binding_id"] != plan["binding_id"] or record["turn_id"] != plan["turn_id"]
                or terminal.get("verified") is not True or terminal.get("image_keys")
                or terminal.get("attempt", {}).get("message_ref") != plan["message_id"]):
            raise ValueError("original text-only reply unavailable")
        native = self.core.read_request(record["request_ref"])
        if (native.get("status") != "accepted" or native.get("origin") != "lark"
                or native.get("command") is not None or native.get("agent_target")
                or native.get("delivery_verified") is not True
                or any(native.get(key) != record.get(key)
                       for key in ("request_ref", "binding_id", "source", "session_id", "turn_id"))):
            raise ValueError("original request no longer matches")
        turn = self.core.controller.store.load_turn(record["session_id"], record["turn_id"]) or {}
        if (not isinstance(answer := (turn.get("response") or {}).get("message"), str)
                or hashlib.sha256(answer.encode()).hexdigest() != plan["answer_sha256"]
                or terminal.get("text") != _presentation_text(answer)
                or not _private_answer_images(answer)[1]
                or (session := resumable_answer_image_session(self.core, native, turn)) is None):
            raise ValueError("original completed answer or its authority changed")
        return session

    def _historical_image_plan(self, record: Mapping[str, Any], plan: Mapping[str, Any]) -> dict:
        from ..public_source_reader import public_url

        if set(plan) != {"binding_id", "turn_id", "message_id", "answer_sha256", "images"}:
            raise ValueError("invalid original image repair fields")
        if (not isinstance(plan["message_id"], str) or not LARK_MESSAGE_ID_PATTERN.fullmatch(plan["message_id"])
                or not isinstance(plan["answer_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", plan["answer_sha256"])
                or not isinstance(plan["images"], list) or not 1 <= len(plan["images"]) <= CHAT_IMAGE_MAX_COUNT):
            raise ValueError("invalid original image repair target")
        images = []
        for image in plan["images"]:
            if (not isinstance(image, dict) or set(image) != {"url", "sha256"}
                    or not isinstance(image["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", image["sha256"])
                    or not isinstance(image["url"], str)):
                raise ValueError("each original image requires a URL and its SHA-256")
            images.append({"url": public_url(image["url"]), "sha256": image["sha256"]})
        if len({image["url"] for image in images}) != len(images):
            raise ValueError("duplicate original image URL")
        self._historical_image_source(record, plan)
        return {**plan, "images": images}

    def _load_historical_image_plan(self, path: Path, record: dict[str, Any]) -> dict | None:
        if record.get("historical_answer_images") is not None:
            return record["historical_answer_images"]
        if not re.fullmatch(r"[a-f0-9]{24}", path.stem):
            raise ValueError("invalid original request reference")
        # This is a private local operator input, scoped to the existing receipt.
        # It is never loaded from a project, provider message or model response.
        spec = self.root / "historical-answer-images" / path.name
        try:
            with spec.open("rb") as handle:
                raw = handle.read(40_001)
        except FileNotFoundError:
            return None
        if len(raw) > 40_000:
            raise ValueError("original image repair descriptor exceeds its size limit")
        plan = json.loads(raw)
        if not isinstance(plan, dict) or path.stem != record.get("request_ref"):
            raise ValueError("invalid original image repair descriptor")
        plan = self._historical_image_plan(record, plan)
        state = record["historical_answer_images"] = {"plan": plan, "status": "pending"}
        _atomic_write_json(path, record)
        return state

    def _reconcile_historical_answer_images(self, path: Path) -> int:
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            try:
                state = self._load_historical_image_plan(path, record)
            except (KeyError, ValueError, OSError):
                return 0
            now = datetime.now(timezone.utc).timestamp()
            if not state or state.get("status") != "pending" or state.get("next_check_at", 0) > now:
                return 0
            state["next_check_at"] = now + ANSWER_IMAGE_RETRY_SECONDS
            _atomic_write_json(path, record)
            plan = state["plan"]
            try:
                session = self._historical_image_source(record, plan)
                selected = self.bindings.resolve(binding_id=plan["binding_id"], session=session,
                                                 session_context=session.get("project_context") or session.get("steward_context"),
                                                 include_observation=True, **record["source"])
                event = record["event"]
                observation = selected["observation"]
                if selected["binding"]["transport_ref"] != record["profile"]:
                    return 0
                original_text = normalize_lark_outbound_text(
                    record["deliveries"]["terminal"]["text"], limit=None, preserve_format=True)
                def original_pair_matches() -> bool:
                    source, reply = self._original_message_pair(record["profile"], event["message_id"], plan["message_id"])
                    return historical_image_source_matches(source, event) and historical_image_reply_matches(
                        reply, event["chat_id"], observation, original_text)

                if not original_pair_matches():
                    return 0
                phase = "answer_images.historical"
                attachments = state.get("attachments")
                recheck_sources = attachments is None or state.get("source_urls_verified") is not True
                if recheck_sources:
                    attachments = _read_answer_images(event["content"], [item["url"] for item in plan["images"]],
                                                      attachments, record["deliveries"].get(phase, {}))
                digests = [hashlib.sha256(base64.b64decode(
                    picture["data_url"].split(",", 1)[1], validate=True)).hexdigest() for picture in attachments]
                if digests != [item["sha256"] for item in plan["images"]]:
                    return 0
                if recheck_sources:
                    state.update(attachments=attachments, source_urls_verified=True)
                    _atomic_write_json(path, record)
                def authorized() -> bool:
                    try:
                        return (selected["binding"] in self.bindings.read()["bindings"]
                                and bool(self._historical_image_source(record, plan)))
                    except (KeyError, OSError, ValueError):
                        return False

                if not self._deliver(path, record, phase, "原图（按原文顺序）",
                                     inbox=lambda: self._inbox(record, observation=observation),
                                     authorization_check=authorized, historical_source_check=original_pair_matches,
                                     finalize_source=False,
                                     reply_placement="chat_root", image_attachments=attachments):
                    return 0
                media = record["deliveries"][phase]
                output = call(self.runner, lark_args(cli_bin=self.cli_bin, profile=record["profile"], tail=[
                    "im", "+messages-mget", "--message-ids", media["attempt"]["message_ref"],
                    "--as", "bot", "--no-reactions", "--format", "json"]))
                sent = _message(json_payload(output), media["attempt"]["message_ref"]) if output.get("returncode") == 0 else None
                if not historical_image_reply_matches(sent, event["chat_id"], observation, media["text"], tuple(media["image_keys"])):
                    return 0
            except (KeyError, ValueError, OSError, http.client.HTTPException):
                return 0
            state.update(status="delivered")
            state.pop("attachments", None)
            _atomic_write_json(path, record)
            return 1

    def _edit_progress(self, path: Path, record: dict[str, Any], text: str, *, inbox: Callable[[], Path], finalizing: bool = False) -> bool:
        text = _presentation_text(_private_answer_images(text)[0] if record["event"].get("chat_type") == "p2p" else text)
        progress = record["deliveries"].get(presentation_phase(record, "progress")) or {}
        if not progress.get("verified"):
            return self._deliver(path, record, "progress", text, inbox=inbox)
        stream = presentation_stream(record)
        if stream.get("edit_limit_reached") or (not finalizing and int(stream.get("edit_attempts") or 0) >= PROGRESS_EDIT_BUDGET):
            return False
        confirmed: dict[str, Any] = {}

        def before_send(_intent: str) -> dict[str, bool]:
            # Persist before the provider write: an uncertain acknowledgement
            # may have consumed an edit too. Restarts retain the same budget.
            stream["edit_attempts"] = int(stream.get("edit_attempts") or 0) + 1
            _atomic_write_json(path, record)
            return {"continue_delivery": True}

        result = update_lark_inbox_reply(project=self.runtime_root, config_path=inbox(),
            message_id=record["event"]["message_id"], text=text,
            previous_text=_presentation_text(stream.get("confirmed_text") or progress["text"]),
            attempt=stream.get("confirmed_attempt") or progress["attempt"],
            runner=self._reply_runner,
            source_authority_verifier=lambda profile, chat, message: self._source_authority(record, profile, chat, message),
            before_send=before_send, delivery_attempt_recorder=lambda value: confirmed.update(value))
        stream["blocker"] = result.get("blocker")
        if result.get("blocker") == "provider_update_edit_limit":
            stream["edit_limit_reached"] = True
        if result.get("reply_verified") is True and result.get("ok") is True:
            stream.update(confirmed_text=text, confirmed_attempt=confirmed)
        _atomic_write_json(path, record)
        return result.get("ok") is True

    def _close_progress(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        if self._retire_progress_parts(path, record, inbox=inbox):
            return True
        text = "回答已生成，完整结果将单独发送。"
        if not self._edit_progress(path, record, text, inbox=inbox, finalizing=True):
            return False
        presentation_stream(record)["closed"] = True
        _atomic_write_json(path, record)
        return True

    def _retire_progress_parts(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        """Verify every concrete draft post before retiring a split draft."""
        from .post_delivery import POST_PARTS_LOCATOR

        progress = record["deliveries"].get(presentation_phase(record, "progress")) or {}
        stream = presentation_stream(record)
        attempt = stream.get("confirmed_attempt") or progress.get("attempt") or {}
        if not POST_PARTS_LOCATOR.fullmatch(str(attempt.get("message_ref") or "")):
            return False
        result = verify_lark_inbox_reply(project=self.runtime_root, config_path=inbox(),
            message_id=record["event"]["message_id"],
            text=stream.get("confirmed_text") or progress["text"], attempt=attempt,
            runner=self._reply_runner, finalize_reactions=False,
            source_authority_verifier=lambda profile, chat, message: self._source_authority(record, profile, chat, message))
        stream["blocker"] = result.get("blocker")
        verified = result.get("reply_verified") is True and result.get("ok") is True
        if verified:
            stream["closed"] = True
        _atomic_write_json(path, record)
        return verified

    def _continue_progress(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        """Retire a verified draft before creating the next source-bound one.

        Only a verified closing edit or a definite provider edit-limit rejection
        permits rollover. An uncertain close/create keeps its frozen intent on
        replay; continuation never submits or restarts the Core Turn.
        """
        progress_key = presentation_phase(record, "progress")
        progress = record["deliveries"].get(progress_key) or {}
        if not progress.get("verified"):
            return False
        stream = presentation_stream(record)
        if not stream.get("edit_limit_reached") and not self._retire_progress_parts(path, record, inbox=inbox):
            closed = self._edit_progress(path, record, "仍在处理，后续进展见下一条消息。",
                                         inbox=inbox, finalizing=True)
            if not closed and not stream.get("edit_limit_reached"):
                return False
        segment = int(stream.get("segment") or 0)
        record["deliveries"][f"{progress_key}.{segment}"] = {
            **progress, "text": stream.get("confirmed_text") or progress["text"],
            "attempt": stream.get("confirmed_attempt") or progress["attempt"],
            "edit_attempts": int(stream.get("edit_attempts") or 0),
            "edit_limit_reached": bool(stream.get("edit_limit_reached")),
        }
        record["deliveries"].pop(progress_key)
        for key in ("confirmed_text", "confirmed_attempt", "edit_attempts", "edit_limit_reached", "blocker", "closed"):
            stream.pop(key, None)
        stream["segment"] = segment + 1
        _atomic_write_json(path, record)
        return True

    def _progress(self, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path],
                  session_id: str | None = None, turn_id: str | None = None) -> None:
        stream = presentation_stream(record)
        events = self.core.controller.store.events_after(session_id or record["session_id"],
            turn_id or record["turn_id"], stream.get("cursor"))
        text = project_progress(stream, events, owner_private=(
            record.get("source", {}).get("private_human_message") is True
            and record.get("event", {}).get("chat_type") == "p2p"))
        if text:
            # Journal the exact presentation the sender freezes, not the raw
            # Core text. The same conversion also recovers older draft proofs.
            text = _presentation_text(text)
        view = text
        if text and stream.get("segment"):
            text = f"**后续进展 {int(stream['segment']) + 1}**\n\n" + text
        _atomic_write_json(path, record)
        if not text or text == stream.get("confirmed_text"):
            return
        now = time.time()
        answer_digest = hashlib.sha256(str(stream.get("answer") or "").encode("utf-8")).hexdigest()
        interval = (ACTIVITY_UPDATE_INTERVAL_SEC if answer_digest == stream.get("confirmed_answer_digest")
                    else UPDATE_INTERVAL_SEC)
        if now - float(stream.get("last_attempt_at") or 0) < interval:
            return
        stream["last_attempt_at"] = now
        _atomic_write_json(path, record)
        # A failed creation must resume its frozen intent rather than trying a
        # new body on an uncertain message. New chunks remain in the view.
        progress_key = presentation_phase(record, "progress")
        progress = record["deliveries"].get(progress_key) or {}
        if progress.get("verified") and (stream.get("edit_limit_reached")
                or int(stream.get("edit_attempts") or 0) >= PROGRESS_EDIT_BUDGET):
            if not self._continue_progress(path, record, inbox=inbox):
                return
            text = f"**后续进展 {int(stream['segment']) + 1}**\n\n{view}"
            progress = {}
        if not progress.get("verified"):
            initial = str(progress.get("text") or text)
            if self._deliver(path, record, "progress", initial, inbox=inbox):
                stream.update(confirmed_text=initial, confirmed_attempt=progress.get("attempt") or record["deliveries"][progress_key]["attempt"])
                _atomic_write_json(path, record)
        else:
            self._edit_progress(path, record, text, inbox=inbox)
        if stream.get("confirmed_text") == text:
            stream["confirmed_answer_digest"] = answer_digest
            _atomic_write_json(path, record)

    def _repair_recovery_presentation(self, path: Path) -> None:
        """Edit proven legacy drafts only; preserve execution and final receipts."""
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            repairs = legacy_recovery_repairs(record)
            if record["status"] != "delivered" or not repairs:
                return
            try:
                native = self.core.read_request(record["request_ref"])
                turn = self.core.controller.store.load_turn(record["session_id"], record["turn_id"])
                if (native.get("delivery_verified") is not True or not turn
                        or turn["status"] not in TERMINAL_TURN_STATES
                        or any(native.get(key) != record.get(key) for key in
                            ("request_ref", "binding_id", "source", "session_id", "turn_id", "context_recovery"))):
                    return
                selected = self.bindings.resolve(binding_id=record["binding_id"],
                    include_observation=True, **record["source"])
                if self._recovery_source_observation(record, selected) is None:
                    return
                config: Path | None = None

                def inbox() -> Path:
                    nonlocal config
                    if config is None:
                        config = self._inbox(record)
                    return config

                for repair in repairs:
                    state = record["deliveries"].setdefault(repair["phase"], {})
                    state.setdefault("edit_from", repair["edit_from"])
                    _atomic_write_json(path, record)
                    self._deliver(path, record, repair["phase"], repair["text"], inbox=inbox, edit_only=True)
            except (KeyError, ValueError, OSError):
                return  # Unknown authority or provider effects stay pending.

    def pending_body_paths(self) -> list[Path]:
        """Find unsent bodies without waiting for historical recovery probes."""
        paths = [path for path, row, _ in self._body_discovery.rows()
                 if row["status"] != "delivered" and triage.pending(row) and not _is_answer_image_followup(row)]
        return [*paths, *self._pending_web_projection_paths()]

    @effect_runtime_request_scope()
    def pending_delivery_paths(self) -> list[Path]:
        """The durable transport store is the queue; no in-memory admission."""
        paths = []
        recovery_candidate = self.core.context_recovery_observer()
        now = datetime.now(timezone.utc).timestamp()
        planned_repairs = {item.name for item in (self.root / "historical-answer-images").glob("*.json")}
        for path, row, repairs in self._delivery_discovery.rows():
            if row["status"] != "delivered":
                if triage.pending(row) and not _answer_image_retry_waits(row, now):
                    paths.append(path)
            elif row.get("historical_images_due_at") is not None and row["historical_images_due_at"] <= now:
                paths.append(path)
            elif (not row.get("historical_images_admitted") and path.name in planned_repairs
                    and time.monotonic() >= self._historical_image_probe_at.get(path, 0)):
                paths.append(path)
            elif (row.get("session_id") and row.get("turn_id")
                    and time.monotonic() >= self._context_recovery_probe_at.get(path, 0)):
                # Only a discovery/backoff hint, never execution authority.
                # The canonical request, provider effect inventory and source
                # are independently re-read by recover_failed_request.
                self._context_recovery_probe_at[path] = time.monotonic() + 60
                if repairs or recovery_candidate(row["request_ref"]):
                    paths.append(path)
        return [*paths, *self._web_projection_paths()]

    def is_recovery_path(self, path: Path) -> bool:
        """Local scheduling hint; reconciliation still rechecks every effect.

        A verified old notice may need a native recovery probe, but must not
        precede an unsent body just because both have zero worker attempts.
        """
        if path.parent == self.root / "web-projections":
            return False
        row = _read_json(path)
        return row.get("status") == "delivered" or _is_answer_image_followup(row)

    def _pending_web_projection_paths(self) -> list[Path]:
        paths = []
        for path in sorted((self.root / "web-projections").glob("*.json")):
            try:
                if _read_json(path).get("status") == "pending":
                    paths.append(path)
            except (OSError, ValueError):
                # An unreadable journal remains unresolved, but must not stop
                # discovery of other original conversations' replies.
                continue
        return paths

    @staticmethod
    def _read_web_projection_journal(path: Path) -> dict[str, Any]:
        row = _read_json(path)
        if (row.get("schema_version") != "lark_private_web_projection_v0"
                or row.get("projection_id") != path.stem
                or row.get("status") not in ("pending", "delivered", "partial")
                or any(not isinstance(row.get(key), str) or not row[key]
                       for key in ("request_ref", "binding_id", "profile", "session_id"))
                or any(not isinstance(row.get(key), dict) for key in ("source", "event", "deliveries"))
                or any(not isinstance(phase, dict) for phase in row["deliveries"].values())):
            raise ValueError("Original App mirror journal is unavailable")
        return row

    def _web_projection_paths(self) -> list[Path]:
        """Discover committed App messages, using the existing reply workers.

        These files are provider outboxes, not input requests or model work.
        The original Core transcript and grant are re-read before each effect.
        File observations only avoid rereading unchanged local Chat JSONL; they
        never authorize a send, acknowledge input or advance a delivery cursor.
        """
        root = self.root / "web-projections"
        if time.monotonic() >= self._web_projection_scan_at:
            self._web_projection_scan_at = time.monotonic() + 5
            enabled = {row["binding_id"] for row in self.bindings.read()["bindings"]
                       if row.get("enabled") is True and row.get("audience", "owner") == "owner"}
            sources: dict[str, Path] = {}
            for path, row, _ in self._delivery_discovery.rows():
                if (row.get("binding_id") in enabled and row.get("session_id")
                        and row.get("event", {}).get("chat_type") == "p2p"
                        and not row.get("agent_target") and row.get("status") in {"accepted", "delivered"}):
                    sources.setdefault(row["session_id"], path)
            store = self.core.controller.store
            for sid, source_path in sources.items():
                try:
                    snapshot = file_change_fingerprint(store.sessions_root / sid / "messages.jsonl")
                    if snapshot is not None and self._web_projection_snapshots.get(sid) == snapshot:
                        continue
                    source = _read_json(source_path)
                    if (source.get("binding_id") not in enabled or source.get("session_id") != sid
                            or source.get("event", {}).get("chat_type") != "p2p"
                            or source.get("agent_target") or source.get("status") not in {"accepted", "delivered"}):
                        continue
                    key = identity_ref(source["binding_id"], sid)
                    path = root / f"{key}.json"
                    selected = self._web_projection_transcript(sid)
                    if selected:
                        with exclusive_file_lock(path, operation="discover_web_conversation_projection"):
                            row = self._read_web_projection_journal(path) if path.exists() else {
                                "schema_version": "lark_private_web_projection_v0",
                                "projection_id": key, "request_ref": source["request_ref"],
                                "binding_id": source["binding_id"], "profile": source["profile"],
                                "source": source["source"], "event": source["event"],
                                "session_id": sid, "deliveries": {}, "status": "pending"}
                            if any(not row["deliveries"].get("web." + message["message_id"], {}).get("verified")
                                   for message in selected):
                                row["status"] = "pending"
                                _atomic_write_json(path, row)
                    if snapshot is not None:
                        self._web_projection_snapshots[sid] = snapshot
                except (OSError, ValueError, KeyError):
                    # An unreadable source is unknown. Keep any prior outbox;
                    # it retains fresh source and canonical-message checks.
                    continue
        return self._pending_web_projection_paths()

    def _web_projection_transcript(self, session_id: str, *, inputs_only: bool = False,
                                   verify_turns: bool = False) -> list[dict[str, Any]]:
        store = self.core.controller.store
        messages = store.messages(session_id)
        web_turns = {message["turn_id"] for message in messages
                     if message.get("role") == "user" and message.get("origin") == "web"}
        roles = {"user"} if inputs_only else {"user", "agent", "error"}
        selected = []
        failed_turns = set()
        for message in messages:
            turn_id, role = message.get("turn_id"), message.get("role")
            if turn_id not in web_turns or role not in roles:
                continue
            turn = store.load_turn(session_id, turn_id) if verify_turns or role == "error" else None
            same_turn = bool(turn and turn.get("origin") == "web" and turn.get("session_id") == session_id
                             and turn.get("turn_id") == turn_id)
            if role == "error":
                # An unconfirmed stop may leave an earlier error row in the
                # transcript. Use only the exact terminal failure, once per Turn.
                if (not same_turn or turn_id in failed_turns or turn["status"] not in {"failed", "timed_out"}
                        or not turn.get("completed_at") or turn.get("error") != message.get("text")
                        or message.get("attachments")):
                    continue
                failed_turns.add(turn_id)
                if turn.get("error_code") == "server_restarted":
                    failure = "服务在处理此请求时重启，本轮未完成。详情可在 App 的原会话查看。"
                else:
                    _, failure = manager_failure_reply(turn)
                message = {**message, "text": f"App 执行记录（{turn['completed_at']}）\n\n{failure}"}
            elif verify_turns and not same_turn:
                raise ValueError("canonical App input identity changed")
            selected.append(message)
        return selected

    def _web_projection_messages(self, row: dict[str, Any], *, inputs_only: bool = False) -> list[dict[str, Any]]:
        store = self.core.controller.store
        source = _read_json(self.root / f"{row['request_ref']}.json")
        native = self.core.read_request(row["request_ref"])
        if (any(row.get(key) != source.get(key) for key in
                ("request_ref", "binding_id", "profile", "source", "event", "session_id"))
                or any(native.get(key) != row.get(key) for key in ("binding_id", "source", "session_id"))
                or source.get("agent_target") or row["event"].get("chat_type") != "p2p"):
            raise ValueError("original App projection source changed")
        session = store.load_session(row["session_id"])
        if session is None:
            raise ValueError("original App projection Session unavailable")
        saved = session.get("steward_context") or session.get("project_context") or {}
        if (saved.get("binding_id") != row["binding_id"] or saved.get("audience") != "bound_owner"
                or saved.get("source_ref") != row["source"]["source_ref"]):
            raise ValueError("original App projection audience changed")
        with self.bindings.input_context(saved, origin="web", session=session, store=store):
            return self._web_projection_transcript(session["session_id"], inputs_only=inputs_only, verify_turns=True)

    @staticmethod
    def _web_projection_delivery_id(row: dict[str, Any], message: dict[str, Any]) -> str:
        """The existing provider identity binds this exact canonical message."""
        return hashlib.sha256(json.dumps([row["binding_id"], row["session_id"], message["message_id"]],
            separators=(",", ":")).encode()).hexdigest()

    def _web_projection_feedback(self, row: dict[str, Any], message: dict[str, Any]) -> None:
        if not self.reaction_feedback or message["role"] != "user":
            return
        phase = row["deliveries"].get("web." + message["message_id"], {})
        if not phase.get("verified"):
            return
        store = self.core.controller.store
        turn = store.load_turn(row["session_id"], message["turn_id"]) or {}
        status = turn.get("status")
        if not self._app_feedback_due(phase, status):
            return
        try:
            if message not in self._web_projection_messages(row, inputs_only=True):
                raise ValueError("canonical App input changed")
            expected_text = f"**你（App）**\n\n{message['text']}"
            if phase.get("text") not in (expected_text, _presentation_text(expected_text)):
                raise ValueError("App input no longer matches its frozen mirror")
            source = _read_json(self.root / f"{row['request_ref']}.json")
            if (source.get("context_recovery") or turn.get("context_recovery")
                    or status not in {"queued", "starting", "running", *TERMINAL_TURN_STATES}):
                raise ValueError("App feedback source/Turn unavailable")
            config = self._inbox(row)
            from .post_delivery import attachment_reply
            _, target = attachment_reply(project=self.runtime_root, config_path=config,
                attempt=phase["attempt"], text=phase["text"])

            def authorized() -> bool:
                try:
                    if message not in self._web_projection_messages(row, inputs_only=True):
                        return False
                    verified = verify_lark_inbox_reply(project=self.runtime_root, config_path=config,
                        message_id=row["event"]["message_id"], text=phase["text"], attempt=phase["attempt"],
                        reply_placement="chat_root", delivery_id=self._web_projection_delivery_id(row, message),
                        image_keys=tuple(phase.get("image_keys", [])), runner=self._reply_runner,
                        finalize_reactions=False, source_authority_verifier=lambda profile, chat, ref:
                            self._source_authority(row, profile, chat, ref))
                    # Provider IO can outlast a grant or a Turn. Re-read both
                    # before this exact mirror's reaction, including cleanup.
                    if (verified.get("ok") is not True or verified.get("reply_verified") is not True
                            or message not in self._web_projection_messages(row, inputs_only=True)):
                        return False
                    current_source = _read_json(self.root / f"{row['request_ref']}.json")
                    current = store.load_turn(row["session_id"], message["turn_id"]) or {}
                    return (current.get("status") == status
                        and current.get("origin") == "web" and not current.get("context_recovery")
                        and not current_source.get("context_recovery"))
                except (OSError, ValueError, KeyError):
                    return False

            result = _reconcile_outbound_input_reactions(project=self.runtime_root, config_path=config,
                message_id=target["message_ref"], processing=status in {"starting", "running"},
                complete=status in TERMINAL_TURN_STATES, before_write=authorized, runner=self._reply_runner)
            phase["feedback"] = {"ok": result.get("ok") is True, "turn_status": status,
                "message_ref": target["message_ref"], "result": result}
        except (OSError, ValueError, KeyError):
            phase["feedback"] = {"ok": False, "turn_status": status,
                "blocker": "original_web_projection_unavailable"}

    @staticmethod
    def _app_feedback_due(phase: dict[str, Any], status: str | None) -> bool:
        if "feedback" not in phase:
            return status not in TERMINAL_TURN_STATES  # Do not backfill finished mirrors.
        prior = phase["feedback"]
        return not (prior.get("ok") and prior.get("turn_status") == status)

    def _has_app_feedback_work(self, row: dict[str, Any]) -> bool:
        # Discovery uses only reference metadata and current Turn state. A
        # due item still follows the complete input/source/grant checks below.
        store = self.core.controller.store
        sid = row["session_id"]
        for message_id, turn_id in self._delivery_discovery.web_input_turn_refs(store, sid):
            phase = row["deliveries"].get("web." + message_id, {})
            if not phase.get("verified"):
                continue
            turn = store.load_turn(sid, turn_id)
            if turn is not None and self._app_feedback_due(phase, turn.get("status")):
                return True
        return False

    def _reconcile_web_projection(self, path: Path) -> int:
        # The existing worker pool serializes this outbox with a durable fence.
        # Recovery reads the same provider attempt; it never starts a Core Turn.
        with exclusive_file_lock(path, operation="deliver_web_conversation_projection"):
            try:
                row = self._read_web_projection_journal(path)
            except (OSError, ValueError):
                # Never overwrite the sole evidence of an unknown send with
                # a blocker or a new outbox when its journal cannot be read.
                return 0
            try:
                messages = self._web_projection_messages(row)
                pending = next((message for message in messages
                    if not row["deliveries"].get("web." + message["message_id"], {}).get("verified")), None)
                if pending is None:
                    # Older verified placeholders retain their exact attempts.
                    # They cannot be relabelled as media delivery or resent.
                    row["status"] = "partial" if any(message.get("attachments") and not
                        row["deliveries"].get("web." + message["message_id"], {}).get("images_verified")
                        for message in messages) else "delivered"
                    _atomic_write_json(path, row)
                    return 0
                def authorized() -> bool:
                    try:
                        return pending in self._web_projection_messages(row)
                    except (OSError, ValueError, KeyError):
                        return False
                who = "你" if pending["role"] == "user" else "LoopX"
                text = f"**{who}（App）**\n\n{pending['text']}"
                if self.reaction_feedback and pending["role"] == "user":
                    # Persist feedback intent before a mirror write/recovery;
                    # old completed mirrors without it are not backfilled.
                    row["deliveries"].setdefault("web." + pending["message_id"], {}).setdefault("feedback", {})
                    _atomic_write_json(path, row)
                delivered = self._deliver(path, row, "web." + pending["message_id"], text,
                    inbox=lambda: self._inbox(row), authorization_check=authorized,
                    reply_placement="chat_root", delivery_id=self._web_projection_delivery_id(row, pending), finalize_source=False,
                    image_attachments=pending.get("attachments"))
                if delivered:
                    row.pop("blocker", None)
                _atomic_write_json(path, row)
                return int(delivered)
            except (OSError, ValueError, KeyError):
                row["blocker"] = "original_web_projection_unavailable"
                _atomic_write_json(path, row)
                return 0

    def reconcile_app_feedback(self) -> int:
        """Reconcile optional reactions independently of the body outbox.

        The live service reserves at most one existing reply worker for this
        pass. A slow reaction must never own the body-delivery fence or prevent
        a later canonical answer from reaching the same audience.
        """
        if not self.reaction_feedback:
            return 0
        for path in sorted((self.root / "web-projections").glob("*.json")):
            with exclusive_file_lock(path.with_suffix(".feedback"), operation="reconcile_web_input_feedback"):
                try:
                    with exclusive_file_lock(path, operation="read_web_input_feedback"):
                        row = self._read_web_projection_journal(path)
                except (OSError, ValueError):
                    continue
                try:
                    due = self._has_app_feedback_work(row)
                except (OSError, ValueError, KeyError):
                    # An unavailable hint cannot suppress the original reader.
                    due = True
                if not due:
                    continue
                try:
                    messages = self._web_projection_messages(row, inputs_only=True)
                except (OSError, ValueError, KeyError):
                    continue
                for message in messages:
                    phase_key = "web." + message["message_id"]
                    phase = row["deliveries"].get(phase_key, {})
                    # Keep a frozen comparison without the independent
                    # feedback field; concurrent body deliveries stay intact.
                    frozen = {key: value for key, value in phase.items() if key != "feedback"}
                    prior_feedback = phase.get("feedback")
                    self._web_projection_feedback(row, message)
                    if "feedback" not in phase or phase["feedback"] == prior_feedback:
                        continue
                    with exclusive_file_lock(path, operation="record_web_input_feedback"):
                        try:
                            current = self._read_web_projection_journal(path)
                        except (OSError, ValueError):
                            continue
                        saved = current["deliveries"].get(phase_key, {})
                        if (all(current.get(key) == row.get(key) for key in
                                ("request_ref", "binding_id", "profile", "source", "event", "session_id"))
                                and {key: value for key, value in saved.items() if key != "feedback"} == frozen):
                            saved["feedback"] = phase["feedback"]
                            _atomic_write_json(path, current)
        return 0

    def observe_ingress(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[str]:
        """The original service observes gaps, then asks Core for one read-only answer."""
        reconnect = self.ingress.observe(cancelled=cancelled)
        for profile, source in self.ingress.answer_candidates(cancelled=cancelled):
            if cancelled():
                break
            try:
                self.admit(profile, source, history_review=True)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                continue  # Preserve the capture independently of live reconnect.
        return reconnect

    def reconcile(self) -> int:
        delivered = sum(self.reconcile_request(path) for path in self.pending_delivery_paths())
        self.reconcile_app_feedback()
        return delivered

    def _recovery_source_observation(self, record: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any] | None:
        source = self._source_message(record, selected=selected)
        native = self.core.read_request(record["request_ref"])
        expected = record.get("reply_context_source_text", native.get("message"))
        # Resume the admitted text, never an edited/replaced source. Keep the
        # original reply-context text when admission had added verified ancestry.
        # An unavailable rendering is unknown, not permission to guess a body.
        if source is None:
            return None
        message_type = record["event"].get("message_type")
        if source.get("msg_type", source.get("message_type")) != message_type:
            return None
        if message_type == "post":
            # Posts froze their provider rendering at intake. Text-only input
            # must also equal its admitted text; image input retains its
            # original model text and frozen attachment payload below.
            frozen = record.get("source_content")
            images = record.get("attachments")
            frozen_images = (isinstance(images, list) and bool(images)
                and images == native.get("attachments") and record.get("message") == native.get("message"))
            if (record.get("attachment_notice") or not isinstance(frozen, str)
                    or (not frozen_images and (images != [] or native.get("attachments")
                                              or frozen.strip() != expected))):
                return None
            # The original post's image keys/caption must still render exactly
            # as frozen at intake. Core separately verifies the saved image
            # bytes against the original pre-dispatch Turn; never redownload or
            # silently execute a text-only replacement of an image request.
            expected = frozen
        elif message_type != "text":
            return None
        if not isinstance(source.get("content"), str) or source["content"] != expected:
            return None
        return dict(source)

    def _received_during_recovery(self, path: Path) -> None:
        """Acknowledge durable ordinary admission independently of recovery.

        This is presentation only and holds only the transport fence. Core
        still performs every recovery check on the existing reply worker.
        """
        if not self.reaction_feedback:
            return
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            if (record["status"] != "accepted"
                    or record.get("feedback", {}).get("received", {}).get("ok")):
                return
            native = self.core.read_request(record["request_ref"])
            if (native["status"] != "accepted" or native.get("agent_target")
                    or native.get("command") or any(native.get(key) != record.get(key)
                        for key in ("request_ref", "binding_id", "source", "session_id", "turn_id"))):
                return
            store = self.core.controller.store
            session = store.load_session(record["session_id"])
            turn = store.load_turn(record["session_id"], record["turn_id"])
            saved = (session or {}).get("project_context") or (session or {}).get("steward_context")
            if (not session or not turn or not saved or session.get("status") == "closed"
                    or turn.get("session_id") != session["session_id"]
                    or turn.get("client_turn_id") != (record.get("context_recovery", {}).get("client_turn_id")
                        or f"external-{record['request_ref']}")
                    or identity_ref(saved["provider_ref"], record["event"]["message_id"]) != record["request_ref"]
                    or record["source"] != lark_private_source(
                        provider_ref=saved["provider_ref"], event=record["event"])):
                return
            selected = self.bindings.resolve(binding_id=record["binding_id"],
                session_context=saved, include_observation=True, **record["source"])
            if (record["profile"] != selected["binding"]["transport_ref"]
                    or session.get("goal_id") != ("loopx-manager" if selected["binding"]["context_kind"] == "steward" else None)
                    or session.get("channel_id") != selected["channel_id"]):
                return
            self._feedback(path, record, inbox=lambda: self._inbox(record, observation=selected["observation"]))

    def _reconcile_native_result(self, path: Path, native: dict[str, Any]) -> int:
        # Use the original reply worker and Inbox intent/readback owner. This
        # separate phase preserves the already delivered failure notice.
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            if any(record.get(key) != native.get(key) for key in
                   ("request_ref", "binding_id", "source", "session_id", "turn_id")):
                return 0
            text = self.core.native_result_reply(record["request_ref"])
            if text is None:
                return 0
            recovered = native["native_result_recovery"]
            def authorized() -> bool:
                return self.core.native_result_reply(record["request_ref"]) == text

            delivery_id = hashlib.sha256(recovered["message_id"].encode()).hexdigest()
            if not self._deliver(path, record, f"native_result.{record['turn_id']}", text,
                    inbox=lambda: self._inbox(record), authorization_check=authorized,
                    reply_placement="chat_root", delivery_id=delivery_id, finalize_source=False):
                return 0
            self.core.record_native_result_delivery(record["request_ref"], message_id=recovered["message_id"])
            return 1

    def reconcile_request(self, path: Path) -> int:
        if path.parent == self.root / "web-projections":
            return self._reconcile_web_projection(path)
        # Repair only this persisted source. Core still owns admission, recovery
        # and the shared Session queue/fence; transport workers never run models.
        row = _read_json(path)
        if "triage" in row:
            return triage.reconcile(self, path)
        historical_images = row.get("historical_answer_images")
        if (row.get("status") == "delivered" and (
                (historical_images or {}).get("status") == "pending"
                or (historical_images is None and (self.root / "historical-answer-images" / path.name).is_file()))):
            if historical_images is None:
                self._historical_image_probe_at[path] = time.monotonic() + 60
            return self._reconcile_historical_answer_images(path)
        if _answer_image_retry_waits(row, datetime.now(timezone.utc).timestamp()):
            return 0
        record_event_timing(row["profile"], row["event"]["message_id"], "reconciliation_started")
        if row["status"] == "delivered" and row.get("session_id") and row.get("turn_id"):
            # Discovery may find more candidates than the shared pool can run.
            # Refresh the probe delay on the actual attempt, which may follow
            # discovery after a full pool. Core still revalidates every effect.
            self._context_recovery_probe_at[path] = time.monotonic() + 60
        if row["status"] == "delivered" and row.get("context_recovery"):
            # Repair historical presentation first. A delivered failure of a
            # continuation still needs Core's fresh recovery judgment; only a
            # completed/ineligible request can stop at presentation repair.
            self._repair_recovery_presentation(path)
            if not self.core.context_recovery_candidate(row["request_ref"]):
                return 0
        if (row.get("session_id") and row.get("turn_id") and (row["status"] == "delivered"
                or row["status"] == "accepted" and self.core.context_recovery_candidate(row["request_ref"]))):
            # Legacy terminal delivery may be only a verified failure notice.
            # Core decides whether a migration continuation is safe; preserve
            # the old Turn and every frozen/ambiguous provider attempt.
            recovered = self.core.recover_failed_request(row["request_ref"],
                source_observation=lambda selected: self._recovery_source_observation(row, selected))
            if (row["status"] == "delivered" and recovered.get("native_result_recovery")
                    and recovered["native_result_recovery"]["turn_id"] == recovered.get("turn_id")):
                return self._reconcile_native_result(path, recovered)
            if (recovered.get("context_recovery") and not recovered.get("delivery_verified")
                    and recovered.get("turn_id") != row["turn_id"]):
                with exclusive_file_lock(path, operation="deliver_private_chat_request"):
                    row = _read_json(path)
                    if any(row.get(key) != recovered.get(key) for key in ("request_ref", "binding_id", "source", "session_id")):
                        raise ValueError("original recovery source changed")
                    row.update(status="accepted", turn_id=recovered["turn_id"],
                        context_recovery=recovered["context_recovery"])
                    _atomic_write_json(path, row)
        if row["status"] == "captured":
            # Intake may still own this capture. Wait on its existing fence and
            # read its result before retrying admission from a stale snapshot.
            with exclusive_file_lock(path, operation="capture_private_chat_request"):
                row = _read_json(path)
                native_path = self.core.root / f"{row['request_ref']}.json"
                if row["status"] == "captured" and native_path.exists():
                    native = self.core.read_request(row["request_ref"])
                    if (row.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING
                            and native.get("status") == "prepared"
                            and native.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING
                            and all(native.get(key) == row.get(key)
                                    for key in ("request_ref", "binding_id", "source", "session_id", "turn_id"))
                            and prepared_steward_binding_waits(native, self.bindings.read()["bindings"], self.core.controller.store)):
                        return 0
                    if (native.get("command") == "stop" and native.get("target_recorded") is True
                            and all(native.get(key) == row.get(key)
                                    for key in ("request_ref", "binding_id", "source"))):
                        # This source was already admitted and target-frozen.
                        # Core revalidates recovery; delivery below re-reads the
                        # provider source. No second content admission is needed.
                        record_event_timing(row["profile"], row["event"]["message_id"], "core_recovery_started")
                        self.core.recover(request_ref=row["request_ref"])
                        record_event_timing(row["profile"], row["event"]["message_id"], "core_recovery_returned")
                        native = self.core.read_request(row["request_ref"])
                        if native["status"] == "prepared":
                            return 0
                        if native["status"] == "command_completed":
                            row.update(**{key: native.get(key) for key in
                                ("status", "session_id", "turn_id", "response", "response_code")})
                            _atomic_write_json(path, row)
            if row["status"] == "captured":
                self.admit(row["profile"], row["event"])
                row = _read_json(path)
                if row["status"] == "captured" and row.get("response_code") == STEWARD_BINDING_ADOPTION_PENDING:
                    return 0
        native_path = self.core.root / f"{row['request_ref']}.json"
        if native_path.exists():
            if (self.reaction_feedback and not row.get("context_recovery") and row["status"] == "accepted"
                    and not row.get("feedback", {}).get("received", {}).get("ok")):
                # One bounded presentation job per existing reply worker. A
                # slow host cannot delay Get, nor can slow provider feedback
                # delay Core recovery. Join before processing/final delivery;
                # the existing Inbox journal still owns uncertain writes.
                with ThreadPoolExecutor(max_workers=1) as feedback:
                    received = feedback.submit(self._received_during_recovery, path)
                    record_event_timing(row["profile"], row["event"]["message_id"], "core_recovery_started")
                    self.core.recover(request_ref=row["request_ref"])
                    record_event_timing(row["profile"], row["event"]["message_id"], "core_recovery_returned")
                    try:
                        received.result()
                    except (KeyError, ValueError, OSError):
                        pass  # Presentation never suppresses durable recovery.
            else:
                record_event_timing(row["profile"], row["event"]["message_id"], "core_recovery_started")
                self.core.recover(request_ref=row["request_ref"])
                record_event_timing(row["profile"], row["event"]["message_id"], "core_recovery_returned")
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _read_json(path)
            if record["status"] in {"captured", "delivered"}:
                return 0
            config: Path | None = None
            selected: dict[str, Any] | None = None
            control_receipt_first = False
            completed_answer = False

            def inbox() -> Path:
                nonlocal config
                if config is None:
                    # A name supplies presentation metadata only; outbound
                    # provenance and Core recovery/admission keep their own
                    # fresh checks, including on the completed-stop path.
                    config = self._inbox(record, observation=selected["observation"] if selected else None,
                                         reuse_display_name=control_receipt_first and selected is None)
                return config

            try:
                if record["status"] in {"command_queued", "command_completed", "commission_running"}:
                    native = self.core.read_request(record["request_ref"])
                    if native["status"] == "command_queued":
                        selected = self.bindings.resolve(binding_id=record["binding_id"],
                                                         include_observation=True, **record["source"])
                        self._feedback(path, record, inbox=inbox)
                        self._deliver(path, record, "admission", native["response"], inbox=inbox)
                        return 0
                    record.update(status=native["status"], response=native.get("response"),
                                  commission_resources=native.get("commission_resources"),
                                  status_snapshot=native.get("status_snapshot"))
                    control_receipt_first = (native["status"] == "command_completed"
                                             and native.get("command") in CHAT_CONTROL_COMMANDS.values())
                # A completed group stop has no Core work left. Its existing
                # Inbox name is presentation only; sending still runs both
                # fresh binding/source observations in _source_verified. Do
                # not put an extra metadata probe ahead of that receipt.
                if not (control_receipt_first and native.get("command") == "stop"
                        and native.get("target_recorded") is True
                        and record["source"].get("group_human_message") is True
                        and not record["deliveries"].get("terminal", {}).get("verified")
                        and all(native.get(key) == record.get(key)
                                for key in ("request_ref", "binding_id", "source"))):
                    selected = self.bindings.resolve(binding_id=record["binding_id"],
                                                     include_observation=True, **record["source"])
                if record["status"] == "accepted":
                    native = self.core.read_request(record["request_ref"])
                    if native.get("agent_target"):
                        self.bindings.resolve_agent_target(self.bindings.resolve(binding_id=record["binding_id"], **record["source"]), native["agent_target"])
                    self._feedback(path, record, inbox=inbox)
                    # Receipt follows persistent Core admission and is
                    # independent of terminal execution and reply delivery.
                    turn = self.core.controller.store.load_turn(record["session_id"], record["turn_id"])
                    # Get already acknowledges admission. Reserve a text
                    # notification for a real wait or unavailable feedback;
                    # resume any old attempt with its original exact text.
                    admission = (record["deliveries"].get(presentation_phase(record, "admission")) or {}).get("text")
                    if not admission:
                        if native.get("agent_target"):
                            admission = "已收到，等待原 Agent 宿主处理。/status 查看状态，/project 返回项目对话。"
                        elif turn and turn["status"] == "queued":
                            # A first Turn can also await its native claim;
                            # queued alone does not prove a preceding request.
                            admission = "已收到，这条已排队。"
                        elif (record.get("feedback", {}).get("received") or {}).get("ok") is not True:
                            admission = "已收到，正在处理。"
                    if admission:
                        self._deliver(path, record, "admission", admission, inbox=inbox)
                    if turn and turn["status"] in {"starting", "running"}:
                        self._feedback(path, record, inbox=inbox, processing=True)
                        # Provider feedback may outlast the native answer. Use
                        # the current Turn before starting a provisional draft
                        # or deferring a final that is already available.
                        turn = self.core.controller.store.load_turn(record["session_id"], record["turn_id"])
                        if turn and turn["status"] in {"starting", "running"}:
                            self._progress(path, record, inbox=inbox)
                    if not turn or turn["status"] not in TERMINAL_TURN_STATES:
                        return 0
                    if turn["status"] == "failed":
                        events = self.core.controller.store.events_after(record["session_id"], record["turn_id"], None
                            ) if turn.get("error_code") in {"host_gate", "response_timeout"} else []
                        preparation_recovery = (record["source"].get("private_human_message") is True
                            and selected["binding"].get("context_kind") in {"project", "steward"} and not native.get("agent_target"))
                        response = manager_failure_reply(turn, phase_events=events,
                            preparation_recovery=preparation_recovery)[1] + " 原会话已保留。"
                    else:
                        response = str((turn.get("response") or {}).get("message") or "") if turn["status"] == "completed" else (
                            "本次执行已停止。" if turn["status"] == "interrupted" else
                            "本次执行超时，原会话已保留；请发送 /status 查看状态后再决定是否重试。" if turn["status"] == "timed_out" else
                            "本次未能完成，原会话已保留。")
                        if turn["status"] == "completed":
                            completed_answer = True
                            response = self.core.todo_confirmation_reply(record["request_ref"], turn.get("response") or {})
                else:
                    if record["status"] != "rejected" and not control_receipt_first:
                        self._feedback(path, record, inbox=inbox)
                    response = (str(record.get("attachment_notice") or "")
                        or _command_text(str(record.get("response_code") or "")) or str(record.get("response") or ""))
                    if record.get("status_snapshot"):
                        response = _status_text(record["status_snapshot"], help_requested=native.get("command") == "help")
                if (self._deliver_completed_answer(path, record, response, inbox=inbox) if completed_answer else
                        self._deliver(path, record, "terminal", response, inbox=inbox)):
                    if control_receipt_first:
                        # The verified result is the useful control receipt.
                        # Preserve the usual idempotent reaction, after it.
                        self._feedback(path, record, inbox=inbox)
                    resources = record.get("commission_resources") or {}
                    if resources.get("session_id") and resources.get("turn_id"):
                        first_turn = self.core.controller.store.load_turn(resources["session_id"], resources["turn_id"])
                        if first_turn and first_turn["status"] in {"starting", "running"}:
                            self._feedback(path, record, inbox=inbox, processing=True)
                            self._progress(path, record, inbox=inbox,
                                session_id=resources["session_id"], turn_id=resources["turn_id"])
                        if not first_turn or first_turn["status"] not in TERMINAL_TURN_STATES:
                            record["status"] = "commission_running"
                            _atomic_write_json(path, record)
                            return 0
                        result_text = str((first_turn.get("response") or {}).get("message") or "")
                        result_text = ("委托执行结果：\n" + result_text if first_turn["status"] == "completed" else
                            "委托首轮执行超时；原 Goal 和回执已保留，请查看状态后决定恢复。" if first_turn["status"] == "timed_out" else
                            "委托首轮执行未完成；原 Goal 和回执已保留，请查看状态后决定恢复。")
                        proposal_id = native.get("proposal_id")
                        if proposal_id:
                            result_text += f"\n如需恢复暂停或额度受限的原执行：/resume-commission {proposal_id} --tokens N（N 为包含历史用量的总上限，须大于已用量；不会重开线程）。"
                        if not self._deliver(path, record, "commission_result", result_text, inbox=inbox):
                            return 0
                    config = inbox()
                    acknowledge_lark_event_inbox(project=self.runtime_root, config_path=config,
                        message_ids=[record["event"]["message_id"]], execute=True)
                    if native_path.exists():
                        self.core.record_delivery(record["request_ref"], session_id=record.get("session_id"),
                            turn_id=record.get("turn_id"), message_text=response)
                    elif record["status"] != "rejected":
                        raise ValueError("delivery request journal unavailable")
                    record["status"] = "delivered"
                    _atomic_write_json(path, record)
                    return 1
                if (control_receipt_first and selected is None
                        and record["deliveries"].get("terminal", {}).get("blocker")
                        == "lark_inbox_reply_sender_identity_mismatch"):
                    # A renamed Bot can invalidate the presentation hint. No
                    # send occurred; refresh only metadata for the next normal
                    # reconcile, preserving any frozen provider attempt.
                    self._inbox(record)
            except (KeyError, ValueError, OSError):
                # Keep pending receipts available for actionable recovery.
                return 0
        return 0


def _command_text(code: str) -> str:
    return {"group_recipient_unavailable": "群助手仅处理当前项目和话题，不能选择个人 Agent 会话；/status 查看状态，/help 查看用法。",
            "unsupported_attachment": "这条消息未提交执行。普通项目或管家对话支持文字与图片；文件、音视频及所选 Agent 的原宿主暂不支持图片。请将文字与图片单独发送，或用 /project 返回项目对话。",
        "no_session": "尚无会话；发送文字即可开始。", "active_session": "正在执行；后续文字会进入同一会话队列。",
        "ready_session": "会话已就绪，可继续发送文字。", "new_session": "已关闭此前会话；下一条文字将开启新会话。",
        "attached_control_unavailable": "原 Agent 宿主尚不支持此处的实时停止或新建会话；原执行没有被停止或替换。请在原宿主处理，/project 返回普通项目对话。",
        "stop_requested": "已请求停止这条消息对应的执行。", "no_active_turn": "当前没有正在执行的消息。"}.get(code, "")


def _status_text(snapshot: dict[str, Any], *, help_requested: bool) -> str:
    """Render Core facts with provider-specific commands and help."""
    steward = snapshot["context_kind"] == "steward"
    attached = bool(snapshot.get("recipient_agent_id"))
    text = render_conversation_status(snapshot)
    if snapshot.get("audience") == "group":
        text += "\n\n/status 当前话题状态 · /stop 停止本话题执行 · /new 本话题新会话 · /help 用法"
        if help_requested:
            text += "\n@此 Bot 开始新话题，后续消息在原话题继续；文字与图片沿用同一会话。仅授权当前项目工作区，不继承个人管家或 Agent 会话。"
        return text
    text += ("\n\n/agents 授权 Agent · /project 返回项目 · /help 用法" if attached else
             "\n\n/stop 停止当前聊天 · /new 新会话 · /help 用法")
    if help_requested:
        if not steward and not attached and snapshot.get("grant") == "workspace_write":
            text += "\n按项目规则和 skills 执行当前指令；读写授权不会创建 Goal 或提高原宿主权限。"
        text += ("\n\n/status 查看当前状态；/help 查看用法。"
                 "\n/agents 查看本 App 已授权的 Agent；使用列表中的完整 /agent 命令选择，/project 返回项目对话。")
        if not attached:
            text += "\n/stop 停止当前聊天执行；/new 关闭当前聊天，下条消息开启新会话。"
        text += (f"\n\n工作区：{markdown_scalar(snapshot['workspace_path'])}"
                 f"\n执行器：{markdown_scalar(snapshot['executor_endpoint_id'])}"
                 f"\n观察时间：{markdown_scalar(snapshot['observed_at'])}"
                 "\n工作区、执行器与解绑：本机 Chat → 设置 → Lark。变更或解绑会重新核验授权；已受理工作不会迁移到新会话。"
                 "\n可直接发送图片或图文消息（PNG/JPEG/GIF/WebP，最多 4 张，单张 5 MB、合计 12 MB）。文件与音视频暂不支持；选择原宿主 Agent 后仅支持文字。")
        if steward:
            text += "\n新委托：/delegate --tokens N 具体目标；读完预览后从原私聊发送完整 /confirm。/cancel 取消预览；/stop-commission 和 /resume-commission 使用原回执中的完整命令。"
    return text
