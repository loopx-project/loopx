from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

from ...control_plane.runtime.public_safety import public_safe_compact_text

from .github_public import (
    MAINTAINER_ASSOCIATIONS,
    fetch_github_pull_request_event_metadata,
)
from .metadata_preview import (
    normalise_github_issue_link_reference,
    normalise_github_issue_reference,
)


ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION = "issue_fix_pr_lifecycle_monitor_v0"
ISSUE_FIX_PR_GROUPED_MONITOR_PROJECTION_SCHEMA_VERSION = (
    "issue_fix_pr_grouped_monitor_projection_v1"
)
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
FAILING_CHECK_STATES = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "ERROR",
    "FAILURE",
    "FAILED",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}
PENDING_CHECK_STATES = {
    "EXPECTED",
    "IN_PROGRESS",
    "PENDING",
    "QUEUED",
    "REQUESTED",
    "WAITING",
}
PASSING_CHECK_STATES = {"NEUTRAL", "SKIPPED", "SUCCESS"}
BRANCH_REPLAN_MERGE_STATES = {"BEHIND", "DIRTY"}
_VERIFIED_REQUESTED_REPOSITORY_FIELD = "_loopx_verified_requested_repository"

_REVIEW_RESPONSE_QUERY = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviewThreads(first:100){
        totalCount
        pageInfo{hasNextPage}
        nodes{isResolved}
      }
      reviews(last:100){
        nodes{state submittedAt}
      }
      commits(last:1){
        nodes{commit{committedDate}}
      }
    }
  }
}
""".strip()


def _upper_label(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _compact_label(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return "_".join(text.replace("/", "_").replace("-", "_").split()).lower()


def _safe_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _safe_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _review_response_observation(
    provider_payload: Mapping[str, Any], *, review_decision: str
) -> dict[str, Any]:
    summary = provider_payload.get("reviewThreadSummary")
    thread_summary = summary if isinstance(summary, Mapping) else {}
    total = _safe_count(thread_summary.get("totalCount"))
    resolved = _safe_count(thread_summary.get("resolvedCount"))
    unresolved = _safe_count(thread_summary.get("unresolvedCount"))
    complete = thread_summary.get("complete") is True
    changes_requested_at = provider_payload.get("latestChangesRequestedAt")
    head_committed_at = provider_payload.get("headCommittedAt")
    requested_time = _timestamp(changes_requested_at)
    head_time = _timestamp(head_committed_at)
    addressed = bool(
        review_decision == "CHANGES_REQUESTED"
        and complete
        and total is not None
        and total > 0
        and resolved == total
        and unresolved == 0
        and requested_time is not None
        and head_time is not None
        and head_time > requested_time
    )
    if addressed:
        status = "changes_addressed_awaiting_rereview"
    elif review_decision == "CHANGES_REQUESTED":
        status = "changes_requested_unverified"
    else:
        status = "not_applicable"
    return {
        "schema_version": "issue_fix_pr_review_response_v0",
        "status": status,
        "metadata_status": str(
            provider_payload.get("reviewResponseMetadataStatus") or "not_fetched"
        ),
        "thread_count": total,
        "resolved_thread_count": resolved,
        "unresolved_thread_count": unresolved,
        "thread_page_complete": complete,
        "latest_changes_requested_at": changes_requested_at,
        "head_committed_at": head_committed_at,
        "raw_reviews_captured": False,
        "raw_thread_comments_captured": False,
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
    if _timestamp(text) is None:
        raise ValueError(
            f"maintainer correction {field} must be a timezone-aware ISO timestamp"
        )
    return text[:40]


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
                    "created_at": str(
                        item.get("created_at") or item.get("createdAt") or ""
                    )[:40]
                    or None,
                    "updated_at": str(updated_at or "")[:40] or None,
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


def _prior_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


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
    prior = _prior_entries(prior_entries)
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
        observed = [
            event
            for event in (
                source_events.get("events") if isinstance(source_events, Mapping) else []
            )
            if isinstance(event, Mapping)
            and event.get("kind") == source_event.get("kind")
            and str(event.get("id")) == str(source_event.get("id"))
        ]
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
                "created_at": str(
                    provider_event.get("created_at") or source_event.get("created_at")
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
    same_revision = existing_entry is not None and revision_relation == "replay"
    replay = bool(
        same_revision
        and existing_entry is not None
        and existing_entry.get("disposition") in {"accepted", "superseded"}
    )
    if replay and existing_entry is not None:
        entry = dict(existing_entry)
        if entry.get("disposition") == "accepted" and not accepted:
            # A later poll can still see terminal PR state for an earlier
            # accepted revision; keep the record and mark why it may no longer
            # produce work. Its Todo, if any, is left to the Todo lifecycle.
            entry["effect_recheck_reason"] = reason
    else:
        # A previously rejected revision may be re-admitted once its gate
        # input changes (for example provider events became observable);
        # an unchanged rejection keeps its original recording time.
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
            "recorded_at": (
                existing_entry.get("recorded_at") or recorded_at
                if same_revision and existing_entry is not None and not accepted
                else recorded_at
            ),
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
        "disposition": entry.get("disposition"),
        "reason": entry.get("reason") if not accepted else None,
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
) -> str | None:
    """Re-run the effect-time gates for an accepted entry just before its Todo write."""

    source_event = entry.get("source_event")
    if isinstance(source_event, Mapping):
        latest = _latest_accepted_revision(
            _prior_entries(ledger_entries), source_event=source_event
        )
        if latest is not None and latest.get("operation_id") != entry.get("operation_id"):
            latest_time = _timestamp(latest["source_event"].get("updated_at"))
            own_time = _timestamp(source_event.get("updated_at"))
            if latest_time is not None and own_time is not None and own_time < latest_time:
                return "stale_revision"
        if _is_bot_or_self_login(source_event.get("author_login")):
            return "self_or_bot_author"
        if (
            _upper_label(source_event.get("author_association"), "NONE")
            not in MAINTAINER_ASSOCIATIONS
        ):
            return "author_not_maintainer"
    if str(observation.get("state") or "") in TERMINAL_PR_STATES:
        return "terminal_pr_state"
    if goal_stopped:
        return "goal_not_active"
    return None

def _maintainer_correction_transition(
    correction: Mapping[str, Any],
    *,
    pr_permalink: str,
) -> dict[str, Any]:
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
        return _transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_maintainer_correction_patch",
            priority="P0",
            task_class="advancement_task",
            reason="Maintainer correction is bounded and actionable; create one claimed patch successor.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
            text=(
                f"[P0] Apply maintainer correction for {pr_permalink} from {source_ref}: "
                f"{summary} Verify: {verification_plan} Update PR via: {pr_update_path}"
                f"{event_suffix}"
            ),
        ) | {
            "terminal_state_precedence": False,
            "material_change": True,
            "required_write_scopes": ["write", "publish"],
        }
    if correction_kind == "semantic_ambiguity":
        return _transition_preview(
            decision="user_gate",
            action_kind="clarify_issue_fix_maintainer_correction",
            priority="P0",
            role="user",
            task_class="user_gate",
            reason="Maintainer correction changes or leaves the intended behavior ambiguous.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
            text=(
                f"[P0] Clarify maintainer correction for {pr_permalink} from {source_ref}: "
                f"{correction.get('user_question')} Context: {summary}{event_suffix}"
            ),
        ) | {"terminal_state_precedence": False, "material_change": True}
    if correction_kind == "missing_authority":
        scopes = ", ".join(
            str(value) for value in correction.get("missing_authority_scopes") or []
        )
        return _transition_preview(
            decision="user_gate",
            action_kind="grant_issue_fix_maintainer_correction_authority",
            priority="P0",
            role="user",
            task_class="user_gate",
            reason="The correction is understood but the required write authority is not active.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
            text=(
                f"[P0] Grant or decline [{scopes}] authority for maintainer correction "
                f"{source_ref} on {pr_permalink}: {summary}{event_suffix}"
            ),
        ) | {
            "terminal_state_precedence": False,
            "material_change": True,
            "missing_authority_scopes": list(
                correction.get("missing_authority_scopes") or []
            ),
        }
    return _transition_preview(
        decision="monitor_continuation",
        action_kind="issue_fix_maintainer_correction_unchanged_monitor",
        priority="P2",
        reason="Maintainer correction is unchanged; keep the monitor quiet and create no successor.",
        depends_on=["issue_fix_pr_lifecycle_monitor"],
    ) | {"terminal_state_precedence": False, "material_change": False}


def _normalise_check_item(item: Mapping[str, Any]) -> str:
    state = (
        item.get("conclusion")
        or item.get("state")
        or item.get("status")
        or item.get("workflowState")
    )
    status = _upper_label(state)
    if status in {"COMPLETED", "COMPLETED_SUCCESSFULLY"}:
        conclusion = _upper_label(item.get("conclusion"), "")
        return conclusion or "SUCCESS"
    return status


def _check_rollup(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_checks = payload.get("statusCheckRollup")
    failing = 0
    pending = 0
    passing = 0
    unknown = 0
    names: list[str] = []
    if isinstance(raw_checks, Sequence) and not isinstance(raw_checks, (str, bytes)):
        for item in raw_checks:
            if not isinstance(item, Mapping):
                unknown += 1
                continue
            state = _normalise_check_item(item)
            name = str(item.get("name") or item.get("context") or "").strip()
            if name:
                names.append(name[:80])
            if state in FAILING_CHECK_STATES:
                failing += 1
            elif state in PENDING_CHECK_STATES:
                pending += 1
            elif state in PASSING_CHECK_STATES:
                passing += 1
            else:
                unknown += 1
    else:
        failing = _safe_count(payload.get("failing_checks")) or 0
        pending = _safe_count(payload.get("pending_checks")) or 0
        passing = _safe_count(payload.get("passing_checks")) or 0
        unknown = _safe_count(payload.get("unknown_checks")) or 0

    total = failing + pending + passing + unknown
    if failing:
        aggregate = "FAILING"
    elif pending:
        aggregate = "PENDING"
    elif unknown and not passing:
        aggregate = "UNKNOWN"
    elif total:
        aggregate = "PASSING"
    else:
        aggregate = "NO_CHECKS"
    return {
        "schema_version": "issue_fix_pr_check_rollup_v0",
        "aggregate": aggregate,
        "failing_count": failing,
        "pending_count": pending,
        "passing_count": passing,
        "unknown_count": unknown,
        "check_name_sample": names[:5],
        "raw_status_captured": False,
        "log_output_captured": False,
    }


def _transition_preview(
    *,
    decision: str,
    action_kind: str,
    priority: str,
    reason: str,
    depends_on: Sequence[str],
    role: str = "agent",
    task_class: str = "continuous_monitor",
    text: str | None = None,
) -> dict[str, Any]:
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
    }


def _build_observation(
    *,
    repo: str,
    pr_ref: str,
    issue_ref: str | None,
    reference: Mapping[str, Any],
    provider_payload: Mapping[str, Any],
) -> dict[str, Any]:
    state = _upper_label(provider_payload.get("state"), "OPEN")
    if state == "OPEN":
        merged_at = provider_payload.get("mergedAt") or provider_payload.get(
            "merged_at"
        )
        merged = provider_payload.get("merged")
        if merged_at or merged is True:
            state = "MERGED"
    review_decision = _upper_label(provider_payload.get("reviewDecision"))
    merge_state = _upper_label(provider_payload.get("mergeStateStatus"))
    linked_issue_ref = (
        normalise_github_issue_link_reference(issue_ref) if issue_ref else ""
    )
    raw_commits = provider_payload.get("commits")
    commit_count = _safe_count(provider_payload.get("commitCount"))
    if (
        commit_count is None
        and isinstance(raw_commits, Sequence)
        and not isinstance(raw_commits, (str, bytes))
    ):
        commit_count = len(raw_commits)
    return {
        "schema_version": "issue_fix_pr_lifecycle_observation_v0",
        "repo": repo,
        "pr_ref": pr_ref,
        "issue_ref": linked_issue_ref or None,
        "kind": "pull_request",
        "number": reference.get("number"),
        "state": state,
        "review_decision": review_decision,
        "merge_state_status": merge_state,
        "is_draft": _safe_bool(provider_payload.get("isDraft")),
        "created_at": provider_payload.get("createdAt")
        or provider_payload.get("created_at"),
        "updated_at": provider_payload.get("updatedAt")
        or provider_payload.get("updated_at"),
        "merged_at": provider_payload.get("mergedAt")
        or provider_payload.get("merged_at"),
        "closed_at": provider_payload.get("closedAt")
        or provider_payload.get("closed_at"),
        "permalink": reference.get("permalink") or provider_payload.get("url"),
        "head_commit_ref": provider_payload.get("headRefOid")
        or provider_payload.get("head_commit_ref"),
        "commit_count": commit_count,
        "checks": _check_rollup(provider_payload),
        "review_response": _review_response_observation(
            provider_payload, review_decision=review_decision
        ),
        "body_captured": False,
        "comment_bodies_captured": False,
        "timeline_captured": False,
        "log_output_captured": False,
        "response_payload_captured": False,
    }


def _first_push_ci_evidence(
    observation: Mapping[str, Any], *, observed_at: str | None
) -> dict[str, Any] | None:
    checks = observation.get("checks")
    rollup = checks if isinstance(checks, Mapping) else {}
    aggregate = str(rollup.get("aggregate") or "").upper()
    if observation.get("commit_count") != 1 or aggregate not in {
        "PASSING",
        "FAILING",
    }:
        return None
    evidence = {
        "schema_version": "issue_fix_first_push_ci_evidence_v0",
        "pr_ref": observation.get("pr_ref"),
        "status": aggregate,
        "observed_at": observed_at,
        "source": "single_commit_terminal_check_rollup",
        "raw_check_status_captured": False,
        "check_logs_captured": False,
    }
    head_commit_ref = str(observation.get("head_commit_ref") or "").strip()
    if head_commit_ref:
        evidence["head_commit_ref"] = head_commit_ref
    return evidence


def _observation_fingerprint(observation: Mapping[str, Any]) -> str:
    text = json.dumps(observation, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def issue_fix_grouped_monitor_identity(
    *, repository: str, state_bucket: str
) -> tuple[str, str]:
    """Return stable repository-scoped monitor target and action identities."""

    repository_key = repository.casefold().replace("/", "--")
    bucket_key = state_bucket.replace("_", "-")
    return (
        f"github-pr-state-{repository_key}-{bucket_key}",
        f"issue_fix_pr_state_{state_bucket}_monitor",
    )


def _grouped_monitor_projection(
    observation: Mapping[str, Any], transition: Mapping[str, Any]
) -> dict[str, Any]:
    state = str(observation.get("state") or "UNKNOWN")
    review_decision = str(observation.get("review_decision") or "UNKNOWN")
    merge_state = str(observation.get("merge_state_status") or "UNKNOWN")
    checks = observation.get("checks")
    check_rollup = checks if isinstance(checks, Mapping) else {}
    review_response = observation.get("review_response")
    review_response_status = (
        str(review_response.get("status") or "")
        if isinstance(review_response, Mapping)
        else ""
    )

    if state in TERMINAL_PR_STATES:
        state_bucket = "terminal"
    elif int(check_rollup.get("failing_count") or 0):
        state_bucket = "checks_failed"
    elif review_decision == "CHANGES_REQUESTED":
        state_bucket = (
            "changes_addressed_awaiting_rereview"
            if review_response_status == "changes_addressed_awaiting_rereview"
            else "changes_requested"
        )
    elif merge_state in BRANCH_REPLAN_MERGE_STATES:
        state_bucket = "branch_blocked"
    elif bool(observation.get("is_draft")):
        state_bucket = "draft"
    elif int(check_rollup.get("pending_count") or 0):
        state_bucket = "checks_pending"
    elif review_decision == "APPROVED":
        state_bucket = "ready_to_merge"
    else:
        state_bucket = "review_required"

    terminal = state_bucket == "terminal"
    member_key = f"{observation.get('repo')}#{observation.get('number')}"
    repository = str(observation.get("repo") or "unknown")
    target_key, action_kind = issue_fix_grouped_monitor_identity(
        repository=repository,
        state_bucket=state_bucket,
    )
    return {
        "schema_version": ISSUE_FIX_PR_GROUPED_MONITOR_PROJECTION_SCHEMA_VERSION,
        "scope": "repository_pr_lifecycle_state",
        "repository": repository,
        "state_bucket": state_bucket,
        "target_key": None if terminal else target_key,
        "action_kind": None if terminal else action_kind,
        "member_key": member_key,
        "member_operation": "remove" if terminal else "upsert",
        "materialize_nonempty_bucket_monitor": not terminal,
        "creates_per_pr_continuous_monitor_todo": False,
        "per_pr_material_action": "one_shot_advancement_todo",
        "external_notification_granularity": "one_pr_per_message",
        "empty_bucket_policy": "complete_monitor",
        "transition_decision": transition.get("decision"),
        "todo_write_performed": False,
    }


def _decide_transition(observation: Mapping[str, Any]) -> dict[str, Any]:
    state = str(observation.get("state") or "UNKNOWN")
    review_decision = str(observation.get("review_decision") or "UNKNOWN")
    merge_state = str(observation.get("merge_state_status") or "UNKNOWN")
    checks = observation.get("checks")
    check_rollup = checks if isinstance(checks, Mapping) else {}
    failing_checks = int(check_rollup.get("failing_count") or 0)
    pending_checks = int(check_rollup.get("pending_count") or 0)
    review_response = observation.get("review_response")
    review_response_status = (
        str(review_response.get("status") or "")
        if isinstance(review_response, Mapping)
        else ""
    )
    is_draft = bool(observation.get("is_draft"))

    if state == "MERGED":
        return _transition_preview(
            decision="no_followup",
            action_kind="issue_fix_pr_merged_no_followup",
            priority="P1",
            task_class="terminal_transition",
            reason=(
                "PR is merged; close the monitor with no follow-up even if stale "
                "review metadata still says review is required."
            ),
            depends_on=["issue_fix_pr_lifecycle_monitor"],
        ) | {
            "terminal_state_precedence": True,
            "material_change": True,
        }
    if state == "CLOSED":
        return _transition_preview(
            decision="no_followup",
            action_kind="issue_fix_pr_closed_no_followup",
            priority="P1",
            task_class="terminal_transition",
            reason="PR is closed without an open continuation; close the monitor.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
        ) | {
            "terminal_state_precedence": True,
            "material_change": True,
        }
    if failing_checks:
        return _transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_ci_failure_replan",
            priority="P0",
            task_class="advancement_task",
            reason="PR checks are failing; inspect public check summary and plan a fix.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
        ) | {
            "terminal_state_precedence": False,
            "material_change": True,
        }
    if review_decision == "CHANGES_REQUESTED":
        if review_response_status == "changes_addressed_awaiting_rereview":
            return _transition_preview(
                decision="monitor_continuation",
                action_kind="issue_fix_review_changes_addressed_monitor",
                priority="P2",
                reason=(
                    "Review changes have a newer repair commit and every fetched "
                    "review thread is resolved; wait for reviewer re-approval."
                ),
                depends_on=["issue_fix_pr_lifecycle_monitor"],
            ) | {
                "terminal_state_precedence": False,
                "material_change": False,
            }
        return _transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_review_changes_replan",
            priority="P0",
            task_class="advancement_task",
            reason="Maintainer review requested changes; derive a follow-up patch or blocker.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
        ) | {
            "terminal_state_precedence": False,
            "material_change": True,
        }
    if merge_state in BRANCH_REPLAN_MERGE_STATES:
        return _transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_branch_or_merge_blocker_replan",
            priority="P1",
            task_class="advancement_task",
            reason="PR branch is stale or conflicted; decide rebase, blocker note, or no-follow-up.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
        ) | {
            "terminal_state_precedence": False,
            "material_change": True,
        }
    if is_draft:
        return _transition_preview(
            decision="user_gate",
            action_kind="approve_pr_ready_for_review_or_keep_draft",
            priority="P1",
            role="user",
            task_class="user_gate",
            reason="PR is still draft; owner must approve marking ready or keep monitoring.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
        ) | {
            "terminal_state_precedence": False,
            "material_change": True,
        }
    if pending_checks:
        return _transition_preview(
            decision="monitor_continuation",
            action_kind="issue_fix_pr_checks_pending_monitor",
            priority="P2",
            reason="PR checks are still pending; continue monitor without spawning work.",
            depends_on=["issue_fix_pr_lifecycle_monitor"],
        ) | {
            "terminal_state_precedence": False,
            "material_change": False,
        }
    return _transition_preview(
        decision="monitor_continuation",
        action_kind="issue_fix_pr_wait_for_review_or_merge_monitor",
        priority="P2",
        reason="No actionable PR state change; keep watching for CI, review, merge, or maintainer signal.",
        depends_on=["issue_fix_pr_lifecycle_monitor"],
    ) | {
        "terminal_state_precedence": False,
        "material_change": False,
    }


def _fetch_github_review_response_metadata(
    *, owner: str, repo_name: str, number: int, timeout_seconds: int
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={repo_name}",
                "-F",
                f"number={number}",
                "-f",
                f"query={_REVIEW_RESPONSE_QUERY}",
            ],
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"reviewResponseMetadataStatus": "unavailable"}
    if result.returncode != 0:
        return {"reviewResponseMetadataStatus": "unavailable"}
    try:
        response = json.loads(result.stdout)
        pull_request = response["data"]["repository"]["pullRequest"]
        threads = pull_request["reviewThreads"]
        thread_nodes = threads["nodes"]
        reviews = pull_request["reviews"]["nodes"]
        commit_nodes = pull_request["commits"]["nodes"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"reviewResponseMetadataStatus": "unavailable"}
    if not all(isinstance(items, list) for items in (thread_nodes, reviews, commit_nodes)):
        return {"reviewResponseMetadataStatus": "unavailable"}

    total = _safe_count(threads.get("totalCount"))
    page_info = threads.get("pageInfo")
    page_complete = bool(
        isinstance(page_info, Mapping)
        and page_info.get("hasNextPage") is False
        and total == len(thread_nodes)
    )
    resolved = sum(
        1
        for item in thread_nodes
        if isinstance(item, Mapping) and item.get("isResolved") is True
    )
    changes_requested_at = max(
        (
            str(item.get("submittedAt"))
            for item in reviews
            if isinstance(item, Mapping)
            and _upper_label(item.get("state"), "") == "CHANGES_REQUESTED"
            and _timestamp(item.get("submittedAt")) is not None
        ),
        default="",
    )
    head_committed_at = ""
    if commit_nodes:
        head = commit_nodes[-1]
        commit = head.get("commit") if isinstance(head, Mapping) else None
        if isinstance(commit, Mapping) and _timestamp(commit.get("committedDate")):
            head_committed_at = str(commit.get("committedDate"))
    return {
        "reviewResponseMetadataStatus": "available",
        "reviewThreadSummary": {
            "totalCount": total,
            "resolvedCount": resolved,
            "unresolvedCount": (
                total - resolved if total is not None and page_complete else None
            ),
            "complete": page_complete,
        },
        "latestChangesRequestedAt": changes_requested_at or None,
        "headCommittedAt": head_committed_at or None,
    }


def fetch_github_pr_lifecycle_payload(
    reference: Mapping[str, Any],
    *,
    timeout_seconds: int = 10,
    compact: bool = False,
) -> dict[str, Any]:
    repo = str(reference.get("repo") or "")
    number = reference.get("number")
    if "/" not in repo or not isinstance(number, int):
        raise ValueError("--fetch-metadata requires a numeric GitHub pull request URL")
    owner, repo_name = repo.split("/", 1)
    repo_slug = f"{quote(owner, safe='')}/{quote(repo_name, safe='')}"
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo_slug,
            "--json",
            ",".join(
                ["state", "url", "mergedAt", "closedAt"] if compact else [
                    "baseRefName",
                    "closingIssuesReferences",
                    "closedAt",
                    "commits",
                    "createdAt",
                    "headRefName",
                    "headRefOid",
                    "isDraft",
                    "mergeStateStatus",
                    "mergedAt",
                    "reviewDecision",
                    "state",
                    "statusCheckRollup",
                    "updatedAt",
                    "url",
                ]
            ),
        ],
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "GitHub PR lifecycle fetch failed; install/authenticate gh or use --metadata-json"
        )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("GitHub PR lifecycle fetch must return a JSON object")
    provider_reference = normalise_github_issue_reference(
        repo=repo,
        issue_ref=f"pull_{number}",
        url=str(payload.get("url") or ""),
    )
    if (
        provider_reference.get("kind") != "pull_request"
        or provider_reference.get("number") != number
    ):
        raise ValueError("GitHub PR lifecycle provider identity mismatch")
    if str(provider_reference["repo"]).lower() != repo.lower():
        redirect = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "nameWithOwner"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        if redirect.returncode != 0:
            raise RuntimeError("GitHub repository redirect verification failed")
        redirect_payload = json.loads(redirect.stdout)
        canonical_repo = (
            str(redirect_payload.get("nameWithOwner") or "").strip().lower()
            if isinstance(redirect_payload, dict)
            else ""
        )
        if canonical_repo != str(provider_reference["repo"]).lower():
            raise ValueError("GitHub PR lifecycle provider identity mismatch")
        payload[_VERIFIED_REQUESTED_REPOSITORY_FIELD] = canonical_repo
    if _upper_label(payload.get("reviewDecision")) == "CHANGES_REQUESTED":
        payload.update(
            _fetch_github_review_response_metadata(
                owner=owner,
                repo_name=repo_name,
                number=number,
                timeout_seconds=timeout_seconds,
            )
        )
    return payload


def validate_github_pr_provider_identity(
    reference: Mapping[str, Any], provider_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate one exact PR response, including a provider-verified repo rename."""

    expected_repo = str(reference.get("repo") or "").strip().lower()
    expected_number = reference.get("number")
    provider_reference = normalise_github_issue_reference(
        repo=expected_repo,
        issue_ref=f"pull_{expected_number}",
        url=str(provider_payload.get("url") or ""),
    )
    if (
        provider_reference.get("kind") != "pull_request"
        or provider_reference.get("number") != expected_number
    ):
        raise ValueError("PR dependency provider identity mismatch")
    provider_repo = str(provider_reference.get("repo") or "").strip().lower()
    if provider_repo != expected_repo and str(
        provider_payload.get(_VERIFIED_REQUESTED_REPOSITORY_FIELD) or ""
    ).strip().lower() != provider_repo:
        raise ValueError("PR dependency provider identity mismatch")
    return provider_reference


