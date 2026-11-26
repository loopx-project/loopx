"""Input normalization and public-safety helpers for maintainer corrections."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from ...control_plane.runtime.public_safety import public_safe_compact_text

ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION = (
    "issue_fix_maintainer_correction_input_v0"
)
ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1 = (
    "issue_fix_maintainer_correction_input_v1"
)
MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSIONS = {
    ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION,
    ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1,
}
ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION = (
    "issue_fix_maintainer_correction_admission_v0"
)
ISSUE_FIX_MAINTAINER_CORRECTION_LEDGER_ENTRY_SCHEMA_VERSION = (
    "issue_fix_maintainer_correction_ledger_entry_v0"
)
MAINTAINER_CORRECTION_OPERATION_ID_PREFIX = "issue-fix-correction:"
MAINTAINER_CORRECTION_KINDS = {
    "actionable_patch",
    "semantic_ambiguity",
    "missing_authority",
    "unchanged",
}
MAINTAINER_CORRECTION_SOURCE_KINDS = {"review", "maintainer_comment"}
MAINTAINER_CORRECTION_SOURCE_EVENT_KINDS = {
    "issue_comment",
    "review",
    "review_comment",
}
MAINTAINER_CORRECTION_SOURCE_EVENT_FIELDS = {
    "kind",
    "id",
    "html_url",
    "author_login",
    "author_association",
    "created_at",
    "updated_at",
}
MAINTAINER_CORRECTION_ADMISSION_BASES = {"host_asserted", "provider_verified"}
MAINTAINER_CORRECTION_DISPOSITIONS = {"accepted", "context_only", "superseded"}
MAINTAINER_CORRECTION_REJECTION_REASONS = {
    "source_event_not_observed",
    "source_event_revision_mismatch",
    "stale_revision",
    "self_or_bot_author",
    "author_not_maintainer",
    "terminal_pr_state",
    "goal_not_active",
    "origin_request_cancelled",
    "origin_request_unreadable",
}
# GitHub Apps post as `<slug>[bot]`; without a configured LoopX actor login this
# suffix is the only self/bot signal available from body-free event metadata.
BOT_LOGIN_SUFFIX = "[bot]"
_GITHUB_LOGIN_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
_PROVIDER_EVENT_ID_PATTERN = re.compile(r"[0-9]{1,20}")
WRITE_SCOPES = {"write", "publish", "external_review_request"}
MAINTAINER_CORRECTION_INPUT_FIELDS = {
    "schema_version",
    "correction_kind",
    "source_kind",
    "source_ref",
    "summary",
    "verification_plan",
    "pr_update_path",
    "user_question",
    "missing_authority_scopes",
    "source_event",
}
TERMINAL_PR_STATES = {"MERGED", "CLOSED"}

# Set to "1" to abort between the accepted ledger entry write and its Todo
# write. Exists only to prove the next run reconciles the pending entry.
MAINTAINER_CORRECTION_FAULT_ENV = "LOOPX_ISSUE_FIX_FAULT_BEFORE_CORRECTION_TODO"
_SOURCE_EVENT_REFUSALS = {
    "source_event_not_observed",
    "source_event_revision_mismatch",
    "self_or_bot_author",
    "author_not_maintainer",
}


def _upper_label(value: object, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def build_issue_fix_transition_preview(
    *,
    decision: str,
    action_kind: str,
    priority: str,
    reason: str,
    depends_on: Sequence[str] = ("issue_fix_pr_lifecycle_monitor",),
    role: str = "agent",
    task_class: str = "continuous_monitor",
    text: str | None = None,
    terminal_state_precedence: bool = False,
    material_change: bool = False,
    **extra: object,
) -> dict[str, object]:
    """Return a non-mutating PR lifecycle transition preview payload."""

    if text is None:
        text = f"[{priority}] {reason}"
    return {
        "schema_version": "issue_fix_pr_lifecycle_transition_v0",
        "decision": decision,
        "command_preview": "loopx todo transition",
        "role": role,
        "priority": priority,
        "task_class": task_class,
        "action_kind": action_kind,
        "reason": reason,
        "text": text,
        "depends_on": list(depends_on),
        "would_write": False,
        "requires_execute_flag": True,
        "terminal_state_precedence": terminal_state_precedence,
        "material_change": material_change,
        **extra,
    }


def _compact_public_text(value: object, *, field: str, limit: int) -> str:
    raw = " ".join(str(value or "").strip().split())
    if len(raw) > limit:
        raise ValueError(f"maintainer correction {field} exceeds the compact limit")
    if "<!--" in raw or "-->" in raw or "\x00" in raw:
        raise ValueError(
            f"maintainer correction {field} must not contain control-plane markup"
        )
    text = public_safe_compact_text(value, limit=limit)
    if not text:
        raise ValueError(
            f"maintainer correction {field} must be compact and public-safe"
        )
    return " ".join(text.split())


def _public_source_reference(value: object) -> str:
    reference = _compact_public_text(value, field="source_ref", limit=300)
    if "://" in reference:
        parsed = urlsplit(reference)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
        ):
            raise ValueError(
                "maintainer correction source_ref URL must be public https without user info or query"
            )
        hostname = (parsed.hostname or "").lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            raise ValueError(
                "maintainer correction source_ref must not target a local host"
            )
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise ValueError(
                "maintainer correction source_ref must target a public host"
            )
        return reference
    if reference.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", reference):
        raise ValueError("maintainer correction source_ref must not be a local path")
    path = PurePosixPath(reference.replace("\\", "/"))
    if path.as_posix() == "." or ".." in path.parts:
        raise ValueError("maintainer correction source_ref must be repo-relative")
    return path.as_posix()


def _normalise_provider_event_id(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError("maintainer correction source_event id must be a provider id")
    text = str(value if value is not None else "").strip()
    if not _PROVIDER_EVENT_ID_PATTERN.fullmatch(text):
        raise ValueError(
            "maintainer correction source_event id must be a numeric provider id"
        )
    return text


def _normalise_author_login(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not _GITHUB_LOGIN_PATTERN.fullmatch(text):
        raise ValueError(
            f"maintainer correction {field} must be a public provider login"
        )
    return text


def _normalise_event_timestamp(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    parsed = _timestamp(text)
    if parsed is None:
        raise ValueError(
            f"maintainer correction {field} must be a timezone-aware ISO timestamp"
        )
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_issue_fix_maintainer_correction_source_event(
    value: object,
) -> dict[str, object]:
    """Normalise the provider event that carried a maintainer correction.

    Only identity and revision metadata survive; no body field is accepted.
    """

    if not isinstance(value, Mapping):
        raise ValueError("maintainer correction source_event must be an object")
    unknown_fields = set(value) - MAINTAINER_CORRECTION_SOURCE_EVENT_FIELDS
    if unknown_fields:
        raise ValueError(
            "maintainer correction source_event contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unknown_fields))
        )
    kind = str(value.get("kind") or "").strip()
    if kind not in MAINTAINER_CORRECTION_SOURCE_EVENT_KINDS:
        raise ValueError(
            "maintainer correction source_event kind must be issue_comment, review, or review_comment"
        )
    html_url = _public_source_reference(value.get("html_url"))
    if "://" not in html_url:
        raise ValueError(
            "maintainer correction source_event html_url must be a public https URL"
        )
    return {
        "kind": kind,
        "id": _normalise_provider_event_id(value.get("id")),
        "html_url": html_url,
        "author_login": _normalise_author_login(
            value.get("author_login"), field="source_event author_login"
        ),
        "author_association": _upper_label(value.get("author_association"), "NONE"),
        "created_at": _normalise_event_timestamp(
            value.get("created_at"), field="source_event created_at"
        ),
        "updated_at": _normalise_event_timestamp(
            value.get("updated_at"), field="source_event updated_at"
        ),
        "body_captured": False,
    }


def normalise_issue_fix_maintainer_correction_input(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Validate and hash one maintainer-correction payload into normalized form."""

    unknown_fields = set(value) - MAINTAINER_CORRECTION_INPUT_FIELDS
    if unknown_fields:
        raise ValueError(
            "maintainer correction input contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unknown_fields))
        )
    schema_version = value.get("schema_version")
    if schema_version not in MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSIONS:
        raise ValueError(
            "maintainer correction schema_version must be "
            "issue_fix_maintainer_correction_input_v0 or issue_fix_maintainer_correction_input_v1"
        )
    provider_verified = (
        schema_version == ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1
    )
    if provider_verified:
        if "source_event" not in value:
            raise ValueError(
                "issue_fix_maintainer_correction_input_v1 requires source_event"
            )
        source_event: dict[str, object] | None = (
            normalise_issue_fix_maintainer_correction_source_event(
                value.get("source_event")
            )
        )
    else:
        if "source_event" in value:
            raise ValueError(
                "source_event requires schema_version issue_fix_maintainer_correction_input_v1"
            )
        source_event = None
    correction_kind = str(value.get("correction_kind") or "").strip()
    if correction_kind not in MAINTAINER_CORRECTION_KINDS:
        raise ValueError(
            "maintainer correction correction_kind must be actionable_patch, semantic_ambiguity, missing_authority, or unchanged"
        )
    source_kind = str(value.get("source_kind") or "").strip()
    if source_kind not in MAINTAINER_CORRECTION_SOURCE_KINDS:
        raise ValueError(
            "maintainer correction source_kind must be review or maintainer_comment"
        )
    source_ref = _public_source_reference(value.get("source_ref"))
    summary = _compact_public_text(value.get("summary"), field="summary", limit=400)
    verification_plan = ""
    pr_update_path = ""
    user_question = ""
    missing_authority_scopes: list[str] = []
    if correction_kind == "actionable_patch":
        verification_plan = _compact_public_text(
            value.get("verification_plan"), field="verification_plan", limit=300
        )
        pr_update_path = _compact_public_text(
            value.get("pr_update_path"), field="pr_update_path", limit=240
        )
    elif correction_kind == "semantic_ambiguity":
        user_question = _compact_public_text(
            value.get("user_question"), field="user_question", limit=300
        )
    elif correction_kind == "missing_authority":
        raw_scopes = value.get("missing_authority_scopes")
        if not isinstance(raw_scopes, Sequence) or isinstance(raw_scopes, (str, bytes)):
            raise ValueError("missing_authority requires missing_authority_scopes")
        for raw_scope in raw_scopes:
            scope = str(raw_scope or "").strip()
            if scope not in WRITE_SCOPES:
                raise ValueError("maintainer correction authority scope is unsupported")
            if scope not in missing_authority_scopes:
                missing_authority_scopes.append(scope)
        if not missing_authority_scopes:
            raise ValueError("missing_authority requires at least one authority scope")
    # The fingerprint covers the compacted correction content under the v0
    # field set, so existing v0 ledger rows keep their recorded fingerprints and
    # a v1 event carrying identical content hashes the same; event identity
    # lives in the operation id, not here.
    content: dict[str, object] = {
        "schema_version": ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION,
        "correction_kind": correction_kind,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "summary": summary,
        "verification_plan": verification_plan or None,
        "pr_update_path": pr_update_path or None,
        "user_question": user_question or None,
        "missing_authority_scopes": missing_authority_scopes,
        "raw_body_captured": False,
        "raw_comment_captured": False,
        "raw_provider_payload_captured": False,
        "local_paths_captured": False,
    }
    fingerprint = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    normalized: dict[str, object] = {
        **content,
        "schema_version": str(schema_version),
        "source_event": source_event,
        "admission_basis": "provider_verified" if provider_verified else "host_asserted",
        "correction_fingerprint": fingerprint,
    }
    return normalized


