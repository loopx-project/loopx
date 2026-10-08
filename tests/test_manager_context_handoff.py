import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from loopx.capabilities.manager_context import (
    POLICY_SCHEMA,
    _root,
    _write,
    acknowledge,
    authority,
    configure_delivery_target,
    deliver,
    pending,
    register_ingress,
    turn_start_hook,
)
from loopx.capabilities.manager_context.tracking import query
from loopx.control_plane.capability_hooks import dispatch_turn_start_hooks


@pytest.fixture
def fixture(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "research",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["worker"]},
                    },
                    {
                        "id": "other",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["peer"]},
                    },
                ]
            }
        )
    )
    session = {"session_id": "manager-session", "channel_id": "manager"}
    turn = {
        "client_turn_id": "request-one",
        "origin": "web",
        "message": "Consider this new method against current evidence; replan only when justified.",
    }
    return (
        tmp_path,
        registry,
        session,
        turn,
        {"goal_id": "research", "agent_id": "worker"},
    )


def test_project_conversation_delivers_only_to_its_registered_goal(fixture):
    root, registry, session, turn, request = fixture
    session = {**session, "channel_id": "goal.research", "goal_id": "research"}
    before = registry.read_bytes()
    grant = authority(root, registry, session, turn)
    assert grant["targets"] == [request]
    receipt = deliver(root, registry, session=session, turn=turn, request=request)
    assert receipt["status"] == "delivered"
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, registry, session=session, turn=turn,
                request={"goal_id": "other", "agent_id": "peer"})
    assert registry.read_bytes() == before
    assert not pending(root, "other", "peer")["items"]


@pytest.mark.parametrize("strict_envelope", [False, True])
@pytest.mark.parametrize("origin", ["web", "lark"])
def test_lifecycle_only_registry_cannot_supply_context_recipients(
    fixture, monkeypatch, strict_envelope, origin
):
    from loopx.control_plane.collaboration import source_grant_observation
    from loopx.control_plane.projects import registry_codec

    root, registry, session, turn, _ = fixture
    payload = json.loads(registry.read_text())
    payload["profile_id"] = registry_codec.SOURCE_SESSION_PROFILE_ID
    source_registry = registry.with_name("source-session-registry.json")
    if strict_envelope:
        with registry_codec.source_session_registry_transaction(
            source_registry,
            operation="create lifecycle-only fixture",
            create=lambda: payload,
        ) as transaction:
            transaction.commit(payload)
    else:
        source_registry.write_text(json.dumps(payload))
    before = source_registry.read_bytes()

    def reject_enumeration(_registry):
        pytest.fail("lifecycle-only registry reached recipient enumeration")

    monkeypatch.setattr(
        source_grant_observation, "registered_context_recipients", reject_enumeration
    )
    assert authority(root, source_registry, session, {**turn, "origin": origin}) == {
        "mode": "unavailable", "targets": []
    }
    assert source_registry.read_bytes() == before
    assert not _root(root).exists()


@pytest.mark.parametrize("strict_envelope", [False, True])
@pytest.mark.parametrize("other_goal", [
    {"goal_instance_id": "ginst_" + "b" * 32},
    {},
    {"goal_instance_id": "invalid"},
    {"goal_instance_id": "ginst_" + "b" * 32, "id": "unsafe/alias"},
    {"goal_instance_id": "ginst_" + "b" * 32, "activation_state": "stopped"},
    {"goal_instance_id": "ginst_" + "b" * 32, "activation_state": "invalid"},
])
def test_source_session_catalog_keeps_only_current_instantiated_recipients(
    fixture, strict_envelope, other_goal
):
    from loopx.control_plane.projects import registry_codec

    root, registry, session, turn, request = fixture
    payload = json.loads(registry.read_text())
    payload["profile_id"] = registry_codec.SOURCE_SESSION_PROFILE_ID
    payload["goals"][0]["goal_instance_id"] = "ginst_" + "a" * 32
    payload["goals"][1].update(other_goal)
    source_registry = registry.with_name("source-session-registry.json")
    if strict_envelope:
        with registry_codec.source_session_registry_transaction(
            source_registry,
            operation="create context catalog fixture",
            create=lambda: payload,
        ) as transaction:
            transaction.commit(payload)
    else:
        source_registry.write_text(json.dumps(payload))
    before = source_registry.read_bytes()
    expected = [request]
    if other_goal == {"goal_instance_id": "ginst_" + "b" * 32}:
        expected.insert(0, {"goal_id": "other", "agent_id": "peer"})
    result = authority(root, source_registry, session, turn)
    assert result["mode"] == "context_only"
    assert result["targets"] == expected
    goal_session = {**session, "channel_id": "goal.research", "goal_id": "research"}
    assert authority(root, source_registry, goal_session, turn)["targets"] == [request]
    assert source_registry.read_bytes() == before
    assert not _root(root).exists()


