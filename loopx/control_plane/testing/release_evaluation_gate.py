"""Deterministic validation for LoopX's four-state a+b release evaluation ledger."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from .release_metadata import checkout_release_metadata


HORIZON_SCHEMA_VERSION = "loopx_commander_horizon_v0"
RELEASE_EVALUATION_SCHEMA_VERSION = "loopx_release_evaluation_v0"
RELEASE_EVALUATION_GATE_RECEIPT_SCHEMA_VERSION = (
    "release_evaluation_gate_receipt_v0"
)

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:[-+][0-9A-Za-z.-]+)?$"
)
_HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_EXPECTED_DOMAIN_IDS = ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8")
_EVALUATION_MODES = ("retrospective_git_audit", "release_gate_qualified")
_GENERATION_STATUSES = ("eol", "superseded", "active_stable", "candidate", "horizon")
_BREAKDOWN_SCOPES = ("domain_aggregate", "full_41_battlelines")
_DIMENSION_KEYS = ("A", "B", "C", "D")


def parse_semver_tuple(version: str) -> tuple[int, int, int]:
    """Parse SemVer major/minor/patch integers from a package version string."""
    match = _SEMVER_RE.fullmatch(version.strip())
    if match is None:
        raise ValueError(f"invalid SemVer package version: {version!r}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def classify_semver_transition(
    candidate_version: str,
    prior_stable_version: str | None,
) -> str:
    """Classify version change relative to prior stable as major, minor, patch, or same."""
    candidate = parse_semver_tuple(candidate_version)
    if prior_stable_version is None or not prior_stable_version.strip():
        return "major" if candidate[1] == 0 and candidate[2] == 0 else "minor"
    prior = parse_semver_tuple(prior_stable_version)
    if candidate < prior:
        raise ValueError(
            f"candidate version {candidate_version!r} regresses behind prior stable "
            f"{prior_stable_version!r}"
        )
    if candidate[0] > prior[0]:
        return "major"
    if candidate[1] > prior[1]:
        return "minor"
    if candidate[2] > prior[2]:
        return "patch"
    return "same"


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"missing required {label} file: {path.name}")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable {label} JSON in {path.name}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{label} must be a JSON object")
    return {str(k): v for k, v in loaded.items()}


def _as_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _as_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _as_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _as_str(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _as_mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _as_sequence(value: object, *, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an array")
    return value


def _validate_horizon(
    horizon: Mapping[str, object],
) -> tuple[
    dict[str, float],
    dict[str, tuple[str, int]],
    dict[str, list[str]],
    float,
    float,
    dict[int, list[str]],
]:
    if horizon.get("schema_version") != HORIZON_SCHEMA_VERSION:
        raise ValueError(f"horizon.schema_version must be {HORIZON_SCHEMA_VERSION}")
    caps = _as_mapping(horizon.get("anti_bloat_caps"), field="horizon.anti_bloat_caps")
    c_cap = _as_float(
        caps.get("c_without_paired_public_evidence_max"),
        field="horizon.anti_bloat_caps.c_without_paired_public_evidence_max",
    )
    d_cap = _as_float(
        caps.get("d_when_default_off_max"),
        field="horizon.anti_bloat_caps.d_when_default_off_max",
    )
    domains = _as_sequence(horizon.get("domains"), field="horizon.domains")
    weights: dict[str, float] = {}
    battleline_meta: dict[str, tuple[str, int]] = {}
    domain_battlelines: dict[str, list[str]] = {}
    for raw_domain in domains:
        domain = _as_mapping(raw_domain, field="horizon.domain")
        did = _as_str(domain.get("id"), field="horizon.domain.id")
        weights[did] = _as_float(domain.get("weight"), field=f"horizon.{did}.weight")
        lines = _as_sequence(domain.get("battlelines"), field=f"horizon.{did}.battlelines")
        domain_ids: list[str] = []
        for raw_line in lines:
            line = _as_mapping(raw_line, field=f"horizon.{did}.battleline")
            bid = _as_str(line.get("id"), field="horizon.battleline.id")
            tier = _as_int(
                line.get("target_generation_major"),
                field=f"horizon.{bid}.target_generation_major",
            )
            if bid in battleline_meta:
                raise ValueError(f"duplicate battleline id in horizon: {bid}")
            battleline_meta[bid] = (did, tier)
            domain_ids.append(bid)
        domain_battlelines[did] = domain_ids

    if tuple(weights.keys()) != _EXPECTED_DOMAIN_IDS:
        raise ValueError("horizon.domains must define D1..D8 in order")
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError("horizon domain weights must sum to 1.0")
    if len(battleline_meta) != 41:
        raise ValueError(f"horizon must define 41 battlelines, found {len(battleline_meta)}")

    exit_gates_by_major: dict[int, list[str]] = {}
    for raw_gen in _as_sequence(horizon.get("generations"), field="horizon.generations"):
        gen = _as_mapping(raw_gen, field="horizon.generation")
        major = _as_int(gen.get("major"), field="horizon.generation.major")
        gate_ids: list[str] = []
        for raw_gate in _as_sequence(
            gen.get("exit_gates", []), field=f"horizon.generation.{major}.exit_gates"
        ):
            gate = _as_mapping(raw_gate, field="horizon.exit_gate")
            gate_ids.append(_as_str(gate.get("id"), field="horizon.exit_gate.id"))
        exit_gates_by_major[major] = gate_ids

    return weights, battleline_meta, domain_battlelines, c_cap, d_cap, exit_gates_by_major


def _validate_domain_map(
    raw_scores: object,
    *,
    weights: Mapping[str, float],
    reported_total: float,
    label: str,
    issues: list[str],
) -> dict[str, float]:
    scores_map = _as_mapping(raw_scores, field=f"{label}.domain_scores")
    normalized: dict[str, float] = {}
    for did in _EXPECTED_DOMAIN_IDS:
        val = _as_float(scores_map.get(did), field=f"{label}.domain_scores.{did}")
        if val < 0.0 or val > 100.0:
            issues.append(f"{label}_domain_out_of_range:{did}")
        normalized[did] = val
    computed_total = round(
        sum(normalized[did] * weights[did] for did in _EXPECTED_DOMAIN_IDS), 1
    )
    if abs(computed_total - reported_total) > 0.1:
        issues.append(
            f"{label}_weighted_total_mismatch:expected_{computed_total}_got_{reported_total}"
        )
    return normalized


def _validate_battlelines(
    evaluation: Mapping[str, object],
    *,
    weights: Mapping[str, float],
    battleline_meta: Mapping[str, tuple[str, int]],
    domain_battlelines: Mapping[str, Sequence[str]],
    c_cap: float,
    d_cap: float,
    a_domains: Mapping[str, float],
    candidate_major: int,
    issues: list[str],
) -> None:
    raw_lines = _as_sequence(evaluation.get("battlelines"), field="evaluation.battlelines")
    seen_scores: dict[str, float] = {}
    benchmark_uplift_claimed = _as_bool(
        _as_mapping(
            evaluation.get("qualification_evidence"),
            field="evaluation.qualification_evidence",
        ).get("claims_net_benchmark_uplift"),
        field="evaluation.qualification_evidence.claims_net_benchmark_uplift",
    )
    for raw_line in raw_lines:
        line = _as_mapping(raw_line, field="evaluation.battleline")
        bid = _as_str(line.get("id"), field="evaluation.battleline.id")
        if bid not in battleline_meta:
            issues.append(f"unknown_battleline:{bid}")
            continue
        if bid in seen_scores:
            issues.append(f"duplicate_battleline:{bid}")
            continue
        dims = _as_mapping(line.get("dimensions"), field=f"battlelines.{bid}.dimensions")
        dim_values = {
            key: _as_float(dims.get(key), field=f"battlelines.{bid}.dimensions.{key}")
            for key in _DIMENSION_KEYS
        }
        for key, val in dim_values.items():
            if val < 0.0 or val > 25.0:
                issues.append(f"dimension_out_of_range:{bid}:{key}")
        score = _as_float(line.get("score"), field=f"battlelines.{bid}.score")
        expected_score = round(sum(dim_values.values()), 1)
        if abs(expected_score - score) > 0.1:
            issues.append(f"battleline_sum_mismatch:{bid}")
        paired = _as_bool(
            line.get("paired_public_evidence"),
            field=f"battlelines.{bid}.paired_public_evidence",
        )
        default_off = _as_bool(
            line.get("default_off"),
            field=f"battlelines.{bid}.default_off",
        )
        if not paired and dim_values["C"] > c_cap:
            issues.append(f"c_cap_exceeded_without_paired_evidence:{bid}")
        if default_off and dim_values["D"] > d_cap:
            issues.append(f"d_cap_exceeded_when_default_off:{bid}")
        if bid == "7.2" and not benchmark_uplift_claimed:
            if paired or dim_values["C"] > c_cap:
                issues.append("benchmark_uplift_claim_inconsistent:7.2")
        seen_scores[bid] = score

    missing = sorted(set(battleline_meta) - set(seen_scores))
    if missing:
        issues.append(f"missing_battlelines:{','.join(missing[:5])}")
        return

    for did in _EXPECTED_DOMAIN_IDS:
        ids = domain_battlelines[did]
        mean_a = round(sum(seen_scores[bid] for bid in ids) / len(ids), 1)
        if abs(mean_a - a_domains[did]) > 0.1:
            issues.append(f"domain_a_average_mismatch:{did}")

    b_scale = _as_mapping(evaluation.get("b_scale"), field="evaluation.b_scale")
    b_reported = _as_float(b_scale.get("score"), field="evaluation.b_scale.score")
    b_major = _as_int(
        b_scale.get("generation_major"), field="evaluation.b_scale.generation_major"
    )
    if b_major != candidate_major:
        issues.append("b_scale_generation_major_mismatch")
    b_domains = _validate_domain_map(
        b_scale.get("domain_scores"),
        weights=weights,
        reported_total=b_reported,
        label="b_scale",
        issues=issues,
    )
    core_count = 0
    for did in _EXPECTED_DOMAIN_IDS:
        core_ids = [
            bid
            for bid in domain_battlelines[did]
            if battleline_meta[bid][1] <= candidate_major
        ]
        core_count += len(core_ids)
        if core_ids:
            mean_b = round(sum(seen_scores[bid] for bid in core_ids) / len(core_ids), 1)
            if abs(mean_b - b_domains[did]) > 0.1:
                issues.append(f"domain_b_average_mismatch:{did}")
    if _as_int(b_scale.get("battleline_count"), field="b_scale.battleline_count") != core_count:
        issues.append("b_scale_battleline_count_mismatch")


def _validate_source_and_git(
    evaluation: Mapping[str, object],
    *,
    expected_version: str,
    candidate_source: Mapping[str, object] | None,
    repo_root: Path | None,
    verify_git_tags: bool,
    enforce_clean_gate: bool,
    issues: list[str],
) -> None:
    source = _as_mapping(evaluation.get("source"), field="evaluation.source")
    pkg_ver = _as_str(source.get("package_version"), field="evaluation.source.package_version")
    tag = _as_str(source.get("version_tag"), field="evaluation.source.version_tag")
    commit = _as_str(source.get("git_commit"), field="evaluation.source.git_commit").lower()
    tree = _as_str(source.get("git_tree"), field="evaluation.source.git_tree").lower()
    if not _HEX_SHA_RE.fullmatch(commit) or not _HEX_SHA_RE.fullmatch(tree):
        issues.append("invalid_git_object_id")
    if pkg_ver != expected_version:
        issues.append("evaluation_package_version_mismatch")
    if tag != f"v{pkg_ver}":
        issues.append("evaluation_version_tag_mismatch")

    mode = _as_str(evaluation.get("evaluation_mode"), field="evaluation.evaluation_mode")
    if mode not in _EVALUATION_MODES:
        issues.append(f"invalid_evaluation_mode:{mode}")
    semver = _as_mapping(evaluation.get("semver"), field="evaluation.semver")
    parsed = parse_semver_tuple(pkg_ver)
    if (
        _as_int(semver.get("major"), field="semver.major"),
        _as_int(semver.get("minor"), field="semver.minor"),
        _as_int(semver.get("patch"), field="semver.patch"),
    ) != parsed:
        issues.append("semver_object_mismatch")

    if enforce_clean_gate:
        if mode != "release_gate_qualified":
            issues.append("release_gate_requires_qualified_mode")
        qual_evidence = _as_mapping(
            evaluation.get("qualification_evidence"),
            field="evaluation.qualification_evidence",
        )
        if (
            qual_evidence.get("receipt_schema_version")
            != "exact_release_commit_qualification_receipt_v0"
        ):
            issues.append("missing_exact_release_receipt_schema")
        if _as_bool(source.get("git_dirty"), field="evaluation.source.git_dirty"):
            issues.append("evaluation_source_git_dirty")

    if candidate_source is not None:
        for key in ("git_commit", "git_tree", "package_version", "version_tag"):
            if source.get(key) != candidate_source.get(key):
                issues.append(f"candidate_source_mismatch:{key}")
        if enforce_clean_gate and candidate_source.get("git_dirty") is True:
            issues.append("candidate_source_git_dirty")

    if verify_git_tags and repo_root is not None:
        _verify_git_tag_objects(repo_root, tag=tag, commit=commit, tree=tree, issues=issues)


def _verify_git_tag_objects(
    repo_root: Path,
    *,
    tag: str,
    commit: str,
    tree: str,
    issues: list[str],
) -> None:
    for ref, expected, code in (
        (f"refs/tags/{tag}^{{commit}}", commit, f"git_tag_commit_mismatch:{tag}"),
        (f"refs/tags/{tag}^{{tree}}", tree, f"git_tag_tree_mismatch:{tag}"),
    ):
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", ref],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode != 0 or proc.stdout.strip().lower() != expected:
            issues.append(code)


def _validate_major_promotion_gate(
    evaluation: Mapping[str, object],
    *,
    prior_major: int,
    candidate_major: int,
    exit_gates_by_major: Mapping[int, Sequence[str]],
    issues: list[str],
) -> None:
    gate_obj = evaluation.get("major_promotion_gate")
    if not isinstance(gate_obj, Mapping):
        issues.append("missing_major_promotion_gate")
        return
    if _as_int(gate_obj.get("from_major"), field="major_promotion_gate.from_major") != prior_major:
        issues.append("major_promotion_gate_from_major_mismatch")
    if _as_int(gate_obj.get("to_major"), field="major_promotion_gate.to_major") != candidate_major:
        issues.append("major_promotion_gate_to_major_mismatch")
    migration = gate_obj.get("breaking_migration_contract")
    if not isinstance(migration, Mapping) or migration.get("verified") is not True:
        issues.append("breaking_migration_contract_unverified")

    recorded: dict[str, Mapping[str, object]] = {}
    for raw_item in _as_sequence(
        gate_obj.get("exit_gates"), field="major_promotion_gate.exit_gates"
    ):
        item = _as_mapping(raw_item, field="major_promotion_gate.exit_gate")
        gid = _as_str(item.get("id"), field="major_promotion_gate.exit_gate.id")
        recorded[gid] = item

    for required_id in exit_gates_by_major.get(prior_major, ()):
        entry = recorded.get(required_id)
        if entry is None or entry.get("met") is not True:
            issues.append(f"major_promotion_gate_unmet:{required_id}")
        elif not isinstance(entry.get("evidence_ref"), str) or not str(entry.get("evidence_ref")).strip():
            issues.append(f"major_promotion_gate_missing_evidence:{required_id}")


def validate_evaluation_document(
    evaluation: Mapping[str, object],
    *,
    horizon: Mapping[str, object],
    expected_version: str,
    candidate_source: Mapping[str, object] | None = None,
    prior_evaluation: Mapping[str, object] | None = None,
    prior_major_for_gate: int | None = None,
    repo_root: Path | None = None,
    verify_git_tags: bool = False,
    enforce_clean_gate: bool = True,
) -> list[str]:
    """Validate one release evaluation payload against horizon rules and return issues."""
    issues: list[str] = []
    if evaluation.get("schema_version") != RELEASE_EVALUATION_SCHEMA_VERSION:
        issues.append("invalid_evaluation_schema_version")
        return issues

    status = _as_str(
        evaluation.get("generation_status"), field="evaluation.generation_status"
    )
    if status not in _GENERATION_STATUSES:
        issues.append(f"invalid_generation_status:{status}")

    weights, battleline_meta, domain_battlelines, c_cap, d_cap, exit_gates_by_major = (
        _validate_horizon(horizon)
    )
    _validate_source_and_git(
        evaluation,
        expected_version=expected_version,
        candidate_source=candidate_source,
        repo_root=repo_root,
        verify_git_tags=verify_git_tags,
        enforce_clean_gate=enforce_clean_gate,
        issues=issues,
    )

    a_scale = _as_mapping(evaluation.get("a_scale"), field="evaluation.a_scale")
    a_reported = _as_float(a_scale.get("score"), field="evaluation.a_scale.score")
    a_domains = _validate_domain_map(
        a_scale.get("domain_scores"),
        weights=weights,
        reported_total=a_reported,
        label="a_scale",
        issues=issues,
    )

    if prior_evaluation is not None:
        prior_a = _as_float(
            _as_mapping(prior_evaluation.get("a_scale"), field="prior.a_scale").get("score"),
            field="prior.a_scale.score",
        )
        delta_val = a_scale.get("delta_from_prior")
        if delta_val is None:
            issues.append("missing_delta_from_prior")
        else:
            expected_delta = round(a_reported - prior_a, 1)
            if abs(_as_float(delta_val, field="a_scale.delta_from_prior") - expected_delta) > 0.1:
                issues.append(f"delta_from_prior_mismatch:expected_{expected_delta}")

    scope = _as_str(
        evaluation.get("battleline_breakdown_scope"),
        field="evaluation.battleline_breakdown_scope",
    )
    if scope not in _BREAKDOWN_SCOPES:
        issues.append(f"invalid_breakdown_scope:{scope}")
    else:
        if enforce_clean_gate and scope != "full_41_battlelines":
            issues.append("clean_gate_requires_full_41_battlelines")
        if scope == "full_41_battlelines":
            candidate_major = parse_semver_tuple(expected_version)[0]
            _validate_battlelines(
                evaluation,
                weights=weights,
                battleline_meta=battleline_meta,
                domain_battlelines=domain_battlelines,
                c_cap=c_cap,
                d_cap=d_cap,
                a_domains=a_domains,
                candidate_major=candidate_major,
                issues=issues,
            )

    if prior_major_for_gate is not None:
        candidate_major = parse_semver_tuple(expected_version)[0]
        _validate_major_promotion_gate(
            evaluation,
            prior_major=prior_major_for_gate,
            candidate_major=candidate_major,
            exit_gates_by_major=exit_gates_by_major,
            issues=issues,
        )

    return sorted(set(issues))


def _resolve_evaluation_path(
    evaluations_dir: Path,
    *,
    version: str,
    transition_kind: str,
) -> tuple[Path | None, str | None]:
    major, minor, _patch = parse_semver_tuple(version)
    history_dir = evaluations_dir / "history"
    exact_path = history_dir / f"v{version}.json"
    if transition_kind == "patch":
        parent_version = f"{major}.{minor}.0"
        parent_path = history_dir / f"v{parent_version}.json"
        if not parent_path.is_file() and (major, minor) == (0, 1):
            parent_version = "0.1.3"
            parent_path = history_dir / "v0.1.3.json"
        if parent_path.is_file():
            return parent_path, f"v{parent_version}"
        return None, f"v{parent_version}"

    if exact_path.is_file():
        return exact_path, None
    current_path = evaluations_dir / "current.json"
    if current_path.is_file():
        current_data = _load_json_object(current_path, label="current evaluation")
        source = current_data.get("source")
        if isinstance(source, Mapping) and source.get("package_version") == version:
            return current_path, None
    return None, None


def validate_release_evaluation_gate(
    *,
    evaluations_dir: Path,
    candidate_source: Mapping[str, object],
    prior_stable_version: str | None = None,
    evaluation_json: Path | None = None,
    repo_root: Path | None = None,
    verify_git_tags: bool = False,
) -> dict[str, object]:
    """Run the deterministic a/b/c release evaluation gate for a candidate source."""
    candidate_version = _as_str(
        candidate_source.get("package_version"), field="candidate.package_version"
    )
    transition_kind = classify_semver_transition(candidate_version, prior_stable_version)
    horizon = _load_json_object(evaluations_dir / "horizon.json", label="horizon")
    _validate_horizon(horizon)

    if evaluation_json is not None and transition_kind != "patch":
        resolved_path: Path | None = evaluation_json if evaluation_json.is_file() else None
        inherited_tag: str | None = None
    else:
        resolved_path, inherited_tag = _resolve_evaluation_path(
            evaluations_dir,
            version=candidate_version,
            transition_kind=transition_kind,
        )
    if resolved_path is None:
        issue = (
            f"missing_parent_evaluation:{inherited_tag}"
            if transition_kind == "patch"
            else f"missing_release_evaluation:v{candidate_version}"
        )
        return {
            "schema_version": RELEASE_EVALUATION_GATE_RECEIPT_SCHEMA_VERSION,
            "ready": False,
            "transition_kind": transition_kind,
            "candidate_version": candidate_version,
            "prior_stable_version": prior_stable_version,
            "inherited_from": inherited_tag,
            "evaluation_path": None,
            "a_scale_score": None,
            "b_scale_score": None,
            "issues": [issue],
        }

    evaluation = _load_json_object(resolved_path, label="release evaluation")
    prior_eval: Mapping[str, object] | None = None
    extra_issues: list[str] = []
    if (
        prior_stable_version
        and prior_stable_version.strip()
        and transition_kind in {"major", "minor"}
    ):
        prior_ver = prior_stable_version.strip()
        prior_path, _ = _resolve_evaluation_path(
            evaluations_dir, version=prior_ver, transition_kind="same"
        )
        if prior_path is None:
            prior_path, _ = _resolve_evaluation_path(
                evaluations_dir, version=prior_ver, transition_kind="patch"
            )
        if prior_path is not None:
            prior_eval = _load_json_object(prior_path, label="prior stable evaluation")
        else:
            extra_issues.append(f"missing_prior_stable_evaluation:v{prior_ver}")

    if transition_kind == "patch":
        parent_version = str(inherited_tag or "").removeprefix("v")
        issues = validate_evaluation_document(
            evaluation,
            horizon=horizon,
            expected_version=parent_version,
            candidate_source=None,
            prior_evaluation=None,
            prior_major_for_gate=None,
            repo_root=repo_root,
            verify_git_tags=verify_git_tags,
            enforce_clean_gate=parse_semver_tuple(parent_version) >= (2, 3, 0),
        )
    else:
        candidate_major = parse_semver_tuple(candidate_version)[0]
        if transition_kind == "major":
            if prior_stable_version and prior_stable_version.strip():
                prior_major: int | None = parse_semver_tuple(prior_stable_version)[0]
            elif candidate_major > 0:
                prior_major = candidate_major - 1
            else:
                prior_major = None
        else:
            prior_major = None
        issues = validate_evaluation_document(
            evaluation,
            horizon=horizon,
            expected_version=candidate_version,
            candidate_source=candidate_source,
            prior_evaluation=prior_eval,
            prior_major_for_gate=prior_major,
            repo_root=repo_root,
            verify_git_tags=verify_git_tags,
            enforce_clean_gate=True,
        )
    issues = sorted(set([*issues, *extra_issues]))

    a_scale = evaluation.get("a_scale")
    b_scale = evaluation.get("b_scale")
    a_score = a_scale.get("score") if isinstance(a_scale, Mapping) else None
    b_score = b_scale.get("score") if isinstance(b_scale, Mapping) else None
    return {
        "schema_version": RELEASE_EVALUATION_GATE_RECEIPT_SCHEMA_VERSION,
        "ready": not issues,
        "transition_kind": transition_kind,
        "candidate_version": candidate_version,
        "prior_stable_version": prior_stable_version,
        "inherited_from": inherited_tag,
        "evaluation_path": resolved_path.name,
        "a_scale_score": a_score,
        "b_scale_score": b_score,
        "issues": issues,
    }


def validate_evaluation_ledger_tree(
    evaluations_dir: Path,
    *,
    repo_root: Path | None = None,
    verify_git_tags: bool = False,
) -> dict[str, object]:
    """Validate horizon.json, all history/*.json snapshots, and current.json together."""
    horizon = _load_json_object(evaluations_dir / "horizon.json", label="horizon")
    _validate_horizon(horizon)
    history_dir = evaluations_dir / "history"
    history_files = sorted(
        history_dir.glob("v*.json"),
        key=lambda item: parse_semver_tuple(item.stem.removeprefix("v")),
    )
    if not history_files:
        raise ValueError("evaluations/history must contain at least one release evaluation")

    all_issues: dict[str, list[str]] = {}
    prior_eval: Mapping[str, object] | None = None
    for path in history_files:
        version = path.stem.removeprefix("v")
        payload = _load_json_object(path, label=path.name)
        parsed = parse_semver_tuple(version)
        issues = validate_evaluation_document(
            payload,
            horizon=horizon,
            expected_version=version,
            candidate_source=None,
            prior_evaluation=prior_eval,
            prior_major_for_gate=None,
            repo_root=repo_root,
            verify_git_tags=verify_git_tags,
            enforce_clean_gate=parsed >= (2, 3, 0),
        )
        if issues:
            all_issues[path.name] = issues
        prior_eval = payload

    current_path = evaluations_dir / "current.json"
    if current_path.is_file():
        current_payload = _load_json_object(current_path, label="current.json")
        source = _as_mapping(current_payload.get("source"), field="current.source")
        current_ver = _as_str(
            source.get("package_version"), field="current.source.package_version"
        )
        current_issues = validate_evaluation_document(
            current_payload,
            horizon=horizon,
            expected_version=current_ver,
            candidate_source=None,
            prior_evaluation=None,
            prior_major_for_gate=None,
            repo_root=repo_root,
            verify_git_tags=False,
            enforce_clean_gate=parse_semver_tuple(current_ver) >= (2, 3, 0),
        )
        if current_issues:
            all_issues["current.json"] = current_issues

    return {
        "schema_version": RELEASE_EVALUATION_GATE_RECEIPT_SCHEMA_VERSION,
        "ready": not all_issues,
        "history_count": len(history_files),
        "issues_by_file": all_issues,
        "checkout_version": (
            checkout_release_metadata(repo_root)["version"] if repo_root is not None else None
        ),
    }
