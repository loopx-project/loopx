import json
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from loopx.chat_server import serve_chat
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.manager_context import deliver
from loopx.control_plane.goals.goal_amendment_runtime import execute_goal_amendment
from loopx.control_plane.collaboration.request_control_source import execute_request_control
from loopx.control_plane.effect_runtime import restart_effect_runtime

root = Path(sys.argv[1])
port = int(sys.argv[2])
tempfile.tempdir = str(root)
registry = root / "project/.loopx/registry.json"
runtime = root / "runtime"


def cli(*args):
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=80,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)
    return json.loads(p.stdout)


created = cli(
    "project",
    "register",
    "--project-id",
    "request-recovery",
    "--project-kind",
    "work",
    "--knowledge-root",
    str(root / "project"),
    "--goal-id",
    "request-goal",
    "--objective",
    "Verify the public contract",
    "--non-goal",
    "No remote changes",
    "--acceptance",
    "Independent validation passes",
    "--next-effect",
    "Inspect the current contract",
    "--stop-condition",
    "Stop when the Goal is inactive",
    "--goal-instance-profile",
    "source_session_v1",
    "--operation-id",
    "create-goal",
    "--registered-agent",
    "agent-a",
    "--registered-agent",
    "agent-b",
    "--canonical-storage",
    "sqlite",
)
ref = created["goal_ref"]
(root / "registration.json").write_text(json.dumps(created))
# Synthetic typed progress supplies a real replan-reader input, not model evidence.
runs = runtime / "goals/request-goal/runs/index.jsonl"
runs.parent.mkdir(parents=True, exist_ok=True)
rows = [
    {
        "generated_at": f"2026-09-01T00:0{i}:00+00:00",
        "goal_id": "request-goal",
        "goal_ref": ref,
        "agent_id": "agent-a",
        "classification": "bounded_replan_progress",
        "turn_instance_id": f"synthetic-{i}",
        "progress_observation": {
            "schema_version": "typed_progress_observation_v0",
            "result_class": "blocked",
            "surface_id": "contract",
            "hypothesis_id": "request-gap",
            "probe_kind": "check",
            "evidence_ids": ["synthetic-evidence"],
        },
    }
    for i in range(2)
]
runs.write_text("".join(json.dumps(r) + "\n" for r in rows))


def goal(op, params=None, actor="agent-a", execute=False):
    return execute_goal_amendment(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=ref["goal_id"],
        goal_instance_id=ref["goal_instance_id"],
        operation=op,
        actor_agent_id=actor,
        parameters=params,
        execute=execute,
    )


def basis(v):
    return {
        "base_goal_revision": v["intent"]["goal_revision"],
        "base_intent_digest": v["intent"]["intent_digest"],
        "base_work_digest": v["work_digest"],
    }


v = goal("inspect", actor=None)
root_config = {
    "operation_id": "root-policy",
    "expected_provider_revision": v["provider_revision"],
    "root_intent": {
        **{
            k: v["source_commitments"][k]
            for k in ("objective", "non_goals", "acceptance")
        },
        "permission_scope": {
            "write_scopes": ["tests/**"],
            "capabilities": ["filesystem_write"],
        },
        "stop_conditions": [v["source_commitments"]["stop_condition"]],
        "terminal_conditions": ["Accepted result returned"],
    },
    "policy": {
        "out_of_policy": "reject",
        "eligible_agents": ["agent-a", "agent-b"],
        "max_created_todos": 4,
        "templates": [
            {
                "id": "check",
                "todo": {
                    "text": "Verify the public contract",
                    "action_kind": "validate",
                    "target_key": "contract-check",
                    "required_write_scopes": ["tests/**"],
                    "required_capabilities": ["filesystem_write"],
                },
            }
        ],
    },
}
assert goal("configure", root_config, actor=None, execute=True)["ok"]
store = ChatSessionStore(runtime)
session = store.create_session(
    goal_id="loopx-manager",
    agent_id="codex",
    adapter_kind="codex_app_server",
    upstream_thread_id="synthetic-owner",
    channel_id="manager",
    session_id="request-owner",
)
turn, _ = store.create_turn(
    session["session_id"],
    client_turn_id="original-source",
    message="Please verify this contract. Preserve the constraints and return findings.",
    origin="web",
)
brief = {
    "schema_version": "collaboration_brief_v0",
    "purpose": "Verify this contract",
    "context": "Use the original contract fixture",
    "constraints": ["No remote changes"],
    "inputs": [],
    "acceptance": ["Independent validation passes"],
    "return_requirement": "Return checked findings",
}
receipt = deliver(
    runtime,
    registry,
    session=session,
    turn=turn,
    request={"goal_id": ref["goal_id"], "agent_id": "agent-a", "brief": brief},
    source_store=store,
)
store.update_turn(
    session["session_id"],
    turn["turn_id"],
    status="completed",
    response={"message": "Request recorded", "context_handoff_receipt": receipt},
)
store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
rid = receipt["request_id"]
route = {
    **ref,
    "agent_id": "agent-a",
    "request_id": rid,
    "session_id": session["session_id"],
    "turn_id": turn["turn_id"],
}
(root / "request-route.json").write_text(json.dumps(route))


