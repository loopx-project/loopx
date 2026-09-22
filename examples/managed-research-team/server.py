"""Synthetic domain tools composed with the reusable collaboration/Turn service."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import research_team as demo
from scenario import REVISIONS, assignments, evidence, upstream, encoded
from acceptance import validate_delivery, validate_member, canonical_tasks, require_completed

server = FastMCP("synthetic-research-team")
worker_server = FastMCP("synthetic-research-member")
worker_identity: tuple[str, str] | None = None


def root(actor: str = "lead", revision: str = "report") -> Path:
    path = Path(os.environ["LOOPX_RESEARCH_DEMO_ROOT"]).resolve()
    workspace = path / "lead" if actor == "lead" else path / actor / revision
    if (os.environ.get("LOOPX_TURN_GOAL_ID") != demo.GOAL or os.environ.get("LOOPX_TURN_AGENT_ID") != actor
            or Path(os.environ["LOOPX_TURN_WORKSPACE"]).resolve() != workspace):
        raise ValueError("demo_caller_not_bound")
    return path


@server.tool()
def read_assignment() -> dict:
    """Read synthetic inputs, authorized roster and independently checked report contract."""
    path = root()
    return {"execution": "Use list_execution_bindings, then start_delegation for your bindings. "
                         "Choose stable operation ids; wait_delegation returns running until finished. "
                         "Follow each assignment upstream and requester; only its requester can delegate a member. "
                         "Read every final artifact with read_accepted_evidence before write_report.",
            "assignments": assignments(path), "inputs": [evidence(revision) for revision in REVISIONS],
            "objective": "Compare the initial and corrected evidence. Obtain an independently accepted result "
                         "for each authorized worker/revision assignment. You choose questions/order; revise rejected work. "
                         "Use every configured member artifact in the report. Count source families corroborating "
                         "CURRENT-period figures only, excluding historical comparison material. No trades or external information.",
            "report_fields": {"initial_normalized_fcf": "integer", "corrected_normalized_fcf": "integer",
                              "revision_delta": "corrected minus initial", "growth_supported": "boolean",
                              "independent_source_families": "integer", "repost_stale_after_correction": "boolean",
                              "dependencies": {"worker/revision": "artifact_sha256 returned by read_accepted_evidence"}, "reason": "short explanation"}}


@server.tool()
def read_accepted_evidence() -> dict:
    """Read accepted canonical member outputs, including results returned through nested members."""
    path = root()
    rows = canonical_tasks(path)
    results = []
    for member in assignments(path):
        actor, revision = member["worker"], member["revision"]
        require_completed(rows, actor, revision)
        results.append(demo.accepted_entry(actor, revision, validate_member(path, actor, revision)))
    return {"results": results}


@server.tool()
def write_report(report: dict) -> dict:
    """Write the synthesized report and check all dependency hashes and substantive conclusions."""
    path = root()
    if len(json.dumps(report)) > 16_000:
        raise ValueError("report_too_large")
    demo.write(path / "lead" / "report.json", report)
    try:
        validate_delivery(path)
    except ValueError as exc:
        return {"accepted": False, "reason": str(exc)[:200]}
    except (OSError, KeyError, TypeError) as exc:
        return {"accepted": False, "reason": type(exc).__name__ + ":report_or_dependencies_rejected"}
    return {"accepted": True, "note": "Artifact checks passed. Host revalidates before canonical Todo completion; Goal stays active."}


def worker_workspace() -> tuple[Path, str]:
    if worker_identity is None:
        raise ValueError("worker_identity_required")
    actor, revision = worker_identity
    path = root(actor, revision)
    if not any(row["worker"] == actor and row["revision"] == revision for row in assignments(path)):
        raise ValueError("worker_assignment_not_authorized")
    if os.environ.get("LOOPX_TURN_TODO_ID") != demo.todo_id(actor, revision):
        raise ValueError("worker_todo_not_bound")
    return path / actor / revision, revision


@worker_server.tool()
def read_input() -> dict:
    """Read only this member's assigned synthetic input and output contract."""
    workspace, _ = worker_workspace()
    raw = (workspace / "input.json").read_bytes()
    dependency = upstream(workspace.parent.parent, workspace.parent.name, workspace.name)
    adopted = {}
    if dependency:
        actor, revision = dependency.split("/")
        path = workspace.parent.parent
        try:
            require_completed(canonical_tasks(path), actor, revision)
        except ValueError:
            adopted = {"identity": dependency, "status": "incomplete", "instruction": "Use your authorized execution binding to request this prerequisite, then wait for acceptance."}
        else:
            artifact = validate_member(path, actor, revision)
            adopted = {"identity": dependency, "artifact": artifact, "artifact_sha256": sha256(encoded(artifact)).hexdigest()}
    return {"input": json.loads(raw), "input_sha256": sha256(raw).hexdigest(), "upstream": adopted,
            "task": (workspace / "TASK.md").read_text(),
            "delegation": json.loads((workspace / "DELEGATION.json").read_text()) if (workspace / "DELEGATION.json").exists() else None,
            "instruction": "Use write_output to submit output.json. Host validation and canonical completion follow separately."}


@worker_server.tool()
def write_output(output: dict) -> dict:
    """Submit output.json, including adopted_dependencies for any read_input upstream.

    That object maps upstream.identity to the full upstream.artifact_sha256.
    Return the normal Turn JSON candidate only after artifact_checks_passed.
    """
    workspace, revision = worker_workspace()
    if len(json.dumps(output)) > 16_000:
        raise ValueError("output_too_large")
    demo.write(workspace / "output.json", output)
    try:
        validate_member(workspace.parent.parent, workspace.parent.name, revision)
    except ValueError as exc:
        return {"artifact_checks_passed": False, "reason": str(exc)[:200]}
    return {"artifact_checks_passed": True, "note": "Return validated_progress; the host owns canonical completion."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path)
    parser.add_argument("--worker")
    parser.add_argument("--revision", choices=REVISIONS)
    args = parser.parse_args()
    if args.local_root:
        path = args.local_root.resolve()
        actor, revision = args.worker or "lead", args.revision or "report"
        workspace = path / actor / revision if args.worker else path / "lead"
        os.environ.update({"LOOPX_RESEARCH_DEMO_ROOT": str(path), "LOOPX_TURN_GOAL_ID": demo.GOAL,
                           "LOOPX_TURN_AGENT_ID": actor, "LOOPX_TURN_TODO_ID": demo.todo_id(actor, revision),
                           "LOOPX_TURN_WORKSPACE": str(workspace)})
    if args.worker or args.revision:
        if not args.worker or not args.revision:
            parser.error("worker and revision required together")
        worker_identity = (args.worker, args.revision)
    actor, revision = args.worker or "lead", args.revision or "report"
    path = root(actor, revision)
    workspace = path / actor / revision if args.worker else path / "lead"
    selected = worker_server if args.worker else server
    from loopx.collaboration_mcp import register_collaboration_tools
    from loopx.collaboration_mcp import Delegations, register_delegation_tools
    register_collaboration_tools(selected, path / "runtime", path / "registry.json", demo.GOAL, actor, workspace)
    if (path / "delegation-config.json").exists():
        register_delegation_tools(selected, Delegations(path / "runtime", path / "registry.json", demo.GOAL,
                                             actor, path / "delegation-config.json"))
    selected.run(transport="stdio")
