"""Default-off, task-scoped feedback loop for anti-entropy retirement work.

Single-task closed loop:

1. Task context: ``build_anti_entropy_turn_context`` projects a declared
   ``RetirementBasis`` into a typed turn-context payload a host can inject into
   agent input when the capability is enabled for that task.
2. In-flight check: agents call the ``anti_entropy_inspect`` tool (or the CLI
   mirror) and receive actionable feedback. Blocking findings are progress
   facts guiding the next correction, not final delivery failures.
3. Delivery verification: stays owned by the existing change-quality owner
   (exact-diff prepare/record/verify and receipt invalidation). This module
   only returns the delivery route; it never writes receipts or creates a
   parallel delivery authority.

Model adoption is proven by actual runs elsewhere; the tests for this module
cover the protocol and the real callable entry points.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .agent_tool import anti_entropy_inspect
from .contract import AntiEntropyFinding, RetirementBasis
from .oracles import normalize_repo_path

_INSPECT_CLI_ROUTE = (
    "loopx anti-entropy inspect --diff-file <diff.patch|-> "
    "--basis-json <json|path> --workspace <post-change-sources> --format json"
)

_DELIVERY_ROUTE = (
    "loopx change-quality prepare --goal-id <goal-id> "
    "(bind --repo-path/--base-ref to the reviewed scope), "
    "then loopx change-quality record --goal-id <goal-id> "
    "--result-json <result.json> --execute "
    "(result conforms to change_quality_agent_result_v2 with the prepared "
    "scope_fingerprint), "
    "then loopx change-quality verify --goal-id <goal-id> "
    "(same --repo-path/--base-ref)"
)

_TOOL_NAME = "anti_entropy_inspect"

_TOOL_DESCRIPTION = (
    "Verify a unified diff against a declared retirement basis and diff hygiene "
    "rules. Returns an immutable receipt with actionable findings: blocking "
    "findings are progress facts to fix before delivery, not final delivery "
    "failures. Symbol retirement certification requires an explicitly bound "
    "workspace holding the complete post-change sources."
)

ANTI_ENTROPY_INSPECT_TOOL_DESCRIPTOR: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": _TOOL_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "required": ["diff_text"],
        "properties": {
            "diff_text": {
                "type": "string",
                "description": "Unified diff text of the change under review.",
            },
            "retirement_basis": {
                "type": "object",
                "description": (
                    "Declared RetirementBasis (target_symbols, target_paths, "
                    "allow_compat_seam, rationale)."
                ),
            },
            "workspace": {
                "type": "string",
                "description": (
                    "Directory with the complete post-change sources; required "
                    "to certify symbol retirement."
                ),
            },
            "goal_id": {"type": "string", "description": "Associated LoopX goal identifier."},
        },
    },
    "cli_route": _INSPECT_CLI_ROUTE,
}


def _rule_next_action(finding: AntiEntropyFinding) -> str:
    if finding.rule_id.startswith("AE001"):
        return "Remove the superseded definition or target path in this change, or declare an authorized compatibility seam."
    if finding.rule_id.startswith("AE002"):
        return "Remove the reintroduced wrapper or active reference so the retired name has no live definition or caller."
    if finding.rule_id.startswith("AE003"):
        return "Delete the commented-out block under version control instead of keeping ghost code."
    if finding.rule_id.startswith("AE004"):
        return "Re-run with --workspace bound to the complete post-change sources; diff-only evidence cannot certify retirement."
    return "Address the reported diff hygiene finding and re-run the inspection."


def build_anti_entropy_turn_context(
    *,
    basis: RetirementBasis | Mapping[str, Any],
    goal_id: str | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Project a declared retirement basis into a typed turn-context payload.

    The payload is data for the host to inject when the capability is enabled
    for the task; calling this function does not execute anything.
    """
    basis_obj = (
        basis if isinstance(basis, RetirementBasis) else RetirementBasis.from_dict(basis)
    )
    context: dict[str, Any] = {
        "capability_id": "anti-entropy-governance",
        "task": {
            "objective": basis_obj.rationale,
            "target_symbols": list(basis_obj.target_symbols),
            "target_paths": list(basis_obj.target_paths),
            "allow_compat_seam": basis_obj.allow_compat_seam,
        },
        "inspect_tool": ANTI_ENTROPY_INSPECT_TOOL_DESCRIPTOR,
        "feedback_semantics": {
            "blocking_findings": (
                "Progress facts for the next correction round; they are not "
                "final delivery failures."
            ),
            "retirement_certification": (
                "Requires an explicitly bound workspace with complete "
                "post-change sources (AE004 otherwise)."
            ),
            "delivery_gate": (
                "Final acceptance is owned by change-quality prepare/record/"
                "verify; this capability supplies evidence only."
            ),
        },
        "delivery_route": _DELIVERY_ROUTE,
    }
    if goal_id is not None:
        context["goal_id"] = goal_id
    if workspace is not None:
        context["workspace"] = str(workspace)
    return context


