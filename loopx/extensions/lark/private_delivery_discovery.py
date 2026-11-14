"""Read-only source and scheduling checks for private-message delivery."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ...chat_store import RESUMABLE_SESSION_STATES, ChatSessionStore, _read_json
from ...capabilities.native_chat.file_discovery import FileChatDiscovery, external_source_route_path, file_change_fingerprint
from ...file_lock import try_exclusive_file_lock
from .presentation.markdown_post import lark_markdown_readback_matches
from .private_ingress import private_source_text
from .private_progress import legacy_recovery_repairs

if TYPE_CHECKING:
    from ...capabilities.native_chat.external_conversations import ChatExternalConversations


_SOURCE_URL = re.compile(r"https://[^\s<>()\[\]\"，。；！？]+")


def allowed_answer_image_urls(source: str, targets: list[str], *, deadline: float) -> set[str]:
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


def answer_image_source_matches(message: Mapping | None, event: Mapping) -> bool:
    if message is None or message.get("deleted") is True or message.get("chat_id") != event["chat_id"]:
        return False
    sender = message.get("sender")
    return (isinstance(sender, Mapping) and sender.get("sender_type") == "user"
            and bool(event["sender_id"]) and sender.get("id") == event["sender_id"]
            and message.get("msg_type", message.get("message_type")) == "text"
            and private_source_text(message) == event["content"])


def historical_image_reply_matches(message: Mapping | None, chat_id: str, observation: Mapping,
                                   text: str, keys: tuple[str, ...] = ()) -> bool:
    if message is None or message.get("deleted") is True or message.get("chat_id") != chat_id:
        return False
    sender = message.get("sender")
    if (not isinstance(sender, Mapping) or sender.get("sender_type") != "app"
            or not sender.get("id") or sender["id"] not in (observation["bot_app_id"], observation["bot_open_id"])):
        return False
    raw_body = message.get("body")
    if isinstance(raw_body, Mapping) and isinstance(raw_body.get("content"), str):
        try:
            body = json.loads(raw_body["content"])
        except ValueError:
            return False
        # Raw mget keeps authored Markdown in content_v2 and a rendered preview
        # in content. Match the authored body through the existing wire verifier.
        if isinstance(body, Mapping) and "content_v2" in body:
            locale = {"title": body.get("title", ""), "content": body["content_v2"]}
            message = {**message, "body": {"content": {"zh_cn": locale}}}
    return lark_markdown_readback_matches(text=text, message=message, image_keys=keys)


def resumable_answer_image_session(
    core: ChatExternalConversations, request: dict[str, Any], turn: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Apply source controls before reading the current Session."""
    store = core.controller.store
    session_id, turn_id = request["session_id"], request["turn_id"]
    if (turn is None or turn.get("status") != "completed"
            or core._source_stop_observed(request, turn, store.events_after(session_id, turn_id, None))):
        return None
    session = store.load_session(session_id)
    return session if session is not None and session.get("status") in RESUMABLE_SESSION_STATES else None


@contextmanager
def answer_media_dispatch(core: ChatExternalConversations, request: dict[str, Any],
                          source_is_current: Callable[[], bool]) -> Iterator[None]:
    """Check the provider source at dispatch under the original local controls."""
    store = core.controller.store
    session_id, turn_id = request["session_id"], request["turn_id"]
    route = external_source_route_path(core.root, request["binding_id"], request["source"]["source_ref"])
    # Ingress takes the route before its binding and Session operations.
    # Contention leaves the original control writer free to finish first.
    with ExitStack() as fences:
        for path in (route, core.bindings.path, store._session_path(session_id)):
            if fences.enter_context(try_exclusive_file_lock(path, operation="dispatch_external_answer_media")) is None:
                raise ValueError("original media authority is being updated")
        if not any(binding.get("enabled") is True and binding["binding_id"] == request["binding_id"]
                   and binding["transport_ref"] == request["profile"] for binding in core.bindings.read()["bindings"]):
            raise ValueError("original media binding unavailable")
        native = core.read_request(request["request_ref"])
        if (native.get("status") != "accepted"
                or any(native.get(key) != request[key] for key in ("binding_id", "source", "session_id", "turn_id"))
                or resumable_answer_image_session(core, native, store.load_turn(session_id, turn_id)) is None):
            raise ValueError("original Session or source no longer authorizes an upload")
        # Feishu has no conditional image upload tied to a message revision.
        # Do the final provider read here, with these local locks held through
        # CLI start. Edits made later are checked again before posting the images.
        if not source_is_current():
            raise ValueError("original upload source changed")
        if resumable_answer_image_session(core, native, store.load_turn(session_id, turn_id)) is None:
            raise ValueError("original Session or source changed during upload authorization")
        yield


