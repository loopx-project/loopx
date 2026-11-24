"""Maintainer-correction admission, transition, settlement, and origin-request binding."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, NotRequired, TypedDict
from urllib.parse import urlsplit

from ...control_plane.projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...control_plane.runtime.time import now_utc_iso
from ...domain_packs.issue_fix import (
    issue_fix_maintainer_correction_entries,
    read_issue_fix_pr_lifecycle_ledger_row,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from .github_public import MAINTAINER_ASSOCIATIONS


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


def _upper_label(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _timestamp(value: Any) -> datetime | None:
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
    **extra: Any,
) -> dict[str, Any]:
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


def _compact_public_text(value: Any, *, field: str, limit: int) -> str:
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


def _public_source_reference(value: Any) -> str:
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


def _normalise_provider_event_id(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("maintainer correction source_event id must be a provider id")
    text = str(value if value is not None else "").strip()
    if not _PROVIDER_EVENT_ID_PATTERN.fullmatch(text):
        raise ValueError("maintainer correction source_event id must be a numeric provider id")
    return text


def _normalise_author_login(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _GITHUB_LOGIN_PATTERN.fullmatch(text):
        raise ValueError(f"maintainer correction {field} must be a public provider login")
    return text


def _normalise_event_timestamp(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    parsed = _timestamp(text)
    if parsed is None:
        raise ValueError(
            f"maintainer correction {field} must be a timezone-aware ISO timestamp"
        )
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_issue_fix_maintainer_correction_source_event(
    value: Any,
) -> dict[str, Any]:
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
        raise ValueError("maintainer correction source_event html_url must be a public https URL")
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
    value: Mapping[str, Any],
) -> dict[str, Any]:
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
        source_event: dict[str, Any] | None = (
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
    content = {
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
    normalized = {
        **content,
        "schema_version": str(schema_version),
        "source_event": source_event,
        "admission_basis": "provider_verified" if provider_verified else "host_asserted",
    }
    normalized["correction_fingerprint"] = fingerprint
    return normalized


def issue_fix_maintainer_correction_operation_id(
    correction: Mapping[str, Any], *, repo: str, pr_ref: str
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


def _provider_event_entries(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def normalise_issue_fix_pr_source_events(
    provider_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project body-free PR comment/review metadata into verifiable events.

    Reviews expose ``submitted_at`` instead of ``updated_at``; that timestamp
    stands in as the review revision because GitHub does not re-time edits of a
    review summary through the list endpoint.
    """

    payload = provider_payload if isinstance(provider_payload, Mapping) else {}
    groups = (
        ("issue_comment", payload.get("comments") or payload.get("issueComments")),
        ("review", payload.get("reviews")),
        ("review_comment", payload.get("reviewComments") or payload.get("review_comments")),
    )
    available = any(
        isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
        for _kind, raw in groups
    )
    events: list[dict[str, Any]] = []
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
                        if _timestamp(created_at) else None
                    ),
                    "updated_at": (
                        _normalise_event_timestamp(updated_at, field="updated_at")
                        if _timestamp(updated_at) else None
                    ),
                    "body_captured": False,
                }
            )
    return {
        "status": "available" if available else "unavailable",
        "events": events,
        "body_captured": False,
    }


def _same_instant(left: Any, right: Any) -> bool:
    left_time = _timestamp(left)
    right_time = _timestamp(right)
    return left_time is not None and right_time is not None and left_time == right_time


def _is_bot_or_self_login(login: Any) -> bool:
    text = str(login or "").strip().lower()
    return text.endswith(BOT_LOGIN_SUFFIX)


