"""Source adapters for the typed, read-only Goal Channel ownership observation."""
from pathlib import Path
from typing import Any

from ..coordination.local_authority import LOCAL_AUTHORITY_SOURCES
from ..effect_runtime import effect_runtime_result
from ..runtime.time import now_local_iso


def coordination_authority_transition(observation: dict[str, Any]) -> dict[str, Any]:
    """Project a read-only authority transition without granting promotion.

    Goal Channel consumers (including Lark) need the same explanation as the
    managed-delegation preflight: legacy state is not launchable, canonical
    state is promoted, and a failed canonical readback needs repair.  Keep the
    serialized transition to its irreducible state: source authority already
    belongs to ``coordination_observation`` and the renderer derives the next
    action.  Read-only mode and the projection truth contract deny mutation;
    only the reviewed TypeScript promotion preview may establish readiness.
    """

    source = str(observation.get("source_authority") or "")
    if source in LOCAL_AUTHORITY_SOURCES:
        state = "promoted"
    elif observation.get("status") != "loaded":
        state = "unavailable"
    else:
        state = "promotion_required"
    return {"state": state}


def observe_goal_coordination(*, runtime_root: Any, goal_id: str) -> dict[str, Any]:
    try:
        root = Path(runtime_root)
        result = effect_runtime_result('coordination.local_authority.ownership_observation', {
            'schema_version': 'loopx_local_ownership_observation_request_v0',
            'runtime_root': str(root.expanduser().resolve()), 'goal_id': goal_id,
            'observed_at': now_local_iso(),
        })
        if (not isinstance(result, dict) or result.get('source_authority') not in LOCAL_AUTHORITY_SOURCES
            or result.get('decision_read_from_provider') is not True or result.get('legacy_fallback_used') is not False
            or not isinstance(result.get('provider_revision'), str)):
            raise RuntimeError('canonical ownership observation unavailable')
        if (not isinstance(result, dict) or result.get('schema_version') != 'loopx_ownership_observation_result_v0'
            or result.get('status') != 'loaded' or not isinstance(result.get('entries'), list)
            or any(not isinstance(row, dict) for row in result['entries'])):
            raise RuntimeError('invalid ownership observation')
        return result
    except (OSError, RuntimeError, TypeError, ValueError):
        # Observation failure is visible, but never activates a legacy fallback or leaks provider errors.
        return {'status': 'unavailable', 'entries': [], 'legacy_fallback_used': False,
                'source_authority': 'canonical_unavailable'}
