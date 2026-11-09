"""Transport for the typed current-work Yardstick owner; no policy or store."""
from __future__ import annotations

from functools import lru_cache
import json
from typing import Any, cast

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.public_safety import validate_public_safe_value


@lru_cache(maxsize=256)
def _capture(encoded: str) -> str:
    try:
        return json.dumps(effect_runtime_result("work_item.yardstick.capture", json.loads(encoded)), sort_keys=True)
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error


def normalize_work_yardstick_capture(capture: Any, *, goal_id: str, agent_id: str, evidence_refs: Any) -> dict[str, Any]:
    params = dict(capture=capture, goal_id=goal_id, agent_id=agent_id, evidence_refs=evidence_refs)
    validate_public_safe_value(params, path="work_yardstick")
    normalized = json.loads(_capture(json.dumps(params, sort_keys=True, allow_nan=False)))
    return cast(dict[str, Any], normalized)


def project_work_yardstick_presentations(rows, *, goal_id=None):
    """One typed projection call for the captures in an entire read snapshot."""
    requests = [{"capture": row["work_yardstick"], "goal_id": goal_id or row["goal_id"],
        "agent_id": row.get("agent_id", ""), "evidence_refs": row.get("evidence_refs", [])}
        for row in rows if "work_yardstick" in row]
    if not requests:
        return rows
    params = {"captures": requests}
    validate_public_safe_value(params, path="work_yardsticks")
    projected = iter(effect_runtime_result("work_item.yardstick.presentation", params)["captures"])
    return [{**row, "work_yardstick": next(projected)} if "work_yardstick" in row else row for row in rows]
