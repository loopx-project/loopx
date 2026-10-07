#!/usr/bin/env python3
"""Exercise typed autonomous-replan projection and semantic closeout."""

from __future__ import annotations

import contextlib
import io
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx import cli as loopx_cli  # noqa: E402


GOAL_ID = "autonomous-replan-fixture"
AGENT_ID = "codex-autonomous-replan-fixture"
CLI_TIMEOUT_SECONDS = 30
USE_SUBPROCESS_CLI = False


def _argv(args: tuple[str, ...], *, registry_path: Path, runtime: Path) -> list[str]:
    project_root = registry_path.parent.parent
    scan_path_args = (
        ["--scan-path", str(project_root)]
        if args and args[0] in {"status", "quota"}
        else []
    )
    return [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        *args,
        *scan_path_args,
    ]


def run_cli(
    *args: str,
    registry_path: Path,
    runtime: Path,
    timeout: int = CLI_TIMEOUT_SECONDS,
) -> dict:
    argv = _argv(args, registry_path=registry_path, runtime=runtime)
    if not USE_SUBPROCESS_CLI:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = loopx_cli.main(argv)
        if exit_code != 0:
            raise AssertionError(
                f"loopx fixture command failed with exit {exit_code}: {' '.join(args)}\n"
                f"{stderr.getvalue().strip()}\n{stdout.getvalue().strip()}"
            )
        return json.loads(stdout.getvalue())

    command = [sys.executable, "-m", "loopx.cli", *argv]
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"loopx fixture command timed out after {timeout}s: {' '.join(args)}"
        ) from exc
    return json.loads(result.stdout)


def run_cli_error(
    *args: str,
    registry_path: Path,
    runtime: Path,
    timeout: int = CLI_TIMEOUT_SECONDS,
) -> str:
    argv = _argv(args, registry_path=registry_path, runtime=runtime)
    if not USE_SUBPROCESS_CLI:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = loopx_cli.main(argv)
        assert exit_code != 0, stdout.getvalue()
        return "\n".join((stderr.getvalue(), stdout.getvalue()))

    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *argv],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode != 0, result.stdout
    return "\n".join((result.stderr, result.stdout))