def test_stopped_goal_is_not_a_context_recipient_and_revokes_replay(fixture):
    root, registry, session, turn, request = fixture
    assert request in authority(root, registry, session, turn)["targets"]
    first = deliver(root, registry, session=session, turn=turn, request=request)

    data = json.loads(registry.read_text())
    data["goals"][0]["activation_state"] = "stopped"
    registry.write_text(json.dumps(data))
    assert authority(root, registry, session, turn)["targets"] == [
        {"goal_id": "other", "agent_id": "peer"}
    ]
    with pytest.raises(ValueError, match="Goal is stopped or archived"):
        deliver(root, registry, session=session, turn=turn, request=request)
    assert len(pending(root, "research", "worker")["items"]) == 1

    data["goals"][0]["activation_state"] = "active"
    registry.write_text(json.dumps(data))
    assert deliver(root, registry, session=session, turn=turn, request=request) == {
        **first, "replayed": True
    }


@pytest.mark.parametrize("local_scope", ["selected", "all_registered"])
def test_stopped_or_invalid_goal_is_excluded_from_lark_and_goal_chat(fixture, local_scope):
    root, registry, session, turn, request = fixture
    data = json.loads(registry.read_text())
    data["goals"][0]["activation"] = {
        "schema_version": "loopx_goal_activation_v1", "state": "stopped"
    }
    registry.write_text(json.dumps(data))

    goal_session = {**session, "channel_id": "goal.research", "goal_id": "research"}
    assert authority(root, registry, goal_session, turn)["targets"] == []
    with pytest.raises(ValueError, match="Goal is stopped or archived"):
        deliver(root, registry, session=goal_session, turn=turn, request=request)

    lark_session = {**session, "channel_id": "manager.external.group"}
    lark_turn = {**turn, "origin": "lark"}
    _write(_root(root) / "policy.json", {
        "schema_version": POLICY_SCHEMA,
        "sources": {lark_session["channel_id"]: {
            "local_delivery_scope": local_scope, "sender_ids": ["owner"], "targets": [request]
        }},
    })
    register_ingress(root, session_id=session["session_id"],
                     client_turn_id=turn["client_turn_id"],
                     channel=lark_session["channel_id"], sender_id="owner",
                     message=turn["message"], source_id="lark:original")
    expected = [] if local_scope == "selected" else [{"goal_id": "other", "agent_id": "peer"}]
    assert authority(root, registry, lark_session, lark_turn)["targets"] == expected
    with pytest.raises(ValueError, match="Goal is stopped or archived"):
        deliver(root, registry, session=lark_session, turn=lark_turn, request=request)

    data["goals"][0]["activation"]["state"] = "unreadable"
    registry.write_text(json.dumps(data))
    assert authority(root, registry, session, turn)["targets"] == [
        {"goal_id": "other", "agent_id": "peer"}
    ]


@pytest.mark.parametrize("changes", [
    {"channel_id": "goal.other"}, {"goal_id": "other"}, {"goal_id": ""},
])
def test_project_channel_cannot_supply_a_different_goal_identity(fixture, changes):
    root, registry, session, turn, _ = fixture
    session = {**session, "channel_id": "goal.research", "goal_id": "research", **changes}
    assert authority(root, registry, session, turn)["targets"] == []


def test_original_context_delivery_is_idempotent_without_priority_or_todo_writes(
    fixture,
):
    root, registry, session, turn, request = fixture
    with ThreadPoolExecutor(max_workers=4) as executor:
        receipts = list(
            executor.map(
                lambda _: deliver(
                    root, registry, session=session, turn=turn, request=request
                ),
                range(4),
            )
        )
    assert sum(not r["replayed"] for r in receipts) == 1
    assert len({r["request_id"] for r in receipts}) == 1
    assert all(
        not r["priority_changed"]
        and not r["todo_created"]
        and not r["execution_interrupted"]
        for r in receipts
    )
    assert pending(root, "research", "worker")["items"][0]["message"] == turn["message"]
    assert not pending(root, "other", "peer")["items"]
    files = list((root / ".local").rglob("*.json"))
    assert len(files) == 2  # original intent and its exact return route
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    with pytest.raises(ValueError, match="identity conflict"):
        deliver(
            root,
            registry,
            session=session,
            turn={**turn, "message": "changed"},
            request=request,
        )
    with pytest.raises(ValueError):
        deliver(
            root,
            registry,
            session=session,
            turn=turn,
            request={**request, "priority": "P0"},
        )


