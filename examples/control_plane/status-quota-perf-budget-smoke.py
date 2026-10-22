#!/usr/bin/env python3
"""Smoke-test status/quota latency with large ignored local state trees."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402

GOAL_ID = "status-quota-perf-budget-fixture"
AGENT_ID = "codex-main-control"
IGNORED_FILE_COUNT = 1_200
STATUS_BUDGET_SECONDS = 3.5
QUOTA_BUDGET_SECONDS = 3.5
PRIVATE_DOC_MARKER = "https://" + "la" + "rk" + "office.example/doc"


def write_fixture(root: Path, *, import_authority: bool = True) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_rel = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_file = project / state_rel
    registry_path = project / ".loopx" / "registry.json"
    public_doc = project / "README.md"

    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Status Quota Perf Budget Fixture\n\n"
        "## Objective\n\n"
        "Keep status and quota hot paths fast without skipping public scans.\n\n"
        "## Next Action\n\n"
        "- Validate the selected hot-path budget todo.\n\n"
        "## Agent Todo\n\n"
        + ("" if import_authority else
           "- [ ] Inspect the historical status budget fixture.\n"
           f"  <!-- loopx:todo todo_id=todo_historical_budget status=open "
           f"task_class=advancement_task claimed_by={AGENT_ID} -->\n"),
        encoding="utf-8",
    )
    public_doc.write_text("Public smoke fixture for status/quota perf budgets.\n", encoding="utf-8")

    ignored_root = project / ".local" / "large-state-tree"
    ignored_root.mkdir(parents=True)
    for index in range(IGNORED_FILE_COUNT):
        shard = ignored_root / f"shard-{index // 100:02d}"
        shard.mkdir(exist_ok=True)
        (shard / f"private-state-{index:04d}.txt").write_text(
            "ignored local runtime state\n",
            encoding="utf-8",
        )

    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "status-quota-perf-budget",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_rel,
                        "adapter": {
                            "kind": "generic_project_goal_v0",
                            "status": "connected",
                        },
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if import_authority:
        import_fixture_authority(registry_path, GOAL_ID)
    return registry_path, project, public_doc


def run_cli_result(
    registry_path: Path,
    *args: str,
    check: bool = True,
) -> tuple[dict[str, Any], float, int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    started = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    elapsed = time.perf_counter() - started
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return payload, elapsed, result.returncode, result.stderr


def run_cli(registry_path: Path, *args: str) -> tuple[dict[str, Any], float]:
    payload, elapsed, _returncode, _stderr = run_cli_result(registry_path, *args)
    return payload, elapsed


def assert_public_scan_is_deferred(payload: dict[str, Any]) -> None:
    checks = "\n".join(
        str(item)
        for item in [
            *(payload.get("checks") or []),
            *((payload.get("contract") or {}).get("checks") or []),
        ]
    )
    assert "public boundary scan deferred for status hot path" in checks, payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-status-quota-perf-budget-") as tmp:
        root = Path(tmp)
        registry_path, project, public_doc = write_fixture(root)

        todo_payload, _todo_elapsed = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Validate status/quota perf budget.",
            "--claimed-by",
            AGENT_ID,
        )
        assert todo_payload.get("ok") is True, todo_payload

        status_payload, status_elapsed = run_cli(
            registry_path,
            "status",
            "--limit",
            "5",
            "--agent-id",
            AGENT_ID,
        )
        assert status_payload.get("ok") is True, status_payload
        assert_public_scan_is_deferred(status_payload)
        assert status_elapsed <= STATUS_BUDGET_SECONDS, (
            status_elapsed,
            STATUS_BUDGET_SECONDS,
            IGNORED_FILE_COUNT,
        )

        quota_payload, quota_elapsed = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--limit",
            "5",
        )
        assert quota_payload.get("ok") is True, quota_payload
        assert quota_payload.get("should_run") is True, quota_payload
        assert quota_elapsed <= QUOTA_BUDGET_SECONDS, (
            quota_elapsed,
            QUOTA_BUDGET_SECONDS,
            IGNORED_FILE_COUNT,
        )

        public_doc.write_text(
            f"Public leak probe must be caught by explicit outgoing check: {PRIVATE_DOC_MARKER}\n",
            encoding="utf-8",
        )
        leak_payload, _leak_elapsed, leak_returncode, leak_stderr = run_cli_result(
            registry_path,
            "check",
            "--scan-path",
            str(public_doc),
            check=False,
        )
        assert leak_returncode != 0 and leak_payload.get("ok") is False, leak_payload
        assert "private_doc_url" in json.dumps(leak_payload) + leak_stderr, leak_payload

        public_doc.write_text(
            "Public smoke fixture for status/quota perf budgets.\n",
            encoding="utf-8",
        )
        state_file = (
            project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        )
        state_text = state_file.read_text(encoding="utf-8")
        assert " status=open " in state_text, state_text
        before, _ = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID)
        tampered = state_text.replace(" status=open ", " status=stuck ", 1)
        state_file.write_text(tampered, encoding="utf-8")
        after, _ = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID)
        assert after["todos"] == before["todos"], after
        assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"], after
        assert after["authority_read"]["legacy_fallback_used"] is False, after
        assert state_file.read_text(encoding="utf-8") == tampered

        # Malformed historical source still has a read-only diagnostic/budget
        # contract. It cannot act as the authority of the imported fixture.
        registry_path, project, public_doc = write_fixture(root / "legacy-inspection", import_authority=False)
        state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        state_text = state_file.read_text(encoding="utf-8")
        assert " status=open " in state_text, state_text
        state_file.write_text(state_text.replace(" status=open ", " status=stuck ", 1), encoding="utf-8")
        legacy_source = state_file.read_bytes()

        (
            malformed_status_payload,
            malformed_status_elapsed,
            malformed_status_returncode,
            _malformed_status_stderr,
        ) = run_cli_result(
            registry_path,
            "status",
            "--limit",
            "5",
            "--agent-id",
            AGENT_ID,
            check=False,
        )
        assert malformed_status_returncode != 0, malformed_status_payload
        assert malformed_status_payload.get("ok") is False, malformed_status_payload
        error_diagnostics = (
            (malformed_status_payload.get("contract") or {}).get("error_diagnostics")
            or []
        )
        assert any(
            item.get("code") == "todo_status_invalid"
            for item in error_diagnostics
            if isinstance(item, dict)
        ), malformed_status_payload
        assert_public_scan_is_deferred(malformed_status_payload)
        assert malformed_status_elapsed <= STATUS_BUDGET_SECONDS, (
            malformed_status_elapsed,
            STATUS_BUDGET_SECONDS,
            "malformed_state",
        )

        (
            malformed_quota_payload,
            malformed_quota_elapsed,
            malformed_quota_returncode,
            _malformed_quota_stderr,
        ) = run_cli_result(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--limit",
            "5",
            check=False,
        )
        assert malformed_quota_returncode != 0, malformed_quota_payload
        assert malformed_quota_payload.get("status_health_ok") is False, (
            malformed_quota_payload
        )
        assert malformed_quota_payload.get("should_run") is False, (
            malformed_quota_payload
        )
        assert malformed_quota_payload.get("effective_action") == "quota_skip", (
            malformed_quota_payload
        )
        assert PRIVATE_DOC_MARKER not in json.dumps(malformed_quota_payload), (
            malformed_quota_payload
        )
        assert malformed_quota_elapsed <= QUOTA_BUDGET_SECONDS, (
            malformed_quota_elapsed,
            QUOTA_BUDGET_SECONDS,
            "malformed_state",
        )
        assert state_file.read_bytes() == legacy_source

        print(
            "status_quota_perf_budget: "
            f"ignored_files={IGNORED_FILE_COUNT} "
            f"status={status_elapsed:.3f}s/{STATUS_BUDGET_SECONDS:.1f}s "
            f"quota={quota_elapsed:.3f}s/{QUOTA_BUDGET_SECONDS:.1f}s "
            "malformed_state "
            f"status={malformed_status_elapsed:.3f}s/{STATUS_BUDGET_SECONDS:.1f}s "
            f"quota={malformed_quota_elapsed:.3f}s/{QUOTA_BUDGET_SECONDS:.1f}s"
        )
    print("status-quota-perf-budget-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
