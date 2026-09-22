#!/usr/bin/env python3
"""Prepare and launch one bounded autonomous collaboration; no business phases."""
from __future__ import annotations

import argparse
import importlib.util
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from loopx.control_plane.operator_provider import operator_provider_environ

from scenario import REVISIONS, roster, encoded, evidence, task
from acceptance import GOAL, canonical_tasks, require_completed, todo_id, validate_delivery, validate_member
from execution import host_arguments, configure_delegations

HERE = Path(__file__).resolve().parent


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def cli(root: Path, *args: str, workspace: Path | None = None, timeout: int = 60) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(root / "registry.json"),
         "--runtime-root", str(root / "runtime"), "--format", "json", *args],
        cwd=workspace or root, capture_output=True, text=True, timeout=timeout,
    )
    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError("loopx_cli_failed_without_json") from exc
    if completed.returncode and "turn" not in args:
        raise RuntimeError("loopx_command_rejected:" + str(result.get("reason_code", "")) + ":" +
                           str(result.get("error", result.get("reason", "unknown")))[:200])
    return result


def prepare(root: Path, provider: str = "file", topology: str = "cloud-led",
            team_size: tuple[int, int, int] | None = None) -> None:
    if root.exists():
        raise ValueError("use_a_new_disposable_directory")
    members = roster(topology, team_size)
    project = root / "project"
    project.mkdir(parents=True)
    write(project / "team.json", members)
    (project / ".gitignore").write_text(".local/\nACTIVE_GOAL_STATE.md\n")
    (project / "README.md").write_text("Disposable synthetic research team.\n")
    (project / "ACTIVE_GOAL_STATE.md").write_text(
        "---\nstatus: active\n---\n# Synthetic research\n\n## User Todo\n\n## Agent Todo\n\n## Next Action\n\n- Validate synthetic evidence.\n"
    )
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(project), "-c", "user.name=Demo",
                        "-c", "user.email=demo@example.invalid", *args], check=True, capture_output=True)
    git("init", "-b", "main")
    git("add", ".gitignore", "README.md")
    git("commit", "-s", "-m", "Initialize disposable fixture")
    git("remote", "add", "origin", "https://example.invalid/synthetic/research.git")
    git("worktree", "add", "-b", "lead", str(root / "lead"))
    for member in members:
        worker, revision = member["worker"], member["revision"]
        workspace = root / worker / revision
        git("worktree", "add", "-b", worker + "-" + revision, str(workspace))
        (workspace / "input.json").write_bytes(encoded(evidence(revision)))
    write(root / "registry.json", {
        "schema_version": 1, "common_runtime_root": str(root / "runtime"),
        "goals": [{"id": GOAL, "domain": "synthetic-research", "status": "active", "repo": str(project),
                   "state_file": "ACTIVE_GOAL_STATE.md", "adapter": {"kind": "fixture_v0", "status": "connected-delivery"},
                   "quota": {"compute": 10.0, "window_hours": 24},
                   "coordination": {"agent_model": "peer_v1", "registered_agents": ["lead", *sorted({row["worker"] for row in members})], "write_scope": ["**"]}}],
    })
    validation = project / "validation"
    validation.mkdir()
    for name in ("scenario.py", "acceptance.py"):
        shutil.copyfile(HERE / name, validation / name)
    pins = [{"path": "validation/" + name, "sha256": sha256((validation / name).read_bytes()).hexdigest()}
            for name in ("scenario.py", "acceptance.py")]
    pins.append({"path": "team.json", "sha256": sha256((project / "team.json").read_bytes()).hexdigest()})
    pairs = [(row["worker"], row["revision"]) for row in members] + [("lead", "report")]
    tasks, criteria, bindings = [], [], []
    for actor, revision in pairs:
        identity = todo_id(actor, revision)
        text = (
            "Use the research_team MCP tools or the same delegation CLI from an existing local session. "
            "Read the assignment with read_assignment, or inspect the synthetic team/input files. Organize the registered "
            "members with list_execution_bindings/start_delegation/wait_delegation to analyze their authorized revisions. "
            "Use stable operation ids and collaboration_brief_v0 (purpose, context, constraints, inputs, acceptance, return_requirement). "
            "Inspect team.json: an upstream must pass acceptance before its consumer finishes. "
            "A member with requester other than lead must be delegated by that requester, using parent_request_id. "
            "You can start independent branches concurrently. A running operation is not failure; wait for its original result. "
            "Read all final artifacts with read_accepted_evidence or canonical CLI readback. Decide questions and order yourself. Review their "
            "accepted results, resolve differences, then write_report or lead/report.json with every member evidence hash. "
            "Only return validated_progress after write_report confirms independent checks."
            if actor == "lead" else
            "Read TASK.md and DELEGATION.json, or use read_input/write_output. Read context and assess_request before working. "
            "Use list_execution_bindings to find any authorized child. If present, start_delegation to the child "
            "with a collaboration_brief_v0 and parent_request_id from DELEGATION.json; wait_delegation until accepted. "
            "Then read_input again to obtain and adopt its exact artifact. A Codex member can read local input.json and "
            "write output.json directly, using the native loopx_delegation context and assess_request tools. "
            "Produce independently checked output.json for " + revision + "."
        )
        if actor != "lead":
            (root / actor / revision / "TASK.md").write_text(task(revision, text))
        tasks.append({"todo_id": identity, "text": text, "claimed_by": actor})
        criteria.append({"id": actor + "-" + revision, "description": "Independent checks for " + actor + " " + revision,
                         "validation_argv": [sys.executable, "validation/acceptance.py", str(root),
                                             *(["report"] if actor == "lead" else [actor, revision])],
                         "validation_timeout_seconds": 5, "validation_files": pins})
        bindings.append({"todo_id": identity, "criterion_ids": [actor + "-" + revision]})
    write(root / "bootstrap.json", {"tasks": tasks, "document": {
        "objective": "Deliver a revision-aware synthetic research report with every configured dependency completed",
        "non_goals": ["Trading", "External research", "Owner approval of the whole Goal"],
        "criteria": criteria, "bindings": bindings,
    }})
    initialized = subprocess.run(
        ["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
         str(HERE / "bootstrap.ts"), str(root), provider],
        capture_output=True, text=True, timeout=45,
    )
    if initialized.returncode:
        raise RuntimeError("disposable_canonical_initialization_failed:" + initialized.stderr[-1000:])
    write(root / "owner-acceptance.json", json.loads(initialized.stdout))


