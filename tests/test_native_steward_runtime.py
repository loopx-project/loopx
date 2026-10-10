"""Private owner audience and explicit host grant meet at the native Session.

The real Core, configuration store and app-server RPC path are exercised with
synthetic provider/model fixtures, not live credentials or model acceptance.
"""
import json

import pytest

from test_native_steward_private import steward, finish  # noqa: F401
from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


def apply_profile(root, profile):
    arguments = {"runtime_root": root, "registry": build_builtin_machine_configuration_registry(),
        "configuration": {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "manager_runtime": {"schema_version": "manager_runtime_profile_v0", "runtime_profile": profile}}}}
    preview = configure_machine_configuration(**arguments, execute=False)
    result = configure_machine_configuration(**arguments, execute=True,
        expected_plan_revision=preview["plan_revision"])
    assert result["readback_verified"]


def test_private_steward_uses_machine_grant_and_continues_without_widening_other_app(steward):  # noqa: F811
    store, runtime, provider, transport, binding, capture, _ = steward

    def admit(name, text):
        assert transport.admit("steward-app", provider.event("steward-app", name, text))["status"] == "durably_accepted"
        row = next(item for item in transport.core.pending() if item["message"] == text)
        assert finish(runtime, row)["status"] == "completed"
        return store.load_session(row["session_id"])

    first = admit("default", "Remember the owner's initial request.")
    assert first["manager_runtime_profile"] == "restricted"
    apply_profile(store.root.parent, "trusted_owner")
    trusted = admit("authorized", "Use the available tools for this authorized request.")
    assert trusted["session_id"] == first["session_id"]
    readback = store.public_session(trusted)["manager_runtime"]
    assert readback["runtime_profile"] == "trusted_owner"
    assert readback["sandbox"] == "danger-full-access"
    assert readback["standing_grant"] == "machine_configuration"
    assert {"shell", "filesystem", "web"} <= set(readback["tool_classes"])
    followup = admit("continued", "Continue the same conversation.")
    assert followup["session_id"] == trusted["session_id"]
    assert followup["upstream_thread_id"] == trusted["upstream_thread_id"]
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [call["params"] for call in calls if call.get("method") == "thread/start"]
    assert [call["sandbox"] for call in starts] == ["read-only", "danger-full-access"]
    prompts = [call["params"]["input"][0]["text"] for call in calls if call.get("method") == "turn/start"]
    assert "normal tools and skills" in prompts[-1]
    assert "Remember the owner's initial request." in prompts[1]
    assert "Do not inspect arbitrary repositories" not in prompts[-1]
    transport.reconcile()
    assert all(item[0] == "steward-app" for item in provider.writes)

    transport.admit("notes-app", provider.event("notes-app", "separate", "An independent project request."))
    notes = next(item for item in transport.core.pending() if item["message"] == "An independent project request.")
    assert finish(runtime, notes)["status"] == "completed"
    assistant = store.load_session(notes["session_id"])
    assert assistant["session_id"] != trusted["session_id"]
    assert assistant["project_context"]["grant"] == "workspace_write"
    assert store.public_session(assistant)["manager_runtime"] is None
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [call["params"]["sandbox"] for call in calls if call.get("method") == "thread/start"][-1] == "workspace-write"

    runtime.close()
    restarted = ChatRuntimeController(store=ChatSessionStore(store.root.parent), codex_bin=runtime.codex_bin,
        project_contexts=runtime.project_contexts, registry_path=runtime.registry_path,
        manager_scope_resolver=transport.bindings.steward_scope)
    transport.bindings.controller = restarted
    source = {"source_ref": trusted["steward_context"]["source_ref"],
        "sender_ref": binding["operator_ref"], "private_human_message": True}

    def reopen():
        return restarted.open_session(goal_id=None, agent_id="codex", work_dir=store.root, objective="",
            mode="resume_latest", conversation_binding_id=binding["binding_id"], source_context=source)[0]

    try:
        resumed = reopen()
        assert resumed["session_id"] == trusted["session_id"]
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        rpc = [call["params"] for call in calls if call.get("method") == "thread/resume"][-1]
        assert rpc["threadId"] == trusted["upstream_thread_id"]
        assert rpc["sandbox"] == "danger-full-access"
        apply_profile(store.root.parent, "restricted")
        revoked = reopen()
        assert revoked["session_id"] == trusted["session_id"]
        assert revoked["manager_runtime_sandbox"] == "read-only"
        assert revoked["manager_runtime_standing_grant"] == "none"
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        assert [call["params"]["sandbox"] for call in calls if call.get("method") == "thread/start"][-1] == "read-only"
        assert "Remember the owner's initial request." in str(store.messages(revoked["session_id"]))
    finally:
        restarted.close()


