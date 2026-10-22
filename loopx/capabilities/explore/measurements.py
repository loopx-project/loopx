"""Transport for Explore's TypeScript-owned quantitative result codec."""
from __future__ import annotations

from functools import lru_cache
import json

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...control_plane.runtime.public_safety import validate_public_safe_value


@lru_cache(maxsize=512)
def _normalize(encoded: str) -> str:
    try:
        result = effect_runtime_result("explore.measurements.normalize", {"measurements": json.loads(encoded)})
    except EffectRuntimeRejected as exc:
        # Preserve the result-log reader's invalid-source contract. Runtime
        # unavailability still propagates separately from malformed evidence.
        raise ValueError(str(exc)) from exc
    return json.dumps(result["measurements"], sort_keys=True, allow_nan=False)


def normalize_measurements(value):
    validate_public_safe_value(value, path="measurements")
    return json.loads(_normalize(json.dumps(value, sort_keys=True, allow_nan=False)))
