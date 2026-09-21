"""Source adapters for the typed, read-only Goal Channel ownership observation."""
from pathlib import Path
from typing import Any

from ..coordination.local_authority import LOCAL_AUTHORITY_SOURCES, local_authority_is_promoted
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


def observe_goal_coordination(*, runtime_root: Any, goal_id: str,
                              agent_todos: list[dict[str, Any]],
                              explicit_entries: list[dict[str, Any]] | None) -> dict[str, Any]:
    canonical = False
    try:
        root = Path(runtime_root) if runtime_root is not None else None
        if root is not None:
            canonical = local_authority_is_promoted(runtime_root=root, goal_id=goal_id)
        if canonical:
            assert root is not None
            result = effect_runtime_result('coordination.local_authority.ownership_observation', {
                'schema_version': 'loopx_local_ownership_observation_request_v0',
                'runtime_root': str(root.expanduser().resolve()), 'goal_id': goal_id,
                'observed_at': now_local_iso(),
            })
            if (not isinstance(result, dict) or result.get('source_authority') not in LOCAL_AUTHORITY_SOURCES
                or result.get('decision_read_from_provider') is not True or result.get('legacy_fallback_used') is not False
                or not isinstance(result.get('provider_revision'), str)):
                raise RuntimeError('canonical ownership observation unavailable')
        else:
            from ..work_items.local_lease_record import TaskLeaseError
            from ..work_items import task_lease as task_lease_module
            rows = []
            if root is not None:
                for path in sorted(task_lease_module.task_lease_dir(runtime_root=root, goal_id=goal_id).glob('todo_*.json')):
                    try:
                        # task_lease re-exports this seam and existing callers/tests patch it there.
                        lease = task_lease_module.read_lease(path)  # type: ignore[attr-defined]
                    except FileNotFoundError:
                        continue
                    except (TaskLeaseError, OSError):
                        rows.append({'todo_id': path.stem, 'unreadable': True})
                    else:
                        if lease is not None:
                            rows.append({'todo_id': path.stem, 'lease': lease})
            if not rows and not agent_todos and not explicit_entries:
                return {'status': 'loaded', 'entries': []}
            result = effect_runtime_result('coordination.ownership_observation', {
                'schema_version': 'loopx_ownership_observation_request_v0',
                'todos': agent_todos, 'lease_rows': rows, 'explicit_entries': explicit_entries,
                'observed_at': now_local_iso(),
            })
        if (not isinstance(result, dict) or result.get('schema_version') != 'loopx_ownership_observation_result_v0'
            or result.get('status') != 'loaded' or not isinstance(result.get('entries'), list)
            or any(not isinstance(row, dict) for row in result['entries'])):
            raise RuntimeError('invalid ownership observation')
        return result
    except (OSError, RuntimeError, TypeError, ValueError):
        # Observation failure is visible, but never activates a legacy fallback or leaks provider errors.
        return {'status': 'unavailable', 'entries': [], 'legacy_fallback_used': False,
                'source_authority': 'canonical_unavailable' if canonical else 'unavailable'}
