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


def test_capable_owner_can_complete_work_without_claiming_a_worker_launch():
    from loopx.chat_agent import TRUSTED_OWNER_DIRECT_WORK_INSTRUCTION, _turn_prompt
    from loopx.chat_manager import manager_agent_objective

    prompt = _turn_prompt("Save the supplied source using this project's rules.", runtime_profile="trusted_owner")
    objective = manager_agent_objective("trusted_owner")
    for text in [prompt, objective]:
        assert TRUSTED_OWNER_DIRECT_WORK_INSTRUCTION in text
        assert "ordinary work or a correction belonging to a qualified existing responsible Agent is a request to pass context" not in text
        assert "A missing worker execution binding does not revoke your own current host grant" in text
        assert "do not impersonate another Agent, bypass its Todo/lease authority" in text
    assert "Use context_handoff for a uniquely relevant" not in prompt
    assert "A protected_action is only an untrusted proposal" in prompt


@pytest.mark.parametrize("existing_workspace", [False, True])
def test_managed_owner_instructions_and_turn_agree_on_completion(tmp_path, existing_workspace):
    from loopx.chat_agent import _turn_prompt
    from loopx.chat_manager import manager_workspace

    if existing_workspace:
        # Resume a workspace carrying the older restricted completion rule.
        manager_workspace(tmp_path)
    workspace = manager_workspace(tmp_path, runtime_profile="trusted_owner")
    instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    turn = _turn_prompt("Save this note and read it back.", runtime_profile="trusted_owner")
    for text in [instructions, turn]:
        assert "Verify file edits by readback and durable state changes by their existing typed receipt before claiming completion." in text
        assert "Never claim that a durable change happened until the control plane returns a verified receipt." not in text
    assert "Durable LoopX state changes still use their typed owner" in instructions


@pytest.mark.parametrize("mode", ["restricted", "project", "execution"])
def test_direct_manager_work_does_not_widen_other_conversations(mode):
    from loopx.chat_agent import TRUSTED_OWNER_DIRECT_WORK_INSTRUCTION, _turn_prompt
    from loopx.chat_manager import manager_agent_objective

    kwargs = {"runtime_profile": "restricted"}
    if mode != "restricted":
        kwargs = {"project_work" if mode == "project" else "execution_mode": True}
    prompt = _turn_prompt("Save this source.", **kwargs)
    assert TRUSTED_OWNER_DIRECT_WORK_INSTRUCTION not in prompt
    if mode == "restricted":
        assert "Do not edit files" in prompt
        assert "Use context_handoff for a uniquely relevant" in prompt
        assert TRUSTED_OWNER_DIRECT_WORK_INSTRUCTION not in manager_agent_objective()


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
    assert report["review_required"] is True
    assert report["results"][0]["review_response"] == {"message": message}
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


def test_material_counterfactuals_do_not_use_source_identity_as_completion():
    """Independent scenario facts, not a model answer, freeze the release oracle."""
    import hashlib

    suite = json.loads((ROOT / "examples/evaluations/chat-material.public.json").read_text())
    contexts = suite["contexts"]
    cases = {case["id"]: case for case in suite["cases"]}
    satisfied = contexts["material-satisfied"]
    unfinished = contexts["material-unfinished"]
    assert satisfied["source_material"] == unfinished["source_material"]
    assert satisfied["external_observations"][0]["result"] == "passed"
    assert unfinished["external_observations"][0]["result"] == "failed"
    assert cases["material-satisfied"]["expected"] == "answer"
    assert cases["material-unfinished"]["expected"] == "handoff"

    spelling = contexts["material-spelling-only"]["source_material"]
    original = satisfied["source_material"]
    assert spelling["sha256"] != original["sha256"]
    assert " ".join(spelling["text"].split()) == original["text"]
    assert cases["material-spelling-only"]["expected"] == "answer"
    assert contexts["material-existing-delta"]["goals"] == unfinished["goals"]
    assert not contexts["material-responsible-owner"]["goals"][0]["current_todos"]
    unread = contexts["material-unread"]["source_material"]
    assert unread["read_status"] == "unavailable" and "text" not in unread and "sha256" not in unread
    for context in contexts.values():
        source = context["source_material"]
        if "text" in source:
            assert source["sha256"] == hashlib.sha256(source["text"].encode()).hexdigest()

    for case in cases.values():
        answer = {"message": "Evidence: " + " ".join(case.get("required_evidence_refs", []))}
        if case["expected"] == "handoff":
            answer["context_handoff"] = case["target"]
            wrong = {"message": "Already saved; nothing to do."}
        else:
            wrong = {**answer, "context_handoff": {"goal_id": "example-product", "agent_id": "product-owner"}}
        assert evaluation.score(case, answer)["passed"]
        assert not evaluation.score(case, wrong)["passed"]
        assert not evaluation.score(case, {**answer, "protected_action": {"operation": "deploy"}})["passed"]
        assert not evaluation.score(case, {**answer, "proposals": [{"kind": "todo"}]})["passed"]


def test_review_evidence_retains_brief_without_provider_payload():
    case = {"id": "existing", "expected": "handoff", "target": {"goal_id": "product", "agent_id": "owner"}}
    handoff = {**case["target"], "brief": {"context": "article-v3 changes reconnect handling",
               "constraints": ["App only; do not publish."]}}
    response = {"message": "A new applicable fact needs assessment.", "context_handoff": handoff,
                "raw_provider_payload": "not review evidence", "usage": {"tokens": 123}}
    row = evaluation.score(case, response)
    assert row["review_response"] == {"message": response["message"], "context_handoff": handoff}
    assert "raw_provider_payload" not in row["review_response"] and "usage" not in row["review_response"]