def prepared_steward_binding_waits(
    request: dict[str, Any], bindings: list[dict[str, Any]], store: ChatSessionStore
) -> bool:
    """Skip provider reads while a recorded executor mismatch persists."""
    session = store.load_session(request["session_id"])
    binding = next((row for row in bindings if row["binding_id"] == request["binding_id"]), None)
    if not session or not binding or not binding.get("enabled"):
        return False
    allocation = session.get("manager_executor_allocation")
    return (session.get("status") in RESUMABLE_SESSION_STATES
            and session.get("executor_endpoint_id") != binding.get("executor_endpoint_id")
            and isinstance(allocation, dict) and allocation.get("selection_policy") in ("preferred", "pinned"))


class PrivateDeliveryDiscovery:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._completed: dict[Path, tuple[tuple[int, ...], dict[str, Any], bool]] = {}
        self._web_inputs = FileChatDiscovery()

    def web_input_turn_refs(self, store: ChatSessionStore, session_id: str) -> list[tuple[str, str]]:
        """Observe App user message/Turn references, never bodies or effect authority."""
        path = store._session_dir(session_id) / "messages.jsonl"

        def read(_: Path) -> dict[str, Any]:
            refs = []
            for message in store.messages(session_id, strict=True):
                if message.get("role") == "user" and message.get("origin") == "web":
                    message_id, turn_id = message["message_id"], message["turn_id"]
                    if not isinstance(message_id, str) or not isinstance(turn_id, str):
                        raise ValueError("invalid App input reference")
                    refs.append((message_id, turn_id))
            return {"refs": refs}

        [(_, view)] = self._web_inputs.rows(path.parent, (path,), read=read, project=lambda _, value: value)
        return cast(list[tuple[str, str]], view["refs"])

    def rows(self) -> Iterator[tuple[Path, dict[str, Any], bool]]:
        """Reuse only unchanged delivered-file hints, never bodies or authority.

        Pending files stay fresh. Reconciliation still reads the complete
        journal and current Core/provider state before any delivery or recovery.
        """
        seen = set()

        for path in sorted(self.root.glob("*.json")):
            seen.add(path)
            try:
                row, repairs = self._observe(path)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                # Keep unreadable or disappearing journals intact and evict
                # their stale hints. The next scan can observe a readable
                # receipt; unrelated replies stay discoverable now.
                self._completed.pop(path, None)
                continue
            yield path, row, repairs
        for path in self._completed.keys() - seen:
            del self._completed[path]

    def _observe(self, path: Path) -> tuple[dict[str, Any], bool]:
        before = file_change_fingerprint(path)
        saved = self._completed.get(path)
        if before is not None and saved is not None and saved[0] == before:
            return saved[1], saved[2]

        self._completed.pop(path, None)
        row = _read_json(path)
        if row["status"] != "delivered":
            return row, False

        repairs = bool(row.get("session_id") and row.get("turn_id") and legacy_recovery_repairs(row))
        view = {key: row.get(key) for key in
                ("status", "request_ref", "binding_id", "session_id", "turn_id")}
        view.update(agent_target=bool(row.get("agent_target")),
                    event={"chat_type": row.get("event", {}).get("chat_type")})
        historic_images = row.get("historical_answer_images") or {}
        if historic_images:
            view["historical_images_admitted"] = True
        if historic_images.get("status") == "pending":
            view["historical_images_due_at"] = historic_images.get("next_check_at", 0)
        if before is not None and file_change_fingerprint(path) == before:
            self._completed[path] = (before, view, repairs)
        return view, repairs
