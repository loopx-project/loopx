"""Publish native results without changing grading, ranking or archives."""

import json


def native_grading_complete(result):
    """Require explicit native completion; retain sources predating this field."""
    return "grading_status" not in result or result["grading_status"] == "complete"


def write_native_result(trial, result, *, status, agent, task, run_id, model, effort,
                        feedback="native", native_goal_outcome=None):
    """Retain partial native scores without publishing a completion marker."""
    if status != "terminal" and not (native_goal_outcome and result.runtime_seconds > 0):
        return
    final = dict(agent=agent, task=task, run_id=run_id, model=model, effort=effort,
                 **result.to_dict())
    complete = status == "terminal" and native_grading_complete(final)
    if not complete or native_goal_outcome is not None:
        final["runtime_status"] = status
    if native_goal_outcome is not None:
        final["native_goal_outcome"] = native_goal_outcome
    if feedback == "best-only":
        final["evaluation_coverage"] = "online_only"
        final["offline_scoring_complete"] = False
    # SForge consumers treat final_result.json itself as a completed run.
    destination = trial / ("final_result.json" if complete else "partial_result.json")
    pending = destination.with_suffix(".json.tmp")
    pending.write_text(json.dumps(final, indent=2, ensure_ascii=False))
    pending.replace(destination)
