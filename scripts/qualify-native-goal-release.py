#!/usr/bin/env python3
"""Opt-in, release-only Codex Goal qualification. Default execution costs no tokens."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
FIXTURE = REPO / "tests/fixtures/native_goal_ledger"
GOAL = "heartbeat-flow-main-control"
AGENT = "worker-a"
TODOS = {"todo_reducer", "todo_cli"}


def _default_active_state() -> str:
    return (
        '---\nstatus: active-read-only\nowner_mode: goal\n'
        'objective: "Deliver the finite local ledger specification."\n---\n\n'
        '# Ledger\n\n## Objective\n\nDeliver and validate TASK.md.\n\n'
        '## Next Action\n\nImplement the reducer, then the CLI.\n\n'
        '## User Todo\n\n## Agent Todo\n\n'
        '- [ ] [P1] Implement and validate the TASK.md reducer.\n'
        '  <!-- loopx:todo todo_id=todo_reducer status=open task_class=advancement_task '
        'action_kind=implement claimed_by=worker-a task_repository=git:example.com/ledger '
        'required_capabilities=filesystem_write -->\n'
        '- [ ] [P1] Implement the CLI, verify full acceptance and close this finite Goal.\n'
        '  <!-- loopx:todo todo_id=todo_cli status=open task_class=advancement_task '
        'action_kind=implement claimed_by=worker-a resume_when=todo_done:todo_reducer '
        'task_repository=git:example.com/ledger required_capabilities=filesystem_write -->\n'
    )


def host_environment(root: Path, launcher: Path) -> dict[str, str]:
    """Explicit transport/tool environment; never clone the operator's environment."""
    home, temporary = root / "home", root / "tmp"
    home.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    return {
        "PATH": str(launcher.parent) + os.pathsep + os.environ.get("PATH", os.defpath),
        "HOME": str(home), "TMPDIR": str(temporary), "SHELL": "/bin/sh",
        "LANG": "C.UTF-8", "PYTHONPATH": str(REPO),
        "LOOPX_USAGE_PING": "0",
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


def configure_codex(root: Path, launcher: Path) -> dict[str, str]:
    env = host_environment(root, launcher)
    home = root / "codex"
    home.mkdir()
    # No auth/config/session files are imported from the operator's Codex home.
    env["CODEX_HOME"] = str(home)
    env["LOOPX_CODEX_QUALIFICATION_API_KEY"] = os.environ["LOOPX_CODEX_QUALIFICATION_API_KEY"]
    settings = {
        "model": os.environ["LOOPX_CODEX_QUALIFICATION_MODEL"],
        "model_provider": "qualification",
        "model_providers.qualification.name": "Release qualification",
        "model_providers.qualification.base_url": os.environ["LOOPX_CODEX_QUALIFICATION_BASE_URL"],
        "model_providers.qualification.env_key": "LOOPX_CODEX_QUALIFICATION_API_KEY",
        "model_providers.qualification.wire_api": "responses",
        "shell_environment_policy.inherit": "none",
    }
    # Tool shells receive only the non-secret runtime environment, not host auth.
    settings.update({f"shell_environment_policy.set.{key}": value
                     for key, value in host_environment(root, launcher).items()})
    (home / "config.toml").write_text("\n".join(
        f"{key} = {json.dumps(value)}" for key, value in settings.items()
    ) + "\n")
    return env


def prerequisite_failure(codex: str) -> str | None:
    for executable in (codex, "git", "node"):
        if not shutil.which(executable):
            return "required_executable_unavailable"
    if not all(os.environ.get("LOOPX_CODEX_QUALIFICATION_" + name)
               for name in ("API_KEY", "MODEL", "BASE_URL")):
        return "isolated_codex_api_profile_unavailable"
    with tempfile.TemporaryDirectory(prefix="loopx-codex-probe-") as raw:
        root = Path(raw)
        env = configure_codex(root, root / "bin/loopx")
        result = subprocess.run([codex, "features", "list"], env=env,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            return "native_goals_unavailable"
        if not any(
            line.split()[:1] == ["goals"] for line in result.stdout.splitlines()
        ):
            return "native_goals_unavailable"
    return None


def setup(
    root: Path,
    *,
    active_state_text: str | None = None,
) -> tuple[Path, Path, Path]:
    # Reuse the existing real CLI fixture, not an alternate registry contract.
    spec = importlib.util.spec_from_file_location(
        "heartbeat_fixture", REPO / "examples/control_plane/heartbeat-quota-flow-smoke.py",
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    project, runtime, registry = fixture.write_fixture(root)
    config = json.loads(registry.read_text())
    goal = config["goals"][0]
    goal.update(state_file="ACTIVE_GOAL_STATE.md", coordination={"registered_agents": [AGENT]})
    goal["quota"]["allowed_slots"] = 12
    registry.write_text(json.dumps(config))
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "registry.global.json").write_text(json.dumps(config))
    (project / "ACTIVE_GOAL_STATE.md").write_text(
        active_state_text if active_state_text is not None else _default_active_state(),
        encoding="utf-8",
    )
    shutil.copyfile(FIXTURE / "TASK.md", project / "TASK.md")
    launcher = project / "bin/loopx"
    launcher.parent.mkdir()
    launcher.write_text(
        "#!/bin/sh\nexport LOOPX_PYTHON=" + shlex.quote(sys.executable) + "\nexec "
        + shlex.join([str(REPO / "scripts/loopx"), "--registry", str(registry),
                      "--runtime-root", str(runtime)]) + ' "$@"\n',
    )
    launcher.chmod(0o700)
    git = ["git", "-C", str(project), "-c", "core.hooksPath=/dev/null",
           "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com"]
    for args in (("init", "-q"), ("remote", "add", "origin", "https://example.com/ledger.git"),
                 ("add", "TASK.md"), ("commit", "-qm", "Initialize synthetic acceptance")):
        subprocess.run([*git, *args], check=True, capture_output=True, timeout=30)
    from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan

    backup = execute_state_backup_plan(
        build_state_backup_plan(
            project=project,
            runtime_root=runtime,
            output_dir=root / "backups",
            backup_id="release-goal-source",
            include_automations=False,
            include_skills=False,
            include_registry_projects=False,
            registry_path=registry,
        )
    )
    if backup.get("ok") is not True:
        raise RuntimeError("release fixture source backup failed")
    prepared = cli(
        launcher,
        "coordination-shadow",
        "prepare-import",
        "--goal-id",
        GOAL,
        "--operation-id",
        "release-goal-source",
        "--backup-manifest",
        str(backup["manifest_path"]),
        "--provider",
        "file",
        "--target-handoff-mode",
        "soft_claim",
    )["cold_import"]
    if prepared.get("status") != "prepared":
        raise RuntimeError("release fixture import plan failed")
    applied = cli(
        launcher,
        "coordination-shadow",
        "apply-import",
        "--goal-id",
        GOAL,
        "--operation-id",
        "release-goal-source",
        "--plan-sha256",
        str(prepared["plan_sha256"]),
        "--writers-stopped",
        "--execute",
    )["cold_import"]
    if applied.get("status") not in {"applied", "replayed"}:
        raise RuntimeError("release fixture canonical import failed")
    return project, runtime, launcher


def cli(launcher: Path, *args: str) -> dict:
    result = subprocess.run(
        [str(launcher), "--format", "json", *args], cwd=launcher.parent.parent,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError("fixture_cli_failed")
    packet = json.loads(result.stdout)
    if packet.get("ok") is not True:
        raise RuntimeError("fixture_cli_not_ok")
    return packet


def verify_settlement(runtime: Path, todos: list[dict]) -> int:
    assert len(todos) == len(TODOS) and {t["todo_id"] for t in todos} == TODOS
    assert all(t.get("status") == "done" for t in todos)
    rows = [json.loads(line) for line in
            (runtime / "goals" / GOAL / "runs/index.jsonl").read_text().splitlines()]
    spends = [r for r in rows if r.get("classification") == "quota_slot_spent"]
    assert {r.get("todo_id") for r in spends if r.get("todo_id")} == TODOS
    assert all(isinstance(r.get("settlement_identity"), dict) for r in spends), "unbound_spend"
    identities = [r["settlement_identity"]["effect_id"] for r in spends]
    assert len(identities) == len(set(identities)), "duplicate_spend"
    verify_spend_receipts(runtime, spends)
    return len(spends)


def verify_spend_receipts(runtime: Path, spends: list[dict]) -> None:
    """Read durable receipts for Todo delivery and independent replan Turns."""
    from loopx.control_plane.effect_program import SettlementStepKind
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    for row in spends:
        readback = read_heartbeat_settlement(
            runtime, goal_id=GOAL, agent_id=AGENT, todo_id=row.get("todo_id"),
            replan_obligation_id=row.get("replan_obligation_id"),
            turn_instance_id=row["turn_instance_id"],
        )
        assert readback is not None, "missing_settlement_readback"
        assert readback.settlement.failure is None, "incomplete_settlement"
        assert {r.step_kind for r in readback.settlement.receipts} >= {
            SettlementStepKind.VALIDATION, SettlementStepKind.DURABLE_WRITEBACK,
            SettlementStepKind.QUOTA_SPEND,
        }, "missing_settlement_receipts"
        assert readback.writeback_run is not None and readback.spend_run is not None


def verify_delivery(project: Path, runtime: Path, launcher: Path, profile: str) -> dict:
    assert (project / "TASK.md").read_bytes() == (FIXTURE / "TASK.md").read_bytes()
    oracle = subprocess.run([sys.executable, str(FIXTURE / "verify.py"), str(project)],
                            capture_output=True, timeout=60)
    assert oracle.returncode == 0, "independent_acceptance_failed"
    todos = cli(launcher, "todo", "list", "--goal-id", GOAL, "--role", "agent")["todos"]
    spends = verify_settlement(runtime, todos)
    quota = cli(launcher, "quota", "should-run", "--goal-id", GOAL,
                "--agent-id", AGENT, "--runtime-profile", profile)
    assert quota["should_run"] is False
    assert quota["interaction_contract"]["mode"] == "terminal_no_followup"
    return {"settled_spends": spends, "independent_acceptance": "passed"}


def qualify(root: Path, codex: str, timeout: int) -> dict:
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
        NativeGoalConfig, StdioNativeGoalTransport, run_native_goal_until_terminal,
    )

    project, runtime, launcher = setup(root)
    prompt = cli(launcher, "heartbeat-prompt", "--bootstrap", "--runtime-profile", "codex_cli",
                 "--goal-id", GOAL, "--agent-id", AGENT, "--cli-bin", str(launcher))
    cli(launcher, "quota", "should-run", "--runtime-profile", "codex_cli",
        "--goal-id", GOAL, "--agent-id", AGENT)
    config = NativeGoalConfig(
        cwd=str(project), objective=prompt["task_body"],
        task_instruction="Proceed with the active Goal. Read TASK.md. Use bin/loopx "
                         "for projected LoopX commands; preserve its isolated binding.",
        sandbox_policy={"type": "workspaceWrite", "writableRoots": [str(root)],
                        "networkAccess": True},  # Local TS worker needs loopback.
    )
    env = configure_codex(root, launcher)
    command = [codex, "--enable", "goals", "-c", "project_doc_max_bytes=0",
               "-c", "allow_login_shell=false", "app-server", "--stdio"]
    with tempfile.TemporaryFile(mode="w+") as stderr:
        with StdioNativeGoalTransport.spawn(command, cwd=str(project), env=env,
                                            stderr=stderr) as transport:
            turn = run_native_goal_until_terminal(transport, config, timeout_sec=timeout)
    assert turn.post_goal_status == "complete", "native_goal_did_not_complete"
    return {"status": "passed", "model_executed": True,
            "native_turns": turn.turn_completed_count,
            **verify_delivery(project, runtime, launcher, "codex_cli")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-live", action="store_true",
                        help="Explicit release qualification; permits real model cost.")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("timeout must be positive")
    if not args.release_live:
        result = {"status": "skipped", "reason": "release_opt_in_required", "model_executed": False}
    else:
        try:
            reason = prerequisite_failure(args.codex_bin)
            if reason:
                result = {"status": "skipped", "reason": reason, "model_executed": False}
            else:
                with tempfile.TemporaryDirectory(prefix="loopx-release-goal-") as raw:
                    result = qualify(Path(raw), args.codex_bin, args.timeout_seconds)
        except Exception as exc:
            # Never turn an attempted-but-failing qualification into an environment skip.
            # Raw errors can contain host paths, prompts or account details.
            result = {"status": "failed", "error_kind": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
