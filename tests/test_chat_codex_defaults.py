"""Configured Goal Chat defaults use native settings without retargeting work."""
from types import SimpleNamespace

import pytest

from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
from loopx.capabilities.native_chat.machine_defaults import CODEX_CHAT_DEFAULTS_SCHEMA_VERSION, normalize_codex_chat_defaults
from loopx.chat_runtime import ChatRuntimeController, CodexAppServerAdapter
from loopx.chat_store import ChatSessionStore


def configure(root, **fields):
    kwargs = dict(runtime_root=root, configuration={"schema_version": "loopx_machine_configuration_v0",
        "namespaces": {"codex_chat": {"schema_version": CODEX_CHAT_DEFAULTS_SCHEMA_VERSION, **fields}}},
        registry=build_builtin_machine_configuration_registry())
    preview = configure_machine_configuration(**kwargs)
    return configure_machine_configuration(**kwargs, execute=True, expected_plan_revision=preview["plan_revision"])


def runtime_fixture(tmp_path, monkeypatch):
    base, gpt = tmp_path / ".codex-ds", tmp_path / ".codex-gpt"
    base.mkdir()
    gpt.mkdir()
    (gpt / "config.toml").write_text('model = "gpt-6.1-sol"\nmodel_reasoning_effort = "max"\n')
    monkeypatch.setenv("LOOPX_CHAT_CODEX_HOME", str(base))
    monkeypatch.setenv("LOOPX_CODEX_HOMES", str(gpt))
    root = tmp_path / "runtime"
    runtime = ChatRuntimeController(store=ChatSessionStore(root), codex_bin="fixture")
    monkeypatch.setattr(runtime, "capabilities", lambda: [
        {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"},
    ])
    calls = []

    def start(**kwargs):
        calls.append(kwargs)
        settings = kwargs.get("agent_config") or {}
        return CodexAppServerAdapter(SimpleNamespace(model=settings.get("model") or "inherited-model",
            resolved_model=settings.get("model") or "inherited-model",
            resolved_reasoning_effort=settings.get("reasoning_effort"), service_tier=settings.get("service_tier"),
            thread_id=f"native-{len(calls)}", process=SimpleNamespace(poll=lambda: None), close=lambda: None))

    monkeypatch.setattr(runtime, "_start_adapter", start)
    return runtime, root, base, gpt, calls


def test_model_effort_and_speed_are_separate_and_reversible_defaults(tmp_path, monkeypatch):
    runtime, root, base, gpt, calls = runtime_fixture(tmp_path, monkeypatch)
    open_args = dict(goal_id="fixture", agent_id="codex", work_dir=tmp_path, objective="Inspect.")
    try:
        old, _ = runtime.open_session(**open_args, mode="new")
        configure(root, codex_profile="codex-gpt", model="gpt-6.1-sol", reasoning_effort="high", service_tier="fast")
        selected, reused = runtime.open_session(**open_args, mode="resume_latest")
        assert not reused and selected["session_id"] != old["session_id"]
        assert selected["codex_home"] == str(gpt)
        assert calls[-1]["codex_home_override"] == gpt
        assert {key: calls[-1]["agent_config"][key] for key in ("model", "reasoning_effort", "service_tier")} == {
            "model": "gpt-6.1-sol", "reasoning_effort": "high", "service_tier": "fast"}
        public = runtime.store.public_session(selected)
        assert (public["agent_model_id"], public["agent_reasoning_effort"], public["agent_service_tier"]) == (
            "gpt-6.1-sol", "high", "fast")
        assert "codex_home" not in public and "codex_chat_configuration" not in public
        assert runtime.store.load_session(old["session_id"])["codex_home"] == str(base)
        # Changing the machine default cannot redirect an exact old Session.
        runtime.adapters.clear()
        runtime.resume_session(session_id=old["session_id"], work_dir=tmp_path, objective="Inspect.")
        assert calls[-1]["codex_home_override"] == base
        configure(root)  # Empty settings restore the original service defaults.
        restored, _ = runtime.open_session(**open_args, mode="resume_latest")
        assert restored["codex_home"] == str(base)
        assert (gpt / "config.toml").read_text() == 'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "max"\n'
        assert not runtime.store.messages(selected["session_id"])
    finally:
        runtime.close()


def test_new_defaults_do_not_replace_an_active_turn_or_its_native_home(tmp_path, monkeypatch):
    runtime, root, base, _gpt, calls = runtime_fixture(tmp_path, monkeypatch)
    args = dict(goal_id="fixture", agent_id="codex", work_dir=tmp_path, objective="Inspect.")
    try:
        session, _ = runtime.open_session(**args, mode="new")
        runtime.store.create_turn(session["session_id"], client_turn_id="working", message="Existing work")
        configure(root, codex_profile="codex-gpt", model="gpt-6.1-sol", reasoning_effort="high", service_tier="fast")
        selected, reused = runtime.open_session(**args, mode="resume_latest")
        assert reused and selected["session_id"] == session["session_id"]
        assert selected["codex_home"] == str(base) and len(calls) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("binding", ["active", "agent_loop", "attached", "task"])
def test_unavailable_new_profile_does_not_break_an_existing_execution_binding(tmp_path, monkeypatch, binding):
    runtime, root, base, _gpt, calls = runtime_fixture(tmp_path, monkeypatch)
    args = dict(goal_id="fixture", agent_id="codex", work_dir=tmp_path, objective="Inspect.")
    if binding == "task":
        args["channel_id"] = "task.fixture"
    try:
        if binding == "attached":
            session = runtime.store.create_session(goal_id="fixture", agent_id="codex",
                adapter_kind="codex_app_server", upstream_thread_id="original-host",
                session_mode="attached_host", host_surface="codex_app", channel_id="goal.fixture")
        else:
            session, _ = runtime.open_session(**args, mode="new")
        if binding == "active":
            runtime.store.create_turn(session["session_id"], client_turn_id="working", message="Keep existing work")
        elif binding == "agent_loop":
            from loopx.chat_runtime import CODEX_GOAL_CHAT_MODE
            runtime.store.update_session(session["session_id"], upstream_mode=CODEX_GOAL_CHAT_MODE)
        configure(root, codex_profile="codex-no-longer-installed", model="gpt-6.1-sol")
        selected, reused = runtime.open_session(**args, mode="resume_latest")
        assert reused and selected["session_id"] == session["session_id"]
        assert len(calls) == (0 if binding == "attached" else 1)
        assert selected.get("codex_home") == (None if binding == "attached" else str(base))
    finally:
        runtime.close()


def test_missing_profile_is_actionable_for_a_new_ordinary_conversation(tmp_path, monkeypatch):
    runtime, root, _base, _gpt, calls = runtime_fixture(tmp_path, monkeypatch)
    try:
        configure(root, codex_profile="codex-no-longer-installed", model="gpt-6.1-sol")
        with pytest.raises(ValueError, match="profile is unavailable or ambiguous"):
            runtime.open_session(goal_id="fixture", agent_id="codex", work_dir=tmp_path, objective="Inspect.", mode="new")
        assert calls == []
    finally:
        runtime.close()


@pytest.mark.parametrize("fields", [
    {"codex_profile": "../../private"}, {"codex_profile": "/tmp/account"},
    {"reasoning_effort": "invented"}, {"service_tier": "fast\nnext"},
    {"model": "bad\tmodel"}, {"api_key": "not-a-setting"},
])
def test_invalid_profiles_or_hidden_settings_fail_before_native_start(fields):
    with pytest.raises(ValueError):
        normalize_codex_chat_defaults({"schema_version": CODEX_CHAT_DEFAULTS_SCHEMA_VERSION, **fields})