def append_run_record(runs_dir: Path, record: dict) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    generated_at = str(record["generated_at"])
    stem = generated_at.replace(":", "-")
    json_path = runs_dir / f"{stem}.json"
    markdown_path = runs_dir / f"{stem}.md"
    stored = {
        **record,
        "goal_id": GOAL_ID,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }
    json_path.write_text(json.dumps(stored, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text("# Fixture Run\n", encoding="utf-8")
    with (runs_dir / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stored, sort_keys=True) + "\n")


def _progress_observation(*, result_class: str, surface_id: str) -> dict:
    return {
        "schema_version": "typed_progress_observation_v0",
        "work_item_id": "todo_bounded_replan_slice",
        "surface_id": surface_id,
        "hypothesis_id": "hypothesis-current-boundary",
        "probe_kind": "probe-current-route",
        "result_class": result_class,
        "evidence_ids": ["evidence-current-route"],
    }


def write_fixture(
    root: Path,
    *,
    typed_repeat_count: int = 0,
    monitor_repeat_count: int = 0,
    heartbeat_monitor_repeat_count: int = 0,
    periodic_run_count: int = 0,
) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Autonomous Replan Fixture\n\n"
        "## Next Action\n\n"
        "- Advance the first executable typed work slice.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Advance the next bounded hardening slice.\n"
        "  <!-- loopx:todo todo_id=todo_bounded_replan_slice status=open "
        "task_class=advancement_task "
        f"claimed_by={AGENT_ID} -->\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "autonomous-replan-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                        "authority_sources": [],
                        "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 5},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    for offset in range(typed_repeat_count):
        append_run_record(
            runs_dir,
            {
                "generated_at": f"2026-01-01T01:{offset:02d}:00+00:00",
                "classification": f"wording-does-not-own-semantics-{offset}",
                "agent_id": AGENT_ID,
                "delivery_outcome": "surface_only",
                "progress_observation": _progress_observation(
                    result_class="unchanged",
                    surface_id="surface-current-route",
                ),
            },
        )
    for offset in range(monitor_repeat_count):
        append_run_record(
            runs_dir,
            {
                "generated_at": f"2026-01-01T02:{offset:02d}:00+00:00",
                "classification": "quota_monitor_poll",
                "agent_id": AGENT_ID,
                "delivery_outcome": "surface_only",
                "todo_id": "todo_due_monitor",
                "target_key": "monitor-current-route",
                "monitor_target": {
                    "schema_version": "quota_monitor_target_v0",
                    "target_id": "monitor-current-route",
                    "monitor_mode": "due_monitor_observed_without_material_transition",
                    "effective_action": "normal_run",
                    "agent_id": AGENT_ID,
                },
            },
        )
    for offset in range(heartbeat_monitor_repeat_count):
        append_run_record(
            runs_dir,
            {
                "generated_at": f"2026-01-01T02:{offset + 20:02d}:00+00:00",
                "classification": "quota_monitor_poll",
                "agent_id": AGENT_ID,
                "turn_instance_id": f"heartbeat-turn-{offset}",
                "delivery_outcome": "surface_only",
                "monitor_target": {
                    "schema_version": "quota_monitor_target_v0",
                    "target_id": "heartbeat-liveness-route",
                    "monitor_mode": "monitor_quiet_until_material_transition",
                    "effective_action": "monitor_quiet_skip",
                    "agent_id": AGENT_ID,
                },
            },
        )
    for offset in range(periodic_run_count):
        append_run_record(
            runs_dir,
            {
                "generated_at": f"2026-01-01T03:{offset:02d}:00+00:00",
                "classification": "bounded_iteration",
                "agent_id": AGENT_ID,
                "delivery_outcome": "outcome_progress",
            },
        )
    return registry_path, runtime


def attention_item(status_payload: dict) -> dict:
    items = status_payload.get("attention_queue", {}).get("items") or []
    assert len(items) == 1, status_payload
    return items[0]


def assert_typed_repeat_requires_two_equivalent_observations() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-typed-replan-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), typed_repeat_count=1)
        item = attention_item(run_cli("status", registry_path=registry_path, runtime=runtime))
        assert "autonomous_replan_obligation" not in item, item

    with tempfile.TemporaryDirectory(prefix="loopx-typed-replan-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), typed_repeat_count=2)
        guard = run_cli(
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            registry_path=registry_path,
            runtime=runtime,
        )
        obligation = guard["autonomous_replan_obligation"]
        assert obligation["triggers"][0]["kind"] == "typed_progress_repeat", guard
        assert obligation["triggers"][0]["run_count"] == 2, guard
        assert obligation["progress_baseline"]["surface_id"] == "surface-current-route", guard
        assert obligation["replan_context"]["delivery"] == "host_projected", guard
        assert guard["replan_action_packet"]["obligation_id"] == obligation["obligation_id"], guard
        assert guard["replan_action_packet"]["required_outcome"] == "semantic_delta", guard


def assert_equivalent_observation_is_rejected_before_write() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-typed-replan-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), typed_repeat_count=2)
        runs_index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        before = runs_index.read_text(encoding="utf-8")
        error = run_cli_error(
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--classification",
            "source_audit_progress",
            "--progress-result-class",
            "advanced",
            "--progress-surface-id",
            "surface-current-route",
            "--progress-hypothesis-id",
            "hypothesis-current-boundary",
            "--progress-probe-kind",
            "probe-current-route",
            "--progress-evidence-id",
            "evidence-new-wording",
            registry_path=registry_path,
            runtime=runtime,
        )
        assert "typed semantic delta" in error, error
        assert runs_index.read_text(encoding="utf-8") == before