def test_selected_external_authority_requires_exact_sender_source_and_recipient(fixture):
    root, registry, session, turn, request = fixture
    session["channel_id"] = "manager.external.group"
    turn["origin"] = "lark"
    _write(
        _root(root) / "policy.json",
        {
            "schema_version": POLICY_SCHEMA,
            "sources": {
                session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [request]}
            },
        },
    )
    assert not authority(root, registry, session, turn)["targets"]
    register_ingress(
        root,
        session_id=session["session_id"],
        client_turn_id=turn["client_turn_id"],
        channel=session["channel_id"],
        sender_id="owner",
        message=turn["message"],
        source_id="lark:original",
    )
    assert authority(root, registry, session, turn)["targets"] == [request]
    assert not authority(root, registry, session, {**turn, "message": "forged"})[
        "targets"
    ]
    assert not authority(root, registry, session, {**turn, "origin": "web"})["targets"]
    with pytest.raises(ValueError):
        deliver(
            root,
            registry,
            session=session,
            turn=turn,
            request={"goal_id": "other", "agent_id": "peer"},
        )
    receipt = deliver(root, registry, session=session, turn=turn, request=request)
    # Revocation is checked before replay, not cached as a permanent grant.
    _write(
        _root(root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {}}
    )
    with pytest.raises(ValueError):
        deliver(root, registry, session=session, turn=turn, request=request)
    assert receipt["status"] == "delivered"


def test_operator_delivery_target_preview_grant_revoke_and_live_authority(fixture):
    root, registry, session, turn, request = fixture
    channel = "manager.external." + "a" * 24
    session["channel_id"] = channel
    turn["origin"] = "lark"
    other = {"goal_id": "other", "agent_id": "peer"}
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {
        "schema_version": POLICY_SCHEMA,
        "sources": {channel: {
            "local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [other],
            "evidence_goal_ids": ["research", "other"],
            "evidence_ssh_hosts": {"example-host": ["research"]},
        }},
    })
    before = policy_path.read_bytes()
    preview = configure_delivery_target(
        root, registry, channel=channel, **request, grant=True
    )
    assert preview["would_change"] and not preview["executed"]
    assert preview["resulting_target_count"] == 2
    assert policy_path.read_bytes() == before

    register_ingress(
        root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
        channel=channel, sender_id="owner", message=turn["message"],
        source_id="lark:original",
    )
    assert authority(root, registry, session, turn)["targets"] == [other]
    applied = configure_delivery_target(
        root, registry, channel=channel, **request, grant=True, execute=True
    )
    assert applied["changed"] and applied["granted_after"] and applied["readback_verified"]
    assert authority(root, registry, session, turn)["targets"] == [other, request]
    assert not configure_delivery_target(
        root, registry, channel=channel, **request, grant=True, execute=True
    )["changed"]
    saved = json.loads(policy_path.read_text())
    assert saved["sources"][channel]["sender_ids"] == ["owner"]
    assert saved["sources"][channel]["evidence_ssh_hosts"] == {"example-host": ["research"]}

    revoked = configure_delivery_target(
        root, registry, channel=channel, **request, grant=False, execute=True
    )
    assert revoked["changed"] and not revoked["granted_after"] and revoked["readback_verified"]
    assert authority(root, registry, session, turn)["targets"] == [other]
    assert not configure_delivery_target(
        root, registry, channel=channel, **request, grant=False, execute=True
    )["changed"]

    # Older policy rows may carry metadata; recipient identity is still the pair.
    assert configure_delivery_target(
        root, registry, channel=channel, **request, grant=True, execute=True
    )["changed"]
    saved = json.loads(policy_path.read_text())
    saved["sources"][channel]["targets"] = [other, {**request, "note": "legacy"}, request]
    _write(policy_path, saved)
    assert not configure_delivery_target(
        root, registry, channel=channel, **request, grant=True, execute=True
    )["changed"]
    assert authority(root, registry, session, turn)["targets"] == [other, request]
    assert configure_delivery_target(
        root, registry, channel=channel, **request, grant=False, execute=True
    )["readback_verified"]
    assert json.loads(policy_path.read_text())["sources"][channel]["targets"] == [other]


