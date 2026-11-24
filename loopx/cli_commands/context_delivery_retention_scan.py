"""Low-level ownership verification, chunked reference scan, and checkpoint IO."""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
from typing import Protocol

from ..control_plane.content_digest import BARE_SHA256_PATTERN

CONTEXT_DELIVERY_EPHEMERAL_PREFIXES: tuple[str, ...] = (
    "decision-",
    "recovery-",
    "settlement-",
    "context-",
)

_KNOWN_CAPTURE_DATA_FILENAMES: frozenset[str] = frozenset(
    {
        "decision.json",
        "delivery.json",
        "delivery-request.json",
        "failure.json",
        "projection.json",
        "required-context.json",
        "result.json",
        "settlement.json",
    }
)
_CONTINUITY_FILENAMES: tuple[str, ...] = ("session.json", "claim-source.json")
_DECISION_OWNERSHIP_KEYS: tuple[str, ...] = (
    "decision",
    "effective_action",
    "heartbeat_receipt",
    "should_run",
    "goal_id",
)
_MAX_CAPTURE_JSON_BYTES = 64 * 1024 * 1024
_REF_SCAN_CHUNK_BYTES = 64 * 1024
_REF_SCAN_OVERLAP_MIN_BYTES = 256

_CAPTURE_REFERENCE_LEAF_KEYS: frozenset[str] = frozenset(
    {
        "path", "capture_path", "capture_file", "decision_capture_path",
        "decision_file", "reference_file", "source_path", "baseline_path",
        "recovery_path", "replan_read_path", "captured_decision_path",
        "context_path", "delivery_path", "result_path",
        "command", "first_read_command", "remaining_read_command",
        "index_command", "select_command", "read_command", "recovery_command",
        "next_command", "command_template", "selection_command_args_template",
        "command_args_template", "ready_context_command_template",
        "full_decision", "full_packet",
        "sha256", "capture_sha256", "source_sha256", "claim_source_sha256",
        "digest", "capture_digest", "selection_sha256", "baseline_sha256",
    }
)
_CAPTURE_REFERENCE_CONTAINER_KEYS: frozenset[str] = frozenset(
    {
        "baseline", "baseline_reference", "candidate", "capture",
        "captured_decision", "captured_recovery", "cli_channel",
        "comparison_base", "context_details", "context_recovery", "detail_ref",
        "execution_preparation", "first_read", "host_execution_preparation",
        "interaction_contract", "navigation", "observed_first_read",
        "pending", "ready_capture", "recovery", "references", "required",
        "result_details", "selection", "selection_command", "session",
        "source_recovery",
    }
)


class _JsonObjectReader(Protocol):
    def __call__(
        self,
        path: Path,
        *,
        uid: int | None,
        max_bytes: int = ...,
    ) -> tuple[dict[str, object], bytes] | None: ...


@dataclass(frozen=True, slots=True)
class _ShallowDirectory:
    mtime: float
    mtime_ns: int
    name: str
    path: Path
    prefix: str


@dataclass(frozen=True, slots=True)
class _MeasuredEntry:
    name: str
    path: Path
    is_reader: bool
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class _AdmissionIdentity:
    runtime_root: str | None
    goal_id: str | None
    turn_instance_id: str | None
    agent_id: str | None
    todo_id: str | None
    replan_obligation_id: str | None


@dataclass(frozen=True, slots=True)
class _CandidateDirectory:
    mtime: float
    mtime_ns: int
    name: str
    path: Path
    prefix: str
    data_digests: frozenset[str]
    reader_digests: frozenset[str]
    admissions: tuple[_AdmissionIdentity, ...]
    referenced_names: frozenset[str]
    referenced_digests: frozenset[str]
    byte_size: int


def _matching_ephemeral_prefix(name: str) -> str | None:
    for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
        if name.startswith(prefix):
            return prefix
    return None


