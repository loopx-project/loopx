from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix import pr_lifecycle
from loopx.capabilities.catalog import BUILTIN_CAPABILITIES
from loopx.capabilities.issue_fix.pr_lifecycle import (
    build_issue_fix_pr_lifecycle_monitor_packet,
)
from loopx.capabilities.issue_fix.pr_monitor_materialization import (
    materialize_issue_fix_grouped_monitors,
)
from loopx.capabilities.issue_fix.workflow_plan import (
    build_issue_fix_goal_command_templates,
    build_issue_fix_pr_lifecycle_command,
)
from loopx.cli import main as cli_main
from loopx.control_plane.scheduler.monitor_poll_writeback import (
    write_monitor_poll_todo_state,
)
from loopx.domain_packs.issue_fix import (
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from loopx.todos import list_goal_todos, update_goal_todo
from loopx.control_plane.todos.resume_condition import evaluate_todo_resume_conditions

GOAL_ID = "issue-fix-monitor-goal"
AGENT_ID = "issue-fix-worker"
PEER_AGENT_ID = "issue-fix-peer"


def _fixture(
    tmp_path: Path, *, registered_agents: list[str] | None = None
) -> tuple[Path, Path, Path]:
    project = tmp_path / "repo"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Continue independent issue-fix work.\n"
        "  <!-- loopx:todo todo_id=todo_advancement status=open "
        "task_class=advancement_task claimed_by=issue-fix-worker -->\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": registered_agents or [AGENT_ID],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return project, state, registry


def _use_provider(state, registry, provider, monkeypatch):
    if provider == "legacy":
        return
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    isolate_sqlite_runtime(registry.parent, monkeypatch)
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
    initialize_canonical_authority(registry.parent, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, leases=[], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    state.unlink()


def _monitor_todos(registry: Path, project: Path) -> list[dict]:
    payload = list_goal_todos(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        project=project,
    )
    return [
        item
        for item in payload["todos"]
        if item.get("task_class") == "continuous_monitor"
    ]


def _packet(
    number: int,
    *,
    state: str = "OPEN",
    owner: str = "huangruiteng",
    repo: str = "loopx",
) -> dict:
    status_rollup = (
        [{"name": "integration", "status": "IN_PROGRESS"}]
        if state == "OPEN"
        else [{"name": "integration", "conclusion": "SUCCESS"}]
    )
    return build_issue_fix_pr_lifecycle_monitor_packet(
        url=f"https://github.com/{owner}/{repo}/pull/{number}",
        provider_payload={
            "state": state,
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": status_rollup,
        },
        generated_at="2026-08-01T16:00:00Z",
    )


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_grouped_monitor_materialization_is_one_per_bucket_and_retires_empty_bucket(
    tmp_path: Path, monkeypatch, provider,
) -> None:
    project, state, registry = _fixture(tmp_path)
    _use_provider(state, registry, provider, monkeypatch)
    ledger = tmp_path / "pr-lifecycle.jsonl"
    first = _packet(101)
    second = _packet(102)
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, first)
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, second)

    created = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2026-08-01T16:00:00Z",
    )

    assert created["write_performed"] is True
    assert created["active_bucket_count"] == 1
    assert created["active_member_count"] == 2
    assert datetime.fromisoformat(created["next_due_at"]).timestamp() == (
        datetime.fromisoformat("2026-08-01T16:30:00+00:00").timestamp()
    )
    active = state.read_text(encoding="utf-8")
    assert active.count("task_class=continuous_monitor") == 1
    assert "target_key=github-pr-state-huangruiteng--loopx-checks-pending" in active
    assert "cadence=30m" in active
    encoded_next_due_at = created["next_due_at"].replace("+", "%2B")
    assert f"next_due_at={encoded_next_due_at}" in active

    unchanged = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2026-08-01T16:01:00Z",
    )
    assert unchanged["active_bucket_count"] == 1
    assert unchanged["write_performed"] is False
    assert state.read_text(encoding="utf-8").count("task_class=continuous_monitor") == 1

    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(101, state="MERGED"))
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(102, state="MERGED"))
    retired = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2026-08-01T16:02:00Z",
    )

    assert retired["active_bucket_count"] == 0
    assert retired["write_performed"] is True
    final_state = state.read_text(encoding="utf-8")
    assert "status=done" in final_state
    assert (
        "evidence=Issue-fix%20PR%20lifecycle%20bucket%20"
        "github-pr-state-huangruiteng--loopx-checks-pending%20is%20empty."
    ) in final_state

    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(103))
    reopened = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        # Completion uses the runtime clock; this new observation follows it.
        generated_at="2030-08-01T16:03:00Z",
    )
    assert reopened["write_performed"] is True
    reopened_state = state.read_text(encoding="utf-8")
    assert reopened_state.count("task_class=continuous_monitor") == 1
    assert "status=open" in reopened_state
    assert "no_followup=true" not in reopened_state
    if provider != "legacy":
        assert reopened["projection_delivery"] in {"delivered", "current"}


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_group_membership_change_advances_monitor_generation(tmp_path: Path, monkeypatch, provider) -> None:
    project, state, registry = _fixture(tmp_path)
    _use_provider(state, registry, provider, monkeypatch)
    ledger = tmp_path / "pr-lifecycle.jsonl"
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(101))
    arguments = dict(registry_path=registry, goal_id=GOAL_ID, project=project,
                     ledger_path=ledger, claimed_by=AGENT_ID, cadence="30m")
    materialize_issue_fix_grouped_monitors(**arguments, generated_at="2030-01-01T01:00:00Z")
    before = _monitor_todos(registry, project)[0]
    baseline = int(before.get("material_change_generation") or 0)
    waiting = {"todo_id": "todo_waiting", "role": "agent", "status": "open",
               "task_class": "advancement_task", "resume_when": f"monitor_changed:{before['todo_id']}",
               "resume_monitor_generation": baseline}
    assert evaluate_todo_resume_conditions([waiting], source_items=[before])["todo_waiting"]["satisfied"] is False
    # Observations must preserve an explicitly bounded existing watch policy,
    # rather than resetting it to the capability's initial create default.
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=before["todo_id"],
                    agent_id=AGENT_ID, project=project,
                    monitor_metadata={"watch_only": "false", "expires_at": "2031-01-01T00:00:00Z"})
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(102))
    materialize_issue_fix_grouped_monitors(**arguments, generated_at="2030-01-01T02:00:00Z")
    after = _monitor_todos(registry, project)[0]
    assert after["todo_id"] == before["todo_id"]
    assert after["result_hash"] != before["result_hash"]
    assert after["material_change_generation"] == baseline + 1
    assert str(after["watch_only"]).lower() == "false"
    assert after["expires_at"] == "2031-01-01T00:00:00Z"
    assert evaluate_todo_resume_conditions([waiting], source_items=[after])["todo_waiting"]["satisfied"] is True
    unchanged = state.read_bytes()
    materialize_issue_fix_grouped_monitors(**arguments, generated_at="2030-01-01T03:00:00Z")
    assert state.read_bytes() == unchanged


