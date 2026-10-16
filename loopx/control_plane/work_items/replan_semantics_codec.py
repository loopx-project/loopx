"""Local snapshot transport for the existing TypeScript replan decision owner."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import effect_runtime_result


def project_replan_semantics(params: Mapping[str, Any]) -> dict[str, Any]:
    # Lifecycle recovery carries canonical rows, including retained evidence;
    # both its input and result can exceed the ordinary RPC wire budget.
    return effect_runtime_result(
        "work_item.replan_semantics.project", params, large_local_snapshot=True,
    )
