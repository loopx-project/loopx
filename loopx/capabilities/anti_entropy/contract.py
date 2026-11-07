from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

ANTI_ENTROPY_CAPABILITY_ID = "anti-entropy-governance"
ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION = "loopx_anti_entropy_receipt_v0"
ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION = "loopx_anti_entropy_retirement_basis_v0"


@dataclass(frozen=True, slots=True)
class RetirementBasis:
    """Declared code symbols and paths slated for safe decommissioning."""

    schema_version: str = ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION
    target_symbols: tuple[str, ...] = ()
    target_paths: tuple[str, ...] = ()
    allow_compat_seam: bool = False
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_symbols": list(self.target_symbols),
            "target_paths": list(self.target_paths),
            "allow_compat_seam": self.allow_compat_seam,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: Any) -> RetirementBasis:
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            raise TypeError(
                f"RetirementBasis data must be a JSON object (mapping), got {type(data).__name__}"
            )

        schema_version = data.get("schema_version")
        if schema_version is not None and not isinstance(schema_version, str):
            raise TypeError(
                f"schema_version must be a string, got {type(schema_version).__name__}"
            )

        symbols_raw = data.get("target_symbols")
        target_symbols: list[str] = []
        if symbols_raw is not None:
            if not isinstance(symbols_raw, (list, tuple, set)):
                raise TypeError(
                    f"target_symbols must be a list of strings, got {type(symbols_raw).__name__}"
                )
            for s in symbols_raw:
                if not isinstance(s, str):
                    raise TypeError(
                        f"target_symbols elements must be strings, got {type(s).__name__}"
                    )
                clean_s = s.strip()
                if clean_s:
                    target_symbols.append(clean_s)

        paths_raw = data.get("target_paths")
        target_paths: list[str] = []
        if paths_raw is not None:
            if not isinstance(paths_raw, (list, tuple, set)):
                raise TypeError(
                    f"target_paths must be a list of strings, got {type(paths_raw).__name__}"
                )
            for p in paths_raw:
                if not isinstance(p, str):
                    raise TypeError(
                        f"target_paths elements must be strings, got {type(p).__name__}"
                    )
                clean_p = p.strip()
                if clean_p:
                    target_paths.append(clean_p)

        allow_compat = data.get("allow_compat_seam", False)
        if not isinstance(allow_compat, bool):
            raise TypeError(
                f"allow_compat_seam must be a boolean, got {type(allow_compat).__name__} (value: {allow_compat!r})"
            )

        rationale = data.get("rationale", "")
        if rationale is not None and not isinstance(rationale, str):
            raise TypeError(
                f"rationale must be a string, got {type(rationale).__name__}"
            )

        return cls(
            schema_version=str(
                schema_version or ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION
            ),
            target_symbols=tuple(target_symbols),
            target_paths=tuple(target_paths),
            allow_compat_seam=allow_compat,
            rationale=str(rationale or ""),
        )


@dataclass(frozen=True, slots=True)
class AntiEntropyFinding:
    """One actionable defect where entropy or ghost code was observed."""

    rule_id: str
    severity: str  # "blocking" | "advisory"
    message: str
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
        }


@dataclass(frozen=True, slots=True)
class AntiEntropyReceipt:
    """Durable receipt verifying diff hygiene and delete-first enforcement.

    Evidence provenance: ``basis_hash`` binds the receipt to the exact declared
    retirement intent; ``source_root`` and ``source_evidence`` record which
    post-change sources (if any) backed the verdict. A ``diff_only`` receipt can
    reject but never certifies symbol retirement.
    """

    schema_version: str = ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION
    receipt_id: str = field(default="")
    diff_hash: str = field(default="")
    verdict: str = "passed"  # "passed" | "rejected"
    findings: tuple[AntiEntropyFinding, ...] = ()
    retired_symbols: tuple[str, ...] = ()
    timestamp_utc: float = field(default_factory=time.time)
    basis_hash: str = field(default="")
    source_root: str | None = field(default=None)
    source_evidence: str = "diff_only"  # "workspace_sources" | "diff_only"

    def to_dict(self) -> dict[str, Any]:
        receipt_id = self.receipt_id
        if not receipt_id:
            payload = f"{self.diff_hash}:{self.verdict}:{self.timestamp_utc}"
            receipt_id = f"aer_{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
        return {
            "schema_version": self.schema_version,
            "receipt_id": receipt_id,
            "diff_hash": self.diff_hash,
            "verdict": self.verdict,
            "findings": [f.to_dict() for f in self.findings],
            "retired_symbols": list(self.retired_symbols),
            "timestamp_utc": self.timestamp_utc,
            "basis_hash": self.basis_hash,
            "source_root": self.source_root,
            "source_evidence": self.source_evidence,
        }
