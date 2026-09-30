"""Real binding/inbox/receipt checks; model and provider replies are doubles."""

import json
from datetime import UTC, datetime

import pytest

from loopx.extensions.lark import goal_topic_runtime as runtime
from loopx.extensions.lark.goal_channel_contracts import (
    binding_for_goal,
    normalize_lark_topic_event_rejection_reason,
    read_goal_channel_binding,
)
from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets
from loopx.extensions.lark.goal_topic_connections import connect_lark_goal_topic
from test_lark_goal_topic_connections import CHAT_ID, _manager_fixture
from test_lark_goal_topic_runtime import _reply_runner


def _direct_connection(tmp_path):
    kwargs, _, bindings = _manager_fixture(tmp_path)
    binding = binding_for_goal(bindings, "goal-alpha")
    assert binding is not None
    connect_lark_goal_topic(
        **{**kwargs, "connection_id": binding["connection_id"]},
        session_id="manager-session",
        conversation_kind="manager",
        turn_trigger="human_messages",
    )
    return {
        "target_payload": read_goal_channel_targets(kwargs["target_path"]),
        "binding_payloads": {
            "goal-alpha": read_goal_channel_binding(kwargs["binding_path"])
        },
        "runtime_root": tmp_path / "runtime",
    }


def _event(message_id, text):
    return {
        "chat_id": CHAT_ID,
        "event_id": "evt_" + message_id,
        "message_id": message_id,
        "sender_type": "user",
        "sender_id": "ou_public_owner",
        "mentions": [],
        "create_time": datetime.now(UTC).isoformat(),
        "content": text,
    }


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"historical_context_only": True}, "historical_context_only"),
        ({"sender_type": "app"}, "bot_message"),
        ({"sender_type": ""}, "human_identity_unverified"),
        ({"sender_id": ""}, "human_identity_unverified"),
    ],
)
def test_context_feedback_preserves_the_typed_non_execution_cause(
    tmp_path, patch, reason
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Context-only input must not invoke an executor or reply")

    result = runtime.process_lark_goal_topic_event(
        **_direct_connection(tmp_path),
        event={**_event("om_context_case", "看看这个 PR。"), **patch},
        answer=forbidden,
        reply_runner=forbidden,
        provider_runner=forbidden,
    )
    assert result["status"] == "context_only_captured"
    assert result["reason"] == reason
    assert normalize_lark_topic_event_rejection_reason(result["reason"]) == reason
    assert result["turn_authorized"] is False
    assert result["model_invoked"] is False
    assert result["external_write_performed"] is False


def test_three_direct_requests_keep_distinct_returns_and_replay_once(
    tmp_path, monkeypatch
):
    """Receipt/transport qualification, not live owner adoption or PR merging."""
    options = _direct_connection(tmp_path)
    monkeypatch.setattr(
        runtime, "ensure_lark_event_inbox_received_reaction", lambda **_: {"ok": True}
    )
    texts = ["修复并合并这个 PR。", "这个 PR 也修一下。", "第三个先 review，别合并。"]
    events = [_event(f"om_request_{index}", text) for index, text in enumerate(texts)]
    answers, returns, reply_state = [], {}, {}
    current_source = ""
    transport = _reply_runner(reply_state)

    def answer(route, text):
        answers.append((route["message_id"], text))
        return {
            "response_text": f"已收到：{text} 这是接收确认，尚未完成工作。",
            "effect_receipt": runtime._session_turn_effect(route),
        }

    def reply(args):
        if "+messages-reply" in args or "+messages-send" in args:
            if "--dry-run" not in args:
                returns[current_source] = ""  # Filled from the provider's retained body below.
        result = transport(args)
        if current_source in returns:
            returns[current_source] = reply_state["reply_text"]
        result["stdout"] = (
            result["stdout"].replace("linkmacbot", "LoopX Mew")
            .replace("om_reply_fixture", "om_return_" + current_source)
        )
        return result

    for event in events:
        current_source = event["message_id"]
        result = runtime.process_lark_goal_topic_event(
            **options, event=event, answer=answer, reply_runner=reply
        )
        assert result["status"] == "replied_and_acknowledged", json.dumps(result)
    for event in reversed(events):
        result = runtime.process_lark_goal_topic_event(
            **options, event=event, answer=answer, reply_runner=reply
        )
        assert result["status"] == "already_acknowledged"
    assert answers == [(event["message_id"], event["content"]) for event in events]
    assert set(returns) == {event["message_id"] for event in events}
    for event in events:
        assert event["content"] in returns[event["message_id"]]
