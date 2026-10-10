"""Verified native sources can reach the existing, separately granted inbox."""
import json

import pytest

from test_native_steward_private import steward, finish  # noqa: F401
from loopx.capabilities.manager_context import (
    POLICY_SCHEMA, _hash, _root, _write, authority, configure_delivery_target,
    configure_evidence_scope, deliver, pending,
)


def ingress_path(store, row):
    return _root(store.root.parent) / "ingress" / (
        _hash([row["session_id"], "external-" + row["request_ref"]]) + ".json")


@pytest.mark.parametrize("separate_coordination_root", [False, True])
def test_native_ingress_uses_coordination_owner_with_legacy_fallback(steward, monkeypatch, tmp_path, separate_coordination_root):  # noqa: F811
    store, runtime, provider, transport, binding, _, workspace = steward
    root = tmp_path / "coordination" if separate_coordination_root else store.root.parent
    if separate_coordination_root:
        runtime.coordination_runtime_root = root
    target = {"goal_id": "registered-work", "agent_id": "worker"}
    registry = json.loads(runtime.registry_path.read_text())
    registry["runtime_root"] = str(root)
    registry["goals"] = [{"id": target["goal_id"], "repo": str(workspace),
                          "coordination": {"registered_agents": [target["agent_id"]]}}]
    runtime.registry_path.write_text(json.dumps(registry))
    # Observe provenance at the actual enqueue boundary, before model execution.
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **kwargs: None)
    enqueue = runtime.enqueue_turn
    saved = []

    def observe(**kwargs):
        path = _root(root) / "ingress" / (_hash([kwargs["session_id"], kwargs["client_turn_id"]]) + ".json")
        saved.append(path.read_bytes())
        return enqueue(**kwargs)

    monkeypatch.setattr(runtime, "enqueue_turn", observe)
    event = provider.event("steward-app", "canonical", "Original request for the registered worker")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    session = store.load_session(row["session_id"])
    turn = store.load_turn(row["session_id"], row["turn_id"])
    assert turn["status"] == "queued" and len(saved) == 1
    if separate_coordination_root:
        assert not ingress_path(store, row).exists()
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {"schema_version": POLICY_SCHEMA, "sources": {session["channel_id"]: {
        "sender_ids": [binding["operator_ref"]], "local_delivery_scope": "selected", "targets": [],
    }}})
    assert authority(root, runtime.registry_path, session, turn)["targets"] == []
    configure_evidence_scope(root, runtime.registry_path, channel=session["channel_id"],
                             goal_ids=[target["goal_id"]], execute=True)
    configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"],
                              **target, grant=True, execute=True)
    receipt = deliver(root, runtime.registry_path, session=session, turn=turn, request=target)
    assert pending(root, **target)["items"][0]["message"] == event["content"]
    assert deliver(root, runtime.registry_path, session=session, turn=turn,
                   request=target) == {**receipt, "replayed": True}
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    assert len(saved) == 1 and len(store.list_sessions()) == 1
    # A sibling App's project source cannot acquire manager provenance or policy.
    monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
    transport.admit("notes-app", provider.event("notes-app", "other", "Other audience"))
    notes = next(item for item in transport.core.pending() if item["message"] == "Other audience")
    assert not authority(root, runtime.registry_path, store.load_session(notes["session_id"]),
                         store.load_turn(notes["session_id"], notes["turn_id"]))["targets"]
    configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"],
                              **target, grant=False, execute=True)
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, runtime.registry_path, session=session, turn=turn, request=target)


