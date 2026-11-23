"""Goal-level coordination configuration, independent of optional producers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..effect_runtime import effect_runtime_result
from .coordination_state_contract_generated import LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA


RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION = "loopx_coordination_runtime_shadow_config_v0"


_CREDENTIAL_SCOPE_SEGMENTS = frozenset(
    {
        ".aws",
        ".docker",
        ".git-credentials",
        ".gnupg",
        ".kube",
        ".netrc",
        ".npmrc",
        ".ssh",
        "credentials",
        "secrets",
    }
)
_CREDENTIAL_FILE_SUFFIXES = (".key", ".p12", ".pem", ".pfx")


def _clean_scope_for_folding(value: str) -> str:
    text = value.strip()
    while "//" in text:
        text = text.replace("//", "/")
    if text == "./**":
        return "**"
    return text


def _has_glob_metacharacters(text: str) -> bool:
    return any(ch in text for ch in ("*", "?", "[", "]"))


def _repo_variants(goal_repo: str | Path | None) -> tuple[str, ...]:
    if goal_repo is None:
        return ()
    raw = str(goal_repo).strip()
    if not raw:
        return ()
    variants: list[str] = []
    raw_posix = PurePosixPath(raw).as_posix().rstrip("/")
    if raw_posix:
        variants.append(raw_posix)
    try:
        resolved = Path(raw).expanduser().resolve(strict=False).as_posix().rstrip("/")
    except OSError:
        resolved = ""
    if resolved and resolved not in variants:
        variants.append(resolved)
    return tuple(variants)


def _is_sensitive_or_unfoldable(clean: str, repo_prefixes: tuple[str, ...]) -> tuple[bool, bool]:
    """Return (unfoldable, is_external_abs) for a cleaned scope token."""

    if not clean or ";" in clean or clean.startswith("~"):
        return True, False
    parts = [part for part in clean.split("/") if part]
    if ".." in parts:
        return True, False
    for segment in parts:
        lowered = segment.lower()
        if lowered in _CREDENTIAL_SCOPE_SEGMENTS or lowered.endswith(_CREDENTIAL_FILE_SUFFIXES):
            return True, False
    if clean.startswith("/"):
        if clean == "/**":
            return False, False
        if any(clean == repo or clean == f"{repo}/" for repo in repo_prefixes):
            # Align with TS projectGoalWriteScopes: only strip `<repo>/...`,
            # never promote bare `<repo>` without `/**` to `**`.
            return True, False
        in_repo = any(clean.startswith(f"{repo}/") for repo in repo_prefixes)
        return False, not in_repo
    return False, False


def _to_repo_relative(clean: str, repo_prefixes: tuple[str, ...]) -> str:
    if clean in ("/**", "./**", "**"):
        return "**"
    for repo in repo_prefixes:
        prefix = f"{repo}/"
        if clean.startswith(prefix):
            rel = clean[len(prefix) :]
            if rel:
                return rel
    return clean


def _is_broad_coverer(rel: str) -> bool:
    if rel == "**":
        return True
    if not rel.endswith("/**"):
        return False
    stem = rel[:-3].rstrip("/")
    return bool(stem) and stem != "/" and not _has_glob_metacharacters(stem)


def _prove_write_scope_coverage(checks: list[dict[str, object]]) -> list[bool]:
    if not checks:
        return []
    response = effect_runtime_result("quota.write_scope_coverage", {"checks": checks})
    covered = response.get("covered") if isinstance(response, dict) else None
    if (
        not isinstance(covered, list)
        or len(covered) != len(checks)
        or any(not isinstance(value, bool) for value in covered)
    ):
        raise TypeError("invalid typed write-scope coverage result")
    return covered


def fold_write_scope_subpaths(
    values: Sequence[str],
    *,
    goal_repo: str | Path | None = None,
) -> list[str]:
    """Collapse redundant subpaths covered by ``/**``, ``**``, or ``dir/**``.

    Proves every non-exact elimination against the normalized coverage owner
    (``quota.write_scope_coverage``), so exact directory grants such as
    ``plans`` remain alongside descendant globs like ``plans/**``. Preserves
    explicit cross-repository external paths, credential directories, and kept
    scope spellings.
    """

    deduped: list[str] = []
    seen_exact: set[str] = set()
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        clean = _clean_scope_for_folding(text)
        if clean in seen_exact:
            continue
        seen_exact.add(clean)
        deduped.append(text)
    if len(deduped) <= 1:
        return deduped

    repo_prefixes = _repo_variants(goal_repo)
    parsed: list[tuple[int, str, str, bool, bool, str]] = []
    for idx, text in enumerate(deduped):
        clean = _clean_scope_for_folding(text)
        unfoldable, is_external_abs = _is_sensitive_or_unfoldable(clean, repo_prefixes)
        rel = clean if (unfoldable or is_external_abs) else _to_repo_relative(clean, repo_prefixes)
        parsed.append((idx, text, clean, unfoldable, is_external_abs, rel))

    # Deduplicate exact semantic equivalents (e.g. `/**` vs `**`, or
    # `/repo/docs/**` vs `docs/**`), preferring repo-relative `**` spellings.
    best_by_rel: dict[tuple[bool, str], tuple[int, tuple[int, int, int]]] = {}
    dropped: set[int] = set()
    for idx, _text, clean, unfoldable, is_external_abs, rel in parsed:
        if unfoldable:
            continue
        key = (is_external_abs, rel)
        rank = (
            1 if clean.startswith("/") else 0,
            1 if clean in ("/**", "./**") else 0,
            idx,
        )
        prior = best_by_rel.get(key)
        if prior is None:
            best_by_rel[key] = (idx, rank)
        elif rank < prior[1]:
            dropped.add(prior[0])
            best_by_rel[key] = (idx, rank)
        else:
            dropped.add(idx)

    checks: list[dict[str, object]] = []
    check_indices: list[int] = []
    for polarity in (False, True):
        polarity_items = [
            entry
            for entry in parsed
            if entry[0] not in dropped and not entry[3] and entry[4] is polarity
        ]
        broad = sorted(
            [entry for entry in polarity_items if _is_broad_coverer(entry[5])],
            key=lambda entry: (0 if entry[5] == "**" else 1, len(entry[5]), entry[0]),
        )
        if not broad:
            continue
        broad_ids = {entry[0] for entry in broad}
        for pos in range(1, len(broad)):
            checks.append(
                {
                    "required": broad[pos][5],
                    "allowed": [broad[j][5] for j in range(pos)],
                }
            )
            check_indices.append(broad[pos][0])
        allowed_all = ["**"] if broad[0][5] == "**" else [entry[5] for entry in broad]
        for entry in polarity_items:
            if entry[0] in broad_ids:
                continue
            checks.append({"required": entry[5], "allowed": allowed_all})
            check_indices.append(entry[0])

    if checks:
        for idx, is_covered in zip(check_indices, _prove_write_scope_coverage(checks)):
            if is_covered:
                dropped.add(idx)

    return [text for idx, text, *_rest in parsed if idx not in dropped]


def normalize_goal_write_scope(
    values: list[str] | None,
    *,
    goal_repo: str | Path | None = None,
    fold: bool = True,
) -> list[str] | None:
    """Split comma-delimited goal scopes, preserve order, and optionally fold redundant subpaths.

    Goal configuration intentionally accepts a wider token vocabulary than a
    Todo's ``required_write_scopes`` contract, so its stricter normalizer is
    not interchangeable with this rule.
    """

    if values is None:
        return None
    scopes: list[str] = []
    for value in values:
        for part in str(value).split(","):
            scope = part.strip()
            if scope and scope not in scopes:
                scopes.append(scope)
    return fold_write_scope_subpaths(scopes, goal_repo=goal_repo) if fold else scopes


__all__ = ["fold_write_scope_subpaths", "normalize_goal_write_scope"]

@dataclass(frozen=True)
class CoordinationRuntimeShadowConfig:
    enabled: bool
    provider: str | None
    reason_code: str


def local_authority_shadow_summary(goal: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recognize retained config without granting it a writer or capture lineage."""
    coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
    if not isinstance(coordination, Mapping) or "authority_shadow" not in coordination:
        return {"enabled": False, "mode": None, "status": "disabled"}
    raw = coordination["authority_shadow"]
    valid = (isinstance(raw, Mapping) and set(raw) == {"schema_version", "mode"}
             and raw.get("schema_version") == LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA
             and raw.get("mode") == "file_one_way")
    return {"enabled": False, "mode": raw.get("mode") if isinstance(raw, Mapping) else None,
            "status": "retired" if valid else "invalid", "configured": True,
            "replacement": "coordination_runtime_shadow"}


def validate_local_authority_shadow_change(enable_file: bool, clear: bool) -> None:
    if enable_file and clear:
        raise ValueError("--local-authority-shadow-file cannot be combined with --clear-local-authority-shadow")
    if enable_file:
        raise ValueError(
            "local_authority_shadow_retired: post-commit observation is retired; "
            "clear it with --clear-local-authority-shadow; explicitly configure "
            "--coordination-runtime-shadow-file and run coordination-shadow bootstrap "
            "before transaction-bound capture. Retained observations are not migration evidence."
        )


def apply_local_authority_shadow_change(goal: dict[str, Any], enable_file: bool, clear: bool) -> None:
    validate_local_authority_shadow_change(enable_file, clear)
    if not clear:
        return
    coordination = goal.get("coordination")
    if isinstance(coordination, dict):
        coordination.pop("authority_shadow", None)
        if not coordination:
            goal.pop("coordination", None)


def coordination_shadow_summaries(
    goal: Mapping[str, Any] | None,
) -> dict[str, dict[str, object]]:
    """Report the active capture configuration and any retired observation setting."""

    return {
        "local_authority_shadow": local_authority_shadow_summary(
            goal
        ),
        "coordination_runtime_shadow": coordination_runtime_shadow_summary(goal),
    }


def validate_coordination_shadow_changes(
    local_enable_file: bool,
    local_clear: bool,
    runtime_enable_file: bool,
    runtime_clear: bool,
) -> None:
    """Reject retired activation before any registry mutation."""

    validate_local_authority_shadow_change(
        local_enable_file, local_clear
    )
    validate_coordination_runtime_shadow_change(runtime_enable_file, runtime_clear)


def apply_coordination_shadow_changes(
    goal: dict[str, Any],
    local_enable_file: bool,
    local_clear: bool,
    runtime_enable_file: bool,
    runtime_clear: bool,
) -> None:
    """Clear retired settings and configure the transaction-bound shadow independently."""

    apply_local_authority_shadow_change(
        goal, local_enable_file, local_clear
    )
    apply_coordination_runtime_shadow_change(goal, runtime_enable_file, runtime_clear)


def coordination_runtime_shadow_summary(
    goal: Mapping[str, Any] | None,
) -> dict[str, object]:
    """Project the transaction-bound shadow configuration for operators."""

    config = resolve_coordination_runtime_shadow_config(goal)
    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "status": "enabled" if config.enabled else config.reason_code,
    }


def validate_coordination_runtime_shadow_change(
    enable_file: bool,
    clear: bool,
) -> None:
    if enable_file and clear:
        raise ValueError(
            "--coordination-runtime-shadow-file cannot be combined with "
            "--clear-coordination-runtime-shadow"
        )


def apply_coordination_runtime_shadow_change(
    goal: dict[str, Any],
    enable_file: bool,
    clear: bool,
) -> None:
    """Apply the explicit transaction-bound file-shadow opt-in."""

    if not enable_file and not clear:
        return
    raw_coordination = goal.get("coordination")
    coordination: dict[str, Any] = (
        raw_coordination if isinstance(raw_coordination, dict) else {}
    )
    if clear:
        coordination.pop("runtime_shadow", None)
    else:
        coordination["runtime_shadow"] = {
            "enabled": True,
            "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
            "provider": "file_v0",
        }
    if coordination:
        goal["coordination"] = coordination
    else:
        goal.pop("coordination", None)


def resolve_coordination_runtime_shadow_config(
    goal: Mapping[str, Any] | None,
) -> CoordinationRuntimeShadowConfig:
    """Resolve one explicit file-shadow opt-in without changing legacy defaults."""

    if not isinstance(goal, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "goal_missing")
    coordination = goal.get("coordination")
    if not isinstance(coordination, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "configuration_absent")
    configured = coordination.get("runtime_shadow")
    if configured is None:
        return CoordinationRuntimeShadowConfig(False, None, "configuration_absent")
    if not isinstance(configured, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "configuration_invalid")
    if configured.get("enabled") is not True:
        return CoordinationRuntimeShadowConfig(False, None, "explicitly_disabled")
    if configured.get("schema_version") != RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION:
        return CoordinationRuntimeShadowConfig(False, None, "schema_mismatch")
    provider = configured.get("provider")
    if provider != "file_v0":
        return CoordinationRuntimeShadowConfig(False, None, "provider_unsupported")
    return CoordinationRuntimeShadowConfig(True, provider, "explicit_opt_in")
