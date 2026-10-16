#!/usr/bin/env python3
"""Smoke-test LoopX Chat's review envelope and preview-locked Todo write."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.chat import (  # noqa: E402
    TodoReviewPreviewConflict,
    apply_todo_review_preview,
    build_todo_review_preview,
    parse_agent_response,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from loopx.control_plane.todos.provider_projection import project_current_canonical_todos  # noqa: E402


GOAL_ID = "loopx-chat-contract-smoke"
TODO_TEXT = "[P0] Add a reviewable Goal Studio interaction."


def write_fixture(root: Path, *, todo_anchor: bool = True) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Keep LoopX Chat reviewable.\n\n"
        "## Next Action\n\n"
        "- Review the next bounded proposal.\n" + ("\n## Agent Todo\n" if todo_anchor else ""),
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "domain": "product-engineering",
                        "status": "active",
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return registry_path, state_file, project


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-chat-contract-") as raw_tmp:
        root = Path(raw_tmp)
        registry_path, state_file, project = write_fixture(root)

        raw_response = (
            "draft text that must not become authority\n"
            "<loopx-review-json>"
            + json.dumps(
                {
                    "schema_version": "loopx_chat_agent_response_v0",
                    "message": f"I inspected {project} and recommend one bounded step.",
                    "proposals": [
                        {
                            "kind": "todo",
                            "text": TODO_TEXT,
                            "priority": "P0",
                            "rationale": "It closes the first human review loop.",
                        }
                    ],
                    "protected_action": {
                        "operation": "merge",
                        "target": "PR #123",
                        "summary": "Prepare a protected merge preview.",
                    },
                    "gate": None,
                }
            )
            + "</loopx-review-json>"
        )
        parsed = parse_agent_response(raw_response, protected_paths=[project, root])
        assert parsed["schema_version"] == "loopx_chat_agent_response_v0", parsed
        assert parsed["message"] == "I inspected [project] and recommend one bounded step.", parsed
        assert parsed["proposals"] == [
            {
                "kind": "todo",
                "text": TODO_TEXT,
                "priority": "P0",
                "rationale": "It closes the first human review loop.",
            }
        ], parsed
        assert parsed["protected_action"] == {
            "operation": "merge",
            "target": "PR #123",
            "summary": "Prepare a protected merge preview.",
        }, parsed
        assert parsed["gate"] is None, parsed
        assert str(project) not in json.dumps(parsed), parsed

        fallback = parse_agent_response(
            f"The host stopped at {root / 'private' / 'gate.json'}.",
            protected_paths=[project, root],
        )
        assert fallback["message"] == "The host stopped at [local-path].", fallback
        assert fallback["proposals"] == [], fallback
        assert fallback["protected_action"] is None, fallback

        unsupported_action = parse_agent_response(
            "<loopx-review-json>"
            + json.dumps(
                {
                    "message": "Unsupported action stays conversational.",
                    "proposals": [],
                    "protected_action": {
                        "operation": "shell",
                        "target": "rm -rf target",
                        "summary": "Run a command.",
                    },
                    "gate": None,
                }
            )
            + "</loopx-review-json>"
        )
        assert unsupported_action["protected_action"] is None, unsupported_action

        local_target = parse_agent_response(
            "<loopx-review-json>"
            + json.dumps(
                {
                    "message": "Local targets stay conversational.",
                    "proposals": [],
                    "protected_action": {
                        "operation": "delete",
                        "target": str(project / "private.txt"),
                        "summary": "Delete one file.",
                    },
                    "gate": None,
                }
            )
            + "</loopx-review-json>",
            protected_paths=[project, root],
        )
        assert local_target["protected_action"] is None, local_target

        malformed = parse_agent_response(
            'tool narration that must stay hidden\n<loopx-review-json>{"message":"Clean answer.",}</loopx-review-json>',
        )
        assert malformed["message"] == "Clean answer.", malformed
        assert malformed["protected_action"] is None, malformed
        assert "tool narration" not in malformed["message"], malformed

        original = state_file.read_text(encoding="utf-8")
        authority_before = read_canonical_todos_if_promoted(runtime_root=root / "runtime", goal_id=GOAL_ID)
        preview = build_todo_review_preview(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            text=TODO_TEXT,
        )
        assert preview["ok"] is True, preview
        assert preview["dry_run"] is True, preview
        assert preview["preview_id"], preview
        assert preview["todo"] == {
            "goal_id": GOAL_ID,
            "todo_id": preview["todo"]["todo_id"],
            "text": TODO_TEXT,
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "loopx_chat_proposal",
        }, preview
        assert state_file.read_text(encoding="utf-8") == original
        assert read_canonical_todos_if_promoted(runtime_root=root / "runtime", goal_id=GOAL_ID) == authority_before
        assert "state_file" not in json.dumps(preview), preview
        assert "project" not in json.dumps(preview), preview

        try:
            apply_todo_review_preview(
                registry_path=registry_path,
                goal_id=GOAL_ID,
                text=TODO_TEXT,
                preview_id="stale-preview",
            )
        except TodoReviewPreviewConflict as exc:
            assert "stale todo preview" in str(exc), exc
            no_write_receipt = exc.receipt
            assert no_write_receipt["schema_version"] == "loopx_chat_todo_no_write_receipt_v0", no_write_receipt
            assert no_write_receipt["status"] == "not_applied", no_write_receipt
            assert no_write_receipt["outcome"] == "preview_stale", no_write_receipt
            assert no_write_receipt["write_attempted"] is False, no_write_receipt
            assert no_write_receipt["goal_id"] == GOAL_ID, no_write_receipt
            assert no_write_receipt["receipt_id"], no_write_receipt
            assert str(project) not in json.dumps(no_write_receipt), no_write_receipt
        else:
            raise AssertionError("stale preview must be rejected")
        assert state_file.read_text(encoding="utf-8") == original
        assert read_canonical_todos_if_promoted(runtime_root=root / "runtime", goal_id=GOAL_ID) == authority_before

        applied = apply_todo_review_preview(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            text=TODO_TEXT,
            preview_id=preview["preview_id"],
        )
        assert applied["ok"] is True, applied
        assert applied["applied"] is True, applied
        # Canonical creation allocates its id at commit; preview locks the
        # intent and provider revision, not a dry-run's provisional id.
        for field in ("goal_id", "text", "status", "task_class", "action_kind"):
            assert applied["todo"][field] == preview["todo"][field], applied
        committed = read_canonical_todos_if_promoted(runtime_root=root / "runtime", goal_id=GOAL_ID)
        assert committed["decision_read_from_provider"] is True and committed["legacy_fallback_used"] is False
        assert len(committed["todos"]) == 1, committed
        assert committed["todos"][0]["todo_id"] == applied["todo"]["todo_id"], committed
        assert committed["todos"][0]["text"] == TODO_TEXT, committed
        receipt = applied["receipt"]
        assert receipt["schema_version"] == "loopx_chat_todo_receipt_v0", receipt
        assert receipt["receipt_id"], receipt
        assert receipt["preview_id"] == preview["preview_id"], receipt
        assert receipt["goal_id"] == GOAL_ID, receipt
        assert receipt["todo_id"] == applied["todo"]["todo_id"], receipt
        assert receipt["status"] == "applied", receipt
        assert receipt["outcome"] == "todo_added", receipt
        assert receipt["already_exists"] is False, receipt
        assert receipt["preview_revision"], receipt
        assert str(project) not in json.dumps(receipt), receipt
        assert state_file.read_text(encoding="utf-8").count(TODO_TEXT) == 1

        # Display edits cannot replace the committed record or create a
        # duplicate. Restore the document before another explicit write.
        display = state_file.read_text(encoding="utf-8")
        state_file.write_text(display.replace(TODO_TEXT, "Spoofed display proposal"), encoding="utf-8")
        assert read_canonical_todos_if_promoted(runtime_root=root / "runtime", goal_id=GOAL_ID) == committed

        state_file.write_text(display, encoding="utf-8")

        retry_preview = build_todo_review_preview(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            text=TODO_TEXT,
        )
        assert retry_preview["already_exists"] is True, retry_preview
        assert retry_preview["todo"]["todo_id"] == receipt["todo_id"], retry_preview
        retry_applied = apply_todo_review_preview(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            text=TODO_TEXT,
            preview_id=retry_preview["preview_id"],
        )
        retry_receipt = retry_applied["receipt"]
        assert retry_receipt["outcome"] == "todo_already_exists", retry_receipt
        assert retry_receipt["already_exists"] is True, retry_receipt
        assert retry_receipt["todo_id"] == receipt["todo_id"], retry_receipt
        assert state_file.read_text(encoding="utf-8").count(TODO_TEXT) == 1
        assert read_canonical_todos_if_promoted(runtime_root=root / "runtime", goal_id=GOAL_ID) == committed

        # An anchorless historical document is retained as a separate display
        # recovery case. A successful canonical commit cannot repair its prose
        # implicitly or turn display failure into a second business write.
        recovery_root = root / "display-recovery"
        recovery_registry, recovery_state, _ = write_fixture(recovery_root, todo_anchor=False)
        original_narrative = recovery_state.read_text(encoding="utf-8")
        recovery_preview = build_todo_review_preview(registry_path=recovery_registry, goal_id=GOAL_ID, text=TODO_TEXT)
        recovery_applied = apply_todo_review_preview(registry_path=recovery_registry, goal_id=GOAL_ID,
                                                   text=TODO_TEXT, preview_id=recovery_preview["preview_id"])
        assert recovery_applied["applied"] is True, recovery_applied
        assert recovery_state.read_text(encoding="utf-8") == original_narrative
        accepted = read_canonical_todos_if_promoted(runtime_root=recovery_root / "runtime", goal_id=GOAL_ID)
        assert len(accepted["todos"]) == 1, accepted
        try:
            project_current_canonical_todos(registry_path=recovery_registry,
                                            runtime_root=recovery_root / "runtime", goal_id=GOAL_ID)
        except ValueError as error:
            assert "no projection anchor" in str(error), error
        else:
            raise AssertionError("an anchorless document must refuse implicit projection")
        assert recovery_state.read_text(encoding="utf-8") == original_narrative
        recovery_state.write_text(original_narrative + "\n## Agent Todo\n", encoding="utf-8")
        delivered = project_current_canonical_todos(registry_path=recovery_registry,
            runtime_root=recovery_root / "runtime", goal_id=GOAL_ID,
            expected_provider_revision=accepted["provider_revision"])
        assert delivered["status"] == "delivered" and delivered["narrative_preserved"] is True, delivered
        assert recovery_state.read_text(encoding="utf-8").startswith(original_narrative)
        assert recovery_state.read_text(encoding="utf-8").count(TODO_TEXT) == 1
        assert read_canonical_todos_if_promoted(runtime_root=recovery_root / "runtime", goal_id=GOAL_ID) == accepted

    print("loopx-chat-contract-smoke: ok")


if __name__ == "__main__":
    main()
