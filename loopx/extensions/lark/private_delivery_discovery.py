"""Read-only source and scheduling checks for private-message delivery."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import zlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ...chat.attachments import CHAT_IMAGE_MAX_COUNT
from ...chat_store import RESUMABLE_SESSION_STATES, ChatSessionStore, _read_json
from ...control_plane.content_digest import BARE_SHA256_PATTERN
from ...capabilities.native_chat import file_discovery as _file_discovery
from ...capabilities.native_chat.file_discovery import (
    REPLY_IMAGE_NAME_PATTERN,
    FileChatDiscovery,
    external_source_route_path,
    file_change_fingerprint,
    optional_file_fingerprint,
    reply_image_workspace,
)
from ...file_lock import try_exclusive_file_lock
from .identity_shapes import LARK_MESSAGE_ID_PATTERN
from .outbound import present_lark_conversation_text as _presentation_text
from .presentation.markdown_post import lark_markdown_readback_matches
from .private_ingress import private_source_text
from .private_progress import legacy_recovery_repairs, presentation_phase
from .private_web_projection import _same_fields

if TYPE_CHECKING:
    from ...capabilities.native_chat.external_conversations import ChatExternalConversations


_SOURCE_URL = re.compile(r"https://[^\s<>()\[\]\"，。；！？]+")
_DEFAULT_FILE_CHANGE_FINGERPRINT = file_change_fingerprint
_DEFAULT_FD_FILE_CHANGE_FP = _file_discovery.file_change_fingerprint
DEFAULT_OPTIONAL_FILE_FINGERPRINT = optional_file_fingerprint
_UNSET = object()
_SESSION_BACKOFF_VOLATILE_FIELDS = frozenset({"updated_at", "last_activity_at"})
SESSION_BACKOFF_TOKEN_CACHE_LIMIT = 1024
ANSWER_IMAGE_RETRY_MAX_SECONDS = 3600


def _entry_change_fingerprint(entry: os.DirEntry[str], path: Path) -> tuple[int, ...] | None:
    if (file_change_fingerprint is _DEFAULT_FILE_CHANGE_FINGERPRINT
            and _file_discovery.file_change_fingerprint is _DEFAULT_FILE_CHANGE_FINGERPRINT):
        if sys.platform == "win32":
            return None
        stat = entry.stat(follow_symlinks=True)
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
    return file_change_fingerprint(path)


def session_recovery_backoff_token(
    session_path: Path,
    cache: dict[Path, tuple[tuple[int, ...], tuple[str, bool, str]]],
) -> tuple[str, bool, str] | None:
    """Return a volatile-timestamp-free session state token for recovery backoff."""
    raw_fp = _file_discovery.optional_file_fingerprint(session_path)
    if raw_fp is None:
        return None
    cached = cache.get(session_path)
    if cached is not None and cached[0] == raw_fp:
        return cached[1]
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        token: tuple[str, bool, str] = ("", False, "missing")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    else:
        if not isinstance(data, dict):
            return None
        status = str(data.get("status") or "")
        is_busy = status == "busy" or bool(data.get("active_turn_id"))
        stable = {k: v for k, v in data.items() if k not in _SESSION_BACKOFF_VOLATILE_FIELDS and k not in {"status", "active_turn_id"}}
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        token = (digest, is_busy, "" if is_busy else status)
    if len(cache) >= SESSION_BACKOFF_TOKEN_CACHE_LIMIT and session_path not in cache:
        del cache[next(iter(cache))]
    cache[session_path] = (raw_fp, token)
    return token


def context_recovery_backoff_valid(saved: tuple[object, ...], current: tuple[object, ...] | None) -> bool:
    """Keep ready backoff across ready->busy pulses; invalidate on busy->ready or semantic changes."""
    if current is None:
        return False
    if current == saved:
        return True
    if len(saved) != 5 or len(current) != 5 or current[:-1] != saved[:-1]:
        return False
    saved_tok, current_tok = saved[-1], current[-1]
    return (isinstance(saved_tok, tuple) and isinstance(current_tok, tuple)
            and len(saved_tok) >= 3 and len(current_tok) >= 2
            and saved_tok[0] == current_tok[0] and saved_tok[1] is False and saved_tok[2] == "ready"
            and current_tok[1] is True)


def answer_image_retry_delay(retry_count: int, *, base_seconds: int = 300,
                             max_seconds: int = ANSWER_IMAGE_RETRY_MAX_SECONDS) -> int:
    """Exponential delay capped at max_seconds for repeated answer image retries."""
    exponent = min(max(retry_count - 1, 0), 4)
    return min(max_seconds, base_seconds * (1 << exponent))


def remote_answer_image_urls_plausible(source: str, targets: list[object]) -> bool:
    """Return True when source and targets satisfy the remote URL pre-check."""
    return bool(targets) and bool(_SOURCE_URL.search(source)) and all(
        isinstance(target, str) and target.startswith("https://") for target in targets
    )


def context_recovery_fingerprints(
    core: object,
    path: Path,
    request_ref: str,
    session_id: str,
    turn_id: str,
    token_cache: dict[Path, tuple[tuple[int, ...], tuple[str, bool, str]]],
    *,
    path_fp: tuple[int, ...] | None = None,
    session_tokens: dict[Path, tuple[str, bool, str] | None] | None = None,
) -> tuple[object, ...] | None:
    """Compute stable multi-file fingerprint tuple for context-recovery no-op backoff."""
    core_root = getattr(core, "root", None)
    store = getattr(getattr(core, "controller", None), "store", None)
    if not isinstance(core_root, Path) or store is None:
        return None
    try:
        use_scanned_fp = (
            path_fp is not None
            and file_change_fingerprint is _DEFAULT_FILE_CHANGE_FINGERPRINT
            and _file_discovery.file_change_fingerprint is _DEFAULT_FILE_CHANGE_FINGERPRINT
            and _file_discovery.optional_file_fingerprint is DEFAULT_OPTIONAL_FILE_FINGERPRINT
        )
        read_fp = _file_discovery.optional_file_fingerprint
        tokens = (
            path_fp if use_scanned_fp else read_fp(path),
            read_fp(core_root / f"{request_ref}.json"),
            read_fp(store._turn_path(session_id, turn_id)),
            read_fp(store._event_path(session_id, turn_id)),
        )
        session_path = store._session_path(session_id)
        if session_tokens is not None and session_path in session_tokens:
            session_token = session_tokens[session_path]
        else:
            session_token = session_recovery_backoff_token(session_path, token_cache)
            if session_tokens is not None:
                session_tokens[session_path] = session_token
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return None if session_token is None or any(t is None for t in tokens) else (*tokens, session_token)


def record_context_recovery_noop_backoff(
    cache: dict[Path, tuple[tuple[object, ...], int]],
    probe_at: dict[Path, float],
    path: Path,
    current: tuple[object, ...] | None,
    *,
    now_monotonic: float,
    stagger_first: bool = False,
) -> None:
    """Record or advance exponential probe backoff for an unchanged non-recoverable row."""
    stagger = zlib.crc32(path.name.encode("utf-8")) % 60
    if current is None:
        cache.pop(path, None)
        probe_at[path] = now_monotonic + (60.0 + stagger if stagger_first else 60.0)
        return
    prev = cache.get(path)
    if prev is not None and context_recovery_backoff_valid(prev[0], current):
        saved_fp, prev_streak = prev
        streak = prev_streak + 1
        cache[path] = (saved_fp, streak)
    else:
        streak = 1
        cache[path] = (current, streak)
    delay = float(60 + stagger if stagger_first else 60) if streak <= 1 else float(min(1800, 60 * (1 << min(streak - 1, 5))) + stagger)
    probe_at[path] = now_monotonic + delay


def load_historical_image_plan(root: Path, core: ChatExternalConversations,
                               path: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    """Load and validate an operator-staged historical image repair descriptor."""
    if record.get("historical_answer_images") is not None:
        return cast(dict[str, Any], record["historical_answer_images"])
    if not re.fullmatch(r"[a-f0-9]{24}", path.stem):
        raise ValueError("invalid original request reference")
    # This is a private local operator input, scoped to the existing receipt.
    # It is never loaded from a project, provider message or model response.
    try:
        with (root / "historical-answer-images" / path.name).open("rb") as handle:
            raw = handle.read(40_001)
    except FileNotFoundError:
        return None
    if len(raw) > 40_000:
        raise ValueError("original image repair descriptor exceeds its size limit")
    plan = json.loads(raw)
    if not isinstance(plan, dict) or path.stem != record.get("request_ref"):
        raise ValueError("invalid original image repair descriptor")
    validated = historical_image_plan(core, record, plan)
    state: dict[str, Any] = {"plan": validated, "status": "pending"}
    record["historical_answer_images"] = state
    return state


_MARKDOWN_CODE_SPAN = re.compile(r"((?<!\x60)(\x60+)(?!\x60).*?(?<!\x60)\2(?!\x60))")
_ANSWER_IMAGE = re.compile(r"""(?<!\\)!\[([^\]\n]*)\]\(\s*(?:<([^<>\n]+)>|([^\s<>()]+))(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'))?\s*\)""")


def private_answer_images(text: str) -> tuple[str, list[str]]:
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

    def rows_with_fingerprints(self) -> Iterator[tuple[Path, dict[str, Any], bool, tuple[int, ...] | None]]:
        """Yield delivery views with observed fingerprints using a single os.scandir pass."""
        seen: set[Path] = set()
        try:
            with os.scandir(self.root) as it:
                entries = sorted((entry for entry in it if entry.name.endswith(".json")), key=lambda entry: entry.name)
        except OSError:
            entries = []
        for entry in entries:
            path = Path(entry.path)
            seen.add(path)
            try:
                before = _entry_change_fingerprint(entry, path)
                row, repairs = self._observe(path, before=before)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                # Keep unreadable or disappearing journals intact and evict
                # their stale hints. The next scan can observe a readable
                # receipt; unrelated replies stay discoverable now.
                self._completed.pop(path, None)
                continue
            yield path, row, repairs, before
        for path in self._completed.keys() - seen:
            del self._completed[path]

    def rows(self) -> Iterator[tuple[Path, dict[str, Any], bool]]:
        """Reuse only unchanged delivered-file hints, never bodies or authority.

        Pending files stay fresh. Reconciliation still reads the complete
        journal and current Core/provider state before any delivery or recovery.
        """
        for path, row, repairs, _before in self.rows_with_fingerprints():
            yield path, row, repairs

    def _observe(self, path: Path, *, before: object = _UNSET) -> tuple[dict[str, Any], bool]:
        fp = file_change_fingerprint(path) if before is _UNSET else cast(tuple[int, ...] | None, before)
        saved = self._completed.get(path)
        if fp is not None and saved is not None and saved[0] == fp:
            return saved[1], saved[2]

        self._completed.pop(path, None)
        row = _read_json(path)
        if row["status"] != "delivered":
            triage = row.get("triage")
            if isinstance(triage, dict) and triage.get("retired"):
                view = {"status": row["status"], "triage": {"retired": triage["retired"]}}
                if fp is not None and file_change_fingerprint(path) == fp:
                    self._completed[path] = (fp, view, False)
                return view, False
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
        if fp is not None and file_change_fingerprint(path) == fp:
            self._completed[path] = (fp, view, repairs)
        return view, repairs


def failure_notice_receipt_unknown(record: Mapping[str, Any]) -> bool:
    """Return True when the current terminal failure notice lost its provider receipt."""
    if record.get("status") != "accepted" or "triage" in record:
        return False
    phase = presentation_phase(record, "terminal")
    terminal = (record.get("deliveries") or {}).get(phase) or {}
    return (
        terminal.get("started") is True
        and not terminal.get("attempt")
        and terminal.get("verified") is not True
        and terminal.get("blocker") == "provider_delivery_receipt_unknown"
    )


def is_answer_image_followup(record: Mapping[str, Any]) -> bool:
    if record.get("status") != "accepted" or not isinstance(record.get("answer_images"), dict):
        return False
    terminal = record.get("deliveries", {}).get(presentation_phase(record, "terminal"), {})
    return terminal.get("verified") is True


def answer_image_retry_waits(record: Mapping[str, Any], now: float) -> bool:
    if not is_answer_image_followup(record):
        return False
    state = record["answer_images"]
    retry_at = state.get("next_check_at")
    return state.get("status") != "delivered" and type(retry_at) in (int, float) and retry_at > now


def historical_image_source(core: ChatExternalConversations, record: Mapping[str, Any], plan: Mapping[str, Any]) -> dict:
    event = record["event"]
    terminal = record["deliveries"]["terminal"]
    if (record["status"] != "delivered" or record.get("context_recovery")
            or event.get("chat_type") != "p2p" or event.get("message_type") != "text"
            or record["source"].get("private_human_message") is not True
            or record["binding_id"] != plan["binding_id"] or record["turn_id"] != plan["turn_id"]
            or terminal.get("verified") is not True or terminal.get("image_keys")
            or terminal.get("attempt", {}).get("message_ref") != plan["message_id"]):
        raise ValueError("original text-only reply unavailable")
    native = core.read_request(record["request_ref"])
    if (native.get("status") != "accepted" or native.get("origin") != "lark"
            or native.get("command") is not None or native.get("agent_target")
            or native.get("delivery_verified") is not True
            or not _same_fields(native, record, "request_ref", "binding_id", "source", "session_id", "turn_id")):
        raise ValueError("original request no longer matches")
    turn = core.controller.store.load_turn(record["session_id"], record["turn_id"]) or {}
    response = turn.get("response") or {}
    if (not isinstance(answer := response.get("message"), str)
            or hashlib.sha256(answer.encode()).hexdigest() != plan["answer_sha256"]
            or terminal.get("text") != _presentation_text(answer)
            or (session := resumable_answer_image_session(core, native, turn)) is None):
        raise ValueError("original completed answer or its authority changed")
    image_targets = private_answer_images(answer)[1]
    if "captured_images" in plan:
        if image_targets or response.get("answer_image_plan") or reply_image_workspace(session, turn) is None:
            raise ValueError("original reply is not a qualified legacy project answer")
    elif not image_targets:
        raise ValueError("original remote image reply unavailable")
    return session


def historical_image_plan(core: ChatExternalConversations, record: Mapping[str, Any], plan: Mapping[str, Any]) -> dict:
    from ..public_source_reader import public_url

    captured = "captured_images" in plan
    image_field, reference_field = ("captured_images", "name") if captured else ("images", "url")
    if set(plan) != {"binding_id", "turn_id", "message_id", "answer_sha256", image_field}:
        raise ValueError("invalid original image repair fields")
    if (not isinstance(plan["message_id"], str) or not LARK_MESSAGE_ID_PATTERN.fullmatch(plan["message_id"])
            or not isinstance(plan["answer_sha256"], str)
            or not BARE_SHA256_PATTERN.fullmatch(plan["answer_sha256"])
            or not isinstance(plan[image_field], list) or not 1 <= len(plan[image_field]) <= CHAT_IMAGE_MAX_COUNT):
        raise ValueError("invalid original image repair target")
    images = []
    for image in plan[image_field]:
        if (not isinstance(image, dict) or set(image) != {reference_field, "sha256"}
                or not isinstance(reference := image[reference_field], str)
                or not isinstance(image["sha256"], str) or not BARE_SHA256_PATTERN.fullmatch(image["sha256"])):
            raise ValueError("each original image requires a reference and its SHA-256")
        if captured and not REPLY_IMAGE_NAME_PATTERN.fullmatch(reference):
            raise ValueError("invalid original image snapshot name")
        images.append({reference_field: reference if captured else public_url(reference), "sha256": image["sha256"]})
    if len({image[reference_field] for image in images}) != len(images):
        raise ValueError("duplicate original image reference")
    historical_image_source(core, record, plan)
    return {**plan, image_field: images}