def assert_new_typed_surface_closes_exact_obligation() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-typed-replan-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), typed_repeat_count=2)
        before = run_cli(
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            registry_path=registry_path,
            runtime=runtime,
        )
        obligation_id = before["autonomous_replan_obligation"]["obligation_id"]
        # Execute the host projection itself; a hand-authored command can hide
        # missing flags or an unusable printed transition.
        action = next(
            action for action in before["interaction_contract"]["cli_channel"]["next_cli_actions"]
            if "refresh-state" in action
        )
        tokens = shlex.split(action)
        replacements = {
            "<advanced|blocked|exploration_exhausted|no_followup>": "advanced",
            "<surface-id>": "surface-new-route",
            "<hypothesis-id>": "hypothesis-current-boundary",
            "<probe-kind>": "probe-current-route",
            "<evidence-id>": "evidence-new-route",
        }
        # The fixture supplies the isolated registry/runtime; preserve every
        # projected refresh argument and replace only declared input slots.
        args = [replacements.get(token, token) for token in tokens[tokens.index("refresh-state"):]]
        refresh = run_cli(*args, registry_path=registry_path, runtime=runtime)
        semantic_delta = refresh["autonomous_replan_ack"]["semantic_delta"]
        assert semantic_delta["accepted"] is True, refresh
        assert semantic_delta["obligation_id"] == obligation_id, refresh
        assert semantic_delta["satisfying_outcomes"] == ["new_surface"], refresh

        after = run_cli(
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            registry_path=registry_path,
            runtime=runtime,
        )
        assert "autonomous_replan_obligation" not in after, after


def assert_typed_monitor_threshold_remains_explicit() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-monitor-replan-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), monitor_repeat_count=6)
        item = attention_item(run_cli("status", registry_path=registry_path, runtime=runtime))
        obligation = item["project_asset"]["autonomous_replan_obligation"]
        assert obligation["triggers"][0]["kind"] == "dead_monitor_repeat", obligation
        assert obligation["triggers"][0]["run_count"] == 6, obligation

    with tempfile.TemporaryDirectory(prefix="loopx-heartbeat-monitor-replan-") as tmp:
        registry_path, runtime = write_fixture(
            Path(tmp),
            heartbeat_monitor_repeat_count=6,
        )
        item = attention_item(run_cli("status", registry_path=registry_path, runtime=runtime))
        obligation = (item.get("project_asset") or {}).get(
            "autonomous_replan_obligation"
        )
        assert not obligation or obligation["triggers"][0]["kind"] != (
            "dead_monitor_repeat"
        ), item


def assert_explicit_completed_todo_periodic_fallback() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-periodic-replan-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), periodic_run_count=20)
        # Twenty durable records have no settlement evidence. They cannot
        # satisfy the default effective-Turn cadence. The retained 20-record
        # fallback belongs only to the explicit completed-Todo unit.
        item = attention_item(
            run_cli("status", "--limit", "30", registry_path=registry_path, runtime=runtime)
        )
        assert "autonomous_replan_obligation" not in item["project_asset"], item
        run_cli(
            "configure-goal", "--goal-id", GOAL_ID,
            "--execution-replan-after-todos", "5", "--execute",
            registry_path=registry_path, runtime=runtime,
        )
        item = attention_item(
            run_cli("status", "--limit", "30", registry_path=registry_path, runtime=runtime)
        )
        obligation = item["project_asset"]["autonomous_replan_obligation"]
        assert obligation["triggers"][0]["kind"] == "periodic_review_due", obligation
        assert obligation["triggers"][0]["run_count"] == 20, obligation
        assert obligation["triggers"][0]["threshold"] == 20, obligation
        run_cli(
            "configure-goal", "--goal-id", GOAL_ID,
            "--clear-execution-replan-after-todos", "--execute",
            registry_path=registry_path, runtime=runtime,
        )
        item = attention_item(
            run_cli("status", "--limit", "30", registry_path=registry_path, runtime=runtime)
        )
        assert "autonomous_replan_obligation" not in item["project_asset"], item


def main() -> int:
    global USE_SUBPROCESS_CLI
    unknown_args = sorted(set(sys.argv[1:]) - {"--subprocess-cli"})
    if unknown_args:
        raise SystemExit(f"unknown arguments: {' '.join(unknown_args)}")
    USE_SUBPROCESS_CLI = "--subprocess-cli" in sys.argv[1:]
    assert_typed_repeat_requires_two_equivalent_observations()
    assert_equivalent_observation_is_rejected_before_write()
    assert_new_typed_surface_closes_exact_obligation()
    assert_typed_monitor_threshold_remains_explicit()
    assert_explicit_completed_todo_periodic_fallback()
    print("autonomous-replan-obligation-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
