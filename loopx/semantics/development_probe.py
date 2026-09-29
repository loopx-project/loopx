"""Development-time advisory for newly introduced semantic vocabularies."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Iterable

from .inventory import DEFAULT_ROOT, SOURCE_SUFFIXES, SourceFile, build_inventory


PROBE_SCHEMA_VERSION = "loopx_semantic_coinage_probe_v0"
SUPPORTED_CARRIERS = (
    ("python_enum", "python_enums"),
    ("python_closed_set", "python_closed_sets"),
    ("python_literal_alias", "python_literal_aliases"),
    ("typescript_const_array", "typescript_const_arrays"),
)
_SCOPE_LIMITATIONS = (
    "Only Python Enum, Literal, named multi-value containers, and TypeScript "
    "as-const arrays are analyzed.",
    "Dynamic construction, unnamed literals, and semantic meaning are not inferred; "
    "an empty report is not proof that no new semantics were introduced.",
    "Untracked files are excluded unless each path is supplied explicitly.",
)


class DevelopmentProbeError(ValueError):
    """The requested Git/source boundary could not be inspected safely."""


@dataclass(frozen=True)
class ChangedSource:
    path: str
    scope: str
    before: SourceFile | None
    after: SourceFile


def _run_git(
    repo_root: Path,
    args: list[str],
    *,
    allow_missing: bool = False,
) -> bytes | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout
    if allow_missing:
        return None
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise DevelopmentProbeError(detail or f"git {' '.join(args)} failed")


def _nul_paths(payload: bytes | None) -> set[str]:
    if not payload:
        return set()
    return {item for item in payload.decode("utf-8").split("\0") if item}


def _validated_source_path(relative: str, *, root: str) -> str:
    candidate = PurePosixPath(relative)
    root_path = PurePosixPath(root)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DevelopmentProbeError(f"source path must be repository-relative: {relative}")
    if not candidate.is_relative_to(root_path):
        raise DevelopmentProbeError(f"source path is outside {root}/: {relative}")
    if candidate.suffix not in SOURCE_SUFFIXES:
        raise DevelopmentProbeError(f"source path must be Python or TypeScript: {relative}")
    return candidate.as_posix()


def _read_source(repo_root: Path, relative: str) -> SourceFile:
    path = repo_root / relative
    if path.is_symlink():
        raise DevelopmentProbeError(f"source path must not be a symlink: {relative}")
    if not path.is_file():
        raise DevelopmentProbeError(f"source path does not exist: {relative}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise DevelopmentProbeError(f"source path is not UTF-8: {relative}") from error
    return SourceFile(path=relative, suffix=path.suffix, text=text)


def _baseline_source(
    repo_root: Path, baseline: str, relative: str
) -> SourceFile | None:
    payload = _run_git(
        repo_root,
        ["show", f"{baseline}:{relative}"],
        allow_missing=True,
    )
    if payload is None:
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise DevelopmentProbeError(
            f"baseline source is not UTF-8: {relative}"
        ) from error
    return SourceFile(path=relative, suffix=Path(relative).suffix, text=text)


def collect_changed_sources(
    repo_root: Path,
    *,
    baseline: str,
    explicit_untracked: Iterable[str] = (),
    root: str = DEFAULT_ROOT,
) -> tuple[str, list[ChangedSource]]:
    """Read changed tracked sources and explicitly named untracked sources."""
    resolved_payload = _run_git(
        repo_root,
        ["rev-parse", "--verify", "--end-of-options", f"{baseline}^{{commit}}"],
    )
    assert resolved_payload is not None
    resolved = resolved_payload.decode("ascii").strip()

    changed = _nul_paths(
        _run_git(
            repo_root,
            ["diff", "--name-only", "-z", "--diff-filter=ACMR", resolved, "--", root],
        )
    )
    staged = _nul_paths(
        _run_git(repo_root, ["diff", "--name-only", "-z", "--cached", "--", root])
    )
    working = _nul_paths(
        _run_git(repo_root, ["diff", "--name-only", "-z", "--", root])
    )

    requested_untracked: set[str] = set()
    for supplied in explicit_untracked:
        relative = _validated_source_path(supplied, root=root)
        tracked = _run_git(
            repo_root,
            ["ls-files", "--error-unmatch", "--", relative],
            allow_missing=True,
        )
        if tracked is not None:
            raise DevelopmentProbeError(
                f"--include-untracked path is already tracked: {relative}"
            )
        visible = _nul_paths(
            _run_git(
                repo_root,
                ["ls-files", "--others", "--exclude-standard", "-z", "--", relative],
            )
        )
        if relative not in visible:
            raise DevelopmentProbeError(
                f"--include-untracked path is missing, ignored, or not untracked: {relative}"
            )
        requested_untracked.add(relative)

    results: list[ChangedSource] = []
    for relative in sorted(changed | requested_untracked):
        if relative in requested_untracked:
            scope = "explicit_untracked"
            before = None
        else:
            try:
                relative = _validated_source_path(relative, root=root)
            except DevelopmentProbeError:
                continue
            if relative in staged and relative in working:
                scope = "staged_and_working_tree"
            elif relative in staged:
                scope = "staged"
            elif relative in working:
                scope = "working_tree"
            else:
                scope = "committed_since_baseline"
            before = _baseline_source(repo_root, resolved, relative)
        results.append(
            ChangedSource(
                path=relative,
                scope=scope,
                before=before,
                after=_read_source(repo_root, relative),
            )
        )
    return resolved, results


def _carriers(source: SourceFile | None) -> dict[tuple[str, str], dict[str, Any]]:
    if source is None:
        return {}
    inventory = build_inventory(Path("."), sources=[source])
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, section in SUPPORTED_CARRIERS:
        for item in inventory[section]:
            found[(kind, item["name"])] = {
                "kind": kind,
                "name": item["name"],
                "values": list(item["values"]),
            }
    return found


def _registry_hints(
    values: list[str], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    candidate_values = set(values)
    hints: list[dict[str, Any]] = []
    for name, entry in registry.get("vocabularies", {}).items():
        registered = {str(value) for value in entry.get("values", [])}
        shared = sorted(candidate_values & registered)
        if not shared:
            continue
        hints.append(
            {
                "vocabulary": name,
                "match": "exact_values" if candidate_values == registered else "overlapping_values",
                "shared_values": shared,
                "owners": entry.get("owners", {}),
            }
        )
    return sorted(
        hints,
        key=lambda item: (
            item["match"] != "exact_values",
            -len(item["shared_values"]),
            item["vocabulary"],
        ),
    )


def build_development_probe(
    *,
    baseline: str,
    changes: list[ChangedSource],
    registry: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for change in changes:
        before = _carriers(change.before)
        after = _carriers(change.after)
        for identity, current in sorted(after.items()):
            previous = before.get(identity)
            old_values = previous["values"] if previous else []
            added = [value for value in current["values"] if value not in old_values]
            if not added:
                continue
            candidates.append(
                {
                    "path": change.path,
                    "scope": change.scope,
                    "carrier_kind": current["kind"],
                    "symbol": current["name"],
                    "change": "new_carrier" if previous is None else "values_added",
                    "previous_values": old_values,
                    "current_values": current["values"],
                    "added_values": added,
                    "reuse_hints": _registry_hints(current["values"], registry),
                    "decision_question": (
                        "Should this reuse an existing owner, extend a registered vocabulary, "
                        "remain local, or introduce a new shared contract?"
                    ),
                }
            )
    return {
        "schema_version": PROBE_SCHEMA_VERSION,
        "advisory": True,
        "baseline": baseline,
        "changed_source_count": len(changes),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "scope_limitations": list(_SCOPE_LIMITATIONS),
    }


def render_development_probe(report: dict[str, Any]) -> str:
    lines = [
        "semantic coinage probe (advisory; findings do not fail the command)",
        f"baseline: {report['baseline']}",
        (
            f"changed sources: {report['changed_source_count']}; "
            f"supported candidates: {report['candidate_count']}"
        ),
    ]
    for candidate in report["candidates"]:
        lines.append(
            f"- {candidate['path']}::{candidate['symbol']} "
            f"[{candidate['carrier_kind']}; {candidate['scope']}]"
        )
        lines.append(f"  added values: {', '.join(candidate['added_values'])}")
        hints = candidate["reuse_hints"]
        if hints:
            for hint in hints:
                lines.append(
                    f"  reuse hint: {hint['vocabulary']} ({hint['match']}; "
                    f"shared: {', '.join(hint['shared_values'])})"
                )
        else:
            lines.append("  reuse hint: none in the registered value sets")
        lines.append(f"  decision: {candidate['decision_question']}")
    if not report["candidates"]:
        lines.append("No supported new vocabulary carriers were detected.")
    lines.append("Scope limitations:")
    lines.extend(f"- {limitation}" for limitation in report["scope_limitations"])
    return "\n".join(lines) + "\n"


def load_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DevelopmentProbeError(f"cannot read semantic registry: {path}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("vocabularies"), dict):
        raise DevelopmentProbeError(f"semantic registry has no vocabularies map: {path}")
    return payload
