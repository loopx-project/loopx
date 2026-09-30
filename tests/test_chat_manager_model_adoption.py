"""A machine model edit reaches existing conversations at a Turn boundary.

The machine configuration and Chat store are real, isolated native stores.
Only the paid upstream adapter is substituted; release model evaluation owns
actual answer quality, separately from this lifecycle regression.
"""

import pytest

from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_manager import manager_channel_binding, manager_executor_allocation
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


def configure(root, *, model, effort, endpoint="codex"):
    document = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {"steward_executor": {
            "schema_version": "steward_executor_machine_defaults_v1",
            "selection_policy": "preferred",
            "eligible_endpoints": [],
            "executor_endpoint": endpoint,
            "executor_model": model,
            "executor_reasoning_effort": effort,
        }},
    }
    registry = build_builtin_machine_configuration_registry()
    preview = configure_machine_configuration(
        runtime_root=root, configuration=document, registry=registry, execute=False,
    )
    result = configure_machine_configuration(
        runtime_root=root, configuration=document, registry=registry, execute=True,
        expected_plan_revision=preview["plan_revision"],
    )
    assert result["readback_verified"] is True


class Adapter:
    def __init__(self, index):
        self.upstream_thread_id = f"upstream-{index}"
        self.closed = False

    def healthcheck(self):
        return not self.closed

    def close_session(self):
        self.closed = True


@pytest.fixture
def conversation(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    configure(root, model="gpt-6-sol", effort="xhigh")
    store = ChatSessionStore(root)
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    monkeypatch.setattr(runtime, "capabilities", lambda: [
        {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"},
    ])
    starts = []

    def start(**kwargs):
        adapter = Adapter(len(starts))
        starts.append((kwargs, adapter))
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start)
    session, _ = runtime.open_session(
        goal_id="loopx-manager", agent_id="codex", work_dir=tmp_path,
        objective="manager", mode="new", channel_id="manager.external.fixture",
        manager_executor_allocation=manager_executor_allocation(runtime, environ={}),
    )
    return root, store, runtime, session, starts


def test_idle_conversation_adopts_model_preserving_context_and_authority(conversation):
    root, store, runtime, session, starts = conversation
    store.append_message(session["session_id"], role="user", text="Keep using public sources.")
    store.append_message(session["session_id"], role="agent", text="Understood.")
    configure(root, model="gpt-6.1-sol", effort="high")
    runtime._ensure_adapter(session, work_dir=root, objective="manager")
    updated = store.load_session(session["session_id"])
    assert starts[0][1].closed
    assert starts[1][0]["executor_model"] == {"model": "gpt-6.1-sol", "reasoning_effort": "high"}
    assert starts[1][0]["resume_thread_id"] is None
    assert starts[1][0]["history"] == [
        {"role": "user", "content": "Keep using public sources."},
        {"role": "assistant", "content": "Understood."},
    ]
    assert updated["channel_id"] == session["channel_id"]
    assert updated["agent_id"] == session["agent_id"]
    assert updated["manager_runtime_profile"] == "restricted"
    assert updated["manager_executor_allocation"]["executor_endpoint"] == "codex"
    binding = manager_channel_binding({}, session=updated, machine_defaults=runtime.steward_executor_defaults())
    assert (binding["model"], binding["reasoning_effort"]) == ("gpt-6.1-sol", "high")
    runtime._ensure_adapter(updated, work_dir=root, objective="manager")
    assert len(starts) == 2


@pytest.mark.parametrize("turn_status", ["queued", "starting", "running", "interrupting"])
def test_model_edit_never_replaces_an_in_flight_turn(conversation, turn_status):
    root, store, runtime, session, starts = conversation
    # Store transitions are covered independently; this fixture models the
    # boundary's public Turn readback without starting a paid provider.
    turn, _ = store.create_turn(session["session_id"], client_turn_id="original", message="Research this.")
    store.update_turn(session["session_id"], turn["turn_id"], status=turn_status)
    busy = store.update_session(session["session_id"], active_turn_id=turn["turn_id"], status="busy")
    configure(root, model="gpt-6.1-sol", effort="high")
    assert runtime._ensure_adapter(busy, work_dir=root, objective="manager") is starts[0][1]
    assert not starts[0][1].closed and len(starts) == 1
    assert store.load_session(session["session_id"])["manager_executor_allocation"]["model"] == "gpt-6-sol"
    assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == turn_status


def test_accepted_queued_turn_can_adopt_before_starting_upstream(conversation):
    root, store, runtime, session, starts = conversation
    turn, _ = store.create_turn(session["session_id"], client_turn_id="next", message="Continue.")
    queued = store.load_session(session["session_id"])
    configure(root, model="gpt-6.1-sol", effort="high")
    with runtime._session_adapter_lock(session["session_id"]):
        runtime._ensure_adapter_locked(
            queued, work_dir=root, objective="manager", accepted_turn_id=turn["turn_id"],
        )
    assert len(starts) == 2
    assert starts[1][0]["executor_model"]["model"] == "gpt-6.1-sol"
    assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == "queued"


def test_other_endpoint_edit_preserves_existing_endpoint_and_model(conversation):
    root, _store, runtime, session, starts = conversation
    configure(root, model="other-provider-model", effort="high", endpoint="dsh")
    assert runtime._ensure_adapter(session, work_dir=root, objective="manager") is starts[0][1]
    assert not starts[0][1].closed and len(starts) == 1


def test_failed_new_adapter_does_not_claim_the_new_model(conversation, monkeypatch):
    root, store, runtime, session, _starts = conversation
    configure(root, model="gpt-6.1-sol", effort="high")

    def fail(**_kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(runtime, "_start_adapter", fail)
    with pytest.raises(CodexChatAgentError, match="could not be restored"):
        runtime._ensure_adapter(session, work_dir=root, objective="manager")
    updated = store.load_session(session["session_id"])
    assert updated["status"] == "resume_failed"
    assert updated["manager_executor_allocation"]["model"] == "gpt-6-sol"
