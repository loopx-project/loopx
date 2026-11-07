"""Keep the native Windows brief workload and executable routes within its budget."""
from __future__ import annotations

import contextlib
import io
import json
import shlex
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

from loopx.cli_runtime import print_payload
from loopx.control_plane.testing.cli_output_budget import (
    CLI_OUTPUT_MODE_VARIANT_BY_ID,
    assert_cli_output_mode_variant,
    measure_cli_output,
)
from loopx.heartbeat_prompt import build_heartbeat_prompt, render_heartbeat_prompt_markdown


GOAL_ID = "cli-output-budget-goal"
_BRIEF_COMMANDS = {
    "brief_prompt_command", "quota_guard_command", "quota_spend_command",
    "refresh_state_command", "progress_refresh_state_command",
}


def _windows_routes() -> tuple[str, str, str, str]:
    # Public synthetic spelling reproduces the real CI workload on every OS.
    # Equal raw length alone does not bound repeated JSON backslash escaping.
    parent = PureWindowsPath("C:/loopx/public/temp/runner/budget/nested/work/case")
    prefix = str(parent / "loopx-brief-")
    root = PureWindowsPath(prefix + "p" * (128 - len(prefix) - 8) + "fixture0")
    assert len(str(root)) == 128
    assert str(root).count("\\") == 9
    return (
        str(root), str(root / "runtime"),
        str(root / "project" / ".loopx" / "registry.json"),
        str(root / "project" / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"),
    )


def _prompt(*, mode: str = "brief", external_evidence_poll: bool = False) -> dict[str, Any]:
    _, runtime, registry, state = _windows_routes()
    return build_heartbeat_prompt(
        goal_id=GOAL_ID, active_state=Path(state), resolved_active_state=Path(state),
        runtime_root=runtime, registry_path=registry, **{mode: True},
        agent_id="codex-alpha", registered_agents=["codex-alpha"],
        agent_scopes=["Public CLI output qualification."],
        # The real CLI adds agent_id to the registered profile before rendering.
        agent_profile={
            "schema_version": "agent_profile_v1", "profile_role": "fixture",
            "scope": "public qualification", "agent_id": "codex-alpha",
        },
        reward_memory_enabled=False,
        available_capabilities=["external_evidence_poll"] if external_evidence_poll else None,
    )


def _render(payload: dict[str, Any], output_format: str) -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        print_payload(payload, output_format, render_heartbeat_prompt_markdown)
    return output.getvalue()


def _assert_routes(payload: dict[str, Any], expected_commands: set[str]) -> None:
    _, runtime, registry, state = _windows_routes()
    commands = {
        key: value for key, value in payload.items()
        if key.endswith("_command") and isinstance(value, str)
    }
    assert set(commands) == expected_commands
    for command in commands.values():
        argv = shlex.split(command)
        assert argv.count("--runtime-root") == argv.count("--registry") == 1
        assert argv[argv.index("--runtime-root") + 1] == runtime
        assert argv[argv.index("--registry") + 1] == registry
    assert payload["active_state"] == payload["resolved_active_state"] == state
    assert payload["runtime_root"] == runtime
    assert f"--active-state {state}" in payload["brief_prompt_command"]
    assert payload["quota_guard_command"] in payload["task_body"]
    assert payload["brief_prompt_command"].replace("--brief", "--compact", 1) in payload["task_body"]


def test_brief_keeps_complete_routes_with_128_character_windows_root() -> None:
    payload = _prompt()
    _assert_routes(payload, _BRIEF_COMMANDS)
    assert payload["interface_budget"]["within_budget"] is True
    encoded = _render(payload, "json")
    markdown = _render(payload, "markdown")
    assert json.loads(encoded) == payload
    root, _, _, _ = _windows_routes()
    assert encoded.count(json.dumps(root)[1:-1]) == 20
    # Assert the decision obligations rather than a hash of dated prose.
    # The full body must reach Markdown, with its executable guard only once.
    body = payload["task_body"]
    assert body in markdown
    assert markdown.count(payload["quota_guard_command"]) == 1
    for obligation in (
        "true=work+writeback; only false permits no-op",
        "Read agent_channel.work_context and required_reads (or envelope) before work",
        "retain original and cumulative outcome requirements",
        "reuse inline goal_acceptance, inspect only if absent, then refine",
        "Known new-Turn choice: add `--todo-id <id>` to first guard; otherwise",
        "use selection_command when required; re-enter as instructed, do/verify authorized work",
        "Settle with current settlement_plan.ordered_steps, else next_cli_actions",
        "preserve exact identities/effect ids and actual outcomes",
        "Never reconstruct static refresh/spend; ambiguous effects need readback/recovery, not blind retry",
        "Todo done≠Turn settled; no invented successor/--no-follow-up",
        "No useful independent work before an evidenced due time",
        "--status deferred --resume-when resume_at:",
        "settle original Turn, then yield to host scheduler",
        "Fresh admission on resume",
        "No spend for waiting alone",
        "Finish Goal only on terminal no-follow-up",
        "Destructive Git/production requires explicit authorization",
    ):
        assert obligation in body, obligation
    spec = CLI_OUTPUT_MODE_VARIANT_BY_ID["heartbeat_prompt_brief"]
    assert spec.max_chars == {"json": 11_400, "markdown": 9_600}
    for output_format, text in (("json", encoded), ("markdown", markdown)):
        assert_cli_output_mode_variant(
            spec, output_format=output_format, text=text,
            measurement=measure_cli_output(text, output_format=output_format),
        )
    assert "pr_review_pre_quota_command" not in payload
    assert {key for key, value in payload.items() if value is None} == {
        "runtime_profile", "scheduler_execution_context",
    }
    old_shape = {**payload, "pr_review_pre_quota_command": None}
    assert len(_render(old_shape, "json")) - len(encoded) == 35
    assert _render(old_shape, "markdown") == markdown


@pytest.mark.parametrize("mode", ["full", "compact", "brief", "thin"])
def test_authored_body_budget_does_not_depend_on_selected_windows_routes(mode: str) -> None:
    payload = _prompt(mode=mode)
    assert payload["interface_budget"]["char_count"] == len(payload["task_body"])
    assert payload["interface_budget"]["within_budget"] is True


def test_brief_external_evidence_poll_keeps_complete_prequota_command() -> None:
    payload = _prompt(external_evidence_poll=True)
    _assert_routes(payload, _BRIEF_COMMANDS | {"pr_review_pre_quota_command"})
    command = payload["pr_review_pre_quota_command"]
    assert "heartbeat-prequota" in command
    assert payload["task_body"].index(command) < payload["task_body"].index(payload["quota_guard_command"])
    assert command in _render(payload, "markdown")
    assert json.loads(_render(payload, "json"))["pr_review_pre_quota_command"] == command


@pytest.mark.parametrize("mode", ["full", "compact", "thin"])
def test_nonbrief_feature_off_keeps_existing_null_metadata_shape(mode: str) -> None:
    payload = _prompt(mode=mode)
    assert {key for key, value in payload.items() if value is None} == {
        "runtime_profile", "scheduler_execution_context", "pr_review_pre_quota_command",
    }
    assert "heartbeat-prequota" not in payload["task_body"]
    assert json.loads(_render(payload, "json"))["pr_review_pre_quota_command"] is None
