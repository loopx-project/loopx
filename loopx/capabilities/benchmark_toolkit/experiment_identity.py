"""Shared benchmark experiment vocabulary and scoped run identity.

An arm role and a public-safe token are validated at the package boundary, at
the study projection, at the concurrency envelope and at the CLI that admits a
case slot.  Each of those places restated the answer, so widening a token in one
file left the others rejecting the same value, and the CLI's ``--arm-role``
choices could disagree with the envelope that admits the run.

``ARM_ROLES`` is the set every checker compares against.  ``ARM_ROLE_CHOICES``
is the same four names in the order ``argparse`` shows them to a human; the
order is part of the help text, so it is stated rather than derived.

``experiment_token_text`` is the reject path that goes with the token shape.
Five modules carried a byte-identical private ``_token`` helper, so the shape and
its rejection message travelled together in five copies.

``experiment_run_key`` preserves the ledger's benchmark/study/case/run identity
when normalized rows, anchors, provenance and insights are joined.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

ARM_ROLES = frozenset({"baseline", "control", "treatment", "explore"})
ARM_ROLE_CHOICES = ("baseline", "control", "treatment", "explore")

EXPERIMENT_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


def experiment_run_key(
    row: Mapping[str, Any], *, run_id: str | None = None
) -> tuple[str, str, str, str]:
    """Identify a normalized board row or insight; anchors inherit the same scope."""
    return (
        row["benchmark_id"],
        row["study_id"],
        row["case_id"],
        row["run_id"] if run_id is None else run_id,
    )


def experiment_token_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not EXPERIMENT_TOKEN_PATTERN.fullmatch(text):
        raise ValueError(f"{field} must be a compact public-safe token")
    return text
