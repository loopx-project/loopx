"""Native quota carries optional navigation without replacing the acceptance body."""
from __future__ import annotations

import argparse
import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections
from loopx.control_plane.turn_driver.codex_context import codex_context_hook
from loopx.cli_commands.quota_capture import read_context_capture


@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("marked_goal", [True, False])
def test_quota_navigation_is_opt_in_and_original_capture_remains_complete(tmp_path, monkeypatch, marked_goal, provider, inherited):
    goal, agent = "scope-cli", "author"
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    todos = [{"schema_version": "todo_item_v0", "todo_id": f"todo_{owner}", "role": "agent",
        "text": f"Complete {owner} export. Preserve cancellation at the tail.", "status": "open", "done": False,
        "task_class": "advancement_task", "action_kind": "implement", "index": i + 1,
        "source_section": "Agent Todo", "archive_state": "active", "claimed_by": owner}
        for i, owner in enumerate((agent, "peer"))]
    body = "---\nstatus: active\n---\n# Goal\n## Objective\nDeliver compatible exports. Never publish.\n## Agent Todo\n\n## User Todo\n\n## Completed Work Archive\n"
    state.write_text(render_canonical_todo_sections(body, todos, provider_revision="fixture-source").markdown
        if marked_goal else body)
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry, goal_id=goal,
        domain="software", adapter_kind="generic_project_goal_v0", state_file=str(state),
        registered_agents=[agent, "peer"], quota_allowed_slots=None)
    initialize_canonical_authority(runtime, goal,
        build_todo_runtime_shadow_projection(goal_id=goal, todos=todos, handoff_mode="soft_claim"), state_path=state, provider=provider)

    def cli(*args):
        code, payload = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
        assert code == 0, json.dumps(payload)
        return payload

    for owner in (agent, "peer"):
        document = tmp_path / f"{owner}.json"
        document.write_text(json.dumps({"outcome_id": owner, "owner_agent_id": owner,
            "objective": f"Deliver the {owner} export.", "todo_ids": [f"todo_{owner}"],
            "requirements": [{"id": f"r{i}", "description":
                (f"{owner} exact requirement {i}. " + "Keep every compatibility and recovery condition. " * 7).strip()}
                for i in range(12)],
            "source": {"ref": f"message:{owner}", "summary": "Original independent export request."}}))
        before = cli("goal-acceptance", "inspect", "--goal-id", goal)
        cli("goal-acceptance", "refine", "--goal-id", goal, "--agent-id", owner, "--document", str(document),
            "--expected-provider-revision", before["provider_revision"], "--operation-id", f"outcome-{owner}", "--execute")
    if inherited:
        from tests.control_plane.test_context_machine_configuration import configure
        code, applied = configure(registry, runtime, tmp_path, "on")
        assert code == 0 and applied["readback_verified"], applied
    off = cli("quota", "should-run", "--goal-id", goal, "--agent-id", agent, "--context-delivery", "off")
    source = next(row["content"] for row in off["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    assert "outcome_context" not in source
    assert len(source["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]) == 2
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    (tmp_path / "captures").mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "scope-session")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    codex_context_hook(str(native), {"hook_event_name": "SessionStart", "session_id": "scope-session",
        "cwd": str(tmp_path), "source": "startup"})
    first = cli("quota", "should-run", "--goal-id", goal, "--agent-id", agent,
        *([] if inherited else ["--context-delivery", "on"]),
        "--runtime-profile", "generic_cli", "--codex-context-dir", str(native),
        "--decision-output-root", str(tmp_path / "captures"), "--turn-instance-id", "scope-turn")
    page = read_context_capture(argparse.Namespace(capture_file=first["recovery"]["path"],
        capture_sha256=first["recovery"]["sha256"], offset=0, page_bytes=64000, max_page_output_bytes=64000, format="json"))
    parts = []
    while True:
        assert page["ok"], page
        parts.append(page["content"])
        if page["eof"]:
            break
        page = read_context_capture(argparse.Namespace(capture_file=first["recovery"]["path"], capture_sha256=first["recovery"]["sha256"],
            offset=page["next_offset"], page_bytes=64000, max_page_output_bytes=64000, format="json"))
    presentation = json.loads("".join(parts))
    assert presentation.get("schema_version") != "outcome_context_projection_v0"
    if not marked_goal:
        goal_source = next(row["content"] for row in
            presentation["interaction_contract"]["agent_channel"]["work_context"]["sources"]
            if row.get("source") == "goal_state")
        assert goal_source["full_source"] is True and goal_source["text"] == body
    observed = next(row["content"] for row in presentation["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    assert observed["outcome_context"]["suggested_outcome_ids"] == [agent]
    assert observed["outcome_context"]["coverage_complete"] is True
    assert "required_outcome_ids" not in observed["outcome_context"]
    assert [row["id"] for row in observed["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]] == [agent, "peer"]
    assert observed["goal_acceptance_contract"] == source["goal_acceptance_contract"]
    # Use the shared exact selector, without a specialized outcome reader.
    source_index = next(i for i, row in enumerate(presentation["interaction_contract"]["agent_channel"]["work_context"]["sources"])
        if row.get("source") == "goal_acceptance")
    pointer = f"/interaction_contract/agent_channel/work_context/sources/{source_index}/content/goal_acceptance_contract/outcome_requirements/outcomes/1"
    detail = cli("quota", "context-read", "--capture-file", first["recovery"]["path"],
        "--capture-sha256", first["recovery"]["sha256"], "--select", pointer,
        "--page-bytes", "64000", "--max-page-output-bytes", "64000")
    assert detail["eof"] and "peer exact requirement 11" in detail["content"]

    # Exercise the real acceptance adapter against this same provider snapshot,
    # injecting only the host oracle observation after the canonical read.
    from loopx.control_plane.goals import acceptance as acceptance_io
    observe = acceptance_io.observe_goal_acceptance_oracles
    def changed(**kwargs):
        result = observe(**kwargs)
        result["outcome_requirements"]["outcomes"][1]["requirements"][0]["status"] = "stale"
        return result
    monkeypatch.setattr(acceptance_io, "observe_goal_acceptance_oracles", changed)
    fresh = acceptance_io.inspect_goal_acceptance(registry_path=registry, goal_id=goal,
        runtime_root=str(runtime), todo_id=f"todo_{agent}", context_agent_id=agent, include_work_details=False)
    assert "outcome_context" not in fresh
    assert fresh["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][1]["requirements"][0]["status"] == "stale"