def _observed_source_events(
    source_events: Mapping[str, Any] | None, source_event: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    raw_events = source_events.get("events") if isinstance(source_events, Mapping) else []
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        return []
    return [
        event
        for event in raw_events
        if isinstance(event, Mapping)
        and event.get("kind") == source_event.get("kind")
        and str(event.get("id")) == str(source_event.get("id"))
    ]


def _latest_accepted_revision(
    prior_entries: Sequence[Mapping[str, Any]], *, source_event: Mapping[str, Any]
) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for entry in prior_entries:
        event = entry.get("source_event")
        if not isinstance(event, Mapping):
            continue
        if event.get("kind") != source_event.get("kind") or str(
            event.get("id")
        ) != str(source_event.get("id")):
            continue
        if entry.get("disposition") not in {"accepted", "superseded"}:
            continue
        candidate_time = _timestamp(event.get("updated_at"))
        if candidate_time is None:
            continue
        latest_time = (
            _timestamp(latest["source_event"].get("updated_at")) if latest else None
        )
        if latest_time is None or candidate_time > latest_time:
            latest = dict(entry)
    return latest


def evaluate_issue_fix_maintainer_correction_admission(
    correction: Mapping[str, Any],
    *,
    observation: Mapping[str, Any],
    source_events: Mapping[str, Any] | None,
    prior_entries: Sequence[Mapping[str, Any]] | None,
    prior_ledger_read: bool,
    recorded_at: str | None,
) -> dict[str, Any]:
    """Run the deterministic admission gates for one maintainer correction.

    Returns the admission record plus the ledger entries this run touches:
    the entry for this correction and any earlier revision it supersedes.
    """

    repo = str(observation.get("repo") or "")
    pr_ref = str(observation.get("pr_ref") or "")
    operation_id = issue_fix_maintainer_correction_operation_id(
        correction, repo=repo, pr_ref=pr_ref
    )
    source_event = correction.get("source_event")
    admission_basis = str(correction.get("admission_basis") or "host_asserted")
    prior = issue_fix_maintainer_correction_entries(
        {"maintainer_corrections": prior_entries}
    )
    existing_entry = next(
        (entry for entry in prior if entry.get("operation_id") == operation_id), None
    )
    gates: list[dict[str, Any]] = []
    reason: str | None = None
    verified_event: dict[str, Any] | None = None
    provider_events_status = (
        str(source_events.get("status") or "unavailable")
        if isinstance(source_events, Mapping)
        else "unavailable"
    )

    def record(name: str, passed: bool, failure_reason: str | None = None) -> None:
        nonlocal reason
        gates.append({"gate": name, "passed": passed})
        if not passed and reason is None:
            reason = failure_reason

    if isinstance(source_event, Mapping):
        observed = _observed_source_events(source_events, source_event)
        if not observed:
            record("source_event_observed", False, "source_event_not_observed")
        elif not any(
            _same_instant(event.get("updated_at"), source_event.get("updated_at"))
            for event in observed
        ):
            record("source_event_observed", False, "source_event_revision_mismatch")
        else:
            record("source_event_observed", True)
            provider_event = observed[0]
            verified_event = {
                **dict(source_event),
                "author_login": str(
                    provider_event.get("author_login") or source_event.get("author_login")
                ),
                "author_association": _upper_label(
                    provider_event.get("author_association"),
                    str(source_event.get("author_association") or "NONE"),
                ),
                "created_at": _normalise_event_timestamp(
                    provider_event.get("created_at") or source_event.get("created_at"),
                    field="source_event created_at",
                ),
            }
        effective_event = verified_event or dict(source_event)
        latest_prior = _latest_accepted_revision(prior, source_event=source_event)
        prior_time = (
            _timestamp(latest_prior["source_event"].get("updated_at"))
            if latest_prior
            else None
        )
        incoming_time = _timestamp(source_event.get("updated_at"))
        revision_relation = "first"
        if prior_time is not None and incoming_time is not None:
            if incoming_time < prior_time:
                revision_relation = "stale"
            elif incoming_time == prior_time:
                revision_relation = "replay"
            else:
                revision_relation = "newer"
        record("revision_not_stale", revision_relation != "stale", "stale_revision")
        record(
            "not_self_or_bot_author",
            not _is_bot_or_self_login(effective_event.get("author_login")),
            "self_or_bot_author",
        )
        record(
            "author_is_maintainer",
            _upper_label(effective_event.get("author_association"), "NONE")
            in MAINTAINER_ASSOCIATIONS,
            "author_not_maintainer",
        )
    else:
        effective_event = None
        latest_prior = None
        revision_relation = "replay" if existing_entry else "first"
    record(
        "pr_state_not_terminal",
        str(observation.get("state") or "") not in TERMINAL_PR_STATES,
        "terminal_pr_state",
    )

    accepted = reason is None
    replay = existing_entry is not None and (
        not accepted or existing_entry.get("disposition") in {"accepted", "superseded"}
    )
    if replay:
        entry = dict(existing_entry)
    else:
        entry = {
            "schema_version": ISSUE_FIX_MAINTAINER_CORRECTION_LEDGER_ENTRY_SCHEMA_VERSION,
            "operation_id": operation_id,
            "source_event": effective_event,
            "correction_fingerprint": correction.get("correction_fingerprint"),
            "correction_kind": correction.get("correction_kind"),
            "admission_basis": admission_basis,
            "disposition": "accepted" if accepted else "context_only",
            "reason": reason,
            "todo_id": None,
            "accepted_head_sha": observation.get("head_commit_ref") if accepted else None,
            "recorded_at": recorded_at,
            "superseded_by": None,
            "body_captured": False,
        }
    touched_entries = [entry]
    superseded_operation_id = None
    if (
        accepted
        and revision_relation == "newer"
        and latest_prior is not None
        and latest_prior.get("operation_id") != operation_id
    ):
        superseded_operation_id = str(latest_prior.get("operation_id"))
        touched_entries.append(
            {
                **latest_prior,
                "disposition": "superseded",
                "superseded_by": operation_id,
            }
        )
    return {
        "schema_version": ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION,
        "operation_id": operation_id,
        "admission_basis": admission_basis,
        "disposition": "accepted" if accepted else "context_only",
        "reason": reason,
        "host_summary_mismatch": bool(
            existing_entry is not None
            and correction.get("correction_fingerprint")
            != existing_entry.get("correction_fingerprint")
        ),
        "gates": gates,
        "revision_relation": revision_relation,
        "replay": replay,
        "supersedes_operation_id": superseded_operation_id,
        "provider_events_status": provider_events_status,
        "prior_ledger_read": bool(prior_ledger_read),
        "source_event": effective_event,
        "entries": touched_entries,
        "body_captured": False,
    }


def recheck_issue_fix_maintainer_correction_effect_gates(
    entry: Mapping[str, Any],
    *,
    observation: Mapping[str, Any],
    ledger_entries: Sequence[Mapping[str, Any]] | None,
    goal_stopped: bool,
    source_events: Mapping[str, Any] | None = None,
) -> str | None:
    """Re-run the effect-time gates for an accepted entry just before its Todo write."""

    source_event = entry.get("source_event")
    if isinstance(source_event, Mapping):
        observed = _observed_source_events(source_events, source_event)
        if not observed:
            return "source_event_not_observed"
        current_event = next(
            (
                event
                for event in observed
                if _same_instant(event.get("updated_at"), source_event.get("updated_at"))
            ),
            None,
        )
        if current_event is None:
            return "source_event_revision_mismatch"
        latest = _latest_accepted_revision(
            issue_fix_maintainer_correction_entries(
                {"maintainer_corrections": ledger_entries}
            ),
            source_event=source_event,
        )
        if latest is not None and latest.get("operation_id") != entry.get("operation_id"):
            latest_time = _timestamp(latest["source_event"].get("updated_at"))
            own_time = _timestamp(source_event.get("updated_at"))
            if latest_time is not None and own_time is not None and own_time < latest_time:
                return "stale_revision"
        if _is_bot_or_self_login(current_event.get("author_login")):
            return "self_or_bot_author"
        if (
            _upper_label(current_event.get("author_association"), "NONE")
            not in MAINTAINER_ASSOCIATIONS
        ):
            return "author_not_maintainer"
    if str(observation.get("state") or "") in TERMINAL_PR_STATES:
        return "terminal_pr_state"
    if goal_stopped:
        return "goal_not_active"
    return None


def maintainer_correction_transition(
    correction: Mapping[str, Any],
    *,
    pr_permalink: str,
) -> dict[str, Any]:
    """Build the transition preview dict for one admitted maintainer correction."""

    correction_kind = str(correction.get("correction_kind"))
    source_ref = str(correction.get("source_ref"))
    summary = str(correction.get("summary"))
    source_event = correction.get("source_event")
    # Todo creation dedupes identical active text, so distinct provider events
    # or revisions with the same wording must name the event they came from.
    event_suffix = (
        f" Source event: {source_event.get('kind')} {source_event.get('id')}"
        f" updated {source_event.get('updated_at')}."
        if isinstance(source_event, Mapping)
        else ""
    )
    if correction_kind == "actionable_patch":
        verification_plan = str(correction.get("verification_plan"))
        pr_update_path = str(correction.get("pr_update_path"))
        return build_issue_fix_transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_maintainer_correction_patch",
            priority="P0",
            task_class="advancement_task",
            reason="Maintainer correction is bounded and actionable; create one claimed patch successor.",
            text=(
                f"[P0] Apply maintainer correction for {pr_permalink} from {source_ref}: "
                f"{summary} Verify: {verification_plan} Update PR via: {pr_update_path}"
                f"{event_suffix}"
            ),
            material_change=True,
            required_write_scopes=["write", "publish"],
        )
    if correction_kind == "semantic_ambiguity":
        return build_issue_fix_transition_preview(
            decision="user_gate",
            action_kind="clarify_issue_fix_maintainer_correction",
            priority="P0",
            role="user",
            task_class="user_gate",
            reason="Maintainer correction changes or leaves the intended behavior ambiguous.",
            text=(
                f"[P0] Clarify maintainer correction for {pr_permalink} from {source_ref}: "
                f"{correction.get('user_question')} Context: {summary}{event_suffix}"
            ),
            material_change=True,
        )
    if correction_kind == "missing_authority":
        scopes = ", ".join(
            str(value) for value in correction.get("missing_authority_scopes") or []
        )
        return build_issue_fix_transition_preview(
            decision="user_gate",
            action_kind="grant_issue_fix_maintainer_correction_authority",
            priority="P0",
            role="user",
            task_class="user_gate",
            reason="The correction is understood but the required write authority is not active.",
            text=(
                f"[P0] Grant or decline [{scopes}] authority for maintainer correction "
                f"{source_ref} on {pr_permalink}: {summary}{event_suffix}"
            ),
            material_change=True,
            missing_authority_scopes=list(
                correction.get("missing_authority_scopes") or []
            ),
        )
    return build_issue_fix_transition_preview(
        decision="monitor_continuation",
        action_kind="issue_fix_maintainer_correction_unchanged_monitor",
        priority="P2",
        reason="Maintainer correction is unchanged; keep the monitor quiet and create no successor.",
    )


