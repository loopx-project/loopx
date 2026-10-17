from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.reward_memory import codex_app_outcome
from loopx.capabilities.reward_memory.codex_app_outcome import (
    codex_app_outcome_candidate_sidecar_path,
    run_staged_codex_app_turn_outcome_ingest,
    stage_codex_app_turn_outcome_candidate,
    run_staged_codex_app_turn_outcome_ingest_fail_open,
)


@pytest.fixture(autouse=True)
def candidate_registry(tmp_path: Path) -> None:
    # Direct adapter tests keep a real invoked registry. They do not resolve an
    # installed or global Goal when qualifying candidate identity fences.
    (tmp_path / "registry.json").write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": "goal", "repo": str(tmp_path),
                   "coordination": {"registered_agents": ["pilot"]}}],
    }))


def _reflection() -> str:
    evidence_refs = ["artifact:app-route", "receipt:app-validation"]
    return json.dumps(
        {
            "schema_version": "turn_reward_memory_reflection_v1",
            "status": "eligible",
            "surface_id": "agent_workflow.turn_admission",
            "outcome_kind": "engineering",
            "content_summary": "Run the exact admission test before changing routing.",
            "reasoning_summary": "The independently checked route avoided stale state.",
            "confidence": "high",
            "evidence_refs": evidence_refs,
            "experience": {
                "schema_version": "procedural_experience_contract_v0",
                "applicability": ["Changing a managed Turn admission route"],
                "observed_outcome": (
                    "The exact admission test avoided a route based on stale state."
                ),
                "attribution": (
                    "Independent validation bound the route result to current "
                    "admission evidence."
                ),
                "future_behavior": {
                    "trigger": "A managed Turn admission route may change.",
                    "action": "Run the exact admission test before changing routing.",
                    "validation": (
                        "Bind the result to the declared validator and current route "
                        "receipt."
                    ),
                    "stop_condition": (
                        "Do not change routing when the validator or current receipt "
                        "is missing."
                    ),
                },
                "limitations": [
                    "This result applies only to the validated route and revision."
                ],
                "evidence_refs": evidence_refs,
            },
        }
    )


def _large_but_valid_reflection() -> str:
    payload = json.loads(_reflection())
    experience = payload["experience"]
    payload["content_summary"] = "C" * 500
    payload["reasoning_summary"] = "R" * 500
    experience["applicability"] = ["A" * 500, "B" * 500]
    experience["observed_outcome"] = "O" * 500
    experience["attribution"] = "T" * 500
    experience["future_behavior"] = {
        "trigger": "G" * 500,
        "action": "X" * 500,
        "validation": "V" * 500,
        "stop_condition": "S" * 500,
    }
    experience["limitations"] = ["L" * 500, "M" * 500]
    serialized = json.dumps(payload)
    assert len(serialized) > 2_400
    return serialized


def _validator(path: Path, *, attest: bool) -> list[str]:
    body = (
        "import json,sys; r=json.load(sys.stdin); "
        + (
            "print(json.dumps({'schema_version':"
            "'reward_memory_reflection_validation_v0','status':'validated',"
            "'reflection_digest':r['reflection_digest'],"
            "'evidence_refs':r['reflection']['evidence_refs']}))"
            if attest
            else "print('validation passed without reflection attestation')"
        )
    )
    path.write_text(body + "\n", encoding="utf-8")
    return [sys.executable, str(path)]


@pytest.fixture
def staged_case(tmp_path, monkeypatch):
    argv = _validator(tmp_path / "validate.py", attest=True)
    declaration = {"validation_command": None, "validation_command_argv": argv,
                   "validation_label": "exact outcome", "validation_timeout_seconds": 5}
    monkeypatch.setattr(codex_app_outcome, "_validation_declaration", lambda **_: declaration)
    stage = dict(registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
                 goal_id="goal", agent_id="pilot", todo_id="todo_app", turn_instance_id="turn-app",
                 effect_id="effect:app", state_file=tmp_path / "STATE.md", validation_workspace=tmp_path,
                 reflection_json=_reflection(), observed_at="2026-10-17T00:00:00Z")
    result = stage_codex_app_turn_outcome_candidate(**stage)
    path = codex_app_outcome_candidate_sidecar_path(tmp_path, goal_id="goal", agent_id="pilot", candidate_id=result["candidate_id"])
    finalize = {key: stage[key] for key in ("registry_path", "runtime_root", "goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id")}
    finalize.update(candidate_id=None, writeback_appended=True, spend_appended=True)
    return stage, finalize, path, declaration


@pytest.mark.parametrize("field", ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id",
                                  "candidate_id", "reflection_digest", "binding_fingerprint", "validation_declaration_sha256"])
def test_corrupted_candidate_never_reaches_provider(staged_case, monkeypatch, field):
    _, finalize, path, _ = staged_case
    value = json.loads(path.read_text())
    value[field] = "other"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(codex_app_outcome, "run_configured_turn_outcome_ingest", lambda **_: pytest.fail("invalid candidate reached provider"))
    result = run_staged_codex_app_turn_outcome_ingest_fail_open(**finalize)
    assert result["external_writes_performed"] is False
    assert result["status"] == "runtime_unavailable"