def test_grouped_monitors_are_isolated_per_repository(tmp_path: Path) -> None:
    project, state, registry = _fixture(tmp_path)
    ledger = tmp_path / "pr-lifecycle.jsonl"
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(101))
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(
        ledger,
        _packet(202, owner="openai", repo="codex"),
    )

    result = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2026-08-01T16:00:00Z",
    )

    assert result["active_bucket_count"] == 2
    assert result["active_member_count"] == 2
    active = state.read_text(encoding="utf-8")
    assert active.count("task_class=continuous_monitor") == 2
    assert "target_key=github-pr-state-huangruiteng--loopx-checks-pending" in active
    assert "target_key=github-pr-state-openai--codex-checks-pending" in active


def test_grouped_monitor_keeps_creator_ownership_across_turns_and_due_poll(
    tmp_path: Path,
) -> None:
    project, state, registry = _fixture(
        tmp_path,
        registered_agents=[AGENT_ID, PEER_AGENT_ID],
    )
    ledger = tmp_path / "pr-lifecycle.jsonl"
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(101))

    created = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2026-08-01T16:00:00Z",
    )
    assert created["write_performed"] is True
    monitors = _monitor_todos(registry, project)
    assert len(monitors) == 1
    monitor = monitors[0]
    assert monitor["claimed_by"] == AGENT_ID
    todo_id = monitor["todo_id"]

    state_before_peer_poll = state.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=f"claimed_by={AGENT_ID!r}"):
        write_monitor_poll_todo_state(
            registry_path=registry,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            todo_id=todo_id,
            result_hash=monitor["result_hash"],
            generated_at="2026-08-01T16:30:00Z",
            execute=True,
            agent_id=PEER_AGENT_ID,
        )
    assert state.read_text(encoding="utf-8") == state_before_peer_poll

    polled = write_monitor_poll_todo_state(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo_id,
        result_hash=monitor["result_hash"],
        generated_at="2026-08-01T16:30:00Z",
        execute=True,
        agent_id=AGENT_ID,
    )
    assert polled is not None
    assert polled["todo_update"]["mutation_authority"]["actor_agent_id"] == (
        AGENT_ID
    )
    assert datetime.fromisoformat(polled["next_due_at"]).timestamp() == (
        datetime.fromisoformat("2026-08-01T17:00:00+00:00").timestamp()
    )

    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(102))
    state_before_peer_attempt = state.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=f"claimed_by={AGENT_ID!r}"):
        materialize_issue_fix_grouped_monitors(
            registry_path=registry,
            goal_id=GOAL_ID,
            project=project,
            ledger_path=ledger,
            claimed_by=PEER_AGENT_ID,
            cadence="30m",
            generated_at="2026-08-01T16:31:00Z",
        )
    assert state.read_text(encoding="utf-8") == state_before_peer_attempt

    changed_by_creator = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2026-08-01T16:31:00Z",
    )
    assert changed_by_creator["write_performed"] is True
    monitors = _monitor_todos(registry, project)
    assert len(monitors) == 1
    assert monitors[0]["todo_id"] == todo_id
    assert monitors[0]["claimed_by"] == AGENT_ID

    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(101, state="MERGED"))
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(102, state="MERGED"))
    retired_by_creator = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2026-08-01T16:32:00Z",
    )
    assert retired_by_creator["write_performed"] is True
    assert _monitor_todos(registry, project)[0]["status"] == "done"

    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(103))
    reopened_by_creator = materialize_issue_fix_grouped_monitors(
        registry_path=registry,
        goal_id=GOAL_ID,
        project=project,
        ledger_path=ledger,
        claimed_by=AGENT_ID,
        cadence="30m",
        generated_at="2030-08-01T16:33:00Z",
    )
    assert reopened_by_creator["write_performed"] is True
    reopened = _monitor_todos(registry, project)[0]
    assert reopened["todo_id"] == todo_id
    assert reopened["status"] == "open"
    assert reopened["claimed_by"] == AGENT_ID


