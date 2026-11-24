"""End-to-end File + SQLite verification for scoped outcomes & lean settlement plans."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.configure_goal import configure_goal
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import write_fixture_registry
from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections
from loopx.control_plane.turn_driver.codex_context import codex_context_hook
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference


def _seed_mature_goal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    outcome_count: int = 33,
) -> dict[str, object]:
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    registry = project / ".loopx" / "registry.json"
    state = project / "ACTIVE_GOAL_STATE.md"
    project.mkdir(parents=True)
    registry.parent.mkdir(parents=True)

    goal_id = "mature-scoped-goal"
    agent_id = "worker"
    todos = [
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_current",
            "role": "agent",
            "text": "Validate and settle the active scoped slice.",
            "status": "open",
            "done": False,
            "task_class": "advancement_task",
            "action_kind": "implement",
            "index": 1,
            "source_section": "Agent Todo",
            "archive_state": "active",
            "claimed_by": agent_id,
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "todo_next",
            "role": "agent",
            "text": "Follow-up pending open deliverable.",
            "status": "open",
            "done": False,
            "task_class": "advancement_task",
            "action_kind": "implement",
            "index": 2,
            "source_section": "Agent Todo",
            "archive_state": "active",
            "claimed_by": "peer-0",
        },
    ]
    for i in range(outcome_count - 1):
        todos.append({
            "schema_version": "todo_item_v0",
            "todo_id": f"todo_hist_{i}",
            "role": "agent",
            "text": f"Historical archived task {i}.",
            "status": "done",
            "done": True,
            "task_class": "advancement_task",
            "action_kind": "implement",
            "index": i + 3,
            "source_section": "Completed Work Archive",
            "archive_state": "archive",
            "claimed_by": f"peer-{i % 4}",
        })

    body = (
        "---\nstatus: active\n---\n# Goal\n## Objective\n"
        "Ship public-safe scoped context projections.\n"
        "## Agent Todo\n\n## User Todo\n\n## Completed Work Archive\n"
    )
    state.write_text(render_canonical_todo_sections(body, todos, provider_revision="fixture-src").markdown)
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=goal_id,
        domain="software",
        adapter_kind="generic_project_goal_v0",
        state_file=str(state),
        registered_agents=[agent_id, "peer-0", "peer-1", "peer-2", "peer-3"],
        quota_allowed_slots=4,
    )
    configure_goal(registry_path=registry, goal_id=goal_id, execute=True, explore_mode="planning")
    initialize_canonical_authority(
        runtime,
        goal_id,
        build_todo_runtime_shadow_projection(goal_id=goal_id, todos=todos, handoff_mode="hard_lease"),
        state_path=state,
        provider=provider,
    )

    patches = [{
        "outcome_id": "outcome-current",
        "owner_agent_id": agent_id,
        "objective": "Deliver current feature slice with verified parity.",
        "todo_ids": ["todo_current"],
        "storage_format": "records",
        "requirements": [
            {"id": f"req-{r}", "description": (f"Current requirement {r}. " + ("Verify parity. " * 20)).strip()}
            for r in range(4)
        ],
        "source": {"ref": "spec:current", "summary": ("Current spec provenance " * 20).strip()},
    }]
    for i in range(outcome_count - 1):
        patches.append({
            "outcome_id": f"outcome-hist-{i}",
            "owner_agent_id": f"peer-{i % 4}",
            "objective": f"Historical outcome {i}.",
            "todo_ids": [f"todo_hist_{i}"],
            "storage_format": "records",
            "requirements": [
                {"id": f"hreq-{r}", "description": (f"Hist req {i}-{r}. " + ("Detail check. " * 22)).strip()}
                for r in range(8)
            ],
            "source": {"ref": f"hist:{i}", "summary": ("Historical provenance " * 15).strip()},
            "review": [
                {"id": f"hreq-{r}", "conclusion": "met", "note": ("Verified acceptance criteria. " * 8).strip(),
                 "evidence_refs": [f"ev:{i}-{r}"]}
                for r in range(8)
            ],
        })

    ts_seed = """
