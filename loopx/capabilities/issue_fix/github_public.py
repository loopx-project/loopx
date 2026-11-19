from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


GITHUB_PUBLIC_CHANNEL_PROBE_PACKET_SCHEMA_VERSION = (
    "github_public_channel_probe_packet_v0"
)
GITHUB_PUBLIC_CHANNEL_REF_SCHEMA_VERSION = "github_public_channel_ref_v0"
GITHUB_PUBLIC_CHANNEL_METADATA_SCHEMA_VERSION = "github_public_channel_metadata_v0"
GITHUB_PUBLIC_REPLY_MONITOR_PACKET_SCHEMA_VERSION = (
    "github_public_reply_monitor_packet_v0"
)
GITHUB_PUBLIC_REPLY_SIGNAL_SCHEMA_VERSION = "github_public_reply_signal_v0"
GITHUB_PUBLIC_READ_FAILURE_SCHEMA_VERSION = "github_public_read_failure_v0"

# A public read that failed is reported as one of these reasons, never as the
# provider's own text: the steward answer contract forbids quoting a provider
# failure into an answer, and a manager that repeats "Cache miss" instead of a
# typed reason cannot say what is unread or how to repair it.
GITHUB_PUBLIC_READ_FAILURE_CODES = (
    "provider_timeout",
    "provider_network_unavailable",
    "provider_response_rejected",
    "provider_result_unreadable",
    "provider_tool_unavailable",
    "provider_read_failed",
)

ALLOWED_GITHUB_REF_TYPES = {"issue", "pull", "discussion"}
MAINTAINER_ASSOCIATIONS = {"COLLABORATOR", "MEMBER", "OWNER"}
GITHUB_COMMENT_BODY_KEYS = {
    "body",
    "body_text",
    "comment_body",
    "comment_bodies",
    "raw",
    "response_payload",
    "timeline",
}