def test_pr_lifecycle_command_contract_is_shared_by_all_operator_surfaces() -> None:
    canonical = build_issue_fix_pr_lifecycle_command(
        cli_bin="loopx",
        goal_id="goal-fixture",
        agent_id="agent-fixture",
    )
    templates = build_issue_fix_goal_command_templates(
        cli_bin="loopx",
        goal_id="goal-fixture",
        agent_id="agent-fixture",
    )
    assert templates["issue_fix_pr_lifecycle_template"] == canonical

    issue_fix = next(item for item in BUILTIN_CAPABILITIES if item["id"] == "issue-fix")
    catalog_command = next(
        item["command"]
        for item in issue_fix["commands"]
        if "issue-fix pr-lifecycle" in item["command"]
    )
    assert catalog_command == build_issue_fix_pr_lifecycle_command(
        cli_bin="loopx",
        goal_id="<goal-id>",
        agent_id="<agent-id>",
        project="<repo>",
    )
    for command in (canonical, catalog_command):
        assert "--goal-id" in command
        assert "--claimed-by" in command
        assert "--execute-transition" in command
        assert "--fetch-metadata" not in command


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_pr_lifecycle_execute_materializes_pending_monitor_and_keeps_goal_runnable(
    tmp_path: Path, monkeypatch, provider,
) -> None:
    project, state, registry = _fixture(tmp_path)
    _use_provider(state, registry, provider, monkeypatch)
    metadata = tmp_path / "pr.json"
    metadata.write_text(
        json.dumps(
            {
                "state": "OPEN",
                "reviewDecision": "REVIEW_REQUIRED",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [{"name": "integration", "status": "IN_PROGRESS"}],
            }
        ),
        encoding="utf-8",
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert (
            cli_main(
                [
                    "--registry",
                    str(registry),
                    "--format",
                    "json",
                    "issue-fix",
                    "pr-lifecycle",
                    "--url",
                    "https://github.com/huangruiteng/loopx/pull/101",
                    "--metadata-json",
                    str(metadata),
                    "--goal-id",
                    GOAL_ID,
                    "--project",
                    str(project),
                    "--claimed-by",
                    AGENT_ID,
                    "--execute-transition",
                    "--generated-at",
                    "2026-08-01T16:00:00Z",
                ]
            )
            == 0
        )
    payload = json.loads(output.getvalue())

    assert payload["ok"] is True
    assert payload["transition"]["decision"] == "monitor_continuation"
    assert payload["first_screen"] == (
        payload["first_screen"]
        | {
            "agent_can_continue": True,
            "current_pr_actionable": False,
            "immediate_repoll_required": False,
        }
    )
    assert payload["grouped_monitor_writeback"] == (
        payload["grouped_monitor_writeback"]
        | {
            "write_performed": True,
            "active_bucket_count": 1,
            "active_member_count": 1,
        }
    )
    assert payload["todo_write_performed"] is True
    active = state.read_text(encoding="utf-8")
    assert active.count("task_class=continuous_monitor") == 1
    assert "target_key=github-pr-state-huangruiteng--loopx-checks-pending" in active
    assert "task_class=advancement_task" in active


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unchanged_group_retries_display_without_repeating_business(tmp_path, monkeypatch, provider):
    from loopx.control_plane.todos import provider_projection as delivery
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    project, state, registry = _fixture(tmp_path)
    _use_provider(state, registry, provider, monkeypatch)
    ledger = tmp_path / "lifecycle.jsonl"
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(101))
    args = dict(registry_path=registry, goal_id=GOAL_ID, project=project,
        ledger_path=ledger, claimed_by=AGENT_ID, cadence="30m", generated_at="2030-01-01T00:00:00Z")
    original = delivery.project_current_canonical_todos
    def unavailable(**kwargs):
        raise OSError("isolated display failure")
    monkeypatch.setattr(delivery, "project_current_canonical_todos", unavailable)
    first = materialize_issue_fix_grouped_monitors(**args)
    assert first["projection_delivery"] == "pending"
    assert first["projection_outbox"]["retry_business_mutation"] is False
    before = read_canonical_todos_if_promoted(runtime_root=tmp_path, goal_id=GOAL_ID)
    assert not state.exists()
    monkeypatch.setattr(delivery, "project_current_canonical_todos", original)
    recovered = materialize_issue_fix_grouped_monitors(**args)
    assert recovered["write_performed"] is False
    assert recovered["projection_delivery"] == "delivered"
    assert read_canonical_todos_if_promoted(runtime_root=tmp_path, goal_id=GOAL_ID) == before
    assert "[P2] Monitor" in state.read_text()


def test_pr_lifecycle_execute_fetches_public_metadata_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, state, registry = _fixture(tmp_path)

    def fake_fetch(reference: dict, *, timeout_seconds: int) -> dict:
        assert reference["repo"] == "huangruiteng/loopx"
        assert timeout_seconds == 10
        return {
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"name": "integration", "status": "IN_PROGRESS"}],
        }

    monkeypatch.setattr(
        pr_lifecycle,
        "fetch_github_pr_lifecycle_payload",
        fake_fetch,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert (
            cli_main(
                [
                    "--registry",
                    str(registry),
                    "--format",
                    "json",
                    "issue-fix",
                    "pr-lifecycle",
                    "--url",
                    "https://github.com/huangruiteng/loopx/pull/101",
                    "--goal-id",
                    GOAL_ID,
                    "--project",
                    str(project),
                    "--claimed-by",
                    AGENT_ID,
                    "--execute-transition",
                    "--generated-at",
                    "2026-08-01T16:00:00Z",
                ]
            )
            == 0
        )

    payload = json.loads(output.getvalue())
    assert payload["ok"] is True
    assert payload["external_reads_performed"] is True
    assert state.read_text(encoding="utf-8").count("task_class=continuous_monitor") == 1
