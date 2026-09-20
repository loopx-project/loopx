"""The default Turn host follows the operator credential; explicit selection wins."""

from __future__ import annotations

import pytest

from loopx.cli import build_parser
from loopx.control_plane.operator_credential import configured_operator_credential
from loopx.control_plane.turn_driver.host_binding import (
    INDIVIDUAL_TURN_HOST,
    MANAGED_TURN_HOST,
    TURN_HOST_ENV_VAR,
    TURN_HOST_SOURCE_EXPLICIT_CONFIG,
    TURN_HOST_SOURCE_NO_OPERATOR_CREDENTIAL,
    TURN_HOST_SOURCE_OPERATOR_CREDENTIAL,
    resolve_default_turn_host,
    selected_turn_host,
)


def test_managed_credential_selects_the_managed_default_host():
    assert MANAGED_TURN_HOST == "dsh"
    environ = {"DEEPSEEK_API_KEY": "sk-operator"}

    assert resolve_default_turn_host(environ) == MANAGED_TURN_HOST
    assert selected_turn_host(environ) == (
        MANAGED_TURN_HOST,
        TURN_HOST_SOURCE_OPERATOR_CREDENTIAL,
    )


@pytest.mark.parametrize(
    "environ",
    [
        {"DEEPSEEK_API_KEY": ""},
        {"DEEPSEEK_API_KEY": "   "},
        {"DEEPSEEK_BASE_URL": "https://example.invalid"},
        {},
    ],
)
def test_no_usable_credential_defaults_to_the_individual_host(environ):
    """Without an operator credential the default is the host that can run."""

    assert INDIVIDUAL_TURN_HOST == "codex-cli"
    assert resolve_default_turn_host(environ) == INDIVIDUAL_TURN_HOST
    assert selected_turn_host(environ) == (
        INDIVIDUAL_TURN_HOST,
        TURN_HOST_SOURCE_NO_OPERATOR_CREDENTIAL,
    )


@pytest.mark.parametrize(
    "environ",
    [
        {TURN_HOST_ENV_VAR: "codex-cli", "DEEPSEEK_API_KEY": "sk-operator"},
        {TURN_HOST_ENV_VAR: "codex-cli"},
        {TURN_HOST_ENV_VAR: "dsh", "DEEPSEEK_API_KEY": ""},
    ],
)
def test_an_explicit_selection_ignores_the_credential(environ):
    """A credential resolves the shipped default only, never an explicit host."""

    assert selected_turn_host(environ) == (
        environ[TURN_HOST_ENV_VAR],
        TURN_HOST_SOURCE_EXPLICIT_CONFIG,
    )
    assert resolve_default_turn_host(environ) == environ[TURN_HOST_ENV_VAR]


def test_explicit_config_repoints_the_default_host():
    environ = {TURN_HOST_ENV_VAR: "codex-cli", "DEEPSEEK_API_KEY": "sk-operator"}

    assert selected_turn_host(environ) == (
        "codex-cli",
        TURN_HOST_SOURCE_EXPLICIT_CONFIG,
    )
    assert resolve_default_turn_host(environ) == "codex-cli"


def test_configured_credential_names_the_env_var():
    assert (
        configured_operator_credential({"DEEPSEEK_API_KEY": "sk-operator"})
        == "DEEPSEEK_API_KEY"
    )
    assert configured_operator_credential({}) is None


def _turn_argv(command: str) -> list[str]:
    argv = ["turn", command, "--goal-id", "goal-x", "--agent-id", "agent-x"]
    if command == "run-once":
        argv.extend(["--project", "."])
    return argv


@pytest.mark.parametrize("command", ["plan", "run-once"])
@pytest.mark.parametrize(
    "environ, expected_host",
    [
        ({}, "codex-cli"),
        ({"DEEPSEEK_API_KEY": "sk-operator"}, "dsh"),
        ({"DEEPSEEK_API_KEY": "   "}, "codex-cli"),
    ],
)
def test_cli_default_follows_the_operator_credential(
    command, environ, expected_host, monkeypatch
):
    for name in ("DEEPSEEK_API_KEY", TURN_HOST_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)

    assert build_parser().parse_args(_turn_argv(command)).host == expected_host


@pytest.mark.parametrize("command", ["plan", "run-once"])
def test_explicit_config_environment_repoints_the_cli_default(command, monkeypatch):
    monkeypatch.setenv(TURN_HOST_ENV_VAR, "codex-cli")

    assert build_parser().parse_args(_turn_argv(command)).host == "codex-cli"


def test_explicit_host_flag_wins_over_the_default(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-operator")
    monkeypatch.setenv(TURN_HOST_ENV_VAR, "dsh")

    args = build_parser().parse_args([*_turn_argv("run-once"), "--host", "generic-cli"])

    assert args.host == "generic-cli"


def test_codex_managed_agent_profile_is_explicit_cli_configuration():
    args = build_parser().parse_args(
        [
            *_turn_argv("run-once"),
            "--host",
            "codex-cli",
            "--codex-model",
            "gpt-5.6-sol",
            "--codex-reasoning-effort",
            "xhigh",
        ]
    )

    assert args.codex_model == "gpt-5.6-sol"
    assert args.codex_reasoning_effort == "xhigh"


@pytest.mark.parametrize("command", ["plan", "run-once"])
def test_default_execution_mode_follows_the_selected_host(command, monkeypatch):
    for name in ("DEEPSEEK_API_KEY", TURN_HOST_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    individual_default = build_parser().parse_args(_turn_argv(command))

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-operator")
    managed = build_parser().parse_args(_turn_argv(command))

    # The managed host runs bounded headless Turns; pairing it with a visible
    # interactive mode would make that default unschedulable.
    # run-once ships only the isolated-headless mode, so it keeps that either way.
    assert managed.host == MANAGED_TURN_HOST
    assert managed.execution_mode == "isolated-headless"
    assert individual_default.host == INDIVIDUAL_TURN_HOST
    assert individual_default.execution_mode == (
        "interactive-visible" if command == "plan" else "isolated-headless"
    )