def complete(root: Path, actor: str, revision: str) -> dict:
    result = cli(root, "todo", "complete", "--goal-id", GOAL, "--agent-id", actor,
                 "--todo-id", todo_id(actor, revision), "--no-follow-up",
                 "--note", "Bounded artifact task; synthesis consumes dependencies through its separately bound task.",
                 workspace=root / "project")
    require_completed(canonical_tasks(root), actor, revision)
    return result


def turn(root: Path, actor: str, revision: str, workspace: Path, validator: list[str], host_args: list[str], timeout: int) -> dict:
    return cli(root, "turn", "run-once", "--goal-id", GOAL, "--agent-id", actor,
               "--todo-id", todo_id(actor, revision),
               "--turn-instance-id", actor + "-" + uuid.uuid4().hex,
               "--execution-mode", "isolated-headless", "--project", str(workspace),
               "--validation-command-json", json.dumps(validator), "--validation-failure-kind", "repair_required",
               "--scan-root", str(workspace), "--no-global-sync", "--timeout-seconds", str(timeout),
               *host_args, "--execute", workspace=workspace, timeout=timeout + 60)


def accepted_entry(worker: str, revision: str, output: dict) -> dict:
    return {"worker": worker, "revision": revision, "accepted": True,
            "todo_id": todo_id(worker, revision), "todo_status": "done",
            "evidence": output, "artifact_sha256": sha256(encoded(output)).hexdigest()}


def prepare_execution(root: Path, model: str, environment_id: str, dsh_model: str,
                      topology: str = "local-led", team_size: tuple[int, int, int] | None = None) -> dict:
    """Prepare a fresh operator fixture without starting a replacement lead."""
    prepare(root, topology=topology, team_size=team_size)
    write(root / "settings.json", {"dsh_model": dsh_model, "ark_model": model, "environment_id": environment_id})
    config = configure_delegations(root)
    return {"goal_id": GOAL, "agent_id": "lead", "registry": str(root / "registry.json"),
            "runtime_root": str(root / "runtime"), "execution_config": str(config),
            "workspace": str(root / "lead"), "execution_started": False,
            "member_count": len(roster(topology, team_size)),
            "next_action": "Use delegation list/start/read/wait from the existing Agent session. "
                           "Supply LOOPX_RESEARCH_DEMO_ROOT and the configured credentials when starting work. "
                           "Independent task acceptance remains bound; prepare does not complete any task."}