def issue_fix_maintainer_correction_operation_id(
    correction: Mapping[str, object], *, repo: str, pr_ref: str
) -> str:
    """Return the stable Todo operation id for one correction revision."""

    source_event = correction.get("source_event")
    if isinstance(source_event, Mapping):
        identity = [
            repo,
            pr_ref,
            str(source_event.get("kind")),
            str(source_event.get("id")),
            str(source_event.get("updated_at")),
        ]
    else:
        identity = [repo, pr_ref, str(correction.get("correction_fingerprint"))]
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"{MAINTAINER_CORRECTION_OPERATION_ID_PREFIX}{digest}"


def _provider_event_entries(value: object) -> list[Mapping[object, object]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def normalise_issue_fix_pr_source_events(
    provider_payload: Mapping[str, object] | None,
) -> dict[str, object]:
    """Project body-free PR comment/review metadata into verifiable events.

    Reviews expose ``submitted_at`` instead of ``updated_at``; that timestamp
    stands in as the review revision because GitHub does not re-time edits of a
    review summary through the list endpoint.
    """

    payload: Mapping[str, object] = (
        provider_payload if isinstance(provider_payload, Mapping) else {}
    )
    groups = (
        ("issue_comment", payload.get("comments") or payload.get("issueComments")),
        ("review", payload.get("reviews")),
        ("review_comment", payload.get("reviewComments") or payload.get("review_comments")),
    )
    available = any(
        isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
        for _kind, raw in groups
    )
    events: list[dict[str, object]] = []
    for kind, raw in groups:
        for item in _provider_event_entries(raw):
            raw_id = item.get("id")
            if isinstance(raw_id, bool) or raw_id is None:
                continue
            event_id = str(raw_id).strip()
            if not _PROVIDER_EVENT_ID_PATTERN.fullmatch(event_id):
                continue
            author = item.get("author") or item.get("user")
            if isinstance(author, Mapping):
                author = author.get("login")
            updated_at = (
                item.get("updated_at")
                or item.get("updatedAt")
                or item.get("submitted_at")
                or item.get("submittedAt")
            )
            created_at = item.get("created_at") or item.get("createdAt")
            events.append(
                {
                    "kind": kind,
                    "id": event_id,
                    "author_login": str(author or "").strip()[:60] or None,
                    "author_association": _upper_label(
                        item.get("author_association")
                        or item.get("authorAssociation"),
                        "NONE",
                    ),
                    "created_at": (
                        _normalise_event_timestamp(created_at, field="created_at")
                        if _timestamp(created_at)
                        else None
                    ),
                    "updated_at": (
                        _normalise_event_timestamp(updated_at, field="updated_at")
                        if _timestamp(updated_at)
                        else None
                    ),
                    "body_captured": False,
                }
            )
    return {
        "status": "available" if available else "unavailable",
        "events": events,
        "body_captured": False,
    }