import {openLocalAuthorityStore} from "./loopx/control_plane/coordination/local_authority_provider.ts";
import {refineGoalOutcome, serializeGoalOutcomes} from "./loopx/control_plane/goals/outcome_requirements.ts";
let input = ''; for await (const c of process.stdin) input += c;
const {root, goal, patches} = JSON.parse(input);
const store = await openLocalAuthorityStore(root, goal);
const prior = await store.loadAuthority();
if (prior.status !== 'loaded') throw new Error('missing authority');
let head = prior.head;
for (const p of patches) {
  const {review, ...decl} = p;
  let next = refineGoalOutcome(head, goal, null, p.owner_agent_id, decl);
  head = {...head, goal_outcomes: serializeGoalOutcomes(next)};
  if (review) {
    next = refineGoalOutcome(head, goal, null, p.owner_agent_id, {outcome_id: p.outcome_id, review, storage_format: "records"});
    head = {...head, goal_outcomes: serializeGoalOutcomes(next)};
  }
}
const res = await store.commitAuthority({operation_id: 'seed-mature', expected_provider_revision: prior.provider_revision, events: [], receipts: [], next_projection: head});
if (res.status !== 'applied') throw new Error(JSON.stringify(res));
"""
    repo_root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types", "--input-type=module", "-e", ts_seed],
        cwd=repo_root,
        input=json.dumps({"root": str(runtime), "goal": goal_id, "patches": patches}),
        text=True,
        check=True,
    )

    def cli(*args: str, check: bool = True) -> tuple[int, dict]:
        proc = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args],
            cwd=project,
            capture_output=True,
            text=True,
        )
        data = json.loads(proc.stdout) if proc.stdout.strip() else {"raw": proc.stderr}
        if check:
            assert proc.returncode == 0, (proc.stdout, proc.stderr)
        return proc.returncode, data

    return {"project": project, "runtime": runtime, "registry": registry, "goal_id": goal_id, "agent_id": agent_id, "cli": cli}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_mature_goal_scoped_envelope_inspect_detail_replan_and_settle_parity(tmp_path, monkeypatch, provider):
    env = _seed_mature_goal(tmp_path, monkeypatch, provider=provider)
    cli = env["cli"]
    goal_id = env["goal_id"]
    agent_id = env["agent_id"]

    # 1. Claim todo_current under hard_lease (soft claim + task-lease acquire/inspect).
    _, claim = cli("todo", "claim", "--goal-id", goal_id, "--todo-id", "todo_current", "--agent-id", agent_id, "--claimed-by", agent_id)
    assert claim["ok"], claim
    _, inspected_lease = cli("task-lease", "inspect", "--goal-id", goal_id, "--todo-id", "todo_current")
    lease = inspected_lease.get("lease")
    if not lease:
        _, acquired = cli("task-lease", "acquire", "--goal-id", goal_id, "--todo-id", "todo_current",
                            "--agent-id", agent_id, "--lease-owner", agent_id, "--execute")
        lease = acquired["lease"]

    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", f"scoped-{provider}")
    codex_context_hook(str(native), {"hook_event_name": "SessionStart", "session_id": f"scoped-{provider}", "cwd": str(env["project"]), "source": "startup"})

    # 2. Routine Turn produces lean decision.json + required-context.json.
    _, view = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "on", "--delta-delivery", "auto",
        "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine", "--codex-context-dir", str(native),
        "--decision-output-root", str(captures),
    )
    decision_path = next(captures.rglob("decision.json"))
    decision = json.loads(decision_path.read_text())
    _, req_ref = read_context_read_reference(view["recovery"]["reference_file"])
    required_ctx = json.loads(Path(req_ref["path"]).read_bytes())

    assert decision_path.stat().st_size < 100_000
    assert Path(req_ref["path"]).stat().st_size < 100_000
    wc_ga = next(s["content"]["goal_acceptance_contract"]
                 for s in decision["interaction_contract"]["agent_channel"]["work_context"]["sources"]
                 if s.get("source") == "goal_acceptance")
    reqs = wc_ga["outcome_requirements"]
    assert reqs["projection_mode"] == "turn_scoped_v0"
    assert reqs["full_outcome_count"] == 33
    assert reqs["inline_outcome_count"] == 1
    assert reqs["compacted_outcome_count"] == 32
    assert len(reqs["outcomes"]) == 33
    assert reqs["outcomes"][0]["id"] == "outcome-current" and "requirements" in reqs["outcomes"][0]
    assert all(row["summary_only"] is True and row["cold_read_command"].startswith("loopx goal-acceptance inspect")
               for row in reqs["outcomes"][1:])
    assert len(required_ctx.get("context_details", {}).get("unread_context_index", {}).get("pointers", [])) < 20

    step1 = decision["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    assert step1["vision_authoring"]["summary_only"] is True
    assert step1["optional_attachments"][0]["summary_only"] is True
    assert step1["optional_attachments"][0]["required"] is False
    assert "--settle-turn" in step1["command_template"]

    # 3. Full cold-read via goal-acceptance inspect still returns all 33 full outcomes verbatim.
    _, inspected = cli("goal-acceptance", "inspect", "--goal-id", goal_id)
    full_outcomes = inspected["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert len(full_outcomes) == 33
    assert all("requirements" in row and "sources" in row for row in full_outcomes)

    # 4. --include-detail vision on the same Turn delivers full vision_authoring & optional_attachments.
    _, detail = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off", "--include-detail", "vision",
        "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine",
    )
    detail_step1 = detail["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    assert "minimal_example" in detail_step1["vision_authoring"]
    assert "guidance" in detail_step1["optional_attachments"][0]

    # 5. End-to-end execution parity: refresh-state + spend-slot settles cleanly on File & SQLite.
    _, settled = cli(
        "refresh-state", "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine", "--classification", "implemented",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--delivery-workspace-path", str(env["project"]),
        "--task-lease-idempotency-key", lease["idempotency_key"],
        "--task-lease-expected-version", str(lease["version"]),
        "--settle-turn", "--settlement-receipt", "--no-global-sync", "--suppress-external-sinks",
    )
    assert settled["ok"] and settled["settlement_progress"]["state"] == "settled", settled

    # 6. Tampered scoped capture is rejected fail-closed.
    tampered_path = tmp_path / "tampered-decision.json"
    tampered = copy.deepcopy(decision)
    tampered["contract_binding"]["content_hash"] = "sha256:" + ("0" * 64)
    tampered_path.write_text(json.dumps(tampered))
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
    with pytest.raises(Exception):
        assert_complete_turn_decision(tampered)
