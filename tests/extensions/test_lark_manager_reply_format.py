"""The rich-text contract a steward answer must satisfy before it is sent."""

from __future__ import annotations

from loopx.extensions.lark.manager_reply_format import (
    ESCAPED_NEWLINE_CODE,
    TEMPLATE_PLACEHOLDER_CODE,
    repair_manager_reply_text,
)


def test_escaped_newlines_become_a_real_bullet_list() -> None:
    body = r"当日结论如下：\n- 里程碑 A 已完成\n- 里程碑 B 已启动"

    repaired, incidents = repair_manager_reply_text(body)

    assert repaired == "当日结论如下：\n- 里程碑 A 已完成\n- 里程碑 B 已启动"
    assert incidents == [
        {
            "schema_version": "lark_manager_reply_rich_text_repair_v0",
            "code": ESCAPED_NEWLINE_CODE,
            "count": 2,
        }
    ]


def test_unresolved_template_placeholder_becomes_a_typed_marker() -> None:
    body = "本轮新增：{new_description}"

    repaired, incidents = repair_manager_reply_text(body)

    assert repaired == "本轮新增：[未解析占位符: new_description]"
    assert incidents == [
        {
            "schema_version": "lark_manager_reply_rich_text_repair_v0",
            "code": TEMPLATE_PLACEHOLDER_CODE,
            "count": 1,
            "names": ["new_description"],
        }
    ]


def test_a_clean_answer_is_returned_unchanged_with_no_incident() -> None:
    body = "结论：已完成。\n\n依据：\n- 测试通过\n- 评审已发布"

    repaired, incidents = repair_manager_reply_text(body)

    assert repaired == body
    assert incidents == []


def test_fenced_code_is_not_repaired() -> None:
    body = "```\nprivate\\nformat {new_description}\n```"

    repaired, incidents = repair_manager_reply_text(body)

    assert repaired == body
    assert incidents == []


def test_repair_is_idempotent_and_reports_each_defect_once() -> None:
    body = r"一\n{new_description}\n二\n{new_description}"

    repaired, incidents = repair_manager_reply_text(body)
    again, second_incidents = repair_manager_reply_text(repaired)

    assert again == repaired
    assert second_incidents == []
    assert [incident["code"] for incident in incidents] == [
        ESCAPED_NEWLINE_CODE,
        TEMPLATE_PLACEHOLDER_CODE,
    ]
    assert incidents[1]["names"] == ["new_description"]
    assert incidents[1]["count"] == 1


def test_prose_braces_and_other_formats_are_left_alone() -> None:
    body = "结果 { not a placeholder } 与 {PascalCase} 保持原样"

    repaired, incidents = repair_manager_reply_text(body)

    assert repaired == body
    assert incidents == []
