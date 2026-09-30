"""Concrete Lark document-comment provider for Agent external Connectors.

The provider translates bounded ``lark-cli`` comment pages into the generic
owner-local Connector inbox.  Credentials, document URLs, raw provider
payloads, comment ids, reply ids, and pagination tokens never enter public
LoopX status.  Reply writes use an owner-local receipt store and are verified
through the provider before the generic runtime may acknowledge an event.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from ...file_lock import exclusive_file_lock
from ..external_connector_provider import (
    build_external_connector_permission_requirement,
    build_external_connector_provider_page,
    evaluate_external_connector_permissions,
)
from ..external_connector_runtime import (
    ExternalCapturePolicy,
    ExternalConnectorCapability,
    ExternalResponsePolicy,
    ExternalSourceKind,
    build_external_connector_event,
    normalize_external_connector_binding,
)
from .private_json import write_private_json_atomic

LARK_DOCUMENT_COMMENT_READ_SCOPE = "docs:document.comment:read"
LARK_DOCUMENT_COMMENT_CREATE_SCOPE = "docs:document.comment:create"
LARK_DOCUMENT_COMMENT_CURSOR_SCHEMA = "lark_document_comment_cursor_v0"
LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA = "lark_document_comment_reply_store_v0"
LARK_DOCUMENT_COMMENT_REPAIR_URL = "https://open.larksuite.com/app"

SAFE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
SAFE_PROFILE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
IDEMPOTENCY_KEY_PATTERN = ENVELOPED_SHA256_PATTERN
CURSOR_PREFIX = "lark-comment-v0."
REPLY_CHAIN_PREFIX = "lark-reply-v0."
MAX_PROVIDER_PAGES = 20
LARK_APP_VERSION_PAGE_SIZE = 2

CommandRunner = Callable[
    [Sequence[str], Path | None, float | None],
    Mapping[str, Any],
]


class LarkDocumentCommentProviderError(ValueError):
    """A content-free provider failure suitable for local failure writeback."""

    def __init__(self, code: str) -> None:
        self.code = _safe_token(code, field="provider error code")
        super().__init__(self.code)


@dataclass(frozen=True)
class LarkDocumentCommentTarget:
    """Owner-local routing data for one Lark document comment stream."""

    source_ref: str
    document_url: str
    provider_kind: str = "loopx-lark"
    provider_identity: str = "default-profile"
    profile: str | None = None
    identity: str = "bot"


def _safe_token(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not SAFE_TOKEN_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be an opaque public-safe token")
    return normalized


def _profile(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not SAFE_PROFILE_PATTERN.fullmatch(normalized):
        raise ValueError("Lark profile must be a local safe-name token")
    return normalized


def _document_url(value: object) -> str:
    normalized = str(value or "").strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
        or len(normalized) > 2048
    ):
        raise ValueError("Lark document URL must be a bounded HTTPS URL")
    return normalized


def _identity(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"bot", "user"}:
        raise ValueError("Lark identity must be bot or user")
    return normalized


def _default_runner(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout: float | None = None,
) -> Mapping[str, Any]:
    completed = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _json_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LarkDocumentCommentProviderError(f"{field}_invalid")
    return {str(key): item for key, item in value.items()}


def _json_output(result: Mapping[str, Any], *, operation: str) -> dict[str, Any]:
    try:
        returncode = int(result.get("returncode", 1))
    except (TypeError, ValueError) as exc:
        raise LarkDocumentCommentProviderError(
            "lark_document_comment_process_receipt_invalid"
        ) from exc
    raw = result.get("stdout") if returncode == 0 else result.get("stderr")
    try:
        payload = json.loads(str(raw or ""))
    except json.JSONDecodeError as exc:
        raise LarkDocumentCommentProviderError(
            "lark_document_comment_response_invalid"
        ) from exc
    if (
        returncode != 0
        or not isinstance(payload, Mapping)
        or payload.get("ok") is not True
    ):
        error = payload.get("error") if isinstance(payload, Mapping) else None
        subtype = str(error.get("subtype") or "") if isinstance(error, Mapping) else ""
        if subtype == "missing_scope":
            code = "lark_document_comment_permission_required"
        elif subtype in {"token_expired", "unauthorized"}:
            code = "lark_document_comment_authorization_required"
        else:
            code = f"lark_document_comment_{operation}_failed"
        raise LarkDocumentCommentProviderError(code)
    return {str(key): item for key, item in payload.items()}


def _command_json_mapping(
    result: Mapping[str, Any],
    *,
    error_code: str,
    allowed_returncodes: frozenset[int] = frozenset({0}),
) -> dict[str, Any]:
    try:
        returncode = int(result.get("returncode", 1))
    except (TypeError, ValueError) as exc:
        raise LarkDocumentCommentProviderError(error_code) from exc
    try:
        payload = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError as exc:
        raise LarkDocumentCommentProviderError(error_code) from exc
    if returncode not in allowed_returncodes or not isinstance(payload, Mapping):
        raise LarkDocumentCommentProviderError(error_code)
    return {str(key): item for key, item in payload.items()}


def _published_tenant_scopes(payload: Mapping[str, Any]) -> list[str]:
    data = payload.get("data")
    items = data.get("items") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        raise LarkDocumentCommentProviderError(
            "lark_document_comment_permission_probe_unknown"
        )
    for item in items:
        if not isinstance(item, Mapping):
            raise LarkDocumentCommentProviderError(
                "lark_document_comment_permission_probe_unknown"
            )
        publish_time = item.get("publish_time")
        if item.get("status") != 1 or publish_time is None or publish_time == "":
            continue
        raw_scopes = item.get("scopes")
        if not isinstance(raw_scopes, list):
            raise LarkDocumentCommentProviderError(
                "lark_document_comment_permission_probe_unknown"
            )
        tenant_scopes: list[str] = []
        for raw_scope in raw_scopes:
            if not isinstance(raw_scope, Mapping):
                raise LarkDocumentCommentProviderError(
                    "lark_document_comment_permission_probe_unknown"
                )
            scope = raw_scope.get("scope")
            token_types = raw_scope.get("token_types")
            if not isinstance(scope, str) or not isinstance(token_types, list):
                raise LarkDocumentCommentProviderError(
                    "lark_document_comment_permission_probe_unknown"
                )
            if any(not isinstance(token_type, str) for token_type in token_types):
                raise LarkDocumentCommentProviderError(
                    "lark_document_comment_permission_probe_unknown"
                )
            if scope and "tenant" in token_types:
                tenant_scopes.append(scope)
        return sorted(set(tenant_scopes))
    raise LarkDocumentCommentProviderError(
        "lark_document_comment_permission_probe_unknown"
    )


def _encode_private(prefix: str, value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{prefix}{encoded}"


def _decode_private(prefix: str, value: str, *, field: str) -> dict[str, Any]:
    normalized = str(value or "").strip()
    if not normalized.startswith(prefix) or len(normalized) > 2048:
        raise LarkDocumentCommentProviderError(f"{field}_invalid")
    encoded = normalized[len(prefix) :]
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise LarkDocumentCommentProviderError(f"{field}_invalid") from exc
    return _json_mapping(payload, field=field)


def _default_cursor() -> dict[str, Any]:
    return {
        "schema_version": LARK_DOCUMENT_COMMENT_CURSOR_SCHEMA,
        "phase": "comments",
        "page_token": None,
        "cycle": 0,
    }


def _decode_cursor(value: str | None) -> dict[str, Any]:
    if value is None:
        return _default_cursor()
    payload = _decode_private(CURSOR_PREFIX, value, field="cursor")
    if payload.get("schema_version") != LARK_DOCUMENT_COMMENT_CURSOR_SCHEMA:
        raise LarkDocumentCommentProviderError("cursor_schema_invalid")
    phase = str(payload.get("phase") or "")
    if phase not in {"comments", "replies"}:
        raise LarkDocumentCommentProviderError("cursor_phase_invalid")
    cycle = payload.get("cycle")
    if not isinstance(cycle, int) or cycle < 0:
        raise LarkDocumentCommentProviderError("cursor_cycle_invalid")
    return payload


def _encode_cursor(value: Mapping[str, Any]) -> str:
    return _encode_private(CURSOR_PREFIX, value)


def _epoch_rfc3339(value: object) -> str:
    if isinstance(value, bool):
        raise LarkDocumentCommentProviderError("comment_timestamp_invalid")
    try:
        numeric = int(str(value))
    except (TypeError, ValueError) as exc:
        raise LarkDocumentCommentProviderError("comment_timestamp_invalid") from exc
    if numeric > 10_000_000_000:
        numeric //= 1000
    try:
        parsed = datetime.fromtimestamp(numeric, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise LarkDocumentCommentProviderError("comment_timestamp_invalid") from exc
    return parsed.isoformat().replace("+00:00", "Z")


def _reply_text(reply: Mapping[str, Any], *, quote: str | None = None) -> str:
    content = reply.get("content")
    elements = content.get("elements") if isinstance(content, Mapping) else None
    if not isinstance(elements, list):
        raise LarkDocumentCommentProviderError("comment_content_invalid")
    parts: list[str] = []
    if quote:
        normalized_quote = " ".join(str(quote).split())
        if normalized_quote:
            parts.append(f"Quoted selection: {normalized_quote}")
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        element_type = str(element.get("type") or "")
        if element_type == "text_run":
            text_run = element.get("text_run")
            text = (
                str(text_run.get("text") or "") if isinstance(text_run, Mapping) else ""
            )
            normalized = " ".join(html.unescape(text).split())
            if normalized:
                parts.append(normalized)
        elif element_type == "docs_link":
            parts.append("[document link]")
        elif element_type == "person":
            parts.append("[person mention]")
    text = "\n".join(parts).strip()
    if not text or len(text) > 20_000:
        raise LarkDocumentCommentProviderError("comment_content_invalid")
    return text


def _reply_idempotency_key(reply: Mapping[str, Any]) -> str:
    extra = reply.get("extra")
    if isinstance(extra, Mapping):
        return str(extra.get("loopx_idempotency_key") or "").strip()
    normalized = str(extra or "").strip()
    if not normalized.startswith("{"):
        return normalized
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return ""
    return (
        str(payload.get("loopx_idempotency_key") or "").strip()
        if isinstance(payload, Mapping)
        else ""
    )


def _event_id(source_ref: str, comment_id: str, reply_id: str) -> str:
    digest = hashlib.sha256(
        f"{source_ref}\0{comment_id}\0{reply_id}".encode()
    ).hexdigest()
    return f"lark-comment-{digest}"


def _reply_chain(
    *,
    source_ref: str,
    file_token: str,
    file_type: str,
    comment_id: str,
    can_reply: bool,
) -> str:
    return _encode_private(
        REPLY_CHAIN_PREFIX,
        {
            "source_ref": source_ref,
            "file_token": file_token,
            "file_type": file_type,
            "comment_id": comment_id,
            "can_reply": can_reply,
        },
    )


def build_lark_document_comment_permission_requirements(
    *,
    provider_kind: str,
    provider_identity: str,
    response_enabled: bool,
    publication_required: bool = True,
    repair_url: str = LARK_DOCUMENT_COMMENT_REPAIR_URL,
) -> list[dict[str, Any]]:
    """Build the exact Lark scopes required by the configured operations."""

    operations = [
        (
            ExternalConnectorCapability.HISTORY_CATCH_UP.value,
            [LARK_DOCUMENT_COMMENT_READ_SCOPE],
        )
    ]
    if response_enabled:
        operations.extend(
            [
                (
                    ExternalConnectorCapability.RESPONSE_WRITE.value,
                    [LARK_DOCUMENT_COMMENT_CREATE_SCOPE],
                ),
                (
                    ExternalConnectorCapability.RESPONSE_READBACK.value,
                    [LARK_DOCUMENT_COMMENT_READ_SCOPE],
                ),
            ]
        )
    return [
        build_external_connector_permission_requirement(
            provider_kind=provider_kind,
            provider_identity=provider_identity,
            operation=operation,
            required_scopes=scopes,
            publication_required=publication_required,
            repair_url=repair_url,
        )
        for operation, scopes in operations
    ]


class LarkCliDocumentCommentProvider:
    """Provide bounded page and response callables backed by ``lark-cli``."""

    def __init__(
        self,
        *,
        target: LarkDocumentCommentTarget,
        cli_bin: str = "lark-cli",
        reply_store: Path | None = None,
        runner: CommandRunner = _default_runner,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._source_ref = _safe_token(target.source_ref, field="source_ref")
        self._document_url = _document_url(target.document_url)
        self._provider_kind = _safe_token(
            target.provider_kind,
            field="provider_kind",
        )
        self._provider_identity = _safe_token(
            target.provider_identity,
            field="provider_identity",
        )
        self._profile = _profile(target.profile)
        self._identity = _identity(target.identity)
        self._cli_bin = str(cli_bin or "").strip()
        if not self._cli_bin or "\x00" in self._cli_bin:
            raise ValueError("lark-cli executable is required")
        self._reply_store = reply_store.expanduser() if reply_store else None
        self._runner = runner
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 300.0))

    def _args(self, tail: Sequence[str]) -> list[str]:
        args = [self._cli_bin]
        if self._profile:
            args.extend(["--profile", self._profile])
        return [*args, *tail]

    def _call(self, tail: Sequence[str], *, operation: str) -> dict[str, Any]:
        result = self._runner(
            self._args(tail),
            None,
            self._timeout_seconds,
        )
        return _json_output(result, operation=operation)

    @staticmethod
    def _data(payload: Mapping[str, Any], *, operation: str) -> dict[str, Any]:
        return _json_mapping(payload.get("data"), field=f"{operation}_data")

    def permission_requirements(
        self,
        *,
        response_enabled: bool,
        publication_required: bool = True,
        repair_url: str = LARK_DOCUMENT_COMMENT_REPAIR_URL,
    ) -> list[dict[str, Any]]:
        return build_lark_document_comment_permission_requirements(
            provider_kind=self._provider_kind,
            provider_identity=self._provider_identity,
            response_enabled=response_enabled,
            publication_required=publication_required,
            repair_url=repair_url,
        )

    def permission_guidance(
        self,
        *,
        response_enabled: bool,
        published: bool,
        publication_required: bool = True,
        repair_url: str = LARK_DOCUMENT_COMMENT_REPAIR_URL,
    ) -> dict[str, Any]:
        """Read identity-appropriate scope readiness without returning secrets."""

        requirements = self.permission_requirements(
            response_enabled=response_enabled,
            publication_required=publication_required,
            repair_url=repair_url,
        )
        scopes = sorted(
            {
                scope
                for requirement in requirements
                for scope in requirement["required_scopes"]
            }
        )
        status_payload = _command_json_mapping(
            self._runner(
                self._args(["auth", "status", "--json"]),
                None,
                self._timeout_seconds,
            ),
            error_code="lark_document_comment_permission_probe_invalid",
        )
        if status_payload.get("identity") != self._identity:
            raise LarkDocumentCommentProviderError(
                "lark_document_comment_permission_probe_identity_mismatch"
            )

        if self._identity == "bot":
            # ``auth check`` reads only a stored user OAuth token.  Bot scope
            # evidence instead comes from the current published app-version
            # ledger, whose tenant scopes are the effective Bot permissions.
            identity_payload = _command_json_mapping(
                self._runner(
                    self._args(["whoami", "--json"]),
                    None,
                    self._timeout_seconds,
                ),
                error_code="lark_document_comment_permission_probe_unknown",
            )
            if (
                identity_payload.get("identity") != "bot"
                or identity_payload.get("available") is not True
                or (
                    self._profile is not None
                    and identity_payload.get("profile") != self._profile
                )
            ):
                raise LarkDocumentCommentProviderError(
                    "lark_document_comment_permission_probe_identity_mismatch"
                )
            try:
                app_id = _safe_token(
                    identity_payload.get("appId"),
                    field="Lark application id",
                )
                app_versions = self._call(
                    [
                        "api",
                        "GET",
                        (
                            "/open-apis/application/v6/applications/"
                            f"{app_id}/app_versions"
                        ),
                        "--as",
                        "bot",
                        "--params",
                        json.dumps(
                            {
                                "lang": "zh_cn",
                                "page_size": str(LARK_APP_VERSION_PAGE_SIZE),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "--json",
                    ],
                    operation="permission_probe",
                )
                granted = _published_tenant_scopes(app_versions)
            except (ValueError, LarkDocumentCommentProviderError) as exc:
                raise LarkDocumentCommentProviderError(
                    "lark_document_comment_permission_probe_unknown"
                ) from exc
        else:
            payload = _command_json_mapping(
                self._runner(
                    self._args(
                        ["auth", "check", "--scope", " ".join(scopes), "--json"]
                    ),
                    None,
                    self._timeout_seconds,
                ),
                error_code="lark_document_comment_permission_probe_invalid",
                allowed_returncodes=frozenset({0, 1}),
            )
            granted = payload.get("granted", [])
            missing = payload.get("missing", [])
            if (
                not isinstance(granted, list)
                or not isinstance(missing, list)
                or any(not isinstance(scope, str) for scope in [*granted, *missing])
                or set(granted).intersection(missing)
                or set(granted).union(missing) != set(scopes)
            ):
                raise LarkDocumentCommentProviderError(
                    "lark_document_comment_permission_probe_invalid"
                )
        return dict(
            evaluate_external_connector_permissions(
                requirements=requirements,
                granted_scopes={self._provider_identity: granted},
                published=published,
            )
        )

    def _validate_connector(self, connector: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_external_connector_binding(connector)
        if normalized["source_kind"] != ExternalSourceKind.DOCUMENT_COMMENT.value:
            raise LarkDocumentCommentProviderError("connector_source_kind_mismatch")
        if normalized["capture_policy"] == ExternalCapturePolicy.ADDRESSED_ONLY.value:
            raise LarkDocumentCommentProviderError(
                "connector_address_filter_unsupported"
            )
        if normalized["provider_kind"] != self._provider_kind:
            raise LarkDocumentCommentProviderError("connector_provider_kind_mismatch")
        if normalized["source_ref"] != self._source_ref:
            raise LarkDocumentCommentProviderError("connector_source_ref_mismatch")
        return dict(normalized)

    def _known_reply_ids(self) -> set[str]:
        if self._reply_store is None or not self._reply_store.is_file():
            return set()
        store = self._load_reply_store()
        return {
            str(item.get("reply_id") or "")
            for item in store["entries"].values()
            if isinstance(item, Mapping) and item.get("reply_id")
        }

    def _events_from_replies(
        self,
        *,
        replies: Sequence[Mapping[str, Any]],
        file_token: str,
        file_type: str,
        comment_id: str,
        can_reply: bool,
        quote: str | None = None,
    ) -> list[dict[str, Any]]:
        ignored_reply_ids = self._known_reply_ids()
        events: list[dict[str, Any]] = []
        for index, reply in enumerate(replies):
            reply_id = _safe_token(reply.get("reply_id"), field="reply_id")
            if reply_id in ignored_reply_ids:
                continue
            content = _reply_text(reply, quote=quote if index == 0 else None)
            events.append(
                build_external_connector_event(
                    event_id=_event_id(self._source_ref, comment_id, reply_id),
                    content=content,
                    occurred_at=_epoch_rfc3339(reply.get("create_time")),
                    addressed=False,
                    event_cursor=reply_id,
                    anchor_ref=comment_id,
                    reply_chain_ref=_reply_chain(
                        source_ref=self._source_ref,
                        file_token=file_token,
                        file_type=file_type,
                        comment_id=comment_id,
                        can_reply=can_reply,
                    ),
                )
            )
        return events

    def _comment_page(
        self,
        *,
        connector: Mapping[str, Any],
        state: Mapping[str, Any],
        expected_cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        args = [
            "drive",
            "+list-comments",
            "--url",
            self._document_url,
            "--solved-status",
            "false",
            "--comment-scope",
            "all",
            "--page-size",
            "1",
            "--as",
            self._identity,
            "--format",
            "json",
        ]
        page_token = str(state.get("page_token") or "")
        if page_token:
            args.extend(["--page-token", page_token])
        data = self._data(
            self._call(args, operation="comment_page_read"),
            operation="comment_page_read",
        )
        items = data.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, Mapping) for item in items
        ):
            raise LarkDocumentCommentProviderError("comment_page_items_invalid")
        file_token = _safe_token(data.get("file_token"), field="file_token")
        file_type = _safe_token(data.get("file_type"), field="file_type")
        outer_has_more = data.get("has_more") is True
        outer_next = str(data.get("page_token") or "") if outer_has_more else ""
        cycle = int(state["cycle"])
        events: list[dict[str, Any]] = []
        next_state: dict[str, Any]
        has_more = outer_has_more
        if items:
            item = items[0]
            comment_id = _safe_token(item.get("comment_id"), field="comment_id")
            replies_container = item.get("reply_list")
            replies = (
                replies_container.get("replies")
                if isinstance(replies_container, Mapping)
                else None
            )
            if not isinstance(replies, list) or any(
                not isinstance(reply, Mapping) for reply in replies
            ):
                raise LarkDocumentCommentProviderError("comment_replies_invalid")
            can_reply = not (
                item.get("is_solved") is True or item.get("is_whole") is True
            )
            if (
                connector["response_policy"] == ExternalResponsePolicy.NO_RESPONSE.value
                or can_reply
            ):
                events = self._events_from_replies(
                    replies=replies,
                    file_token=file_token,
                    file_type=file_type,
                    comment_id=comment_id,
                    can_reply=can_reply,
                    quote=str(item.get("quote") or "") or None,
                )
            if len(events) > limit:
                raise LarkDocumentCommentProviderError("comment_page_limit_exceeded")
            if item.get("has_more") is True:
                reply_page_token = str(item.get("page_token") or "").strip()
                if not reply_page_token:
                    raise LarkDocumentCommentProviderError(
                        "comment_reply_cursor_missing"
                    )
                next_state = {
                    "schema_version": LARK_DOCUMENT_COMMENT_CURSOR_SCHEMA,
                    "phase": "replies",
                    "cycle": cycle,
                    "comment_id": comment_id,
                    "file_token": file_token,
                    "file_type": file_type,
                    "reply_page_token": reply_page_token,
                    "outer_page_token": outer_next or None,
                    "can_reply": can_reply,
                }
                has_more = True
            else:
                next_state = {
                    "schema_version": LARK_DOCUMENT_COMMENT_CURSOR_SCHEMA,
                    "phase": "comments",
                    "cycle": cycle if outer_has_more else cycle + 1,
                    "page_token": outer_next or None,
                }
        else:
            next_state = {
                "schema_version": LARK_DOCUMENT_COMMENT_CURSOR_SCHEMA,
                "phase": "comments",
                "cycle": cycle if outer_has_more else cycle + 1,
                "page_token": outer_next or None,
            }
        return dict(
            build_external_connector_provider_page(
                events=events,
                expected_cursor=expected_cursor,
                next_cursor=_encode_cursor(next_state),
                has_more=has_more,
            )
        )

    def _reply_page(
        self,
        *,
        connector: Mapping[str, Any],
        state: Mapping[str, Any],
        expected_cursor: str,
        limit: int,
    ) -> dict[str, Any]:
        comment_id = _safe_token(state.get("comment_id"), field="comment_id")
        file_token = _safe_token(state.get("file_token"), field="file_token")
        file_type = _safe_token(state.get("file_type"), field="file_type")
        reply_page_token = str(state.get("reply_page_token") or "").strip()
        if not reply_page_token:
            raise LarkDocumentCommentProviderError("comment_reply_cursor_missing")
        data = self._data(
            self._call(
                [
                    "drive",
                    "file.comment.replys",
                    "list",
                    "--file-token",
                    file_token,
                    "--file-type",
                    file_type,
                    "--comment-id",
                    comment_id,
                    "--page-size",
                    str(min(limit, 100)),
                    "--page-token",
                    reply_page_token,
                    "--as",
                    self._identity,
                    "--format",
                    "json",
                ],
                operation="comment_reply_page_read",
            ),
            operation="comment_reply_page_read",
        )
        replies = data.get("items")
        if not isinstance(replies, list) or any(
            not isinstance(reply, Mapping) for reply in replies
        ):
            raise LarkDocumentCommentProviderError("comment_replies_invalid")
        can_reply = state.get("can_reply") is True
        events = []
        if (
            connector["response_policy"] == ExternalResponsePolicy.NO_RESPONSE.value
            or can_reply
        ):
            events = self._events_from_replies(
                replies=replies,
                file_token=file_token,
                file_type=file_type,
                comment_id=comment_id,
                can_reply=can_reply,
            )
        if len(events) > limit:
            raise LarkDocumentCommentProviderError("comment_page_limit_exceeded")
        reply_has_more = data.get("has_more") is True
        if reply_has_more:
            next_reply_token = str(data.get("page_token") or "").strip()
            if not next_reply_token:
                raise LarkDocumentCommentProviderError("comment_reply_cursor_missing")
            next_state = {**state, "reply_page_token": next_reply_token}
        else:
            outer_page_token = str(state.get("outer_page_token") or "").strip()
            next_state = {
                "schema_version": LARK_DOCUMENT_COMMENT_CURSOR_SCHEMA,
                "phase": "comments",
                "cycle": int(state["cycle"])
                if outer_page_token
                else int(state["cycle"]) + 1,
                "page_token": outer_page_token or None,
            }
        return dict(
            build_external_connector_provider_page(
                events=events,
                expected_cursor=expected_cursor,
                next_cursor=_encode_cursor(next_state),
                has_more=reply_has_more or bool(state.get("outer_page_token")),
            )
        )

    def page_reader(
        self,
        connector: Mapping[str, Any],
        expected_cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Read one comment or nested-reply page for the generic runtime."""

        normalized = self._validate_connector(connector)
        bounded_limit = max(1, min(int(limit), 100))
        state = _decode_cursor(expected_cursor)
        if state["phase"] == "replies":
            if expected_cursor is None:
                raise LarkDocumentCommentProviderError("cursor_phase_invalid")
            return self._reply_page(
                connector=normalized,
                state=state,
                expected_cursor=expected_cursor,
                limit=bounded_limit,
            )
        return self._comment_page(
            connector=normalized,
            state=state,
            expected_cursor=expected_cursor,
            limit=bounded_limit,
        )

    def _load_reply_store(self) -> dict[str, Any]:
        if self._reply_store is None or not self._reply_store.is_file():
            return {
                "schema_version": LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA,
                "entries": {},
            }
        try:
            payload = json.loads(self._reply_store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LarkDocumentCommentProviderError("reply_store_unreadable") from exc
        store = _json_mapping(payload, field="reply_store")
        if store.get("schema_version") != LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA:
            raise LarkDocumentCommentProviderError("reply_store_schema_invalid")
        if not isinstance(store.get("entries"), Mapping):
            raise LarkDocumentCommentProviderError("reply_store_entries_invalid")
        return store

    def _write_reply_store(self, store: Mapping[str, Any]) -> None:
        if self._reply_store is None:
            raise LarkDocumentCommentProviderError("reply_store_required")
        write_private_json_atomic(self._reply_store, store)

    def _reply_pages(
        self,
        *,
        file_token: str,
        file_type: str,
        comment_id: str,
    ) -> Iterator[list[Mapping[str, Any]]]:
        page_token = ""
        for _page in range(MAX_PROVIDER_PAGES):
            args = [
                "drive",
                "file.comment.replys",
                "list",
                "--file-token",
                file_token,
                "--file-type",
                file_type,
                "--comment-id",
                comment_id,
                "--page-size",
                "100",
                "--as",
                self._identity,
                "--format",
                "json",
            ]
            if page_token:
                args.extend(["--page-token", page_token])
            data = self._data(
                self._call(args, operation="comment_reply_readback"),
                operation="comment_reply_readback",
            )
            items = data.get("items")
            if not isinstance(items, list) or any(
                not isinstance(item, Mapping) for item in items
            ):
                raise LarkDocumentCommentProviderError("comment_reply_readback_invalid")
            yield items
            if data.get("has_more") is not True:
                return
            page_token = str(data.get("page_token") or "").strip()
            if not page_token:
                raise LarkDocumentCommentProviderError("comment_reply_cursor_missing")
        raise LarkDocumentCommentProviderError("comment_reply_readback_page_limit")

    def _reply_readback(
        self,
        *,
        file_token: str,
        file_type: str,
        comment_id: str,
        reply_id: str,
        expected_text: str,
    ) -> bool:
        for items in self._reply_pages(
            file_token=file_token,
            file_type=file_type,
            comment_id=comment_id,
        ):
            for item in items:
                if str(item.get("reply_id") or "") == reply_id:
                    return _reply_text(item) == expected_text
        return False

    def _recover_reply_id(
        self,
        *,
        file_token: str,
        file_type: str,
        comment_id: str,
        idempotency_key: str,
        expected_text: str,
    ) -> str | None:
        for items in self._reply_pages(
            file_token=file_token,
            file_type=file_type,
            comment_id=comment_id,
        ):
            for item in items:
                if _reply_idempotency_key(item) != idempotency_key:
                    continue
                if _reply_text(item) != expected_text:
                    raise LarkDocumentCommentProviderError(
                        "reply_receipt_digest_mismatch"
                    )
                return _safe_token(item.get("reply_id"), field="reply_id")
        return None

    def response_writer(
        self,
        connector: Mapping[str, Any],
        event: Mapping[str, Any],
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create and verify one reply before the generic runtime may ACK."""

        normalized = self._validate_connector(connector)
        if normalized["response_policy"] == ExternalResponsePolicy.NO_RESPONSE.value:
            raise LarkDocumentCommentProviderError("connector_response_disabled")
        if self._reply_store is None:
            raise LarkDocumentCommentProviderError("reply_store_required")
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(str(idempotency_key or "")):
            raise LarkDocumentCommentProviderError("idempotency_key_invalid")
        chain = _decode_private(
            REPLY_CHAIN_PREFIX,
            str(event.get("reply_chain_ref") or ""),
            field="reply_chain",
        )
        if str(chain.get("source_ref") or "") != self._source_ref:
            raise LarkDocumentCommentProviderError("reply_chain_source_mismatch")
        if chain.get("can_reply") is not True:
            raise LarkDocumentCommentProviderError("comment_reply_not_supported")
        file_token = _safe_token(chain.get("file_token"), field="file_token")
        file_type = _safe_token(chain.get("file_type"), field="file_type")
        comment_id = _safe_token(chain.get("comment_id"), field="comment_id")
        normalized_text = " ".join(str(text or "").split())
        if not normalized_text or len(normalized_text) > 1200:
            raise LarkDocumentCommentProviderError("reply_text_invalid")
        text_digest = "sha256:" + hashlib.sha256(normalized_text.encode()).hexdigest()

        with exclusive_file_lock(
            self._reply_store,
            operation="lark_document_comment_response",
        ):
            store = self._load_reply_store()
            entries = {str(key): value for key, value in dict(store["entries"]).items()}
            existing = entries.get(idempotency_key)
            if isinstance(existing, Mapping):
                binding = {
                    "source_ref": self._source_ref,
                    "file_token": file_token,
                    "file_type": file_type,
                    "comment_id": comment_id,
                    "text_digest": text_digest,
                }
                if any(
                    existing.get(field) != value for field, value in binding.items()
                ):
                    raise LarkDocumentCommentProviderError(
                        "reply_receipt_digest_mismatch"
                    )
                reply_id = (
                    _safe_token(existing.get("reply_id"), field="reply_id")
                    if existing.get("reply_id")
                    else self._recover_reply_id(
                        file_token=file_token,
                        file_type=file_type,
                        comment_id=comment_id,
                        idempotency_key=idempotency_key,
                        expected_text=normalized_text,
                    )
                )
                if reply_id is None:
                    raise LarkDocumentCommentProviderError(
                        "comment_reply_recovery_pending"
                    )
                entries[idempotency_key] = {
                    **dict(existing),
                    "reply_id": reply_id,
                    "status": "pending_readback",
                }
                self._write_reply_store(
                    {
                        "schema_version": LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA,
                        "entries": entries,
                    }
                )
                verified = self._reply_readback(
                    file_token=file_token,
                    file_type=file_type,
                    comment_id=comment_id,
                    reply_id=reply_id,
                    expected_text=normalized_text,
                )
                if not verified:
                    raise LarkDocumentCommentProviderError(
                        "comment_reply_readback_failed"
                    )
            else:
                escaped_text = normalized_text.replace("<", "&lt;").replace(">", "&gt;")
                entries[idempotency_key] = {
                    "source_ref": self._source_ref,
                    "file_token": file_token,
                    "file_type": file_type,
                    "comment_id": comment_id,
                    "text_digest": text_digest,
                    "status": "creating",
                }
                self._write_reply_store(
                    {
                        "schema_version": LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA,
                        "entries": entries,
                    }
                )
                data = self._data(
                    self._call(
                        [
                            "drive",
                            "file.comment.replys",
                            "create",
                            "--file-token",
                            file_token,
                            "--file-type",
                            file_type,
                            "--comment-id",
                            comment_id,
                            "--data",
                            json.dumps(
                                {
                                    "content": {
                                        "elements": [
                                            {
                                                "type": "text_run",
                                                "text_run": {"text": escaped_text},
                                            }
                                        ]
                                    },
                                    "extra": json.dumps(
                                        {"loopx_idempotency_key": idempotency_key},
                                        separators=(",", ":"),
                                    ),
                                },
                                separators=(",", ":"),
                            ),
                            "--as",
                            self._identity,
                            "--format",
                            "json",
                        ],
                        operation="comment_reply_create",
                    ),
                    operation="comment_reply_create",
                )
                reply_id = _safe_token(data.get("reply_id"), field="reply_id")
                entries[idempotency_key] = {
                    **dict(entries[idempotency_key]),
                    "reply_id": reply_id,
                    "status": "pending_readback",
                }
                self._write_reply_store(
                    {
                        "schema_version": LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA,
                        "entries": entries,
                    }
                )
                verified = self._reply_readback(
                    file_token=file_token,
                    file_type=file_type,
                    comment_id=comment_id,
                    reply_id=reply_id,
                    expected_text=normalized_text,
                )
                if not verified:
                    raise LarkDocumentCommentProviderError(
                        "comment_reply_readback_failed"
                    )
            entries[idempotency_key] = {
                **dict(entries[idempotency_key]),
                "status": "verified",
            }
            self._write_reply_store(
                {
                    "schema_version": LARK_DOCUMENT_COMMENT_REPLY_STORE_SCHEMA,
                    "entries": entries,
                }
            )
        return {
            "external_write_performed": True,
            "verification_performed": True,
            "readback_verified": True,
            # The generic provider call site validates this opaque key before it
            # reduces the provider receipt to the public-safe response receipt.
            "idempotency_key": idempotency_key,
            "idempotency_key_digest": "sha256:"
            + hashlib.sha256(idempotency_key.encode()).hexdigest(),
            "private_provider_payload_captured": False,
        }