def _non_empty_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _expand_user_safe(raw_or_path: str | Path) -> Path | None:
    """Resolve leading ``~`` home references only, avoiding ``getpwnam`` on prose."""
    raw = str(raw_or_path)
    if not raw:
        return None
    try:
        path = raw_or_path if isinstance(raw_or_path, Path) else Path(raw)
        if raw == "~" or raw.startswith(("~/", "~\\")):
            path = path.expanduser()
        return path.absolute()
    except (OSError, RuntimeError, ValueError):
        return None


def _resolved_variants(root: Path) -> tuple[Path, Path]:
    absolute = _expand_user_safe(root) or root.absolute()
    try:
        resolved = absolute.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        resolved = absolute
    return absolute, resolved


def _read_regular_json_object(
    path: Path,
    *,
    uid: int | None,
    max_bytes: int = _MAX_CAPTURE_JSON_BYTES,
) -> tuple[dict[str, object], bytes] | None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            if uid is not None and info.st_uid != uid:
                return None
            if info.st_size <= 0 or info.st_size > max_bytes:
                return None
            raw = stream.read(max_bytes + 1)
    except OSError:
        return None
    if len(raw) > max_bytes:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed, raw


def _is_reader_filename(name: str) -> bool:
    if name.endswith(".reader.json"):
        return len(name) > len(".reader.json")
    stem, sep, suffix = name.partition(".reader.json.")
    return (
        bool(sep)
        and bool(stem)
        and len(suffix) == 16
        and all(ch in "0123456789abcdef" for ch in suffix)
    )


def _reader_reference_is_valid(
    reader_payload: Mapping[str, object],
    *,
    candidate_resolved: Path,
    data_filenames: frozenset[str],
) -> bool:
    if reader_payload.get("schema_version") != "context_capture_read_reference_v1":
        return False
    digest = reader_payload.get("sha256")
    if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None:
        return False
    raw_target = reader_payload.get("path")
    if not isinstance(raw_target, str) or not raw_target.strip():
        return False
    target = _expand_user_safe(raw_target)
    if target is None:
        return False
    try:
        if target.is_symlink():
            return False
        resolved_target = target.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    return (
        resolved_target.parent == candidate_resolved
        and resolved_target.name in data_filenames
    )


def _prefix_ownership_summary_matches(
    prefix: str,
    *,
    data_flags: Mapping[str, tuple[bool, bool, bool]],
    has_verified_reader: bool,
) -> bool:
    """Verify capture prefix ownership using compact per-file boolean summaries."""
    if prefix == "decision-":
        decision = data_flags.get("decision.json")
        if decision is not None:
            _, has_decision_keys, _ = decision
            return has_decision_keys or has_verified_reader
        failure = data_flags.get("failure.json")
        return failure is not None and failure[0] and has_verified_reader
    if prefix == "recovery-":
        required = data_flags.get("required-context.json")
        return required is not None and required[0]
    if prefix == "settlement-":
        return any(
            (flags := data_flags.get(name)) is not None and flags[0]
            for name in ("result.json", "settlement.json")
        )
    if prefix == "context-":
        if any(
            (flags := data_flags.get(name)) is not None and flags[0]
            for name in ("projection.json", "required-context.json")
        ):
            return True
        delivery = data_flags.get("delivery.json")
        if delivery is not None and delivery[0]:
            _, has_decision_keys, is_delivery_v1 = delivery
            return has_verified_reader or is_delivery_v1 or has_decision_keys
        return False
    return False


