"""Offline oracles for the opt-in model evaluation; no credentials or paid calls."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

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
    assert evaluation.score(case, {"message": "Here is the draft.", "goal_draft": draft})["passed"]
    assert not evaluation.score(case, {"message": "Here is the draft.", "goal_draft": {**draft, "question": "Ready to begin?"}})["passed"]


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


@pytest.mark.parametrize("message", [None, "", " \n\t", 7, ["Answer"]])
def test_an_ordinary_answer_needs_visible_text(message):
    row = evaluation.score({"id": "ordinary", "expected": "answer"}, {"message": message})
    assert not row["passed"] and "missing_answer" in row["errors"]


@pytest.mark.parametrize("provider", ["codex", "operator-api"])
@pytest.mark.parametrize("message", ["A sourced explanation.", ""])
def test_release_cli_scores_provider_output_and_hashes_effective_prompt(
    tmp_path, monkeypatch, provider, message
):
    """Exercise the real CLI/report flow; only provider inference is simulated."""
    from contextlib import contextmanager
    import hashlib
    from io import BytesIO

    suite = {"contexts": {"ordinary": {"scope": "public", "goals": []}},
             "cases": [{"id": "ordinary", "context_ref": "ordinary", "expected": "answer",
                        "request": "Explain\n  what a Goal means."}]}
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(suite))
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["chat-intake", "--live", "--provider", provider,
                                      "--model", "fixture-model", "--cases", str(cases), "--output", str(output)])
    monkeypatch.setattr(evaluation, "operator_provider_environ", lambda _: {"DEEPSEEK_API_KEY": "synthetic-test-only"})
    observed = []
    response = {"message": message, "proposals": [], "protected_action": None, "gate": None}

    class CodexFixture:
        context_summary = "wrong startup context"

        def send(self, request, *, on_event):
            observed.append(evaluation._turn_prompt(request, context_summary=self.context_summary))
            on_event("answer.final", {"response": response})
            return response

    @contextmanager
    def start(**kwargs):
        assert kwargs["model"] == "fixture-model"
        yield CodexFixture()

    def urlopen(request, **_):
        observed.append(json.loads(request.data)["messages"][0]["content"])
        envelope = evaluation.CHAT_REVIEW_OPEN_TAG + json.dumps(response) + evaluation.CHAT_REVIEW_CLOSE_TAG
        return BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": envelope}}]}).encode())

    monkeypatch.setattr(evaluation.CodexChatAgentSession, "start", start)
    monkeypatch.setattr(evaluation.urllib.request, "urlopen", urlopen)
    assert evaluation.main() == (0 if message else 1)
    report = json.loads(output.read_text())
    assert report["total"] == len(observed) == 1
    expected = evaluation._turn_prompt("Explain what a Goal means.", context_summary=json.dumps(suite["contexts"]["ordinary"], ensure_ascii=False))
    assert observed == [expected], "Both providers must receive the same case context and request"
    assert report["results"][0]["prompt_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    assert report["passed"] == bool(message)
    assert "synthetic-test-only" not in output.read_text()


def test_codex_protocol_warning_fails_even_with_a_readable_fallback(tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager

    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"contexts": {"ordinary": {}}, "cases": [
        {"id": "ordinary", "context_ref": "ordinary", "expected": "answer", "request": "Explain Goals."}]}))
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["chat-intake", "--live", "--provider", "codex", "--model", "fixture-model",
                                      "--cases", str(cases), "--output", str(output)])
    monkeypatch.setattr(evaluation, "operator_provider_environ", lambda _: {})

    class WarningFixture:
        def send(self, request, *, on_event):
            on_event("protocol.warning", {"error_code": "missing_review_envelope", "detail": "do-not-persist-provider-payload"})
            return {"message": "A readable fallback that is not protocol-qualified."}

    @contextmanager
    def start(**_):
        yield WarningFixture()

    monkeypatch.setattr(evaluation.CodexChatAgentSession, "start", start)
    assert evaluation.main() == 1
    row = json.loads(output.read_text())["results"][0]
    assert row["errors"] == ["protocol_warning"] and not row["passed"]
    assert "do-not-persist-provider-payload" not in output.read_text() + capsys.readouterr().out


def test_release_cli_without_live_never_reads_credentials_or_calls_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["chat-intake", "--model", "fixture-model", "--output", str(tmp_path / "report.json")])

    def forbidden(*_, **__):
        pytest.fail("No provider or credential access without explicit live opt-in")

    monkeypatch.setattr(evaluation, "operator_provider_environ", forbidden)
    monkeypatch.setattr(evaluation.CodexChatAgentSession, "start", forbidden)
    monkeypatch.setattr(evaluation.urllib.request, "urlopen", forbidden)
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert not (tmp_path / "report.json").exists()