def test_operator_target_grant_fails_closed_without_audited_source_or_agent(fixture):
    root, registry, _, _, request = fixture
    channel = "manager.external." + "b" * 24
    with pytest.raises((OSError, ValueError)):
        configure_delivery_target(root, registry, channel=channel, **request, grant=True, execute=True)

    policy_path = _root(root) / "policy.json"
    source = {"local_delivery_scope": "selected", "sender_ids": ["owner"], "evidence_goal_ids": ["other"], "targets": []}
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {channel: source}})
    with pytest.raises(ValueError, match="outside the channel read scope"):
        configure_delivery_target(root, registry, channel=channel, **request, grant=True, execute=True)
    source["evidence_goal_ids"] = ["research"]
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {channel: source}})
    with pytest.raises(ValueError, match="registered Agent"):
        configure_delivery_target(root, registry, channel=channel, goal_id="research",
                                  agent_id="unknown", grant=True, execute=True)
    source["evidence_goal_ids"] = ["research", "*"]
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {channel: source}})
    with pytest.raises(ValueError, match="outside the channel read scope"):
        configure_delivery_target(root, registry, channel=channel, **request, grant=True, execute=True)
    source["evidence_goal_ids"] = ["research"]
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {channel: source}})
    data = json.loads(registry.read_text())
    data["goals"][0]["activation_state"] = "stopped"
    registry.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="active Goal"):
        configure_delivery_target(root, registry, channel=channel, **request, grant=True, execute=True)
    assert json.loads(policy_path.read_text())["sources"][channel]["targets"] == []


@pytest.mark.parametrize("whole_goal", [False, True])
def test_manager_inbox_cli_previews_and_applies_delivery_scope(fixture, whole_goal):
    root, registry, _, _, request = fixture
    channel = "manager.external." + "c" * 24
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {
        "schema_version": POLICY_SCHEMA,
        "sources": {channel: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": []}},
    })
    base = [
        sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(root), "manager-inbox",
    ]
    options = ["--channel-id", channel, "--goal-id", request["goal_id"]]
    if not whole_goal:
        options.extend(["--agent-id", request["agent_id"]])

    def call(action, execute=False):
        completed = subprocess.run(
            [*base, action, *options, *(["--execute"] if execute else [])],
            capture_output=True, text=True, check=True,
        )
        return json.loads(completed.stdout)

    original = policy_path.read_bytes()
    assert call("grant-delivery-target")["would_change"]
    assert policy_path.read_bytes() == original
    assert call("grant-delivery-target", execute=True)["granted_after"]
    assert call("revoke-delivery-target", execute=True)["granted_after"] is False
    assert json.loads(policy_path.read_text())["sources"][channel]["targets"] == []


def test_goal_delivery_grant_inherits_agents_and_rechecks_specific_revocation(fixture):
    root, registry, session, turn, request = fixture
    channel = "manager.external." + "d" * 24
    session["channel_id"] = channel
    turn["origin"] = "lark"
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {channel: {
        "local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [{"goal_id": "research"}],
    }}})
    register_ingress(root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
                     channel=channel, sender_id="owner", message=turn["message"], source_id="lark:original")
    assert authority(root, registry, session, turn)["targets"] == [request]
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("future")
    registry.write_text(json.dumps(data))
    future = {"goal_id": "research", "agent_id": "future"}
    assert authority(root, registry, session, turn)["targets"] == [future, request]
    receipt = deliver(root, registry, session=session, turn=turn, request=future)
    assert receipt["status"] == "delivered"
    configure_delivery_target(root, registry, channel=channel, **future, grant=False, execute=True)
    assert authority(root, registry, session, turn)["targets"] == [request]
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, registry, session=session, turn=turn, request=future)
    # Restoring the whole Goal does not silently restore an individually revoked Agent.
    configure_delivery_target(root, registry, channel=channel, goal_id="research", grant=True, execute=True)
    assert authority(root, registry, session, turn)["targets"] == [request]
    configure_delivery_target(root, registry, channel=channel, **future, grant=True, execute=True)
    assert deliver(root, registry, session=session, turn=turn, request=future)["request_id"] == receipt["request_id"]
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, registry, session=session, turn=turn, request={"goal_id": "other", "agent_id": "peer"})
    data["goals"][0]["activation_state"] = "stopped"
    registry.write_text(json.dumps(data))
    assert authority(root, registry, session, turn)["targets"] == []