def run_anti_entropy_feedback_cycle(
    *,
    diff_text: str,
    basis: RetirementBasis | Mapping[str, Any] | None = None,
    goal_id: str | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Run one inspect round and classify its outcome as actionable feedback.

    This is the real in-flight check entry: it never raises on verification
    findings. A rejected verdict yields ``is_progress_fact=True`` with
    per-finding next actions; only a passed, source-bound verification with all
    targets covered marks the round delivery-ready (final acceptance still
    belongs to the change-quality owner).
    """
    result = anti_entropy_inspect(
        diff_text=diff_text,
        retirement_basis=(
            dict(basis) if isinstance(basis, Mapping) else basis
        ),
        goal_id=goal_id,
        workspace=workspace,
    )
    receipt = result.get("receipt", {})
    findings = receipt.get("findings", [])
    actionable: list[dict[str, Any]] = [
        {
            "rule_id": f.get("rule_id"),
            "severity": f.get("severity"),
            "message": f.get("message"),
            "location": f.get("location"),
            "next_action": _rule_next_action(
                AntiEntropyFinding(
                    rule_id=f.get("rule_id", ""),
                    severity=f.get("severity", "advisory"),
                    message=f.get("message", ""),
                    location=f.get("location"),
                )
            ),
        }
        for f in findings
    ]

    verdict = result.get("verdict")
    retired = result.get("retired_symbols", [])
    source_evidence = receipt.get("source_evidence", "diff_only")

    basis_obj = (
        basis if isinstance(basis, RetirementBasis) else (
            RetirementBasis.from_dict(basis) if basis is not None else None
        )
    )
    declared_symbols = list(basis_obj.target_symbols) if basis_obj is not None else []
    declared_paths = list(basis_obj.target_paths) if basis_obj is not None else []
    has_declared_targets = bool(declared_symbols or declared_paths)

    symbols_retired = (
        not declared_symbols
        or (bool(declared_symbols) and all(s in retired for s in declared_symbols))
    )

    finding_locations = {f.get("location") for f in findings if f.get("location")}
    paths_clean = True
    if declared_paths:
        if any(p in finding_locations for p in declared_paths):
            paths_clean = False
        elif workspace is not None:
            workspace_path = Path(workspace).resolve()
            if workspace_path.exists():
                for p in declared_paths:
                    norm = normalize_repo_path(p)
                    if norm != "/dev/null" and (workspace_path / norm).resolve().exists():
                        paths_clean = False
                        break
            else:
                paths_clean = False
        else:
            paths_clean = False

    all_targets_retired = has_declared_targets and symbols_retired and paths_clean

    delivery_ready = (
        verdict == "passed"
        and source_evidence == "workspace_sources"
        and all_targets_retired
    )

    return {
        "ok": True,
        "goal_id": goal_id,
        "verdict": verdict,
        "source_evidence": source_evidence,
        "retired_symbols": retired,
        "actionable_feedback": actionable,
        "is_progress_fact": verdict == "rejected",
        "delivery_ready": delivery_ready,
        "delivery_route": _DELIVERY_ROUTE,
        "inspect_cli_route": _INSPECT_CLI_ROUTE,
        "receipt": receipt,
    }
