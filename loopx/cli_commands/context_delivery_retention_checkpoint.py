"""Atomic checkpoint persistence for resumable context-delivery reference scans."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context_delivery_retention_scan import (
        _AdmissionIdentity,
        _CandidateDirectory,
        _JsonObjectReader,
        _ShallowDirectory,
    )

RETENTION_REFSCAN_CHECKPOINT_NAME = ".retention-refscan.json"
REFSCAN_CHECKPOINT_VERSION = 4

_ADMISSION_KEYS: frozenset[str] = frozenset(
    {
        "runtime_root",
        "goal_id",
        "turn_instance_id",
        "agent_id",
        "todo_id",
        "replan_obligation_id",
    }
)


@dataclass(frozen=True, slots=True)
class _TargetCohortIdentity:
    name: str
    mtime_ns: int
    data_digests: frozenset[str]
    reader_digests: frozenset[str]


@dataclass(frozen=True, slots=True)
class _ReferrerSummary:
    data_digests: frozenset[str]
    reader_digests: frozenset[str]
    admissions: tuple[_AdmissionIdentity, ...]
    referenced_names: frozenset[str]
    referenced_digests: frozenset[str]
    byte_size: int


@dataclass(frozen=True, slots=True)
class _RefScanCheckpoint:
    target_cohort: tuple[_TargetCohortIdentity, ...]
    protected_names: frozenset[str]
    seen_frontiers: frozenset[str]
    active_frontier: frozenset[str]
    next_frontier: frozenset[str]
    referrer_witnesses: dict[str, tuple[int, int, int, int]]
    referrer_summaries: dict[str, _ReferrerSummary]
    completed_witnesses: dict[str, tuple[int, int, int, int]]
    round_witnesses: dict[str, tuple[int, int, int, int]]


def _valid_witness_tuple(raw: object) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    if not all(type(item) is int and item >= 0 for item in raw):
        return None
    return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))


def _valid_name_set(raw: object) -> frozenset[str] | None:
    if not isinstance(raw, list):
        return None
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item:
            return None
        out.add(item)
    return frozenset(out)


def _valid_witness_map(raw: object) -> dict[str, tuple[int, int, int, int]] | None:
    if not isinstance(raw, Mapping):
        return None
    witnesses: dict[str, tuple[int, int, int, int]] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not key:
            return None
        witness = _valid_witness_tuple(val)
        if witness is None:
            return None
        witnesses[key] = witness
    return witnesses


def _valid_cohort_list(raw: object) -> tuple[_TargetCohortIdentity, ...] | None:
    from .context_delivery_retention_scan import (
        _matching_ephemeral_prefix,
        _valid_sha256_tuple,
    )

    if not isinstance(raw, list) or not raw:
        return None
    cohort: list[_TargetCohortIdentity] = []
    seen_names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            return None
        name = entry.get("name")
        mtime_ns = entry.get("mtime_ns")
        data_digests = _valid_sha256_tuple(entry.get("data_digests"))
        reader_digests = _valid_sha256_tuple(entry.get("reader_digests"))
        if (
            not isinstance(name, str)
            or _matching_ephemeral_prefix(name) is None
            or name in seen_names
            or type(mtime_ns) is not int
            or mtime_ns < 0
            or data_digests is None
            or reader_digests is None
        ):
            return None
        seen_names.add(name)
        cohort.append(
            _TargetCohortIdentity(
                name=name,
                mtime_ns=mtime_ns,
                data_digests=data_digests,
                reader_digests=reader_digests,
            )
        )
    return tuple(cohort)


def _valid_opt_str(value: object) -> tuple[bool, str | None]:
    if value is None:
        return True, None
    if isinstance(value, str) and value.strip():
        return True, value
    return False, None


def _valid_admission_tuple(raw: object) -> tuple[_AdmissionIdentity, ...] | None:
    from .context_delivery_retention_scan import _AdmissionIdentity

    if not isinstance(raw, list):
        return None
    out: list[_AdmissionIdentity] = []
    for item in raw:
        if not isinstance(item, Mapping) or not (set(item) <= _ADMISSION_KEYS):
            return None
        parsed_fields: dict[str, str | None] = {}
        for key in _ADMISSION_KEYS:
            ok, val = _valid_opt_str(item.get(key))
            if not ok:
                return None
            parsed_fields[key] = val
        out.append(
            _AdmissionIdentity(
                runtime_root=parsed_fields["runtime_root"],
                goal_id=parsed_fields["goal_id"],
                turn_instance_id=parsed_fields["turn_instance_id"],
                agent_id=parsed_fields["agent_id"],
                todo_id=parsed_fields["todo_id"],
                replan_obligation_id=parsed_fields["replan_obligation_id"],
            )
        )
    return tuple(out)


def _valid_referrer_summaries(raw: object) -> dict[str, _ReferrerSummary] | None:
    from .context_delivery_retention_scan import (
        _matching_ephemeral_prefix,
        _valid_sha256_tuple,
    )

    if not isinstance(raw, Mapping):
        return None
    summaries: dict[str, _ReferrerSummary] = {}
    for key, entry in raw.items():
        if (
            not isinstance(key, str)
            or _matching_ephemeral_prefix(key) is None
            or not isinstance(entry, Mapping)
        ):
            return None
        data_digests = _valid_sha256_tuple(entry.get("data_digests"))
        reader_digests = _valid_sha256_tuple(entry.get("reader_digests"))
        admissions = _valid_admission_tuple(entry.get("admissions"))
        referenced_names = _valid_name_set(entry.get("referenced_names"))
        referenced_digests = _valid_sha256_tuple(entry.get("referenced_digests"))
        byte_size = entry.get("byte_size")
        if (
            data_digests is None
            or reader_digests is None
            or admissions is None
            or referenced_names is None
            or referenced_digests is None
            or type(byte_size) is not int
            or byte_size < 0
        ):
            return None
        summaries[key] = _ReferrerSummary(
            data_digests=data_digests,
            reader_digests=reader_digests,
            admissions=admissions,
            referenced_names=referenced_names,
            referenced_digests=referenced_digests,
            byte_size=byte_size,
        )
    return summaries


def _load_refscan_checkpoint(
    root: Path,
    *,
    uid: int | None,
    read_json: _JsonObjectReader | None = None,
) -> tuple[_RefScanCheckpoint | None, bool]:
    """Load v4 ``.retention-refscan.json``; return ``(None, True)`` to fail closed."""
    from .context_delivery_retention_scan import _read_regular_json_object

    active_reader = read_json or _read_regular_json_object
    checkpoint_path = root / RETENTION_REFSCAN_CHECKPOINT_NAME
    try:
        info = os.lstat(checkpoint_path)
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None, True
    if uid is not None and info.st_uid != uid:
        return None, True
    if (info.st_mode & 0o077) != 0:
        return None, True
    loaded = active_reader(checkpoint_path, uid=uid)
    if loaded is None:
        return None, True
    parsed, _ = loaded
    version = parsed.get("version")
    if type(version) is not int or version != REFSCAN_CHECKPOINT_VERSION:
        return None, True

    cohort = _valid_cohort_list(parsed.get("target_cohort"))
    protected_names = _valid_name_set(parsed.get("protected_names"))
    seen_frontiers = _valid_name_set(parsed.get("seen_frontiers"))
    active_frontier = _valid_name_set(parsed.get("active_frontier"))
    next_frontier = _valid_name_set(parsed.get("next_frontier"))
    referrer_witnesses = _valid_witness_map(parsed.get("referrer_witnesses"))
    referrer_summaries = _valid_referrer_summaries(parsed.get("referrer_summaries"))
    completed_witnesses = _valid_witness_map(parsed.get("completed_witnesses"))
    round_witnesses = _valid_witness_map(parsed.get("round_witnesses"))
    if (
        cohort is None
        or protected_names is None
        or seen_frontiers is None
        or active_frontier is None
        or next_frontier is None
        or referrer_witnesses is None
        or referrer_summaries is None
        or completed_witnesses is None
        or round_witnesses is None
    ):
        return None, True

    # Enforce strict layer disjointness, witness/summary parity, and non-empty paused frontier.
    target_names = {entry.name for entry in cohort}
    if not active_frontier or (active_frontier & seen_frontiers):
        return None, True
    if next_frontier & (seen_frontiers | active_frontier):
        return None, True
    if not seen_frontiers and completed_witnesses:
        return None, True
    ref_keys = set(referrer_witnesses)
    if set(referrer_summaries) != ref_keys:
        return None, True
    unscanned_keys = set(completed_witnesses) | set(round_witnesses)
    if ref_keys & target_names:
        return None, True
    if ref_keys & unscanned_keys:
        return None, True
    if target_names & unscanned_keys:
        return None, True

    return (
        _RefScanCheckpoint(
            target_cohort=cohort,
            protected_names=protected_names,
            seen_frontiers=seen_frontiers,
            active_frontier=active_frontier,
            next_frontier=next_frontier,
            referrer_witnesses=referrer_witnesses,
            referrer_summaries=referrer_summaries,
            completed_witnesses=completed_witnesses,
            round_witnesses=round_witnesses,
        ),
        False,
    )


def _save_refscan_checkpoint(
    root: Path,
    *,
    uid: int | None,
    checkpoint: _RefScanCheckpoint,
) -> bool:
    checkpoint_path = root / RETENTION_REFSCAN_CHECKPOINT_NAME
    try:
        info = os.lstat(checkpoint_path)
    except FileNotFoundError:
        info = None
    except OSError:
        return False
    if info is not None and (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or (uid is not None and info.st_uid != uid)
    ):
        return False
    payload = {
        "version": REFSCAN_CHECKPOINT_VERSION,
        "target_cohort": [
            {
                "name": item.name,
                "mtime_ns": item.mtime_ns,
                "data_digests": sorted(item.data_digests),
                "reader_digests": sorted(item.reader_digests),
            }
            for item in checkpoint.target_cohort
        ],
        "protected_names": sorted(checkpoint.protected_names),
        "seen_frontiers": sorted(checkpoint.seen_frontiers),
        "active_frontier": sorted(checkpoint.active_frontier),
        "next_frontier": sorted(checkpoint.next_frontier),
        "referrer_witnesses": {
            key: list(val) for key, val in sorted(checkpoint.referrer_witnesses.items())
        },
        "referrer_summaries": {
            key: {
                "data_digests": sorted(summary.data_digests),
                "reader_digests": sorted(summary.reader_digests),
                "admissions": [
                    {
                        "runtime_root": adm.runtime_root,
                        "goal_id": adm.goal_id,
                        "turn_instance_id": adm.turn_instance_id,
                        "agent_id": adm.agent_id,
                        "todo_id": adm.todo_id,
                        "replan_obligation_id": adm.replan_obligation_id,
                    }
                    for adm in summary.admissions
                ],
                "referenced_names": sorted(summary.referenced_names),
                "referenced_digests": sorted(summary.referenced_digests),
                "byte_size": summary.byte_size,
            }
            for key, summary in sorted(checkpoint.referrer_summaries.items())
        },
        "completed_witnesses": {
            key: list(val) for key, val in sorted(checkpoint.completed_witnesses.items())
        },
        "round_witnesses": {
            key: list(val) for key, val in sorted(checkpoint.round_witnesses.items())
        },
    }
    from .context_delivery_retention_scan import _MAX_CAPTURE_JSON_BYTES

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_CAPTURE_JSON_BYTES:
        return False
    tmp_path = root / f".retention-refscan.{os.getpid()}.{threading.get_ident()}.tmp"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
        try:
            view = memoryview(encoded)
            written = 0
            while written < len(encoded):
                written += os.write(fd, view[written:])
        finally:
            os.close(fd)
        os.replace(tmp_path, checkpoint_path)
        return True
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


def _remove_refscan_checkpoint(root: Path, *, uid: int | None) -> None:
    checkpoint_path = root / RETENTION_REFSCAN_CHECKPOINT_NAME
    try:
        info = os.lstat(checkpoint_path)
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return
    if uid is not None and info.st_uid != uid:
        return
    try:
        os.unlink(checkpoint_path)
    except OSError:
        pass


def _cohort_identities_for_candidates(
    candidates: Iterable[_CandidateDirectory],
) -> tuple[_TargetCohortIdentity, ...]:
    return tuple(
        _TargetCohortIdentity(
            name=item.name,
            mtime_ns=item.mtime_ns,
            data_digests=item.data_digests,
            reader_digests=item.reader_digests,
        )
        for item in candidates
    )


@dataclass(slots=True)
class _ResumedCheckpointState:
    preliminary_reclaimable: list[_CandidateDirectory]
    unscanned_dirs: dict[str, _ShallowDirectory]
    ref_scan_remaining: int
    seen_frontiers: set[str]
    frontier: set[str]
    next_frontier: set[str]
    completed_witnesses: dict[str, tuple[int, int, int, int]]
    prior_round_witnesses: dict[str, tuple[int, int, int, int]]
    referrer_witnesses: dict[str, tuple[int, int, int, int]]
    invalid_aged: int
    done_empty: bool


def _persist_paused_refscan(
    *,
    root: Path,
    uid: int | None,
    remaining_targets: list[_CandidateDirectory],
    inspected_by_name: Mapping[str, _CandidateDirectory],
    protected: set[str],
    seen_frontiers: set[str],
    round_frontier: set[str],
    next_frontier: set[str],
    referrer_witnesses: dict[str, tuple[int, int, int, int]],
    completed_witnesses: dict[str, tuple[int, int, int, int]],
    next_round_witnesses: dict[str, tuple[int, int, int, int]],
    unscanned_dirs: dict[str, _ShallowDirectory],
) -> None:
    if not seen_frontiers:
        save_seen: frozenset[str] = frozenset()
        save_active = frozenset(item.name for item in remaining_targets)
        save_next = frozenset(next_frontier - protected - save_active)
        save_completed_src: dict[str, tuple[int, int, int, int]] = {}
        save_round_src = next_round_witnesses
    else:
        remaining_round = round_frontier - protected - seen_frontiers
        if remaining_round:
            save_seen = frozenset(seen_frontiers)
            save_active = frozenset(remaining_round)
            save_next = frozenset(next_frontier - protected - save_seen - save_active)
            save_completed_src = completed_witnesses
            save_round_src = next_round_witnesses
        else:
            save_seen = frozenset(seen_frontiers | round_frontier)
            save_active = frozenset(next_frontier - protected - save_seen)
            save_next = frozenset()
            save_completed_src = completed_witnesses
            save_round_src = {}
    if not save_active:
        _remove_refscan_checkpoint(root, uid=uid)
        return
    cohort_snapshot = _cohort_identities_for_candidates(remaining_targets)
    target_names = {entry.name for entry in cohort_snapshot}
    save_referrers: dict[str, tuple[int, int, int, int]] = {}
    save_summaries: dict[str, _ReferrerSummary] = {}
    for ref_name, witness in referrer_witnesses.items():
        if ref_name in target_names:
            continue
        owned = inspected_by_name.get(ref_name)
        if owned is None:
            _remove_refscan_checkpoint(root, uid=uid)
            return
        save_referrers[ref_name] = witness
        save_summaries[ref_name] = _ReferrerSummary(
            data_digests=owned.data_digests,
            reader_digests=owned.reader_digests,
            admissions=owned.admissions,
            referenced_names=owned.referenced_names,
            referenced_digests=owned.referenced_digests,
            byte_size=owned.byte_size,
        )
    excluded = save_seen | save_active | save_next | set(save_referrers) | target_names
    _save_refscan_checkpoint(
        root,
        uid=uid,
        checkpoint=_RefScanCheckpoint(
            target_cohort=cohort_snapshot,
            protected_names=frozenset(protected),
            seen_frontiers=save_seen,
            active_frontier=save_active,
            next_frontier=save_next,
            referrer_witnesses=save_referrers,
            referrer_summaries=save_summaries,
            completed_witnesses={
                k: v
                for k, v in save_completed_src.items()
                if k in unscanned_dirs and k not in excluded
            },
            round_witnesses={
                k: v
                for k, v in save_round_src.items()
                if k in unscanned_dirs and k not in excluded
            },
        ),
    )


def _verify_checkpoint_targets_and_referrers(
    checkpoint: _RefScanCheckpoint,
    *,
    aged_by_name: dict[str, _ShallowDirectory],
    all_shallow_by_name: dict[str, _ShallowDirectory],
    protected: set[str],
    active_digests: set[str],
    runtime_root: Path | None,
    abs_root: Path,
    resolved_root: Path,
    uid: int | None,
    max_inspect_bytes_per_sweep: int,
    inspected_by_name: dict[str, _CandidateDirectory],
    settlement_cache: dict[tuple[str, str, str, str], bool],
    read_json: _JsonObjectReader,
) -> list[_CandidateDirectory] | None:
    preliminary_reclaimable: list[_CandidateDirectory] = []
    inspected_bytes = 0
    from .context_delivery_retention_scan import (
        _CandidateDirectory as _RuntimeCandidateDirectory,
        _candidate_is_directly_protected,
        _dir_witness,
        _inspect_owned_capture_dir,
        _measure_candidate_dir,
    )
    for entry in checkpoint.target_cohort:
        shallow = aged_by_name.get(entry.name)
        if shallow is None and entry.name in protected:
            shallow = all_shallow_by_name.get(entry.name)
        if shallow is None or shallow.mtime_ns != entry.mtime_ns:
            return None
        measured = _measure_candidate_dir(shallow.path, uid=uid)
        if measured is None:
            return None
        entries, dir_bytes, _ = measured
        if (
            dir_bytes > max_inspect_bytes_per_sweep
            or inspected_bytes + dir_bytes > max_inspect_bytes_per_sweep
        ):
            return None
        inspected_bytes += dir_bytes
        owned = _inspect_owned_capture_dir(
            shallow.path, name=shallow.name, prefix=shallow.prefix,
            mtime=shallow.mtime, mtime_ns=shallow.mtime_ns, uid=uid,
            abs_root=abs_root, resolved_root=resolved_root,
            measured=(entries, dir_bytes), read_json=read_json,
        )
        if (
            owned is None
            or owned.data_digests != entry.data_digests
            or owned.reader_digests != entry.reader_digests
        ):
            return None
        inspected_by_name[owned.name] = owned
        if _candidate_is_directly_protected(
            owned, protected=protected, active_digests=active_digests,
            runtime_root=runtime_root, settlement_cache=settlement_cache,
        ):
            protected.add(owned.name)
        else:
            preliminary_reclaimable.append(owned)

    # Re-verify saved referrers via O(stat) witness + live settlement checks without
    # re-reading unchanged capture JSON payloads or consuming next-sweep scan budget.
    for ref_name, expected_witness in checkpoint.referrer_witnesses.items():
        summary = checkpoint.referrer_summaries.get(ref_name)
        shallow = all_shallow_by_name.get(ref_name)
        if summary is None or shallow is None:
            return None
        measured = _measure_candidate_dir(shallow.path, uid=uid)
        if measured is None:
            return None
        entries, dir_bytes, max_file_mtime_ns = measured
        if (
            dir_bytes != summary.byte_size
            or _dir_witness(shallow.mtime_ns, entries, dir_bytes, max_file_mtime_ns)
            != expected_witness
        ):
            return None
        owned = _RuntimeCandidateDirectory(
            mtime=shallow.mtime,
            mtime_ns=shallow.mtime_ns,
            name=shallow.name,
            path=shallow.path,
            prefix=shallow.prefix,
            data_digests=summary.data_digests,
            reader_digests=summary.reader_digests,
            admissions=summary.admissions,
            referenced_names=summary.referenced_names,
            referenced_digests=summary.referenced_digests,
            byte_size=dir_bytes,
        )
        inspected_by_name[owned.name] = owned
        if _candidate_is_directly_protected(
            owned, protected=protected, active_digests=active_digests,
            runtime_root=runtime_root, settlement_cache=settlement_cache,
        ):
            protected.add(owned.name)

    return preliminary_reclaimable
