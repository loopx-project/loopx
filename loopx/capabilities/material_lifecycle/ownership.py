"""Material ownership metadata; references never grant write authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from ._validation import check_record_keys, compact_token


@dataclass(frozen=True)
class MaterialProjectScope:
    """An explicitly activated source profile within one authorized workspace."""

    project_ref: str
    source_profile_ref: str
    workspace_grant_ref: str


def material_owner_fields(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if (goal_id is None) == (project_scope is None):
        raise ValueError("exactly one goal_id or project_scope is required")
    if goal_id is not None:
        return {"goal_id": compact_token(goal_id, field="goal_id")}
    scope: Mapping[str, Any]
    if isinstance(project_scope, MaterialProjectScope):
        scope = {
            "project_ref": project_scope.project_ref,
            "source_profile_ref": project_scope.source_profile_ref,
            "workspace_grant_ref": project_scope.workspace_grant_ref,
        }
    elif isinstance(project_scope, Mapping):
        scope = project_scope
    else:
        raise TypeError("project_scope must be MaterialProjectScope or an object")
    fields = {"project_ref", "source_profile_ref", "workspace_grant_ref"}
    check_record_keys(scope, field="project_scope", allowed=fields, required=fields)
    return {
        "project_scope": {
            key: compact_token(scope[key], field=f"project_scope.{key}")
            for key in sorted(fields)
        }
    }


class MaterialProjectScopeVerifier(Protocol):
    """Source adapter resolves authorization through its existing Core owner."""

    def verify_project_scope(
        self, *, project_scope: Mapping[str, str], store_id: str,
        owner_gate_ref: str, observed_at: str,
    ) -> bool: ...


def verify_project_material_write(
    provider: object,
    *,
    owner: Mapping[str, Any],
    store_id: str,
    owner_gate_ref: str,
    observed_at: str,
) -> None:
    """Require the source owner to resolve current authorization before writes.

    Project refs, profile refs and grant refs are selectors, not permission.
    The adapter must resolve the exact store/profile/workspace and current
    caller, audience, gate expiry, write scope and revocation. No Goal is
    created or used to borrow manager authority.
    """
    scope = owner.get("project_scope")
    if scope is None:
        return
    verifier = getattr(provider, "verify_project_scope", None)
    if not callable(verifier):
        raise ValueError("project material writes require a source authorization verifier")
    verified = cast(MaterialProjectScopeVerifier, provider).verify_project_scope(
        project_scope=dict(scope),
        store_id=store_id,
        owner_gate_ref=owner_gate_ref,
        observed_at=observed_at,
    )
    if verified is not True:
        raise ValueError("project material source authorization was not verified")
