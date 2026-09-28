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