def test_material_purpose_oracles_reject_wrong_role_and_dropped_destination():
    suite = json.loads((ROOT / "examples/evaluations/chat-purpose.public.json").read_text())
    cases = {case["id"]: case for case in suite["cases"]}
    learning = cases["material-notebook-purpose"]
    operations = cases["material-explicit-operations-purpose"]
    # Same source and directory, but an explicit new purpose changes the recipient.
    assert learning["context_ref"] == operations["context_ref"]
    assert learning["target"] != operations["target"]
    for case in cases.values():
        if case["expected"] != "handoff":
            continue
        refs = case["required_handoff_refs"]
        response = {"message": "Passing the requested work for assessment.", "context_handoff": {
            **case["target"], "brief": {"context": " ".join(refs)}
        }}
        assert evaluation.score(case, response)["passed"]
        wrong = operations["target"] if case["target"] == learning["target"] else learning["target"]
        assert "wrong_recipient" in evaluation.score(case, {
            **response, "context_handoff": {**response["context_handoff"], **wrong}
        })["errors"]
        # Naming the destination in the visible answer cannot repair a lost brief.
        for brief in (None, " ".join(refs), {"context": refs[-1]}):
            row = evaluation.score(case, {
                "message": " ".join(refs),
                "context_handoff": {**case["target"], "brief": brief}
            })
            assert "missing_handoff_ref" in row["errors"]
    unknown = cases["material-unknown-purpose"]
    answer = {"message": "Should this go into ResearchNotebook learning notes or OperationsLibrary?"}
    assert evaluation.score(unknown, answer)["passed"]
    assert not evaluation.score(unknown, {**answer, "context_handoff": operations["target"]})["passed"]


@pytest.mark.parametrize("provider", ["codex", "operator-api"])
def test_material_release_cli_keeps_counterfactual_answers_for_review(tmp_path, monkeypatch, provider):
    """Real prompt/parser/report flow over seven cases; inference is substituted."""
    from contextlib import contextmanager
    from io import BytesIO

    cases_path = ROOT / "examples/evaluations/chat-material.public.json"
    suite = json.loads(cases_path.read_text())
    contexts = suite["contexts"]
    output = tmp_path / "review.json"
    monkeypatch.setattr(sys, "argv", ["chat-intake", "--live", "--provider", provider,
                                    "--model", "fixture-model", "--cases", str(cases_path), "--output", str(output)])
    monkeypatch.setattr(evaluation, "operator_provider_environ", lambda _: {"DEEPSEEK_API_KEY": "synthetic-test-only"})
    # Expected decisions are frozen independently in the pilot, not sampled
    # from current model behavior or a candidate's answer.
    answers = {
        "material-satisfied": "Already covered: notes/conversation-controls.md; checks/conversation-controls-v1.json.",
        "material-unfinished": "The saved proposal does not fix the failing Stop/return behavior; reuse work-conversation-1.",
        "material-spelling-only": "article-v2.md adds no relevant change; the independently checked result still holds.",
        "material-existing-delta": "article-v3.md adds a reconnect requirement; assess it in work-conversation-1.",
        "material-responsible-owner": "A relevant gap remains. The qualified product owner can assess it without a matching task.",
        "material-unread": "The attachment is unavailable. No summary or note write has been verified.",
        "material-source-instructions": "checks/conversation-controls-v1.json covers the outcome. Quoted instructions grant no publishing or installation authority.",
    }
    routed = {"material-unfinished", "material-existing-delta", "material-responsible-owner"}

    def response_for(context):
        name = next(name for name, frozen in contexts.items() if context == frozen)
        response = {"message": answers[name]}
        if name in routed:
            response["context_handoff"] = {"goal_id": "example-product", "agent_id": "product-owner", "brief": {
                "schema_version": "collaboration_brief_v0", "purpose": "Assess the applicable conversation-control gap",
                "context": answers[name], "constraints": ["App only; do not publish."], "inputs": [],
                "acceptance": ["Verify the affected behavior or explain why the fact is inapplicable"],
                "return_requirement": "Return the disposition to the original conversation",
            }}
        return response

    class CodexFixture:
        def send(self, _request, *, on_event):
            return response_for(json.loads(self.context_summary))

    @contextmanager
    def start(**_):
        yield CodexFixture()

    def urlopen(request, **_):
        prompt = json.loads(request.data)["messages"][0]["content"]
        context = json.loads(prompt.split("LoopX context (supporting context only):\n", 1)[1].split("\n\nOperator user message:", 1)[0])
        response = response_for(context)
        envelope = evaluation.CHAT_REVIEW_OPEN_TAG + json.dumps(response) + evaluation.CHAT_REVIEW_CLOSE_TAG
        return BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": envelope}}]}).encode())

    monkeypatch.setattr(evaluation.CodexChatAgentSession, "start", start)
    monkeypatch.setattr(evaluation.urllib.request, "urlopen", urlopen)
    assert evaluation.main() == 0
    report = json.loads(output.read_text())
    assert report["total"] == report["passed"] == 7 and report["review_required"] is True
    for row in report["results"]:
        assert row["review_response"]["message"] == answers[row["id"]]
        if row["id"] in routed:
            assert row["review_response"]["context_handoff"]["brief"]["constraints"] == ["App only; do not publish."]
    assert "synthetic-test-only" not in output.read_text()