def _require_origin_registry(registry_path: Path) -> None:
    """Check the origin profile before looking up the request in its store."""
    try:
        profile = load_project_registry(registry_path).get("profile_id")
    except (ValueError, OSError) as exc:
        raise ValueError("origin registry is unreadable") from exc
    if profile != SOURCE_SESSION_PROFILE_ID:
        raise ValueError(f"origin registry requires {SOURCE_SESSION_PROFILE_ID}")


def _goal_stopped_for_correction(registry_path: Path | None, goal_id: str | None) -> bool:
    if registry_path is None or not goal_id:
        return False
    from ...agent_registry import load_goal_from_registry
    from ...control_plane.goals.activation import goal_is_stopped

    try:
        goal = load_goal_from_registry(registry_path, goal_id)
        if goal is None:
            return True
        return goal_is_stopped(goal)
    except ValueError:
        # An unreadable activation record cannot prove the Goal is active.
        return True


class _CorrectionTodoIntent(TypedDict, total=False):
    role: str
    text: str
    task_class: str
    action_kind: str
    required_write_scopes: list[str]


class _OriginRequestIdentity(TypedDict):
    goal_id: str
    agent_id: str
    goal_ref: NotRequired[dict[str, object]]


def _write_maintainer_correction_todo(
    *,
    registry_path: Path,
    goal_id: str,
    claimed_by: str,
    project: Path,
    operation_id: str,
    intent: _CorrectionTodoIntent,
) -> dict[str, object]:
    from ...control_plane.todos.mutation_api import add_goal_todo

    role = str(intent.get("role") or "agent")
    return add_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        role=role,
        text=str(intent.get("text") or ""),
        task_class=str(intent.get("task_class") or "advancement_task"),
        action_kind=str(intent.get("action_kind") or ""),
        required_write_scopes=(
            list(intent.get("required_write_scopes") or []) if role == "agent" else None
        ),
        claimed_by=claimed_by if role == "agent" else None,
        agent_id=claimed_by if role == "user" else None,
        project=project,
        operation_id=operation_id,
    )


