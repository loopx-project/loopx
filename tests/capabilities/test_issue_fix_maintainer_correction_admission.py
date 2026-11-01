"""Maintainer-correction admission through the real ``pr-lifecycle`` CLI.

Each case runs ``loopx issue-fix pr-lifecycle --execute-transition`` as a
subprocess against a disposable File or SQLite registry, then reads the
canonical Todo projection and the ``pr-lifecycle.jsonl`` ledger directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.testing.canary_harness import import_fixture_authority
from loopx.domain_packs.issue_fix import (
    default_issue_fix_domain_state_ledger_path,
    read_issue_fix_pr_lifecycle_ledger_row,
)
from loopx.todos import complete_goal_todo, list_goal_todos

ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "issue-fix-correction-admission"
AGENT_ID = "issue-fix-agent"
PR_URL = "https://github.com/example-org/example-repo/pull/3121"
REPO = "example-org/example-repo"
PR_REF = "pull_3121"
PATCH_ACTION = "issue_fix_maintainer_correction_patch"
CORRECTION_ACTIONS = {
    PATCH_ACTION,
    "clarify_issue_fix_maintainer_correction",
    "grant_issue_fix_maintainer_correction_authority",
}
MAINTAINER = {"login": "maintainer-a", "association": "MEMBER"}


def _event(
    event_id: str,
    *,
    kind: str = "issue_comment",
    login: str = MAINTAINER["login"],
    association: str = MAINTAINER["association"],
    created_at: str = "2026-06-20T10:00:00Z",
    updated_at: str = "2026-06-20T10:00:00Z",
) -> dict[str, object]:
    return {
        "kind": kind,
        "id": event_id,
        "html_url": f"https://github.com/example-org/example-repo/pull/3121#issuecomment-{event_id}",
        "author_login": login,
        "author_association": association,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _fixture_event(event: dict[str, object]) -> dict[str, object]:
    """Body-free provider metadata row for one comment or review."""

    row = {
        "id": int(str(event["id"])),
        "user": {"login": event["author_login"]},
        "author_association": event["author_association"],
        "created_at": event["created_at"],
        "html_url": event["html_url"],
    }
    if event["kind"] == "review":
        row["submitted_at"] = event["updated_at"]
    else:
        row["updated_at"] = event["updated_at"]
    return row


def _metadata(
    events: list[dict[str, object]], *, state: str = "OPEN", merged_at: str | None = None
) -> dict[str, object]:
    comments = [_fixture_event(e) for e in events if e["kind"] == "issue_comment"]
    reviews = [_fixture_event(e) for e in events if e["kind"] == "review"]
    review_comments = [_fixture_event(e) for e in events if e["kind"] == "review_comment"]
    payload: dict[str, object] = {
        "state": state,
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [{"name": "focused", "conclusion": "SUCCESS"}],
        "comments": comments,
        "reviews": reviews,
        "reviewComments": review_comments,
    }
    if merged_at:
        payload["mergedAt"] = merged_at
    return payload


def _correction_v1(source_event: dict[str, object], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "issue_fix_maintainer_correction_input_v1",
        "correction_kind": "actionable_patch",
        "source_kind": "maintainer_comment",
        "source_ref": str(source_event["html_url"]),
        "summary": "Keep the compatibility branch and add the missing regression assertion.",
        "verification_plan": "Run the focused compatibility test and the module smoke.",
        "pr_update_path": "Commit the bounded patch, push the PR branch, and post the validation summary.",
        "source_event": source_event,
    }
    payload.update(overrides)
    return payload


def _correction_v0(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "issue_fix_maintainer_correction_input_v0",
        "correction_kind": "actionable_patch",
        "source_kind": "review",
        "source_ref": "https://code.example.org/reviews/42",
        "summary": "Keep the compatibility branch and add the missing regression assertion.",
        "verification_plan": "Run the focused compatibility test and the module smoke.",
        "pr_update_path": "Commit the bounded patch, push the PR branch, and post the validation summary.",
    }
    payload.update(overrides)
    return payload


class Harness:
    def __init__(self, root: Path, provider: str, *, goal_overrides: dict | None = None):
        self.root = root
        self.project = root / "project"
        self.project.mkdir()
        state_file = self.project / "ACTIVE_GOAL_STATE.md"
        state_file.write_text(
            "# Correction Admission\n\n"
            "## User Todo / Owner Review Reading Queue\n\n"
            "## Agent Todo\n",
            encoding="utf-8",
        )
        self.registry = root / "registry.json"
        goal = {
            "id": GOAL_ID,
            "repo": str(self.project),
            "state_file": str(state_file),
            "coordination": {
                "agent_model": "peer_v1",
                "registered_agents": [AGENT_ID],
            },
        }
        goal.update(goal_overrides or {})
        self.registry.write_text(
            json.dumps({"common_runtime_root": str(root / "runtime"), "goals": [goal]}),
            encoding="utf-8",
        )
        import_fixture_authority(self.registry, GOAL_ID, provider=provider)
        self._counter = 0

    def _write_json(self, name: str, payload: dict[str, object]) -> Path:
        self._counter += 1
        path = self.root / f"{self._counter:03d}-{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def run(
        self,
        *,
        metadata: dict[str, object],
        correction: dict[str, object] | None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        args = [
            sys.executable, "-m", "loopx.cli",
            "--registry", str(self.registry), "--format", "json",
            "issue-fix", "pr-lifecycle",
            "--url", PR_URL,
            "--metadata-json", str(self._write_json("metadata", metadata)),
            "--goal-id", GOAL_ID,
            "--project", str(self.project),
            "--claimed-by", AGENT_ID,
            "--execute-transition",
        ]
        if correction is not None:
            args += ["--maintainer-correction-json", str(self._write_json("correction", correction))]
        result = subprocess.run(
            args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )
        if check:
            assert result.returncode == 0, result.stderr
        return result

    def run_json(self, **kwargs: object) -> dict[str, object]:
        result = self.run(**kwargs)  # type: ignore[arg-type]
        payload = json.loads(result.stdout)
        assert payload["ok"] is True, payload
        return payload

    def correction_todos(self) -> list[dict[str, object]]:
        native = list_goal_todos(registry_path=self.registry, goal_id=GOAL_ID)
        assert native["authority_read"]["decision_read_from_provider"] is True, native
        return [t for t in native["todos"] if t.get("action_kind") in CORRECTION_ACTIONS]

    def ledger_row(self) -> dict[str, object]:
        ledger = default_issue_fix_domain_state_ledger_path(project=self.project, goal_id=GOAL_ID)
        row = read_issue_fix_pr_lifecycle_ledger_row(ledger, repo=REPO, pr_ref=PR_REF)
        assert row is not None
        assert row.get("maintainer_correction_body_captured") is False, row
        assert "body" not in json.dumps(row.get("maintainer_corrections")).lower().replace(
            "body_captured", ""
        )
        return row

    def entries(self) -> list[dict[str, object]]:
        return list(self.ledger_row().get("maintainer_corrections") or [])

    def mark_done(self, todo_id: str) -> None:
        result = complete_goal_todo(
            registry_path=self.registry, goal_id=GOAL_ID, todo_id=todo_id,
            role="agent", agent_id=AGENT_ID, claimed_by=AGENT_ID,
            evidence="patch landed in fixture", no_followup=True,
        )
        assert result.get("status") == "done", result


@pytest.fixture(params=["file", "sqlite"])
def harness(tmp_path: Path, request: pytest.FixtureRequest) -> Harness:
    return Harness(tmp_path, request.param)


def _correction_write(payload: dict[str, object]) -> dict[str, object]:
    """The correction Todo receipt, independent of grouped-monitor writes."""

    receipt = payload["maintainer_correction_settlement"]["todo_write"]
    assert receipt["schema_version"] == "issue_fix_maintainer_correction_todo_write_v0"
    return receipt


def _assert_no_correction_todo(payload: dict[str, object], h: Harness, reason: str) -> None:
    admission = payload["maintainer_correction_admission"]
    assert admission["disposition"] == "context_only", admission
    assert admission["reason"] == reason, admission
    assert admission["admission_basis"] == "provider_verified"
    assert _correction_write(payload)["write_performed"] is False, _correction_write(payload)
    assert h.correction_todos() == []
    entries = h.entries()
    assert len(entries) == 1, entries
    assert entries[0]["disposition"] == "context_only"
    assert entries[0]["reason"] == reason
    assert entries[0]["todo_id"] is None


def test_t01_same_event_replayed_yields_one_todo_even_after_done(harness: Harness) -> None:
    event = _event("1001")
    metadata = _metadata([event])
    first = harness.run_json(metadata=metadata, correction=_correction_v1(event))
    assert first["maintainer_correction_admission"]["disposition"] == "accepted"
    assert first["maintainer_correction_admission"]["admission_basis"] == "provider_verified"
    assert _correction_write(first)["write_performed"] is True, _correction_write(first)
    assert _correction_write(first)["claimed_by"] == AGENT_ID
    operation_id = first["maintainer_correction_admission"]["operation_id"]
    assert operation_id.startswith("issue-fix-correction:")
    todo_id = _correction_write(first)["todo_id"]

    for _ in range(9):
        replay = harness.run_json(metadata=metadata, correction=_correction_v1(event))
        assert _correction_write(replay)["write_performed"] is False, _correction_write(replay)
        assert _correction_write(replay)["already_exists"] is True, _correction_write(replay)
        assert _correction_write(replay)["todo_id"] == todo_id
        assert replay["maintainer_correction_admission"]["replay"] is True
        assert replay["domain_state_projection"]["write_result"]["status"] == "unchanged"

    todos = harness.correction_todos()
    assert len(todos) == 1, todos
    assert todos[0]["todo_id"] == todo_id
    entries = harness.entries()
    assert len(entries) == 1, entries
    assert entries[0]["operation_id"] == operation_id
    assert entries[0]["todo_id"] == todo_id
    assert entries[0]["disposition"] == "accepted"
    assert entries[0]["source_event"]["id"] == "1001"

    harness.mark_done(todo_id)
    after_done = harness.run_json(metadata=metadata, correction=_correction_v1(event))
    assert _correction_write(after_done)["write_performed"] is False, _correction_write(after_done)
    assert _correction_write(after_done)["already_exists"] is True
    todos = harness.correction_todos()
    assert len(todos) == 1, todos
    assert todos[0]["status"] == "done"
    assert len(harness.entries()) == 1


def test_t02_distinct_events_with_identical_text_yield_two_todos(harness: Harness) -> None:
    first_event = _event("2001")
    second_event = _event("2002", created_at="2026-06-20T11:00:00Z", updated_at="2026-06-20T11:00:00Z")
    metadata = _metadata([first_event, second_event])
    shared_ref = "https://github.com/example-org/example-repo/pull/3121"
    first = harness.run_json(
        metadata=metadata, correction=_correction_v1(first_event, source_ref=shared_ref)
    )
    second = harness.run_json(
        metadata=metadata, correction=_correction_v1(second_event, source_ref=shared_ref)
    )
    assert first["maintainer_correction"]["summary"] == second["maintainer_correction"]["summary"]
    assert first["transition"]["text"] != second["transition"]["text"]
    assert "issue_comment 2001" in first["transition"]["text"]
    assert "issue_comment 2002" in second["transition"]["text"]
    assert _correction_write(first)["write_performed"] is True
    assert _correction_write(second)["write_performed"] is True
    assert _correction_write(first)["todo_id"] != _correction_write(second)["todo_id"]
    assert first["maintainer_correction_fingerprint"] == second["maintainer_correction_fingerprint"]
    assert len(harness.correction_todos()) == 2
    entries = harness.entries()
    assert len(entries) == 2
    assert {e["source_event"]["id"] for e in entries} == {"2001", "2002"}
    assert all(e["disposition"] == "accepted" and e["todo_id"] for e in entries)


def test_t03_older_revision_is_stale_and_newer_supersedes(harness: Harness) -> None:
    older = _event("3001", updated_at="2026-06-20T10:00:00Z")
    newer = _event("3001", updated_at="2026-06-20T12:00:00Z")
    newest = _event("3001", updated_at="2026-06-20T13:00:00Z")

    adopted = harness.run_json(metadata=_metadata([newer]), correction=_correction_v1(newer))
    assert adopted["maintainer_correction_admission"]["disposition"] == "accepted"
    adopted_operation = adopted["maintainer_correction_admission"]["operation_id"]
    adopted_todo = _correction_write(adopted)["todo_id"]

    stale = harness.run_json(metadata=_metadata([older]), correction=_correction_v1(older))
    admission = stale["maintainer_correction_admission"]
    assert admission["disposition"] == "context_only"
    assert admission["reason"] == "stale_revision"
    assert admission["revision_relation"] == "stale"
    assert _correction_write(stale)["write_performed"] is False
    assert _correction_write(stale)["skip_reason"] == "stale_revision"
    entries = {e["operation_id"]: e for e in harness.entries()}
    assert len(entries) == 2
    assert entries[adopted_operation]["disposition"] == "accepted"
    assert entries[adopted_operation]["todo_id"] == adopted_todo
    assert entries[adopted_operation]["superseded_by"] is None
    todos = harness.correction_todos()
    assert [t["todo_id"] for t in todos] == [adopted_todo]

    superseding = harness.run_json(metadata=_metadata([newest]), correction=_correction_v1(newest))
    admission = superseding["maintainer_correction_admission"]
    assert admission["disposition"] == "accepted"
    assert admission["revision_relation"] == "newer"
    assert admission["supersedes_operation_id"] == adopted_operation
    assert _correction_write(superseding)["write_performed"] is True
    assert _correction_write(superseding)["todo_id"] != adopted_todo
    entries = {e["operation_id"]: e for e in harness.entries()}
    assert entries[adopted_operation]["disposition"] == "superseded"
    assert entries[adopted_operation]["superseded_by"] == admission["operation_id"]
    assert entries[admission["operation_id"]]["disposition"] == "accepted"
    assert len(harness.correction_todos()) == 2


def test_t04_bot_author_is_context_only(harness: Harness) -> None:
    event = _event("4001", login="loopx-automation[bot]", association="MEMBER")
    payload = harness.run_json(metadata=_metadata([event]), correction=_correction_v1(event))
    _assert_no_correction_todo(payload, harness, "self_or_bot_author")


@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR"])
def test_t05_non_maintainer_association_is_context_only(harness: Harness, association: str) -> None:
    event = _event("5001", association=association)
    payload = harness.run_json(metadata=_metadata([event]), correction=_correction_v1(event))
    _assert_no_correction_todo(payload, harness, "author_not_maintainer")


def test_t05_provider_association_wins_over_host_assertion(harness: Harness) -> None:
    event = _event("5002", association="CONTRIBUTOR")
    asserted = {**event, "author_association": "OWNER"}
    payload = harness.run_json(metadata=_metadata([event]), correction=_correction_v1(asserted))
    _assert_no_correction_todo(payload, harness, "author_not_maintainer")


def test_t05_unobserved_event_is_context_only(harness: Harness) -> None:
    observed = _event("5101")
    claimed = _event("5102")
    payload = harness.run_json(metadata=_metadata([observed]), correction=_correction_v1(claimed))
    _assert_no_correction_todo(payload, harness, "source_event_not_observed")
    assert payload["maintainer_correction_admission"]["provider_events_status"] == "available"


def test_t05_revision_mismatch_is_context_only(harness: Harness) -> None:
    observed = _event("5201", updated_at="2026-06-20T12:00:00Z")
    claimed = _event("5201", updated_at="2026-06-20T10:00:00Z")
    payload = harness.run_json(metadata=_metadata([observed]), correction=_correction_v1(claimed))
    _assert_no_correction_todo(payload, harness, "source_event_revision_mismatch")


def test_t05_missing_event_metadata_is_context_only(harness: Harness) -> None:
    event = _event("5301")
    metadata = _metadata([event])
    for key in ("comments", "reviews", "reviewComments"):
        metadata.pop(key)
    payload = harness.run_json(metadata=metadata, correction=_correction_v1(event))
    _assert_no_correction_todo(payload, harness, "source_event_not_observed")
    assert payload["maintainer_correction_admission"]["provider_events_status"] == "unavailable"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_t06_stopped_goal_refuses_at_effect_time(tmp_path: Path, provider: str) -> None:
    h = Harness(tmp_path, provider, goal_overrides={"activation_state": "stopped"})
    event = _event("6001")
    payload = h.run_json(metadata=_metadata([event]), correction=_correction_v1(event))
    admission = payload["maintainer_correction_admission"]
    # Packet-time gates pass; only the effect-time Goal read refuses.
    assert admission["disposition"] == "accepted", admission
    settlement = payload["maintainer_correction_settlement"]
    assert settlement["goal_stopped"] is True
    assert settlement["settlements"] == [
        {"operation_id": admission["operation_id"], "status": "refused", "reason": "goal_not_active"}
    ]
    assert _correction_write(payload)["write_performed"] is False
    assert _correction_write(payload)["skip_reason"] == "goal_not_active"
    assert h.correction_todos() == []
    entries = h.entries()
    assert len(entries) == 1
    assert entries[0]["disposition"] == "context_only"
    assert entries[0]["reason"] == "goal_not_active"
    assert entries[0]["effect_recheck"] == "refused"
    assert entries[0]["todo_id"] is None


def test_t06_terminal_state_refuses_pending_entry_at_effect_time(harness: Harness) -> None:
    event = _event("6101")
    open_metadata = _metadata([event])
    faulted = harness.run(
        metadata=open_metadata, correction=_correction_v1(event),
        env={"LOOPX_ISSUE_FIX_FAULT_BEFORE_CORRECTION_TODO": "1"}, check=False,
    )
    assert faulted.returncode != 0
    pending = harness.entries()
    assert pending[0]["disposition"] == "accepted" and pending[0]["todo_id"] is None
    merged = _metadata([event], state="MERGED", merged_at="2026-06-21T00:00:00Z")
    payload = harness.run_json(metadata=merged, correction=None)
    assert payload["transition"]["decision"] == "no_followup"
    settlement = payload["maintainer_correction_settlement"]
    assert settlement["settlements"][0]["reason"] == "terminal_pr_state"
    assert harness.correction_todos() == []
    entries = harness.entries()
    assert entries[0]["disposition"] == "context_only"
    assert entries[0]["reason"] == "terminal_pr_state"


def test_t11_crash_between_ledger_entry_and_todo_write_is_reconciled(harness: Harness) -> None:
    event = _event("11001")
    metadata = _metadata([event])
    faulted = harness.run(
        metadata=metadata, correction=_correction_v1(event),
        env={"LOOPX_ISSUE_FIX_FAULT_BEFORE_CORRECTION_TODO": "1"}, check=False,
    )
    assert faulted.returncode != 0, faulted.stdout
    assert "LOOPX_ISSUE_FIX_FAULT_BEFORE_CORRECTION_TODO" in faulted.stderr + faulted.stdout
    assert harness.correction_todos() == []
    pending = harness.entries()
    assert len(pending) == 1
    assert pending[0]["disposition"] == "accepted"
    assert pending[0]["todo_id"] is None
    assert pending[0]["todo_intent"]["action_kind"] == PATCH_ACTION
    operation_id = pending[0]["operation_id"]

    # A plain poll without the correction input finishes the pending write.
    poll = harness.run_json(metadata=metadata, correction=None)
    settlement = poll["maintainer_correction_settlement"]
    assert settlement["settlements"] == [
        {"operation_id": operation_id, "status": "written",
         "todo_id": settlement["settlements"][0]["todo_id"]}
    ]
    todos = harness.correction_todos()
    assert len(todos) == 1
    entries = harness.entries()
    assert len(entries) == 1
    assert entries[0]["todo_id"] == todos[0]["todo_id"]
    assert entries[0]["operation_id"] == operation_id

    # Re-sending the same correction afterwards is a replay, not a second Todo.
    replay = harness.run_json(metadata=metadata, correction=_correction_v1(event))
    assert _correction_write(replay)["already_exists"] is True
    assert _correction_write(replay)["write_performed"] is False
    assert len(harness.correction_todos()) == 1


def test_t19_merged_pr_records_late_correction_as_context_only(harness: Harness) -> None:
    event = _event("19001")
    merged = _metadata([event], state="MERGED", merged_at="2026-06-21T00:00:00Z")
    payload = harness.run_json(metadata=merged, correction=_correction_v1(event))
    assert payload["transition"]["decision"] == "no_followup"
    assert payload["transition"]["terminal_state_precedence"] is True
    _assert_no_correction_todo(payload, harness, "terminal_pr_state")
    assert _correction_write(payload)["skip_reason"] == "terminal_pr_state"
    row = harness.ledger_row()
    assert row["maintainer_correction_admission"]["reason"] == "terminal_pr_state"


def test_v0_input_replays_through_stable_operation_id(harness: Harness) -> None:
    """Formerly the smoke's replay assertion: host-asserted v0 stays idempotent."""

    metadata = _metadata([])
    first = harness.run_json(metadata=metadata, correction=_correction_v0())
    admission = first["maintainer_correction_admission"]
    assert admission["admission_basis"] == "host_asserted"
    assert admission["disposition"] == "accepted"
    assert first["transition"]["action_kind"] == PATCH_ACTION
    assert _correction_write(first)["write_performed"] is True
    assert _correction_write(first)["claimed_by"] == AGENT_ID
    assert _correction_write(first)["path_recorded"] is False

    replay = harness.run_json(metadata=metadata, correction=_correction_v0())
    assert _correction_write(replay)["write_performed"] is False
    assert _correction_write(replay)["already_exists"] is True
    assert replay["domain_state_projection"]["write_result"]["status"] == "unchanged"
    state_text = (harness.project / "ACTIVE_GOAL_STATE.md").read_text(encoding="utf-8")
    assert state_text.count(f"action_kind={PATCH_ACTION}") == 1
    assert len(harness.correction_todos()) == 1
    entries = harness.entries()
    assert len(entries) == 1
    assert entries[0]["source_event"] is None
    assert entries[0]["admission_basis"] == "host_asserted"
    assert entries[0]["todo_id"] == _correction_write(first)["todo_id"]

    harness.mark_done(_correction_write(first)["todo_id"])
    after_done = harness.run_json(metadata=metadata, correction=_correction_v0())
    assert _correction_write(after_done)["already_exists"] is True
    assert len(harness.correction_todos()) == 1


