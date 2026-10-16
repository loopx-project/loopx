#!/usr/bin/env python3
"""Qualify canonical Todo previews, once-only commit, actor checks and CAS."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    import_fixture_authority,
    run_json_cli,
    run_json_cli_result,
    write_fixture_registry,
)

GOAL_ID = "todo-write-correctness-goal"
AGENT_ID = "codex-product-capability"
TODO_TEXT = "Preview a todo write correctness packet before mutating state."


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
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
        "# Todo Write Correctness Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] Existing todo for update preview.\n"
        "  <!-- loopx:todo todo_id=todo_existing status=open task_class=advancement_task -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="todo-write-correctness-fixture",
        adapter_kind="generic_project_goal_v0",
        registered_agents=("codex-main-control", AGENT_ID),
        quota_allowed_slots=None,
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return registry_path, state_file, project


def run_cli(registry_path: Path, *args: str, as_json: bool = True) -> dict | str:
    if as_json:
        return run_json_cli(
            *args,
            registry_path=registry_path,
            include_returncode=False,
        )
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
    ]
    command.extend(args)
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-write-correctness-") as tmp:
        registry, state, project = write_fixture(Path(tmp))

        def readback() -> dict:
            return run_cli(registry, "todo", "list", "--goal-id", GOAL_ID)

        def unchanged(before: dict, document: bytes) -> None:
            after = readback()
            assert after["todos"] == before["todos"], after
            assert after["authority_read"] == before["authority_read"], after
            assert state.read_bytes() == document

        before, document = readback(), state.read_bytes()
        create = ("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
                  "--text", TODO_TEXT, "--claimed-by", AGENT_ID,
                  "--operation-id", "preview-create")
        first = run_cli(registry, *create, "--dry-run")
        repeat = run_cli(registry, *create, "--dry-run")
        for planned in (first, repeat):
            assert planned["status"] == "planned" and planned["dry_run"] is True, planned
            assert planned["source_authority"] == "file_v0", planned
            assert planned["decision_read_from_provider"] is True, planned
            assert planned["legacy_fallback_used"] is False, planned
            assert str(project) not in json.dumps(planned), planned
        assert first["todo_id"] == repeat["todo_id"]
        assert first["operation_id"] == repeat["operation_id"] == "preview-create"
        unchanged(before, document)
        # A preview must not consume the original operation or mint its receipt.
        committed = run_cli(registry, *create)
        assert committed["todo_id"] == first["todo_id"] and committed["status"] == "applied", committed
        replay = run_cli(registry, *create)
        assert replay["provider_revision"] == committed["provider_revision"], replay
        assert len([t for t in readback()["todos"] if t["todo_id"] == first["todo_id"]]) == 1

        before, document = readback(), state.read_bytes()
        update = ("todo", "update", "--goal-id", GOAL_ID, "--todo-id", first["todo_id"],
                  "--agent-id", AGENT_ID, "--note", "Preview update without writing state.")
        planned = run_cli(registry, *update, "--dry-run")
        assert planned["status"] == "planned", planned
        unchanged(before, document)

        # The provider revision, rather than editable Markdown bytes, owns CAS.
        revision = before["authority_read"]["provider_revision"]
        run_cli(registry, "todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
                "--text", "A later independently committed Todo.")
        before, document = readback(), state.read_bytes()
        for dry_run in (("--dry-run",), ()):
            code, stale = run_json_cli_result(
                *update, "--update-operation-id", "stale-preview",
                "--update-expected-provider-revision", revision, *dry_run,
                registry_path=registry)
            assert code == 1 and stale["error_code"] == "provider_revision_mismatch", stale
            unchanged(before, document)
            code, foreign = run_json_cli_result(
                "todo", "update", "--goal-id", GOAL_ID, "--todo-id", first["todo_id"],
                "--agent-id", "codex-main-control", "--note", "Foreign actor edit.",
                *dry_run, registry_path=registry)
            assert code == 1 and "claimed_by" in foreign["error"], foreign
            unchanged(before, document)
        applied = run_cli(registry, *update)
        assert applied["status"] == "applied", applied
        todo = next(t for t in readback()["todos"] if t["todo_id"] == first["todo_id"])
        assert todo["note"] == "Preview update without writing state.", todo

    print("todo-write-correctness-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
