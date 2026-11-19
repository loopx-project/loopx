"""Transport/cache for the Explore-owned typed execution observation codec."""
from __future__ import annotations

from functools import lru_cache
import json
from typing import Any, cast

from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.runtime.public_safety import validate_public_safe_value


@lru_cache(maxsize=512)
def _normalize(encoded: str) -> str:
    result = effect_runtime_result("explore.execution.normalize", {"observation": json.loads(encoded)})
    return json.dumps(result, sort_keys=True, allow_nan=False)


def normalize_execution_observation(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    validate_public_safe_value(value, path="execution_observation")
    # Do not let readers mutate the cached canonical source.
    return cast(dict[str, Any], json.loads(_normalize(encoded)))