def _maintainer_correction_todo_write_receipt(
    todo_write: Mapping[str, object], *, operation_id: str
) -> dict[str, object]:
    todo_changed = bool(
        todo_write.get("added")
        or todo_write.get("metadata_updated")
        or todo_write.get("status_changed")
    )
    created_todo = todo_write.get("todo")
    created_todo = created_todo if isinstance(created_todo, dict) else {}
    return {
        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
        "write_performed": todo_changed,
        "already_exists": bool(todo_write.get("already_exists")),
        "todo_id": todo_write.get("todo_id"),
        "operation_id": operation_id,
        "role": todo_write.get("role"),
        "claimed_by": created_todo.get("claimed_by"),
        "blocks_agent": created_todo.get("blocks_agent"),
        "path_recorded": False,
    }


def _correction_todo_readback_receipt(
    *, skip_reason: str, operation_id: str | None = None, todo_id: str | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
        "write_performed": False,
        "already_exists": bool(todo_id),
        "skip_reason": skip_reason,
        "path_recorded": False,
    }
    if operation_id is not None:
        receipt["operation_id"] = operation_id
    if todo_id is not None:
        receipt["todo_id"] = todo_id
    return receipt


def _resolve_origin_request_identity(runtime_root: Path, request_id: str) -> _OriginRequestIdentity:
    paths = list((runtime_root / ".local/manager-context/entries").glob(f"*/{request_id}.json"))
    if not paths:
        raise ValueError("origin_request_not_found")
    if len(paths) != 1:
        raise ValueError("origin_request_ambiguous")
    try:
        entry = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("origin_request_not_found") from exc
    if not isinstance(entry, dict):
        raise ValueError("origin_request_not_found")
    if isinstance(entry.get("recipient"), dict):
        raise ValueError("origin_request_unsupported_target")
    goal_id = entry.get("goal_id")
    agent_id = entry.get("agent_id")
    if not isinstance(goal_id, str) or not goal_id or not isinstance(agent_id, str) or not agent_id:
        raise ValueError("origin_request_unsupported_target")
    identity: _OriginRequestIdentity = {"goal_id": goal_id, "agent_id": agent_id}
    if isinstance(entry.get("goal_ref"), dict):
        identity["goal_ref"] = entry["goal_ref"]
    return identity