def inspect():
    return execute_request_control(
        registry_path=registry,
        runtime_root=runtime,
        goal_ref=ref,
        receiver_agent_id="agent-a",
        request_id=rid,
        actor_agent_id=None,
        operation="inspect",
    )["view"]


def commands():
    try:
        for line in sys.stdin:
            operation = line.strip()
            if operation == "inspect":
                result = inspect()
            elif operation == "receiver":
                packet = cli(
                    "manager-inbox",
                    "read",
                    "--goal-id",
                    ref["goal_id"],
                    "--agent-id",
                    "agent-a",
                )
                item = next(x for x in packet["items"] if x["request_id"] == rid)
                cli(
                    "manager-inbox",
                    "acknowledge",
                    "--goal-id",
                    ref["goal_id"],
                    "--agent-id",
                    "agent-a",
                    "--request-id",
                    rid,
                    "--decision",
                    "adopt",
                    "--reason",
                    "Use current source",
                    "--request-revision",
                    str(item["request_revision"]),
                    "--request-digest",
                    item["request_revision_digest"],
                )
                result = {"receiver_revision": item["request_revision"]}
            elif operation == "reserve":
                v = goal("inspect")
                goal("adopt", {**basis(v), "operation_id": "adopt-root"}, execute=True)
                source = inspect()
                reference = {
                    "request_id": rid,
                    "request_revision": source["revision"],
                    "request_digest": source["revision_digest"],
                }
                proposed = goal(
                    "propose",
                    {
                        **basis(v),
                        "source_request": reference,
                        "operation_id": "source-proposal",
                        "proposal_id": "request-work",
                        "template_id": "check",
                        "evidence_refs": ["evidence:request-gap"],
                        "replan_obligation_id": v["replan_observations"][0][
                            "obligation_id"
                        ],
                    },
                    execute=True,
                )
                assert proposed["ok"], proposed
                from loopx.control_plane.collaboration import (
                    request_amendment as io_adapter,
                )

                real = io_adapter._goal

                def lost(*a, **kw):
                    if a[4] == "commit" and kw.get("execute"):
                        raise OSError("Synthetic loss before known Goal outcome")
                    return real(*a, **kw)

                io_adapter._goal = lost
                try:
                    result = goal(
                        "commit",
                        {
                            "operation_id": "reserved-effect",
                            "source_request": reference,
                            "proposal_id": "request-work",
                            "proposal_digest": proposed["proposal"]["digest"],
                        },
                        execute=True,
                    )
                finally:
                    io_adapter._goal = real
                assert result["request_linked"] is False and result["effect"] is None, (
                    result
                )
            elif operation == "correct":
                current = inspect()
                result = execute_request_control(
                    registry_path=registry,
                    runtime_root=runtime,
                    goal_ref=ref,
                    receiver_agent_id="agent-a",
                    request_id=rid,
                    actor_agent_id=None,
                    operation="revise",
                    parameters={
                        "operation_id": "separate-owner-correction",
                        "expected_revision": current["revision"],
                        "expected_digest": current["revision_digest"],
                        "brief": {
                            **current["brief"],
                            "context": "Correction from another authorized view",
                        },
                        "reason": "Source changed",
                    },
                    execute=True,
                )
            else:
                raise ValueError(operation)
            print("FIXTURE " + json.dumps(result), flush=True)
    except Exception as exc:
        print("FIXTURE " + json.dumps({"fixture_error": str(exc)}), flush=True)


threading.Thread(target=commands, daemon=True).start()
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    serve_chat(
        registry_path=registry,
        runtime_root_override=runtime,
        scan_roots=[root / "project"],
        port=port,
        codex_bin="missing-synthetic-codex",
        claude_bin="missing-synthetic-claude",
        kiro_cli_bin="missing-synthetic-kiro",
    )
finally:
    restart_effect_runtime()