@pytest.mark.parametrize("local_scope", ["selected", "all_registered"])
def test_agent_revoke_during_goal_revocation_still_denies_original_replay(fixture, local_scope):
    root, registry, session, turn, request = fixture
    channel = "manager.external." + "f" * 24
    session["channel_id"] = channel
    turn["origin"] = "lark"
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {channel: {
        "local_delivery_scope": local_scope, "sender_ids": ["owner"],
        "targets": [{"goal_id": "research"}],
    }}})
    register_ingress(root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
                     channel=channel, sender_id="owner", message=turn["message"], source_id="lark:original")
    receipt = deliver(root, registry, session=session, turn=turn, request=request)
    configure_delivery_target(root, registry, channel=channel, goal_id="research", grant=False, execute=True)
    policy_before_preview = policy_path.read_bytes()
    preview = configure_delivery_target(root, registry, channel=channel, **request, grant=False)
    assert preview["would_change"] and not preview["granted_before"]
    assert policy_path.read_bytes() == policy_before_preview
    assert configure_delivery_target(root, registry, channel=channel, **request, grant=False, execute=True)["changed"]
    configure_delivery_target(root, registry, channel=channel, goal_id="research", grant=True, execute=True)
    assert request not in authority(root, registry, session, turn)["targets"]
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, registry, session=session, turn=turn, request=request)
    configure_delivery_target(root, registry, channel=channel, **request, grant=True, execute=True)
    assert deliver(root, registry, session=session, turn=turn, request=request)["request_id"] == receipt["request_id"]


def test_same_goal_recipients_keep_inboxes_and_decisions_separate(fixture):
    root, registry, session, turn, request = fixture
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("peer")
    registry.write_text(json.dumps(data))
    peer = {**request, "agent_id": "peer"}

    worker_receipt = deliver(
        root, registry, session=session, turn=turn, request=request
    )
    worker_id = worker_receipt["request_id"]
    assert pending(root, "research", "peer")["items"] == []
    with pytest.raises((OSError, ValueError)):
        acknowledge(root, "research", "peer", worker_id, "adopt", "Wrong recipient")

    peer_receipt = deliver(root, registry, session=session, turn=turn, request=peer)
    peer_id = peer_receipt["request_id"]
    assert peer_id != worker_id
    acknowledge(root, "research", "worker", worker_id, "adopt", "Worker decision")
    assert not pending(root, "research", "peer")["items"][0].get(
        "receiver_decision_recorded", False
    )
    acknowledge(root, "research", "peer", peer_id, "reject", "Peer decision")

    rows = query(root, registry, goal_ids=["research"], owner_scope=True)["rows"]
    assert {row["agent_id"]: row["decision"]["status"] for row in rows} == {
        "worker": "adopt",
        "peer": "reject",
    }
    for agent_id, request_id in (("worker", worker_id), ("peer", peer_id)):
        assert [
            row["request_id"] for row in pending(root, "research", agent_id)["items"]
        ] == [request_id]


def test_new_request_round_preserves_the_previous_receiver_decision(fixture):
    root, registry, session, turn, request = fixture
    first = deliver(root, registry, session=session, turn=turn, request=request)
    acknowledge(
        root, "research", "worker", first["request_id"], "adopt", "First round"
    )
    corrected_turn = {
        **turn,
        "client_turn_id": "request-two",
        "message": "Reconsider the method with this corrected acceptance criterion.",
    }
    second = deliver(
        root, registry, session=session, turn=corrected_turn, request=request
    )
    assert second["request_id"] != first["request_id"]
    items = {
        row["request_id"]: row
        for row in pending(root, "research", "worker")["items"]
    }
    assert set(items) == {first["request_id"], second["request_id"]}
    assert items[first["request_id"]]["receiver_decision_recorded"] is True
    assert items[second["request_id"]]["message"] == corrected_turn["message"]
    assert not items[second["request_id"]].get("receiver_decision_recorded", False)
    acknowledge(
        root, "research", "worker", second["request_id"], "defer", "Assess correction"
    )
    replay = deliver(root, registry, session=session, turn=turn, request=request)
    assert replay["request_id"] == first["request_id"] and replay["replayed"]

    rows = query(root, registry, goal_ids=["research"], owner_scope=True)["rows"]
    assert {row["request_id"]: row["decision"]["status"] for row in rows} == {
        first["request_id"]: "adopt",
        second["request_id"]: "defer",
    }


