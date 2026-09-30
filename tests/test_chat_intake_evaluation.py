"""Offline oracles for the opt-in model evaluation; no credentials or paid calls."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chat_intake_eval", ROOT / "examples/evaluations/chat-intake.py")
assert spec and spec.loader
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_existing_work_requires_the_right_recipient_without_a_second_gate():
    case = {"id": "existing", "expected": "handoff", "target": {"goal_id": "research", "agent_id": "owner"}}
    response = {"message": "Continue", "context_handoff": case["target"]}
    assert evaluation.score(case, response)["passed"]
    for wrong in [
        {"goal_draft": {"objective": "Duplicate"}},
        {"context_handoff": {"goal_id": "unrelated", "agent_id": "owner"}},
        {**response, "gate": {"kind": "confirmation"}},
        {**response, "proposals": [{"kind": "todo"}]},
    ]:
        assert not evaluation.score(case, wrong)["passed"]


def test_complete_new_request_does_not_need_another_question():
    case = {"id": "new", "expected": "draft", "ready": True}
    draft = {"completion_criteria": "Cited report", "question": ""}
    assert evaluation.score(case, {"goal_draft": draft})["passed"]
    assert not evaluation.score(case, {"goal_draft": {**draft, "question": "Ready to begin?"}})["passed"]


def test_ordinary_question_cannot_silently_become_work():
    case = {"id": "question", "expected": "answer"}
    assert evaluation.score(case, {"message": "A goal describes an outcome."})["passed"]
    assert not evaluation.score(case, {"goal_draft": {"objective": "Do work"}})["passed"]
    assert not evaluation.score(case, {"protected_action": {"operation": "merge"}})["passed"]


def test_satisfied_outcome_needs_evidence_and_no_competing_effect():
    case = {"id": "satisfied", "expected": "answer", "required_evidence_refs": ["report-v2"]}
    answer = {"message": "The requested report is ready: report-v2."}
    assert evaluation.score(case, answer)["passed"]
    for competing in [
        {"context_handoff": {"goal_id": "research", "agent_id": "owner"}},
        {"goal_draft": {"objective": "Duplicate report"}},
        {"protected_action": {"operation": "merge", "target": "42"}},
        {"proposals": [{"kind": "todo"}]},
    ]:
        assert not evaluation.score(case, {**answer, **competing})["passed"]
    assert not evaluation.score(case, {"message": "Done."})["passed"]


def test_shared_intake_guidance_precedes_handoff_without_widening_runtime():
    from loopx.chat_agent import CONVERSATION_INTENT_RESOLUTION_INSTRUCTION, _turn_prompt
    from loopx.chat_manager import manager_agent_objective

    prompt = _turn_prompt("Continue.")
    assert CONVERSATION_INTENT_RESOLUTION_INSTRUCTION in prompt
    assert prompt.index(CONVERSATION_INTENT_RESOLUTION_INSTRUCTION) < prompt.index("context_handoff=")
    assert "Do not edit files" in prompt
    for profile in ["restricted", "trusted_owner"]:
        assert CONVERSATION_INTENT_RESOLUTION_INSTRUCTION in manager_agent_objective(profile)
    assert CONVERSATION_INTENT_RESOLUTION_INSTRUCTION not in _turn_prompt("Execute.", execution_mode=True)
