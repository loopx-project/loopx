"""Version-bound requester decisions through real Turn acceptance and durable IO."""
import json
import subprocess
import sys

import pytest

from test_local_delegation import service as delegation_service

service = delegation_service


def test_shadow_basis_uses_current_canonical_criteria_and_withdraws_after_task_edit(service):
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/loopx-jev/src"))
    from loopx_jev.runner import read_basis
    root, runner = service
    manifest = root / "basis.json"
    manifest.write_text(json.dumps({"goal_id": runner.goal_id, "objective": "Review declared task change",
        "acceptance": ["Operator claim must not replace the canonical criterion"], "evidence": [],
        "acceptance_scope": {"registry_ref": "registry.json", "runtime_ref": "runtime", "agent_id": "analyst",
            "todo_id": "todo_analyst-initial", "criterion_ids": ["analyst-initial"]}}))
    basis, current = read_basis(manifest, root)
    assert basis["criterion_binding"]["origin"] == "goal_acceptance"
    assert basis["criterion_binding"]["criterion_ids"] == ["analyst-initial"]
    assert basis["acceptance"] == ["Independent checks for analyst initial"]
    assert current()
    from tests.capabilities.test_progress_review import receipt
    from loopx.capabilities.progress_review.receipt import write_progress_review_receipt
    from loopx.capabilities.progress_review.context import external_progress_review_context
    goal = {"id": runner.goal_id, "control_plane": {"progress_review": {"mode": "shadow"}}}
    canonical_receipt = receipt(goal_id=runner.goal_id,
        evidence_scope={"criterion_binding": basis["criterion_binding"], "coverage": "declared_file_net_change", "files": ["output.json"]})
    canonical_receipt["run"].update(agent_id="analyst", todo_id="todo_analyst-initial")
    for mismatch in ({"todo_id": "todo_reviewer-corrected"}, {"agent_id": "reviewer"}):
        with pytest.raises(ValueError, match="evidence scope"):
            write_progress_review_receipt(runner.root, runner.goal_id,
                {**canonical_receipt, "run": {**canonical_receipt["run"], **mismatch}})
    persisted = write_progress_review_receipt(runner.root, runner.goal_id, canonical_receipt)
    persisted.write_text(json.dumps({**canonical_receipt, "run": {**canonical_receipt["run"], "todo_id": "todo_reviewer-corrected"}}))
    rejected_context = external_progress_review_context(goal, runner.root)
    assert rejected_context["receipts"] == []
    write_progress_review_receipt(runner.root, runner.goal_id, canonical_receipt)
    assert external_progress_review_context(goal, runner.root)["summary"]["latest"]["criterion_current"] is True
    # Exercise the real consumer too: canonical revisions include the owner
    # binding, so comparing only the unchanged manifest digest would reject all jobs.
    from loopx_jev import drift
    from loopx_jev.store import atomic_json
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/loopx-jev/tests"))
    from drift_fixtures import response
    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    probe = root / "probe.txt"
    probe.write_text("before\n")
    git("add", "probe.txt")
    git("commit", "-qm", "fixture baseline")
    config = root / "shadow-config.json"
    atomic_json(config, {"schema_version": "loopx_jev_drift_config_v0", "mode": "shadow",
        "scenarios": ["progress_review"], "model": "fixture-v1", "allow_egress": True})
    observer = root / "observer"
    drift.initialize(observer, root, manifest, config, ["probe.txt"], runtime_root=runner.root)
    def queue(sequence):
        probe.write_text(f"changed {sequence}\n")
        record = root / f"run-{sequence}.json"
        atomic_json(record, {"goal_id": runner.goal_id, "generated_at": f"2026-01-01T00:00:0{sequence}Z",
            "agent_id": "analyst", "todo_id": "todo_analyst-initial", "turn_instance_id": f"shadow-{sequence}"})
        prepared = drift.prepare(observer, config)
        original = json.loads(record.read_text())
        for mismatch in ({"todo_id": "todo_reviewer-corrected"}, {"agent_id": "reviewer"}):
            atomic_json(record, {**original, **mismatch})
            with pytest.raises(ValueError, match="run_acceptance_scope_mismatch"):
                drift.enqueue(observer, prepared, record)
        atomic_json(record, original)
        return drift.enqueue(observer, prepared, record)["event_id"]
    calls = []
    def send(request, *_):
        calls.append(request)
        assert "acceptance_scope" not in request["state"]["goal_basis"]
        assert request["state"]["goal_basis"]["acceptance"] == ["Independent checks for analyst initial"]
        return {"response": response(request, ["off_goal", "new_evidence"])}
    event = queue(1)
    drift.drain(observer, config, transport=send, credential=lambda: "fixture")
    evaluated = next(row for row in drift.status(observer)["events"] if row["event_id"] == event)
    assert evaluated["status"] == "completed", evaluated
    assert len(calls) == 1
    queue(2)
    def edit_during_request(request, *args):
        result = runner._cli(runner.binding("analysis"), "todo", "update", "--goal-id", runner.goal_id,
            "--agent-id", "analyst", "--todo-id", "todo_analyst-initial", "--text", "Changed declared task scope",
            "--update-operation-id", "shadow-scope-edit")
        assert result["ok"] is True, result
        return send(request, *args)
    drift.drain(observer, config, transport=edit_during_request, credential=lambda: "fixture")
    assert len(calls) == 2
    assert all(row["status"] == "stale" for row in drift.status(observer)["events"] if row["event_id"] != event)
    assert not current(), "A stale owner binding cannot be promoted by an unchanged study manifest"
    latest = external_progress_review_context(goal, runner.root)["summary"]["latest"]
    assert latest["status"] == "stale" and latest["criterion_current"] is False
    assert latest["drift_signal"] == {"noul": None, "choice": None}


