"""Release-only prompt decision probes, not host execution/settlement proof."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from ...heartbeat_prompt import build_heartbeat_prompt
from ..quota.cli_projection import compact_quota_should_run_cli_payload
from ..quota.effective_action import EffectiveAction
from .action_portfolio_scenarios import external_wait_fallback_scenario_source
from .model_tool_behavior import DoubaoExecToolClient


def cases() -> list[dict]:
    # Independent semantic oracle: silence does not cancel work; a gate does;
    # required vision replan is not terminal closure. An already-transitioned
    # external wait selects its independent fallback for work. Never send
    # expected to the model, or derive it from the renderer being qualified.
    rows = (
        ("quiet_work", True, False, False, "work"),
        ("notifying_wait", False, True, False, "wait"),
        ("quiet_wait", False, False, False, "wait"),
        ("vision_replan", True, False, True, "replan"),
    )
    probes = [{
        "id": name,
        "packet": {
            "ok": True,
            "should_run": work,
            "effective_action": (
                EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value if replan
                else EffectiveAction.NORMAL_RUN.value if work else EffectiveAction.QUOTA_SKIP.value
            ),
            "execution_obligation": {"must_attempt_work": work},
            "heartbeat_recommendation": {"agent_must_attempt": work},
            "autonomous_replan_obligation": {"required": replan},
            "interaction_contract": {
                "user_channel": {"notify": "NOTIFY" if notify else "DONT_NOTIFY"},
                "agent_channel": {"delivery_allowed": work and not replan},
            },
            "operator_question": "Approve the pending change?" if notify else None,
            # Historical success cannot override the present work/gate/replan.
            "run_history": {"latest_runs": [{"delivery_outcome": "outcome_progress"}]},
        },
        "expected": {"action": action, "notify": notify, "finish_goal": False},
    } for name, work, notify, replan, action in rows]
    probes.append({
        "id": "wait_fallback_work",
        "packet": compact_quota_should_run_cli_payload(external_wait_fallback_scenario_source()),
        "expected": {"action": "work", "notify": True, "finish_goal": False},
    })
    return probes


def probe_messages(mode: str, packet: dict) -> list[dict]:
    if mode not in {"thin", "brief"}:
        raise ValueError("unsupported host prompt mode")
    prompt = build_heartbeat_prompt(
        goal_id="host-prompt-fixture", active_state=Path("ACTIVE_GOAL_STATE.md"),
        agent_id="worker-a", registered_agents=["worker-a"],
        runtime_profile="codex_app_heartbeat", **{mode: True},
    )
    if not prompt["ok"] or not prompt["interface_budget"]["within_budget"]:
        raise ValueError("production prompt exceeds its declared budget")
    return [
        {"role": "system", "content": (
            "This is a decision-only host simulation; no tools or side effects are available. "
            "The current quota result has already been read. Decide the next action using "
            "the supplied host instructions and result. Return only JSON with exactly "
            "action (work, wait, replan, external_wait or stop), notify (boolean), "
            "finish_goal (boolean). "
            "work means advancing ordinary delivery; replan means revising the frontier; "
            "external_wait means authoring the typed wait transition and continuing its "
            "independent successor; "
            "wait means no delivery now; stop means terminate the Goal."
        )},
        {"role": "user", "content": prompt["task_body"]},
        {"role": "user", "content": "Current quota result:\n" + json.dumps(packet)},
    ]


def run_probe(client: DoubaoExecToolClient, *, repeats: int = 2) -> dict:
    if not 1 <= repeats <= 5:
        raise ValueError("repeats must be between 1 and 5")
    results = []
    for mode in ("thin", "brief"):
        for case in cases():
            messages = probe_messages(mode, case["packet"])
            for attempt in range(repeats):
                # Each repetition is independent; failed attempts are not
                # repaired by feeding an answer or retried until one passes.
                response = client.next_final_content(messages)
                try:
                    decision = json.loads(response or "")
                except (ValueError, TypeError):
                    decision = None
                valid = (isinstance(decision, dict)
                    and set(decision) == {"action", "notify", "finish_goal"}
                    and type(decision.get("notify")) is bool
                    and type(decision.get("finish_goal")) is bool)
                results.append({"mode": mode, "case": case["id"], "attempt": attempt + 1,
                    "passed": bool(valid and decision == case["expected"]),
                    "input_sha256": sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()})
    return {"schema_version": "host_prompt_decision_probe_v0",
        "qualification_passed": all(row["passed"] for row in results),
        "actor_ref": client.actor_ref, "provider_call_count": len(results),
        "scope": "synthetic_prompt_decisions_only",
        "host_execution_qualified": False, "raw_responses_retained": False,
        "results": results}
