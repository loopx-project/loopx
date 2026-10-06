"""Open a Chat session on the channel that owns its executor and model.

A Chat entry point (the Codex App / dashboard Chat server, a CLI surface, or a
future transport) translates a request into a session; it does not decide which
executor runs the conversation. Each channel resolves that itself through its
own owner, so an entry point can never disagree with the readback the same
channel publishes:

* the steward channel resolves through :func:`chat_manager.open_manager_session`
  -- its explicit configuration, else its shipped default -- and its transcript
  is one conversation across whatever executor it currently resolves;
* a Goal-scoped channel runs on ``DEFAULT_GOAL_AGENT_ID`` when the caller makes
  no explicit pick;
* an ordinary project channel resolves an exact host workspace reference through
  Core and shares its managed Session without creating a Goal or steward scope.

An explicit pick is the caller's own choice and always travels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .chat_manager import open_manager_session

# The executor a Goal-scoped session runs on when the caller makes no explicit
# pick. The steward channel does not use this: it resolves its own default.
DEFAULT_GOAL_AGENT_ID = "codex"


def open_chat_session(
    *,
    controller: Any,
    context_kind: str,
    goal_id: str | None,
    work_dir: Path,
    objective: str,
    mode: str,
    requested_endpoint: str = "",
    project_ref: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return ``(session, resumed)`` for one entry-point request."""

    if context_kind == "manager":
        return open_manager_session(
            controller=controller,
            goal_id=goal_id,
            work_dir=work_dir,
            executor_endpoint_id=requested_endpoint or None,
            mode=mode,
        )
    if context_kind == "project":
        if goal_id is not None or not project_ref:
            raise ValueError("ordinary project conversation requires project_ref and no Goal")
        return controller.open_session(
            goal_id=None, agent_id=requested_endpoint or DEFAULT_GOAL_AGENT_ID,
            work_dir=work_dir, objective=objective, mode=mode, project_ref=project_ref,
        )
    if context_kind != "goal":
        raise ValueError("unknown conversation context")
    return controller.open_session(
        goal_id=goal_id,
        agent_id=requested_endpoint or DEFAULT_GOAL_AGENT_ID,
        work_dir=work_dir,
        objective=objective,
        mode=mode,
        channel_id=f"goal.{goal_id}",
        agent_goal_id=goal_id,
    )