@pytest.mark.parametrize("decision", ["no_change", "adopt"])
def test_hook_keeps_decided_requests_open_until_receiver_returns_conclusion(
    fixture, decision
):
    root, registry, session, turn, request = fixture
    receipt = deliver(root, registry, session=session, turn=turn, request=request)
    dispatch = dispatch_turn_start_hooks(
        [turn_start_hook(root, registry, "research", "worker")]
    )
    assert not dispatch["failures"]
    assert dispatch["required_reads"][0]["ordering"] == "before_work"
    assert "manager-inbox read" in dispatch["required_reads"][0]["command"]
    assert turn["message"] not in json.dumps(dispatch)
    assert dispatch["results"][0]["agent_read_required"]
    acknowledge(
        root,
        "research",
        "worker",
        receipt["request_id"],
        decision,
        "Current experiment still has stronger evidence; retain its order.",
    )
    assert pending(root, "research", "worker")["items"][0]["receiver_decision_recorded"]
    from loopx.capabilities.manager_context.roundtrip import report

    report(
        root,
        "research",
        "worker",
        receipt["request_id"],
        "conclusion",
        "Current evidence supports retaining the existing experiment; no plan change.",
    )
    assert not pending(root, "research", "worker")["items"]
    assert not dispatch_turn_start_hooks(
        [turn_start_hook(root, registry, "research", "worker")]
    )["required_reads"]
    assert deliver(root, registry, session=session, turn=turn, request=request)[
        "replayed"
    ]
    assert not pending(root, "research", "worker")["items"]
    with pytest.raises((ValueError, OSError)):
        acknowledge(
            root, "other", "peer", receipt["request_id"], "adopt", "Wrong target"
        )


def test_actual_manager_turn_delivers_and_reports_host_receipt(fixture, monkeypatch):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    import loopx.chat_manager_context as manager_context

    root, registry, _session, turn, request = fixture
    original_registry = registry.read_bytes()
    store = ChatSessionStore(root)
    controller = ChatRuntimeController(
        store=store, codex_bin="codex", registry_path=registry
    )

    class Adapter:
        upstream_thread_id = "fixture-upstream"

        def healthcheck(self):
            return True

        def close_session(self):
            pass

        def start_turn(self, message, sink):
            assert "context_delegation" in message
            return {
                "message": "Preparing handoff",
                "context_handoff": request,
                "proposals": [],
                "gate": None,
            }

    monkeypatch.setattr(
        controller,
        "capabilities",
        lambda: [
            {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"}
        ],
    )
    monkeypatch.setattr(controller, "_start_adapter", lambda **kw: Adapter())
    monkeypatch.setattr(
        manager_context,
        "collect_manager_turn_context",
        lambda *a, **_: {"coverage": {}, "goals": []},
    )
    try:
        session, _ = controller.open_session(
            goal_id="loopx-manager",
            agent_id="codex",
            work_dir=root,
            objective="manager",
            mode="resume_latest",
            channel_id="manager",
        )
        accepted, created = controller.submit_turn(
            session_id=session["session_id"],
            client_turn_id=turn["client_turn_id"],
            message=turn["message"],
            work_dir=root,
            objective="manager",
        )
        completed = controller.wait_for_turn(
            session_id=session["session_id"], turn_id=accepted["turn_id"], timeout_sec=5
        )
        assert created and completed["status"] == "completed", completed
        response = completed["response"]
        assert response["context_handoff_receipt"]["status"] == "delivered"
        assert "已将原消息交给 worker" in response["message"]
        assert response["proposals"] == [] and response["gate"] is None
        assert len(pending(root, "research", "worker")["items"]) == 1
        assert registry.read_bytes() == original_registry
    finally:
        controller.close()


def test_provider_wrapper_is_not_forwarded_and_large_registry_is_supported(fixture):
    root, registry, session, turn, request = fixture
    data = json.loads(registry.read_text())
    data["unrelated_metadata"] = "x" * 180000
    registry.write_text(json.dumps(data))
    assert request in authority(root, registry, session, turn)["targets"]
    session["channel_id"] = "manager.external.group"
    turn["origin"] = "lark"
    _write(
        _root(root) / "policy.json",
        {
            "schema_version": POLICY_SCHEMA,
            "sources": {
                session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [request]}
            },
        },
    )
    register_ingress(
        root,
        session_id=session["session_id"],
        client_turn_id=turn["client_turn_id"],
        channel=session["channel_id"],
        sender_id="owner",
        message=turn["message"],
        source_id="lark:original",
        source_message="Original user intent",
    )
    deliver(root, registry, session=session, turn=turn, request=request)
    assert (
        pending(root, "research", "worker")["items"][0]["message"]
        == "Original user intent"
    )