def test_reflection_tampering_and_legacy_unstamped_candidates_are_held(staged_case, monkeypatch):
    _, finalize, path, _ = staged_case
    original = json.loads(path.read_text())
    monkeypatch.setattr(codex_app_outcome, "run_configured_turn_outcome_ingest", lambda **_: pytest.fail("unbound reflection reached provider"))
    changed = json.loads(path.read_text())
    changed["reflection"]["content_summary"] = "A different proposed lesson."
    path.write_text(json.dumps(changed))
    assert run_staged_codex_app_turn_outcome_ingest_fail_open(**finalize)["external_writes_performed"] is False
    del original["binding_fingerprint"]
    path.write_text(json.dumps(original))
    assert run_staged_codex_app_turn_outcome_ingest_fail_open(**finalize)["status"] == "runtime_unavailable"


@pytest.mark.parametrize("missing", ["writeback_appended", "spend_appended"])
def test_candidate_waits_for_both_settlement_steps(staged_case, monkeypatch, missing):
    _, finalize, _, _ = staged_case
    monkeypatch.setattr(codex_app_outcome, "run_configured_turn_outcome_ingest", lambda **_: pytest.fail("unsettled candidate reached provider"))
    result = run_staged_codex_app_turn_outcome_ingest_fail_open(**{**finalize, missing: False})
    assert result["status"] == "evidence_validation_required"
    assert result["external_writes_performed"] is False


def test_changed_route_and_validator_cannot_reuse_earlier_attestation(staged_case, monkeypatch):
    stage, finalize, _, declaration = staged_case
    monkeypatch.setattr(codex_app_outcome, "run_configured_turn_outcome_ingest", lambda **_: pytest.fail("drifted candidate reached provider"))
    declaration["validation_label"] = "replacement declaration"
    assert run_staged_codex_app_turn_outcome_ingest_fail_open(**finalize)["status"] == "runtime_unavailable"
    # The same reflection can be independently revalidated against the current
    # declaration, but the destination route cannot move with it.
    assert stage_codex_app_turn_outcome_candidate(**stage)["validation_bound"] is True
    registry = stage["registry_path"]
    data = json.loads(registry.read_text())
    data["goals"][0]["control_plane"] = {"reward_memory": {"enabled": True, "experimental": True, "config_path": "replacement.json"}}
    registry.write_text(json.dumps(data))
    assert run_staged_codex_app_turn_outcome_ingest_fail_open(**finalize)["status"] == "runtime_unavailable"
    with pytest.raises(RuntimeError, match="binding_fingerprint"):
        stage_codex_app_turn_outcome_candidate(**stage)


def test_failed_revalidation_does_not_reuse_previous_pass(staged_case, monkeypatch):
    stage, finalize, path, _ = staged_case
    _validator(stage["validation_workspace"] / "validate.py", attest=False)
    assert stage_codex_app_turn_outcome_candidate(**stage)["validation_bound"] is False
    assert json.loads(path.read_text())["task_validation"]["ok"] is False
    monkeypatch.setattr(codex_app_outcome, "run_configured_turn_outcome_ingest", lambda **_: pytest.fail("failed revalidation reached provider"))
    assert run_staged_codex_app_turn_outcome_ingest_fail_open(**finalize)["status"] == "evidence_validation_required"


def test_concurrent_stages_cannot_replace_the_first_reflection(staged_case, monkeypatch):
    stage, _, path, _ = staged_case
    path.unlink()  # A disposable fixture, before either competing first stage.
    calls = []
    original = codex_app_outcome._run_reflection_validator

    def validate(**kwargs):
        calls.append(kwargs["reflection_digest"])
        time.sleep(0.05)  # Allow the other caller to contend for this candidate.
        return original(**kwargs)

    monkeypatch.setattr(codex_app_outcome, "_run_reflection_validator", validate)
    changed = json.loads(stage["reflection_json"])
    changed["content_summary"] = "A different competing proposal for the same Turn."

    def attempt(text):
        try:
            return stage_codex_app_turn_outcome_candidate(**{**stage, "reflection_json": text})
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [stage["reflection_json"], json.dumps(changed)]))
    assert sum(result is not None for result in results) == 1
    assert len(calls) == 1
    assert json.loads(path.read_text())["reflection_digest"] == calls[0]


