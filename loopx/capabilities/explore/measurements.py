"""Transport for Explore's TypeScript-owned quantitative result codec."""
from __future__ import annotations

from functools import lru_cache
import json

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...control_plane.runtime.public_safety import validate_public_safe_value

_UNSET = object()


@lru_cache(maxsize=512)
def _normalize(encoded: str) -> str:
    try:
        result = effect_runtime_result("explore.measurements.normalize", json.loads(encoded))
    except EffectRuntimeRejected as exc:
        # Preserve the result-log reader's invalid-source contract. Runtime
        # unavailability still propagates separately from malformed evidence.
        raise ValueError(str(exc)) from exc
    return json.dumps(result["measurements"], sort_keys=True, allow_nan=False)


def normalize_measurements(value, *, measurement_context=_UNSET):
    validate_public_safe_value(value, path="measurements")
    request = {"measurements": value}
    if measurement_context is not _UNSET:
        validate_public_safe_value(measurement_context, path="measurement_context")
        request["measurement_context"] = measurement_context
    return json.loads(_normalize(json.dumps(request, sort_keys=True, allow_nan=False)))
