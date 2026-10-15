from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.subagent_host_adapter import (
    project_child_context_adapter,
    supported_child_context_modes,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CODEX_HOST_CONTRACT = REPO_ROOT / "examples" / "fixtures" / (
    "codex-spawn-agent-contract.public.json"
)


@pytest.mark.parametrize(
    ("context_mode", "expected_operation", "expected_arguments", "requires_session"),
    [
        pytest.param(
            "fresh",
            "spawn_agent",
            {"fork_turns": "none"},
            False,
            id="fresh_child_does_not_inherit_parent_context",
        ),
        pytest.param(
            "forked_snapshot",
            "spawn_agent",
            {"fork_turns": "all"},
            False,
            id="explicit_parent_snapshot",
        ),
    ],
)
def test_codex_child_context_adapter_maps_modes(
    context_mode: str,
    expected_operation: str,
    expected_arguments: dict[str, str],
    requires_session: bool,
) -> None:
    assert project_child_context_adapter(
        host="codex-cli",
        context_mode=context_mode,
    ) == {
        "host": "codex-cli",
        "native_operation": expected_operation,
        "arguments": expected_arguments,
        "requires_session": requires_session,
    }


def test_codex_child_context_adapter_matches_pinned_host_contract() -> None:
    contract = json.loads(CODEX_HOST_CONTRACT.read_text(encoding="utf-8"))
    for mode, expected_arguments in contract["context_mode_arguments"].items():
        adapter = project_child_context_adapter(host="codex-cli", context_mode=mode)
        assert adapter is not None
        assert adapter["arguments"] == expected_arguments
        for forbidden_argument in contract["forbidden_arguments"]:
            assert forbidden_argument not in adapter["arguments"]


def test_host_child_context_adapter_exposes_only_supported_modes() -> None:
    assert supported_child_context_modes("codex-cli") == (
        "fresh",
        "forked_snapshot",
    )
    assert supported_child_context_modes("claude-code") == ("fresh",)
    assert supported_child_context_modes("generic-cli") == ()
    assert (
        project_child_context_adapter(
            host="claude-code",
            context_mode="forked_snapshot",
        )
        is None
    )


def test_child_context_adapter_returns_isolated_native_arguments() -> None:
    first = project_child_context_adapter(
        host="codex-cli",
        context_mode="fresh",
    )
    assert first is not None
    first["arguments"]["fork_turns"] = "all"

    second = project_child_context_adapter(
        host="codex-cli",
        context_mode="fresh",
    )
    assert second is not None
    assert second["arguments"] == {"fork_turns": "none"}
    assert (
        project_child_context_adapter(
            host="codex-cli",
            context_mode="resume",
        )
        is None
    )