def launch(root: Path, model: str, environment_id: str, dsh_model: str, topology: str = "local-led",
           team_size: tuple[int, int, int] | None = None) -> dict:
    roster(topology, team_size)
    if team_size and not shutil.which("codex"):
        raise ValueError("codex_cli_required_for_luna_members")
    if importlib.util.find_spec("deepseek_harness") is None:
        raise ValueError("install_loopx_deepseek_harness_extra_in_this_interpreter")
    if not os.environ.get("ARK_API_KEY") or not operator_provider_environ().get("DEEPSEEK_API_KEY"):
        raise ValueError("ARK_API_KEY_and_machine_or_environment_DEEPSEEK_credential_required")
    prepare_execution(root, model, environment_id, dsh_model, topology, team_size)
    os.environ["LOOPX_RESEARCH_DEMO_ROOT"] = str(root)
    result = turn(root, "lead", "report", root / "lead", [sys.executable, str(HERE / "research_team.py"), "validate-report", str(root)],
                  host_arguments(root, "lead", "report", host="ark" if topology == "cloud-led" else "dsh"), 1200)
    summary = {key: result.get(key) for key in ("status", "result_kind", "validation", "resume_turn_key", "error", "host_failure")}
    write(root / "lead-turn.json", summary)
    if result.get("status") == "committed" and result.get("result_kind") == "validated_progress":
        complete(root, "lead", "report")
        rows = canonical_tasks(root)
        summary["canonical_completed_todos"] = [identity for identity, row in rows.items() if row["done"]]
        summary["goal_status"] = json.loads((root / "registry.json").read_text())["goals"][0]["status"]
        write(root / "completion.json", summary)
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare", "prepare-chat", "run", "validate-worker", "validate-report"])
    p.add_argument("root", type=Path)
    p.add_argument("--revision", choices=REVISIONS)
    p.add_argument("--model", default=os.environ.get("ARK_MODEL_ID"))
    p.add_argument("--environment-id", default=os.environ.get("ARK_ENVIRONMENT_ID"))
    p.add_argument("--dsh-model", default="deepseek-v4-flash")
    p.add_argument("--topology", choices=["local-led", "cloud-led"], default=None)
    p.add_argument("--team-size", nargs=3, type=int, metavar=("LUNA", "DSH", "ARK"),
                   help="Positive member counts, excluding the coordinator; uses a mixed dependency graph.")
    args = p.parse_args()
    if args.team_size and args.topology:
        p.error("--team-size selects the mixed graph; do not combine it with --topology")
    if args.team_size and args.command not in {"prepare", "prepare-chat", "run"}:
        p.error("--team-size is only valid when preparing or running a new team")
    team_size = tuple(args.team_size) if args.team_size else None
    topology = "mixed" if team_size else (args.topology or "local-led")
    try:
        roster(topology, team_size)
    except ValueError as exc:
        p.error(str(exc))
    if args.command in {"prepare", "prepare-chat", "run"}:
        if not args.model or not args.environment_id:
            p.error("explicit model and existing environment required")
        if args.command == "prepare":
            print(json.dumps(prepare_execution(args.root.resolve(), args.model, args.environment_id,
                                               args.dsh_model, topology, team_size)))
            return
        if args.command == "prepare-chat":
            root = args.root.resolve()
            prepare_execution(root, args.model, args.environment_id, args.dsh_model, topology, team_size)
            target = root / "project" / ".loopx" / "config" / "delegations.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / "delegation-config.json", target)
            with (root / "project" / "ACTIVE_GOAL_STATE.md").open("a") as stream:
                stream.write("\n## Objective\n\nOrganize the authorized members with loopx_collaboration. "
                             "Follow the prepared team.json dependency and requester graph; consume every configured member. "
                             "Wait for independently accepted results and adopt their exact artifacts. "
                             "Return the corrected cash-flow comparison, source and period caveats, and exact dependency "
                             "hashes in this conversation. The canonical report task and whole Goal remain for owner review.\n")
            print("Prepared Goal Chat team. Select lead and .loopx/config/delegations.json in LoopX mode settings.")
            return
        result = launch(args.root.resolve(), args.model, args.environment_id, args.dsh_model, topology, team_size)
        print(json.dumps(result))
        if result.get("status") != "committed" or result.get("result_kind") != "validated_progress":
            raise SystemExit(1)
    elif args.command == "validate-worker":
        validate_member(args.root.parent.parent, args.root.parent.name, args.revision)
        print("Independent worker acceptance passed")
    else:
        validate_delivery(args.root)
        print("Independent collaboration acceptance passed")


if __name__ == "__main__":
    main()
