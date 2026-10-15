"""Agent preparation -> one local-owner check; real File/SQLite authority."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from loopx.chat_actions import ChatActionService
from loopx.chat_action_store import ActionConflictError, ChatActionStore
from loopx.control_plane.goals import acceptance_actions
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal


def prepare(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    raw = json.loads(document.read_text())
    # The Agent prepares the public-safe runtime alias, never a machine path.
    raw["criteria"][0]["validation_argv"] = ["python3", "check.py"]
    (project / "check.py").write_text(
        "from pathlib import Path\n"
        "p=Path('runs.txt')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        "assert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    document.write_text(json.dumps(raw))
    (project / "artifact.txt").write_text("accepted")
    code, result = cli("prepare", "--agent-id", "agent-a", "--document", str(document),
                       "--operation-id", "prepare-check", "--execute")
    assert code == 0, result
    root = project.parent
    service = ChatActionService(store=ChatActionStore(root / "runtime/chat/actions"),
                                registry_path=root / "registry.json")
    return project, cli, service, result["proposal"]


def test_agent_preparation_does_not_approve_or_execute(acceptance_goal):
    project, cli, service, proposal = prepare(acceptance_goal)
    assert cli("inspect")[1]["goal_acceptance_contract"] == {"enabled": False}
    assert not (project / "runs.txt").exists()
    assert proposal["permission_classification"] == "local_command_execution"
    result = service.apply(proposal["proposal_id"])
    assert result["proposal"]["status"] == "applied"
    assert result["proposal"]["receipt"]["checks_passed"] is True
    assert (project / "runs.txt").read_text() == "x"
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "accepted"
    assert service.apply(proposal["proposal_id"]) == result
    assert (project / "runs.txt").read_text() == "x"


def test_concurrent_apply_consumes_one_prepared_intent(acceptance_goal):
    project, _, service, proposal = prepare(acceptance_goal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.apply(proposal["proposal_id"]), range(2)))
    assert all(r["proposal"]["status"] == "applied" for r in results)
    assert (project / "runs.txt").read_text() == "x"


@pytest.mark.parametrize("lost_step", ["acceptance_configured", "native_check_response"])
def test_lost_response_recovers_native_receipt_without_rerunning(
    acceptance_goal, monkeypatch, lost_step,
):
    project, _, service, proposal = prepare(acceptance_goal)
    original_save = service.store.save_checkpoint
    original_verify = acceptance_actions.verify_goal_acceptance
    if lost_step == "acceptance_configured":
        def lose_configuration(*args, **kwargs):
            if kwargs["step"] == lost_step:
                raise RuntimeError("lost configuration response")
            return original_save(*args, **kwargs)
        monkeypatch.setattr(service.store, "save_checkpoint", lose_configuration)
    else:
        def lose_native_receipt(**kwargs):
            original_verify(**kwargs)
            raise RuntimeError("lost check response")
        monkeypatch.setattr(acceptance_actions, "verify_goal_acceptance", lose_native_receipt)
    with pytest.raises(RuntimeError, match="lost"):
        service.apply(proposal["proposal_id"])
    monkeypatch.setattr(service.store, "save_checkpoint", original_save)
    monkeypatch.setattr(acceptance_actions, "verify_goal_acceptance", original_verify)
    result = service.apply(proposal["proposal_id"])
    assert result["proposal"]["status"] == "applied"
    assert (project / "runs.txt").read_text() == "x"


def test_missing_native_receipt_does_not_authorize_automatic_retry(acceptance_goal, monkeypatch):
    project, cli, service, proposal = prepare(acceptance_goal)
    def interrupted(**_kwargs):
        raise RuntimeError("host interrupted after execution fence")
    monkeypatch.setattr(acceptance_actions, "verify_goal_acceptance", interrupted)
    with pytest.raises(RuntimeError):
        service.apply(proposal["proposal_id"])
    with pytest.raises(ActionConflictError, match="unconfirmed"):
        service.apply(proposal["proposal_id"])
    assert not (project / "runs.txt").exists()
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "unverified"


def test_stale_prepared_plan_cannot_replace_new_owner_configuration(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    _, _, service, proposal = prepare(acceptance_goal)
    before = cli("inspect")[1]
    code, configured = cli("configure", "--document", str(document),
        "--expected-provider-revision", before["provider_revision"],
        "--operation-id", "another-owner-change", "--execute")
    assert code == 0, configured
    result = service.apply(proposal["proposal_id"])
    assert result["proposal"]["status"] == "stale"
    assert not (project / "runs.txt").exists()


def test_prepare_dry_run_and_unknown_agent_do_not_write(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    before = cli("inspect")[1]
    code, preview = cli("prepare", "--agent-id", "agent-a", "--document", str(document),
                        "--operation-id", "read-only-preparation")
    assert code == 0 and preview["executed"] is False and preview["prepared"] is False
    assert cli("inspect")[1] == before
    assert not (project.parent / "runtime/chat/actions").exists()
    code, rejected = cli("prepare", "--agent-id", "unknown", "--document", str(document),
                         "--operation-id", "unknown-preparation", "--execute")
    assert code != 0 and rejected["ok"] is False
    assert cli("inspect")[1] == before


def test_check_failure_is_recorded_without_goal_completion(acceptance_goal):
    project, cli, service, proposal = prepare(acceptance_goal)
    (project / "artifact.txt").write_text("not accepted")
    applied = service.apply(proposal["proposal_id"])["proposal"]
    assert applied["status"] == "applied"
    assert applied["receipt"]["checks_passed"] is False
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "failed"
    assert (project / "runs.txt").read_text() == "x"


def test_prepared_plan_cannot_claim_another_goal_context(acceptance_goal):
    project, cli, service, proposal = prepare(acceptance_goal)
    with pytest.raises(ValueError, match="context must match"):
        service.preview({"action_kind": "goal.acceptance.check", "summary": "Wrong context",
            "idempotency_key": "wrong-context", "context": {"kind": "goal", "goal_id": "other"},
            "normalized_parameters": proposal["normalized_parameters"]})
    assert cli("inspect")[1]["goal_acceptance_contract"]["enabled"] is False
    assert not (project / "runs.txt").exists()
