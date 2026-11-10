"""End-to-end File + SQLite real-path CLI, Turn-context, and loopback API tests
for the 5-stage methodology operator catalog, scoped multi-point Yardstick
fidelity, budgeted continuation planning, and counterfactual pair evaluation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.capabilities.explore.turn_context import explore_turn_context
from loopx.presentation.explore_results_api import _result_rows
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    _initialize_fixture_authority,
    _run_cli,
    _write_fixture,
)


def _setup_goal(tmp_path: Path, *, enabled: bool = True, provider: str = "sqlite"):
    project, runtime, registry_path = _write_fixture(tmp_path)
    config = json.loads(registry_path.read_text(encoding="utf-8"))
    config["goals"][0]["explore_graph"] = {"enabled": True}
    config["goals"][0]["quota"]["allowed_slots"] = 8
    config["goals"][0].setdefault("execution_profile", {})["yardstick_enabled"] = enabled
    registry_path.write_text(json.dumps(config), encoding="utf-8")
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="soft_claim")
    return project, runtime, registry_path


def _profiles():
    auth = {
        "metric": "accepted_score",
        "unit": "points",
        "higher_is_better": True,
        "comparison_scope": "validation:fixed",
        "input_revision": "inputs-v1",
        "evaluation_ref": "evaluator:outcome-v1",
    }
    proxy = {
        "metric": "proxy_loss",
        "unit": "nats",
        "higher_is_better": False,
        "comparison_scope": "validation:fixed",
        "input_revision": "inputs-v1",
        "evaluation_ref": "evaluator:proxy-v1",
    }
    return auth, proxy


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_yardstick_fidelity_cli_turn_context_and_api_across_providers(tmp_path: Path, provider: str) -> None:
    project, runtime, registry_path = _setup_goal(tmp_path, enabled=True, provider=provider)
    auth, proxy = _profiles()

    decision = {
        "schema_version": "work_yardstick_decision_v0",
        "target": {
            "summary": "Maximize accepted score within fixed validation budget",
            "scope": "Fixed benchmark split and compute allowance",
            "acceptance_ref": "acceptance:task-v1",
        },
        "authoritative": auth,
        "proxy": proxy,
        "counterevidence_refs": [],
        "prior_finding_id": None,
        "next_action": {
            "method_id": "method:calibrated_search",
            "reason": "Track proxy concordance across multiple candidates and audit frontier reversals",
            "cost_bound": "Bounded confirmation evaluations within allowed quota",
        },
    }

    init_attach = tmp_path / "init_attach.json"
    init_attach.write_text(
        json.dumps(
            {
                "schema_version": "explore_result_attachment_v0",
                "question": "Does proxy_loss remain concordant near the top frontier?",
                "applicability": "Fixed split inputs-v1",
                "input_revision": "inputs-v1",
                "observation": "Initial baseline candidate c1 evaluated on both metrics",
                "interpretation": "Continue multi-point tracking",
                "status": "confirmed",
                "evidence_refs": ["ev:c1:a", "ev:c1:p"],
                "measurements": [
                    {**auth, "candidate_ref": "cand:c1", "evidence_ref": "ev:c1:a", "value": 20.0},
                    {**proxy, "candidate_ref": "cand:c1", "evidence_ref": "ev:c1:p", "value": 2.40},
                ],
                "work_yardstick_decision": decision,
            }
        ),
        encoding="utf-8",
    )

    rc, _ = _run_cli(
        registry_path, runtime,
        "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "turn-1",
        cwd=project,
    )
    assert rc == 0
    rc, settled = _run_cli(
        registry_path, runtime,
        "refresh-state",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "turn-1",
        "--classification", "validated_change",
        "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(init_attach),
        "--no-global-sync", "--suppress-external-sinks",
        cwd=project,
    )
    assert rc == 0, settled

    # Record 4 additional paired candidates + 1 proxy-only winner on the same linked question:
    # Bottom candidates (c1..c2) agree, top-3 authoritative frontier (c3..c5) reverses!
    candidates = [
        ("cand:c2", 35.0, 1.90),
        ("cand:c3", 72.0, 0.80),
        ("cand:c4", 81.0, 0.92),
        ("cand:c5", 93.0, 1.10),
    ]
    for idx, (cand_ref, a_val, p_val) in enumerate(candidates, start=2):
        res_path = tmp_path / f"cand_{idx}.json"
        res_path.write_text(
            json.dumps(
                {
                    "schema_version": "explore_result_attachment_v0",
                    "question": "Does proxy_loss remain concordant near the top frontier?",
                    "applicability": "Fixed split inputs-v1",
                    "input_revision": "inputs-v1",
                    "observation": f"Evaluated {cand_ref}",
                    "interpretation": "Recorded paired outcome and proxy",
                    "status": "confirmed",
                    "evidence_refs": [f"ev:{cand_ref}:a", f"ev:{cand_ref}:p"],
                    "measurements": [
                        {**auth, "candidate_ref": cand_ref, "evidence_ref": f"ev:{cand_ref}:a", "value": a_val},
                        {**proxy, "candidate_ref": cand_ref, "evidence_ref": f"ev:{cand_ref}:p", "value": p_val},
                    ],
                }
            ),
            encoding="utf-8",
        )
        rc, out = _run_cli(
            registry_path, runtime,
            "explore", "finding",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--todo-id", TODO_ID, "--turn-instance-id", f"turn-{idx}",
            "--result-json", str(res_path),
            cwd=project,
        )
        assert rc == 0, out

    proxy_star_path = tmp_path / "proxy_star.json"
    proxy_star_path.write_text(
        json.dumps(
            {
                "schema_version": "explore_result_attachment_v0",
                "question": "Does proxy_loss remain concordant near the top frontier?",
                "applicability": "Fixed split inputs-v1",
                "input_revision": "inputs-v1",
                "observation": "Proxy-screened candidate star",
                "interpretation": "Awaits authoritative confirmation",
                "status": "confirmed",
                "evidence_refs": ["ev:star:p"],
                "measurements": [
                    {**proxy, "candidate_ref": "cand:proxy_star", "evidence_ref": "ev:star:p", "value": 0.65},
                ],
            }
        ),
        encoding="utf-8",
    )
    rc, out = _run_cli(
        registry_path, runtime,
        "explore", "finding",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", "turn-star",
        "--result-json", str(proxy_star_path),
        cwd=project,
    )
    assert rc == 0, out

    # 1. CLI `explore yardstick-fidelity`
    rc, fidelity_packet = _run_cli(
        registry_path, runtime,
        "explore", "yardstick-fidelity",
        "--goal-id", GOAL_ID, "--frontier-k", "3",
        cwd=project,
    )
    assert rc == 0, fidelity_packet
    assert fidelity_packet["evaluation_count"] == 1
    eval_row = fidelity_packet["evaluations"][0]
    assert eval_row["fidelity_status"] == "frontier_bias_cliff"
    assert eval_row["counts"]["paired_candidates"] == 5
    assert eval_row["frontier_neighborhood"]["frontier_kendall_tau_b"] == -1
    assert eval_row["audit_candidates"]["unconfirmed_proxy_winners"][0]["candidate_ref"] == "cand:proxy_star"

    # 1b. CLI `explore evolutionary-plan` discovers declared Yardstick, selects elites, and quarantines proxy star
    rc, evo_packet = _run_cli(
        registry_path, runtime,
        "explore", "evolutionary-plan",
        "--goal-id", GOAL_ID, "--elite-limit", "3",
        cwd=project,
    )
    assert rc == 0, evo_packet
    assert evo_packet["plan_count"] == 1
    evo_row = evo_packet["plans"][0]
    assert evo_row["selection_status"] == "multi_island_evolution"
    assert evo_row["proxy_fidelity_status"] == "frontier_bias_cliff"
    assert evo_row["counts"]["selected_elites"] == 3
    assert evo_row["proxy_quarantine"][0]["candidate_ref"] == "cand:proxy_star"
    assert evo_row["proxy_quarantine"][0]["disposition"] == "quarantined_proxy_cliff"

    # 2. Compact & Node-Detail `explore_turn_context`
    compact_ctx = explore_turn_context(
        registry_path=registry_path, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
    )
    compact_cue = compact_ctx["graph"]["work_yardsticks"][0]
    assert compact_cue["fidelity_status"] == "frontier_bias_cliff"
    assert compact_cue["paired_candidates"] == 5

    detail_ctx = explore_turn_context(
        registry_path=registry_path, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
        result_node=compact_cue["node_id"],
    )
    detail_cue = detail_ctx["graph"]["work_yardsticks"][0]
    assert detail_cue["yardstick_fidelity"]["fidelity_status"] == "frontier_bias_cliff"

    # 3. Loopback API `_result_rows` enriches `work_yardstick` findings with `yardstick_fidelity`
    api_rows = _result_rows(runtime, GOAL_ID, registry_path)
    yardstick_rows = [r for r in api_rows if "work_yardstick" in r]
    assert len(yardstick_rows) == 1
    assert yardstick_rows[0]["yardstick_fidelity"]["fidelity_status"] == "frontier_bias_cliff"

    # 4. Feature-off rollback parity: disabling `yardstick_enabled` removes runtime cues cleanly
    config = json.loads(registry_path.read_text(encoding="utf-8"))
    config["goals"][0]["execution_profile"]["yardstick_enabled"] = False
    registry_path.write_text(json.dumps(config), encoding="utf-8")
    disabled_ctx = explore_turn_context(
        registry_path=registry_path, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID,
    )
    assert "work_yardsticks" not in disabled_ctx["graph"]
    assert "work_yardstick_capture" not in disabled_ctx["graph"]


def test_stateless_planning_cli_commands_end_to_end(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "LOOPX_USAGE_PING": "0"}

    def invoke(*args: str, expect_rc: int = 0) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [sys.executable, "-m", "loopx.cli", *args],
            cwd=repo,
            text=True,
            capture_output=True,
            env=env,
            timeout=45,
        )
        assert proc.returncode == expect_rc, proc.stdout + proc.stderr
        return proc

    # 1. `explore methodology-operators` (JSON + Markdown)
    cat_json = json.loads(invoke("explore", "methodology-operators", "--format", "json").stdout)
    assert cat_json["ok"] is True
    assert cat_json["schema_version"] == "stage_methodology_operator_catalog_v0"
    assert len(cat_json["operators"]) == 6
    cat_md = invoke("explore", "methodology-operators", "--stage", "feedback_evolve", "--format", "markdown").stdout
    assert "5-Stage Methodology Operator Catalog" in cat_md
    assert "budgeted_continuation" in cat_md
    assert "evolutionary_population" in cat_md

    # 2. `explore budgeted-plan`
    plan_doc = tmp_path / "budgeted_plan.json"
    plan_doc.write_text(
        json.dumps(
            {
                "remaining_budget": {"wall_minutes": 120, "token_budget": None, "authoritative_eval_slots": 1},
                "confirmation": {"eval_duration_minutes": 30, "cooldown_minutes": 10},
                "incumbent": {"candidate_ref": "cand:inc", "value": 0.5, "higher_is_better": True, "metric": "acc", "unit": "ratio"},
                "baseline_route": {
                    "route_id": "route:base",
                    "summary": "Continue local step",
                    "probe_minutes": 0,
                    "implementation_minutes": 20,
                    "expected_tokens": 10000,
                    "requires_authoritative_eval": True,
                    "expected_gain_min": 0.01,
                    "expected_gain_expected": 0.02,
                    "expected_gain_max": 0.03,
                },
                "candidate_routes": [
                    {
                        "route_id": "route:probe_switch",
                        "summary": "Probe new kernel",
                        "kind": "switch_mechanism",
                        "probe_minutes": 15,
                        "implementation_minutes": 35,
                        "expected_tokens": None,
                        "requires_authoritative_eval": True,
                        "expected_gain_min": 0.0,
                        "expected_gain_expected": 0.08,
                        "expected_gain_max": 0.12,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan_out = json.loads(invoke("explore", "budgeted-plan", "--document", str(plan_doc), "--format", "json").stdout)
    assert plan_out["ok"] is True
    assert plan_out["candidate_routes"][0]["recommended_disposition"] == "probe_then_decide"
    assert plan_out["candidate_routes"][0]["total_expected_tokens"] is None

    # 3. `explore counterfactual-eval` (strict failure vs. audit mode)
    sha = "d" * 64
    cf_doc = tmp_path / "cf_eval.json"
    cf_doc.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "pair_id": "pair:clean",
                        "task_id": "task:1",
                        "checkpoint": {
                            "checkpoint_id": "ckpt:1",
                            "source_state_sha256": sha,
                            "input_revision": "rev-1",
                            "evaluation_ref": "eval:v1",
                            "metric": "score",
                            "unit": "pts",
                            "higher_is_better": True,
                        },
                        "treatment": {
                            "arm_id": "arm:treat",
                            "checkpoint_id": "ckpt:1",
                            "source_state_sha256": sha,
                            "input_revision": "rev-1",
                            "evaluation_ref": "eval:v1",
                            "workspace_isolation_id": "ws:treat",
                            "first_action_signature": "act:a",
                            "terminal_value": 88.0,
                            "valid_submission": True,
                            "costs": {"wall_seconds": 600, "total_tokens": 50000},
                        },
                        "control": {
                            "arm_id": "arm:ctrl",
                            "checkpoint_id": "ckpt:1",
                            "source_state_sha256": sha,
                            "input_revision": "rev-1",
                            "evaluation_ref": "eval:v1",
                            "workspace_isolation_id": "ws:ctrl",
                            "first_action_signature": "act:b",
                            "terminal_value": 75.0,
                            "valid_submission": True,
                            "costs": {"wall_seconds": 580, "total_tokens": 48000},
                        },
                    },
                    {
                        "pair_id": "pair:shared_ws",
                        "task_id": "task:2",
                        "checkpoint": {
                            "checkpoint_id": "ckpt:2",
                            "source_state_sha256": sha,
                            "input_revision": "rev-1",
                            "evaluation_ref": "eval:v1",
                            "metric": "score",
                            "unit": "pts",
                            "higher_is_better": True,
                        },
                        "treatment": {
                            "arm_id": "arm:treat",
                            "checkpoint_id": "ckpt:2",
                            "source_state_sha256": sha,
                            "input_revision": "rev-1",
                            "evaluation_ref": "eval:v1",
                            "workspace_isolation_id": "ws:SHARED",
                            "terminal_value": 99.0,
                            "valid_submission": True,
                            "costs": {},
                        },
                        "control": {
                            "arm_id": "arm:ctrl",
                            "checkpoint_id": "ckpt:2",
                            "source_state_sha256": sha,
                            "input_revision": "rev-1",
                            "evaluation_ref": "eval:v1",
                            "workspace_isolation_id": "ws:SHARED",
                            "terminal_value": 70.0,
                            "valid_submission": True,
                            "costs": {},
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    failed = json.loads(
        invoke("explore", "counterfactual-eval", "--document", str(cf_doc), "--format", "json", expect_rc=1).stdout
    )
    assert failed["ok"] is False

    audited = json.loads(
        invoke(
            "explore", "counterfactual-eval",
            "--document", str(cf_doc),
            "--no-strict-identification",
            "--format", "json",
        ).stdout
    )
    assert audited["ok"] is True
    assert audited["counts"]["valid_pairs"] == 1
    assert audited["counts"]["contaminated_pairs"] == 1
    assert audited["estimand_summary"]["mean_tau_b"] == 13.0
