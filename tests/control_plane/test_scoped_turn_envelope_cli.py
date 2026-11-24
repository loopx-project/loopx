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
    goal_id: str = "mature-scoped-goal",
    objective_prefix: str = "Historical outcome",
    explore_graph_enabled: bool = False,
) -> dict[str, object]:
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    registry = project / ".loopx" / "registry.json"
    state = project / "ACTIVE_GOAL_STATE.md"
    project.mkdir(parents=True)
    registry.parent.mkdir(parents=True)

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
    if explore_graph_enabled:
        cfg = json.loads(registry.read_text())
        cfg["goals"][0]["explore_graph"] = {"enabled": True}
        registry.write_text(json.dumps(cfg))
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
            "objective": f"{objective_prefix} {i}.",
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

    # required-context.json omits deferred_settlement_details and stays lean; decision.json preserves admission-time goal_acceptance.
    assert Path(req_ref["path"]).stat().st_size < decision_path.stat().st_size * 0.38
    assert "deferred_settlement_details" not in required_ctx
    deferred_ga = decision["deferred_settlement_details"]["goal_acceptance"]["outcome_requirements"]
    assert len(deferred_ga["outcomes"]) == 33
    assert all("requirements" in row and "sources" in row for row in deferred_ga["outcomes"])
    wc_ga = next(s["content"]["goal_acceptance_contract"]
                 for s in decision["interaction_contract"]["agent_channel"]["work_context"]["sources"]
                 if s.get("source") == "goal_acceptance")
    reqs = wc_ga["outcome_requirements"]
    assert reqs["projection_mode"] == "turn_scoped_v0"
    assert reqs["full_outcome_count"] == 33
    assert reqs["inline_outcome_count"] == 1
    assert reqs["compacted_outcome_count"] == 32
    assert isinstance(reqs["full_outcomes_sha256"], str) and reqs["full_outcomes_sha256"].startswith("sha256:")
    assert reqs["outcome_revision"] == reqs["revision"]
    assert "--reference-file" in reqs["capture_read_command"]
    assert "--select /deferred_settlement_details/goal_acceptance" in reqs["capture_read_command"]
    assert "--include-detail goal_acceptance" in reqs["cold_read_instruction"]
    assert str(env["registry"]) in reqs["cold_read_command"]
    assert str(env["runtime"]) in reqs["cold_read_command"]
    assert f"goal-acceptance inspect --goal-id {goal_id}" in reqs["cold_read_command"]
    assert len(reqs["outcomes"]) == 33
    assert reqs["outcomes"][0]["id"] == "outcome-current" and "requirements" in reqs["outcomes"][0]
    assert all(
        row["summary_only"] is True
        and row["cold_read_command"] == reqs["cold_read_command"]
        and f"/deferred_settlement_details/goal_acceptance/outcome_requirements/outcomes/{idx}" in row["capture_read_command"]
        for idx, row in enumerate(reqs["outcomes"][1:], start=1)
    )
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

    # Uncaptured --context-delivery off omits deferred_settlement_details and preserves both
    # all 33 full inline goal_acceptance outcomes and all 14 Explore attachment fields alongside
    # complete 11-field vision_authoring for disabled-delivery parity.
    _, uncaptured = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off",
        "--goal-id", goal_id, "--agent-id", agent_id, "--todo-id", "todo_current",
        "--turn-instance-id", "turn-routine",
    )
    assert "deferred_settlement_details" not in uncaptured
    uncaptured_ga = next(
        s["content"]["goal_acceptance_contract"]
        for s in uncaptured["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    uncaptured_ga_reqs = uncaptured_ga["outcome_requirements"]
    assert "projection_mode" not in uncaptured_ga_reqs
    assert "capture_read_command" not in uncaptured_ga_reqs
    assert len(uncaptured_ga_reqs["outcomes"]) == 33
    assert all(
        "requirements" in row and "sources" in row and "summary_only" not in row
        for row in uncaptured_ga_reqs["outcomes"]
    )
    uncaptured_step1 = uncaptured["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    assert uncaptured_step1["vision_authoring"] == detail_step1["vision_authoring"]
    assert uncaptured_step1["optional_attachments"] == detail_step1["optional_attachments"]
    assert "summary_only" not in uncaptured_step1["optional_attachments"][0]
    assert "guidance" in uncaptured_step1["optional_attachments"][0]

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
    assert Path(req_ref["path"]).stat().st_size < len(json.dumps(ga_detail).encode("utf-8")) * 0.45
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


def _claim_current_todo(env: dict[str, object]) -> None:
    cli = env["cli"]
    goal_id = env["goal_id"]
    agent_id = env["agent_id"]
    _, claim = cli(
        "todo", "claim", "--goal-id", goal_id, "--todo-id", "todo_current",
        "--agent-id", agent_id, "--claimed-by", agent_id,
    )
    assert claim["ok"], claim
    _, inspected_lease = cli("task-lease", "inspect", "--goal-id", goal_id, "--todo-id", "todo_current")
    if not inspected_lease.get("lease"):
        _, acquired = cli(
            "task-lease", "acquire", "--goal-id", goal_id, "--todo-id", "todo_current",
            "--agent-id", agent_id, "--lease-owner", agent_id, "--execute",
        )
        assert acquired.get("lease"), acquired


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scoped_acceptance_cold_read_binds_original_workspace_across_sibling_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    ws_a = _seed_mature_goal(
        tmp_path / "ws_a",
        monkeypatch,
        provider=provider,
        outcome_count=9,
        goal_id="shared-goal",
        objective_prefix="Workspace A outcome",
    )
    ws_b = _seed_mature_goal(
        tmp_path / "ws_b",
        monkeypatch,
        provider=provider,
        outcome_count=7,
        goal_id="shared-goal",
        objective_prefix="Workspace B outcome",
    )
    _claim_current_todo(ws_a)

    # Sanity check: Workspace B directly holds 7 outcomes under the same goal_id.
    _, ws_b_direct = ws_b["cli"]("goal-acceptance", "inspect", "--goal-id", "shared-goal")
    ws_b_outcomes = ws_b_direct["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert len(ws_b_outcomes) == 7
    assert ws_b_outcomes[1]["objective"].startswith("Workspace B outcome")

    native = tmp_path / "native_a"
    native.mkdir(mode=0o700)
    captures = tmp_path / "captures_a"
    captures.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", f"cross-ws-{provider}")
    codex_context_hook(
        str(native),
        {"hook_event_name": "SessionStart", "session_id": f"cross-ws-{provider}", "cwd": str(ws_a["project"]), "source": "startup"},
    )

    _, view = ws_a["cli"](
        "quota", "should-run", "--codex-app", "--context-delivery", "on", "--delta-delivery", "auto",
        "--goal-id", "shared-goal", "--agent-id", "worker", "--todo-id", "todo_current",
        "--turn-instance-id", "turn-ws-a", "--codex-context-dir", str(native),
        "--decision-output-root", str(captures),
    )
    decision_path = next(captures.rglob("decision.json"))
    decision = json.loads(decision_path.read_text())
    _, req_ref = read_context_read_reference(view["recovery"]["reference_file"])
    required_ctx = json.loads(Path(req_ref["path"]).read_bytes())

    wc_ga = next(
        s["content"]["goal_acceptance_contract"]
        for s in decision["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    reqs = wc_ga["outcome_requirements"]
    assert reqs["full_outcome_count"] == 9
    assert reqs["compacted_outcome_count"] == 8
    assert isinstance(reqs["full_outcomes_sha256"], str) and reqs["full_outcomes_sha256"].startswith("sha256:")
    assert reqs["outcome_revision"] == reqs["revision"]
    assert "--include-detail goal_acceptance" in reqs["cold_read_instruction"]

    summary_cmd = reqs["cold_read_command"]
    compact_rows = [row for row in reqs["outcomes"] if row.get("summary_only") is True]
    assert len(compact_rows) == 8
    row_cmd = compact_rows[0]["cold_read_command"]
    assert row_cmd == summary_cmd

    # Progressive first read carries the exact same bound summary command, metadata, and row commands.
    req_ga = next(
        s["content"]["goal_acceptance_contract"]
        for s in required_ctx["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    req_reqs = req_ga["outcome_requirements"]
    assert req_reqs["cold_read_command"] == summary_cmd
    assert req_reqs["full_outcomes_sha256"] == reqs["full_outcomes_sha256"]
    assert req_reqs["outcome_revision"] == reqs["outcome_revision"]
    assert req_reqs["cold_read_instruction"] == reqs["cold_read_instruction"]
    assert req_reqs["outcomes"][1]["cold_read_command"] == summary_cmd

    # Run BOTH emitted cold_read_commands verbatim from Workspace B without adding flags.
    for cmd in (summary_cmd, row_cmd):
        proc = subprocess.run(
            shlex.split(cmd),
            cwd=ws_b["project"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        inspected = json.loads(proc.stdout)
        outcomes = inspected["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
        assert len(outcomes) == 9
        assert all(row["objective"].startswith("Workspace A outcome") for row in outcomes[1:])

    # Default relative `.loopx/registry.json` invocation in Workspace A also binds absolutely across cwd.
    ws_a_rel = _seed_mature_goal(
        tmp_path / "ws_a_rel",
        monkeypatch,
        provider=provider,
        outcome_count=9,
        goal_id="shared-goal",
        objective_prefix="Workspace A rel outcome",
    )
    _claim_current_todo(ws_a_rel)
    rel_native = tmp_path / "native_rel"
    rel_native.mkdir(mode=0o700)
    rel_captures = tmp_path / "captures_rel"
    rel_captures.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", f"cross-ws-rel-{provider}")
    codex_context_hook(
        str(rel_native),
        {"hook_event_name": "SessionStart", "session_id": f"cross-ws-rel-{provider}", "cwd": str(ws_a_rel["project"]), "source": "startup"},
    )
    rel_proc = subprocess.run(
        [
            sys.executable, "-m", "loopx.cli",
            "--runtime-root", str(ws_a_rel["runtime"]),
            "--format", "json",
            "quota", "should-run", "--codex-app", "--context-delivery", "on", "--delta-delivery", "auto",
            "--goal-id", "shared-goal", "--agent-id", "worker", "--todo-id", "todo_current",
            "--turn-instance-id", "turn-ws-a-rel", "--codex-context-dir", str(rel_native),
            "--decision-output-root", str(rel_captures),
        ],
        cwd=ws_a_rel["project"],
        capture_output=True,
        text=True,
    )
    assert rel_proc.returncode == 0, rel_proc.stdout + rel_proc.stderr
    rel_view = json.loads(rel_proc.stdout)
    _, rel_req_ref = read_context_read_reference(rel_view["recovery"]["reference_file"])
    rel_req_ctx = json.loads(Path(rel_req_ref["path"]).read_bytes())
    rel_reqs = next(
        s["content"]["goal_acceptance_contract"]
        for s in rel_req_ctx["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )["outcome_requirements"]
    rel_cold_proc = subprocess.run(
        shlex.split(rel_reqs["cold_read_command"]),
        cwd=ws_b["project"],
        capture_output=True,
        text=True,
    )
    assert rel_cold_proc.returncode == 0, rel_cold_proc.stdout + rel_cold_proc.stderr
    rel_outcomes = json.loads(rel_cold_proc.stdout)["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert len(rel_outcomes) == 9
    assert all(row["objective"].startswith("Workspace A rel outcome") for row in rel_outcomes[1:])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_captured_turn_recovers_admission_time_outcomes_after_goal_advances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    env = _seed_mature_goal(
        tmp_path,
        monkeypatch,
        provider=provider,
        outcome_count=9,
        goal_id="drift-goal",
        objective_prefix="Admitted outcome",
    )
    cli = env["cli"]
    _claim_current_todo(env)

    _, before_inspect = cli("goal-acceptance", "inspect", "--goal-id", "drift-goal")
    admitted_rev = before_inspect["goal_acceptance_contract"]["outcome_requirements"]["revision"]
    provider_rev = before_inspect["provider_revision"]
    orig_hist0 = before_inspect["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][1]
    assert orig_hist0["id"] == "outcome-hist-0"
    orig_req_ids = [req["id"] for req in orig_hist0["requirements"]]
    assert len(orig_req_ids) == 8

    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", f"drift-{provider}")
    codex_context_hook(
        str(native),
        {"hook_event_name": "SessionStart", "session_id": f"drift-{provider}", "cwd": str(env["project"]), "source": "startup"},
    )

    # 1. Admit Turn 1 with capture + progressive delivery.
    _, view = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "on", "--delta-delivery", "auto",
        "--goal-id", "drift-goal", "--agent-id", "worker", "--todo-id", "todo_current",
        "--turn-instance-id", "turn-drift", "--codex-context-dir", str(native),
        "--decision-output-root", str(captures),
    )
    decision_path = next(captures.rglob("decision.json"))
    decision = json.loads(decision_path.read_text())
    _, req_ref = read_context_read_reference(view["recovery"]["reference_file"])
    required_ctx = json.loads(Path(req_ref["path"]).read_bytes())

    assert "deferred_settlement_details" not in required_ctx
    assert Path(req_ref["path"]).stat().st_size < decision_path.stat().st_size * 0.6
    req_reqs = next(
        s["content"]["goal_acceptance_contract"]["outcome_requirements"]
        for s in required_ctx["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    assert req_reqs["projection_mode"] == "turn_scoped_v0"
    assert req_reqs["outcome_revision"] == admitted_rev
    row1 = req_reqs["outcomes"][1]
    assert row1["id"] == "outcome-hist-0" and row1["summary_only"] is True
    assert "capture_read_command" in req_reqs and "capture_read_command" in row1
    assert "cold_read_command" in req_reqs and "cold_read_command" in row1

    # 2. Advance live Goal acceptance after Turn 1 admission by adding a new requirement.
    refine_doc = tmp_path / "advance-outcome.json"
    refine_doc.write_text(json.dumps({
        "outcome_id": "outcome-hist-0",
        "owner_agent_id": "peer-0",
        "objective": orig_hist0["objective"],
        "todo_ids": orig_hist0["todo_ids"],
        "storage_format": "records",
        "source": {"ref": "spec:post-admission", "summary": "Post-admission requirement provenance."},
        "requirements": [
            {"id": req["id"], "description": req["description"]}
            for req in orig_hist0["requirements"]
        ] + [
            {"id": "hreq-post-admission", "description": "Post-admission requirement added after Turn 1."},
        ],
    }))
    _, refined = cli(
        "goal-acceptance", "refine",
        "--goal-id", "drift-goal",
        "--agent-id", "peer-0",
        "--document", str(refine_doc),
        "--operation-id", "op-advance-after-turn1",
        "--expected-provider-revision", provider_rev,
        "--execute",
    )
    assert refined["ok"] is True
    live_reqs = refined["goal_acceptance_contract"]["outcome_requirements"]
    assert live_reqs["revision"] == admitted_rev + 1

    # 3. Immutable capture readback (both summary and compacted row capture_read_command)
    #    recovers the exact admission-time outcomes/requirements (NO post-admission requirement).
    summary_capture_proc = subprocess.run(
        shlex.split(req_reqs["capture_read_command"]),
        cwd=env["project"],
        capture_output=True,
        text=True,
    )
    assert summary_capture_proc.returncode == 0, summary_capture_proc.stdout + summary_capture_proc.stderr
    summary_page = json.loads(summary_capture_proc.stdout)
    assert summary_page["ok"] is True and summary_page["eof"] is True
    captured_ga = json.loads(summary_page["content"])["selections"][0]["value"]
    captured_reqs = captured_ga["outcome_requirements"]
    assert captured_reqs["revision"] == admitted_rev
    assert len(captured_reqs["outcomes"]) == 9
    assert [r["id"] for r in captured_reqs["outcomes"][1]["requirements"]] == orig_req_ids
    assert all(r["id"] != "hreq-post-admission" for r in captured_reqs["outcomes"][1]["requirements"])

    row_capture_proc = subprocess.run(
        shlex.split(row1["capture_read_command"]),
        cwd=env["project"],
        capture_output=True,
        text=True,
    )
    assert row_capture_proc.returncode == 0, row_capture_proc.stdout + row_capture_proc.stderr
    row_page = json.loads(row_capture_proc.stdout)
    assert row_page["ok"] is True and row_page["eof"] is True
    captured_row = json.loads(row_page["content"])["selections"][0]["value"]
    assert captured_row["id"] == "outcome-hist-0"
    assert [r["id"] for r in captured_row["requirements"]] == orig_req_ids

    # 4. Live cold_read_command and --include-detail goal_acceptance inspect current live revision.
    live_proc = subprocess.run(
        shlex.split(req_reqs["cold_read_command"]),
        cwd=env["project"],
        capture_output=True,
        text=True,
    )
    assert live_proc.returncode == 0, live_proc.stdout + live_proc.stderr
    live_inspect_reqs = json.loads(live_proc.stdout)["goal_acceptance_contract"]["outcome_requirements"]
    assert live_inspect_reqs["revision"] == admitted_rev + 1
    assert "hreq-post-admission" in [r["id"] for r in live_inspect_reqs["outcomes"][1]["requirements"]]

    _, live_detail = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off",
        "--include-detail", "goal_acceptance",
        "--goal-id", "drift-goal", "--agent-id", "worker", "--todo-id", "todo_current",
        "--turn-instance-id", "turn-drift",
    )
    detail_reqs = next(
        s["content"]["goal_acceptance_contract"]["outcome_requirements"]
        for s in live_detail["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    assert detail_reqs["revision"] == admitted_rev + 1
    assert "hreq-post-admission" in [r["id"] for r in detail_reqs["outcomes"][1]["requirements"]]
    assert decision["deferred_settlement_details"]["goal_acceptance"]["outcome_requirements"]["revision"] == admitted_rev


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_disabled_context_delivery_preserves_full_inline_outcomes_without_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    env = _seed_mature_goal(
        tmp_path,
        monkeypatch,
        provider=provider,
        outcome_count=9,
        goal_id="disabled-delivery-goal",
        objective_prefix="Disabled delivery outcome",
        explore_graph_enabled=True,
    )
    cli = env["cli"]
    _claim_current_todo(env)

    _, before_inspect = cli("goal-acceptance", "inspect", "--goal-id", "disabled-delivery-goal")
    admitted_rev = before_inspect["goal_acceptance_contract"]["outcome_requirements"]["revision"]
    provider_rev = before_inspect["provider_revision"]
    orig_hist0 = before_inspect["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][1]
    assert orig_hist0["id"] == "outcome-hist-0"
    orig_req_ids = [req["id"] for req in orig_hist0["requirements"]]
    assert len(orig_req_ids) == 8

    # 1. Explicit `--context-delivery off` with NO capture location must keep all 9 full outcomes,
    #    all 8 historical requirements, all 14 Explore attachment fields, and Explore graph guidance ref inline.
    _, off_nocapture = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off",
        "--goal-id", "disabled-delivery-goal", "--agent-id", "worker", "--todo-id", "todo_current",
        "--turn-instance-id", "turn-off-nocapture",
    )
    assert "deferred_settlement_details" not in off_nocapture
    off_reqs = next(
        s["content"]["goal_acceptance_contract"]["outcome_requirements"]
        for s in off_nocapture["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    assert "projection_mode" not in off_reqs
    assert off_reqs["revision"] == admitted_rev
    assert len(off_reqs["outcomes"]) == 9
    assert all("summary_only" not in row and "requirements" in row for row in off_reqs["outcomes"])
    assert [r["id"] for r in off_reqs["outcomes"][1]["requirements"]] == orig_req_ids

    off_step1 = off_nocapture["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    off_att = off_step1["optional_attachments"][0]
    assert len(off_att) == 14
    assert "summary_only" not in off_att and "cold_read_command" not in off_att
    for key in ("guidance", "attachment_template", "path_delta_attachment_template"):
        assert key in off_att
    off_graph = next(
        s["content"]["graph"]
        for s in off_nocapture["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("kind") == "explore_turn_context"
    )
    assert "result_writeback_guidance_summary_only" not in off_graph
    assert "result_writeback_guidance_ref" in off_graph

    # Uncaptured compose_turn_decision (without capture_reference_file) also refuses to compact outcomes or attachments.
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision

    stripped_input = {
        key: copy.deepcopy(val)
        for key, val in off_nocapture.items()
        if key not in {"schema_version", "contract_binding", "deferred_settlement_details"}
    }
    composed_no_capture = compose_turn_decision(
        stripped_input,
        scheduler_execution_context=None,
        composed_settlement=True,
        include_goal_acceptance_detail=False,
        include_vision_detail=False,
        capture_reference_file=None,
    )
    assert "deferred_settlement_details" not in composed_no_capture
    nocap_reqs = next(
        s["content"]["goal_acceptance_contract"]["outcome_requirements"]
        for s in composed_no_capture["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    assert "projection_mode" not in nocap_reqs
    assert len(nocap_reqs["outcomes"]) == 9
    assert [r["id"] for r in nocap_reqs["outcomes"][1]["requirements"]] == orig_req_ids
    nocap_att = composed_no_capture["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["optional_attachments"][0]
    assert nocap_att == off_att and len(nocap_att) == 14

    # 2. Advance live Goal acceptance (`admitted_rev -> admitted_rev + 1`), run `--context-delivery off`
    #    with `--decision-output-dir` while Explore is enabled, then disable Explore in registry.
    refine_doc = tmp_path / "advance-disabled-delivery.json"
    refine_doc.write_text(json.dumps({
        "outcome_id": "outcome-hist-0",
        "owner_agent_id": "peer-0",
        "objective": orig_hist0["objective"],
        "todo_ids": orig_hist0["todo_ids"],
        "storage_format": "records",
        "source": {"ref": "spec:post-off-admission", "summary": "Post-admission requirement provenance."},
        "requirements": [
            {"id": req["id"], "description": req["description"]}
            for req in orig_hist0["requirements"]
        ] + [
            {"id": "hreq-post-off-admission", "description": "Added after off admission."},
        ],
    }))
    _, refined = cli(
        "goal-acceptance", "refine",
        "--goal-id", "disabled-delivery-goal",
        "--agent-id", "peer-0",
        "--document", str(refine_doc),
        "--operation-id", "op-advance-after-off",
        "--expected-provider-revision", provider_rev,
        "--execute",
    )
    assert refined["ok"] is True

    off_capture_dir = tmp_path / "off-capture-dir"
    _, off_with_dir = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off",
        "--decision-output-dir", str(off_capture_dir),
        "--goal-id", "disabled-delivery-goal", "--agent-id", "worker", "--todo-id", "todo_current",
        "--turn-instance-id", "turn-off-nocapture",
    )
    off_dir_decision = json.loads((off_capture_dir / "decision.json").read_text())

    configure_goal(registry_path=env["registry"], goal_id="disabled-delivery-goal", execute=True, explore_mode="off")
    explore_rc, explore_disabled = cli(
        "explore", "turn-context", "--goal-id", "disabled-delivery-goal", "--agent-id", "worker", "--todo-id", "todo_current",
        check=False,
    )
    assert explore_rc == 0
    assert explore_disabled["graph_enabled"] is False
    assert "result_writeback_guidance" not in (explore_disabled.get("graph") or {})

    # Original response and `--context-delivery off --decision-output-dir` both preserve all 14 Explore
    # attachment fields and full inline outcomes even after Explore is disabled and Goal advances;
    # same-Turn `--include-detail goal_acceptance` reads live rev + 1.
    assert len(off_att) == 14 and "guidance" in off_att
    for payload in (off_with_dir, off_dir_decision):
        assert "deferred_settlement_details" not in payload
        dir_reqs = next(
            s["content"]["goal_acceptance_contract"]["outcome_requirements"]
            for s in payload["interaction_contract"]["agent_channel"]["work_context"]["sources"]
            if s.get("source") == "goal_acceptance"
        )
        assert "projection_mode" not in dir_reqs
        assert dir_reqs["revision"] == admitted_rev + 1
        assert len(dir_reqs["outcomes"]) == 9
        assert all("summary_only" not in row and "requirements" in row for row in dir_reqs["outcomes"])
        assert "hreq-post-off-admission" in [r["id"] for r in dir_reqs["outcomes"][1]["requirements"]]
        dir_att = payload["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["optional_attachments"][0]
        assert dir_att == off_att and len(dir_att) == 14

    _, detail_after = cli(
        "quota", "should-run", "--codex-app", "--context-delivery", "off",
        "--include-detail", "goal_acceptance",
        "--goal-id", "disabled-delivery-goal", "--agent-id", "worker", "--todo-id", "todo_current",
        "--turn-instance-id", "turn-off-nocapture",
    )
    detail_after_reqs = next(
        s["content"]["goal_acceptance_contract"]["outcome_requirements"]
        for s in detail_after["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if s.get("source") == "goal_acceptance"
    )
    assert detail_after_reqs["revision"] == admitted_rev + 1
    assert "hreq-post-off-admission" in [r["id"] for r in detail_after_reqs["outcomes"][1]["requirements"]]
    assert [r["id"] for r in off_reqs["outcomes"][1]["requirements"]] == orig_req_ids
