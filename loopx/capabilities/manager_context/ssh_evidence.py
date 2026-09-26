"""Read scoped Core evidence from an operator-configured SSH source."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

from . import POLICY_SCHEMA, _root, _read, _write
from ...file_lock import exclusive_file_lock
from ...control_plane.status.ssh_host_catalog import configured_ssh_host_aliases

# --- The bounded turn-time source read ---------------------------------------
#
# A declared source that is never read is a coverage gap the model cannot close
# on a prompt-only transport, and an unbounded read on every Turn would make the
# channel unusable. So the Turn-time read is cached, budgeted and typed: one
# read per host per TTL window, one total time budget per Turn, and a declared
# status with its freshness for every source whether it was read or not.
MANAGER_REMOTE_EVIDENCE_SCHEMA = "manager_remote_evidence_v0"
MANAGER_REMOTE_EVIDENCE_TTL_SECONDS = 600
MANAGER_REMOTE_EVIDENCE_HOST_TIMEOUT_SECONDS = 9
MANAGER_REMOTE_EVIDENCE_BUDGET_SECONDS = 10
MANAGER_REMOTE_EVIDENCE_MAX_HOSTS = 2
MANAGER_REMOTE_EVIDENCE_ROW_LIMIT = 8
MANAGER_REMOTE_EVIDENCE_SCOPE_OWNER = "owner_global"

def grants(root, channel):
    try:
        policy = _read(_root(root) / "policy.json")
        if policy.get("schema_version") != POLICY_SCHEMA:
            return {}
        raw = policy.get("sources", {}).get(channel, {}).get("evidence_ssh_hosts", {})
        return {
            h: sorted(set(ids))
            for h, ids in raw.items()
            if isinstance(h, str)
            and isinstance(ids, list)
            and ids
            and len(ids) <= 128
            and all(
                isinstance(g, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", g)
                for g in ids
            )
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def configure(root, *, channel, host, goal_ids, execute=False, config_path=None):
    if not re.fullmatch(r"manager\.external\.[a-f0-9]{24}", channel):
        raise ValueError("exact external manager channel required")
    if host not in configured_ssh_host_aliases(config_path):
        raise ValueError("host must be an existing SSH alias")
    if len(goal_ids) > 128 or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", g) for g in goal_ids
    ):
        raise ValueError("bounded exact remote Goal IDs required")
    ids = sorted(set(goal_ids))
    if execute:
        path = _root(root) / "policy.json"
        with exclusive_file_lock(path.with_suffix(".lock")):
            policy = (
                _read(path)
                if path.exists()
                else {"schema_version": POLICY_SCHEMA, "sources": {}}
            )
            if policy.get("schema_version") != POLICY_SCHEMA:
                raise ValueError("invalid manager policy")
            sources = (
                policy.setdefault("sources", {})
                .setdefault(channel, {})
                .setdefault("evidence_ssh_hosts", {})
            )
            if ids:
                sources[host] = ids
            else:
                sources.pop(host, None)
            _write(path, policy)
    return {
        "ok": True,
        "executed": execute,
        "source_id": "ssh:" + host,
        "goal_ids": ids,
        "scope": "audience_remote_goal_summaries",
    }


def registered_evidence_hosts(root, config_path=None) -> list[str]:
    """Return the hosts this machine registered as an evidence source.

    A registration is an explicit ``evidence_ssh_hosts`` grant on any channel.
    Every configured SSH alias is not a LoopX evidence host: an operator's
    ``github.com`` or personal jump host holds no Core state, and declaring it
    only puts unrelated names in front of the model.
    """

    try:
        policy = _read(_root(root) / "policy.json")
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    if policy.get("schema_version") != POLICY_SCHEMA:
        return []
    hosts: list[str] = []
    raw_sources = policy.get("sources")
    if not isinstance(raw_sources, dict):
        return []
    for channel_sources in raw_sources.values():
        if not isinstance(channel_sources, dict):
            continue
        raw_hosts = channel_sources.get("evidence_ssh_hosts")
        if not isinstance(raw_hosts, dict):
            continue
        for host in raw_hosts:
            if isinstance(host, str) and host not in hosts:
                hosts.append(host)
    return hosts


def sources(root, channel, owner, config_path=None):
    """Declare the evidence sources; declaring a source never reads it."""

    allowed = sorted(grants(root, channel))
    declared = registered_evidence_hosts(root, config_path) if owner else allowed
    aliases = set(configured_ssh_host_aliases(config_path))
    rows: list[dict[str, Any]] = [
        {"source_id": "local", "source_host": "local", "status": "available"}
    ]
    for host in declared:
        configured = host in aliases
        rows.append(
            {
                "source_id": "ssh:" + host,
                "source_host": host,
                # A registered host whose alias disappeared is drift the answer
                # must name, not a source that silently stops being declared.
                "status": "not_read" if configured else "not_configured",
                "reason": None if configured else "ssh_alias_not_configured",
                "scope": "remote_registry" if owner else "explicit_goal_grant",
            }
        )
    return rows


def read_remote(
    root,
    channel,
    owner,
    args,
    scope_valid,
    *,
    runner=subprocess.run,
    config_path=None,
    timeout_seconds: float = 45,
):
    host = args["source_id"].removeprefix("ssh:")
    before = grants(root, channel)
    if (
        not scope_valid()
        or host not in configured_ssh_host_aliases(config_path)
        or (not owner and host not in before)
    ):
        return {"ok": False, "error": "source_outside_available_scope"}
    goal = args.get("goal_id")
    if goal and not owner and goal not in before[host]:
        return {"ok": False, "error": "goal_outside_available_scope"}

    def unavailable(code: str, reason: str) -> dict[str, Any]:
        # A failure may arrive after the grant or alias changed. Never reveal a
        # cached row unless the caller still owns the same source scope.
        if (
            not scope_valid()
            or (not owner and before != grants(root, channel))
            or host not in configured_ssh_host_aliases(config_path)
        ):
            return {"ok": False, "error": "authorization_changed"}
        return _unavailable_read_with_cached_portfolio(
            root,
            channel,
            owner,
            args,
            code,
            reason,
        )

    # Values are validated/quoted; the model cannot choose a binary, path or command.
    argv = [
        "goal-portfolio",
        "--manager-view",
        args["view"],
        "--offset",
        str(args.get("offset", 0)),
        "--limit",
        str(args.get("limit", 8)),
        "--days",
        str(args.get("days", 1)),
    ]
    for gid in ([goal] if goal else (None if owner else before[host])) or []:
        argv += ["--goal-id", gid]
    if args.get("view") == "agents":
        argv += ["--query", args.get("query", "")]
    if args.get("include_stopped"):
        argv += ["--include-stopped"]
    command = (
        'exec "$HOME/.local/bin/loopx" --format json '
        + shlex.join(argv)
    )
    ssh = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ForwardX11=no",
    ]
    if config_path:
        ssh += ["-F", str(config_path)]
    try:
        result = runner(
            [*ssh, host, command],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode:
            code, reason = _remote_read_failure(
                returncode=int(result.returncode),
                stderr=str(getattr(result, "stderr", "") or ""),
            )
            return unavailable(code, reason)
        if len(result.stdout) > 100000:
            code, reason = _remote_read_failure(
                returncode=0,
                stderr="",
                protocol=True,
            )
            return unavailable(code, reason)
        packet = json.loads(result.stdout)
        if (
            not isinstance(packet, dict)
            or packet.get("schema_version") != "manager_evidence_page_v1"
            or not isinstance(packet.get("rows"), list)
            or any(not isinstance(r, dict) for r in packet["rows"])
        ):
            code, reason = _remote_read_failure(
                returncode=0, stderr="", protocol=True
            )
            return unavailable(code, reason)
        if (
            not scope_valid()
            or (not owner and before != grants(root, channel))
            or host not in configured_ssh_host_aliases(config_path)
        ):
            return {"ok": False, "error": "authorization_changed"}
        if not owner and any(
            r.get("goal_id") not in before[host] for r in packet["rows"]
        ):
            return {"ok": False, "error": "remote_scope_mismatch"}
        packet.update(source_id=args["source_id"], source_host=host)
        packet["rows"] = [
            {**r, "source_id": args["source_id"], "source_host": host}
            for r in packet["rows"]
        ]
        return packet
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        code, reason = _remote_read_failure(
            returncode=0,
            stderr=str(exc),
            protocol=isinstance(exc, ValueError),
        )
        return unavailable(code, reason)


def _cache_dir(root) -> Path:
    return _root(root) / "remote-reads"


def _scope_identity(root, channel, owner) -> str:
    """Cache identity: a scope or grant change must invalidate every entry."""

    if owner:
        return MANAGER_REMOTE_EVIDENCE_SCOPE_OWNER
    return "channel:" + hashlib.sha256(
        json.dumps(grants(root, channel), sort_keys=True).encode()
    ).hexdigest()


REMOTE_READ_AUTH_REQUIRED = "ssh_auth_required"
REMOTE_READ_HOST_UNREACHABLE = "ssh_host_unreachable"
REMOTE_READ_CLI_MISSING = "remote_cli_missing"
REMOTE_READ_PROTOCOL = "remote_protocol_unavailable"
REMOTE_READ_UNKNOWN = "remote_evidence_unavailable"

_REMOTE_READ_REASONS = {
    REMOTE_READ_AUTH_REQUIRED: (
        "the host refused this machine's SSH credential, and it accepts only a "
        "login this machine did not present: renew this machine's Kerberos "
        "ticket with kinit, or provision a key the host accepts"
    ),
    REMOTE_READ_HOST_UNREACHABLE: (
        "the host could not be reached from this machine: check that it is up "
        "and reachable from here before the next read"
    ),
    REMOTE_READ_CLI_MISSING: (
        "the host has no LoopX CLI at ~/.local/bin/loopx, so it cannot answer "
        "an evidence read: install or expose the CLI on that host"
    ),
    REMOTE_READ_PROTOCOL: (
        "the remote answer was not a LoopX evidence packet, so the CLI there is "
        "older than this read: upgrade the remote LoopX CLI"
    ),
    REMOTE_READ_UNKNOWN: (
        "the bounded remote read did not complete and the cause is not in the "
        "answer: retry the read, then inspect this host's SSH setup"
    ),
}

# The compact packet carries one typed limitation per failed source beside the
# per-row reason, so a reader that only sees the summary still learns that the
# source needs a repair rather than that the remote Goal made no progress.
_REMOTE_READ_LIMITATIONS = {
    REMOTE_READ_AUTH_REQUIRED: "remote_source_ssh_auth_required",
    REMOTE_READ_HOST_UNREACHABLE: "remote_source_ssh_host_unreachable",
    REMOTE_READ_CLI_MISSING: "remote_source_cli_missing",
    REMOTE_READ_PROTOCOL: "remote_source_protocol_unavailable",
    REMOTE_READ_UNKNOWN: "remote_source_unavailable",
}
REMOTE_SOURCE_STALE_COVERAGE_EFFECT = "remote_goals_may_be_outdated_not_absent"
REMOTE_SOURCE_FAILURE_NEXT_ACTION = (
    "Tell the owner this source is unread, why it failed, how to repair it, and "
    "the last successful read; use cached rows only as stale evidence, continue "
    "with the evidence that is readable, and do not present the failure as no "
    "progress."
)


def _remote_read_failure(
    *, returncode: int, stderr: str, protocol: bool = False
) -> tuple[str, str]:
    """Classify one failed bounded read into a typed code and public-safe text.

    A declared source that fails must say which side has to change. Reported as
    an undifferentiated "unavailable", an authentication gap and a dead host
    look identical to the owner, so the one action that would unblock the read
    cannot be named.
    """

    if protocol:
        return REMOTE_READ_PROTOCOL, _REMOTE_READ_REASONS[REMOTE_READ_PROTOCOL]
    text = " ".join(str(stderr or "").split())[:400]
    lowered = text.lower()
    if returncode == 127 or "command not found" in lowered:
        return REMOTE_READ_CLI_MISSING, _REMOTE_READ_REASONS[REMOTE_READ_CLI_MISSING]
    # Bounded substring classification, kept deliberately narrow: ssh prints
    # `Permission denied (<method list>)` for its own rejected credential, while
    # a remote command can print a bare `Permission denied` about a file. Only
    # the ssh forms may claim the owner's credential is the cause, or the read
    # would tell the owner to renew a ticket when the remote CLI failed instead.
    if (
        "permission denied (" in lowered
        or "gssapi" in lowered
        or "authentication failed" in lowered
        or "no supported authentication" in lowered
    ):
        return (
            REMOTE_READ_AUTH_REQUIRED,
            _REMOTE_READ_REASONS[REMOTE_READ_AUTH_REQUIRED],
        )
    if any(
        token in lowered
        for token in (
            "timed out",
            "connection refused",
            "no route to host",
            "could not resolve hostname",
            "unreachable",
            "connection reset",
        )
    ):
        return (
            REMOTE_READ_HOST_UNREACHABLE,
            _REMOTE_READ_REASONS[REMOTE_READ_HOST_UNREACHABLE],
        )
    return REMOTE_READ_UNKNOWN, _REMOTE_READ_REASONS[REMOTE_READ_UNKNOWN]


def _unavailable_read(args: Mapping[str, Any], code: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "source_id": args["source_id"],
        # Kept for compatibility: readers that only know the old code still see
        # an unavailable source rather than a silent success.
        "error": "remote_evidence_unavailable_or_upgrade_required",
        "reason_code": code,
        "reason": reason,
        "coverage": {"discovered": None, "complete": False},
        "rows": [],
    }


def _cached_entry(
    root, host: str, *, identity: str, window_days: int | None
) -> dict[str, Any] | None:
    try:
        entry = _read(_cache_dir(root) / (host + ".json"))
    except (OSError, ValueError, TypeError):
        return None
    if (
        not isinstance(entry, dict)
        or entry.get("schema_version") != MANAGER_REMOTE_EVIDENCE_SCHEMA
        or entry.get("source_host") != host
        or entry.get("scope_identity") != identity
        or (window_days is not None and entry.get("window_days") != window_days)
    ):
        return None
    return entry


def _unavailable_read_with_cached_portfolio(
    root,
    channel: str,
    owner: bool,
    args: Mapping[str, Any],
    code: str,
    reason: str,
) -> dict[str, Any]:
    """Attach the latest authorized portfolio snapshot to a failed point read."""

    result = _unavailable_read(args, code, reason)
    if args.get("view") != "portfolio":
        return result
    host = str(args["source_id"]).removeprefix("ssh:")
    entry = _cached_entry(
        root,
        host,
        identity=_scope_identity(root, channel, owner),
        window_days=None,
    )
    if entry is None:
        return result
    packet = entry.get("packet") if isinstance(entry.get("packet"), dict) else {}
    rows = [row for row in (packet.get("rows") or []) if isinstance(row, dict)]
    goal_id = args.get("goal_id")
    if goal_id:
        rows = [row for row in rows if row.get("goal_id") == goal_id]
    stale_rows = [
        _compact_remote_row(
            row,
            source_id=str(args["source_id"]),
            host=host,
            fresh=False,
        )
        for row in rows
    ]
    return {
        **result,
        "last_success_at": entry.get("read_at"),
        "cached_window_days": entry.get("window_days"),
        "stale_rows_included": bool(stale_rows),
        "stale_portfolio_rows": stale_rows,
        "coverage_effect": REMOTE_SOURCE_STALE_COVERAGE_EFFECT,
        "next_action": REMOTE_SOURCE_FAILURE_NEXT_ACTION,
    }


def _entry_age_seconds(entry: dict[str, Any], now: datetime) -> float | None:
    read_at = entry.get("read_at")
    if not isinstance(read_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(read_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


def _dial_rank(
    source: dict[str, Any], entry: dict[str, Any] | None
) -> tuple[int, int, str, str]:
    """Order one declared source for the single dial a Turn is allowed to make.

    A source that cannot be dialled is ranked last, then a source that has never
    been read, then the oldest successful read. The timestamp is compared as
    text so the ranking stays a pure function of the stored value, and the
    source id breaks ties so the rotation is deterministic rather than a
    function of the order the SSH config happened to declare.
    """

    source_id = str(source["source_id"])
    if source.get("status") == "not_configured":
        return (2, 0, "", source_id)
    read_at = (entry or {}).get("read_at")
    if not isinstance(read_at, str) or not read_at:
        return (0, 0, "", source_id)
    return (1, 0, read_at, source_id)


def _compact_remote_row(row: dict[str, Any], *, source_id: str, host: str, fresh: bool):
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    return {
        "goal_id": row.get("goal_id"),
        "activation_state": row.get("activation_state"),
        "quality": row.get("quality"),
        "progress": row.get("progress"),
        "description": row.get("description"),
        "latest_recorded_at": source.get("latest_recorded_at"),
        "agent_coverage": row.get("agent_coverage"),
        "warnings": [str(item) for item in (row.get("warnings") or [])][:3],
        "source_id": source_id,
        "source_host": host,
        "source_freshness": "current" if fresh else "stale",
    }


def remote_evidence(
    root,
    channel,
    owner,
    *,
    window_days: int,
    scope_valid,
    config_path=None,
    runner=subprocess.run,
    now=None,
    budget_seconds: float = MANAGER_REMOTE_EVIDENCE_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Read every declared source once per TTL window, inside one Turn budget.

    The prompt-only transport cannot call a read tool, so a declared source it
    never receives is a coverage gap the model cannot close. This reads the
    registered sources before the segment starts, reuses a fresh cached read
    instead of dialling again, and declares the typed outcome and freshness of
    every source whether it was read, cached, refused or left for the next Turn.
    """

    now = now or datetime.now(timezone.utc)
    if type(window_days) is not int or not 1 <= window_days <= 90:
        raise ValueError("window_days must be 1..90")
    identity = _scope_identity(root, channel, owner)
    declared = [
        source
        for source in sources(root, channel, owner, config_path)
        if source.get("source_id") != "local"
    ]
    # One Turn dials one host, so *which* source it dials is a rotation decision
    # rather than a declaration order: the source read longest ago -- or never --
    # goes first. Without this, a source listed later would be deferred on every
    # Turn that arrives after the cache TTL and could never be read at all, while
    # the source listed first kept taking the single dial.
    cached: dict[str, dict[str, Any] | None] = {
        str(source["source_id"]): _cached_entry(
            root,
            str(source["source_host"]),
            identity=identity,
            window_days=window_days,
        )
        for source in declared
        if source.get("status") != "not_configured"
    }
    rotation = sorted(
        declared,
        key=lambda source: _dial_rank(
            source, cached.get(str(source["source_id"]))
        ),
    )
    deadline = now.timestamp() + max(0.0, float(budget_seconds))
    host_rows_by_source: dict[str, dict[str, Any]] = {}
    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    limitations: list[str] = []
    stale_rows_included = False
    attempted = 0
    for index, source in enumerate(rotation):
        source_id = str(source["source_id"])
        host = str(source["source_host"])
        if source.get("status") == "not_configured":
            host_rows_by_source[source_id] = {
                "source_id": source_id,
                "source_host": host,
                "status": "not_configured",
                "reason": "ssh_alias_not_configured",
                "fresh": False,
                "next_action": "Register an existing SSH alias for this host and retry.",
            }
            if "declared_source_alias_missing" not in limitations:
                limitations.append("declared_source_alias_missing")
            continue
        entry = cached.get(source_id)
        age = _entry_age_seconds(entry, now) if entry else None
        if entry is not None and age is not None:
            if age <= MANAGER_REMOTE_EVIDENCE_TTL_SECONDS:
                packet = entry.get("packet") or {}
                rows_by_source.setdefault(source_id, []).extend(
                    _compact_remote_row(row, source_id=source_id, host=host, fresh=True)
                    for row in (packet.get("rows") or [])
                    if isinstance(row, dict)
                )
                host_rows_by_source[source_id] = {
                    "source_id": source_id,
                    "source_host": host,
                    "status": "cached",
                    "fresh": True,
                    "read_at": entry.get("read_at"),
                    "age_seconds": int(age),
                    "rows_included": len(packet.get("rows") or []),
                    "coverage": packet.get("coverage"),
                    "next_action": None,
                }
                continue
        remaining = deadline - datetime.now(timezone.utc).timestamp()
        # One dial per Turn keeps the channel responsive: every other declared
        # source keeps its typed status and stays readable on the next Turn.
        if index >= MANAGER_REMOTE_EVIDENCE_MAX_HOSTS or remaining <= 1 or attempted:
            if "remote_source_deferred_to_next_turn" not in limitations:
                limitations.append("remote_source_deferred_to_next_turn")
            host_rows_by_source[source_id] = {
                "source_id": source_id,
                "source_host": host,
                "status": "deferred_budget",
                "fresh": False,
                "reason": "turn_budget",
                "last_success_at": (entry or {}).get("read_at"),
                "next_action": "Ask again in the next Turn or read this source on demand.",
            }
            continue
        attempted += 1
        packet = read_remote(
            root,
            channel,
            owner,
            {
                "source_id": source_id,
                "view": "portfolio",
                "offset": 0,
                "limit": MANAGER_REMOTE_EVIDENCE_ROW_LIMIT,
                "days": window_days,
            },
            scope_valid,
            runner=runner,
            config_path=config_path,
            timeout_seconds=max(
                1.0, min(MANAGER_REMOTE_EVIDENCE_HOST_TIMEOUT_SECONDS, remaining)
            ),
        )
        if packet.get("ok") is True:
            read_at = datetime.now(timezone.utc).isoformat()
            fresh_rows = [
                row for row in (packet.get("rows") or []) if isinstance(row, dict)
            ]
            _write(
                _cache_dir(root) / (host + ".json"),
                {
                    "schema_version": MANAGER_REMOTE_EVIDENCE_SCHEMA,
                    "source_host": host,
                    "scope_identity": identity,
                    "window_days": window_days,
                    "read_at": read_at,
                    "packet": {**packet, "rows": fresh_rows},
                },
            )
            rows_by_source.setdefault(source_id, []).extend(
                _compact_remote_row(row, source_id=source_id, host=host, fresh=True)
                for row in fresh_rows
            )
            host_rows_by_source[source_id] = {
                "source_id": source_id,
                "source_host": host,
                "status": "read",
                "fresh": True,
                "read_at": read_at,
                "age_seconds": 0,
                "rows_included": len(fresh_rows),
                "coverage": packet.get("coverage")
                or (packet.get("source") or {}).get("coverage"),
                "next_action": None,
            }
            continue
        last_success_at = (entry or {}).get("read_at")
        stale_packet = (entry or {}).get("packet") or {}
        stale_rows = [
            row for row in (stale_packet.get("rows") or []) if isinstance(row, dict)
        ]
        if stale_rows:
            stale_rows_included = True
            rows_by_source.setdefault(source_id, []).extend(
                _compact_remote_row(row, source_id=source_id, host=host, fresh=False)
                for row in stale_rows
            )
            if "remote_source_rows_are_stale" not in limitations:
                limitations.append("remote_source_rows_are_stale")
        host_rows_by_source[source_id] = {
            "source_id": source_id,
            "source_host": host,
            "status": "unavailable",
            "fresh": False,
            # Name why the read failed and which side has to change, so an
            # authentication gap is not reported as a dead host or the reverse.
            "reason": str(
                packet.get("reason")
                or packet.get("error")
                or "remote_evidence_unavailable_or_upgrade_required"
            ),
            "reason_code": str(packet.get("reason_code") or "") or None,
            "last_success_at": last_success_at,
            "stale_rows_included": bool(stale_rows),
            "coverage_effect": REMOTE_SOURCE_STALE_COVERAGE_EFFECT,
            "next_action": REMOTE_SOURCE_FAILURE_NEXT_ACTION,
        }
        limitation = _REMOTE_READ_LIMITATIONS.get(
            str(packet.get("reason_code") or ""), "remote_source_unavailable"
        )
        if limitation not in limitations:
            limitations.append(limitation)
    # Report in declaration order: the rotation chooses which source is dialled,
    # not how the packet reads.
    host_rows = [
        host_rows_by_source[str(source["source_id"])]
        for source in declared
        if str(source["source_id"]) in host_rows_by_source
    ]
    rows = [
        row
        for source in declared
        for row in rows_by_source.get(str(source["source_id"]), [])
    ]
    statuses = {str(row.get("status")) for row in host_rows}
    if not declared:
        read_status = "not_read"
    elif statuses <= {"read", "cached"}:
        read_status = "read"
    elif statuses & {"read", "cached"}:
        read_status = "partial"
    elif "unavailable" in statuses:
        read_status = "unavailable"
    else:
        read_status = "not_read"
    return {
        "schema_version": MANAGER_REMOTE_EVIDENCE_SCHEMA,
        "applies_to": "declared_ssh_sources",
        "window_days": window_days,
        "read_status": read_status,
        "ttl_seconds": MANAGER_REMOTE_EVIDENCE_TTL_SECONDS,
        "budget": {
            "max_hosts": MANAGER_REMOTE_EVIDENCE_MAX_HOSTS,
            "total_seconds": budget_seconds,
            "per_host_seconds": MANAGER_REMOTE_EVIDENCE_HOST_TIMEOUT_SECONDS,
            "row_limit": MANAGER_REMOTE_EVIDENCE_ROW_LIMIT,
        },
        "declared_source_count": len(declared),
        "sources": host_rows,
        "rows": rows[: MANAGER_REMOTE_EVIDENCE_ROW_LIMIT * MANAGER_REMOTE_EVIDENCE_MAX_HOSTS],
        "stale_rows_included": stale_rows_included,
        "limitations": limitations,
    }
