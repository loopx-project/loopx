"""Open validation work reaches outcome-aware replan and actual local repair."""
import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.effect_runtime import restart_effect_runtime
from test_replan_progress_native_cli import (
    AGENT, GOAL, TODO, TURN, call_progress_cli, make_progress_cli_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("checks", [False, True], ids=["default-review", "configured-checks"])
def test_open_validation_replan_retains_requirements_and_executes_repair(tmp_path, monkeypatch, provider, checks):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    registry = json.loads(fixture["registry"].read_text())
    registry["goals"][0]["quota"]["allowed_slots"] = 4
    fixture["registry"].write_text(json.dumps(registry))

    def call(*args, expected_code=0):
        return call_progress_cli(fixture, *args, expected_code=expected_code)

    def inspect():
        return call("goal-acceptance", "inspect", "--goal-id", GOAL)

    def refine(patch, operation):
        document = tmp_path / "outcome.json"
        document.write_text(json.dumps(patch))
        return call("goal-acceptance", "refine", "--goal-id", GOAL, "--agent-id", AGENT,
            "--document", str(document), "--operation-id", operation,
            "--expected-provider-revision", inspect()["provider_revision"], "--execute")

    def guard(turn):
        return call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--todo-id", TODO, "--turn-instance-id", turn,
            )

    try:
        artifact = fixture["project"] / "artifact.txt"
        artifact.write_text("Introduction\nBody\n")
        requirements = [{"id": "opening", "description": "Retain the introduction"},
            {"id": "appendix", "description": "Include the supporting appendix"}]
        refine({"outcome_id": "artifact", "owner_agent_id": AGENT,
            "objective": "Deliver the complete artifact", "requirements": requirements, "todo_ids": [TODO],
            "source": {"ref": "request:artifact", "summary": "The artifact must retain both sections"},
            "review": [{"id": r["id"], "conclusion": "uncertain", "evidence_refs": ["artifact:initial"],
                "note": "The complete artifact has not been checked"} for r in requirements]}, "declare-artifact")
        checker = fixture["project"] / "check.py"
        checker.write_text("from pathlib import Path\ns = Path('artifact.txt').read_text()\n"
            "assert s.startswith('Introduction\\n')\nassert s.endswith('Appendix\\n')\n")
        if checks:
            document = tmp_path / "checks.json"
            document.write_text(json.dumps({"outcome_id": "artifact", "criteria": [
                {"id": r["id"], "validation_argv": [sys.executable, "check.py"]} for r in requirements]}))
            call("goal-acceptance", "configure", "--goal-id", GOAL, "--document", str(document),
                "--operation-id", "configure-checks", "--expected-provider-revision", inspect()["provider_revision"], "--execute")
            call("goal-acceptance", "verify", "--goal-id", GOAL, "--execute", expected_code=1)

        before = inspect()
        call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", TURN, )
        admitted = guard(TURN)
        obligation = admitted["autonomous_replan_obligation"]
        assert [t["kind"] for t in obligation["triggers"]] == ["typed_progress_repeat"]
        acceptance = obligation["replan_context"]["core_goal"]["acceptance_contract"]
        assert acceptance == before["goal_acceptance_contract"]
        outcomes = acceptance["outcome_requirements"]
        assert outcomes["acceptance_gaps"] == [], "open work is covered, but still must be reviewed"
        assert outcomes["outcomes"][0]["work"] == [{"todo_id": TODO, "status": "open"}]
        assert not all(r["planning_satisfied"] for r in outcomes["outcomes"][0]["requirements"])
        assert any("even with open Todos" in s for s in admitted["replan_action_packet"]["planning_guidance"])
        envelope = call("quota", "should-run", "--codex-app", "--turn-envelope", "--goal-id", GOAL,
            "--agent-id", AGENT, "--todo-id", TODO, "--turn-instance-id", TURN,
            )
        delivered = next(source for source in envelope["work_context"]["sources"] if source["source"] == "goal_acceptance")
        assert acceptance == delivered["content"]["goal_acceptance_contract"]
        assert inspect() == before, "review admission does not rewrite acceptance"

        # Script an actual correction in the same Todo against the delivered
        # requirements; model adoption is not tested by this control-path case.
        diagnostic = subprocess.run([sys.executable, str(checker)], cwd=fixture["project"], capture_output=True)
        assert diagnostic.returncode == 1
        assert requirements[1]["id"] == "appendix"
        artifact.write_text(artifact.read_text() + "Appendix\n")
        subprocess.run([sys.executable, str(checker)], cwd=fixture["project"], check=True)
        vision = tmp_path / "vision.json"
        vision.write_text(json.dumps({"state": "vision_patch_proposed",
            "vision_patch": {"acceptance_summary": "Preserve the introduction and include the appendix"},
            "path_delta": {"outcome": "replan", "prior_assumption": "Repeat local validation",
                "observed_reality": "The complete artifact was missing its required appendix",
                "retained": ["Original requirements and validation Todo"],
                "evidence_refs": ["probe:complete-artifact"]}}))
        refresh = fixture["refresh"][:-2] + ["--agent-vision-json", str(vision)]
        accepted = call(*refresh)
        assert accepted["autonomous_replan_ack"]["semantic_delta"]["accepted"]
        restart_effect_runtime(wait_for_exit=True)
        assert call(*refresh)["appended"] is False
        spent = call("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
            "--todo-id", TODO, "--turn-instance-id", TURN, "--slots", "1", "--source", "heartbeat", "--execute")
        assert spent["settlement_progress"]["state"] == "settled"
        following = guard("continue-original-validation")
        assert following["decision"] == "run", following
        assert following["selected_todo"]["todo_id"] == TODO
        assert following["selected_todo"]["status"] == "open"
        assert inspect()["goal_acceptance_contract"] == before["goal_acceptance_contract"], (
            "replan/repair success cannot certify unmet requirements or whole-Goal completion")
        if checks:
            call("goal-acceptance", "verify", "--goal-id", GOAL, "--execute")
            assert inspect()["goal_acceptance_contract"]["status"] == "accepted"
        else:
            refine({"outcome_id": "artifact", "source": {"ref": "artifact:corrected", "summary": "Verified both required sections"},
                "review": [{"id": r["id"], "conclusion": "met", "evidence_refs": ["probe:complete-artifact"],
                    "note": "The corrected artifact preserves both required sections"} for r in requirements]}, "review-artifact")
        final = inspect()["goal_acceptance_contract"]["outcome_requirements"]
        assert all(r["planning_satisfied"] for r in final["outcomes"][0]["requirements"])
        assert final["goal_completion_assessed"] is False
    finally:
        restart_effect_runtime(wait_for_exit=True)