def _settle_maintainer_correction_entries(
    *,
    payload: dict[str, object],
    ledger_path: Path,
    registry_path: Path,
    goal_id: str,
    claimed_by: str,
    project: Path,
    source_events: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Write Todos for accepted ledger entries that still lack a todo_id.

    Each accepted entry stores its own `todo_intent`, so an entry left pending
    by an interrupted run is retried under its original operation id on any
    later run. Every candidate is re-gated against the ledger as it stands
    now, the same observation, and the Goal activation state.
    """

    observation = payload.get("observation")
    observation = observation if isinstance(observation, dict) else {}
    admission = payload.get("maintainer_correction_admission")
    admission = admission if isinstance(admission, dict) else {}
    current_operation_id = str(admission.get("operation_id") or "")
    goal_stopped = _goal_stopped_for_correction(registry_path, goal_id)
    repo = str(observation.get("repo") or "")
    pr_ref = str(observation.get("pr_ref") or "")

    row = read_issue_fix_pr_lifecycle_ledger_row(ledger_path, repo=repo, pr_ref=pr_ref)
    ledger_entries = issue_fix_maintainer_correction_entries(row)
    if source_events is None:
        pending_source_events = payload.pop("_maintainer_correction_source_events", None)
        source_events = pending_source_events if isinstance(pending_source_events, dict) else None
    origin = (row or {}).get("origin_request")
    view = None
    origin_refusal = None
    if origin:
        from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
        from ...control_plane.collaboration.request_control_source import execute_request_control

        try:
            identity = _resolve_origin_request_identity(Path(origin["runtime_root"]), origin["request_id"])
            if any(identity[key] != origin[key] for key in ("goal_id", "agent_id")):
                raise ValueError("origin request identity changed")
            with collaboration_goal_scope(Path(origin["registry_path"]), goal_id=identity["goal_id"],
                    agents=(), require_active=False) as scope:
                if scope.profile_id != SOURCE_SESSION_PROFILE_ID:
                    raise ValueError("origin registry profile changed")
                goal_ref = identity.get("goal_ref") or scope.current_goal_ref or {"goal_id": identity["goal_id"]}
            if origin.get("goal_ref") and goal_ref != origin["goal_ref"]:
                raise ValueError("origin request identity changed")
            result = execute_request_control(registry_path=Path(origin["registry_path"]),
                runtime_root=Path(origin["runtime_root"]), goal_ref=goal_ref,
                receiver_agent_id=identity["agent_id"], request_id=origin["request_id"],
                actor_agent_id=identity["agent_id"], operation="inspect", execute=False)
            if not result.get("ok"):
                raise ValueError("request inspection failed")
            view = result["view"]
            if not view.get("control_state"):
                raise ValueError("request control state unavailable")
            if view["control_state"] in {"cancelled", "cancellation_pending"}:
                origin_refusal = "origin_request_cancelled"
        except (OSError, ValueError, KeyError):
            origin_refusal = "origin_request_unreadable"
    settlements: list[dict[str, object]] = []
    current_todo_write: dict[str, object] | None = None
    changed_entries: list[dict[str, object]] = []
    for entry in ledger_entries:
        if origin and entry.get("disposition") == "accepted" and "accepted_head_sha" not in entry:
            entry["accepted_head_sha"] = observation.get("head_commit_ref")
            changed_entries.append(dict(entry))
        operation_id = str(entry.get("operation_id") or "")
        is_current = operation_id == current_operation_id
        intent = entry.get("todo_intent")
        if (
            entry.get("disposition") != "accepted"
            or not isinstance(intent, dict)
            or (entry.get("todo_id") and not is_current)
        ):
            continue
        refusal = recheck_issue_fix_maintainer_correction_effect_gates(
            entry,
            observation=observation,
            ledger_entries=ledger_entries,
            goal_stopped=goal_stopped,
            source_events=source_events,
        )
        refusal = origin_refusal or refusal
        if refusal is not None and not entry.get("todo_id"):
            if refusal in _SOURCE_EVENT_REFUSALS:
                settlements.append(
                    {"operation_id": operation_id, "status": "refused", "reason": refusal}
                )
                if is_current:
                    current_todo_write = _correction_todo_readback_receipt(
                        skip_reason=refusal, operation_id=operation_id,
                    )
                continue
            if is_current and origin_refusal:
                admission.update(disposition="context_only", reason=refusal)
            changed_entries.append(
                {**entry, "disposition": "context_only", "reason": refusal,
                 "effect_recheck": "refused"}
            )
            settlements.append(
                {"operation_id": operation_id, "status": "refused", "reason": refusal}
            )
            if is_current:
                current_todo_write = _correction_todo_readback_receipt(
                    skip_reason=refusal, operation_id=operation_id,
                )
            continue
        if refusal is not None:
            # The Todo already exists for this revision; a replay only reads it
            # back and leaves its lifecycle to the Todo owner.
            settlements.append(
                {"operation_id": operation_id, "status": "settled_earlier", "reason": refusal}
            )
            if is_current:
                current_todo_write = _correction_todo_readback_receipt(
                    skip_reason=refusal, operation_id=operation_id,
                    todo_id=entry.get("todo_id"),
                )
            continue
        if not entry.get("todo_id") and os.environ.get(MAINTAINER_CORRECTION_FAULT_ENV) == "1":
            raise RuntimeError(
                "maintainer correction Todo write aborted by "
                f"{MAINTAINER_CORRECTION_FAULT_ENV}"
            )
        todo_write = _write_maintainer_correction_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            claimed_by=claimed_by,
            project=project,
            operation_id=operation_id,
            intent=intent,
        )
        receipt = _maintainer_correction_todo_write_receipt(
            todo_write, operation_id=operation_id
        )
        if entry.get("todo_id") != receipt["todo_id"]:
            changed_entries.append({**entry, "todo_id": receipt["todo_id"]})
        settlements.append(
            {
                "operation_id": operation_id,
                "status": "written" if receipt["write_performed"] else "already_exists",
                "todo_id": receipt["todo_id"],
            }
        )
        if is_current:
            current_todo_write = receipt
    if current_todo_write is None and current_operation_id:
        current_todo_write = _correction_todo_readback_receipt(
            skip_reason=(
                str(admission.get("reason") or admission.get("disposition") or "")
                if admission.get("disposition") != "accepted"
                else "unchanged_monitor_quiet"
            ),
            operation_id=current_operation_id,
        )
    if changed_entries:
        payload["maintainer_corrections"] = changed_entries
        upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
        row = read_issue_fix_pr_lifecycle_ledger_row(
            ledger_path, repo=repo, pr_ref=pr_ref
        )
    entries = issue_fix_maintainer_correction_entries(row)
    payload["maintainer_corrections"] = entries
    if origin:
        from ...control_plane.todos.list_readback import list_goal_todos
        from ...control_plane.collaboration.inbox import reply_status
        from ..manager_context.roundtrip import report

        todos = {} if origin_refusal else {t["todo_id"]: t for t in list_goal_todos(
            registry_path=registry_path, goal_id=goal_id)["todos"]}
        head = str(observation.get("head_commit_ref") or "")
        for entry in entries:
            if (origin_refusal or not view or entry.get("disposition") != "accepted"
                    or not entry.get("todo_id")
                    or todos.get(entry["todo_id"], {}).get("status") != "done"):
                continue
            returns = entry.setdefault("returns", [])
            operation_id = entry["operation_id"]
            origin_root = Path(origin["runtime_root"])
            pending = entry.get("pending_return")
            pending_head = pending.get("head_sha") if (
                isinstance(pending, dict)
                and isinstance(pending.get("head_sha"), str)
                and isinstance(pending.get("text"), str)
                and isinstance(pending.get("request_revision"), int)
            ) else None
            # Finish the immutable publication before a newer observation can
            # replace it, and record the return against its original full head.
            for return_head in dict.fromkeys((pending_head, head)):
                if (not return_head or return_head == entry.get("accepted_head_sha")
                        or any(r["head_sha"] == return_head for r in returns)):
                    continue
                update_id = f"issue-fix-correction:{operation_id}:{return_head}"
                if return_head != pending_head:
                    event = entry.get("source_event") or {}
                    checks = observation.get("checks") or {}
                    text = (f"Maintainer correction {entry.get('correction_kind')} "
                        f"({event.get('kind')} {event.get('id')}) applied in "
                        f"{observation.get('permalink')} at head {return_head[:12]}; "
                        f"PR state {observation.get('state')}, checks {checks.get('aggregate')}; "
                        f"todo {entry['todo_id']} done.")
                    # Persist the entire publication before report(), including
                    # the first conclusion's absence of an update_id.
                    pending = {
                        "schema_version": "issue_fix_maintainer_correction_publication_v1",
                        "head_sha": return_head,
                        "update_id": update_id if any(
                            r["phase"] == "conclusion"
                            for r in reply_status(origin_root, origin)
                        ) else None,
                        "text": text,
                        "request_revision": view["revision"],
                        "phase": "conclusion",
                    }
                    entry["pending_return"] = pending
                    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
                result = report(origin_root, identity["goal_id"], identity["agent_id"],
                    origin["request_id"], str(pending.get("phase") or "conclusion"),
                    str(pending["text"]),
                    registry=Path(origin["registry_path"]),
                    update_id=pending.get("update_id"), workspace=project,
                    request_revision=pending["request_revision"],
                    caller_goal_ref=goal_ref)
                entry["pending_return"] = None
                returns.append({"update_id": update_id, "result_key": result.get("result_key", "conclusion"),
                    "enqueued_at": now_utc_iso(), "head_sha": return_head})
                upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
        statuses = {r.get("result_key", r["phase"]): r["status"]
            for r in reply_status(Path(origin["runtime_root"]), origin)}
        payload["return_delivery"] = [{"operation_id": e["operation_id"],
            "update_id": r["update_id"], "status": statuses.get(r["result_key"], "explicit_unverified")}
            for e in entries for r in e.get("returns", [])]
    return {
        "schema_version": "issue_fix_maintainer_correction_settlement_v0",
        "settlements": settlements,
        "goal_stopped": goal_stopped,
        "todo_write": current_todo_write,
        "path_recorded": False,
    }
