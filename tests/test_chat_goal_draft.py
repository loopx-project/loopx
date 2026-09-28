from copy import deepcopy

import pytest

from loopx.chat import normalize_agent_response
from loopx.chat_store import ChatSessionStore


DRAFT = {
    "objective": "Research public cash flows",
    "completion_criteria": "A report with cited sources",
    "execution_boundary": "Public sources only",
    "question": "Which period?",
    "options": ["Three years", "One year"],
}


def test_real_typed_normalizer_preserves_draft_and_redacts_transport_paths():
    draft = {**DRAFT, "execution_boundary": "Use /private/work/research"}
    response = normalize_agent_response(
        {"message": "Please confirm the period.", "goal_draft": draft},
        protected_paths=["/private/work/research"],
    )
    assert response["goal_draft"]["objective"] == DRAFT["objective"]
    assert "/private/work/research" not in response["goal_draft"]["execution_boundary"]
    assert response["proposals"] == []
    assert response["protected_action"] is None
    assert draft["execution_boundary"] == "Use /private/work/research"


def test_ordinary_answers_and_malformed_drafts_keep_existing_contract():
    ordinary = normalize_agent_response({"message": "Explain goals"})
    assert "goal_draft" not in ordinary
    for draft in [[], {**DRAFT, "permission": "workspace_write"}, {**DRAFT, "objective": ""}]:
        assert normalize_agent_response({"message": "Explain goals", "goal_draft": draft}) == ordinary


@pytest.mark.parametrize("attached", [False, True])
def test_draft_survives_completion_restart_and_replay(tmp_path, attached):
    store = ChatSessionStore(tmp_path)
    session = store.create_session(goal_id="research", agent_id="codex", adapter_kind="codex_app_server", upstream_thread_id="thread", session_mode="attached_host" if attached else "managed_runtime", host_surface="codex_app" if attached else None)
    sid = session["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="draft-turn", message="Help shape a goal")
    tid = turn["turn_id"]
    response = normalize_agent_response({"message": "Choose a period", "goal_draft": deepcopy(DRAFT)})
    store.update_turn(sid, tid, status="starting")
    store.update_turn(sid, tid, status="completing", response=response, **({"completion_id": "host-completion"} if attached else {}))
    restored = ChatSessionStore(tmp_path)
    restored.finalize_turn_completion(sid, tid)
    restored.finalize_turn_completion(sid, tid)
    messages = [m for m in restored.messages(sid) if m["role"] == "agent"]
    assert len(messages) == 1
    assert messages[0]["goal_draft"] == DRAFT
    assert restored.load_turn(sid, tid)["status"] == "completed"
    assert restored.load_session(sid)["active_turn_id"] is None


@pytest.mark.parametrize("conflict", [
    {"protected_action": {"operation": "merge", "target": "#1"}},
    {"gate": {"kind": "binding_required", "summary": "Owner needs a binding"}},
    {"proposals": [{"kind": "todo", "text": "Continue existing work"}]},
])
def test_competing_intent_does_not_become_a_new_goal(conflict):
    response = normalize_agent_response({"message": "Keep the existing work", "goal_draft": DRAFT, **conflict})
    assert "goal_draft" not in response
    assert response["message"] == "Keep the existing work"


def test_invalid_handoff_remains_rejected_instead_of_creating_goal():
    with pytest.raises(ValueError):
        normalize_agent_response({"message": "Continue", "goal_draft": DRAFT, "context_handoff": {}})