def _normalise_github_url(url: str) -> tuple[str, dict[str, Any]]:
    text = str(url or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https":
        raise ValueError("GitHub channel URL must use https")
    if parsed.username or parsed.password:
        raise ValueError("GitHub channel URL must not include auth material")
    if parsed.query or parsed.fragment:
        raise ValueError("GitHub channel URL must not include query or fragment")
    if parsed.hostname != "github.com":
        raise ValueError("GitHub channel URL must target github.com")
    if parsed.port not in (None, 443):
        raise ValueError("GitHub channel URL must use the default https port")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4 or parts[2] not in {"issues", "pull", "discussions"}:
        raise ValueError(
            "GitHub channel URL must look like /owner/repo/issues/123, "
            "/owner/repo/pull/123, or /owner/repo/discussions/123"
        )
    owner, repo, kind_part, raw_number = parts
    try:
        number = int(raw_number)
    except ValueError as exc:
        raise ValueError("GitHub channel number must be an integer") from exc
    if number <= 0:
        raise ValueError("GitHub channel number must be positive")
    ref_type = {
        "issues": "issue",
        "pull": "pull",
        "discussions": "discussion",
    }[kind_part]
    normalised = urlunsplit(("https", "github.com", f"/{owner}/{repo}/{kind_part}/{number}", "", ""))
    return normalised, {
        "schema_version": GITHUB_PUBLIC_CHANNEL_REF_SCHEMA_VERSION,
        "owner": owner,
        "repo": repo,
        "ref_type": ref_type,
        "number": number,
        "url": normalised,
    }


def _normalise_github_issue_comment_url(url: str) -> tuple[str, dict[str, Any]]:
    text = str(url or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https":
        raise ValueError("GitHub issue comment URL must use https")
    if parsed.username or parsed.password:
        raise ValueError("GitHub issue comment URL must not include auth material")
    if parsed.query:
        raise ValueError("GitHub issue comment URL must not include query data")
    if parsed.hostname != "github.com":
        raise ValueError("GitHub issue comment URL must target github.com")
    if parsed.port not in (None, 443):
        raise ValueError("GitHub issue comment URL must use the default https port")
    if not parsed.fragment.startswith("issuecomment-"):
        raise ValueError("GitHub issue comment URL must include an issuecomment fragment")
    raw_comment_id = parsed.fragment.removeprefix("issuecomment-")
    try:
        comment_id = int(raw_comment_id)
    except ValueError as exc:
        raise ValueError("GitHub issue comment id must be an integer") from exc
    issue_url, issue_ref = _normalise_github_url(
        urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    )
    if issue_ref["ref_type"] not in {"issue", "pull"}:
        raise ValueError("GitHub issue comment URL must point at an issue or pull request")
    normalised_comment_url = urlunsplit(
        ("https", "github.com", urlsplit(issue_url).path, "", f"issuecomment-{comment_id}")
    )
    return normalised_comment_url, {
        "schema_version": "github_public_issue_comment_ref_v0",
        "owner": issue_ref["owner"],
        "repo": issue_ref["repo"],
        "ref_type": issue_ref["ref_type"],
        "number": issue_ref["number"],
        "comment_id": comment_id,
        "issue_url": issue_url,
        "comment_url": normalised_comment_url,
    }


def github_public_read_failure_row(exc: BaseException) -> dict[str, Any]:
    """One failed public read as a typed row the answer can carry.

    The row names the source, a typed reason, the coverage effect of the failed
    read and the next action. The provider's message is replaced by its digest:
    it stays correlatable with the provider log without letting an answer quote
    it, which is what "Cache miss" and "invalid_arguments" leaking into steward
    answers required.
    """

    if isinstance(exc, (subprocess.TimeoutExpired, TimeoutError)):
        code = "provider_timeout"
    elif isinstance(exc, HTTPError):
        code = "provider_response_rejected"
    elif isinstance(exc, URLError):
        code = "provider_network_unavailable"
    elif isinstance(exc, json.JSONDecodeError):
        code = "provider_result_unreadable"
    elif isinstance(exc, RuntimeError):
        code = "provider_tool_unavailable"
    else:
        code = "provider_read_failed"
    compact = " ".join(str(exc).split())
    row: dict[str, Any] = {
        "schema_version": GITHUB_PUBLIC_READ_FAILURE_SCHEMA_VERSION,
        "source_id": "github_public_channel",
        "code": code,
        "coverage_effect": (
            "the public GitHub metadata for this target was not read in this "
            "call; the target is unread rather than inactive"
        ),
        "next_action": (
            "retry with the GitHub CLI or authenticated tooling, or pass the "
            "metadata explicitly; do not report the target as having no activity"
        ),
        "provider_message_digest": "sha256:"
        + hashlib.sha256(compact.encode("utf-8")).hexdigest(),
    }
    status = getattr(exc, "code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        row["provider_status"] = status
    return row


def _fetch_issue_or_pull_metadata(ref: Mapping[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    api_url = (
        f"https://api.github.com/repos/{ref['owner']}/{ref['repo']}/issues/"
        f"{ref['number']}"
    )
    request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "LoopX-value-connectors",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - public GitHub API URL is validated above.
        payload = json.loads(response.read().decode("utf-8"))
    labels = payload.get("labels") if isinstance(payload.get("labels"), list) else []
    return {
        "schema_version": GITHUB_PUBLIC_CHANNEL_METADATA_SCHEMA_VERSION,
        "provider": "github_rest",
        "title": payload.get("title"),
        "state": payload.get("state"),
        "number": payload.get("number"),
        "labels": [
            item.get("name")
            for item in labels
            if isinstance(item, Mapping) and item.get("name")
        ],
        "comments_count": payload.get("comments"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "closed_at": payload.get("closed_at"),
        "author_association": payload.get("author_association"),
        "is_pull_request": isinstance(payload.get("pull_request"), Mapping),
        "html_url": payload.get("html_url"),
        "raw_body_captured": False,
        "comment_bodies_captured": False,
        "timeline_captured": False,
    }


def _fetch_discussion_metadata(ref: Mapping[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("discussion metadata fetch requires GitHub CLI `gh`")
    query = """
query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    discussion(number:$number) {
      number
      title
      url
      createdAt
      updatedAt
      comments { totalCount }
      category { name }
      author { login }
    }
  }
}
"""
    result = subprocess.run(
        [
            gh,
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={ref['owner']}",
            "-F",
            f"repo={ref['repo']}",
            "-F",
            f"number={ref['number']}",
        ],
        check=False,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError("gh graphql discussion metadata request failed")
    payload = json.loads(result.stdout)
    discussion = (
        payload.get("data", {})
        .get("repository", {})
        .get("discussion")
        if isinstance(payload, Mapping)
        else None
    )
    if not isinstance(discussion, Mapping):
        raise RuntimeError("discussion metadata was not found")
    category = discussion.get("category") if isinstance(discussion.get("category"), Mapping) else {}
    comments = discussion.get("comments") if isinstance(discussion.get("comments"), Mapping) else {}
    author = discussion.get("author") if isinstance(discussion.get("author"), Mapping) else {}
    return {
        "schema_version": GITHUB_PUBLIC_CHANNEL_METADATA_SCHEMA_VERSION,
        "provider": "github_graphql",
        "title": discussion.get("title"),
        "state": None,
        "number": discussion.get("number"),
        "labels": [],
        "comments_count": comments.get("totalCount"),
        "created_at": discussion.get("createdAt"),
        "updated_at": discussion.get("updatedAt"),
        "closed_at": None,
        "author_association": None,
        "category": category.get("name"),
        "author_login": author.get("login"),
        "html_url": discussion.get("url"),
        "raw_body_captured": False,
        "comment_bodies_captured": False,
        "timeline_captured": False,
    }


def _fetch_issue_comment_metadata(
    ref: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("reply monitor metadata fetch requires GitHub CLI `gh`")
    endpoint = f"repos/{ref['owner']}/{ref['repo']}/issues/{ref['number']}/comments?per_page=100"
    jq_filter = (
        "[.[] | {"
        "author: .user.login, "
        "author_association: .author_association, "
        "created_at: .created_at, "
        "updated_at: .updated_at, "
        "url: .html_url"
        "}]"
    )
    result = subprocess.run(
        [
            gh,
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            endpoint,
            "--jq",
            jq_filter,
        ],
        check=False,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError("gh issue comments metadata request failed")
    comments = json.loads(result.stdout)
    if not isinstance(comments, list):
        raise RuntimeError("issue comments metadata must be a JSON array")
    return {"comments": comments}


def _fetch_body_free_event_list(
    gh: str, endpoint: str, jq_filter: str, *, timeout_seconds: float
) -> list[Any]:
    result = subprocess.run(
        [
            gh,
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "--paginate",
            endpoint,
            "--jq",
            jq_filter,
        ],
        check=False,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError("gh pull request event metadata request failed")
    # --paginate with --jq emits one JSON array per page.
    events: list[Any] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        page = json.loads(line)
        if not isinstance(page, list):
            raise RuntimeError("pull request event metadata must be a JSON array")
        events.extend(page)
    return events


def fetch_github_pull_request_event_metadata(
    *, owner: str, repo: str, number: int, timeout_seconds: float
) -> dict[str, Any]:
    """Fetch PR issue comments, reviews and review comments without bodies.

    Each item keeps only the provider id, author login, author association,
    timestamps and html URL so a host-asserted correction can be matched to
    the event that carried it.
    """

    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("pull request event metadata fetch requires GitHub CLI `gh`")
    comment_filter = (
        "[.[] | {id: .id, author: .user.login, "
        "author_association: .author_association, created_at: .created_at, "
        "updated_at: .updated_at, url: .html_url}]"
    )
    review_filter = (
        "[.[] | {id: .id, author: .user.login, "
        "author_association: .author_association, submitted_at: .submitted_at, "
        "url: .html_url}]"
    )
    base = f"repos/{owner}/{repo}"
    return {
        "comments": _fetch_body_free_event_list(
            gh, f"{base}/issues/{number}/comments?per_page=100", comment_filter,
            timeout_seconds=timeout_seconds,
        ),
        "reviews": _fetch_body_free_event_list(
            gh, f"{base}/pulls/{number}/reviews?per_page=100", review_filter,
            timeout_seconds=timeout_seconds,
        ),
        "reviewComments": _fetch_body_free_event_list(
            gh, f"{base}/pulls/{number}/comments?per_page=100", comment_filter,
            timeout_seconds=timeout_seconds,
        ),
        "body_captured": False,
    }


def _parse_github_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _comment_author(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:120]
    if isinstance(value, Mapping):
        login = value.get("login")
        if isinstance(login, str) and login.strip():
            return login.strip()[:120]
    return None


def _comment_url(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return None
    if parsed.username or parsed.password or parsed.query:
        return None
    return urlunsplit(("https", "github.com", parsed.path, "", parsed.fragment))


def _provider_comments(provider_payload: Mapping[str, Any] | list[Any] | None) -> list[Any]:
    if isinstance(provider_payload, list):
        return list(provider_payload)
    if not isinstance(provider_payload, Mapping):
        return []
    comments = provider_payload.get("comments")
    if isinstance(comments, list):
        return list(comments)
    return []


def _normalise_comment_signal(
    item: Any,
    *,
    anchor_url: str,
    anchor_created_at: datetime | None,
) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    url = _comment_url(item.get("url") or item.get("html_url"))
    created_at = item.get("created_at") or item.get("createdAt")
    created_dt = _parse_github_timestamp(created_at)
    association = str(
        item.get("author_association")
        or item.get("authorAssociation")
        or item.get("association")
        or "NONE"
    ).strip().upper()
    gated_fields = sorted(
        str(key) for key in item if str(key).lower() in GITHUB_COMMENT_BODY_KEYS
    )
    is_anchor = url == anchor_url
    is_after_anchor = (
        bool(anchor_created_at and created_dt and created_dt > anchor_created_at)
        if not is_anchor
        else False
    )
    return {
        "schema_version": GITHUB_PUBLIC_REPLY_SIGNAL_SCHEMA_VERSION,
        "author": _comment_author(item.get("author") or item.get("user")) or "unknown",
        "author_association": association,
        "created_at": str(created_at)[:40] if created_at else None,
        "updated_at": str(item.get("updated_at") or item.get("updatedAt") or "")[:40]
        or None,
        "url": url,
        "is_anchor_comment": is_anchor,
        "is_after_anchor": is_after_anchor,
        "is_maintainer_signal": bool(is_after_anchor and association in MAINTAINER_ASSOCIATIONS),
        "body_captured": False,
        "comment_body_captured": False,
        "raw_payload_captured": False,
        "gated_fields_present": gated_fields,
    }


def build_github_public_reply_monitor_packet(
    *,
    issue_url: str,
    after_comment_url: str,
    provider_payload: Mapping[str, Any] | list[Any] | None = None,
    fetch_metadata: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    normalised_issue_url, issue_ref = _normalise_github_url(issue_url)
    if issue_ref["ref_type"] not in {"issue", "pull"}:
        raise ValueError("--issue-url must point at a GitHub issue or pull request")
    normalised_comment_url, comment_ref = _normalise_github_issue_comment_url(after_comment_url)
    same_target = (
        issue_ref["owner"] == comment_ref["owner"]
        and issue_ref["repo"] == comment_ref["repo"]
        and issue_ref["number"] == comment_ref["number"]
    )
    if not same_target:
        raise ValueError("--issue-url and --after-comment-url must point at the same GitHub thread")
    read_failure: dict[str, Any] | None = None
    live_payload: Mapping[str, Any] | None = None
    if fetch_metadata:
        try:
            live_payload = _fetch_issue_comment_metadata(issue_ref, timeout_seconds=timeout_seconds)
        except (
            RuntimeError,
            json.JSONDecodeError,
            subprocess.TimeoutExpired,
        ) as exc:
            read_failure = github_public_read_failure_row(exc)

    payload_source = live_payload if live_payload is not None else provider_payload
    comments = _provider_comments(payload_source)
    anchor_created_at: datetime | None = None
    for item in comments:
        if isinstance(item, Mapping) and _comment_url(item.get("url") or item.get("html_url")) == normalised_comment_url:
            anchor_created_at = _parse_github_timestamp(item.get("created_at") or item.get("createdAt"))
            break
    signals = [
        signal
        for item in comments
        if (
            signal := _normalise_comment_signal(
                item,
                anchor_url=normalised_comment_url,
                anchor_created_at=anchor_created_at,
            )
        )
    ]
    new_replies = [signal for signal in signals if signal["is_after_anchor"]]
    maintainer_replies = [signal for signal in new_replies if signal["is_maintainer_signal"]]
    gated_field_count = sum(len(signal["gated_fields_present"]) for signal in signals)
    metadata_collected = bool(comments)
    validation_errors: list[str] = []
    validation_warnings: list[str] = []
    if read_failure:
        validation_errors.append("metadata read failed; retry with gh auth/tooling or use --metadata-json")
    if metadata_collected and anchor_created_at is None:
        validation_errors.append("anchor LoopX comment was not found in comment metadata")
    if not metadata_collected:
        validation_warnings.append("no comment metadata was provided; monitor is a reference-only packet")
    recommended_action = (
        "prepare_public_triage_note"
        if maintainer_replies
        else "wait_no_bump"
    )
    money_signal = (
        "public_maintainer_interest"
        if maintainer_replies
        else "no_conversion_signal_yet"
    )
    connector_call = {
        "schema_version": "connector_call_intent_v0",
        "call_id": f"github_{issue_ref['ref_type']}_{issue_ref['number']}_reply_monitor",
        "connector_id": "github_public_reply_monitor",
        "connector_kind": "lead_monitor",
        "channel": f"GitHub {issue_ref['ref_type']} replies",
        "stage": "monitor",
        "target_ref": f"{issue_ref['owner']}/{issue_ref['repo']}#{issue_ref['number']}",
        "target_url": normalised_issue_url,
        "access_mode": "public_metadata_only",
        "external_reads_allowed": True,
        "external_writes_allowed": False,
        "external_write_requested": False,
        "requires_user_approval": False,
        "approval_gate_id": None,
        "value_axis": "demand",
        "money_metric": "public maintainer reply asking for a LoopX triage note or workflow audit",
        "success_metric": "maintainer reply after the LoopX comment with MEMBER/OWNER/COLLABORATOR association",
        "kill_condition": "no public maintainer reply appears; do not bump the thread",
        "promotion_target": "public_triage_note_or_stop",
    }
    return {
        "ok": not validation_errors,
        "schema_version": GITHUB_PUBLIC_REPLY_MONITOR_PACKET_SCHEMA_VERSION,
        "mode": "github-public-reply-monitor",
        "connector_id": "github_public_reply_monitor",
        "ref": issue_ref,
        "anchor_comment": comment_ref,
        "connector_call": connector_call,
        "external_reads_performed": bool(fetch_metadata),
        "external_writes_performed": False,
        "raw_body_captured": False,
        "comment_bodies_captured": False,
        "timeline_captured": False,
        "raw_provider_payload_captured": False,
        "restricted_material_recorded": False,
        "autopublish_allowed": False,
        "reply_signals": signals,
        "new_public_reply_count": len(new_replies),
        "maintainer_reply_count": len(maintainer_replies),
        "money_signal": money_signal,
        "recommended_action": recommended_action,
        "stop_condition": "do not bump or draft a triage note until public maintainer interest appears",
        "read_failure": read_failure,
        "validation": {
            "ok": not validation_errors,
            "errors": validation_errors,
            "warnings": validation_warnings,
            "metadata_collected": metadata_collected,
            "anchor_found": anchor_created_at is not None,
            "gated_provider_field_count": gated_field_count,
            "new_public_reply_count": len(new_replies),
            "maintainer_reply_count": len(maintainer_replies),
        },
    }


def build_github_public_channel_probe_packet(
    *,
    url: str,
    fetch_metadata: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    normalised_url, ref = _normalise_github_url(url)
    metadata: dict[str, Any] | None = None
    read_failure: dict[str, Any] | None = None
    if fetch_metadata:
        try:
            if ref["ref_type"] in {"issue", "pull"}:
                metadata = _fetch_issue_or_pull_metadata(ref, timeout_seconds=timeout_seconds)
            else:
                metadata = _fetch_discussion_metadata(ref, timeout_seconds=timeout_seconds)
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            read_failure = github_public_read_failure_row(exc)
    connector_call = {
        "schema_version": "connector_call_intent_v0",
        "call_id": f"github_{ref['ref_type']}_{ref['number']}_metadata_probe",
        "connector_id": "github_public_channel",
        "connector_kind": "github_channel",
        "channel": f"GitHub {ref['ref_type']}",
        "stage": "observe",
        "target_ref": f"{ref['owner']}/{ref['repo']}#{ref['number']}",
        "target_url": normalised_url,
        "access_mode": "public_metadata_only",
        "external_reads_allowed": True,
        "external_writes_allowed": False,
        "external_write_requested": False,
        "requires_user_approval": False,
        "approval_gate_id": None,
        "value_axis": "demand",
        "money_metric": "qualified public workflow signal that can become a LoopX audit or pilot",
        "success_metric": "metadata is enough to decide monitor, reply draft, or stop",
        "kill_condition": "public metadata shows no workflow owner, reply path, or reusable agent-work signal",
        "promotion_target": "value_connector_plan_v0",
    }
    validation_errors: list[str] = []
    if read_failure:
        validation_errors.append("metadata read failed; retry with auth material/tooling or use no-fetch mode")
    return {
        "ok": not validation_errors,
        "schema_version": GITHUB_PUBLIC_CHANNEL_PROBE_PACKET_SCHEMA_VERSION,
        "mode": "github-public-channel-probe",
        "connector_id": "github_public_channel",
        "ref": ref,
        "connector_call": connector_call,
        "metadata": metadata,
        "read_failure": read_failure,
        "external_reads_performed": bool(fetch_metadata),
        "external_writes_performed": False,
        "raw_body_captured": False,
        "comment_bodies_captured": False,
        "timeline_captured": False,
        "restricted_material_recorded": False,
        "autopublish_allowed": False,
        "validation": {
            "ok": not validation_errors,
            "errors": validation_errors,
            "ref_type": ref["ref_type"],
            "metadata_collected": metadata is not None,
        },
    }


def render_github_public_reply_monitor_markdown(payload: dict[str, Any]) -> str:
    ref = payload.get("ref") if isinstance(payload.get("ref"), Mapping) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), Mapping) else {}
    lines = [
        "# LoopX GitHub Public Reply Monitor",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- raw_body_captured: `{payload.get('raw_body_captured')}`",
        f"- comment_bodies_captured: `{payload.get('comment_bodies_captured')}`",
        f"- money_signal: `{payload.get('money_signal')}`",
        f"- recommended_action: `{payload.get('recommended_action')}`",
        "",
    ]
    if ref:
        lines.insert(
            3,
            f"- ref: `{ref.get('owner')}/{ref.get('repo')} {ref.get('ref_type')} #{ref.get('number')}`",
        )
    lines.extend(
        [
            "## Reply Signals",
            "",
            f"- new_public_reply_count: `{payload.get('new_public_reply_count')}`",
            f"- maintainer_reply_count: `{payload.get('maintainer_reply_count')}`",
            "",
        ]
    )
    for signal in payload.get("reply_signals") or []:
        if not isinstance(signal, Mapping) or not signal.get("is_after_anchor"):
            continue
        lines.append(
            "- "
            f"{signal.get('created_at')} `{signal.get('author_association')}` "
            f"maintainer_signal=`{signal.get('is_maintainer_signal')}` "
            f"url={signal.get('url')}"
        )
    failure = (
        payload.get("read_failure")
        if isinstance(payload.get("read_failure"), Mapping)
        else None
    )
    if failure:
        lines.extend(
            [
                "",
                "## Unread Source",
                "",
                f"- source_id: `{failure.get('source_id')}`",
                f"- code: `{failure.get('code')}`",
                f"- coverage_effect: {failure.get('coverage_effect')}",
                f"- next_action: {failure.get('next_action')}",
                f"- provider_message_digest: `{failure.get('provider_message_digest')}`",
            ]
        )
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    warnings = validation.get("warnings") if isinstance(validation.get("warnings"), list) else []
    if errors:
        lines.extend(["", "## Validation Errors", ""])
        lines.extend(f"- {error}" for error in errors)
    if warnings:
        lines.extend(["", "## Validation Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines).rstrip() + "\n"

def render_github_public_channel_probe_markdown(payload: dict[str, Any]) -> str:
    ref = payload.get("ref") if isinstance(payload.get("ref"), Mapping) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), Mapping) else {}
    lines = [
        "# LoopX GitHub Public Channel Probe",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- raw_body_captured: `{payload.get('raw_body_captured')}`",
        f"- comment_bodies_captured: `{payload.get('comment_bodies_captured')}`",
        "",
    ]
    if ref:
        lines.insert(
            3,
            f"- ref: `{ref.get('owner')}/{ref.get('repo')} {ref.get('ref_type')} #{ref.get('number')}`",
        )
    if metadata:
        lines.extend(
            [
                "## Metadata",
                "",
                f"- title: {metadata.get('title')}",
                f"- state: `{metadata.get('state')}`",
                f"- comments_count: `{metadata.get('comments_count')}`",
                f"- updated_at: `{metadata.get('updated_at')}`",
                "",
            ]
        )
    failure = (
        payload.get("read_failure")
        if isinstance(payload.get("read_failure"), Mapping)
        else None
    )
    if failure:
        lines.extend(
            [
                "## Unread Source",
                "",
                f"- source_id: `{failure.get('source_id')}`",
                f"- code: `{failure.get('code')}`",
                f"- coverage_effect: {failure.get('coverage_effect')}",
                f"- next_action: {failure.get('next_action')}",
                f"- provider_message_digest: `{failure.get('provider_message_digest')}`",
                "",
            ]
        )
    if payload.get("error"):
        lines.extend(["## Error", "", str(payload.get("error")), ""])
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    if errors:
        lines.extend(["## Validation Errors", ""])
        lines.extend(f"- {error}" for error in errors)
    return "\n".join(lines).rstrip() + "\n"
