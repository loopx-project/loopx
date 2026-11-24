"""End-to-end File + SQLite verification for scoped outcomes & lean settlement plans."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shlex
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
    assert len(step1["vision_authoring"]) == 11
    assert "minimal_example" in step1["vision_authoring"]
    assert step1["optional_attachments"][0]["summary_only"] is True
    assert step1["optional_attachments"][0]["required"] is False
    assert "--settle-turn" in step1["command_template"]

    # 3. Full cold-read via goal-acceptance inspect still returns all 33 full outcomes verbatim.
    _, inspected = cli("goal-acceptance", "inspect", "--goal-id", goal_id)
    full_outcomes = inspected["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert len(full_outcomes) == 33
    assert all("requirements" in row and "sources" in row for row in full_outcomes)

    # 4. --include-detail vision on the same Turn delivers full optional_attachments alongside complete vision_authoring.
    _, detail = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off", "--include-detail", "vision",
        "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine",
    )
    detail_step1 = detail["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    assert detail_step1["vision_authoring"] == step1["vision_authoring"]
    assert len(detail_step1["vision_authoring"]) == 11
    assert "guidance" in detail_step1["optional_attachments"][0]

    # 4b. Captured Turn retains complete 11-field vision_authoring at its canonical step pointer
    #     and preserves full optional_attachments in deferred_settlement_details inside decision.json
    #     while omitting deferred_settlement_details from required-context.json; initial first-read
    #     keeps vision_authoring concise while immutable capture readback and explore cold-read succeed
    #     verbatim on File & SQLite.
    deferred = decision["deferred_settlement_details"]
    assert deferred["optional_attachments"] == detail_step1["optional_attachments"]
    assert "deferred_settlement_details" not in required_ctx

    first_read = subprocess.run(
        shlex.split(view["first_read_command"]),
        cwd=env["project"],
        capture_output=True,
        text=True,
    )
    assert first_read.returncode == 0, first_read.stderr
    first_page = json.loads(first_read.stdout)
    assert first_page["ok"] is True
    assert "minimal_example" not in first_page["content"]

    ga_index = next(
        i for i, s in enumerate(decision["interaction_contract"]["agent_channel"]["work_context"]["sources"])
        if s.get("source") == "goal_acceptance"
    )
    recovery_read_cmd = (
        view["recovery"]["command"]
        + " --select /interaction_contract/cli_channel/settlement_plan/ordered_steps/1/vision_authoring"
        + " --select /interaction_contract/cli_channel/settlement_plan/identity"
        + f" --select /interaction_contract/agent_channel/work_context/sources/{ga_index}/content/goal_acceptance_contract"
    )
    captured_cold_read = subprocess.run(
        shlex.split(recovery_read_cmd),
        cwd=env["project"],
        capture_output=True,
        text=True,
    )
    assert captured_cold_read.returncode == 0, captured_cold_read.stdout + captured_cold_read.stderr
    read_page = json.loads(captured_cold_read.stdout)
    assert read_page["ok"] is True and read_page["eof"] is True
    selected = json.loads(read_page["content"])
    by_pointer = {row["pointer"]: row["value"] for row in selected["selections"]}
    assert by_pointer["/interaction_contract/cli_channel/settlement_plan/ordered_steps/1/vision_authoring"] == detail_step1["vision_authoring"]
    assert by_pointer["/interaction_contract/cli_channel/settlement_plan/identity"]["effect_id"] == decision["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]["effect_id"]
    acceptance_ptrs = [ptr for ptr in by_pointer if ptr.endswith(("goal_acceptance_contract", "acceptance_contract"))]
    assert acceptance_ptrs == [f"/interaction_contract/agent_channel/work_context/sources/{ga_index}/content/goal_acceptance_contract"]
    assert by_pointer[acceptance_ptrs[0]]["outcome_requirements"]["outcomes"][0]["id"] == "outcome-current"

    # Full decision capture reader (.reader.json) delivers deferred_settlement_details,
    # 11-field vision_authoring, settlement identity, and scoped goal_acceptance verbatim.
    decision_reader_cmd = [
        sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota", "context-read",
        "--reference-file", str(decision_path.with_suffix(".reader.json")),
        "--select", "/interaction_contract/cli_channel/settlement_plan/ordered_steps/1/vision_authoring",
        "--select", "/deferred_settlement_details/optional_attachments",
        "--select", "/interaction_contract/cli_channel/settlement_plan/identity",
        "--select", f"/interaction_contract/agent_channel/work_context/sources/{ga_index}/content/goal_acceptance_contract",
        "--page-bytes", "64000", "--max-page-output-bytes", "64000",
    ]
    decision_read = subprocess.run(decision_reader_cmd, cwd=env["project"], capture_output=True, text=True)
    assert decision_read.returncode == 0, decision_read.stdout + decision_read.stderr
    decision_selected = {
        row["pointer"]: row["value"]
        for row in json.loads(json.loads(decision_read.stdout)["content"])["selections"]
    }
    assert decision_selected["/interaction_contract/cli_channel/settlement_plan/ordered_steps/1/vision_authoring"] == detail_step1["vision_authoring"]
    assert decision_selected["/deferred_settlement_details/optional_attachments"] == detail_step1["optional_attachments"]
    assert decision_selected["/interaction_contract/cli_channel/settlement_plan/identity"]["effect_id"] == decision["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]["effect_id"]
    assert decision_selected[f"/interaction_contract/agent_channel/work_context/sources/{ga_index}/content/goal_acceptance_contract"] == wc_ga

    # Explore cold_read_command inherits registry & runtime-root and executes verbatim.
    explore_cold_read = subprocess.run(
        shlex.split(step1["optional_attachments"][0]["cold_read_command"]),
        cwd=env["project"],
        capture_output=True,
        text=True,
    )
    assert explore_cold_read.returncode == 0, explore_cold_read.stderr
    explore_payload = json.loads(explore_cold_read.stdout)
    assert explore_payload["ok"] is True

    # Uncaptured --context-delivery off omits deferred_settlement_details, retains complete 11-field
    # vision_authoring, and executes its verbatim explore cold_read_command cleanly.
    _, uncaptured = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off",
        "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine",
    )
    assert "deferred_settlement_details" not in uncaptured
    uncaptured_step1 = uncaptured["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    assert uncaptured_step1["vision_authoring"] == detail_step1["vision_authoring"]
    uncaptured_explore_read = subprocess.run(
        shlex.split(uncaptured_step1["optional_attachments"][0]["cold_read_command"]),
        cwd=env["project"],
        capture_output=True,
        text=True,
    )
    assert uncaptured_explore_read.returncode == 0, uncaptured_explore_read.stderr
    assert json.loads(uncaptured_explore_read.stdout)["ok"] is True

    # 4c. --include-detail goal_acceptance returns all 33 full inline outcomes cleanly,
    #     and unsupported or invalid --include-detail choices fail cleanly.
    _, ga_detail = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off", "--include-detail", "goal_acceptance",
        "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine",
    )
    ga_full = next(
        s["content"]["goal_acceptance_contract"]
        for s in ga_detail["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    ga_full_reqs = ga_full["outcome_requirements"]
    assert "projection_mode" not in ga_full_reqs
    assert len(ga_full_reqs["outcomes"]) == 33
    assert all("requirements" in row and "sources" in row and "summary_only" not in row for row in ga_full_reqs["outcomes"])

    bad_detail_rc, _ = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off", "--include-detail", "bogus",
        "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine", check=False,
    )
    assert bad_detail_rc == 2
    plan_detail_rc, plan_detail_err = cli(
        "quota", "plan", "--include-detail", "goal_acceptance",
        "--goal-id", goal_id, "--agent-id", agent_id, check=False,
    )
    assert plan_detail_rc == 1
    assert plan_detail_err["error_code"] == "QUOTA_VALIDATION_FAILED"
    assert "goal_acceptance" in json.dumps(plan_detail_err)

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
