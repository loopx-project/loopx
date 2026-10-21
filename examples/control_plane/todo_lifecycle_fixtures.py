"""Fixtures for the todo lifecycle CLI smoke."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as loopx_cli_main  # noqa: E402
from loopx.control_plane.coordination.goal_storage_initialization import (  # noqa: E402
    initialize_goal_storage_target,
)
from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    runtime_root_from_registry,
    write_fixture_registry,
)
from loopx.status import parse_active_state_todos  # noqa: E402


GOAL_ID = "todo-lifecycle-goal"
RUN_TODO = "Run a fresh-seed full PR3-r8 treatment repeat after the support-blocked seed failed."
REBUILD_TODO = "Rebuild labels and scorer after the fresh repeat."
VALIDATE_TODO = "Validate scorer labels and write back the compact result."
SIDE_TODO = "Refine todo ownership contract from a side worktree."
SIDE_CONTINUATION_TODO = "Continue the small side-agent productization lane after self-merge."
SIDE_REVIEW_TODO = "Refine todo ownership contract with primary review required."
REVIEW_TODO = "Primary agent review, verify, and merge the side-agent ownership contract work."
SIDE_HANDOFF_SOURCE_TODO = "Refine todo ownership contract with configured side-agent handoff."
SIDE_HANDOFF_TODO = "Review, verify, and continue the side-agent handoff work."
HANDOFF_SOURCE_TODO = "Prepare successor handoff routing from the primary agent."
HANDOFF_SUCCESSOR_TODO = "Continue successor handoff routing from the side agent."
UNCLAIMED_SOURCE_TODO = "Prepare an unclaimed successor routing check."
UNCLAIMED_SUCCESSOR_TODO = "Continue the unclaimed successor routing check."


def write_fixture(root: Path) -> tuple[Path, Path]:
    root = root.resolve()
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Exercise todo lifecycle transitions.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "## Completed Work Archive\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="todo-lifecycle-fixture",
        adapter_kind="generic_project_goal_v0",
        registered_agents=["codex-main-control", "codex-side-bypass"],
        quota_allowed_slots=None,
        extra_goal_fields={
            "creation_operation_id": f"goal-create:{GOAL_ID}",
            "coordination": {
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", "codex-side-bypass"],
                "storage_target": {
                    "schema_version": "loopx_new_goal_storage_target_v1",
                    "provider": "file",
                    "handoff_mode": "soft_claim",
                },
            },
        },
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    initialized = initialize_goal_storage_target(
        runtime,
        registry["goals"][0],
        registry_path=registry_path,
    )
    if not isinstance(initialized, dict) or (
        initialized.get("authority_initialized") is not True
        or initialized.get("legacy_fallback_used") is not False
    ):
        raise AssertionError("fixture did not initialize canonical File authority")
    return registry_path, state_file


def run_cli(registry_path: Path, *args: str) -> dict:
    returncode, payload = run_fixture_cli(registry_path, args)
    if returncode != 0:
        raise AssertionError(json.dumps(payload, ensure_ascii=False))
    payload.pop("_returncode", None)
    return payload


def run_cli_error(registry_path: Path, *args: str) -> dict:
    returncode, payload = run_fixture_cli(registry_path, args)
    assert returncode == 1, payload
    return payload


def run_fixture_cli(
    registry_path: Path,
    args: tuple[str, ...],
) -> tuple[int, dict]:
    argv = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root_from_registry(registry_path)),
        "--format",
        "json",
        *args,
    ]
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            returncode = loopx_cli_main(argv)
        except SystemExit as exc:
            returncode = int(exc.code or 0)
    if not stdout.getvalue().strip():
        raise AssertionError(f"empty CLI output for {argv!r}: {stderr.getvalue()}")
    payload = json.loads(stdout.getvalue())
    assert isinstance(payload, dict), payload
    payload["_returncode"] = returncode
    return returncode, payload


def parsed_items(state_file: Path) -> list[dict]:
    fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    return fields["agent_todos"]["items"]


def parsed_agent_summary(state_file: Path) -> dict:
    fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    return fields["agent_todos"]


def assert_configured_peer_handoff() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-peer-handoff-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        coordination = registry["goals"][0]["coordination"]
        coordination["registered_agents"].append("codex-side-reviewer")
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        side_handoff_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            SIDE_HANDOFF_SOURCE_TODO,
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        side_handoff_completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_handoff_added["todo_id"],
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "side-worktree-contract-diff",
            "--next-agent-todo",
            SIDE_HANDOFF_TODO,
            "--next-claimed-by",
            "codex-side-reviewer",
        )
        assert side_handoff_completed["changed"] is True, side_handoff_completed
        side_handoff_successor_id = side_handoff_completed["next_todos"][0]["todo_id"]
        assert side_handoff_completed["next_todos"][0]["claimed_by"] == "codex-side-reviewer", (
            side_handoff_completed
        )
        assert side_handoff_completed["next_todos"][0].get("blocks_agent") is None, side_handoff_completed
        assert side_handoff_completed["next_todos"][0]["unblocks_todo_id"] == side_handoff_added["todo_id"], (
            side_handoff_completed
        )
        side_handoff_successor = next(
            item for item in parsed_items(state_file) if item["todo_id"] == side_handoff_successor_id
        )
        assert not side_handoff_successor.get("action_kind"), side_handoff_successor
        assert side_handoff_successor["claimed_by"] == "codex-side-reviewer", side_handoff_successor

    with tempfile.TemporaryDirectory(prefix="loopx-explicit-review-handoff-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        coordination = registry["goals"][0]["coordination"]
        coordination["registered_agents"].append("codex-side-reviewer")
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        review_source_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            SIDE_HANDOFF_SOURCE_TODO,
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        explicit_primary_review = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            review_source_added["todo_id"],
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "side-worktree-contract-diff",
            "--next-agent-todo",
            REVIEW_TODO,
            "--next-claimed-by",
            "codex-main-control",
            "--next-action-kind",
            "review_merge",
            "--next-continuation-policy",
            "independent_handoff",
            "--next-excluded-agent",
            "codex-side-bypass",
        )
        assert explicit_primary_review["changed"] is True, explicit_primary_review
        review_successor = explicit_primary_review["next_todos"][0]
        assert review_successor["claimed_by"] == "codex-main-control", explicit_primary_review
        assert review_successor["action_kind"] == "review_merge", explicit_primary_review
        assert review_successor["continuation_policy"] == "independent_handoff", explicit_primary_review
        assert review_successor["excluded_agents"] == ["codex-side-bypass"], explicit_primary_review
        assert review_successor.get("blocks_agent") is None, explicit_primary_review
        assert review_successor["unblocks_todo_id"] == review_source_added["todo_id"], explicit_primary_review
        review_successor_item = next(
            item for item in parsed_items(state_file) if item["todo_id"] == review_successor["todo_id"]
        )
        assert review_successor_item["claimed_by"] == "codex-main-control", review_successor_item
        assert review_successor_item["action_kind"] == "review_merge", review_successor_item
        assert review_successor_item["continuation_policy"] == "independent_handoff", review_successor_item
        assert review_successor_item["excluded_agents"] == ["codex-side-bypass"], review_successor_item

    with tempfile.TemporaryDirectory(prefix="loopx-review-handoff-self-smoke-") as tmp:
        root = Path(tmp)
        registry_path, _state_file = write_fixture(root)
        side_handoff_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            SIDE_HANDOFF_SOURCE_TODO,
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        same_agent_handoff = run_cli_error(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            side_handoff_added["todo_id"],
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "side-worktree-contract-diff",
            "--next-agent-todo",
            SIDE_HANDOFF_TODO,
            "--next-claimed-by",
            "codex-side-bypass",
            "--next-continuation-policy",
            "independent_handoff",
            "--next-excluded-agent",
            "codex-side-bypass",
        )
        assert "cannot also appear in excluded_agents" in (
            same_agent_handoff["error"]
        ), same_agent_handoff

    with tempfile.TemporaryDirectory(prefix="loopx-advisory-profile-handoff-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        coordination = registry["goals"][0]["coordination"]
        coordination["registered_agents"].append("codex-product-capability")
        coordination["agent_profiles"] = {
            "codex-product-capability": {
                "schema_version": "agent_profile_v1",
                "profile_role": "product-validation",
            }
        }
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        profile_handoff_added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            SIDE_HANDOFF_SOURCE_TODO,
            "--claimed-by",
            "codex-product-capability",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        profile_handoff_completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            profile_handoff_added["todo_id"],
            "--claimed-by",
            "codex-product-capability",
            "--agent-id",
            "codex-product-capability",
            "--evidence",
            "product-capability-runtime-contract-diff",
            "--next-agent-todo",
            SIDE_HANDOFF_TODO,
            "--next-claimed-by",
            "codex-main-control",
        )
        assert profile_handoff_completed["changed"] is True, profile_handoff_completed
        assert profile_handoff_completed["next_todos"][0]["claimed_by"] == "codex-main-control", (
            profile_handoff_completed
        )
        assert profile_handoff_completed["next_todos"][0].get("blocks_agent") is None, profile_handoff_completed
        assert profile_handoff_completed["next_todos"][0]["unblocks_todo_id"] == profile_handoff_added["todo_id"], (
            profile_handoff_completed
        )
        profile_handoff_successor = next(
            item
            for item in parsed_items(state_file)
            if item["todo_id"] == profile_handoff_completed["next_todos"][0]["todo_id"]
        )
        assert profile_handoff_successor["claimed_by"] == "codex-main-control", profile_handoff_successor


def assert_no_followup_cli_metadata() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-no-followup-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)
        added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Review completed work that intentionally needs no successor.",
            "--claimed-by",
            "codex-main-control",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "review",
        )
        missing_rationale = run_cli_error(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            added["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--no-follow-up",
        )
        assert "--no-follow-up requires" in missing_rationale["error"], missing_rationale
        updated = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            added["todo_id"],
            "--claimed-by",
            "codex-main-control",
            "--agent-id",
            "codex-main-control",
            "--no-follow-up",
            "--note",
            "No rollout or follow-up is needed after validation.",
        )
        assert updated["changed"] is True, updated
        item = next(item for item in parsed_items(state_file) if item["todo_id"] == added["todo_id"])
        assert item["status"] == "done", item
        assert item["no_followup"] is True, item


def assert_complete_links_existing_successor() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-existing-successor-complete-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)
        source = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Ship a side-agent slice that already has its next lane todo.",
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        successor = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            SIDE_CONTINUATION_TODO,
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "contract_refine",
        )
        source_id = source["todo_id"]
        successor_id = successor["todo_id"]

        mixed_successor_modes = run_cli_error(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            source_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "self-merged commit ghi789 after focused validation",
            "--self-merged",
            "--successor-todo-id",
            successor_id,
            "--next-agent-todo",
            "Duplicate successor should be rejected.",
        )
        assert "--successor-todo-id links existing work" in mixed_successor_modes["error"], (
            mixed_successor_modes
        )

        completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            source_id,
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "self-merged commit ghi789 after focused validation",
            "--self-merged",
            "--successor-todo-id",
            successor_id,
        )
        assert completed["changed"] is True, completed
        assert completed["completion_policy"]["self_merged"] is True, completed
        assert completed["next_todos"] == [], completed
        assert completed["successor_todo_ids"] == [successor_id], completed

        source_item = next(item for item in parsed_items(state_file) if item["todo_id"] == source_id)
        assert source_item["status"] == "done", source_item
        assert source_item["successor_todo_ids"] == [successor_id], source_item
        successor_item = next(item for item in parsed_items(state_file) if item["todo_id"] == successor_id)
        assert successor_item["status"] == "open", successor_item
        assert successor_item["claimed_by"] == "codex-side-bypass", successor_item

        summary = parsed_agent_summary(state_file)
        assert summary.get("completed_without_successor_count", 0) == 0, summary
        assert "todo_succession_warning" not in summary, summary


def assert_same_title_completion_creates_fresh_successor() -> None:
    title = "[P1] Continue the next non-benchmark LoopX state-machine canary/refactor slice."
    with tempfile.TemporaryDirectory(prefix="loopx-same-title-successor-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)
        added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            title,
            "--claimed-by",
            "codex-side-bypass",
            "--task-class",
            "advancement_task",
            "--action-kind",
            "state_machine_canary_refactor",
        )
        completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            added["todo_id"],
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
            "--evidence",
            "self-merged commit same-title after focused validation",
            "--self-merged",
            "--next-agent-todo",
            title,
            "--next-claimed-by",
            "codex-side-bypass",
            "--next-action-kind",
            "state_machine_canary_refactor",
        )
        assert completed["changed"] is True, completed
        assert completed["next_todos"][0]["status"] == "open", completed
        successor_id = completed["next_todos"][0]["todo_id"]
        assert successor_id != added["todo_id"], completed
        assert completed["next_todos"][0]["unblocks_todo_id"] == added["todo_id"], completed

        items = parsed_items(state_file)
        source_item = next(item for item in items if item["todo_id"] == added["todo_id"])
        successor_item = next(item for item in items if item["todo_id"] == successor_id)
        assert source_item["status"] == "done", source_item
        assert successor_item["status"] == "open", successor_item
        assert successor_item["text"] == title, successor_item
        assert successor_item["unblocks_todo_id"] == added["todo_id"], successor_item

        summary = parsed_agent_summary(state_file)
        assert summary.get("completed_without_successor_count", 0) == 0, summary
        assert "todo_succession_warning" not in summary, summary
