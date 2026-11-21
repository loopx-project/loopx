from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Iterable, Mapping

from ..todos.contract import normalize_todo_claimed_by


PEER_AGENT_IDENTITY_SCHEMA_VERSION = "peer_agent_identity_v1"
PEER_AGENT_PROFILE_SCHEMA_VERSION = "agent_profile_v1"


class AgentRuntimeModel(str, Enum):
    PEER_V1 = "peer_v1"


def agent_runtime_model_for_goal(goal: Mapping[str, Any] | None) -> AgentRuntimeModel:
    """Return the only live agent runtime model."""

    if isinstance(goal, Mapping):
        coordination = goal.get("coordination")
        raw = coordination.get("agent_model") if isinstance(coordination, Mapping) else None
        raw = raw or goal.get("agent_model")
        if raw not in {None, "", AgentRuntimeModel.PEER_V1.value, "legacy_hierarchy"}:
            raise ValueError("coordination.agent_model must be peer_v1")
    return AgentRuntimeModel.PEER_V1


def normalized_peer_agent_ids(values: Iterable[Any]) -> list[str]:
    agents = sorted(
        {
            agent
            for value in values
            for agent in [
                normalize_todo_claimed_by(
                    value.get("id") or value.get("agent_id") or value.get("name")
                    if isinstance(value, Mapping)
                    else value
                )
            ]
            if agent
        }
    )
    return agents


def peer_work_key(value: Mapping[str, Any] | None, *, fallback: str) -> str:
    if not isinstance(value, Mapping):
        return fallback
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def select_peer_for_work(
    registered_agents: Iterable[Any],
    *,
    work_key: str,
) -> str | None:
    agents = normalized_peer_agent_ids(registered_agents)
    if not agents:
        return None
    digest = hashlib.sha256(str(work_key).encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(agents)
    return agents[index]
