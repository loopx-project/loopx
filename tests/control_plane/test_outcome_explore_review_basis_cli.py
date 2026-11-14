"""Same-batch reviews cover the authored link, never unrelated later evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_explore_result_writeback import ATTACHMENT
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _initialize_fixture_authority,
    _run_cli, _spend_run_count, _write_fixture,
)


def _batch_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, *, separate_review: bool = False):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0].update(status="active", explore_graph={"enabled": True})
    registry.write_text(json.dumps(config))
    _initialize_fixture_authority(project, runtime, provider=provider)

    def call(*args: str, expected_code: int = 0) -> dict:
        code, result = _run_cli(registry, runtime, *args, cwd=project)
        assert code == expected_code, result
        return result

    def inspect() -> dict:
        return call("goal-acceptance", "inspect", "--goal-id", GOAL_ID)

    reviewed_todo = (call("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--claimed-by", AGENT_ID, "--text", "Validate a separate retained outcome",
        "--task-class", "advancement_task", "--action-kind", "validate")["todo_id"]
        if separate_review else TODO_ID)
    requirements = [{"id": key, "description": description} for key, description in (
        ("content", "Preserve the requested content"),
        ("recovery", "Validate interrupted recovery"),
        ("scope", "Keep conclusions within the tested scope"),
    )]
    declaration = {"outcome_id": "export", "owner_agent_id": AGENT_ID,
        "objective": "Deliver a recoverable export", "todo_ids": [reviewed_todo],
        "requirements": requirements,
        "source": {"ref": "message:export", "summary": "The owner requires a recoverable export"}}
    document = tmp_path / "declaration.json"
    document.write_text(json.dumps(declaration))
    call("goal-acceptance", "refine", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
         "--document", str(document), "--operation-id", "declare-export",
         "--expected-provider-revision", inspect()["provider_revision"], "--execute")
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", TURN_ID)
    guard = call("quota", "should-run", "--codex-app", *binding)
    assert guard["should_run"]
    reviews = [{"id": row["id"], "conclusion": verdict,
        "evidence_refs": ATTACHMENT["evidence_refs"], "note": "Read the result within its tested scope"}
        for row, verdict in zip(requirements, ("met", "unmet", "uncertain"), strict=True)]
    update = tmp_path / "review.json"
    update.write_text(json.dumps({"operation_id": "export-review",
        "expected_provider_revision": inspect()["provider_revision"],
        "refinement": {"outcome_id": "export", "review": reviews}}))
    attachment = tmp_path / "result.json"
    attachment.write_text(json.dumps(ATTACHMENT))
    batch = ("refresh-state", *binding, "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--vision-unchanged-reason", "The result preserves the current validation plan",
        "--no-global-sync", "--suppress-external-sinks", "--goal-outcome-update-json", str(update),
        "--explore-result-json", str(attachment))
    return project, runtime, registry, call, inspect, declaration, reviews, batch


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_batch_review_of_separate_outcome_retains_its_own_work_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    _, _, _, call, inspect, declaration, reviews, batch = _batch_fixture(
        tmp_path, monkeypatch, provider, separate_review=True)
    before = inspect()
    preview = call(*batch, "--dry-run")
    assert preview["goal_outcome_update"]["status"] == "planned" and inspect() == before
    result = call(*batch)
    assert result["explore_result_delivery"]["ok"]
    outcome = inspect()["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert outcome["revision"] == 2 and outcome["todo_ids"] == declaration["todo_ids"]
    assert outcome["todo_ids"] != [TODO_ID], "batching does not attach unrelated work to the reviewed outcome"
    for row, review in zip(outcome["requirements"], reviews, strict=True):
        assert row["agent_review"] == {**review, "basis_current": True, "independently_verified": False}
    current = call("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)["todo"]
    other = call("todo", "list", "--goal-id", GOAL_ID, "--todo-id", outcome["todo_ids"][0])["todo"]
    assert current["explore_result_node_refs"] == [ATTACHMENT["node_id"]]
    assert not other.get("explore_result_node_refs")
    retained = inspect()
    replay = call(*batch)
    assert replay["goal_outcome_update"]["status"] == "replayed" and inspect() == retained


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("source_mode", ["file", "path_delta"])
def test_batch_review_is_bound_to_exact_authored_link_without_new_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, source_mode: str,
) -> None:
    _, runtime, _, call, inspect, declaration, reviews, batch = _batch_fixture(tmp_path, monkeypatch, provider)
    if source_mode == "path_delta":
        vision = tmp_path / "vision.json"
        vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
            "state": "vision_patch_proposed", "vision_patch": {"acceptance_summary": "Validate the export within its tested scope"},
            "path_delta": {"outcome": "continue", "prior_assumption": "The result needed scope validation",
                "observed_reality": ATTACHMENT["observation"], "changed": [ATTACHMENT["interpretation"]],
                "evidence_refs": ATTACHMENT["evidence_refs"]},
            "explore_result": {"schema_version": "explore_result_from_path_delta_v0",
                **{key: ATTACHMENT[key] for key in ("node_id", "question", "applicability", "input_revision", "status")}}}))
        for option in ("--vision-unchanged-reason", "--explore-result-json"):
            position = batch.index(option)
            batch = batch[:position] + batch[position + 2:]
        batch += ("--agent-vision-json", str(vision))
    batch += ("--settle-turn", "--settlement-receipt")
    before = inspect()
    preview = call(*batch, "--dry-run")
    assert preview["goal_outcome_update"]["status"] == "planned"
    assert inspect() == before
    result = call(*batch)
    assert result["explore_result_delivery"]["ok"]
    assert _spend_run_count(runtime) == 1
    outcome = inspect()["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert outcome["revision"] == 2, "one authored review must not require another refinement"
    assert outcome["sources"] == [declaration["source"]]
    assert [{"id": r["id"], "description": r["description"]} for r in outcome["requirements"]] == declaration["requirements"]
    for row, review in zip(outcome["requirements"], reviews, strict=True):
        assert row["agent_review"] == {**review, "basis_current": True, "independently_verified": False}
        assert row["status"] == "unverified"
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    committed = inspect(), index.read_bytes()
    replay = call(*batch)
    assert replay["goal_outcome_update"]["status"] == "replayed"
    assert replay["explore_result_delivery"]["idempotent_replay"]
    assert (inspect(), index.read_bytes()) == committed
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("concurrent", [False, True])
def test_interrupted_batch_stays_stale_until_exact_link_and_preserves_concurrent_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    provider: str, concurrent: bool,
) -> None:
    from loopx.cli import main
    import loopx.capabilities.explore.result_writeback as writeback

    project, runtime, registry, call, inspect, _, reviews, batch = _batch_fixture(tmp_path, monkeypatch, provider)
    original = writeback.update_goal_todo

    def interrupt_link(**kwargs):
        if kwargs.get("dry_run"):
            return original(**kwargs)
        if concurrent:
            original(**{**kwargs, "append_explore_result_node_refs": ["unreviewed-result"]})
        raise OSError("interrupted after graph persistence")

    with monkeypatch.context() as patch:
        patch.chdir(project)
        patch.setattr(writeback, "update_goal_todo", interrupt_link)
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *batch])
        result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["appended"] and not result["explore_result_delivery"]["ok"], json.dumps(result)
    interrupted = inspect()["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert interrupted["revision"] == 2
    assert all(not row["agent_review"]["basis_current"] for row in interrupted["requirements"])
    # Replay in a new CLI process: it recovers the original digest, never hashes
    # whatever authority head happens to be current after a failed delivery.
    recovered = call(*batch)
    assert recovered["goal_outcome_update"]["status"] == "replayed"
    assert recovered["explore_result_delivery"]["ok"]
    after = inspect()["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert after["revision"] == 2
    for row, review in zip(after["requirements"], reviews, strict=True):
        assert row["agent_review"] == {**review, "basis_current": not concurrent, "independently_verified": False}
    todo = call("todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)["todo"]
    assert todo["explore_result_node_refs"] == (["unreviewed-result"] if concurrent else []) + [ATTACHMENT["node_id"]]
    retained = inspect()
    call(*batch)
    assert inspect() == retained


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_stale_initial_cas_refuses_review_and_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from loopx.todos import update_goal_todo

    _, runtime, registry, call, inspect, _, _, batch = _batch_fixture(tmp_path, monkeypatch, provider)
    update_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id=GOAL_ID,
        todo_id=TODO_ID, agent_id=AGENT_ID, append_explore_result_node_refs=["unreviewed-result"])
    before = inspect()
    refused = call(*batch, expected_code=1)
    assert not refused["appended"] and "goal_outcome_update" not in refused
    assert inspect() == before
    from loopx.capabilities.explore.result_log import explore_result_log_path
    assert not explore_result_log_path(runtime, GOAL_ID).exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("legacy_batch", [False, True])
def test_separate_or_legacy_review_does_not_adopt_new_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, legacy_batch: bool,
) -> None:
    from loopx.control_plane.goals.acceptance import refine_goal_outcome

    _, runtime, registry, call, inspect, _, _, batch = _batch_fixture(tmp_path, monkeypatch, provider)
    update_index = batch.index("--goal-outcome-update-json")
    update = json.loads(Path(batch[update_index + 1]).read_text())
    # Represents a separately reviewed state, including a committed operation
    # from an older runtime whose batched request did not anticipate the link.
    refine_goal_outcome(registry_path=registry, runtime_root=str(runtime), goal_id=GOAL_ID,
        agent_id=AGENT_ID, execute=True, **update)
    before = inspect()["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert all(row["agent_review"]["basis_current"] for row in before["requirements"])
    args = batch if legacy_batch else batch[:update_index] + batch[update_index + 2:]
    result = call(*args)
    assert result["explore_result_delivery"]["ok"]
    if legacy_batch:
        assert result["goal_outcome_update"]["status"] == "replayed"
    after = inspect()["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert after["revision"] == before["revision"] == 2
    assert all(not row["agent_review"]["basis_current"] for row in after["requirements"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_record_review_patch_adopts_only_its_same_batch_result(tmp_path, monkeypatch, provider):
    _, _, _, call, inspect, _, reviews, batch = _batch_fixture(tmp_path, monkeypatch, provider)
    update = Path(batch[batch.index("--goal-outcome-update-json") + 1])
    request = json.loads(update.read_text())
    request["refinement"] = {"outcome_id": "export", "storage_format": "records", "review_patch": reviews[:1]}
    update.write_text(json.dumps(request))
    result = call(*batch)
    assert result["explore_result_delivery"]["ok"]
    rows = inspect()["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"]
    assert rows[0]["agent_review"]["basis_current"]
    assert rows[0]["agent_review"]["evidence_refs"] == reviews[0]["evidence_refs"]
    assert all("agent_review" not in row for row in rows[1:])
    assert all(row["status"] == "unverified" for row in rows)
    retained = inspect()
    assert call(*batch)["goal_outcome_update"]["status"] == "replayed"
    assert inspect() == retained
