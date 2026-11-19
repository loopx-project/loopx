"""Actual adapter selection must also select its default model and effort."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx import chat_runtime
from loopx.chat_manager import MANAGER_AGENT_GOAL_ID, manager_model_config


@pytest.mark.parametrize("endpoint", ["codex", "dsh"])
def test_explicit_endpoint_selects_defaults_without_changing_overrides(endpoint):
    environment = {
        "LOOPX_MANAGER_ENDPOINT": "dsh" if endpoint == "codex" else "codex",
        "LOOPX_TURN_MODEL": "managed-fixture-model",
        "LOOPX_TURN_REASONING_EFFORT": "low",
    }
    expected = (
        {"model": "managed-fixture-model", "reasoning_effort": "low"}
        if endpoint == "dsh"
        else {"model": "gpt-6.1-sol", "reasoning_effort": "xhigh"}
    )
    assert manager_model_config(environment, endpoint=endpoint) == expected
    assert manager_model_config(
        {
            **environment,
            "LOOPX_MANAGER_MODEL": "explicit-model",
            "LOOPX_MANAGER_REASONING_EFFORT": "medium",
        },
        endpoint=endpoint,
    ) == {"model": "explicit-model", "reasoning_effort": "medium"}
    assert manager_model_config(
        environment,
        endpoint=endpoint,
        machine_defaults={
            "executor_model": "machine-model",
            "executor_reasoning_effort": "xhigh",
        },
    ) == {"model": "machine-model", "reasoning_effort": "xhigh"}


def test_managed_adapter_uses_its_profile_when_machine_default_is_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    environment = {
        "LOOPX_TURN_MODEL": "managed-fixture-model",
        "LOOPX_TURN_REASONING_EFFORT": "low",
    }
    monkeypatch.setattr(
        chat_runtime,
        "operator_credential_resolution",
        lambda _: {"environ": environment},
    )
    monkeypatch.setattr(chat_runtime, "operator_credential_pair", lambda _: {})
    controller = SimpleNamespace(
        hard_timeout_sec=10,
        steward_executor_defaults=lambda: {"executor_endpoint": "codex"},
        _session_objective=lambda **kwargs: kwargs["objective"],
    )
    for goal in (MANAGER_AGENT_GOAL_ID, "synthetic-project"):
        adapter = chat_runtime.ChatRuntimeController._start_adapter(
            controller,
            agent_id="dsh",
            work_dir=tmp_path,
            goal_id=goal,
            objective="Read synthetic evidence",
        )
        assert isinstance(adapter, chat_runtime.DshChatAdapter)
        assert (adapter.model, adapter.reasoning_effort) == (
            "managed-fixture-model",
            "low",
        )
