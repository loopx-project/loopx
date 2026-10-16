"""Per-goal handoff mode: which ownership authority governs todo handoffs.

The canonical provider owns the current policy; Markdown remains a historical
source. Current execution uses only ``soft_claim`` or ``hard_lease``:

* ``legacy`` (including an absent historical field) is decodable for import,
  original receipts and exact lease cleanup. It cannot admit new execution or
  turn a historical lease receipt into current proof. Existing Goals require
  a reviewed, backed-up policy migration; their bytes are never silently changed.
* ``soft_claim`` uses the Todo assignment. New lease acquire/renew/transfer are
  rejected; retained release and inspection remain available.
* ``hard_lease`` uses the current execution proof and typed ownership/terminal
  rules. Claims, delegated actions and linked user decisions do not independently
  grant a lease or bypass the transaction's fence.

TypeScript owns policy admission, quiescence, migration, CAS and receipt recovery.
Python retains source decoding, transport and public result adaptation. A missing
or stale display never replaces canonical authority; unpromoted writes refuse.

"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...paths import effective_runtime_root
from ..goals.active_state_metadata import parse_state_frontmatter

HANDOFF_MODE_SCHEMA_VERSION = "goal_handoff_mode_v0"
HANDOFF_MODE_FRONTMATTER_KEY = "handoff_mode"
HANDOFF_MODE_LEGACY = "legacy"
HANDOFF_MODE_SOFT_CLAIM = "soft_claim"
HANDOFF_MODE_HARD_LEASE = "hard_lease"
HANDOFF_MODE_VALUES = (
    HANDOFF_MODE_LEGACY,
    HANDOFF_MODE_SOFT_CLAIM,
    HANDOFF_MODE_HARD_LEASE,
)


class HandoffModeError(ValueError):
    """Typed handoff-mode failure; mirrors TaskLeaseError's code/payload shape."""

    def __init__(
        self, message: str, *, code: str, payload: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


def normalize_handoff_mode(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return HANDOFF_MODE_LEGACY
    if candidate not in HANDOFF_MODE_VALUES:
        raise HandoffModeError(
            f"unsupported handoff_mode {candidate!r}; expected one of: "
            + ", ".join(HANDOFF_MODE_VALUES),
            code="invalid_handoff_mode",
            payload={"handoff_mode": candidate, "supported": list(HANDOFF_MODE_VALUES)},
        )
    return candidate


def goal_handoff_mode(state_text: str) -> str:
    """Read the goal handoff mode from active-state text; absent means legacy."""

    front_matter = parse_state_frontmatter(state_text)
    return normalize_handoff_mode(front_matter.get(HANDOFF_MODE_FRONTMATTER_KEY))


def _resolve_state(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
) -> tuple[Path | None, Path]:
    from .path_resolution import resolve_todo_state_path

    return resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )


def show_goal_handoff_mode(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
    runtime_root_arg: str | None = None,
) -> dict[str, Any]:
    from .provider_handoff_mode import read_canonical_handoff_mode

    canonical = read_canonical_handoff_mode(
        runtime_root=effective_runtime_root(registry_path, runtime_root_arg), goal_id=goal_id)
    if canonical is not None:
        return {"ok": True, "schema_version": HANDOFF_MODE_SCHEMA_VERSION, "action": "show",
                "goal_id": goal_id, **canonical,
                "handoff_mode": normalize_handoff_mode(canonical["handoff_mode"]), "source": "canonical_provider"}
    _project, resolved_state_file = _resolve_state(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    front_matter = parse_state_frontmatter(
        resolved_state_file.read_text(encoding="utf-8")
    )
    raw = front_matter.get(HANDOFF_MODE_FRONTMATTER_KEY)
    return {
        "ok": True,
        "schema_version": HANDOFF_MODE_SCHEMA_VERSION,
        "action": "show",
        "goal_id": goal_id,
        "handoff_mode": normalize_handoff_mode(raw),
        "source": "frontmatter" if str(raw or "").strip() else "default",
        "state_file": str(resolved_state_file),
    }


def set_goal_handoff_mode(
    *,
    registry_path: Path,
    goal_id: str,
    mode: str,
    project: Path | None = None,
    state_file: Path | None = None,
    runtime_root_arg: str | None = None,
    operation_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set the provider-owned mode; unpromoted Markdown is read-only in 2.0."""

    requested = normalize_handoff_mode(mode)
    if not str(mode or "").strip():
        raise HandoffModeError(
            "handoff-mode set requires an explicit --mode value",
            code="invalid_handoff_mode",
        )
    from .provider_handoff_mode import set_canonical_handoff_mode

    runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    return set_canonical_handoff_mode(
        runtime_root=runtime_root,
        goal_id=goal_id,
        mode=requested,
        operation_id=operation_id,
        dry_run=dry_run,
    )