def test_app_refresh_stages_private_candidate_and_spend_finalizes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = _validator(tmp_path / "validate.py", attest=True)
    monkeypatch.setattr(
        codex_app_outcome,
        "_validation_declaration",
        lambda **_kwargs: {
            "validation_command": None,
            "validation_command_argv": argv,
            "validation_label": "app reflection validator",
            "validation_timeout_seconds": 5,
        },
    )
    monkeypatch.setattr(
        codex_app_outcome,
        "_goal_repo",
        lambda *_args, **_kwargs: tmp_path,
    )

    staged = stage_codex_app_turn_outcome_candidate(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal",
        agent_id="pilot",
        todo_id="todo_app",
        turn_instance_id="turn-app",
        effect_id="effect:app",
        state_file=tmp_path / "ACTIVE_GOAL_STATE.md",
        validation_workspace=tmp_path,
        reflection_json=_reflection(),
        observed_at="2026-09-13T03:00:00+00:00",
    )

    assert staged["status"] == "validation_bound"
    assert staged["validation_bound"] is True
    assert staged["raw_content_projected"] is False
    assert staged["external_writes_performed"] is False
    path = codex_app_outcome_candidate_sidecar_path(
        tmp_path,
        goal_id="goal",
        agent_id="pilot",
        candidate_id=staged["candidate_id"],
    )
    assert path.stat().st_mode & 0o777 == 0o600

    observed: dict[str, Any] = {}

    def ingest(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {
            "ok": True,
            "status": "activated",
            "external_writes_performed": True,
        }

    monkeypatch.setattr(
        codex_app_outcome,
        "run_configured_turn_outcome_ingest",
        ingest,
    )
    finalized = run_staged_codex_app_turn_outcome_ingest(
        registry_path=tmp_path / "registry.json",
        goal_id="goal",
        agent_id="pilot",
        todo_id="todo_app",
        turn_instance_id="turn-app",
        effect_id="effect:app",
        candidate_id=staged["candidate_id"],
        writeback_appended=True,
        spend_appended=True,
    )

    assert finalized["status"] == "activated"
    assert finalized["host_wiring"] == (
        "codex_app_refresh_spend_post_settlement"
    )
    assert observed["turn_key"] == "effect:app"
    evidence = observed["settlement_evidence"]
    assert evidence["task_validation"]["ok"] is True
    assert evidence["writeback"] == {"ok": True, "appended": True}
    assert evidence["quota_spend"] == {"ok": True, "appended": True}


def test_app_refresh_without_exact_validator_attestation_stays_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = _validator(tmp_path / "validate.py", attest=False)
    monkeypatch.setattr(
        codex_app_outcome,
        "_validation_declaration",
        lambda **_kwargs: {
            "validation_command": None,
            "validation_command_argv": argv,
            "validation_label": "ordinary validator",
            "validation_timeout_seconds": 5,
        },
    )
    monkeypatch.setattr(
        codex_app_outcome,
        "_goal_repo",
        lambda *_args, **_kwargs: tmp_path,
    )

    staged = stage_codex_app_turn_outcome_candidate(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal",
        agent_id="pilot",
        todo_id="todo_app",
        turn_instance_id="turn-app",
        effect_id="effect:app",
        state_file=tmp_path / "ACTIVE_GOAL_STATE.md",
        validation_workspace=tmp_path,
        reflection_json=_reflection(),
        observed_at="2026-09-13T03:00:00+00:00",
    )

    assert staged["status"] == "awaiting_evidence_validation"
    assert staged["validation_bound"] is False
    assert staged["provider_sync_count"] == 0
    assert staged["external_writes_performed"] is False


def test_app_refresh_accepts_large_valid_reflection_without_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_app_outcome,
        "_validation_declaration",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        codex_app_outcome,
        "_goal_repo",
        lambda *_args, **_kwargs: tmp_path,
    )

    staged = stage_codex_app_turn_outcome_candidate(
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="goal",
        agent_id="pilot",
        todo_id="todo_without_validator",
        turn_instance_id="turn-large-reflection",
        effect_id="effect:large-reflection",
        state_file=tmp_path / "ACTIVE_GOAL_STATE.md",
        validation_workspace=tmp_path,
        reflection_json=_large_but_valid_reflection(),
        observed_at="2026-09-21T15:02:02+00:00",
    )

    assert staged["status"] == "awaiting_evidence_validation"
    assert staged["reason_code"] == "reflection_not_bound_to_independent_validation"
    assert staged["candidate_id"]
    assert staged["reflection_digest"].startswith("sha256:")
    assert staged["validation_bound"] is False
    assert staged["provider_sync_count"] == 0
    assert staged["external_writes_performed"] is False


def test_app_refresh_rejects_truly_oversized_reflection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codex_app_outcome,
        "_goal_repo",
        lambda *_args, **_kwargs: tmp_path,
    )
    payload = json.loads(_reflection())
    payload["content_summary"] = "X" * (17 * 1024)

    with pytest.raises(
        ValueError,
        match="reward memory reflection exceeds its bounded contract",
    ):
        stage_codex_app_turn_outcome_candidate(
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime",
            goal_id="goal",
            agent_id="pilot",
            todo_id="todo_oversized",
            turn_instance_id="turn-oversized-reflection",
            effect_id="effect:oversized-reflection",
            state_file=tmp_path / "ACTIVE_GOAL_STATE.md",
            validation_workspace=tmp_path,
            reflection_json=json.dumps(payload),
            observed_at="2026-09-21T15:02:02+00:00",
        )
