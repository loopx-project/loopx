"""Presentation of private, read-only Steward queries."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .message_card import compact_plain_text

FOCUS_TITLES = {"gates": "待我处理", "todos": "任务速览", "all": "总览"}
ButtonValue = Callable[..., dict[str, str]]


def _text(text: str) -> dict[str, Any]:
    # Goal and Todo text is data; do not interpret markdown, links or @ mentions.
    return {"tag": "div", "text": {"tag": "plain_text", "content": text}}


def _card(title: str, elements: list[dict[str, Any]], *, tone: str = "blue") -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "enable_forward": False},
        "header": {"template": tone, "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def _buttons(*choices: tuple[str, dict[str, str]]) -> dict[str, Any]:
    return {"tag": "action", "actions": [
        {"tag": "button", "text": {"tag": "plain_text", "content": label},
         "type": "default", "value": action}
        for label, action in choices
    ]}


def build_steward_triage_menu(value: ButtonValue) -> dict[str, Any]:
    return _card("LoopX 快捷操作", [_buttons(*(
        (label, value("query", focus=focus)) for focus, label in FOCUS_TITLES.items()
    ))])


def steward_triage_without_buttons(
    card: Mapping[str, Any], *, query_result: bool = False,
) -> dict[str, Any]:
    if query_result:
        guidance = [_text("按钮暂不可用。可重发查询命令，或发送 /triage 查看其他命令。")]
    else:
        guidance = [
            _text("按钮暂不可用，可以发送文字命令。"),
            *(_text(f"{command} · {label}") for command, label in (
                ("/gates", FOCUS_TITLES["gates"]), ("/todos", FOCUS_TITLES["todos"]),
                ("/triage all", FOCUS_TITLES["all"]),
            )),
            _text("查看单个项目时，在命令后加 --goal 项目ID。"),
        ]
    return {**card, "elements": [
        *(row for row in card["elements"] if row["tag"] != "action"), *guidance,
    ]}


def build_steward_triage_projects(
    value: ButtonValue, goals: Sequence[str], *, focus: str, offset: int, selected_goal: str = "",
) -> dict[str, Any]:
    page = goals[offset:offset + 6]
    buttons = [
        (compact_plain_text(goal, max_chars=36), value("query", focus=focus, goal=goal))
        for goal in page
    ]
    elements = [_buttons(*buttons[i:i + 2]) for i in range(0, len(buttons), 2)]
    if not goals:
        elements.append(_text("当前没有可查看的项目。"))
    navigation = []
    if offset:
        navigation.append(("上一页", value("projects", focus=focus, offset=str(offset - 6))))
    if offset + len(page) < len(goals):
        navigation.append(("下一页", value("projects", focus=focus, offset=str(offset + 6))))
    if navigation:
        elements.append(_buttons(*navigation))
    elements.append(_buttons(
        ("全部授权项目", value("query", focus=focus)),
        ("返回菜单", value("menu")),
    ))
    label = "选择项目 · " + FOCUS_TITLES[focus]
    if selected_goal:
        elements.insert(0, _text("当前：" + compact_plain_text(selected_goal, max_chars=72)))
    return _card(label, elements)


def _rows(projection: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    rows = projection.get(key)
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def build_steward_triage_card(
    projection: Mapping[str, Any], *, value: ButtonValue | None = None,
    navigation_goal: str | None = None,
) -> dict[str, Any]:
    if projection.get("schema_version") != "steward_portfolio_triage_v0":
        raise ValueError("triage card requires a Steward portfolio projection")
    focus = str(projection["focus"])
    counts = projection["counts"]
    scope = projection["scoped_goal_ids"]
    missing = _rows(projection, "unavailable_goals")
    gates = _rows(projection, "open_user_gates")
    todos = _rows(projection, "attention_todos")
    progress = _rows(projection, "recent_progress")
    progress_unavailable = projection.get("unavailable_recent_progress") or []
    quiet = projection.get("quiet") is True and bool(scope) and not missing and not progress_unavailable
    if not scope:
        elements = [_text("当前没有可查看的项目。")]
    elif len(missing) == len(scope):
        elements = [_text("暂时读不到项目状态。请稍后刷新。")]
    else:
        label = compact_plain_text(scope[0], max_chars=100) if len(scope) == 1 else f"{len(scope)} 个项目"
        summary = []
        if focus in {"all", "gates"}:
            summary.append(f"待我处理 {counts['open_user_gates']}")
        if focus in {"all", "todos"}:
            summary.append(f"重点与阻塞任务 {counts['attention_todos']}")
        elements = [_text(label + " · " + " · ".join(summary))]
        if missing:
            elements.insert(0, _text(f"{len(missing)} 个项目暂时读不到，以下为已读取的结果。"))
        elif quiet:
            elements.append(_text("全部正常，没有需要关注的事项。"))
        elif focus == "gates" and not gates:
            elements.append(_text("这个范围暂无待你处理的事项。"))
        elif focus == "todos" and not todos:
            elements.append(_text("这个范围暂无重点或阻塞任务。"))
        if progress_unavailable:
            elements.insert(0, _text(f"{len(progress_unavailable)} 个项目的近期进展暂未读全。"))

    for title, rows, omitted in (
        ("待我处理", gates, counts["omitted_user_gates"]),
        ("重点与阻塞任务", todos, counts["omitted_attention_todos"]),
        ("近期进展", progress, counts["omitted_recent_progress"]),
    ):
        if not rows:
            continue
        elements.extend([{"tag": "hr"}, _text(title)])
        for item in rows:
            badge = "阻塞" if item.get("status") == "blocked" else item.get("priority")
            item_label = " · ".join(str(part) for part in (item.get("goal_id"), badge) if part)
            text = compact_plain_text(item.get("text") or item.get("summary"), max_chars=200)
            if item_label and len(scope) != 1:
                text = f"{item_label}｜{text}"
            elif badge:
                text = f"{badge}｜{text}"
            elements.append(_text(text))
        if omitted:
            elements.append(_text(f"另有 {omitted} 条。"))

    if value is not None:
        controls = [("选择项目", value("projects", focus=focus, goal=navigation_goal or "")),
                    ("返回菜单", value("menu"))]
        if navigation_goal is not None:
            controls.insert(0, ("刷新", value("query", focus=focus, goal=navigation_goal)))
        elements.append(_buttons(*controls))
    return _card(FOCUS_TITLES[focus], elements,
                 tone="orange" if missing or progress_unavailable or gates else "green" if quiet else "blue")
