"""Return delivery reuses the actual Lark inbox/preview/readback path."""

import json
import pytest

import test_lark_goal_topic_connections as fixtures
from loopx.chat_store import ChatSessionStore
from loopx.extensions.lark.manager_returns import send_return
from loopx.extensions.lark.goal_topic_runtime import _inbox_config
from loopx.extensions.lark.goal_topic_connections import decide_lark_topic_event
from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets
from loopx.extensions.lark.event_inbox import (
    ingest_lark_event_inbox,
    acknowledge_lark_event_inbox,
    load_lark_event_inbox_config,
)
from loopx.extensions.lark.inbox_reactions import (
    record_lark_inbox_reaction,
    lark_inbox_reaction_receipts,
)
from loopx.capabilities.manager_context import (
    _root,
    _write,
    register_ingress,
    POLICY_SCHEMA,
    deliver,
)


@pytest.mark.parametrize("revoke_before_send", [False, True])
def test_original_source_reply_waits_for_ack_and_rechecks_authority(
    tmp_path, revoke_before_send
):
    kwargs, state, bindings = fixtures._manager_fixture(tmp_path)
    root = tmp_path / "runtime"
    registry = kwargs["registry_path"]
    targets = read_goal_channel_targets(kwargs["target_path"])
    snapshot = {"target_payload": targets, "binding_payloads": {"goal-alpha": bindings}}
    event = {
        "schema_version": "lark_event_inbox_event_v0",
        "event_id": "event-return",
        "chat_id": fixtures.CHAT_ID,
        "message_id": "om_return_source",
        "root_id": "om_original_thread",
        "mentions": [{"id": fixtures.APP_ID}],
        "content": "Assess the constraint",
        "sender_id": "owner",
    }
    route = decide_lark_topic_event(
        target_payload=targets,
        binding_payloads=snapshot["binding_payloads"],
        event=event,
    )["route"]
    store = ChatSessionStore(root)
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="test",
        session_id=route["session_id"],
        channel_id=route["manager_channel_id"],
    )
    turn, _ = store.create_turn(
        session["session_id"],
        client_turn_id="source",
        message=event["content"],
        origin="lark",
    )
    target = {"goal_id": "goal-alpha", "agent_id": "agent-alpha"}
    policy = {
        "schema_version": POLICY_SCHEMA,
        "sources": {
            session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [target]}
        },
    }
    _write(_root(root) / "policy.json", policy)
    register_ingress(
        root,
        session_id=session["session_id"],
        client_turn_id="source",
        channel=session["channel_id"],
        sender_id="owner",
        message=turn["message"],
        source_id="lark:" + event["message_id"],
    )
    rid = deliver(root, registry, session=session, turn=turn, request=target)[
        "request_id"
    ]
    return_route = {
        **target,
        "request_id": rid,
        "session_id": session["session_id"],
        "source_id": "lark:" + event["message_id"],
    }
    config, _ = _inbox_config(runtime_root=root, route=route, target_payload=targets)
    ingest_lark_event_inbox(
        project=root, config_path=config, events=[event], execute=True
    )
    calls = []
    sent = []

    def transport(args):
        calls.append(args)
        if "auth" in args:
            data = {
                "appId": fixtures.APP_ID,
                "identities": {
                    "bot": {"available": True, "verified": True, "appName": "LoopX Mew"}
                },
            }
        elif "chats" in args:
            data = {"data": {"chat_id": fixtures.CHAT_ID}}
        elif "+chat-members-list" in args:
            data = {"data": {"items": [{"app_id": fixtures.APP_ID}]}}
        elif "+messages-reply" in args:
            assert args[args.index("--message-id") + 1] == "om_return_source"
            assert args[args.index("--msg-type") + 1] == "post"
            content = args[args.index("--content") + 1]
            assert json.loads(content)["zh_cn"]["content"][0][0] == {"tag": "md", "text": "Concrete conclusion"}
            if "--dry-run" in args:
                if revoke_before_send:
                    _write(
                        _root(root) / "policy.json",
                        {"schema_version": POLICY_SCHEMA, "sources": {}},
                    )
                data = {"api": [{"body": {"msg_type": "post", "content": content}}]}
            else:
                sent.append(args)
                data = {"data": {"message_id": "om_return_result"}}
        elif "+messages-mget" in args:
            data = {
                "data": {
                    "items": [
                        {
                            "message_id": "om_return_result",
                            "msg_type": "post",
                            "content": "Concrete conclusion",
                        }
                    ]
                }
            }
        else:
            raise AssertionError(args)
        return {"returncode": 0, "stdout": json.dumps(data)}

    def invoke():
        return send_return(
            root=root,
            registry=registry,
            snapshot_provider=lambda: snapshot,
            route=return_route,
            session=session,
            turn=turn,
            text="Concrete conclusion",
            runner=transport,
        )

    with pytest.raises(ValueError, match="initial reply") as pending:
        invoke()
    assert pending.value.reason == "initial_delivery_receipt_unavailable"
    assert not calls
    acknowledge_lark_event_inbox(
        project=root,
        config_path=config,
        message_ids=[event["message_id"]],
        execute=True,
    )
    inbox = load_lark_event_inbox_config(project=root, config_path=config)["inbox_path"]
    record_lark_inbox_reaction(
        inbox=inbox, message_id=event["message_id"], phase="received",
        reaction_id="reaction_Get", emoji_type="Get",
    )
    if revoke_before_send:
        with pytest.raises(ValueError, match="revoked") as revoked:
            invoke()
        assert revoked.value.reason == "return_authorization_unavailable"
        assert not sent
    else:
        result = invoke()
        assert result["reply_verified"] and result["external_write_performed"], result
        assert len(sent) == 1 and "--idempotency-key" in sent[0]
        assert load_lark_event_inbox_config(project=root, config_path=config)["reply"][
            "received_reaction_policy"
        ] == "retain"
        assert lark_inbox_reaction_receipts(
            inbox=inbox, message_id=event["message_id"]
        )["received"]["reaction_id"] == "reaction_Get"
        assert invoke()["reply_verified"]
        assert not any("reactions" in call for call in calls)
        assert sent[0][sent[0].index("--idempotency-key") + 1] == sent[1][
            sent[1].index("--idempotency-key") + 1
        ]