def test_v0_terminal_state_is_recorded_not_dropped(harness: Harness) -> None:
    merged = _metadata([], state="MERGED", merged_at="2026-06-21T00:00:00Z")
    payload = harness.run_json(metadata=merged, correction=_correction_v0())
    admission = payload["maintainer_correction_admission"]
    assert admission["disposition"] == "context_only"
    assert admission["reason"] == "terminal_pr_state"
    assert admission["admission_basis"] == "host_asserted"
    entries = harness.entries()
    assert len(entries) == 1 and entries[0]["reason"] == "terminal_pr_state"
    assert harness.correction_todos() == []


def test_v1_input_rejects_body_fields_and_v0_rejects_source_event() -> None:
    from loopx.capabilities.issue_fix.pr_lifecycle import (
        normalise_issue_fix_maintainer_correction_input,
    )

    event = _event("9001")
    with pytest.raises(ValueError):
        normalise_issue_fix_maintainer_correction_input(
            _correction_v1({**event, "body": "raw body must not enter"})
        )
    with pytest.raises(ValueError):
        normalise_issue_fix_maintainer_correction_input(
            _correction_v0(source_event=event)
        )
    with pytest.raises(ValueError):
        normalise_issue_fix_maintainer_correction_input(
            {k: v for k, v in _correction_v1(event).items() if k != "source_event"}
        )
    with pytest.raises(ValueError):
        normalise_issue_fix_maintainer_correction_input(
            _correction_v1({**event, "updated_at": "2026-06-20 10:00"})
        )
    normalized = normalise_issue_fix_maintainer_correction_input(_correction_v1(event))
    assert normalized["admission_basis"] == "provider_verified"
    assert normalized["source_event"]["body_captured"] is False
    assert normalized["correction_fingerprint"] == (
        normalise_issue_fix_maintainer_correction_input(
            _correction_v0(source_kind="maintainer_comment", source_ref=event["html_url"])
        )["correction_fingerprint"]
    )