def _extract_dir_name_from_path_token(
    token: str,
    *,
    abs_root: Path,
    resolved_root: Path,
) -> str | None:
    if not token:
        return None
    base = _expand_user_safe(token)
    if base is None:
        return None
    resolved_targets: list[Path] = [base]
    try:
        resolved_targets.append(base.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        pass
    for target in resolved_targets:
        for base_root in (resolved_root, abs_root):
            try:
                rel = target.relative_to(base_root)
            except ValueError:
                continue
            if rel.parts and _matching_ephemeral_prefix(rel.parts[0]) is not None:
                return rel.parts[0]
    return None


def _extract_from_leaf_string(
    raw: str,
    *,
    names: set[str],
    digests: set[str],
    abs_root: Path,
    resolved_root: Path,
) -> None:
    text = raw.strip()
    if not text:
        return
    if BARE_SHA256_PATTERN.fullmatch(text) is not None:
        digests.add(text)
        return
    direct = _extract_dir_name_from_path_token(
        text, abs_root=abs_root, resolved_root=resolved_root
    )
    if direct is not None:
        names.add(direct)
    if any(sep in text for sep in (" ", "\t", "\n")):
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()
        for token in tokens:
            if BARE_SHA256_PATTERN.fullmatch(token) is not None:
                digests.add(token)
                continue
            found = _extract_dir_name_from_path_token(
                token, abs_root=abs_root, resolved_root=resolved_root
            )
            if found is not None:
                names.add(found)


def _extract_capture_names_and_digests(
    value: object,
    *,
    abs_root: Path,
    resolved_root: Path,
) -> tuple[set[str], set[str]]:
    """Extract capture directory names and digests from reference/path/command fields."""
    names: set[str] = set()
    digests: set[str] = set()
    if not isinstance(value, Mapping):
        return names, digests
    stack: list[tuple[Mapping[object, object], bool]] = [(value, False)]
    while stack:
        current, in_detail_replan = stack.pop()
        for key, child in current.items():
            if not isinstance(key, str):
                continue
            is_leaf = key in _CAPTURE_REFERENCE_LEAF_KEYS or (
                in_detail_replan and key in ("review", "writeback")
            )
            if is_leaf:
                if isinstance(child, str):
                    _extract_from_leaf_string(
                        child, names=names, digests=digests,
                        abs_root=abs_root, resolved_root=resolved_root,
                    )
                elif isinstance(child, (list, tuple)):
                    for item in child:
                        if isinstance(item, str):
                            _extract_from_leaf_string(
                                item, names=names, digests=digests,
                                abs_root=abs_root, resolved_root=resolved_root,
                            )
                if not in_detail_replan:
                    continue
            is_detail_ref = key == "detail_ref"
            in_container = key in _CAPTURE_REFERENCE_CONTAINER_KEYS or (
                in_detail_replan and key in ("replan_action_packet", "review", "writeback")
            )
            if not in_container:
                continue
            next_detail_replan = is_detail_ref or (
                in_detail_replan and key in ("replan_action_packet", "review", "writeback")
            )
            if isinstance(child, Mapping):
                stack.append((child, next_detail_replan))
            elif isinstance(child, (list, tuple)):
                for item in child:
                    if isinstance(item, Mapping):
                        stack.append((item, next_detail_replan))
    return names, digests


def _has_admission_receipt(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    receipt = payload.get("heartbeat_receipt")
    if not isinstance(receipt, Mapping):
        return None
    if (
        _non_empty_str(receipt.get("event_id")) is not None
        or _non_empty_str(receipt.get("turn_instance_id")) is not None
        or receipt.get("settlement_binding_owed") is True
        or receipt.get("closeout_required") is True
    ):
        return receipt
    return None


def _extract_admission_identity(payload: Mapping[str, object]) -> _AdmissionIdentity | None:
    receipt = _has_admission_receipt(payload)
    if receipt is None:
        return None
    settlement_identity = receipt.get("settlement_identity")
    identity_map = settlement_identity if isinstance(settlement_identity, Mapping) else {}
    agent_identity = payload.get("agent_identity")
    agent_map = agent_identity if isinstance(agent_identity, Mapping) else {}
    selected_todo = payload.get("selected_todo")
    todo_map = selected_todo if isinstance(selected_todo, Mapping) else {}
    replan_obligation = payload.get("autonomous_replan_obligation")
    replan_map = replan_obligation if isinstance(replan_obligation, Mapping) else {}
    return _AdmissionIdentity(
        runtime_root=_non_empty_str(payload.get("runtime_root")),
        goal_id=_non_empty_str(payload.get("goal_id"))
        or _non_empty_str(identity_map.get("goal_id")),
        turn_instance_id=_non_empty_str(receipt.get("turn_instance_id"))
        or _non_empty_str(identity_map.get("turn_instance_id"))
        or _non_empty_str(payload.get("turn_instance_id")),
        agent_id=_non_empty_str(agent_map.get("agent_id"))
        or _non_empty_str(identity_map.get("agent_id"))
        or _non_empty_str(payload.get("agent_id")),
        todo_id=_non_empty_str(todo_map.get("todo_id"))
        or _non_empty_str(identity_map.get("todo_id"))
        or _non_empty_str(payload.get("todo_id")),
        replan_obligation_id=_non_empty_str(identity_map.get("replan_obligation_id"))
        or _non_empty_str(replan_map.get("obligation_id")),
    )


def _is_admission_settled(
    admission: _AdmissionIdentity,
    *,
    runtime_root: Path | None,
    settlement_cache: dict[tuple[str, str, str, str], bool],
) -> bool:
    if runtime_root is None or admission.runtime_root is None:
        return False
    payload_runtime = _expand_user_safe(admission.runtime_root)
    target_runtime = _expand_user_safe(runtime_root)
    if payload_runtime is None or target_runtime is None:
        return False
    try:
        resolved_payload = payload_runtime.resolve()
        resolved_target = target_runtime.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if resolved_payload != resolved_target:
        return False
    if admission.goal_id is None or admission.turn_instance_id is None:
        return False

    cache_key = (
        admission.goal_id,
        admission.turn_instance_id,
        admission.agent_id or "",
        admission.todo_id or "",
    )
    cached = settlement_cache.get(cache_key)
    if cached is not None:
        return cached

    from ..control_plane.quota.settlement import read_heartbeat_settlement

    try:
        readback = read_heartbeat_settlement(
            resolved_target,
            goal_id=admission.goal_id,
            agent_id=admission.agent_id,
            todo_id=admission.todo_id,
            turn_instance_id=admission.turn_instance_id,
            replan_obligation_id=admission.replan_obligation_id,
            include_record_bodies=False,
            tolerate_malformed_history=True,
        )
    except Exception:
        settlement_cache[cache_key] = False
        return False
    if readback is None:
        settled = False
    else:
        progress_state = (
            readback.progress.get("state")
            if isinstance(readback.progress, Mapping)
            else None
        )
        settled = progress_state == "settled" or (
            progress_state is None and str(readback.replay_phase) == "settled"
        )
    settlement_cache[cache_key] = settled
    return settled


def _candidate_has_unsettled_admission(
    candidate: _CandidateDirectory,
    *,
    runtime_root: Path | None,
    settlement_cache: dict[tuple[str, str, str, str], bool],
) -> bool:
    return any(
        not _is_admission_settled(
            admission,
            runtime_root=runtime_root,
            settlement_cache=settlement_cache,
        )
        for admission in candidate.admissions
    )


def _measure_candidate_dir(
    candidate_path: Path,
    *,
    uid: int | None,
) -> tuple[list[_MeasuredEntry], int, int] | None:
    """Stat candidate capture files and compute byte/mtime metadata without reading JSON."""
    entries_out: list[_MeasuredEntry] = []
    has_data = False
    total_bytes = 0
    max_file_mtime_ns = 0
    try:
        with os.scandir(candidate_path) as entries:
            for entry in entries:
                entry_name = entry.name
                is_data = entry_name in _KNOWN_CAPTURE_DATA_FILENAMES
                is_reader = _is_reader_filename(entry_name)
                if not is_data and not is_reader:
                    return None
                try:
                    if entry.is_symlink():
                        return None
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    return None
                if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(
                    entry_stat.st_mode
                ):
                    return None
                if uid is not None and entry_stat.st_uid != uid:
                    return None
                if (
                    entry_stat.st_size <= 0
                    or entry_stat.st_size > _MAX_CAPTURE_JSON_BYTES
                ):
                    return None
                if is_data:
                    has_data = True
                total_bytes += entry_stat.st_size
                if entry_stat.st_mtime_ns > max_file_mtime_ns:
                    max_file_mtime_ns = entry_stat.st_mtime_ns
                entries_out.append(
                    _MeasuredEntry(
                        name=entry_name,
                        path=Path(entry.path),
                        is_reader=is_reader,
                        size=entry_stat.st_size,
                        mtime_ns=entry_stat.st_mtime_ns,
                    )
                )
    except OSError:
        return None
    if not has_data:
        return None
    return entries_out, total_bytes, max_file_mtime_ns


def _dir_witness(
    dir_mtime_ns: int,
    entries: list[_MeasuredEntry],
    total_bytes: int,
    max_file_mtime_ns: int,
) -> tuple[int, int, int, int]:
    return (dir_mtime_ns, len(entries), total_bytes, max_file_mtime_ns)


def _inspect_owned_capture_dir(
    candidate_path: Path,
    *,
    name: str,
    prefix: str,
    mtime: float,
    mtime_ns: int = 0,
    uid: int | None,
    abs_root: Path | None = None,
    resolved_root: Path | None = None,
    measured: tuple[list[_MeasuredEntry], int] | None = None,
    read_json: _JsonObjectReader = _read_regular_json_object,
) -> _CandidateDirectory | None:
    """Verify strict LoopX capture ownership and release JSON dicts immediately."""
    try:
        candidate_resolved = candidate_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if measured is None:
        measured_full = _measure_candidate_dir(candidate_path, uid=uid)
        if measured_full is None:
            return None
        measured_entries, total_bytes, _ = measured_full
    else:
        measured_entries, total_bytes = measured
    if abs_root is None or resolved_root is None:
        abs_root, resolved_root = _resolved_variants(candidate_path.parent)

    data_flags: dict[str, tuple[bool, bool, bool]] = {}
    reader_digests: set[str] = set()
    reader_meta: list[tuple[bool, object, object]] = []
    data_digests: set[str] = set()
    admissions: list[_AdmissionIdentity] = []
    referenced_names: set[str] = set()
    referenced_digests: set[str] = set()

    for item in measured_entries:
        loaded = read_json(item.path, uid=uid)
        if loaded is None:
            return None
        parsed, raw = loaded
        found_names, found_digests = _extract_capture_names_and_digests(
            parsed, abs_root=abs_root, resolved_root=resolved_root
        )
        referenced_names.update(found_names)
        referenced_digests.update(found_digests)
        if item.is_reader:
            sha = parsed.get("sha256")
            if isinstance(sha, str) and BARE_SHA256_PATTERN.fullmatch(sha) is not None:
                reader_digests.add(sha)
            reader_meta.append(
                (
                    parsed.get("schema_version") == "context_capture_read_reference_v1",
                    parsed.get("sha256"),
                    parsed.get("path"),
                )
            )
        else:
            data_flags[item.name] = (
                bool(parsed),
                any(key in parsed for key in _DECISION_OWNERSHIP_KEYS),
                parsed.get("schema_version") == "context_delivery_v1",
            )
            data_digests.add(hashlib.sha256(raw).hexdigest())
            identity = _extract_admission_identity(parsed)
            if identity is not None:
                admissions.append(identity)

    data_names = frozenset(data_flags)
    for valid_schema, digest_val, raw_target in reader_meta:
        reader_stub: dict[str, object] = {
            "schema_version": "context_capture_read_reference_v1" if valid_schema else "",
            "sha256": digest_val,
            "path": raw_target,
        }
        if not _reader_reference_is_valid(
            reader_stub, candidate_resolved=candidate_resolved, data_filenames=data_names
        ):
            return None
    if not _prefix_ownership_summary_matches(
        prefix, data_flags=data_flags, has_verified_reader=bool(reader_meta)
    ):
        return None
    return _CandidateDirectory(
        mtime=mtime, mtime_ns=mtime_ns, name=name, path=candidate_path, prefix=prefix,
        data_digests=frozenset(data_digests), reader_digests=frozenset(reader_digests),
        admissions=tuple(admissions), referenced_names=frozenset(referenced_names),
        referenced_digests=frozenset(referenced_digests), byte_size=total_bytes,
    )


def _collect_active_continuity_references(
    non_ephemeral_dirs: list[Path],
    *,
    uid: int | None,
    abs_root: Path,
    resolved_root: Path,
    max_scan_bytes: int,
    read_json: _JsonObjectReader = _read_regular_json_object,
) -> tuple[set[str], set[str], int] | None:
    """Read active continuity JSONs under a strict byte budget; return None to fail closed."""
    protected: set[str] = set()
    active_digests: set[str] = set()
    bytes_used = 0
    for subdir in non_ephemeral_dirs:
        for filename in _CONTINUITY_FILENAMES:
            path = subdir / filename
            try:
                file_stat = os.lstat(path)
            except FileNotFoundError:
                continue
            except OSError:
                return None
            if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
                return None
            if uid is not None and file_stat.st_uid != uid:
                return None
            if file_stat.st_size <= 0 or file_stat.st_size > _MAX_CAPTURE_JSON_BYTES:
                return None
            if bytes_used + file_stat.st_size > max_scan_bytes:
                return None
            loaded = read_json(path, uid=uid)
            if loaded is None:
                return None
            bytes_used += file_stat.st_size
            parsed, _ = loaded
            found_names, found_digests = _extract_capture_names_and_digests(
                parsed, abs_root=abs_root, resolved_root=resolved_root
            )
            protected.update(found_names)
            active_digests.update(found_digests)
    return protected, active_digests, bytes_used


def _file_contains_any_needle(
    path: Path,
    *,
    needles: tuple[bytes, ...],
    overlap: int,
    uid: int | None,
    expected_size: int,
    budget_remaining: int,
) -> tuple[str, int]:
    """Stream a regular file in 64 KiB chunks; fail closed on unreadable or budget overrun."""
    if expected_size > budget_remaining:
        return "budget_exceeded", 0
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError:
        return "unreadable", 0
    bytes_read = 0
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return "not_capture", 0
            if uid is not None and info.st_uid != uid:
                return "not_capture", 0
            if info.st_size <= 0 or info.st_size > _MAX_CAPTURE_JSON_BYTES:
                return "not_capture", 0
            if info.st_size > budget_remaining:
                return "budget_exceeded", 0
            tail = b""
            while True:
                chunk = stream.read(_REF_SCAN_CHUNK_BYTES)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > budget_remaining:
                    return "budget_exceeded", bytes_read
                window = tail + chunk
                if any(needle in window for needle in needles):
                    return "match", bytes_read
                tail = window[-overlap:] if overlap > 0 else b""
            return "miss", bytes_read
    except OSError:
        return "unreadable", bytes_read


def _scan_dir_entries_for_needles(
    entries: list[_MeasuredEntry],
    *,
    needles: tuple[bytes, ...],
    overlap: int,
    uid: int | None,
    budget_remaining: int,
) -> tuple[str, int]:
    scanned_bytes = 0
    for item in entries:
        file_status, used = _file_contains_any_needle(
            item.path, needles=needles, overlap=overlap, uid=uid,
            expected_size=item.size, budget_remaining=budget_remaining - scanned_bytes,
        )
        scanned_bytes += used
        if file_status != "miss":
            return file_status, scanned_bytes
    return "miss", scanned_bytes


def _collect_root_entries(
    root: Path,
    *,
    uid: int | None,
) -> tuple[dict[str, list[_ShallowDirectory]], list[Path], int] | None:
    by_prefix: dict[str, list[_ShallowDirectory]] = {
        prefix: [] for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES
    }
    non_ephemeral_dirs: list[Path] = []
    ephemeral_count = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                name = entry.name
                try:
                    if entry.is_symlink():
                        continue
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(
                    entry_stat.st_mode
                ):
                    continue
                if uid is not None and entry_stat.st_uid != uid:
                    continue
                prefix = _matching_ephemeral_prefix(name)
                if prefix is None:
                    non_ephemeral_dirs.append(Path(entry.path))
                    continue
                ephemeral_count += 1
                by_prefix[prefix].append(
                    _ShallowDirectory(
                        mtime=entry_stat.st_mtime,
                        mtime_ns=entry_stat.st_mtime_ns,
                        name=name,
                        path=Path(entry.path),
                        prefix=prefix,
                    )
                )
    except OSError:
        return None
    return by_prefix, non_ephemeral_dirs, ephemeral_count


def _expand_protected_closure(
    seed_names: set[str],
    *,
    owned_by_name: Mapping[str, _CandidateDirectory],
) -> set[str]:
    protected = set(seed_names)
    queue: deque[str] = deque(seed_names)
    while queue:
        current = queue.popleft()
        candidate = owned_by_name.get(current)
        if candidate is None:
            continue
        for ref_name in candidate.referenced_names:
            if ref_name not in protected:
                protected.add(ref_name)
                queue.append(ref_name)
    return protected


def _partition_shallow_candidates(
    shallow_by_prefix: Mapping[str, list[_ShallowDirectory]],
    *,
    protected_names: frozenset[str] | set[str],
    current_now: float,
    min_age_seconds: float,
    keep_recent_per_prefix: int,
) -> tuple[list[_ShallowDirectory], list[_ShallowDirectory]]:
    cutoff = current_now - min_age_seconds
    aged_shallow: list[_ShallowDirectory] = []
    kept_shallow: list[_ShallowDirectory] = []
    for prefix in CONTEXT_DELIVERY_EPHEMERAL_PREFIXES:
        kept_shallow.extend(
            item for item in shallow_by_prefix[prefix] if item.name in protected_names
        )
        group = [
            item for item in shallow_by_prefix[prefix] if item.name not in protected_names
        ]
        if not group:
            continue
        group.sort(key=lambda item: (item.mtime, item.name))
        excess = len(group) - keep_recent_per_prefix
        if excess <= 0:
            kept_shallow.extend(group)
            continue
        for candidate in group[:excess]:
            if candidate.mtime <= cutoff:
                aged_shallow.append(candidate)
            else:
                kept_shallow.append(candidate)
        kept_shallow.extend(group[excess:])
    aged_shallow.sort(key=lambda item: (item.mtime, item.name))
    return aged_shallow, kept_shallow


def _candidate_is_directly_protected(
    owned: _CandidateDirectory,
    *,
    protected: set[str],
    active_digests: set[str],
    runtime_root: Path | None,
    settlement_cache: dict[tuple[str, str, str, str], bool],
) -> bool:
    return (
        owned.name in protected
        or bool(owned.data_digests & active_digests)
        or bool(owned.reader_digests & active_digests)
        or _candidate_has_unsettled_admission(
            owned, runtime_root=runtime_root, settlement_cache=settlement_cache
        )
    )


def _valid_sha256_tuple(raw: object) -> frozenset[str] | None:
    if not isinstance(raw, list):
        return None
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or BARE_SHA256_PATTERN.fullmatch(item) is None:
            return None
        out.add(item)
    return frozenset(out)


from .context_delivery_retention_checkpoint import (  # noqa: E402
    RETENTION_REFSCAN_CHECKPOINT_NAME as RETENTION_REFSCAN_CHECKPOINT_NAME,
    _RefScanCheckpoint as _RefScanCheckpoint,
    _TargetCohortIdentity as _TargetCohortIdentity,
    _cohort_identities_for_candidates as _cohort_identities_for_candidates,
    _load_refscan_checkpoint as _load_refscan_checkpoint,
    _remove_refscan_checkpoint as _remove_refscan_checkpoint,
    _save_refscan_checkpoint as _save_refscan_checkpoint,
    _valid_witness_tuple as _valid_witness_tuple,
)
