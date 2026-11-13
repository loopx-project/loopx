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
from loopx.chat_manager import (
    MANAGER_CONTEXT_VERSION,
    manager_channel_binding,
    manager_executor_allocation,
    open_manager_session,
)
from loopx.chat_runtime import ChatRuntimeController, CodexAppServerAdapter
from loopx.chat_store import ChatSessionStore


def configure(root, *, model, effort, endpoint="codex", policy="preferred"):
    document = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {"steward_executor": {
            "schema_version": "steward_executor_machine_defaults_v1",
            "selection_policy": policy,
            "eligible_endpoints": ["antigravity", "codex"] if policy == "flexible" else [],
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


def test_api_primary_route_has_fresh_observation_without_reading_argon_quota(conversation, monkeypatch):
    import sys
    import time
    from types import ModuleType, SimpleNamespace
    import loopx.chat_manager as manager
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter

    root, _, runtime, _, _ = conversation
    configure(root, model="gemini-4-argon-high", effort="high", endpoint="antigravity", policy="flexible")
    calls = []
    module = ModuleType("loopx_codex_provider_routing.native")

    def route(**inputs):
        calls.append(inputs)
        return {"endpoint": "codex", "blocked_primary": None}

    module.NativeAccounts = lambda _: SimpleNamespace(steward_route=route, model_transport=lambda **_: None)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(manager, "operator_credential_resolution", lambda _: {"environ": {}})
    monkeypatch.setattr(AntigravityAdapter, "metadata_review_capacity",
        lambda **_: pytest.fail("API primary must not wait for an unrelated native quota read"))
    before = time.time()
    result = manager.manager_session_quota_route(runtime, {"agent_id": "antigravity", "upstream_thread_id": "original"})
    after = time.time()
    assert result["allocation"]["executor_endpoint"] == "codex"
    assert before <= result["observed_at"] <= after
    assert len(calls) == 1 and calls[0]["primary_capacity"] is None
    runtime.close()


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
    assert len(store.messages(session["session_id"])) == 2
    assert updated["channel_id"] == session["channel_id"]
    assert updated["agent_id"] == session["agent_id"]
    assert updated["manager_runtime_profile"] == "restricted"
    assert updated["manager_executor_allocation"]["executor_endpoint"] == "codex"
    binding = manager_channel_binding({}, session=updated, machine_defaults=runtime.steward_executor_defaults())
    assert (binding["model"], binding["reasoning_effort"]) == ("gpt-6.1-sol", "high")
    runtime._ensure_adapter(updated, work_dir=root, objective="manager")
    assert len(starts) == 2


def test_legacy_bound_steward_adopts_explicit_machine_model_once(conversation):
    root, store, runtime, session, starts = conversation
    sid = session["session_id"]
    import json
    legacy = {**store.load_session(sid), "agent_model_id": "gpt-6-sol", "agent_reasoning_effort": "xhigh"}
    legacy.pop("manager_executor_allocation", None)
    store._session_path(sid).write_text(json.dumps(legacy))  # retained pre-allocation disk format
    store.append_message(sid, role="user", text="Preserve the source of the requirement.")
    configure(root, model="gpt-6.1-sol", effort="high")
    runtime._ensure_adapter(legacy, work_dir=root, objective="manager")
    current = store.load_session(sid)
    assert starts[-1][0]["executor_model"] == {"model": "gpt-6.1-sol", "reasoning_effort": "high"}
    assert starts[-1][0]["history"] == [{"role": "user", "content": "Preserve the source of the requirement."}]
    assert current["channel_id"] == session["channel_id"]
    assert current["manager_executor_allocation"]["configured_model"] == "gpt-6.1-sol"
    count = len(starts)
    runtime._ensure_adapter(current, work_dir=root, objective="manager")
    assert len(starts) == count


def test_legacy_model_adoption_preserves_unset_fields_and_endpoint(conversation):
    from loopx.chat_manager import manager_session_model_allocation
    root, store, runtime, session, _ = conversation
    legacy = {**session, "manager_executor_allocation": None,
              "agent_model_id": "gpt-6-sol", "agent_reasoning_effort": "xhigh"}
    configure(root, model=None, effort="high")
    allocation = manager_session_model_allocation(runtime, legacy)
    assert allocation["model"] == "gpt-6-sol" and allocation["reasoning_effort"] == "high"
    configure(root, model="gemini-4-argon-high", effort="high", endpoint="antigravity")
    assert manager_session_model_allocation(runtime, legacy) is None


def test_readback_observation_does_not_cache_later_allocation(conversation, monkeypatch):
    _, store, runtime, session, _ = conversation
    before = store.load_session(session["session_id"])
    observed = []

    def capabilities():
        available = bool(observed)
        observed.append(available)
        return [{"agent_id": "codex", "available": available}]

    monkeypatch.setattr(runtime, "capabilities", capabilities)
    assert manager_executor_allocation(runtime, environ={},
        endpoint_capabilities=[{"agent_id": "codex", "available": True}])["available"] is True
    # An explicitly empty observation remains unknown, without a hidden retry.
    assert manager_executor_allocation(runtime, environ={}, endpoint_capabilities=[])["available"] is None
    assert observed == []
    assert manager_executor_allocation(runtime, environ={})["available"] is False
    assert manager_executor_allocation(runtime, environ={})["available"] is True
    assert observed == [False, True]
    assert store.load_session(session["session_id"]) == before


def test_restart_migrates_legacy_history_once_preserving_source_and_native_provenance(conversation):
    root, store, runtime, session, starts = conversation
    sid = session["session_id"]
    for index in range(40):
        store.append_message(sid, role="user" if index % 2 == 0 else "agent",
            text=f"Retain original constraint and result {index}.")
    before = store.messages(sid)
    legacy = store.update_session(sid, manager_context_version=20)
    runtime.adapters.pop(sid).close_session()  # the existing service restarted
    runtime._ensure_adapter(legacy, work_dir=root, objective="manager")
    current = store.load_session(sid)
    assert starts[-1][0]["resume_thread_id"] is None
    assert current["manager_context_version"] == MANAGER_CONTEXT_VERSION
    assert current["upstream_context_history"] == [{"thread_id": session["upstream_thread_id"],
        "manager_context_version": 20}]
    assert [item["content"] for item in starts[-1][0]["history"]] == [item["text"] for item in before]
    assert all(item["text"] not in starts[-1][0]["objective"] for item in before)
    assert store.messages(sid) == before and current["channel_id"] == session["channel_id"]
    runtime._ensure_adapter(current, work_dir=root, objective="manager")
    assert len(starts) == 2 and store.load_session(sid)["upstream_context_history"] == current["upstream_context_history"]
    with pytest.raises(ValueError, match="preserve"):
        store.update_session(sid, upstream_context_history=[])


@pytest.mark.parametrize("turn_status", ["starting", "running", "interrupting"])
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


@pytest.mark.parametrize("explicit_acceptance", [False, True])
def test_accepted_queued_turn_can_adopt_before_starting_upstream(conversation, explicit_acceptance):
    root, store, runtime, session, starts = conversation
    turn, _ = store.create_turn(session["session_id"], client_turn_id="next", message="Continue.")
    queued = store.update_session(session["session_id"], active_turn_id=turn["turn_id"], status="busy")
    configure(root, model="gpt-6.1-sol", effort="high")
    with runtime._session_adapter_lock(session["session_id"]):
        runtime._ensure_adapter_locked(
            queued, work_dir=root, objective="manager",
            **({"accepted_turn_id": turn["turn_id"]} if explicit_acceptance else {}),
        )
    assert len(starts) == 2 and starts[0][1].closed
    assert store.load_session(session["session_id"])["active_turn_id"] == turn["turn_id"]
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


@pytest.fixture
def upstream_boundary(monkeypatch, tmp_path):
    """Keep public session opening and real history/model assembly in the path."""
    root = tmp_path / "runtime"
    store = ChatSessionStore(root)
    starts = []

    def start(**kwargs):
        adapter = Adapter(len(starts))
        if kwargs.get("resume_thread_id"):
            adapter.upstream_thread_id = kwargs["resume_thread_id"]
        starts.append((kwargs, adapter))
        return adapter

    monkeypatch.setattr(CodexAppServerAdapter, "start", start)

    def controller():
        runtime = ChatRuntimeController(store=store, codex_bin="codex")
        monkeypatch.setattr(runtime, "capabilities", lambda: [
            {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"},
        ])
        return runtime

    def open_conversation(runtime):
        return open_manager_session(
            controller=runtime, goal_id="loopx-manager", work_dir=tmp_path,
            provider="fixture", audience="owner",
        )[0]

    return root, store, controller, open_conversation, starts


@pytest.mark.parametrize("refresh_context", [False, True])
def test_model_edit_keeps_early_context_through_public_resume(upstream_boundary, refresh_context):
    root, store, controller, open_conversation, starts = upstream_boundary
    configure(root, model="gpt-6-sol", effort="xhigh")
    runtime = controller()
    session = open_conversation(runtime)
    messages = ["Use public sources only; never publish this report."] + [
        f"Research note {index}." for index in range(13)
    ]
    for text in messages:
        store.append_message(session["session_id"], role="user", text=text)
    if refresh_context:
        store.update_session(session["session_id"], manager_context_version=MANAGER_CONTEXT_VERSION - 1)
    configure(root, model="gpt-6.1-sol", effort="high")

    updated = open_conversation(runtime)

    assert (starts[-1][0]["model"], starts[-1][0]["reasoning_effort"]) == ("gpt-6.1-sol", "high")
    assert len(store.messages(session["session_id"])) == 14
    # Model adoption, including an accompanying context/tool migration, starts
    # fresh with the whole visible conversation. Empty native threads need not
    # have a persisted rollout, so a model edit cannot assume resumability.
    assert starts[-1][0]["resume_thread_id"] is None
    assert updated["upstream_thread_id"] != session["upstream_thread_id"]
    # Native history is restored once, separately from the repeated objective.
    assert [item["content"] for item in starts[-1][0]["history"]] == messages
    assert all(text not in starts[-1][0]["objective"] for text in messages)


@pytest.mark.parametrize("policy,effort,expected_effort", [
    ("pinned", None, "low"),
    ("preferred", "medium", "medium"),
])
def test_restart_then_policy_or_effort_edit_keeps_unedited_model(
    upstream_boundary, monkeypatch, policy, effort, expected_effort,
):
    root, _store, controller, open_conversation, starts = upstream_boundary
    configure(root, model=None, effort=None)
    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gpt-6-sol")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "low")
    session = open_conversation(controller())
    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    restarted = controller()
    restored = open_conversation(restarted)
    assert restored["manager_executor_allocation"]["model"] == "gpt-6-sol"
    before = len(starts)

    configure(root, model=None, effort=effort, policy=policy)
    updated = open_conversation(restarted)

    allocation = updated["manager_executor_allocation"]
    assert (allocation["model"], allocation["reasoning_effort"]) == ("gpt-6-sol", expected_effort)
    if effort is None:
        assert updated["upstream_thread_id"] == session["upstream_thread_id"]
        assert len(starts) == before  # Policy-only edits do not restart a provider.
    else:
        assert updated["upstream_thread_id"] != session["upstream_thread_id"]
    again = open_conversation(controller())
    assert again["manager_executor_allocation"] == allocation


def test_model_only_edit_keeps_persisted_effort(upstream_boundary, monkeypatch):
    root, _store, controller, open_conversation, starts = upstream_boundary
    configure(root, model=None, effort=None)
    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gpt-6-sol")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "low")
    open_conversation(controller())
    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    configure(root, model="gpt-6-luna", effort=None)

    updated = open_conversation(controller())

    assert updated["manager_executor_allocation"]["reasoning_effort"] == "low"
    assert (starts[-1][0]["model"], starts[-1][0]["reasoning_effort"]) == ("gpt-6-luna", "low")


@pytest.mark.parametrize("clear_model", [True, False])
def test_explicit_clear_resolves_only_the_cleared_field(upstream_boundary, monkeypatch, clear_model):
    root, _store, controller, open_conversation, starts = upstream_boundary
    configure(root, model="gpt-6.1-sol", effort="high")
    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gpt-6-sol")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "low")
    runtime = controller()
    open_conversation(runtime)
    configure(root, model=None if clear_model else "gpt-6.1-sol", effort="high" if clear_model else None)

    updated = open_conversation(runtime)

    expected = ("gpt-6-sol", "high") if clear_model else ("gpt-6.1-sol", "low")
    assert (starts[-1][0]["model"], starts[-1][0]["reasoning_effort"]) == expected
    assert (updated["manager_executor_allocation"]["model"],
            updated["manager_executor_allocation"]["reasoning_effort"]) == expected


def test_same_effective_value_still_records_explicit_selection_before_clear(upstream_boundary, monkeypatch):
    root, _store, controller, open_conversation, starts = upstream_boundary
    configure(root, model=None, effort=None)
    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gpt-6-sol")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "low")
    runtime = controller()
    open_conversation(runtime)
    configure(root, model=None, effort="low")
    open_conversation(runtime)
    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    configure(root, model=None, effort=None)

    updated = open_conversation(runtime)

    assert (starts[-1][0]["model"], starts[-1][0]["reasoning_effort"]) == ("gpt-6-sol", "high")
    assert updated["manager_executor_allocation"]["model"] == "gpt-6-sol"


@pytest.mark.parametrize("status,client,fault", [("queued", "new-input", None), ("running", "new-input", None),
    ("queued", "external-original-recovery-22-quota-1", None),
    ("queued", "new-input", "startup"), ("queued", "new-input", "configuration")])
def test_quota_fallback_keeps_original_session_and_only_routes_unstarted_input(monkeypatch, tmp_path, status, client, fault):
    import loopx.chat_runtime as runtime_module
    root = tmp_path / "runtime"
    configure(root, model="gemini-4-argon-high", effort="high", endpoint="antigravity", policy="flexible")
    store = ChatSessionStore(root)
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    capabilities = [{"agent_id": name, "available": True, "adapter_kind": kind} for name, kind in
        [("antigravity", "antigravity_ls"), ("codex", "codex_app_server")]]
    monkeypatch.setattr(runtime, "capabilities", lambda: capabilities)
    starts = []

    def start(**kwargs):
        if len(starts) == 1 and fault == "startup":
            raise ValueError("Synthetic provider startup failure")
        if len(starts) == 1 and fault == "configuration":
            configure(root, model="gemini-4-argon-high", effort="high", endpoint="antigravity", policy="pinned")
        adapter = Adapter(len(starts))
        adapter.current_model_id = kwargs["executor_model"]["model"]
        starts.append((kwargs, adapter))
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start)
    original, _ = runtime.open_session(goal_id="loopx-manager", agent_id="antigravity",
        work_dir=tmp_path, objective="manager", mode="new", channel_id="manager.external.fixture",
        manager_executor_allocation=manager_executor_allocation(runtime, environ={}))
    sid = original["session_id"]
    store.update_session(sid, agent_model_id="gemini-4-argon-high")
    store.append_message(sid, role="user", text="Keep the original audience and outstanding work.")
    turn, _ = store.create_turn(sid, client_turn_id=client, message="New work.")
    store.update_turn(sid, turn["turn_id"], status=status)
    queued = store.update_session(sid, active_turn_id=turn["turn_id"], status="busy")
    fallback = manager_executor_allocation(runtime, environ={}, endpoint_capabilities=[
        {**row, "available": row["agent_id"] == "codex"} for row in capabilities])
    observed = []
    monkeypatch.setattr(runtime_module, "manager_session_quota_route", lambda *args:
        observed.append(True) or {"allocation": fallback, "blocked_primary": None})
    before = store.load_turn(sid, turn["turn_id"])
    if fault:
        with pytest.raises(CodexChatAgentError):
            runtime._ensure_adapter(queued, work_dir=root, objective="manager")
        held = store.load_session(sid)
        assert held["agent_id"] == "antigravity" and held["upstream_thread_id"] == original["upstream_thread_id"]
        assert not held.get("upstream_context_history")
        recorded = store.load_turn(sid, turn["turn_id"])
        preparation = recorded.pop("native_context_preparation")
        assert recorded == before
        assert preparation["source_thread_id"] == original["upstream_thread_id"]
        assert preparation["planned_method"] == "thread/start" and preparation["executor_reallocated"] is True
        return
    runtime._ensure_adapter(queued, work_dir=root, objective="manager")
    current = store.load_session(sid)
    if status == "queued" and client == "new-input":
        assert observed == [True] and len(starts) == 2
        assert current["agent_id"] == current["executor_endpoint_id"] == "codex"
        assert current["agent_model_id"] == "gpt-6.1-sol" and current["upstream_mode"] == "chat"
        assert current["channel_id"] == original["channel_id"] and current["active_turn_id"] == turn["turn_id"]
        assert current["upstream_context_history"][-1]["thread_id"] == original["upstream_thread_id"]
        assert current["upstream_context_history"][-1]["agent_id"] == "antigravity"
        assert starts[-1][0]["agent_id"] == "codex" and starts[-1][0]["resume_thread_id"] is None
        assert starts[-1][0]["history"] == [{"role": "user", "content": "Keep the original audience and outstanding work."}]
    else:
        assert observed == [] and len(starts) == 1 and current["agent_id"] == "antigravity"
    recorded = store.load_turn(sid, turn["turn_id"])
    preparation = recorded.pop("native_context_preparation", None)
    assert recorded == before
    if status == "queued" and client == "new-input":
        assert preparation["source_thread_id"] == original["upstream_thread_id"]
        assert preparation["planned_method"] == "thread/start" and preparation["history_message_count"] == 1
    else:
        assert preparation is None

    if status == "queued" and client == "new-input":
        store.update_turn(sid, turn["turn_id"], status="completed")
        store.update_session(sid, active_turn_id=None, status="ready")
        next_turn, _ = store.create_turn(sid, client_turn_id="after-reset", message="Next authorized input")
        store.update_turn(sid, next_turn["turn_id"], status="queued")
        queued = store.update_session(sid, active_turn_id=next_turn["turn_id"], status="busy")
        primary = manager_executor_allocation(runtime, environ={})
        monkeypatch.setattr(runtime_module, "manager_session_quota_route", lambda *args:
            {"allocation": primary, "blocked_primary": None})
        runtime._ensure_adapter(queued, work_dir=root, objective="manager")
        restored = store.load_session(sid)
        assert restored["agent_id"] == "antigravity" and restored["agent_model_id"] == "gemini-4-argon-high"
        assert [item["agent_id"] for item in restored["upstream_context_history"]] == ["antigravity", "codex"]
        assert restored["channel_id"] == original["channel_id"]


def test_proven_quota_recovery_opens_fresh_context_when_codex_already_owns_the_session(conversation, monkeypatch):
    from types import SimpleNamespace
    import loopx.chat_runtime as runtime_module
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations

    root, store, runtime, session, starts = conversation
    configure(root, model="gemini-4-argon-high", effort="high", endpoint="antigravity", policy="flexible")
    capabilities = [{"agent_id": name, "available": name == "codex", "adapter_kind": kind} for name, kind in
        [("antigravity", "antigravity_ls"), ("codex", "codex_app_server")]]
    monkeypatch.setattr(runtime, "capabilities", lambda: capabilities)
    allocation = manager_executor_allocation(runtime, environ={}, endpoint_capabilities=capabilities)
    session = store.update_session(session["session_id"], manager_executor_allocation=allocation,
        agent_model_id="gpt-6.1-sol")
    assert runtime_module.manager_session_model_allocation(runtime, session) is None
    store.append_message(session["session_id"], role="user", text="Keep the original source and completed work.")
    turn, _ = store.create_queued_turn(session["session_id"], client_turn_id="external-original-recovery-22-quota-1",
        message="Continue the proven unstarted request", origin="lark")
    session = store.update_session(session["session_id"], active_turn_id=turn["turn_id"], status="busy")
    runtime.project_contexts.conversation_bindings = SimpleNamespace()
    monkeypatch.setattr(ChatExternalConversations, "queued_recovery_context", lambda *_:
        {"quota_attempt": 1, "quota_executor": "codex"})
    monkeypatch.setattr(runtime_module, "manager_session_quota_route", lambda *_:
        {"allocation": allocation, "blocked_primary": None})
    runtime._ensure_adapter(session, work_dir=root, objective="manager")
    assert len(starts) == 2 and starts[0][1].closed
    assert starts[1][0]["resume_thread_id"] is None  # A formatter's native permissions cannot become business permissions.
    assert starts[1][0]["read_only_review"] is False and starts[1][0]["metadata_review_context"] is None
    assert starts[1][0]["history"] == [{"role": "user", "content": "Keep the original source and completed work."}]
    assert store.load_turn(session["session_id"], turn["turn_id"])["status"] == "queued"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_native_account_epoch_reopens_only_unstarted_codex_adapter(conversation, monkeypatch, status):
    import sys
    from types import ModuleType
    root, store, runtime, session, starts = conversation
    sid = session["session_id"]
    parent, module = ModuleType("loopx_codex_provider_routing"), ModuleType("loopx_codex_provider_routing.native")
    epoch = ["new-owner-revision", "subscription-b"]
    observed = []
    class Accounts:
        def __init__(self, path):
            assert path == runtime.coordination_runtime_root
        def model_transport(self, **_parameters):
            return None
        def conversation_epoch(self, *, codex_home, consumer="worker", model=None):
            assert consumer == "steward"
            observed.append(codex_home)
            return tuple(epoch)
    module.NativeAccounts = Accounts
    monkeypatch.setitem(sys.modules, parent.__name__, parent)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    turn, _ = store.create_turn(sid, client_turn_id="new-message", message="Current authorized work")
    store.update_turn(sid, turn["turn_id"], status=status)
    queued = store.update_session(sid, active_turn_id=turn["turn_id"], status="busy")
    before = store.load_turn(sid, turn["turn_id"])
    runtime._ensure_adapter(queued, work_dir=root, objective="manager")
    if status == "queued":
        assert len(starts) == 2 and observed == [runtime.codex_home]
        assert starts[-1][0]["resume_thread_id"] == session["upstream_thread_id"]
        assert starts[-1][0]["history"] is None
        # Same owner revision does not churn the connection on an idle read.
        runtime._ensure_adapter(store.load_session(sid), work_dir=root, objective="manager")
        assert len(starts) == 2
    else:
        assert len(starts) == 1 and not observed
    recorded = store.load_turn(sid, turn["turn_id"])
    preparation = recorded.pop("native_context_preparation", None)
    assert recorded == before
    if status == "queued":
        assert preparation["planned_method"] == "thread/resume" and preparation["history_message_count"] == 0
    else:
        assert preparation is None
