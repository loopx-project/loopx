from __future__ import annotations

from loopx.heartbeat_prompt import build_heartbeat_prompt


def test_visible_goal_delegates_settlement_then_checks_terminal_readback() -> None:
    payload = build_heartbeat_prompt(
        goal_id="terminal-settlement-fixture",
        thin=True,
        runtime_profile="codex_app_ssh_goal",
    )
    task_body = " ".join(payload["task_body"].split())

    # Ordering belongs to the live settlement contract (covered by the real CLI
    # suite), not a second static list embedded in the host objective.
    settlement = "settlement_plan.ordered_steps"
    readback = "After settlement recheck quota"
    terminal_readback = (
        "Complete visible Goal only on `should_run=false` + terminal "
        "no-follow-up"
    )

    assert task_body.index(settlement) < task_body.index(readback)
    assert terminal_readback in task_body
    assert payload["interface_budget"]["within_budget"] is True