def test_private_owner_proof_is_rechecked_before_reusing_a_healthy_host(steward):  # noqa: F811
    store, runtime, provider, transport, binding, capture, _ = steward
    apply_profile(store.root.parent, "trusted_owner")
    transport.admit("steward-app", provider.event("steward-app", "first", "An authorized request."))
    row = transport.core.pending()[0]
    assert finish(runtime, row)["status"] == "completed"
    session = store.load_session(row["session_id"])
    saved = session["steward_context"]
    with pytest.raises(ValueError, match="audience changed"):
        runtime.manager_runtime_profile("manager.external.other", steward_context=saved)
    assert runtime.manager_runtime_profile(session["channel_id"])["runtime_profile"] == "restricted"
    source = {"source_ref": saved["source_ref"], "sender_ref": binding["operator_ref"], "private_human_message": False}
    with pytest.raises(ValueError, match="audience"):
        transport.bindings.resolve(binding_id=binding["binding_id"], **source)
    group_event = provider.event("steward-app", "group", "A group request.")
    group_event["chat_type"] = "group"
    assert transport.admit("steward-app", group_event)["status"] == "audience_rejected"
    before = capture.read_bytes()
    observation = transport.bindings.observe
    transport.bindings.observe = lambda profile: {**observation(profile), "verified": False}
    with pytest.raises(ValueError, match="verified"):
        runtime.enqueue_turn(session_id=session["session_id"], client_turn_id="identity-lost", message="more",
            work_dir=store.root, objective="", origin="lark")
    assert store.turn_for_client(session["session_id"], "identity-lost") is None
    assert capture.read_bytes() == before
    transport.bindings.observe = observation
    transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    with pytest.raises(ValueError, match="no longer authorized"):
        runtime.manager_runtime_profile(session["channel_id"], steward_context=saved)
    assert capture.read_bytes() == before


def test_workspace_only_steward_cannot_inherit_unbounded_host_grant(steward):  # noqa: F811
    store, runtime, provider, transport, _, capture, _ = steward
    runtime.project_contexts.filesystem_scope = "workspace_only"
    apply_profile(store.root.parent, "trusted_owner")
    transport.admit("steward-app", provider.event("steward-app", "bounded", "Stay within the public workspace."))
    row = transport.core.pending()[0]
    assert finish(runtime, row)["status"] == "completed"
    session = store.load_session(row["session_id"])
    assert session["manager_runtime_profile"] == "restricted"
    assert session["manager_runtime_standing_grant"] == "none"
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [call["params"]["sandbox"] for call in calls if call.get("method") == "thread/start"] == ["read-only"]


def test_portfolio_scope_refresh_preserves_the_verified_private_host_profile(steward):  # noqa: F811
    store, runtime, provider, transport, _, capture, workspace = steward
    apply_profile(store.root.parent, "trusted_owner")

    def admit(name, text):
        transport.admit("steward-app", provider.event("steward-app", name, text))
        row = next(item for item in transport.core.pending() if item["message"] == text)
        assert finish(runtime, row)["status"] == "completed"
        return store.load_session(row["session_id"])

    first = admit("empty", "Read the currently authorized portfolio.")
    runtime.registry_path.write_text(json.dumps({"schema_version": "0.1", "goals": [
        {"id": "new-project", "repo": str(workspace)}]}))
    refreshed = admit("expanded", "Read the newly registered project as well.")
    assert refreshed["session_id"] == first["session_id"]
    assert refreshed["manager_authorization_scope_id"] != first["manager_authorization_scope_id"]
    assert transport.bindings.session_context(refreshed["steward_context"])["context"]["goal_ids"] == ["new-project"]
    assert refreshed["manager_runtime_profile"] == "trusted_owner"
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert all(call["params"]["sandbox"] == "danger-full-access"
        for call in calls if call.get("method") in {"thread/start", "thread/resume"})
