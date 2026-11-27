"""Typed contracts and redaction primitives for read-only Goal Channel projections."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict


GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION = "goal_channel_projection_v0"

RAW_MATERIAL_KEY_HINTS: tuple[str, ...] = (
    "credential",
    "local_path",
    "log",
    "raw",
    "secret",
    "stderr",
    "stdout",
    "token",
    "trace",
    "transcript",
)

PRIVATE_VALUE_HINTS: tuple[str, ...] = (
    "/private/",
    "/users/",
    "credential" + "-value",
    "secret" + "-value",
    "token" + "-value",
)

_PRIVATE_DOC_HOST = ".".join(("byte" + "dance", "lark" + "office"))
_PRIVATE_TEXT_MARKERS: tuple[str, ...] = (_PRIVATE_DOC_HOST, *PRIVATE_VALUE_HINTS)

_TodoRole = Literal["user", "agent"]
_GoalChannelMode = Literal["read_only"]
_GoalChannelWriteAuthority = Literal["none"]


class GoalChannelSourceRefs(TypedDict):
    status_generated_at: str | None
    active_state_updated_at: str | None
    latest_run_generated_at: str | None
    review_packet_generated_at: str | None
    event_ledger_source: Literal["run_history"]


class GoalChannelDecisionFrame(TypedDict):
    user_action_required: bool
    agent_action_required: bool
    quiet_noop_allowed: bool


class GoalChannelTruthContract(TypedDict):
    event_ledger_is_source_of_truth: Literal[True]
    projection_is_writable: Literal[False]
    write_authority: _GoalChannelWriteAuthority
    recompute_rule: str


class GoalChannelSourceWarning(TypedDict, total=False):
    kind: Literal["raw_or_private_material_omitted", "coordination_unavailable", "coordination_truncated"]
    key_names: list[str]
    message: str


def _mapping_or_empty(value: Mapping[str, object] | None) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_mapping(value: Mapping[str, object] | None) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_mappings(values: Sequence[Mapping[str, object]] | None) -> list[dict[str, object]]:
    if not values:
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _private_value(value: object) -> bool:
    text = str(value).lower()
    return any(marker in text for marker in _PRIVATE_TEXT_MARKERS)


def compact_goal_channel_text(value: object, *, limit: int = 180) -> str | None:
    if value is None or _private_value(value):
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _text(value: object, *, limit: int = 180) -> str | None:
    return compact_goal_channel_text(value, limit=limit)


def _copy_compact_text_fields(
    target: dict[str, object],
    source: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    limit: int,
) -> None:
    for key in keys:
        value = _text(source.get(key), limit=limit)
        if value is not None:
            target[key] = value


def _raw_material_keys(*values: object) -> list[str]:
    keys: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_str = str(key)
                lowered = key_str.lower()
                if any(hint in lowered for hint in RAW_MATERIAL_KEY_HINTS) or _private_value(child):
                    keys.add(key_str)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return sorted(keys)


def _first_text(*values: object, limit: int = 180) -> str | None:
    for value in values:
        text = _text(value, limit=limit)
        if text:
            return text
    return None


def _project_asset(status_item: Mapping[str, object]) -> Mapping[str, object]:
    asset = status_item.get("project_asset")
    return asset if isinstance(asset, Mapping) else {}


def _interaction_channel(interaction: object, key: str) -> Mapping[str, object]:
    if not isinstance(interaction, Mapping):
        return {}
    channel = interaction.get(key)
    return channel if isinstance(channel, Mapping) else {}
