"""One recorded work sequence, evaluated with and without the sentinel.

The same Goal, the same real `refresh-state` runs and the same cosmetic file
churn are evaluated twice: with the default policy (off) the core sees nothing
because every round self-reports `advanced`; with `assist` the two typed drift
receipts become the existing autonomous replan obligation, an acknowledged
replan re-arms it, and `loopx status` shows the receipts. No model is called:
the observer answers are injected, so this pins the integration, not the model.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from loopx.configure_goal import configure_goal
from loopx.control_plane.work_items.external_progress_review import (
    EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND,
)
from loopx.control_plane.work_items.progress_observation import (
    typed_progress_repeat_trigger,
)
from loopx.history import load_index, load_registry
from loopx.state_refresh import refresh_state_run
from loopx.status import (
    autonomous_replan_obligation_from_runs,
    external_progress_review_context,
)
from loopx_jev import drift
from loopx_jev.store import atomic_json
from drift_fixtures import DRIFT_NOULS, response
from test_drift import git
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    _initialize_fixture_authority,
    _write_fixture,
)

SOURCE = Path(__file__).resolve().parents[3]


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(SOURCE / "packages/loopx-jev/src"), str(SOURCE)]),
        "LOOPX_GLOBAL_REGISTRY": str(tmp_path / "global.json"),
    }


def _cli(env: dict[str, str], *args: str) -> tuple[dict, dict | None]:
    process = subprocess.run(
        [sys.executable, "-m", "loopx_jev", *args],
        cwd=SOURCE,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert process.returncode == 0, process.stderr[-2000:]
    diagnostic = None
    for line in process.stderr.splitlines():
        if line.startswith('{"jev_drift"'):
            diagnostic = json.loads(line)["jev_drift"]
    return json.loads(process.stdout), diagnostic


def _newest_first_runs(runtime: Path) -> list[dict]:
    records, _ = load_index(runtime / "goals" / GOAL_ID / "runs" / "index.jsonl")
    return sorted(records, key=lambda row: str(row.get("generated_at") or ""), reverse=True)


def _goal(registry: Path) -> dict:
    return next(goal for goal in load_registry(registry)["goals"] if goal["id"] == GOAL_ID)


def _find(payload, key: str):
    if isinstance(payload, dict):
        if key in payload:
            yield payload[key]
        for value in payload.values():
            yield from _find(value, key)
    elif isinstance(payload, list):
        for item in payload:
            yield from _find(item, key)


@pytest.fixture
def sequence(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path / "fixture")
    _initialize_fixture_authority(project, runtime)
    work = tmp_path / "delivery"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "config", "user.name", "Fixture")
    git(work, "config", "user.email", "fixture@example.invalid")
    (work / "retry.py").write_text("DEFAULT_DELAY = 1\n\n\ndef deliver(send, payload):\n    return send(payload)\n")
    git(work, "add", "retry.py")
    git(work, "commit", "-qm", "baseline")
    config = tmp_path / "config.json"
    atomic_json(
        config,
        {
            "schema_version": "loopx_jev_drift_config_v0",
            "mode": "shadow",
            "scenarios": ["progress_review"],
            "model": "fixture-v1",
            "allow_egress": True,
        },
    )
    basis = tmp_path / "basis.json"
    atomic_json(
        basis,
        {
            "goal_id": GOAL_ID,
            "objective": "Make deliver() retry one transient TimeoutError",
            "acceptance": ["One TimeoutError is retried once", "ValueError is not retried"],
            "evidence": [],
        },
    )
    state = tmp_path / "observer"
    env = _env(tmp_path)
    created, _ = _cli(
        env,
        "drift", "init", "--state-dir", str(state), "--config", str(config),
        "--workspace", str(work), "--basis", str(basis), "--runtime-root", str(runtime),
        "--path", "retry.py",
    )
    assert created["receipts"] == "goal_runtime"
    assert len(created["contract_revision"]) == 64
    return project, runtime, registry, work, config, state, env, created["contract_revision"]


def _cosmetic_round(work: Path, config: Path, state: Path, env: dict[str, str], registry: Path, runtime: Path, project: Path, number: int) -> None:
    names = ["DEFAULT_DELAY", "BASE_DELAY", "INITIAL_DELAY", "START_DELAY"]
    (work / "retry.py").write_text(f"{names[number]} = 1\n\n\ndef deliver(send, payload):\n    return send(payload)\n")
    time.sleep(1.05)  # distinct generated_at seconds for the fallback run identity
    _, diagnostic = _cli(
        env,
        "drift", "refresh", "--state-dir", str(state), "--config", str(config), "--",
        "--registry", str(registry), "--runtime-root", str(runtime), "refresh-state",
        "--goal-id", GOAL_ID, "--format", "json", "--no-global-sync", "--suppress-external-sinks",
        "--agent-id", AGENT_ID, "--progress-result-class", "advanced",
        "--progress-hypothesis-id", f"hypothesis-{number}", "--progress-surface-id", "retry",
        "--progress-evidence-id", f"evidence-{number}",
    )
    assert diagnostic is not None and diagnostic["status"] == "queued", diagnostic


def _drain_with_drift(state: Path, config: Path) -> None:
    def send(request, config_, key):
        return {"response": response(request, ["off_goal", "no_new_evidence"], nouls=DRIFT_NOULS)}

    drift.drain(state, config, transport=send, credential=lambda: "fixture")


def test_same_sequence_off_sees_nothing_and_assist_raises_the_obligation(sequence):
    project, runtime, registry, work, config, state, env, revision = sequence
    for number in (1, 2):
        _cosmetic_round(work, config, state, env, registry, runtime, project, number)
    _drain_with_drift(state, config)
    view = drift.status(state)
    assert view["receipts_written"] == 2
    runs = _newest_first_runs(runtime)
    assert [run["progress_observation"]["result_class"] for run in runs[:2]] == ["advanced", "advanced"]

    # Without the sentinel: the typed fuse cannot fire on self-declared advancement,
    # the default policy loads nothing, and no obligation exists.
    assert typed_progress_repeat_trigger(runs, agent_id=AGENT_ID) is None
    assert external_progress_review_context(_goal(registry), runtime) is None
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None) is None

    # Shadow: receipts are visible, still no obligation.
    configure_goal(registry_path=registry, goal_id=GOAL_ID, progress_review_mode="shadow", execute=True)
    shadow = external_progress_review_context(_goal(registry), runtime)
    assert shadow is not None and shadow["summary"]["receipt_count"] == 2
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=shadow) is None

    # Assist without a pinned goal contract is blocked and says so.
    configure_goal(
        registry_path=registry, goal_id=GOAL_ID, progress_review_mode="assist",
        progress_review_drift_threshold=2, execute=True,
    )
    unpinned = external_progress_review_context(_goal(registry), runtime)
    assert unpinned is not None and unpinned["summary"]["assist_blocked_reason"] == "contract_revision_unpinned"
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=unpinned) is None

    # Assist pinned to this observer's basis: the same two receipts become the existing obligation contract.
    configure_goal(
        registry_path=registry, goal_id=GOAL_ID, progress_review_contract_revision=revision, execute=True,
    )
    assist = external_progress_review_context(_goal(registry), runtime)
    assert assist is not None and assist["summary"]["stale_receipts"] == 0
    obligation = autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=assist)
    assert obligation is not None
    assert obligation["required"] is True
    assert obligation["triggers"][0]["kind"] == EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND
    assert obligation["triggers"][0]["run_count"] == 2
    assert obligation["frontier_identity"].startswith("progress_review:")
    assert obligation["stop_condition"]

    # The same contract is what `loopx status` publishes for the Goal.
    process = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime),
         "--format", "json", "status", "--goal-id", GOAL_ID],
        cwd=SOURCE, env=env, capture_output=True, text=True, timeout=180,
    )
    assert process.returncode == 0, process.stderr[-2000:]
    payload = json.loads(process.stdout)
    summaries = [item for item in _find(payload, "external_progress_review") if isinstance(item, dict) and "receipt_count" in item]
    assert summaries and summaries[0]["receipt_count"] == 2 and summaries[0]["mode"] == "assist"
    kinds = {
        trigger.get("kind")
        for obligation_view in _find(payload, "autonomous_replan_obligation")
        if isinstance(obligation_view, dict)
        for trigger in obligation_view.get("triggers") or []
        if isinstance(trigger, dict)
    }
    assert EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND in kinds

    # Repeating the evaluated observation, or the same hypothesis with fresh
    # evidence ids, is not a discharge: the obligation binds that window's typed
    # baseline and the real writeback rejects both.
    for non_novel, message in (
        ({"schema_version": "typed_progress_observation_v0", "result_class": "advanced", "surface_id": "retry", "hypothesis_id": "hypothesis-2", "evidence_ids": ["evidence-2"]}, "already claimed in the obligation window"),
        ({"schema_version": "typed_progress_observation_v0", "result_class": "advanced", "surface_id": "retry", "hypothesis_id": "hypothesis-2", "evidence_ids": ["evidence-fresh"]}, "typed semantic delta"),
    ):
        time.sleep(1.05)
        with pytest.raises(ValueError, match=message):
            refresh_state_run(
                registry_path=registry,
                runtime_root_override=str(runtime),
                goal_id=GOAL_ID,
                project=project,
                state_file=None,
                classification="state_refreshed",
                recommended_action="Repeat the same slice.",
                delivery_batch_scale="single_surface",
                delivery_outcome="surface_only",
                agent_id=AGENT_ID,
                autonomous_replan_recorded=True,
                repair_delta_kinds=["blocker"],
                progress_observation=non_novel,
                dry_run=False,
                sync_global=False,
            )
    # Renaming the hypothesis over the evaluated evidence ids is exactly the
    # pattern the review found; the external-review outcome policy refuses it.
    time.sleep(1.05)
    with pytest.raises(ValueError, match="evidence ids absent from the evaluated baseline"):
        refresh_state_run(
            registry_path=registry,
            runtime_root_override=str(runtime),
            goal_id=GOAL_ID,
            project=project,
            state_file=None,
            classification="state_refreshed",
            recommended_action="Rename the hypothesis.",
            delivery_batch_scale="single_surface",
            delivery_outcome="surface_only",
            agent_id=AGENT_ID,
            autonomous_replan_recorded=True,
            repair_delta_kinds=["blocker"],
            progress_observation={"schema_version": "typed_progress_observation_v0", "result_class": "advanced", "surface_id": "retry", "hypothesis_id": "hypothesis-renamed", "evidence_ids": ["evidence-2"]},
            dry_run=False,
            sync_global=False,
        )
    # Replaying the first round's complete claim looks novel against the
    # newest baseline alone; the obligation carries the whole window, so the
    # real writeback refuses it too.
    time.sleep(1.05)
    with pytest.raises(ValueError, match="already claimed in the obligation window"):
        refresh_state_run(
            registry_path=registry,
            runtime_root_override=str(runtime),
            goal_id=GOAL_ID,
            project=project,
            state_file=None,
            classification="state_refreshed",
            recommended_action="Return to the first slice.",
            delivery_batch_scale="single_surface",
            delivery_outcome="surface_only",
            agent_id=AGENT_ID,
            autonomous_replan_recorded=True,
            repair_delta_kinds=["blocker"],
            progress_observation={"schema_version": "typed_progress_observation_v0", "result_class": "advanced", "surface_id": "retry", "hypothesis_id": "hypothesis-1", "evidence_ids": ["evidence-1"]},
            dry_run=False,
            sync_global=False,
        )
    runs = _newest_first_runs(runtime)
    assist = external_progress_review_context(_goal(registry), runtime)
    still_open = autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=assist)
    assert still_open is not None and still_open["progress_baseline"]["hypothesis_id"] == "hypothesis-2"
    assert [item["hypothesis_id"] for item in still_open["progress_window"]] == ["hypothesis-2", "hypothesis-1"]

    # An acknowledged bounded replan with a genuinely new blocker re-arms the trigger; one more drift round is not enough.
    time.sleep(1.05)
    acked = refresh_state_run(
        registry_path=registry,
        runtime_root_override=str(runtime),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="state_refreshed",
        recommended_action="Select a behaviour-changing slice for the retry acceptance.",
        delivery_batch_scale="single_surface",
        delivery_outcome="surface_only",
        agent_id=AGENT_ID,
        autonomous_replan_recorded=True,
        repair_delta_kinds=["blocker"],
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "result_class": "blocked",
            "blocker_id": "blocker-cosmetic-churn",
            "evidence_ids": ["evidence-progress-review-obligation"],
        },
        dry_run=False,
        sync_global=False,
    )
    assert acked.get("ok") is True
    runs = _newest_first_runs(runtime)
    assert runs[0].get("autonomous_replan_ack", {}).get("recorded") is True
    assist = external_progress_review_context(_goal(registry), runtime)
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=assist) is None
    _cosmetic_round(work, config, state, env, registry, runtime, project, 3)
    _drain_with_drift(state, config)
    runs = _newest_first_runs(runtime)
    assist = external_progress_review_context(_goal(registry), runtime)
    assert assist is not None and assist["summary"]["receipt_count"] == 3
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=assist) is None


def test_on_goal_receipts_never_raise_an_obligation_in_assist(sequence):
    project, runtime, registry, work, config, state, env, revision = sequence
    for number in (1, 2):
        _cosmetic_round(work, config, state, env, registry, runtime, project, number)

    def send(request, config_, key):
        return {"response": response(request, ["on_goal", "new_evidence"])}

    drift.drain(state, config, transport=send, credential=lambda: "fixture")
    configure_goal(
        registry_path=registry, goal_id=GOAL_ID, progress_review_mode="assist",
        progress_review_contract_revision=revision, execute=True,
    )
    runs = _newest_first_runs(runtime)
    context = external_progress_review_context(_goal(registry), runtime)
    assert context is not None and context["summary"]["drift_counts"] == {"noul": 0, "choice": 0}
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=context) is None


def _refresh(project, runtime, registry, **overrides):
    options = dict(
        registry_path=registry,
        runtime_root_override=str(runtime),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="state_refreshed",
        recommended_action="Continue the retry slice.",
        delivery_batch_scale="single_surface",
        delivery_outcome="surface_only",
        agent_id=AGENT_ID,
        dry_run=False,
        sync_global=False,
    )
    options.update(overrides)
    return refresh_state_run(**options)


def test_retried_turn_claim_is_rejected_by_real_refresh_writeback(tmp_path):
    """A later untyped retry cannot erase an earlier claim of the same Turn."""

    from loopx.capabilities.progress_review.receipt import write_progress_review_receipt
    from tests.control_plane.test_external_progress_review import receipt, run

    project, runtime, registry = _write_fixture(tmp_path / "retry-fixture")
    _initialize_fixture_authority(project, runtime)
    first = run(1, turn="t1", agent=AGENT_ID)
    typed_retry = run(2, turn="t2", agent=AGENT_ID)
    untyped_retry = run(3, turn="t2", agent=AGENT_ID)
    untyped_retry.pop("progress_observation")
    first["progress_observation"] = {
        "schema_version": "typed_progress_observation_v0", "result_class": "blocked",
        "blocker_id": "blocker-already-recorded", "evidence_ids": ["evidence-already-recorded"],
    }
    typed_retry["progress_observation"]["surface_id"] = "retry"
    typed_retry["progress_observation"]["evidence_ids"] = ["evidence-2"]
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("".join(json.dumps(row) + "\n" for row in (first, typed_retry, untyped_retry)))
    revision = hashlib.sha256(b"contract-1").hexdigest()
    for sequence in (1, 2):
        write_progress_review_receipt(runtime, GOAL_ID, {
            **receipt(sequence, turn=f"t{sequence}", agent=AGENT_ID),
            "schema_version": "progress_review_receipt_v0",
            "signal_rule_version": "progress_review_signal_rule_v1",
            "goal_id": GOAL_ID,
            "question_version": "scoped-progress-sentinel-v2",
            "model": "fixture-v1",
            "judgments": {
                "choice": {"relation": "off_goal", "increment": "no_new_evidence"},
                "noul": {"behavior_change": 0.05, "serves_acceptance": 0.04, "evidence_increment": 0.1},
            },
            "label_probability_threshold": 0.6,
            "recorded_at": float(sequence),
        })
    configure_goal(
        registry_path=registry, goal_id=GOAL_ID, progress_review_mode="assist",
        progress_review_drift_threshold=2, progress_review_contract_revision=revision,
        execute=True,
    )
    context = external_progress_review_context(_goal(registry), runtime)
    obligation = autonomous_replan_obligation_from_runs(
        _newest_first_runs(runtime), agent_todos=None, external_progress_review=context,
    )
    assert obligation is not None and obligation["required"] is True
    assert [claim["result_class"] for claim in obligation["progress_window"]] == ["advanced", "blocked"]
    before = len(_newest_first_runs(runtime))
    with pytest.raises(ValueError, match="already claimed in the obligation window"):
        _refresh(
            project, runtime, registry, autonomous_replan_recorded=True,
            repair_delta_kinds=["blocker"],
            progress_observation=typed_retry["progress_observation"],
        )
    assert len(_newest_first_runs(runtime)) == before
    # The older blocker is also in the same window. It is not a new blocker
    # merely because the latest baseline is an advanced claim.
    with pytest.raises(ValueError, match="already claimed in the obligation window"):
        _refresh(
            project, runtime, registry, autonomous_replan_recorded=True,
            repair_delta_kinds=["blocker"],
            progress_observation=first["progress_observation"],
        )
    assert len(_newest_first_runs(runtime)) == before
    assert _refresh(
        project, runtime, registry, autonomous_replan_recorded=True,
        repair_delta_kinds=["blocker"],
        progress_observation={
            **typed_retry["progress_observation"], "hypothesis_id": "hypothesis-new",
            "evidence_ids": ["evidence-new"],
        },
    )["ok"] is True
    assert _newest_first_runs(runtime)[0]["autonomous_replan_ack"]["recorded"] is True


def test_outstanding_evaluations_keep_the_obligation_and_an_evidence_linked_vision_discharges_it(sequence):
    """Formation needs verdicts; persistence does not. Only evidence ends it."""

    project, runtime, registry, work, config, state, env, revision = sequence
    # The Agent already holds a vision before any drift, as a real Goal would.
    _refresh(project, runtime, registry, recommended_action="Start the retry slice.", agent_vision_packet={
        "vision_patch": {"acceptance_summary": "deliver() retries one transient TimeoutError", "replan_trigger_summary": "No behaviour change on retry.py"},
    })
    for number in (1, 2):
        _cosmetic_round(work, config, state, env, registry, runtime, project, number)
    _drain_with_drift(state, config)
    # A third transition is captured while the policy is still off, so no guard
    # applies and its evaluation is outstanding when the owner turns assist on.
    _cosmetic_round(work, config, state, env, registry, runtime, project, 3)
    configure_goal(
        registry_path=registry, goal_id=GOAL_ID, progress_review_mode="assist",
        progress_review_drift_threshold=2, progress_review_contract_revision=revision, execute=True,
    )
    runs = _newest_first_runs(runtime)
    assist = external_progress_review_context(_goal(registry), runtime)
    assert assist is not None and assist["summary"]["pending_receipts"] == 1
    open_obligation = autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=assist)
    assert open_obligation is not None
    review = open_obligation["external_progress_review"]
    assert review["run_count"] == 2 and review["unevaluated_transitions"]["by_reason"] == {"pending": 1}
    # The baseline is the newest typed claim, the still-pending third round.
    assert open_obligation["progress_baseline"]["hypothesis_id"] == "hypothesis-3"
    # The writeback owner sees the same obligation: re-submitting the pending
    # claim, or renaming its hypothesis over its evidence ids, is refused.
    for observation, message in (
        ({"hypothesis_id": "hypothesis-3", "evidence_ids": ["evidence-3"]}, "already claimed in the obligation window"),
        ({"hypothesis_id": "hypothesis-4", "evidence_ids": ["evidence-3"]}, "evidence ids absent from the evaluated baseline"),
    ):
        time.sleep(1.05)
        with pytest.raises(ValueError, match=message):
            _refresh(project, runtime, registry, autonomous_replan_recorded=True, repair_delta_kinds=["blocker"], progress_observation={
                "schema_version": "typed_progress_observation_v0", "result_class": "advanced", "surface_id": "retry", **observation,
            })
    # The outstanding evaluation fails closed: still no verdict, still open.
    calls = {"n": 0}

    def failing(request, config_, key):
        calls["n"] += 1
        return {"response": {"model": request["model"], "answers": {}}}

    drift.drain(state, config, transport=failing, credential=lambda: "fixture")
    assert calls["n"] == 1
    runs = _newest_first_runs(runtime)
    assist = external_progress_review_context(_goal(registry), runtime)
    assert assist is not None and assist["summary"]["status_counts"].get("failed") == 1
    still_open = autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=assist)
    assert still_open is not None
    assert still_open["external_progress_review"]["unevaluated_transitions"]["by_reason"] == {"failed": 1}
    # Reviewing and keeping the plan on evidence is a legal exit: the fresh
    # evidence-linked vision path discharges the obligation and re-arms it.
    time.sleep(1.05)
    kept = _refresh(project, runtime, registry, autonomous_replan_recorded=True, repair_delta_kinds=["blocker"],
        recommended_action="Keep the retry slice; implement the TimeoutError retry next.",
        agent_vision_packet={
            "agent_id": AGENT_ID,
            "state": "active",
            # `continue` keeps the acceptance as seeded; changing it would be a
            # durable replan and the vision prepare gate demands `outcome=replan`.
            "vision_patch": {"acceptance_summary": "deliver() retries one transient TimeoutError"},
            "path_delta": {
                "schema_version": "goal_path_delta_v0",
                "outcome": "continue",
                "prior_assumption": "Renaming the delay constant prepared the retry slice.",
                "observed_reality": "The evaluated deltas changed no behaviour; the retry branch is still missing.",
                "retained": ["retry.py stays the surface; the next slice adds the retry branch."],
                "evidence_refs": ["evidence:progress-review-vision-continue"],
            },
        })
    assert kept.get("ok") is True
    runs = _newest_first_runs(runtime)
    ack = runs[0].get("autonomous_replan_ack") or {}
    assert ack.get("recorded") is True
    assert ack["semantic_delta"]["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    assist = external_progress_review_context(_goal(registry), runtime)
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None, external_progress_review=assist) is None
