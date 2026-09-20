"""Preview-locked Codex child-thread capacity alignment.

LoopX owns the desired Goal parallelism. Codex owns the host-side child-thread
limit. This adapter keeps those authorities separate: reads always expose a
plan, and writes happen only after an explicit alignment request. A write may
raise the host limit but never lowers an existing higher value.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any

from ...file_lock import exclusive_file_lock


CODEX_HOST_CAPACITY_SCHEMA_VERSION = "codex_subagent_host_capacity_v0"
CANONICAL_LIMIT_KEY = "max_concurrent_threads_per_session"
LEGACY_LIMIT_KEY = "max_threads"
_ASSIGNMENT_PATTERN = re.compile(
    rf"^(?P<indent>\s*)(?P<key>{CANONICAL_LIMIT_KEY}|{LEGACY_LIMIT_KEY})"
    r"(?P<separator>\s*=\s*)(?P<value>[^#\r\n]*)(?P<comment>\s*(?:#.*)?)$"
)
_TABLE_PATTERN = re.compile(r"^\s*\[([^\[\]]+)]\s*(?:#.*)?$")
_PUBLIC_CAPACITY_FIELDS = {
    "action",
    "alignment_requested",
    "canonical_config_key",
    "configured_children",
    "configured_source_key",
    "counts_main_thread",
    "host",
    "legacy_alias_present",
    "never_lower",
    "new_session_required",
    "new_session_required_after_write",
    "previous_configured_children",
    "readback_verified",
    "reason",
    "required_children",
    "schema_version",
    "status",
    "write_required",
    "written",
}


def _sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def public_codex_host_capacity(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a path-free receipt for browser and remote presentation."""

    capacity = payload.get("codex_host_capacity")
    if not isinstance(capacity, dict):
        return {}
    return {
        key: capacity[key]
        for key in _PUBLIC_CAPACITY_FIELDS
        if key in capacity
    }


def _codex_home() -> Path:
    """Match the LoopX managed Chat runtime's host-store precedence."""

    return Path(
        os.environ.get("LOOPX_CHAT_CODEX_HOME")
        or os.environ.get("CODEX_HOME")
        or Path.home() / ".codex"
    ).expanduser()