def test_lark_bridge_registers_provenance_before_queueing(fixture):
    from types import SimpleNamespace
    from loopx.extensions.lark.goal_topic_runtime import answer_lark_goal_topic

    root, registry, session, _turn, request = fixture
    session.update(channel_id="manager.external.group", agent_id="codex", status="open")
    _write(
        _root(root) / "policy.json",
        {
            "schema_version": POLICY_SCHEMA,
            "sources": {
                session["channel_id"]: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [request]}
            },
        },
    )

    class Controller:
        store = SimpleNamespace(root=root / "chat", load_session=lambda _sid: session)

        def enqueue_turn(self, **kw):
            assert kw["origin"] == "lark"
            assert authority(root, registry, session, kw)["targets"] == [request]
            deliver(root, registry, session=session, turn=kw, request=request)
            return {"turn_id": "fixture-turn"}, True

        def wait_for_turn(self, **_kw):
            return {"status": "completed", "response": {"message": "Delivered"}}

    route = {
        "goal_id": "manager",
        "session_id": session["session_id"],
        "conversation_kind": "manager",
        "executor_endpoint_id": "codex",
        "manager_channel_id": session["channel_id"],
        "ingress_mode": "session_queue",
        "source_sender_id": "owner",
        "message_id": "provider-original",
        "topic_root_message_id": "topic",
    }
    assert (
        answer_lark_goal_topic(
            route=route,
            text="Original intent",
            work_dir=root,
            objective="manager",
            runtime_controller=Controller(),
        )
        == "Delivered"
    )
    assert (
        pending(root, "research", "worker")["items"][0]["message"] == "Original intent"
    )


def test_sender_bound_default_delivers_across_goals_and_new_registration(fixture):
    root, registry, session, turn, target = fixture
    channel = "manager.external." + "d" * 24
    session = {**session, "channel_id": channel}
    turn = {**turn, "origin": "lark"}
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {"schema_version": POLICY_SCHEMA,
                        "sources": {channel: {"sender_ids": ["owner"]}}})
    register_ingress(root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
                     channel=channel, sender_id="owner", message=turn["message"], source_id="lark:default-request")
    other = {"goal_id": "other", "agent_id": "peer"}
    assert authority(root, registry, session, turn)["targets"] == [other, target]
    receipt = deliver(root, registry, session=session, turn=turn, request=other)
    assert receipt["status"] == "delivered"
    assert pending(root, "other", "peer")["items"][0]["message"] == turn["message"]
    data = json.loads(registry.read_text())
    data["goals"].append({"id": "new-goal", "repo": str(root),
                          "coordination": {"registered_agents": ["new-worker"]}})
    registry.write_text(json.dumps(data))
    newcomer = {"goal_id": "new-goal", "agent_id": "new-worker"}
    assert newcomer in authority(root, registry, session, turn)["targets"]
    original = policy_path.read_bytes()
    preview = configure_delivery_target(root, registry, channel=channel, **other, grant=False)
    assert preview["granted_before"] and preview["would_change"]
    assert policy_path.read_bytes() == original
    configure_delivery_target(root, registry, channel=channel, **other, grant=False, execute=True)
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, registry, session=session, turn=turn, request=other)
    configure_delivery_target(root, registry, channel=channel, goal_id="other", grant=True, execute=True)
    assert other not in authority(root, registry, session, turn)["targets"]
    configure_delivery_target(root, registry, channel=channel, **other, grant=True, execute=True)
    assert deliver(root, registry, session=session, turn=turn, request=other)["request_id"] == receipt["request_id"]
