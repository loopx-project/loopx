#!/usr/bin/env python3
"""Smoke-test that auto-research worker-turn does not fake research output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.auto_research.demo_e2e import _seed_visible_demo_control_plane  # noqa: E402
from demo.auto_research.demo_supervisor import (  # noqa: E402
    build_auto_research_demo_supervisor_plan,
)
from demo.auto_research.evidence_packet import (  # noqa: E402
    build_auto_research_evidence_packet,
)
from demo.auto_research.preset import (  # noqa: E402
    auto_research_successor_specs_for_action,
)
from demo.auto_research.rollout_append import (  # noqa: E402
    append_auto_research_rollout_events,
)
from demo.multi_agent.role_successor import (  # noqa: E402
    apply_role_successor_todos,
)
from loopx.todos import add_goal_todo, complete_goal_todo  # noqa: E402
from loopx.control_plane.coordination.local_authority import (  # noqa: E402
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.control_plane.work_items.task_lease import (  # noqa: E402
    TaskLeaseError,
    acquire_task_lease,
    inspect_task_lease,
    release_task_lease,
)
from demo.auto_research.worker_runtime import run_auto_research_worker_turn  # noqa: E402

from examples.auto_research_lightweight_fixture import (  # noqa: E402
    AGENT_ID as EVIDENCE_AGENT_ID,
    GROUNDING_REF,
    HYPOTHESIS_ID,
    HYPOTHESIS_TEXT,
    MECHANISM_FAMILY,
    TODO_ID as EVIDENCE_TODO_ID,
    eval_result,
    research_contract,
)


GOAL_ID = "loopx-auto-research-demo"
CURATOR_AGENT_ID = "research-curator"
HYPOTHESIS_AGENT_ID = "hypothesis-proposer"
EXECUTOR_AGENT_ID = "research-executor"
EVALUATOR_AGENT_ID = "evaluator-promoter"
LANES = [
    "research-curator:research-curator:research_curator",
    "hypothesis-proposer:hypothesis-proposer:hypothesis_proposer",
    "research-executor:research-executor:research_executor",
    "evaluator-promoter:evaluator-promoter:evaluator_promoter",
]


def assert_public_safe(payload: Any) -> None:
    text = json.dumps(payload, sort_keys=True) if not isinstance(payload, str) else payload
    forbidden = [
        "/" + "Users/",
        "/" + "private/",
        "/" + "tmp/",
        "http" + "://",
        "https" + "://",
        "api" + "_key",
        "pass" + "word",
        "sec" + "ret",
    ]
    leaked = [needle for needle in forbidden if needle.lower() in text.lower()]
    assert not leaked, leaked


def run_worker_turn(
    *,
    registry: Path,
    runtime_root: str | None,
    workspace: Path,
    agent_id: str,
    execute: bool,
    complete: bool = False,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
    args = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        "auto-research",
        "worker-turn",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        agent_id,
        "--lane-count",
        "4",
        "--visible-lanes-accepted",
    ]
    if execute:
        args.append("--execute")
    if complete:
        args.append("--complete-selected-todo")
    result = subprocess.run(
        args,
        cwd=workspace,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"worker-turn failed rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return json.loads(result.stdout)


def assert_manual_research_required(payload: dict[str, Any], *, action: str) -> None:
    assert payload["schema_version"] == "auto_research_worker_turn_v0", payload
    assert payload["mode"] == "manual_research_required", payload
    assert payload["selected_action"] == action, payload
    assert payload["executed"] is False, payload
    assert payload["manual_research_required"] is True, payload
    assert payload["completion"]["executed"] is False, payload
    assert payload["public_boundary"]["fake_metrics_recorded"] is False, payload
    assert "dev_metric" not in payload, payload
    assert "holdout_metric" not in payload, payload
    assert "live_evidence" not in payload, payload
    assert_public_safe(payload)


def assert_no_action(payload: dict[str, Any], *, agent_id: str) -> None:
    assert payload["schema_version"] == "auto_research_worker_turn_v0", payload
    assert payload["mode"] == "no_action", payload
    assert payload["agent_id"] == agent_id, payload
    assert payload["executed"] is False, payload
    assert payload["frontier"]["frontier"]["selected"] is None, payload
    assert payload["frontier"]["frontier"]["runnable_count"] == 0, payload
    assert_public_safe(payload)


def write_summary_state(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# Auto Research Worker Summary Smoke",
                "",
                "## User Todo / Owner Review Reading Queue",
                "",
                "## Agent Todo",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_registry(
    path: Path, *, project: Path, state_file: Path, runtime_root: Path, provider: str,
) -> None:
    path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "auto_research_smoke",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_file),
                        "adapter": {
                            "kind": "auto_research_demo_local_queue",
                            "status": "connected",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [
                                CURATOR_AGENT_ID,
                                HYPOTHESIS_AGENT_ID,
                                EXECUTOR_AGENT_ID,
                                EVALUATOR_AGENT_ID,
                            ]
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    import_fixture_authority(path, GOAL_ID, provider=provider)


def append_real_fixture_evidence(*, registry: Path, runtime_root: Path, temp: Path) -> None:
    packet = build_auto_research_evidence_packet(
        contract=research_contract(goal_id=GOAL_ID),
        eval_results=[eval_result("dev"), eval_result("holdout")],
        hypothesis_id=HYPOTHESIS_ID,
        todo_id=EVIDENCE_TODO_ID,
        agent_id=EVIDENCE_AGENT_ID,
        claimed_by=EVIDENCE_AGENT_ID,
        mechanism_family=MECHANISM_FAMILY,
        hypothesis=HYPOTHESIS_TEXT,
        grounding_refs=[GROUNDING_REF],
        branch_ref="codex/auto-research-worker-turn-smoke",
    )
    packet_path = temp / "real-evidence-packet.public.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    appended = append_auto_research_rollout_events(
        packet_path=str(packet_path),
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        dry_run=False,
    )
    assert appended["appended_count"] == 3, appended
    assert_public_safe(appended)


def append_fixture_evidence(
    *,
    registry: Path,
    runtime_root: Path,
    temp: Path,
    hypothesis_id: str,
    todo_id: str,
    dev_metric: float,
    holdout_metric: float,
) -> None:
    packet = build_auto_research_evidence_packet(
        contract=research_contract(goal_id=GOAL_ID),
        eval_results=[
            eval_result("dev", value=dev_metric),
            eval_result("holdout", value=holdout_metric),
        ],
        hypothesis_id=hypothesis_id,
        todo_id=todo_id,
        agent_id=EVIDENCE_AGENT_ID,
        claimed_by=EVIDENCE_AGENT_ID,
        mechanism_family=MECHANISM_FAMILY,
        hypothesis=f"{HYPOTHESIS_TEXT} Variant {hypothesis_id}.",
        grounding_refs=[GROUNDING_REF],
        branch_ref="codex/auto-research-worker-turn-smoke",
    )
    packet_path = temp / f"{hypothesis_id}.public.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    appended = append_auto_research_rollout_events(
        packet_path=str(packet_path),
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        dry_run=False,
    )
    assert appended["appended_count"] == 3, appended
    assert_public_safe(appended)


def append_negative_fixture_evidence(
    *,
    registry: Path,
    runtime_root: Path,
    temp: Path,
    hypothesis_id: str,
    todo_id: str,
    failure_kind: str | None = None,
    measurement_scope: str | None = None,
    remediation_attempt: bool = False,
    eval_status: str = "scored",
    primary_metric_status: str = "regressed",
) -> None:
    result = eval_result("dev", value=0.75)
    result["eval_status"] = eval_status
    result["primary_metric_status"] = primary_metric_status
    if failure_kind:
        result["failure_kind"] = failure_kind
    if measurement_scope:
        result["measurement_scope"] = measurement_scope
    if remediation_attempt:
        result["remediation_attempt"] = True
    packet = build_auto_research_evidence_packet(
        contract=research_contract(goal_id=GOAL_ID),
        eval_results=[result],
        hypothesis_id=hypothesis_id,
        todo_id=todo_id,
        agent_id=EVIDENCE_AGENT_ID,
        claimed_by=EVIDENCE_AGENT_ID,
        mechanism_family=MECHANISM_FAMILY,
        hypothesis=f"{HYPOTHESIS_TEXT} Failure {hypothesis_id}.",
        grounding_refs=[GROUNDING_REF],
        branch_ref="codex/auto-research-worker-turn-smoke",
    )
    packet_path = temp / f"{hypothesis_id}.public.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    appended = append_auto_research_rollout_events(
        packet_path=str(packet_path),
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        dry_run=False,
    )
    assert appended["appended_count"] == 2, appended
    assert_public_safe(appended)


def assert_seeded_worker_turns() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        supervisor = build_auto_research_demo_supervisor_plan(
            goal_id=GOAL_ID,
            agent_specs=LANES,
            session_name="loopx-auto-research-worker-turn-smoke",
            cli_bin="loopx",
            codex_bin="codex",
            tmux_bin="tmux",
            reasoning_effort="high",
        )
        visible_control, registry, runtime_root = _seed_visible_demo_control_plane(
            demo_root=temp,
            goal_id=GOAL_ID,
            objective="Verify visible auto-research requires real role-authored evidence.",
            supervisor=supervisor,
        )
        seeded_todos = visible_control["seeded_todos"]
        executor_seed = next(item for item in seeded_todos if item["agent_id"] == EXECUTOR_AGENT_ID)
        evaluator_seed = next(item for item in seeded_todos if item["agent_id"] == EVALUATOR_AGENT_ID)
        assert evaluator_seed["resume_when"] == f"todo_done:{executor_seed['todo_id']}", visible_control
        workspace = temp / "visible-workspace"
        workspace.mkdir()

        curator_preview = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=CURATOR_AGENT_ID,
            execute=False,
        )
        assert curator_preview["mode"] == "dry_run", curator_preview
        assert curator_preview["selected_action"] == "write_research_contract", curator_preview

        curator_execute = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=CURATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert_manual_research_required(curator_execute, action="write_research_contract")

        executor_execute = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EXECUTOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert_no_action(executor_execute, agent_id=EXECUTOR_AGENT_ID)

        evaluator_execute = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=False,
        )
        assert_no_action(evaluator_execute, agent_id=EVALUATOR_AGENT_ID)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        supervisor = build_auto_research_demo_supervisor_plan(
            goal_id=GOAL_ID,
            agent_specs=LANES,
            session_name="loopx-auto-research-resume-lineage-smoke",
            cli_bin="loopx",
            codex_bin="codex",
            tmux_bin="tmux",
            reasoning_effort="high",
        )
        visible_control, registry, runtime_root = _seed_visible_demo_control_plane(
            demo_root=temp,
            goal_id=GOAL_ID,
            objective="Verify seeded evaluator summaries retain executor lineage.",
            supervisor=supervisor,
        )
        seeded_todos = visible_control["seeded_todos"]
        executor_seed = next(item for item in seeded_todos if item["agent_id"] == EXECUTOR_AGENT_ID)
        evaluator_seed = next(item for item in seeded_todos if item["agent_id"] == EVALUATOR_AGENT_ID)
        assert evaluator_seed["resume_when"] == f"todo_done:{executor_seed['todo_id']}", visible_control
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=Path(runtime_root),
            temp=temp,
            hypothesis_id="hyp_seeded_retry_exhausted",
            todo_id=executor_seed["todo_id"],
            failure_kind="retry_exhausted",
            eval_status="failed_to_run",
            primary_metric_status="inconclusive",
        )
        # This seeded Goal uses the ordinary canonical SQLite/hard_lease default.
        # Admit the exact executor lease before terminal writeback; a claim alone
        # is not execution authority under that contract.
        for prerequisite, successor in zip(seeded_todos[:2], seeded_todos[1:3]):
            lease_key = "seeded-prerequisite-" + str(prerequisite["action_kind"])
            lease = acquire_task_lease(
                registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
                todo_id=prerequisite["todo_id"], owner=prerequisite["agent_id"],
                idempotency_key=lease_key, ttl_seconds=600,
            )
            completed = complete_goal_todo(
                registry_path=registry, goal_id=GOAL_ID, todo_id=prerequisite["todo_id"],
                role="agent", claimed_by=prerequisite["agent_id"], agent_id=prerequisite["agent_id"],
                task_lease_idempotency_key=lease_key, task_lease_expected_version=lease["lease"]["version"],
                note="synthetic predecessor resolved for exhausted-retry lineage fixture",
                evidence="public-safe predecessor fixture; no research metric or result claimed",
                successor_todo_ids=[successor["todo_id"]], self_merged=True, dry_run=False,
            )
            assert completed["completed"] is True, completed
        executor_lease = acquire_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=executor_seed["todo_id"], owner=EXECUTOR_AGENT_ID,
            idempotency_key="seeded-executor-completion", ttl_seconds=600,
        )
        assert executor_lease["source_authority"] == "sqlite_v0", executor_lease
        executor_completion = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=executor_seed["todo_id"],
            role="agent",
            claimed_by=EXECUTOR_AGENT_ID,
            agent_id=EXECUTOR_AGENT_ID,
            task_lease_idempotency_key="seeded-executor-completion",
            task_lease_expected_version=executor_lease["lease"]["version"],
            note="lane-authored exhausted retry evidence was appended",
            evidence="public-safe evidence packet recorded retry exhaustion",
            no_followup=True,
            self_merged=True,
            dry_run=False,
        )
        assert executor_completion["completed"] is True, executor_completion
        workspace = temp / "visible-workspace"
        workspace.mkdir()

        evaluator_request = dict(
            registry_path=registry, runtime_root_arg=runtime_root, goal_id=GOAL_ID,
            agent_id=EVALUATOR_AGENT_ID, workspace=workspace, execute=True,
            complete_selected_todo=True,
        )
        external = acquire_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=evaluator_seed["todo_id"], owner=EVALUATOR_AGENT_ID,
            idempotency_key="external-summary-attempt", ttl_seconds=600,
        )
        try:
            run_auto_research_worker_turn(**evaluator_request)
        except TaskLeaseError as exc:
            assert exc.code == "todo_lease_conflict", exc
        else:
            raise AssertionError("a worker must not borrow another execution's lease")
        assert not (workspace / ".local").exists(), workspace
        retained = inspect_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=evaluator_seed["todo_id"],
        )
        assert retained["active"] is True and retained["lease"] == external["lease"], retained
        release_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=evaluator_seed["todo_id"], owner=EVALUATOR_AGENT_ID,
            idempotency_key="external-summary-attempt", expected_version=external["lease"]["version"],
        )
        # A real output failure must retire only this worker's admitted lease.
        (workspace / ".local").write_text("blocked output directory", encoding="utf-8")
        try:
            run_auto_research_worker_turn(**evaluator_request)
        except (FileExistsError, NotADirectoryError):
            pass
        else:
            raise AssertionError("the invalid output directory must fail before Todo completion")
        (workspace / ".local").unlink()
        failed_attempt = inspect_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=evaluator_seed["todo_id"],
        )
        assert failed_attempt["active"] is False, failed_attempt

        unfinished_summary = run_auto_research_worker_turn(
            **{**evaluator_request, "complete_selected_todo": False}
        )
        assert unfinished_summary["selected_todo_id"] == evaluator_seed["todo_id"], unfinished_summary
        assert unfinished_summary["executed"] is True, unfinished_summary
        assert unfinished_summary["completion"]["requested"] is False, unfinished_summary
        unfinished_lease = inspect_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=evaluator_seed["todo_id"],
        )
        assert unfinished_lease["active"] is False, unfinished_lease
        assert unfinished_lease["lease"]["idempotency_key"].startswith("auto-research-worker-"), unfinished_lease

        seeded_failure_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert seeded_failure_turn["selected_todo_id"] == evaluator_seed["todo_id"], seeded_failure_turn
        assert seeded_failure_turn["selected_action"] == "summarize_evidence", seeded_failure_turn
        assert (
            seeded_failure_turn["frontier"]["frontier"]["selected"]["resume_when"]
            == f"todo_done:{executor_seed['todo_id']}"
        ), seeded_failure_turn
        assert (
            seeded_failure_turn["evaluation_summary"]["failure_continuation"]["source_todo_id"]
            == executor_seed["todo_id"]
        ), seeded_failure_turn
        successors = seeded_failure_turn["successor_todos"]["successors"]
        assert len(successors) == 1, seeded_failure_turn
        assert successors[0]["action_kind"] == "propose_failure_successor", seeded_failure_turn
        assert successors[0]["unblocks_todo_id"] == executor_seed["todo_id"], seeded_failure_turn
        assert seeded_failure_turn["completion"]["successor_todo_ids"] == [
            successors[0]["todo_id"]
        ], seeded_failure_turn
        completed_attempt = inspect_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=evaluator_seed["todo_id"],
        )
        assert completed_attempt["active"] is False, completed_attempt
        assert_public_safe(seeded_failure_turn)

        manual_todo = add_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, role="agent",
            text="Author a real failure successor before further research execution.",
            task_class="advancement_task", action_kind="propose_failure_successor",
            claimed_by="research-curator", priority="P0", dry_run=False,
        )
        manual_successor = run_worker_turn(
            registry=registry, runtime_root=runtime_root, workspace=workspace,
            agent_id="research-curator", execute=True, complete=True,
        )
        assert_manual_research_required(manual_successor, action="propose_failure_successor")
        assert manual_successor["selected_todo_id"] == manual_todo["todo_id"], manual_successor
        manual_lease = inspect_task_lease(
            registry_path=registry, runtime_root=Path(runtime_root), goal_id=GOAL_ID,
            todo_id=manual_successor["selected_todo_id"],
        )
        assert manual_lease["lease"] is None, manual_lease



def assert_evidence_lineage(provider: str) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        summary_todo = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text=(
                "[P0-auto-research-live] Summarize visible held-out evidence and "
                "open the next research round."
            ),
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            dry_run=False,
        )
        summary_todo_id = summary_todo["todo_id"]
        append_real_fixture_evidence(registry=registry, runtime_root=runtime_root, temp=temp)
        workspace = project / "visible-workspace"
        workspace.mkdir()

        evaluator_summary = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert evaluator_summary["mode"] == "execute", evaluator_summary
        assert evaluator_summary["selected_action"] == "summarize_evidence", evaluator_summary
        successor_todos = evaluator_summary["successor_todos"]
        assert successor_todos["executed"] is True, successor_todos
        successor_ids = [
            successor["todo_id"]
            for successor in successor_todos["successors"]
            if successor.get("todo_id")
        ]
        assert len(successor_ids) == 1, successor_todos
        assert successor_todos["successors"][0]["claimed_by"] == CURATOR_AGENT_ID, successor_todos
        assert successor_todos["successors"][0]["action_kind"] == "review_research_contract", successor_todos
        completion = evaluator_summary["completion"]
        assert completion["executed"] is True, completion
        assert completion["successor_todo_ids"] == successor_ids, evaluator_summary
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={summary_todo_id} status=done" in state_text, state_text
        assert "no_followup=true" not in state_text, state_text
        for successor_id in successor_ids:
            assert successor_id in state_text, state_text
        assert_public_safe(evaluator_summary)
        canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID)
        by_id = {row["todo_id"]: row for row in canonical["todos"]}
        assert by_id[summary_todo_id]["status"] == "done", canonical
        assert by_id[summary_todo_id]["claimed_by"] == EVALUATOR_AGENT_ID
        assert by_id[successor_ids[0]]["claimed_by"] == CURATOR_AGENT_ID
        assert by_id[successor_ids[0]]["unblocks_todo_id"] == summary_todo_id

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        promotion_todo = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text=(
                "[P0-auto-research-live] Review promotion readiness and "
                "open the next research round when acceptance is not met."
            ),
            task_class="advancement_task",
            action_kind="review_promotion_readiness",
            claimed_by=EVALUATOR_AGENT_ID,
            dry_run=False,
        )
        promotion_todo_id = promotion_todo["todo_id"]
        append_real_fixture_evidence(registry=registry, runtime_root=runtime_root, temp=temp)
        workspace = project / "visible-workspace"
        workspace.mkdir()

        promotion_review = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert promotion_review["mode"] == "execute", promotion_review
        assert promotion_review["selected_action"] == "review_promotion_readiness", promotion_review
        assert promotion_review["evaluation_summary"]["holdout_improvement_count"] == 1, promotion_review
        successor_todos = promotion_review["successor_todos"]
        assert successor_todos["executed"] is True, successor_todos
        successor_ids = [
            successor["todo_id"]
            for successor in successor_todos["successors"]
            if successor.get("todo_id")
        ]
        assert len(successor_ids) == 1, successor_todos
        assert successor_todos["successors"][0]["claimed_by"] == CURATOR_AGENT_ID, successor_todos
        assert successor_todos["successors"][0]["action_kind"] == "review_research_contract", successor_todos
        completion = promotion_review["completion"]
        assert completion["executed"] is True, completion
        assert completion["successor_todo_ids"] == successor_ids, promotion_review
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={promotion_todo_id} status=done" in state_text, state_text
        assert "no_followup=true" not in state_text, state_text
        for successor_id in successor_ids:
            assert successor_id in state_text, state_text
        assert_public_safe(promotion_review)

        curator_handoff = apply_role_successor_todos(
            registry_path=registry,
            goal_id=GOAL_ID,
            source_todo_id=successor_ids[0],
            current_agent_id=CURATOR_AGENT_ID,
            role_id="research_curator",
            action="review_research_contract",
            successor_specs=auto_research_successor_specs_for_action(
                role_id="research_curator",
                action="review_research_contract",
            ),
            decision_summary={
                "validated_promotion_candidate_count": 1,
                "holdout_improvement_count": 1,
            },
            execute=True,
        )
        assert curator_handoff["executed"] is True, curator_handoff
        curator_successor_ids = [
            successor["todo_id"]
            for successor in curator_handoff["successors"]
            if successor.get("todo_id")
        ]
        assert len(curator_successor_ids) == 1, curator_handoff
        assert curator_handoff["successors"][0]["claimed_by"] == HYPOTHESIS_AGENT_ID, curator_handoff
        assert curator_handoff["successors"][0]["action_kind"] == "propose_hypothesis", curator_handoff
        curator_completion = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=successor_ids[0],
            role="agent",
            claimed_by=CURATOR_AGENT_ID,
            agent_id=CURATOR_AGENT_ID,
            note="auto-research curator review routed the next hypothesis round",
            evidence="state-summary agent=research-curator action=review_research_contract linked next hypothesis successor",
            successor_todo_ids=curator_successor_ids,
            self_merged=True,
            dry_run=False,
        )
        assert curator_completion["completed"] is True, curator_completion
        assert curator_completion["successor_todo_ids"] == curator_successor_ids, curator_completion
        state_text = state_file.read_text(encoding="utf-8")
        assert "no_followup=true" not in state_text, state_text
        for successor_id in curator_successor_ids:
            assert successor_id in state_text, state_text
        assert_public_safe(curator_handoff)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        first_failure_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate the first failed evidence branch.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        second_failure_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate the second failed evidence branch.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        unrelated_summary = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Summarize an independent evidence branch.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            dry_run=False,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_first_failure",
            todo_id=first_failure_evidence["todo_id"],
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_second_failure",
            todo_id=second_failure_evidence["todo_id"],
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        unrelated_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert unrelated_turn["evaluation_summary"]["failure_continuation"] is None, unrelated_turn
        assert unrelated_turn["successor_todos"]["successors"] == [], unrelated_turn
        assert unrelated_turn["completion"] == {
            "requested": True,
            "executed": False,
            "reason": "failure_successor_lineage_unresolved",
        }, unrelated_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={unrelated_summary['todo_id']} status=done" not in state_text, state_text
        assert "propose_failure_successor" not in state_text, state_text
        assert_public_safe(unrelated_turn)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        retry_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Retry one pending evidence branch.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        other_failure_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate a separately retired evidence branch.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        missing_match_summary = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Summarize the retry evidence branch.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            unblocks_todo_id=retry_evidence["todo_id"],
            dry_run=False,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_retry_pending",
            todo_id=retry_evidence["todo_id"],
            eval_status="failed_to_run",
            primary_metric_status="inconclusive",
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_other_retired",
            todo_id=other_failure_evidence["todo_id"],
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        missing_match_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert (
            missing_match_turn["frontier"]["frontier"]["completion"]["status"]
            == "failure_successor_required"
        ), missing_match_turn
        assert missing_match_turn["evaluation_summary"]["failure_continuation"] is None, (
            missing_match_turn
        )
        assert missing_match_turn["successor_todos"]["successors"] == [], missing_match_turn
        assert missing_match_turn["completion"] == {
            "requested": True,
            "executed": False,
            "reason": "failure_successor_lineage_unresolved",
        }, missing_match_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={missing_match_summary['todo_id']} status=done" not in state_text, state_text
        assert_public_safe(missing_match_turn)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        self_referential_summary = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Summarize a self-referential retired branch.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            dry_run=False,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_summary_id_failure",
            todo_id=self_referential_summary["todo_id"],
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        self_referential_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert (
            self_referential_turn["frontier"]["frontier"]["completion"]["status"]
            == "failure_successor_required"
        ), self_referential_turn
        assert self_referential_turn["evaluation_summary"]["failure_continuation"] is None, (
            self_referential_turn
        )
        assert self_referential_turn["successor_todos"]["successors"] == [], self_referential_turn
        assert self_referential_turn["completion"] == {
            "requested": True,
            "executed": False,
            "reason": "failure_successor_lineage_unresolved",
        }, self_referential_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert (
            f"todo_id={self_referential_summary['todo_id']} status=done" not in state_text
        ), state_text
        assert_public_safe(self_referential_turn)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        positive_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate the supported evidence branch.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        unrelated_failure_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate an unrelated failed evidence branch.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        resume_gate = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Complete an independent summary gate.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        positive_summary = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Summarize the supported evidence branch.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            unblocks_todo_id=positive_evidence["todo_id"],
            resume_when=f"todo_done:{resume_gate['todo_id']}",
            dry_run=False,
        )
        append_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_positive_branch",
            todo_id=positive_evidence["todo_id"],
            dev_metric=1.4,
            holdout_metric=1.5,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_unrelated_failure",
            todo_id=unrelated_failure_evidence["todo_id"],
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_resume_gate_failure",
            todo_id=resume_gate["todo_id"],
        )
        resume_gate_completion = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=resume_gate["todo_id"],
            role="agent",
            claimed_by=EXECUTOR_AGENT_ID,
            agent_id=EXECUTOR_AGENT_ID,
            note="independent gate completed for summary lineage priority coverage",
            evidence="public-safe gate evidence",
            no_followup=True,
            self_merged=True,
            dry_run=False,
        )
        assert resume_gate_completion["completed"] is True, resume_gate_completion
        workspace = project / "visible-workspace"
        workspace.mkdir()

        positive_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert positive_turn["evaluation_summary"]["failure_continuation"] is None, positive_turn
        assert (
            positive_turn["frontier"]["frontier"]["completion"]["status"]
            == "promotion_review_required"
        ), positive_turn
        assert (
            positive_turn["frontier"]["frontier"]["selected"]["unblocks_todo_id"]
            == positive_evidence["todo_id"]
        ), positive_turn
        assert (
            positive_turn["frontier"]["frontier"]["selected"]["resume_when"]
            == f"todo_done:{resume_gate['todo_id']}"
        ), positive_turn
        successors = positive_turn["successor_todos"]["successors"]
        assert len(successors) == 1, positive_turn
        assert successors[0]["claimed_by"] == CURATOR_AGENT_ID, positive_turn
        assert successors[0]["action_kind"] == "review_research_contract", positive_turn
        assert positive_turn["completion"]["successor_todo_ids"] == [successors[0]["todo_id"]], positive_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={positive_summary['todo_id']} status=done" in state_text, state_text
        assert_public_safe(positive_turn)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        failure_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate retired evidence.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        failure_review = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Summarize retired evidence.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            unblocks_todo_id=failure_evidence["todo_id"],
            dry_run=False,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_failure_successor",
            todo_id=failure_evidence["todo_id"],
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        failure_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        successors = failure_turn["successor_todos"]["successors"]
        assert len(successors) == 1, failure_turn
        assert successors[0]["claimed_by"] == HYPOTHESIS_AGENT_ID, failure_turn
        assert successors[0]["action_kind"] == "propose_failure_successor", failure_turn
        assert failure_turn["evaluation_summary"]["failure_continuation"]["monitor_allowed"] is False, failure_turn
        assert (
            failure_turn["evaluation_summary"]["failure_continuation"]["source_todo_id"]
            == failure_evidence["todo_id"]
        ), failure_turn
        assert (
            failure_turn["frontier"]["frontier"]["selected"]["unblocks_todo_id"]
            == failure_evidence["todo_id"]
        ), failure_turn
        assert successors[0]["unblocks_todo_id"] == failure_evidence["todo_id"], failure_turn
        assert failure_turn["completion"]["successor_todo_ids"] == [successors[0]["todo_id"]], failure_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={failure_review['todo_id']} status=done" in state_text, state_text
        assert "continuous_monitor" not in state_text, state_text
        assert_public_safe(failure_turn)

        proposer_successors = auto_research_successor_specs_for_action(
            role_id="hypothesis_proposer",
            action="propose_failure_successor",
        )
        assert proposer_successors == [], proposer_successors
        classified_failure_successors = auto_research_successor_specs_for_action(
            role_id="evaluator_promoter",
            action="classify_evidence",
        )
        assert {
            successor["action_kind"] for successor in classified_failure_successors
        } == {
            "remediate_data_measurement",
            "propose_failure_successor",
        }, classified_failure_successors

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        data_gap_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate the declared data gap.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        data_gap_review = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Summarize a declared data gap.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            unblocks_todo_id=data_gap_evidence["todo_id"],
            dry_run=False,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_data_gap_successor",
            todo_id=data_gap_evidence["todo_id"],
            failure_kind="data_or_measurement_gap",
            measurement_scope="adjusted_price_field",
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        data_gap_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        successors = data_gap_turn["successor_todos"]["successors"]
        assert len(successors) == 1, data_gap_turn
        assert successors[0]["claimed_by"] == EXECUTOR_AGENT_ID, data_gap_turn
        assert successors[0]["action_kind"] == "remediate_data_measurement", data_gap_turn
        assert data_gap_turn["evaluation_summary"]["failure_continuation"]["remediation_attempt_limit"] == 1, data_gap_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={data_gap_review['todo_id']} status=done" in state_text, state_text
        assert "continuous_monitor" not in state_text, state_text
        assert_public_safe(data_gap_turn)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        retry_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Retry the exhausted evidence branch.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        retry_review = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Summarize exhausted retry evidence.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            unblocks_todo_id=retry_evidence["todo_id"],
            dry_run=False,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_retry_exhausted",
            todo_id=retry_evidence["todo_id"],
            failure_kind="retry_exhausted",
            eval_status="failed_to_run",
            primary_metric_status="inconclusive",
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        retry_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        successors = retry_turn["successor_todos"]["successors"]
        assert len(successors) == 1, retry_turn
        assert successors[0]["claimed_by"] == HYPOTHESIS_AGENT_ID, retry_turn
        assert successors[0]["action_kind"] == "propose_failure_successor", retry_turn
        assert (
            retry_turn["evaluation_summary"]["failure_continuation"]["failure_kind"]
            == "retry_exhausted"
        ), retry_turn
        assert (
            retry_turn["evaluation_summary"]["failure_continuation"]["source_todo_id"]
            == retry_evidence["todo_id"]
        ), retry_turn
        assert successors[0]["unblocks_todo_id"] == retry_evidence["todo_id"], retry_turn
        assert retry_turn["completion"]["successor_todo_ids"] == [successors[0]["todo_id"]], retry_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={retry_review['todo_id']} status=done" in state_text, state_text
        assert_public_safe(retry_turn)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        generic_todo = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text=(
                "[P1] Fix auto-research completion projection after validated "
                "promotion and holdout evidence."
            ),
            task_class="advancement_task",
            claimed_by=EVALUATOR_AGENT_ID,
            dry_run=False,
        )
        generic_todo_id = generic_todo["todo_id"]
        append_real_fixture_evidence(registry=registry, runtime_root=runtime_root, temp=temp)
        workspace = project / "visible-workspace"
        workspace.mkdir()

        generic_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        assert generic_turn["mode"] == "unsupported_action", generic_turn
        assert generic_turn["selected_action"] == "advance_todo", generic_turn
        assert generic_turn["executed"] is False, generic_turn
        state_text = state_file.read_text(encoding="utf-8")
        assert f"todo_id={generic_todo_id} status=done" not in state_text, state_text
        assert "satisfied_generic_handoff_closed" not in json.dumps(generic_turn), generic_turn
        assert_public_safe(generic_turn)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        append_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_target_reached_first",
            todo_id="todo_auto_research_target_first",
            dev_metric=1.4,
            holdout_metric=1.5,
        )
        append_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_target_reached_second",
            todo_id="todo_auto_research_target_second",
            dev_metric=2.0,
            holdout_metric=2.1,
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        target_reached = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=False,
        )
        assert target_reached["mode"] == "quiet_completion", target_reached
        completion = target_reached["completion"]
        assert completion["status"] == "target_reached", target_reached
        assert completion["quiet_completion_allowed"] is True, target_reached
        assert completion["next_action"] == "quiet_completion", target_reached
        assert completion["holdout_improvement_count"] == 2, target_reached
        assert completion["required_holdout_improvement_count"] == 2, target_reached
        frontier_completion = target_reached["frontier"]["frontier"]["completion"]
        assert frontier_completion["status"] == "target_reached", target_reached
        assert target_reached["frontier"]["frontier"]["selected"] is None, target_reached
        assert_public_safe(target_reached)

    for historical_parent in (False, True):
        assert_failure_successor_lineage(provider, historical_parent)


def assert_failure_successor_lineage(provider: str, historical_parent: bool) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        project = temp / "project"
        project.mkdir()
        state_file = project / "ACTIVE_GOAL_STATE.md"
        registry = temp / "registry.json"
        runtime_root = temp / "runtime"
        write_summary_state(state_file)
        write_registry(registry, project=project, state_file=state_file, runtime_root=runtime_root, provider=provider)
        failure_evidence = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Evaluate a retired evidence branch for lineage dedup.",
            task_class="advancement_task",
            action_kind="run_dev_eval",
            claimed_by=EXECUTOR_AGENT_ID,
            dry_run=False,
        )
        first_summary = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] First summary of the retired evidence branch.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            unblocks_todo_id=failure_evidence["todo_id"],
            dry_run=False,
        )
        second_summary = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="[P0-auto-research-live] Second summary of the same retired evidence branch.",
            task_class="advancement_task",
            action_kind="summarize_evidence",
            claimed_by=EVALUATOR_AGENT_ID,
            unblocks_todo_id=failure_evidence["todo_id"],
            dry_run=False,
        )
        append_negative_fixture_evidence(
            registry=registry,
            runtime_root=runtime_root,
            temp=temp,
            hypothesis_id="hyp_lineage_dedup_failure",
            todo_id=failure_evidence["todo_id"],
        )
        workspace = project / "visible-workspace"
        workspace.mkdir()

        if historical_parent:
            # Seed the published pre-cutoff producer intent through native create;
            # its parent must survive upgrade even when another summary retries.
            historical = apply_role_successor_todos(registry_path=registry, goal_id=GOAL_ID,
                source_todo_id=first_summary['todo_id'], text_source_todo_id=failure_evidence['todo_id'],
                current_agent_id=EVALUATOR_AGENT_ID, role_id='evaluator_promoter',
                action='summarize_evidence', execute=True,
                successor_specs=[spec for spec in auto_research_successor_specs_for_action(
                    role_id='evaluator_promoter', action='summarize_evidence')
                    if spec['action_kind'] == 'propose_failure_successor'],
                decision_summary={'failure_continuation': {'next_outcome': 'propose_failure_successor'}})
            assert historical['successors'][0]['added'] is True
        original = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID)
        original_source = next(row for row in original['todos'] if row['todo_id'] == failure_evidence['todo_id'])

        first_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        first_successors = first_turn["successor_todos"]["successors"]
        assert len(first_successors) == 1, first_turn
        assert first_successors[0]["action_kind"] == "propose_failure_successor", first_turn
        assert first_successors[0]["claimed_by"] == HYPOTHESIS_AGENT_ID, first_turn
        first_successor_text = first_successors[0]["todo_command"]
        assert ".." not in first_successor_text, f"successor text contains ..: {first_successor_text!r}"
        assert_public_safe(first_turn)
        first_snapshot = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID)
        successor_id = first_successors[0]['todo_id']
        accepted = next(row for row in first_snapshot['todos'] if row['todo_id'] == successor_id)
        assert accepted['unblocks_todo_id'] == (first_summary['todo_id'] if historical_parent else failure_evidence['todo_id'])

        # An explicit attempt to rebind this existing successor remains illegal.
        # The producer fix must not weaken the canonical duplicate-intent fence.
        try:
            add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role='agent',
                text=accepted['text'], task_class='advancement_task',
                action_kind='propose_failure_successor', claimed_by=HYPOTHESIS_AGENT_ID,
                unblocks_todo_id=second_summary['todo_id'], dry_run=False)
        except LocalCoordinationAuthorityUnavailable as error:
            assert error.code == 'todo_semantic_duplicate_conflict', error
        else:
            raise AssertionError('same-title successor must not acquire another source binding')
        assert read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID) == first_snapshot
        if historical_parent:
            from loopx.control_plane.testing.canary_harness import run_json_cli
            run_json_cli('todo', 'archive-completed', '--goal-id', GOAL_ID, '--role', 'agent',
                '--max-active-done', '0', '--execute',
                registry_path=registry, runtime_root=runtime_root)

        if historical_parent:
            from unittest.mock import patch
            from demo.auto_research import worker_runtime
            observed_after_race = None

            def concurrent_read(**kwargs):
                nonlocal observed_after_race
                snapshot = read_canonical_todos_if_promoted(**kwargs)
                add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role='agent',
                    text='[P2] Independent concurrent planning work.', task_class='advancement_task',
                    claimed_by=CURATOR_AGENT_ID, dry_run=False)
                observed_after_race = read_canonical_todos_if_promoted(**kwargs)
                return snapshot

            # Intervene after a real read; both the update and CAS refusal use
            # the real provider. No mock supplies the claimed postcondition.
            with patch.object(worker_runtime, 'read_canonical_todos_if_promoted', concurrent_read):
                try:
                    run_auto_research_worker_turn(registry_path=registry,
                        runtime_root_arg=str(runtime_root), goal_id=GOAL_ID,
                        agent_id=EVALUATOR_AGENT_ID, workspace=workspace, execute=True,
                        complete_selected_todo=True, lane_count=4, visible_lanes_accepted=True)
                except LocalCoordinationAuthorityUnavailable as error:
                    assert error.code == 'provider_revision_mismatch', error
                else:
                    raise AssertionError('stale historical-lineage proof must refuse')
            assert read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID) == observed_after_race

        second_turn = run_worker_turn(
            registry=registry,
            runtime_root=runtime_root,
            workspace=workspace,
            agent_id=EVALUATOR_AGENT_ID,
            execute=True,
            complete=True,
        )
        second_successors = second_turn["successor_todos"]["successors"]
        assert len(second_successors) == 1, second_turn
        second_successor = second_successors[0]
        assert second_successor["already_exists"] is True, (
            f"second successor should be deduplicated, got: {second_successor}"
        )
        assert second_successor["todo_id"] == first_successors[0]["todo_id"], (
            f"second successor should have the same todo_id, got: {second_successor['todo_id']}"
        )
        assert_public_safe(second_turn)
        final = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=GOAL_ID)
        rows = {row['todo_id']: row for row in final['todos']}
        assert len(final['todos']) == len(original['todos']) + 1
        assert rows[successor_id] == accepted
        assert rows[failure_evidence['todo_id']] == original_source
        for summary in (first_summary, second_summary):
            stored = rows[summary['todo_id']]
            assert stored['status'] == 'done' and stored['claimed_by'] == EVALUATOR_AGENT_ID
            assert stored['successor_todo_ids'] == [successor_id]

def main() -> int:
    assert_seeded_worker_turns()
    for provider in ("file", "sqlite"):
        assert_evidence_lineage(provider)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