def _fetch_pr_source_events(
    reference: Mapping[str, Any], *, timeout_seconds: int
) -> dict[str, Any]:
    repo = str(reference.get("repo") or "")
    number = reference.get("number")
    if "/" not in repo or not isinstance(number, int):
        return {"status": "unavailable", "events": [], "body_captured": False}
    owner, repo_name = repo.split("/", 1)
    try:
        payload = fetch_github_pull_request_event_metadata(
            owner=owner,
            repo=repo_name,
            number=number,
            timeout_seconds=timeout_seconds,
        )
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "events": [], "body_captured": False}
    return normalise_issue_fix_pr_source_events(payload)


def build_issue_fix_pr_lifecycle_monitor_packet(
    *,
    repo: str = "public_repo_fixture",
    pr_ref: str = "pull_123_public_metadata_fixture",
    issue_ref: str | None = None,
    url: str | None = None,
    provider_payload: Mapping[str, Any] | None = None,
    fetch_metadata: bool = False,
    fetch_timeout_seconds: int = 10,
    maintainer_correction_input: Mapping[str, Any] | None = None,
    prior_maintainer_corrections: Sequence[Mapping[str, Any]] | None = None,
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    reference = normalise_github_issue_reference(repo=repo, issue_ref=pr_ref, url=url)
    if not url:
        reference = {**reference, "kind": "pull_request"}
    if reference.get("kind") != "pull_request":
        raise ValueError("PR lifecycle projection requires a GitHub /pull/<number> URL")
    payload = dict(provider_payload or {})
    if fetch_metadata:
        if provider_payload:
            raise ValueError("--fetch-metadata cannot be combined with --metadata-json")
        payload = fetch_github_pr_lifecycle_payload(
            reference,
            timeout_seconds=fetch_timeout_seconds,
        )
    canonical_reference = reference
    provider_url = payload.get("url")
    if isinstance(provider_url, str) and provider_url.strip():
        provider_reference = normalise_github_issue_reference(
            repo=repo,
            issue_ref=pr_ref,
            url=provider_url,
        )
        if (
            provider_reference.get("kind") == "pull_request"
            and provider_reference.get("number") == reference.get("number")
        ):
            canonical_reference = provider_reference
    if not issue_ref:
        raw_linked_issues = payload.get("closingIssuesReferences") or payload.get(
            "closing_issues_references"
        )
        if isinstance(raw_linked_issues, Sequence) and not isinstance(
            raw_linked_issues, (str, bytes)
        ):
            for item in raw_linked_issues:
                number = item.get("number") if isinstance(item, Mapping) else None
                if isinstance(number, int):
                    issue_ref = f"issues_{number}"
                    break
    observation = _build_observation(
        repo=str(canonical_reference["repo"]),
        pr_ref=str(canonical_reference["issue_ref"]),
        issue_ref=issue_ref,
        reference=canonical_reference,
        provider_payload=payload,
    )
    requested_repo = str(reference["repo"])
    canonical_repo = str(canonical_reference["repo"])
    if canonical_repo != requested_repo:
        observation["requested_repo"] = requested_repo
        observation["repository_aliases"] = [requested_repo]
    transition = _decide_transition(observation)
    maintainer_correction = (
        normalise_issue_fix_maintainer_correction_input(maintainer_correction_input)
        if maintainer_correction_input is not None
        else None
    )
    maintainer_correction_admission: dict[str, Any] | None = None
    source_events: dict[str, Any] | None = None
    if maintainer_correction is not None:
        if isinstance(maintainer_correction.get("source_event"), Mapping):
            if fetch_metadata:
                source_events = _fetch_pr_source_events(
                    reference, timeout_seconds=fetch_timeout_seconds
                )
            else:
                source_events = normalise_issue_fix_pr_source_events(payload)
        maintainer_correction_admission = (
            evaluate_issue_fix_maintainer_correction_admission(
                maintainer_correction,
                observation=observation,
                source_events=source_events,
                prior_entries=prior_maintainer_corrections,
                prior_ledger_read=prior_maintainer_corrections is not None,
                recorded_at=generated_at,
            )
        )
        if (
            maintainer_correction_admission["disposition"] == "accepted"
            and observation["state"] not in TERMINAL_PR_STATES
        ):
            transition = _maintainer_correction_transition(
                maintainer_correction,
                pr_permalink=str(
                    observation.get("permalink")
                    or reference.get("permalink")
                    or observation["pr_ref"]
                ),
            )
            if transition["decision"] in {"runnable_successor", "user_gate"}:
                # The entry carries its own Todo intent so a later run can
                # finish an interrupted write without the original input.
                maintainer_correction_admission["entries"][0]["todo_intent"] = {
                    "role": transition["role"],
                    "text": transition["text"],
                    "task_class": transition["task_class"],
                    "action_kind": transition["action_kind"],
                    "required_write_scopes": (
                        list(transition.get("required_write_scopes") or [])
                        if transition["role"] == "agent"
                        else None
                    ),
                }
    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION,
        "mode": "issue-fix-pr-lifecycle",
        "generated_at": generated_at,
        "observation": observation,
        "observation_fingerprint": _observation_fingerprint(observation),
        "maintainer_correction": maintainer_correction,
        "maintainer_correction_fingerprint": (
            maintainer_correction.get("correction_fingerprint")
            if maintainer_correction is not None
            else None
        ),
        "transition": transition,        "grouped_monitor_projection": _grouped_monitor_projection(
            observation, transition
        ),
        "first_screen": {
            "waiting_on": transition["role"],
            "user_action_required": transition["role"] == "user",
            "agent_can_continue": transition["decision"] != "user_gate",
            "current_pr_actionable": transition["decision"] == "runnable_successor",
            "immediate_repoll_required": False,
            "next_safe_action": transition["reason"],
        },
        "writeback_contract": {
            "schema_version": "issue_fix_pr_lifecycle_writeback_contract_v0",
            "allowed_decisions": [
                "runnable_successor",
                "monitor_continuation",
                "user_gate",
                "no_followup",
            ],
            "monitor_quiet_skip_allowed": transition["material_change"] is False,
            "successor_or_terminal_required": transition["material_change"] is True,
            "external_comment_performed": False,
            "external_pr_created": False,
            "merge_performed": False,
            "todo_write_performed": False,
        },
        "domain_state_projection": {
            "schema_version": "issue_fix_pr_lifecycle_domain_state_projection_v0",
            "domain_pack": "issue_fix",
            "default_filename": "pr-lifecycle.jsonl",
            "row_key": {
                "repo": observation["repo"],
                "pr_ref": observation["pr_ref"],
            },
            "write_performed": False,
            "path_recorded": False,
        },
        "external_reads_performed": bool(fetch_metadata),
        "external_writes_performed": False,
        "issue_body_captured": False,
        "comment_bodies_captured": False,
        "maintainer_correction_body_captured": False,
        "response_payloads_captured": False,
        "raw_check_logs_captured": False,
        "local_paths_captured": False,
        "private_repo_state_read": False,
        "todo_write_performed": False,
        "destructive_git_used": False,
    }
    first_push_ci = _first_push_ci_evidence(observation, observed_at=generated_at)
    if first_push_ci is not None:
        packet["first_push_ci"] = first_push_ci
    if maintainer_correction_admission is not None:
        entries = maintainer_correction_admission.pop("entries")
        packet["maintainer_correction_admission"] = maintainer_correction_admission
        packet["maintainer_corrections"] = entries
        if source_events is not None:
            packet["source_events_observed"] = {
                "status": source_events.get("status"),
                "event_count": len(source_events.get("events") or []),
                "body_captured": False,
            }
    validation = validate_issue_fix_pr_lifecycle_monitor_packet(packet)
    packet["ok"] = bool(validation["ok"])
    packet["validation"] = validation
    return packet


