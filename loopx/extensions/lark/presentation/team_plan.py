"""Lark presentation for one canonical ``team.plan`` proposal."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import html
from typing import Any

from ....chat_action_store import ActionConflictError
from ....control_plane.effect_runtime import effect_runtime_result


TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION = "loopx_team_plan_card_action_v0"


def _card_text(value: object) -> str:
    text = html.escape(str(value or "").strip(), quote=True)
    for character, entity in {
        "*": "&#42;",
        "~": "&#126;",
        "[": "&#91;",
        "]": "&#93;",
        "(": "&#40;",
        ")": "&#41;",
        "#": "&#35;",
        "_": "&#95;",
    }.items():
        text = text.replace(character, entity)
    return text


def _review_frame(proposal: Mapping[str, Any]) -> dict[str, Any]:
    plan = effect_runtime_result(
        "presentation.action_review_plan.compile",
        {"proposal": proposal},
    )
    frame = plan.get("reviewCardFrame") if isinstance(plan, Mapping) else None
    if (
        not isinstance(frame, Mapping)
        or frame.get("schemaVersion") != "review_card_frame_v0"
        or frame.get("actionKind") != "team.plan"
        or frame.get("proposalId") != proposal.get("proposal_id")
        or frame.get("stateFingerprint")
        != proposal.get("expected_state_fingerprint")
    ):
        raise ValueError("team plan review frame is unavailable")
    return dict(frame)


def _field_label(key: object) -> str:
    token = str(key or "")
    labels = {
        "goal": "Goal",
        "objective": "目标",
        "lane_gaps": "待补齐 lane",
        "quota_envelope": "配额边界",
        "stop_condition": "停止条件",
    }
    if token.startswith("lane_") and token[5:].isdigit():
        return f"Lane {token[5:]}"
    return labels.get(token, token)


def _field_markdown(fields: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(
        f"**{_card_text(_field_label(field.get('key')))}**\n"
        f"{_card_text(field.get('value'))}"
        for field in fields
    )


def build_team_plan_review_card(
    proposal: Mapping[str, Any], *, audience_id: str
) -> dict[str, Any]:
    """Render provider-neutral review semantics into one Lark Card 2.0."""

    frame = _review_frame(proposal)
    if frame.get("kind") != "confirmation":
        raise ActionConflictError("team plan is not awaiting confirmation")
    fields = frame.get("fields")
    if not isinstance(fields, list) or not all(
        isinstance(item, Mapping) for item in fields
    ):
        raise ValueError("team plan review fields are unavailable")
    action_base = {
        "schema_version": TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION,
        "proposal_id": str(frame["proposalId"]),
        "state_fingerprint": str(frame["stateFingerprint"]),
        "audience_id": audience_id,
    }
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "enable_forward": False,
            "summary": {"content": "LoopX 团队计划待确认"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "团队计划待确认"},
            "subtitle": {
                "tag": "plain_text",
                "content": "仅预览；确认前不会创建 lane Todo",
            },
            "template": "orange",
            "icon": {"tag": "standard_icon", "token": "approval_colorful"},
            "text_tag_list": [
                {
                    "tag": "text_tag",
                    "text": {"tag": "plain_text", "content": "待确认"},
                    "color": "orange",
                }
            ],
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "vertical_spacing": "12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**计划范围**\n{_card_text(frame['focus'])}",
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "background_style": "grey-50",
                            "padding": "12px",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": _field_markdown(fields),
                                }
                            ],
                        }
                    ],
                },
                {
                    "tag": "markdown",
                    "content": (
                        "**确认边界**\n确认后仅为每条就绪 lane 创建首个有界 Todo；"
                        "当前 proposal 之外不授予任何写权限。"
                    ),
                },
                {
                    "tag": "column_set",
                    "flex_mode": "bisect",
                    "horizontal_spacing": "12px",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "确认团队计划",
                                    },
                                    "type": "primary_filled",
                                    "width": "fill",
                                    "behaviors": [
                                        {
                                            "type": "callback",
                                            "value": {
                                                **action_base,
                                                "decision": "confirm",
                                            },
                                        }
                                    ],
                                    "confirm": {
                                        "title": {
                                            "tag": "plain_text",
                                            "content": "确认这个精确计划？",
                                        },
                                        "text": {
                                            "tag": "plain_text",
                                            "content": (
                                                "提交后只应用卡片中的当前 proposal；"
                                                "计划或 Goal 状态变化会要求重新确认。"
                                            ),
                                        },
                                    },
                                }
                            ],
                        },
                        {
                            "tag": "column",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {
                                        "tag": "plain_text",
                                        "content": "拒绝",
                                    },
                                    "type": "danger",
                                    "width": "fill",
                                    "behaviors": [
                                        {
                                            "type": "callback",
                                            "value": {
                                                **action_base,
                                                "decision": "reject",
                                            },
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                },
            ],
        },
    }


def build_team_plan_result_card(proposal: Mapping[str, Any]) -> dict[str, Any]:
    frame = _review_frame(proposal)
    kind = str(frame.get("kind") or "")
    if kind not in {"pending", "result"}:
        raise ActionConflictError("team plan result is not available")
    result_kind = str(frame.get("resultKind") or "pending")
    labels = {
        "pending": ("正在应用", "blue"),
        "applied": ("已应用", "green"),
        "rejected": ("已拒绝", "red"),
        "stale": ("需要重新确认", "orange"),
        "failed": ("应用失败", "red"),
        "inactive": ("已失效", "grey"),
    }
    label, template = labels.get(result_kind, labels["inactive"])
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "enable_forward": False,
            "summary": {"content": f"LoopX 团队计划 · {label}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "团队计划"},
            "subtitle": {"tag": "plain_text", "content": str(frame["focus"])},
            "template": template,
            "icon": {"tag": "standard_icon", "token": "approval_colorful"},
            "text_tag_list": [
                {
                    "tag": "text_tag",
                    "text": {"tag": "plain_text", "content": label},
                    "color": template,
                }
            ],
        },
        "body": {
            "direction": "vertical",
            "padding": "12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**{_card_text(label)}**\n"
                        f"{_card_text(frame.get('resultSummary') or result_kind)}"
                    ),
                }
            ],
        },
    }


__all__ = [
    "build_team_plan_result_card",
    "build_team_plan_review_card",
    "TEAM_PLAN_CARD_ACTION_SCHEMA_VERSION",
]