def _limit(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Codex agents.{field} must be a positive integer")
    return int(value)


def _read_source(config_path: Path) -> tuple[str, dict[str, Any]]:
    if config_path.is_symlink():
        raise ValueError("symlinked Codex config files are not supported")
    if not config_path.exists():
        return "", {}
    source = config_path.read_text(encoding="utf-8")
    if len(source.encode("utf-8")) > 2_000_000:
        raise ValueError("Codex config exceeds the supported alignment size")
    parsed = tomllib.loads(source)
    if not isinstance(parsed, dict):
        raise ValueError("Codex config must contain a TOML table")
    return source, parsed


def _configured_limits(parsed: dict[str, Any]) -> tuple[int | None, int | None]:
    agents = parsed.get("agents")
    if agents is None:
        return None, None
    if not isinstance(agents, dict):
        raise ValueError("Codex [agents] must be a TOML table")
    return (
        _limit(agents.get(CANONICAL_LIMIT_KEY), field=CANONICAL_LIMIT_KEY),
        _limit(agents.get(LEGACY_LIMIT_KEY), field=LEGACY_LIMIT_KEY),
    )


def plan_codex_subagent_capacity(
    required_children: int,
    *,
    home: Path | None = None,
) -> dict[str, Any]:
    """Return a public-safe plan without assuming Codex's implicit default."""

    if isinstance(required_children, bool) or not isinstance(required_children, int):
        raise ValueError("required Codex child-thread capacity must be an integer")
    if required_children < 0:
        raise ValueError("required Codex child-thread capacity cannot be negative")
    resolved_home = (home or _codex_home()).expanduser().resolve()
    config_path = resolved_home / "config.toml"
    if required_children == 0:
        return {
            "schema_version": CODEX_HOST_CAPACITY_SCHEMA_VERSION,
            "host": "codex",
            "status": "not_required",
            "action": "none",
            "required_children": 0,
            "configured_children": None,
            "configured_source_key": None,
            "canonical_config_key": f"agents.{CANONICAL_LIMIT_KEY}",
            "legacy_alias_present": False,
            "counts_main_thread": False,
            "write_required": False,
            "never_lower": True,
            "config_path": str(config_path),
            "source_sha256": None,
            "new_session_required_after_write": False,
            "reason": "Goal sub-agents are disabled",
        }
    source, parsed = _read_source(config_path)
    canonical, legacy = _configured_limits(parsed)
    # Codex gives the canonical spelling precedence when both it and the legacy
    # alias are present. Preserve that runtime meaning in the preview; apply
    # still keeps the larger textual value when it collapses both keys into the
    # canonical spelling.
    configured = canonical if canonical is not None else legacy
    source_key = (
        CANONICAL_LIMIT_KEY
        if canonical is not None
        else LEGACY_LIMIT_KEY
        if legacy is not None
        else None
    )
    if configured is None:
        status = "implicit_default_unknown"
        action = "set_explicit_limit"
    elif configured < required_children:
        status = "explicit_shortfall"
        action = "raise_explicit_limit"
    else:
        status = "explicit_sufficient"
        action = "none"
    return {
        "schema_version": CODEX_HOST_CAPACITY_SCHEMA_VERSION,
        "host": "codex",
        "status": status,
        "action": action,
        "required_children": required_children,
        "configured_children": configured,
        "configured_source_key": source_key,
        "canonical_config_key": f"agents.{CANONICAL_LIMIT_KEY}",
        "legacy_alias_present": legacy is not None,
        "counts_main_thread": False,
        "write_required": action != "none",
        "never_lower": True,
        "config_path": str(config_path),
        "source_sha256": _sha256(source),
        "new_session_required_after_write": action != "none",
        "reason": (
            "Codex has no explicit child-thread limit; its implicit default is not guessed"
            if status == "implicit_default_unknown"
            else "Codex's explicit child-thread limit is below the Goal maximum"
            if status == "explicit_shortfall"
            else "Codex's explicit child-thread limit already satisfies the Goal maximum"
        ),
    }


def _desired_document(parsed: dict[str, Any], target: int) -> dict[str, Any]:
    desired = deepcopy(parsed)
    agents = desired.setdefault("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("Codex [agents] must be a TOML table")
    agents.pop(LEGACY_LIMIT_KEY, None)
    agents[CANONICAL_LIMIT_KEY] = target
    return desired


def _replace_capacity(source: str, target: int) -> str:
    """Preserve unrelated TOML text and prove the exact semantic delta."""

    parsed = tomllib.loads(source) if source else {}
    desired = _desired_document(parsed, target)
    lines = source.splitlines(keepends=True)
    agents_headers = [
        index
        for index, line in enumerate(lines)
        if (match := _TABLE_PATTERN.match(line.rstrip("\r\n")))
        and match.group(1).strip() == "agents"
    ]

    candidates: list[str] = []
    for header in agents_headers:
        end = len(lines)
        for index in range(header + 1, len(lines)):
            if _TABLE_PATTERN.match(lines[index].rstrip("\r\n")):
                end = index
                break
        assignments = [
            index
            for index in range(header + 1, end)
            if _ASSIGNMENT_PATTERN.match(lines[index].rstrip("\r\n"))
        ]
        canonical_assignments = []
        for index in assignments:
            assignment = _ASSIGNMENT_PATTERN.match(lines[index].rstrip("\r\n"))
            if assignment is not None and assignment.group("key") == CANONICAL_LIMIT_KEY:
                canonical_assignments.append(index)
        primary = canonical_assignments[0] if canonical_assignments else (
            assignments[0] if assignments else None
        )
        candidate_lines = list(lines)
        replacement = f"{CANONICAL_LIMIT_KEY} = {target}\n"
        if primary is None:
            candidate_lines.insert(header + 1, replacement)
        else:
            match = _ASSIGNMENT_PATTERN.match(
                candidate_lines[primary].rstrip("\r\n")
            )
            assert match is not None
            newline = "\r\n" if candidate_lines[primary].endswith("\r\n") else "\n"
            comment = match.group("comment")
            if comment and not comment.startswith((" ", "\t")):
                comment = " " + comment
            candidate_lines[primary] = (
                f"{match.group('indent')}{CANONICAL_LIMIT_KEY}"
                f"{match.group('separator')}{target}{comment}{newline}"
            )
            for duplicate in reversed(assignments):
                if duplicate != primary:
                    del candidate_lines[duplicate]
        candidates.append("".join(candidate_lines))

    suffix = "" if not source or source.endswith(("\n", "\r")) else "\n"
    candidates.append(
        source
        + suffix
        + ("" if not source else "\n")
        + f"[agents]\n{CANONICAL_LIMIT_KEY} = {target}\n"
    )
    for candidate in candidates:
        try:
            if tomllib.loads(candidate) == desired:
                return candidate
        except tomllib.TOMLDecodeError:
            continue
    raise ValueError(
        "unsupported Codex [agents] TOML layout; no configuration was written"
    )


def _atomic_write(path: Path, source: str, *, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def apply_codex_subagent_capacity(
    required_children: int,
    *,
    expected_source_sha256: str,
    home: Path | None = None,
) -> dict[str, Any]:
    """Raise and verify the Codex child-thread limit; never lower it."""

    initial = plan_codex_subagent_capacity(required_children, home=home)
    config_path = Path(initial["config_path"])
    with exclusive_file_lock(
        config_path,
        operation="align_codex_subagent_capacity",
    ):
        current = plan_codex_subagent_capacity(required_children, home=config_path.parent)
        if current["source_sha256"] != expected_source_sha256:
            raise ValueError(
                "Codex config changed after preview; preview capacity alignment again"
            )
        if not current["write_required"]:
            return {
                **current,
                "status": "already_sufficient",
                "written": False,
                "readback_verified": True,
                "backup_path": None,
                "new_session_required": False,
            }
        source, parsed = _read_source(config_path)
        canonical, legacy = _configured_limits(parsed)
        target = max(
            [
                required_children,
                *[value for value in (canonical, legacy) if value is not None],
            ]
        )
        replacement = _replace_capacity(source, target)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = None
        mode = config_path.stat().st_mode & 0o777 if config_path.exists() else 0o600
        if config_path.exists():
            backup_path = config_path.with_name(
                f"{config_path.name}.before-loopx-capacity-{stamp}.bak"
            )
            _atomic_write(backup_path, source, mode=0o600)
        _atomic_write(config_path, replacement, mode=mode)
        readback = plan_codex_subagent_capacity(required_children, home=config_path.parent)
        verified = (
            readback["status"] == "explicit_sufficient"
            and readback["configured_children"] == target
            and readback["configured_source_key"] == CANONICAL_LIMIT_KEY
            and not readback["legacy_alias_present"]
        )
        if not verified:
            raise ValueError(
                "Codex capacity write did not pass exact readback; inspect the retained backup"
            )
        return {
            **readback,
            "status": "updated",
            "written": True,
            "readback_verified": True,
            "backup_path": str(backup_path) if backup_path else None,
            "new_session_required": True,
            "previous_configured_children": current["configured_children"],
        }
