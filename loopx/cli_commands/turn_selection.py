from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

from ..control_plane.turn_driver import LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION
from ..control_plane.turn_driver.host_binding import managed_executor_binding


def resolve_turn_resume_session_binding(
    args: Any,
) -> tuple[bool, dict[str, Any] | None]:
    """Resolve the explicit resume binding for one Turn as ``(requested, binding)``.

    A resume names the exact Goal, Agent, and Todo session to continue. A partial
    resume is refused rather than completed from ambient context, because the
    binding is what the Turn journal fence compares against.

    ``requested`` reports that the caller named a resume identity. That is not the
    same fact as "a session binding exists": a run-once Codex CLI Turn derives one
    from its envelope, and that derived binding must not be read back as an
    explicit resume request.
    """

    identity = {
        "goal_id": args.resume_goal_id,
        "agent_id": args.resume_agent_id,
        "todo_id": args.resume_todo_id,
    }
    supplied = [name for name, value in identity.items() if value is not None]
    if not supplied:
        return False, None
    if len(supplied) != len(identity):
        raise ValueError(
            "resume planning requires --resume-goal-id, --resume-agent-id, "
            "and --resume-todo-id together"
        )
    return (
        True,
        {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            **identity,
        },
    )


def turn_controller_advisory_primary(
    decision: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Resolve the default for Turn's model-free outer-controller phase."""

    interaction = decision.get("interaction_contract")
    cli_channel = (
        interaction.get("cli_channel")
        if isinstance(interaction, Mapping)
        else None
    )
    if not isinstance(cli_channel, Mapping) or (
        cli_channel.get("selection_required") is not True
    ):
        return None
    portfolio = decision.get("action_portfolio")
    if not isinstance(portfolio, Mapping) or (
        portfolio.get("schema_version") != "quota_action_portfolio_v2"
    ):
        raise ValueError(
            "Turn action selection requires a typed advisory action portfolio"
        )
    policy = portfolio.get("selection_policy")
    primary = portfolio.get("primary")
    todo_id = (
        str(primary.get("todo_id") or "").strip()
        if isinstance(primary, Mapping)
        else ""
    )
    if (
        not isinstance(policy, Mapping)
        or policy.get("requires_explicit_turn_binding") is not True
        or not todo_id
    ):
        raise ValueError("Turn advisory action portfolio has no bindable primary")
    return todo_id, dict(portfolio)


def managed_executor_cli_binding(
    args: argparse.Namespace, *, environ: Mapping[str, str],
) -> dict[str, Any]:
    """Adapt parsed CLI host options to the existing executor read-model owner.

    The readback names where model work runs and whether the host can launch.
    An explicit runner is a launchability fact owned by this CLI adapter.
    Machine authentication is resolved by the caller independently of any
    Goal runtime override and shared with the eventual host launch.
    """
    return managed_executor_binding(
        args.host,
        # The credential a managed Turn authenticates with is this
        # machine's resolved pair, not whatever the invoking shell happens
        # to export: the readback above the launch and the launch itself
        # have to name the same credential.
        environ=environ,
        dsh_runner_configured=bool(getattr(args, "dsh_runner", None)),
        provider=(
            getattr(args, "dsh_provider", None)
            if args.host == "dsh"
            else None
        ),
        model=(
            getattr(args, "dsh_model", None)
            if args.host == "dsh"
            else getattr(args, "codex_model", None)
            if args.host == "codex-cli"
            else None
        ),
        reasoning_effort=(
            getattr(args, "dsh_reasoning_effort", None)
            if args.host == "dsh"
            else getattr(args, "codex_reasoning_effort", None)
            if args.host == "codex-cli"
            else None
        ),
        max_tokens=(
            getattr(args, "dsh_max_tokens", None)
            if args.host == "dsh"
            else None
        ),
        codex_operation_tools=bool(getattr(args, "codex_operation_tools", False)),
        codex_sandbox=getattr(args, "codex_sandbox", "read-only"),
    )