def validate_issue_fix_pr_lifecycle_monitor_packet(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION:
        errors.append("packet schema_version must be issue_fix_pr_lifecycle_monitor_v0")
    for key in (
        "external_writes_performed",
        "issue_body_captured",
        "comment_bodies_captured",
        "maintainer_correction_body_captured",
        "response_payloads_captured",
        "raw_check_logs_captured",
        "local_paths_captured",
        "private_repo_state_read",
        "destructive_git_used",
    ):
        if packet.get(key) is not False:
            errors.append(f"packet {key} must be false")
    observation = packet.get("observation")
    if not isinstance(observation, Mapping):
        errors.append("observation is required")
        observation = {}
    if observation.get("kind") != "pull_request":
        errors.append("observation kind must be pull_request")
    for key in (
        "body_captured",
        "comment_bodies_captured",
        "timeline_captured",
        "log_output_captured",
        "response_payload_captured",
    ):
        if observation.get(key) is not False:
            errors.append(f"observation {key} must be false")
    checks = observation.get("checks")
    if isinstance(checks, Mapping):
        if checks.get("raw_status_captured") is not False:
            errors.append("checks raw_status_captured must be false")
        if checks.get("log_output_captured") is not False:
            errors.append("checks log_output_captured must be false")
    else:
        errors.append("observation checks are required")
    review_response = observation.get("review_response")
    if not isinstance(review_response, Mapping):
        errors.append("observation review_response is required")
    else:
        if review_response.get("raw_reviews_captured") is not False:
            errors.append("review_response raw_reviews_captured must be false")
        if review_response.get("raw_thread_comments_captured") is not False:
            errors.append(
                "review_response raw_thread_comments_captured must be false"
            )
    transition = packet.get("transition")
    if not isinstance(transition, Mapping):
        errors.append("transition is required")
        transition = {}
    decision = transition.get("decision")
    if decision not in {
        "runnable_successor",
        "monitor_continuation",
        "user_gate",
        "no_followup",
    }:
        errors.append("transition decision is invalid")
    if transition.get("would_write") is not False:
        errors.append("transition would_write must be false")
    if transition.get("requires_execute_flag") is not True:
        errors.append("transition must require execute flag")
    state = observation.get("state")
    if state in TERMINAL_PR_STATES and decision != "no_followup":
        errors.append("terminal PR state must choose no_followup")
    if (
        state in TERMINAL_PR_STATES
        and transition.get("terminal_state_precedence") is not True
    ):
        errors.append("terminal PR state must record terminal_state_precedence")
    if transition.get("material_change") is True and decision == "monitor_continuation":
        errors.append("material PR change must not choose monitor_continuation")
    correction = packet.get("maintainer_correction")
    if correction is not None:
        if not isinstance(correction, Mapping):
            errors.append("maintainer_correction must be an object")
        else:
            for key in (
                "raw_body_captured",
                "raw_comment_captured",
                "raw_provider_payload_captured",
                "local_paths_captured",
            ):
                if correction.get(key) is not False:
                    errors.append(f"maintainer_correction {key} must be false")
            if packet.get("maintainer_correction_fingerprint") != correction.get(
                "correction_fingerprint"
            ):
                errors.append(
                    "maintainer correction fingerprint must match normalized input"
                )
            if state in TERMINAL_PR_STATES and decision != "no_followup":
                errors.append("terminal PR state must supersede maintainer correction")
    admission = packet.get("maintainer_correction_admission")
    if correction is not None and admission is None:
        errors.append("maintainer_correction requires maintainer_correction_admission")
    if admission is not None:
        if not isinstance(admission, Mapping):
            errors.append("maintainer_correction_admission must be an object")
        else:
            if admission.get("body_captured") is not False:
                errors.append("maintainer_correction_admission body_captured must be false")
            if admission.get("admission_basis") not in MAINTAINER_CORRECTION_ADMISSION_BASES:
                errors.append("maintainer_correction_admission admission_basis is invalid")
            if admission.get("disposition") not in MAINTAINER_CORRECTION_DISPOSITIONS:
                errors.append("maintainer_correction_admission disposition is invalid")
            if admission.get("disposition") == "context_only" and (
                admission.get("reason") not in MAINTAINER_CORRECTION_REJECTION_REASONS
            ):
                errors.append("context_only maintainer correction must record a reason")
            if not str(admission.get("operation_id") or "").startswith(
                MAINTAINER_CORRECTION_OPERATION_ID_PREFIX
            ):
                errors.append("maintainer_correction_admission operation_id is invalid")
    entries = packet.get("maintainer_corrections")
    if entries is not None:
        if not isinstance(entries, list):
            errors.append("maintainer_corrections must be a list")
        else:
            for entry in entries:
                if not isinstance(entry, Mapping):
                    errors.append("maintainer_corrections entries must be objects")
                    continue
                if entry.get("body_captured") is not False:
                    errors.append("maintainer_corrections entry body_captured must be false")
                if entry.get("disposition") not in MAINTAINER_CORRECTION_DISPOSITIONS:
                    errors.append("maintainer_corrections entry disposition is invalid")
                event = entry.get("source_event")
                if event is not None and (
                    not isinstance(event, Mapping)
                    or event.get("body_captured") is not False
                    or any(str(key).lower() in {"body", "body_text", "body_html"} for key in event)
                ):
                    errors.append("maintainer_corrections source_event must stay body-free")
    contract = packet.get("writeback_contract")
    if not isinstance(contract, Mapping):
        errors.append("writeback_contract is required")
    elif contract.get("todo_write_performed") is not packet.get("todo_write_performed"):
        errors.append("writeback_contract todo_write_performed must match packet")
    grouped_monitor = packet.get("grouped_monitor_projection")
    if not isinstance(grouped_monitor, Mapping):
        errors.append("grouped_monitor_projection is required")
    else:
        if (
            grouped_monitor.get("schema_version")
            != ISSUE_FIX_PR_GROUPED_MONITOR_PROJECTION_SCHEMA_VERSION
        ):
            errors.append(
                "grouped_monitor_projection schema_version must be issue_fix_pr_grouped_monitor_projection_v1"
            )
        if grouped_monitor.get("creates_per_pr_continuous_monitor_todo") is not False:
            errors.append("grouped monitor projection must not create per-PR monitors")
        if grouped_monitor.get("per_pr_material_action") != "one_shot_advancement_todo":
            errors.append("grouped monitor projection must use one-shot per-PR actions")
        if grouped_monitor.get("external_notification_granularity") != "one_pr_per_message":
            errors.append("grouped monitor projection must preserve one-PR messages")
        if state in TERMINAL_PR_STATES:
            if grouped_monitor.get("member_operation") != "remove":
                errors.append("terminal PR must be removed from grouped monitor membership")
            if grouped_monitor.get("target_key") is not None:
                errors.append("terminal PR must not retain a grouped monitor target")
            if grouped_monitor.get("materialize_nonempty_bucket_monitor") is not False:
                errors.append("terminal PR must not materialize a grouped monitor")
        else:
            target_key = str(grouped_monitor.get("target_key") or "")
            if not target_key.startswith("github-pr-state-"):
                errors.append("open PR grouped monitor target_key is invalid")
            if grouped_monitor.get("member_operation") != "upsert":
                errors.append("open PR must upsert grouped monitor membership")
            if grouped_monitor.get("materialize_nonempty_bucket_monitor") is not True:
                errors.append("open PR must materialize its nonempty grouped monitor")
    todo_write_performed = packet.get("todo_write_performed")
    if not isinstance(todo_write_performed, bool):
        errors.append("todo_write_performed must be boolean")
    todo_write = packet.get("todo_write")
    if todo_write_performed is True:
        if (
            not isinstance(todo_write, Mapping)
            or todo_write.get("write_performed") is not True
        ):
            errors.append("performed todo write requires a compact todo_write receipt")
        elif todo_write.get("path_recorded") is not False:
            errors.append("todo_write path_recorded must be false")
    domain_state = packet.get("domain_state_projection")
    if not isinstance(domain_state, Mapping):
        errors.append("domain_state_projection is required")
    else:
        if domain_state.get("domain_pack") != "issue_fix":
            errors.append("domain_state_projection domain_pack must be issue_fix")
        if domain_state.get("path_recorded") is not False:
            errors.append("domain_state_projection path_recorded must be false")
    return {
        "ok": not errors,
        "schema_version": "issue_fix_pr_lifecycle_monitor_validation_v0",
        "errors": errors,
        "decision": decision,
        "terminal_state_precedence": transition.get("terminal_state_precedence"),
    }


def render_issue_fix_pr_lifecycle_monitor_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Issue Fix PR Lifecycle",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- todo_write_performed: `{payload.get('todo_write_performed')}`",
        f"- observation_fingerprint: `{payload.get('observation_fingerprint')}`",
    ]
    observation = payload.get("observation")
    if isinstance(observation, Mapping):
        checks = (
            observation.get("checks")
            if isinstance(observation.get("checks"), Mapping)
            else {}
        )
        lines.extend(
            [
                "",
                "## Observation",
                "",
                f"- repo: `{observation.get('repo')}`",
                f"- pr_ref: `{observation.get('pr_ref')}`",
                f"- state: `{observation.get('state')}`",
                f"- review_decision: `{observation.get('review_decision')}`",
                f"- merge_state_status: `{observation.get('merge_state_status')}`",
                f"- checks: `{checks.get('aggregate')}`",
            ]
        )
    transition = payload.get("transition")
    if isinstance(transition, Mapping):
        lines.extend(
            [
                "",
                "## Transition",
                "",
                f"- decision: `{transition.get('decision')}`",
                f"- action_kind: `{transition.get('action_kind')}`",
                f"- material_change: `{transition.get('material_change')}`",
                f"- terminal_state_precedence: `{transition.get('terminal_state_precedence')}`",
                f"- reason: {transition.get('reason')}",
            ]
        )
    grouped_monitor = payload.get("grouped_monitor_projection")
    if isinstance(grouped_monitor, Mapping):
        lines.extend(
            [
                "",
                "## Grouped Monitor Projection",
                "",
                f"- state_bucket: `{grouped_monitor.get('state_bucket')}`",
                f"- target_key: `{grouped_monitor.get('target_key')}`",
                f"- member_operation: `{grouped_monitor.get('member_operation')}`",
                f"- creates_per_pr_continuous_monitor_todo: "
                f"`{grouped_monitor.get('creates_per_pr_continuous_monitor_todo')}`",
                f"- per_pr_material_action: "
                f"`{grouped_monitor.get('per_pr_material_action')}`",
            ]
        )
    correction = payload.get("maintainer_correction")
    if isinstance(correction, Mapping):
        lines.extend(
            [
                "",
                "## Maintainer Correction",
                "",
                f"- correction_kind: `{correction.get('correction_kind')}`",
                f"- source_kind: `{correction.get('source_kind')}`",
                f"- source_ref: `{correction.get('source_ref')}`",
                f"- correction_fingerprint: `{correction.get('correction_fingerprint')}`",
            ]
        )
    admission = payload.get("maintainer_correction_admission")
    if isinstance(admission, Mapping):
        lines.extend(
            [
                "",
                "## Maintainer Correction Admission",
                "",
                f"- admission_basis: `{admission.get('admission_basis')}`",
                f"- disposition: `{admission.get('disposition')}`",
                f"- reason: `{admission.get('reason')}`",
                f"- operation_id: `{admission.get('operation_id')}`",
            ]
        )
    domain_state = payload.get("domain_state_projection")
    if isinstance(domain_state, Mapping):
        lines.extend(
            [
                "",
                "## Domain State Projection",
                "",
                f"- domain_pack: `{domain_state.get('domain_pack')}`",
                f"- default_filename: `{domain_state.get('default_filename')}`",
                f"- write_performed: `{domain_state.get('write_performed')}`",
                f"- path_recorded: `{domain_state.get('path_recorded')}`",
            ]
        )
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        errors = (
            validation.get("errors")
            if isinstance(validation.get("errors"), list)
            else []
        )
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_ok: `{validation.get('ok')}`",
                f"- error_count: `{len(errors)}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"
