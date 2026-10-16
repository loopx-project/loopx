#!/usr/bin/env python3
"""Smoke-test the public-safe global manager CLI commands."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "control_plane"))

from canonical_authority_fixture import initialize_canonical_authority  # noqa: E402
from loopx.control_plane.coordination.authority_source_capture import (  # noqa: E402
    build_todo_runtime_shadow_projection,
    capture_todo_archive_dependencies,
)
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from loopx.control_plane.todos.goal_todo_projection import project_goal_todo_items  # noqa: E402

GOAL_ID = "smoke-goal"
RUNNABLE_GOAL_ID = "smoke-goal-runnable"
DEFERRED_GOAL_ID = "smoke-goal-deferred"
AGENT_ID = "codex-smoke"
BLOCKED_TODO_ID = "todo_blocked"
RUNNABLE_REVIEW_TODO_ID = "todo_runnable_review"
DEFERRED_TODO_ID = "todo_deferred_ready"
DEFERRED_PREREQUISITE_ID = "todo_deferred_prerequisite"
PRIVATE_PATTERNS = [
    re.compile(r"/" + r"Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/" + r"private/"),
    re.compile(r"/tmp/"),
    re.compile(r"/var/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
]


def synthetic_private_markers() -> dict[str, str]:
    return {
        "first_path": "/" + "Users" + "/fixture/Documents/private-first.txt",
        "second_path": "/" + "Users" + "/fixture/Documents/private-second.txt",
        "credential": "tok" + "en=" + "abcdefghijklmnop",
        "log": "raw-" + "log-private-marker",
        "transcript": "raw-" + "transcript-private-marker",
    }


def assert_public_safe(payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise AssertionError(
                f"global manager payload leaked private pattern {pattern.pattern!r}"
            )
    if "/loopx-summary-all" in text:
        raise AssertionError("global manager payload exposed a superseded alias")


def run_cli(
    *,
    registry: Path,
    runtime: Path,
    command: str,
    output_format: str,
    command_args: list[str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            output_format,
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            command,
            *(command_args or []),
        ],
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def write_fixture(
    root: Path, *, provider: str | None = None,
) -> tuple[Path, Path, Path, Path, list[Path]]:
    runtime = root / "runtime"
    registry = root / "registry.global.json"
    projects = {
        GOAL_ID: root / "project-blocked",
        RUNNABLE_GOAL_ID: root / "project-runnable",
        DEFERRED_GOAL_ID: root / "project-deferred",
    }
    states = {
        goal_id: project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        for goal_id, project in projects.items()
    }
    states[GOAL_ID].parent.mkdir(parents=True)
    states[GOAL_ID].write_text(
        "---\n"
        "status: active\n"
        f"goal_id: {GOAL_ID}\n"
        "updated_at: 2026-08-11T01:00:00+00:00\n"
        "---\n\n"
        "# Global Manager CLI Smoke\n\n"
        "## User Todo\n\n"
        "- [ ] [P0] Approve the public-safe blocked delivery.\n"
        "  <!-- loopx:todo todo_id=todo_gate_approve status=open "
        "task_class=user_gate action_kind=approve "
        f"blocks_agent={AGENT_ID} unblocks_todo_id={BLOCKED_TODO_ID} -->\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Deliver the approved public-safe change.\n"
        f"  <!-- loopx:todo todo_id={BLOCKED_TODO_ID} status=open "
        f"task_class=advancement_task claimed_by={AGENT_ID} -->\n\n"
        "## Next Action\n\n"
        "- Approve the public-safe blocked delivery.\n",
        encoding="utf-8",
    )
    states[RUNNABLE_GOAL_ID].parent.mkdir(parents=True)
    states[RUNNABLE_GOAL_ID].write_text(
        "---\n"
        "status: active\n"
        f"goal_id: {RUNNABLE_GOAL_ID}\n"
        "updated_at: 2026-08-10T00:00:00+00:00\n"
        "---\n\n"
        "# Runnable Review Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Review the public-safe todo projection.\n"
        f"  <!-- loopx:todo todo_id={RUNNABLE_REVIEW_TODO_ID} status=open "
        "task_class=advancement_task action_kind=review_pr "
        f"claimed_by={AGENT_ID} -->\n\n"
        "## Next Action\n\n"
        "- Review the public-safe todo projection.\n",
        encoding="utf-8",
    )
    states[DEFERRED_GOAL_ID].parent.mkdir(parents=True)
    states[DEFERRED_GOAL_ID].write_text(
        "---\n"
        "status: active\n"
        f"goal_id: {DEFERRED_GOAL_ID}\n"
        "updated_at: 2026-08-10T00:00:00+00:00\n"
        "---\n\n"
        "# Deferred Ready Fixture\n\n"
        "## Agent Todo\n\n"
        "- [-] [P0] Resume the ready public-safe successor.\n"
        f"  <!-- loopx:todo todo_id={DEFERRED_TODO_ID} status=deferred "
        f"task_class=advancement_task claimed_by={AGENT_ID} "
        f"resume_when=todo_done:{DEFERRED_PREREQUISITE_ID} -->\n"
        "- [ ] [P1] Keep the fallback lane available.\n"
        "  <!-- loopx:todo todo_id=todo_deferred_fallback status=open "
        f"task_class=advancement_task claimed_by={AGENT_ID} -->\n\n"
        "## Completed Work Archive\n\n"
        "- [x] [P0] Complete the deferred prerequisite.\n"
        f"  <!-- loopx:todo todo_id={DEFERRED_PREREQUISITE_ID} status=done "
        "task_class=advancement_task -->\n\n"
        "## Next Action\n\n"
        "- Resume the ready public-safe successor.\n",
        encoding="utf-8",
    )

    goals = []
    for goal_id, project in projects.items():
        goals.append(
            {
                "id": goal_id,
                "objective": f"Smoke global manager commands for {goal_id}.",
                "domain": "loopx-smoke",
                "status": "active",
                "repo": str(project),
                "state_file": str(states[goal_id].relative_to(project)),
                "adapter": {
                    "kind": "read_only_project_map_v0",
                    "status": "connected-read-only",
                },
                "coordination": {
                    "agent_model": "peer_v1",
                    "registered_agents": [AGENT_ID],
                },
            }
        )
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": goals,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if provider:
        for goal in goals:
            state = states[goal["id"]]
            state_text = state.read_text(encoding="utf-8")
            todos = project_goal_todo_items(
                goal, state_text=state_text, state_path=state, rollout_events=[],
            )
            # Deferred readiness depends on the completed archive, too.
            todos = capture_todo_archive_dependencies(todos, state_text)
            projection = build_todo_runtime_shadow_projection(
                goal_id=goal["id"], todos=todos, handoff_mode="soft_claim",
            )
            initialize_canonical_authority(
                runtime, goal["id"], projection, state_path=state, provider=provider,
            )
    run_index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    run_index.parent.mkdir(parents=True)
    run_index.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-10T00:00:00+00:00",
                "classification": "smoke_progress",
                "recommended_action": "Continue the next public-safe smoke step.",
                "json_exists": True,
                "markdown_exists": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    risk_input = root / "public-risk-input.md"
    risk_input.write_text(
        "\n".join(synthetic_private_markers().values()) + "\n",
        encoding="utf-8",
    )
    safe_input = root / "public-safe-input.md"
    safe_input.write_text("# Public-safe manager fixture\n", encoding="utf-8")
    return (
        registry,
        runtime,
        risk_input,
        safe_input,
        [*states.values(), run_index, risk_input, safe_input],
    )


def snapshot_files(paths: list[Path]) -> dict[Path, tuple[bytes, int]]:
    return {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in paths
    }


def assert_summary_payload(payload: dict[str, object]) -> None:
    assert payload["schema_version"] == "global_manager_command_response_v0", payload
    request = payload["request"]
    assert request["command"] == "/loopx-global-summary", request
    assert "/loop-global-summary" in request["legacy_aliases"], request
    assert request["cli_command"] == "loopx global-summary", request
    assert request["privacy_mode"] == "public_safe_summary", request
    assert request["dry_run"] is True, request
    assert payload["boundary"]["absolute_paths_recorded"] is False, payload["boundary"]
    assert "groups" in payload and "recent_progress" in payload["groups"], payload
    assert_public_safe(payload)


def assert_gates_payload(payload: dict[str, object]) -> None:
    assert payload["ok"] is True, payload
    request = payload["request"]
    assert request["command"] == "/loopx-global-gates", request
    assert request["legacy_aliases"] == ["/loop-global-gates"], request
    assert request["cli_command"] == "loopx global-gates", request
    assert request["dry_run"] is True, request
    assert payload["summary"]["open_gate_count"] == 1, payload
    assert payload["gates"][0]["blocks"] == [BLOCKED_TODO_ID], payload
    assert_public_safe(payload)


def assert_gates_markdown(markdown: str, *, private_root: Path) -> None:
    assert "# LoopX Global Gates" in markdown, markdown
    assert "`/loopx-global-gates`" in markdown, markdown
    assert f"`{GOAL_ID}`" in markdown, markdown
    assert f"blocks=`{BLOCKED_TODO_ID}`" in markdown, markdown
    assert "Recent Progress" not in markdown, markdown
    assert "Risks" not in markdown, markdown
    assert str(private_root) not in markdown, markdown


def assert_todos_payload(payload: dict[str, object]) -> None:
    assert payload["ok"] is True, payload
    request = payload["request"]
    assert request["command"] == "/loopx-global-todos", request
    assert request["legacy_aliases"] == ["/loop-global-todos"], request
    assert request["cli_command"] == "loopx global-todos", request
    assert request["dry_run"] is True, request
    summary = payload["summary"]
    assert summary["matched_todo_count"] >= 3, payload
    assert summary["returned_todo_count"] >= 3, payload
    groups = payload["groups"]
    assert groups["runnable"], payload
    assert groups["blocked"], payload
    assert groups["deferred_ready"], payload
    assert groups["review"], payload
    review_ids = {item["todo_id"] for item in groups["review"]}
    runnable_ids = {item["todo_id"] for item in groups["runnable"]}
    assert RUNNABLE_REVIEW_TODO_ID in review_ids & runnable_ids, payload
    assert_public_safe(payload)


def assert_todos_markdown(markdown: str, *, private_root: Path) -> None:
    assert "# LoopX Global Todos" in markdown, markdown
    assert "`/loopx-global-todos`" in markdown, markdown
    assert "## Runnable" in markdown, markdown
    assert "## Deferred Ready" in markdown, markdown
    assert "## Blocked" in markdown, markdown
    assert "## Review" in markdown, markdown
    assert "Review is an overlapping work-kind facet" in markdown, markdown
    assert str(private_root) not in markdown, markdown


def assert_risks_payload(
    payload: dict[str, object], *, unresolved_failure: bool,
) -> None:
    assert payload["ok"] is True, payload
    request = payload["request"]
    assert request["command"] == "/loopx-global-risks", request
    assert request["legacy_aliases"] == ["/loop-global-risks"], request
    assert request["cli_command"] == "loopx global-risks", request
    assert request["time_range"] == "1h", request
    assert request["dry_run"] is True, request

    risks = payload["risks"]
    boundary_rows = [
        row for row in risks if row["kind"] == "public_boundary_violation"
    ]
    assert len(boundary_rows) >= 2, payload
    assert len({row["occurrence_id"] for row in boundary_rows}) == len(boundary_rows)
    assert all(row["category"] == "boundary_warning" for row in boundary_rows)
    assert payload["groups"]["rollback_candidates"] == [], payload
    assert payload["summary"]["rollback_candidate_count"] == 0, payload
    assert payload["summary"]["rollback_candidates_overlap_risks"] is False, payload
    assert payload["omissions"] == [
        {
            "kind": "rollback_candidate_source_unavailable",
            "reason": (
                "Current projections do not carry rollback trigger and causal linkage."
            ),
        }
    ], payload

    stale_rows = payload["groups"]["stale_runs"]
    if unresolved_failure:
        assert len(stale_rows) == 1, payload
        assert stale_rows[0]["kind"] == "stale_latest_run_projection", stale_rows
        assert stale_rows[0]["goal_id"] == GOAL_ID, stale_rows
    else:
        # A successful current-state read reconciles ordinary stale progress.
        assert stale_rows == [], payload
    assert any(
        warning["reason_code"] == "global_registry_unavailable"
        for warning in payload["source_warnings"]
    ), payload
    text = json.dumps(payload, ensure_ascii=False)
    for marker in synthetic_private_markers().values():
        assert marker not in text, marker
    assert_public_safe(payload)


def assert_risks_markdown(markdown: str, *, private_root: Path) -> None:
    assert "# LoopX Global Risks" in markdown, markdown
    assert "`/loopx-global-risks`" in markdown, markdown
    assert "`1h`" in markdown, markdown
    assert "## Stale Runs" in markdown, markdown
    assert "## Boundary Warnings" in markdown, markdown
    assert "## Failing Checks" in markdown, markdown
    assert "## Rollback Candidates" in markdown, markdown
    assert "No current accepted source proves a rollback candidate" in markdown, markdown
    assert "does not authorize rollback" in markdown, markdown
    assert str(private_root) not in markdown, markdown
    for marker in synthetic_private_markers().values():
        assert marker not in markdown, marker


def assert_error_envelope(root: Path, *, runtime: Path) -> None:
    error_registry = root / "private" / "error-registry"
    error_registry.mkdir(parents=True)
    error_proc = run_cli(
        registry=error_registry,
        runtime=runtime,
        command="global-gates",
        output_format="json",
        check=False,
    )
    assert error_proc.returncode != 0, error_proc
    payload = json.loads(error_proc.stdout)
    assert payload["request"]["command"] == "/loopx-global-gates", payload
    assert str(error_registry) not in error_proc.stdout, error_proc.stdout
    assert "<local-path-redacted>" in error_proc.stdout, error_proc.stdout
    assert_public_safe(payload)

    todos_error_proc = run_cli(
        registry=error_registry,
        runtime=runtime,
        command="global-todos",
        output_format="json",
        check=False,
    )
    assert todos_error_proc.returncode != 0, todos_error_proc
    todos_payload = json.loads(todos_error_proc.stdout)
    assert todos_payload["ok"] is False, todos_payload
    assert todos_payload["request"]["command"] == "/loopx-global-todos", todos_payload
    assert "summary" not in todos_payload, todos_payload
    assert "todos" not in todos_payload, todos_payload
    assert str(error_registry) not in todos_error_proc.stdout, todos_error_proc.stdout
    assert "<local-path-redacted>" in todos_error_proc.stdout, todos_error_proc.stdout
    assert_public_safe(todos_payload)

    for output_format in ("json", "markdown"):
        risks_error_proc = run_cli(
            registry=error_registry,
            runtime=runtime,
            command="global-risks",
            output_format=output_format,
            check=False,
        )
        assert risks_error_proc.returncode != 0, risks_error_proc
        assert str(error_registry) not in risks_error_proc.stdout, risks_error_proc.stdout
        assert "<local-path-redacted>" in risks_error_proc.stdout, risks_error_proc.stdout
        if output_format == "json":
            risks_payload = json.loads(risks_error_proc.stdout)
            assert risks_payload["ok"] is False, risks_payload
            assert risks_payload["error_code"] == "status_collection_unavailable", risks_payload
            assert "risks" not in risks_payload, risks_payload
            assert "groups" not in risks_payload, risks_payload
            assert_public_safe(risks_payload)
        else:
            assert "# LoopX Global Risks Unavailable" in risks_error_proc.stdout


def assert_unhealthy_status_envelope(
    root: Path,
    *,
    registry: Path,
    runtime: Path,
    safe_input: Path,
) -> None:
    unhealthy_registry = root / "unhealthy-registry.json"
    unhealthy_payload = json.loads(registry.read_text(encoding="utf-8"))
    del unhealthy_payload["goals"][0]["domain"]
    unhealthy_registry.write_text(
        json.dumps(unhealthy_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    status_proc = run_cli(
        registry=unhealthy_registry,
        runtime=runtime,
        command="status",
        output_format="json",
        command_args=["--scan-path", str(safe_input)],
        check=False,
    )
    status_payload = json.loads(status_proc.stdout)
    assert status_payload["ok"] is False, status_proc
    assert "attention_queue" in status_payload, status_payload
    assert "error" not in status_payload, status_payload

    gates_proc = run_cli(
        registry=unhealthy_registry,
        runtime=runtime,
        command="global-gates",
        output_format="json",
        command_args=["--scan-path", str(safe_input)],
        check=False,
    )
    assert gates_proc.returncode != 0, gates_proc
    payload = json.loads(gates_proc.stdout)
    assert payload["ok"] is False, payload
    assert payload["error"] == "Global status source unavailable.", payload
    assert "summary" not in payload, payload
    assert "gates" not in payload, payload
    assert "lanes" not in payload, payload
    assert str(root) not in gates_proc.stdout, gates_proc.stdout
    assert_public_safe(payload)

    todos_proc = run_cli(
        registry=unhealthy_registry,
        runtime=runtime,
        command="global-todos",
        output_format="json",
        command_args=["--scan-path", str(safe_input)],
        check=False,
    )
    assert todos_proc.returncode != 0, todos_proc
    todos_payload = json.loads(todos_proc.stdout)
    assert todos_payload["ok"] is False, todos_payload
    assert todos_payload["error"] == "Global status source unavailable.", todos_payload
    assert "summary" not in todos_payload, todos_payload
    assert "todos" not in todos_payload, todos_payload
    assert str(root) not in todos_proc.stdout, todos_proc.stdout
    assert_public_safe(todos_payload)

    risks_proc = run_cli(
        registry=unhealthy_registry,
        runtime=runtime,
        command="global-risks",
        output_format="json",
        command_args=["--scan-path", str(safe_input)],
        check=False,
    )
    assert risks_proc.returncode == 0, risks_proc
    risks_payload = json.loads(risks_proc.stdout)
    assert risks_payload["ok"] is True, risks_payload
    assert risks_payload["summary"]["source_health_ok"] is False, risks_payload
    assert risks_payload["groups"]["failing_checks"], risks_payload
    assert_public_safe(risks_payload)


def run_scenario(provider: str | None) -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-global-manager-smoke-") as tmp:
        root = Path(tmp)
        registry, runtime, risk_input, safe_input, fixture_files = write_fixture(
            root, provider=provider,
        )
        files = [registry, *fixture_files]
        before = snapshot_files(files)
        canonical_before = {
            goal_id: read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal_id)
            for goal_id in (GOAL_ID, RUNNABLE_GOAL_ID, DEFERRED_GOAL_ID)
        }
        assert all((value is not None) == bool(provider) for value in canonical_before.values())

        summary_proc = run_cli(
            registry=registry,
            runtime=runtime,
            command="global-summary",
            output_format="json",
            command_args=[
                "--agent-id",
                AGENT_ID,
                "--limit",
                "5",
                "--scan-path",
                str(safe_input),
            ],
        )
        gates_proc = run_cli(
            registry=registry,
            runtime=runtime,
            command="global-gates",
            output_format="json",
            command_args=[
                "--agent-id",
                AGENT_ID,
                "--limit",
                "5",
                "--scan-path",
                str(safe_input),
            ],
        )
        markdown_proc = run_cli(
            registry=registry,
            runtime=runtime,
            command="global-gates",
            output_format="markdown",
            command_args=[
                "--agent-id",
                AGENT_ID,
                "--limit",
                "5",
                "--scan-path",
                str(safe_input),
            ],
        )
        todos_proc = run_cli(
            registry=registry,
            runtime=runtime,
            command="global-todos",
            output_format="json",
            command_args=[
                "--agent-id",
                AGENT_ID,
                "--limit",
                "5",
                "--scan-path",
                str(safe_input),
            ],
        )
        todos_markdown_proc = run_cli(
            registry=registry,
            runtime=runtime,
            command="global-todos",
            output_format="markdown",
            command_args=[
                "--agent-id",
                AGENT_ID,
                "--limit",
                "5",
                "--scan-path",
                str(safe_input),
            ],
        )
        risks_args = [
            "--agent-id",
            AGENT_ID,
            "--time-range",
            "1h",
            "--limit",
            "8",
            "--scan-path",
            str(risk_input),
        ]
        risks_proc = run_cli(
            registry=registry,
            runtime=runtime,
            command="global-risks",
            output_format="json",
            command_args=risks_args,
        )
        risks_markdown_proc = run_cli(
            registry=registry,
            runtime=runtime,
            command="global-risks",
            output_format="markdown",
            command_args=risks_args,
        )

        assert_summary_payload(json.loads(summary_proc.stdout))
        assert_gates_payload(json.loads(gates_proc.stdout))
        assert_gates_markdown(markdown_proc.stdout, private_root=root)
        assert_todos_payload(json.loads(todos_proc.stdout))
        assert_todos_markdown(todos_markdown_proc.stdout, private_root=root)
        assert_risks_payload(json.loads(risks_proc.stdout), unresolved_failure=False)
        assert_risks_markdown(risks_markdown_proc.stdout, private_root=root)
        assert snapshot_files(files) == before

        # Change only this synthetic historical run: real failures must not be
        # hidden by the same current-state reconciliation that clears progress.
        run_index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        record = json.loads(run_index.read_text(encoding="utf-8"))
        record["classification"] = "runtime_error"
        run_index.write_text(json.dumps(record) + "\n", encoding="utf-8")
        failure_before = snapshot_files(files)
        failure_proc = run_cli(
            registry=registry, runtime=runtime, command="global-risks",
            output_format="json", command_args=risks_args,
        )
        assert_risks_payload(json.loads(failure_proc.stdout), unresolved_failure=True)
        assert snapshot_files(files) == failure_before
        assert_error_envelope(root, runtime=runtime)
        assert_unhealthy_status_envelope(
            root,
            registry=registry,
            runtime=runtime,
            safe_input=safe_input,
        )
        assert snapshot_files(files) == failure_before
        assert {
            goal_id: read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal_id)
            for goal_id in canonical_before
        } == canonical_before


def main() -> int:
    os.environ["NODE_OPTIONS"] = os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite"
    # Keep historical read compatibility alongside both canonical providers.
    for provider in (None, "file", "sqlite"):
        run_scenario(provider)
    print("global-manager-command-cli-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