def test_verified_ingress_precedes_model_launch_and_survives_admission_replay(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, binding, _, _ = steward
    enqueue = runtime.enqueue_turn
    observations = []

    def interrupted(**kwargs):
        row = transport.core.pending()[0]
        saved = json.loads(ingress_path(store, row).read_text())
        assert saved["client_turn_id"] == kwargs["client_turn_id"]
        assert saved["sender_id"] == binding["operator_ref"]
        assert saved["source_id"] == row["request_ref"]
        assert saved["message_digest"] == _hash(kwargs["message"])
        observations.append(saved)
        enqueue(**kwargs)
        raise OSError("owner interrupted after canonical admission")

    event = provider.event("steward-app", "first", "Original owner context")
    monkeypatch.setattr(runtime, "enqueue_turn", interrupted)
    with pytest.raises(OSError):
        transport.admit("steward-app", event)
    prepared = transport.core.pending()[0]
    before = ingress_path(store, prepared).read_bytes()
    turn = store.turn_for_client(prepared["session_id"], "external-" + prepared["request_ref"])
    assert finish(runtime, {**prepared, "turn_id": turn["turn_id"]})["status"] == "completed"
    monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    accepted = transport.core.pending()[0]
    assert accepted["session_id"] == prepared["session_id"] and accepted["turn_id"] == turn["turn_id"]
    assert ingress_path(store, accepted).read_bytes() == before and len(observations) == 1
    assert len(store.list_sessions()) == 1
    # A regular project source cannot create manager provenance or a Goal.
    transport.admit("notes-app", provider.event("notes-app", "notes", "Private project context"))
    notes = next(row for row in transport.core.pending() if row["message"] == "Private project context")
    assert finish(runtime, notes)["status"] == "completed"
    assert not ingress_path(store, notes).exists()


def test_native_steward_uses_separate_sender_target_grants_and_exact_return_route(steward):  # noqa: F811
    store, runtime, provider, transport, binding, _, workspace = steward
    target = {"goal_id": "fresh-commission", "agent_id": "operations"}
    registry = json.loads(runtime.registry_path.read_text())
    registry["goals"] = [{"id": target["goal_id"], "repo": str(workspace),
                          "coordination": {"registered_agents": [target["agent_id"]]}}]
    runtime.registry_path.write_text(json.dumps(registry))
    registry_before = runtime.registry_path.read_bytes()
    event = provider.event("steward-app", "source", "Read this source; return private findings, never publish.")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    assert finish(runtime, row)["status"] == "completed"
    session = store.load_session(row["session_id"])
    turn = store.load_turn(row["session_id"], row["turn_id"])
    root = store.root.parent
    # The verified owner's default covers registered recipients; an explicit
    # selected source still needs a separate delivery grant below.
    assert authority(root, runtime.registry_path, session, turn)["targets"] == [target]
    policy = {"schema_version": POLICY_SCHEMA, "sources": {session["channel_id"]: {
        "sender_ids": [binding["operator_ref"]], "local_delivery_scope": "selected", "targets": [],
    }}}
    policy_path = _root(root) / "policy.json"
    _write(policy_path, policy)
    # Reading a portfolio does not confer delivery authority.
    configure_evidence_scope(root, runtime.registry_path, channel=session["channel_id"],
                             goal_ids=[target["goal_id"]], execute=True)
    assert authority(root, runtime.registry_path, session, turn)["targets"] == []
    preview = configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"], **target, grant=True)
    assert preview["would_change"] and not preview["executed"]
    assert authority(root, runtime.registry_path, session, turn)["targets"] == []
    applied = configure_delivery_target(root, runtime.registry_path, channel=session["channel_id"], **target, grant=True, execute=True)
    assert applied["readback_verified"]
    assert authority(root, runtime.registry_path, session, turn)["targets"] == [target]
    assert not authority(root, runtime.registry_path, session, {**turn, "message": "forged"})["targets"]
    assert not authority(root, runtime.registry_path, session, {**turn, "origin": "web"})["targets"]
    receipt = deliver(root, runtime.registry_path, session=session, turn=turn, request=target)
    assert not receipt["todo_created"] and not receipt["execution_interrupted"]
    assert deliver(root, runtime.registry_path, session=session, turn=turn, request=target) == {**receipt, "replayed": True}
    assert pending(root, **target)["items"][0]["message"] == event["content"]
    assert runtime.registry_path.read_bytes() == registry_before
    assert policy_path.stat().st_mode & 0o777 == 0o600
    # The independently verified second App cannot use this channel's grant.
    transport.admit("notes-app", provider.event("notes-app", "notes", "Other private audience"))
    notes = next(row for row in transport.core.pending() if row["message"] == "Other private audience")
    assert finish(runtime, notes)["status"] == "completed"
    assert not authority(root, runtime.registry_path, store.load_session(notes["session_id"]),
                         store.load_turn(notes["session_id"], notes["turn_id"]))["targets"]
    policy["sources"][session["channel_id"]]["targets"] = [target]
    policy["sources"][session["channel_id"]]["sender_ids"] = ["another-App-owner"]
    _write(policy_path, policy)
    with pytest.raises(ValueError, match="not authorized"):
        deliver(root, runtime.registry_path, session=session, turn=turn, request=target)


@pytest.mark.parametrize("channel", ["manager.external.native", "manager.external.native." + "a" * 24,
                                    "manager.external.native." + "a" * 24 + ".*",
                                    "manager.external." + "a" * 23])
def test_configuration_rejects_partial_or_wildcard_audiences(tmp_path, channel):
    with pytest.raises(ValueError, match="exact external"):
        configure_evidence_scope(tmp_path, tmp_path / "missing.json", channel=channel, goal_ids=[])
    with pytest.raises(ValueError, match="exact external"):
        configure_delivery_target(tmp_path, tmp_path / "missing.json", channel=channel,
                                  goal_id="fresh-commission", agent_id="operations", grant=True)


def test_inbox_bound_does_not_truncate_or_reject_ordinary_native_chat(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, _, _, _ = steward
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **kwargs: None)
    message = "x" * 32001
    assert transport.admit("steward-app", provider.event("steward-app", "large", message))["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    turn = store.load_turn(row["session_id"], row["turn_id"])
    assert turn["message"] == message and turn["status"] == "queued"
    assert not ingress_path(store, row).exists()
    assert not authority(store.root.parent, runtime.registry_path, store.load_session(row["session_id"]), turn)["targets"]
