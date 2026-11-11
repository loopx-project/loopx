"""Execute the documented owner reconciliation against disposable canonical stores."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_documented_owner_scope_reconciliation(tmp_path, monkeypatch, provider):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "parsed.txt").write_text("alpha\nbeta\n")
    state = project / "state.md"
    state.write_text("---\nstatus: active\n---\n# Fixture\n## Agent Todo\n")
    runtime, registry = tmp_path / "runtime", tmp_path / "registry.json"
    write_fixture_registry(project=project, runtime_root=runtime, registry_path=registry,
        goal_id="example-goal", domain="acceptance", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["example-agent"])
    todos = [{"schema_version": "todo_item_v0", "todo_id": todo_id, "role": "agent",
        "text": text, "status": "done" if index == 1 else "open", "done": index == 1,
        "task_class": "advancement_task", "action_kind": "implement", "index": index,
        "source_section": "Agent Todo", "archive_state": "active", "claimed_by": "example-agent"}
        for index, (todo_id, text) in enumerate([
            ("todo_parse", "Parse the records"),
            ("todo_transform_old", "Transform using the obsolete ordering"),
            ("todo_transform_current", "Transform preserving the requested order"),
            ("todo_final", "Check the final delivery"),
        ], 1)]
    initialize_canonical_authority(runtime, "example-goal",
        build_todo_runtime_shadow_projection(goal_id="example-goal", todos=todos, handoff_mode="hard_lease"),
        state_path=state, provider=provider)

    def run(*args, expected=0):
        code, result = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
        assert code == expected, result
        return result

    def acceptance(action, *args, expected=0):
        return run("goal-acceptance", action, "--goal-id", "example-goal", *args, expected=expected)

    refinement = project / "outcome.json"
    refinement.write_text(json.dumps({"outcome_id": "records", "owner_agent_id": "example-agent",
        "objective": "Deliver validated records", "todo_ids": ["todo_parse"],
        "requirements": [{"id": "parse", "description": "The accepted parsed records remain intact."}],
        "source": {"ref": "message:initial", "summary": "The owner requested validated records"}}))
    acceptance("refine", "--agent-id", "example-agent", "--document", str(refinement),
        "--expected-provider-revision", acceptance("inspect")["provider_revision"],
        "--operation-id", "declare-records", "--execute")
    retained = acceptance("inspect")["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]

    docs = Path(__file__).resolve().parents[2] / "docs/reference/goal-acceptance-observations.md"
    section = docs.read_text(encoding="utf-8").split('<a id="owner-check-scope-reconciliation"></a>', 1)[1]
    document = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
    # Use this test process's interpreter; all validator code is the documented code.
    for criterion in document["criteria"]:
        criterion["validation_argv"][0] = sys.executable
    path = project / "acceptance-current.json"

    def configure(value, operation):
        path.write_text(json.dumps(value))
        return ("--document", str(path), "--expected-provider-revision",
            acceptance("inspect")["provider_revision"], "--operation-id", operation)

    original = deepcopy(document)
    original["scope"] = {"kind": "all_advancement"}
    original["bindings"][1]["todo_id"] = "todo_transform_old"
    original["criteria"][1].update(id="transform-old", description="The obsolete transform reverses the records.",
        validation_argv=[sys.executable, "-c",
            "from pathlib import Path; assert Path('transformed.txt').read_text() == 'BETA\\nALPHA\\n'"])
    original["bindings"][1]["criterion_ids"] = ["transform-old"]
    acceptance("configure", *configure(original, "configure-original"), "--execute")
    lease = run("task-lease", "acquire", "--goal-id", "example-goal", "--todo-id", "todo_transform_old",
        "--owner", "example-agent", "--idempotency-key", "retire-transform-v1")["lease"]
    supersede = ("todo", "supersede", "--goal-id", "example-goal", "--todo-id", "todo_transform_old",
        "--agent-id", "example-agent", "--reason", "message:owner-correction: use the corrected transform",
        "--successor-todo-id", "todo_transform_current", "--task-lease-idempotency-key", lease["idempotency_key"],
        "--task-lease-expected-version", str(lease["version"]))
    assert run(*supersede, expected=1)["reason_code"] == "goal_acceptance_validation_required"

    rebind_only = {**document, "scope": {"kind": "all_advancement"}}
    acceptance("configure", *configure(rebind_only, "rebind-only"), "--execute")
    assert run(*supersede, expected=1)["reason_code"] == "goal_acceptance_unbound"

    args = configure(document, "reconcile-transform-v2")
    assert acceptance("configure", *args)["status"] == "planned"
    applied = acceptance("configure", *args, "--execute")
    assert applied["status"] == "applied" and applied["operation_id"] == "reconcile-transform-v2"
    assert applied["source_authority"] == f"{provider}_v0"
    replay = acceptance("configure", *args, "--execute")
    assert replay["status"] == "replayed" and replay["provider_revision"] == applied["provider_revision"]
    assert replay["operation_id"] == applied["operation_id"]
    contract = applied["goal_acceptance_contract"]
    assert contract["scope"]["kind"] == "selected_work"
    assert set(contract["scope"]["todo_ids"]) == set(document["scope"]["todo_ids"])
    assert {item["todo_id"]: item["criterion_ids"] for item in contract["tasks"]} == {
        binding["todo_id"]: binding["criterion_ids"] for binding in document["bindings"]}
    assert all(item["state"] == "ready" for item in contract["tasks"])
    assert {item["id"] for item in contract["criteria"]} == {"parse", "transform-current", "final"}
    assert contract["outcome_requirements"]["outcomes"] == retained

    retired = run(*supersede)
    assert retired["superseded"] is True and retired["source_authority"] == f"{provider}_v0"
    readback = run("todo", "list", "--goal-id", "example-goal")
    records = {todo["todo_id"]: todo for todo in readback["todos"]}
    assert records["todo_transform_old"]["superseded_by"] == "todo_transform_current"
    assert records["todo_parse"]["done"] is True
    assert records["todo_transform_current"]["done"] is False
    assert records["todo_final"]["done"] is False
    assert acceptance("inspect")["goal_acceptance_contract"]["outcome_requirements"]["outcomes"] == retained
    assert acceptance("verify", "--agent-id", "example-agent", "--execute", expected=1)["checks_passed"] is False
    for filename, content in [("parsed.txt", "alpha\nbeta\n"),
                              ("transformed.txt", "ALPHA\nBETA\n"), ("delivery.txt", "ALPHA\nBETA\n")]:
        (project / filename).write_text(content)
    verified = acceptance("verify", "--agent-id", "example-agent", "--execute")
    assert verified["checks_passed"] is True
    assert acceptance("inspect")["goal_acceptance_contract"]["status"] == "accepted"
