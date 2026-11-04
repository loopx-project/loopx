from __future__ import annotations

from pathlib import Path
from typing import Any

from .contract import (
    ANTI_ENTROPY_CAPABILITY_ID,
    RetirementBasis,
)
from .oracles import evaluate_diff_hygiene


def run_anti_entropy_verify(
    *,
    diff_text: str,
    basis_data: Any = None,
    goal_id: str | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Verify exact diff hygiene and delete-first compliance, returning structured payload.

    ``workspace`` explicitly binds the post-change sources used to verify symbol
    retirement. Without it, verdicts can reject but never certify retirement
    (AE004 insufficient source evidence).
    """
    basis = (
        basis_data
        if isinstance(basis_data, RetirementBasis)
        else (RetirementBasis.from_dict(basis_data) if basis_data is not None else None)
    )
    receipt = evaluate_diff_hygiene(diff_text, basis, workspace=workspace)

    blocking_count = sum(1 for f in receipt.findings if f.severity == "blocking")
    advisory_count = sum(1 for f in receipt.findings if f.severity == "advisory")

    return {
        "ok": receipt.verdict == "passed",
        "command": "verify",
        "capability_id": ANTI_ENTROPY_CAPABILITY_ID,
        "goal_id": goal_id,
        "verdict": receipt.verdict,
        "receipt": receipt.to_dict(),
        "finding_count": len(receipt.findings),
        "blocking_finding_count": blocking_count,
        "advisory_finding_count": advisory_count,
        "retired_symbols": list(receipt.retired_symbols),
    }
