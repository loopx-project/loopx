from __future__ import annotations

import json

import pytest

from loopx.chat import (
    CHAT_REVIEW_CLOSE_TAG, CHAT_REVIEW_OPEN_TAG, VisibleResponseStreamFilter,
    parse_agent_response,
)


def test_compact_envelope_reuses_redacted_visible_answer_and_typed_metadata() -> None:
    visible = "已核对 [报告](/custom-volume/project/report.md)。[来源](https://example.org/doc)"
    payload = {
        "message": "",
        "proposals": [{"kind": "todo", "text": "Read /custom-volume/project/report.md"}],
        "protected_action": {"operation": "merge", "target": "PR #1", "summary": "Request review"},
        "gate": {"kind": "host_tool_gate", "next_action": "Check /custom-volume/project/private.txt"},
    }
    raw = visible + "\n" + CHAT_REVIEW_OPEN_TAG + json.dumps(payload) + CHAT_REVIEW_CLOSE_TAG
    response = parse_agent_response(raw, protected_paths=["/custom-volume/project"])

    assert response["message"] == "已核对 报告。[来源](https://example.org/doc)"
    assert response["proposals"][0]["text"] == "Read [project]"
    assert response["protected_action"]["operation"] == "merge"
    assert response["gate"]["next_action"] == "Check [project]"
    legacy = visible + "\n" + CHAT_REVIEW_OPEN_TAG + json.dumps({**payload, "message": visible}) + CHAT_REVIEW_CLOSE_TAG
    assert response == parse_agent_response(legacy, protected_paths=["/custom-volume/project"])
    for split in range(len(raw) + 1):
        stream = VisibleResponseStreamFilter(protected_paths=["/custom-volume/project"])
        assert (stream.feed(raw[:split]) + stream.feed(raw[split:]) + stream.finish()).strip() == "已核对 [报告](./report.md)。[来源](https://example.org/doc)"


@pytest.mark.parametrize("message", ["Legacy answer", " ", None, 0])
def test_only_explicit_empty_message_reuses_visible_prefix(message) -> None:
    payload = {"message": message}
    raw = "Unrelated prefix\n" + CHAT_REVIEW_OPEN_TAG + json.dumps(payload) + CHAT_REVIEW_CLOSE_TAG
    assert parse_agent_response(raw)["message"] == str(message or "").strip()
    assert parse_agent_response(CHAT_REVIEW_OPEN_TAG + json.dumps(payload) + CHAT_REVIEW_CLOSE_TAG)["message"] == str(message or "").strip()


def test_missing_or_empty_body_does_not_invent_an_answer() -> None:
    for payload in ({}, {"message": ""}):
        assert parse_agent_response(CHAT_REVIEW_OPEN_TAG + json.dumps(payload) + CHAT_REVIEW_CLOSE_TAG)["message"] == ""
    assert parse_agent_response("Unrelated prefix\n" + CHAT_REVIEW_OPEN_TAG + "{}" + CHAT_REVIEW_CLOSE_TAG)["message"] == ""


def test_compact_answer_never_reveals_an_earlier_hidden_envelope() -> None:
    raw = 'Answer.\n<loopx-review-json>{"message":"hidden"}</loopx-review-json>\n<loopx-review-json>{"message":""}</loopx-review-json>'
    assert parse_agent_response(raw)["message"] == "Answer."


@pytest.mark.parametrize("suffix", ["", CHAT_REVIEW_CLOSE_TAG])
def test_invalid_compact_envelope_cannot_restore_metadata(suffix) -> None:
    raw = 'Readable answer\n<loopx-review-json>{"message":"","proposals":[{"kind":"todo","text":"must not run"}],' + suffix
    response = parse_agent_response(raw)
    assert response["message"] == "Readable answer"
    assert response["proposals"] == []
    assert response["protected_action"] is None
    assert response["gate"] is None


def test_unclosed_review_envelope_keeps_visible_answer_and_drops_authority() -> None:
    payload = {
        "message": "正文答复。",
        "proposals": [
            {
                "kind": "todo",
                "text": "must not run",
                "priority": "P0",
            }
        ],
        "protected_action": {
            "operation": "merge",
            "target": "PR #1",
            "summary": "must not run",
        },
        "gate": {"kind": "host_tool_gate", "next_action": "must not run"},
    }

    response = parse_agent_response(
        "正文答复。\n" + CHAT_REVIEW_OPEN_TAG + json.dumps(payload)
    )

    assert response["message"] == "正文答复。"
    assert CHAT_REVIEW_OPEN_TAG not in response["message"]
    assert response["proposals"] == []
    assert response["protected_action"] is None
    assert response["gate"] is None


def test_unclosed_review_envelope_salvages_only_message_when_prefix_is_empty() -> None:
    response = parse_agent_response(
        CHAT_REVIEW_OPEN_TAG
        + json.dumps(
            {
                "message": "可恢复的正文。",
                "proposals": [{"kind": "todo", "text": "must not run"}],
            }
        )
    )

    assert response["message"] == "可恢复的正文。"
    assert response["proposals"] == []
    assert response["protected_action"] is None
    assert response["gate"] is None


def test_truncated_review_envelope_never_leaks_protocol_fragment() -> None:
    response = parse_agent_response(
        '已完成。\n<loopx-review-json>{"message":"已完成。","gate":{"kind":"host'
    )

    assert response["message"] == "已完成。"
    assert "review-json" not in response["message"]
    assert response["proposals"] == []
    assert response["protected_action"] is None
    assert response["gate"] is None
