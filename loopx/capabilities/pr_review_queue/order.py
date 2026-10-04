"""IO adapter for the typed direction owner; no host-side reverse policy."""
from collections.abc import Mapping, Sequence
from typing import Any

from ...control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRejected


def configuration(request: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return effect_runtime_result("capabilities.pr_review.configuration", dict(request))
    except EffectRuntimeRejected as exc:
        if exc.diagnostic_code == "pr_review_configuration_type":
            raise TypeError(str(exc)) from exc
        raise ValueError(str(exc)) from exc


def order_queue(items: Sequence[Mapping[str, Any]], review_order: str | None) -> list[dict[str, Any]]:
    if review_order is None:
        return [dict(item) for item in items]  # Existing Python API callers retain their legacy mode.
    # The direction owner only needs eligibility and position, not complete
    # review plans/evidence. Keep large packets out of the IPC size budget.
    indices = effect_runtime_result("capabilities.pr_review.order", {
        "items": [{"index": index, "review_action_kind": item.get("review_action_kind")}
                  for index, item in enumerate(items)], "review_order": review_order,
    })["indices"]
    return [dict(items[index]) for index in indices]
