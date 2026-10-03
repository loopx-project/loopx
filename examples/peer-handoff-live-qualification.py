#!/usr/bin/env python3
"""Opt-in real Codex peer handoff using only disposable LoopX state.

Run from the source checkout with an authenticated Codex home. This creates two
synthetic host threads and consumes model quota; it never resumes a user thread.
Only compact assertions are printed. Host transcripts stay in the selected home.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.chat_agent import CodexChatAgentSession  # noqa: E402
from loopx.control_plane.effect_runtime import restart_effect_runtime  # noqa: E402


def tool(name, description, properties=None):
    fields = properties or {}
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": fields, "required": list(fields),
        "additionalProperties": False,
    }}


def qualify(workspace: Path, *, codex_bin: str, codex_home: Path) -> dict:
    """The host owns thread submission; LoopX owns request/read/return receipts."""
    runtime = workspace / "runtime"
    registry = workspace / "registry.json"
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    artifact = workspace / "review-packet.json"
    artifact.write_text(json.dumps({"head": head, "demand": 15,
        "allocated": 13, "reserved": 2}), encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    brief = workspace / "brief.json"
    brief.write_text(json.dumps({
        "schema_version": "collaboration_brief_v0",
        "purpose": "Independently review the synthetic allocation artifact",
        "context": f"Review exactly checkout head {head}; do not substitute another peer.",
        "constraints": ["No child Agents", "No shell commands", "No external writes"],
        "inputs": [{"ref": artifact.name, "description": "Synthetic review packet",
                    "sha256": digest}],
        "acceptance": ["Allocated plus reserved equals demand", "Head and digest match"],
        "return_requirement": "Return the verdict, exact head and artifact digest to requester",
    }), encoding="utf-8")
    env = {**os.environ, "LOOPX_CODEX_HOMES": str(codex_home)}

    def cli(agent, action, *args, ok=True):
        process = subprocess.run([sys.executable, "-m", "loopx.cli",
            "--registry", str(registry), "--runtime-root", str(runtime),
            "--format", "json", "manager-inbox", action,
            "--goal-id", "peer-qualification", "--agent-id", agent, *args],
            env=env, cwd=workspace, text=True, capture_output=True, timeout=45)
        value = json.loads(process.stdout)
        assert process.returncode == (0 if ok else 1), value
        return value

    events, calls = [], []
    sessions = []

    def observe(method, value):
        if method == "item/completed":
            item = value.get("item") or {}
            events.append(item.get("type"))

    def start(agent, tools, *, resume=None):
        session = CodexChatAgentSession.start(
            codex_bin=codex_bin, codex_home=codex_home, work_dir=workspace,
            goal_id="peer-qualification", objective=f"Synthetic {agent} qualification",
            execution_mode=True, sandbox="read-only", resume_thread_id=resume,
            dynamic_tools=tools, isolate_process_tree=True, hard_timeout_sec=180,
            host_config={"features.multi_agent": False},
        )
        sessions.append(session)
        return session

    review_tools = [
        tool("peer_review_read", "Read your scoped Inbox request and its exact artifact"),
        tool("peer_review_adopt", "Adopt the request after reading it"),
        tool("peer_review_report", "Return the independent review to its requester", {
            "head": {"type": "string"}, "sha256": {"type": "string"},
            "verdict": {"type": "string", "enum": ["accept", "reject"]},
        }),
    ]
    requester_tools = [
        tool("peer_return_read", "Read the review returned to this requester"),
        tool("peer_return_consume", "Acknowledge the returned result after reading it"),
    ]
    try:
        reviewer = start("reviewer", review_tools)
        requester = start("requester", requester_tools)
        reviewer_id, requester_id = reviewer.thread_id, requester.thread_id
        # These are new test sessions. Finishing a real Turn makes the production
        # local-store observer readable without fabricating its SQLite records.
        for session in (reviewer, requester):
            session.send("Reply fixture-ready. Do not use tools or create child Agents.",
                         on_event=observe)
        reviewer.close()
        requester.close()
        registry.write_text(json.dumps({"goals": [{"id": "peer-qualification",
            "repo": str(workspace), "coordination": {
                "registered_agents": ["requester", "reviewer"],
                "thread_agent_bindings": [
                    {"agent_id": "reviewer", "host_surface": "codex-app",
                     "thread_id": reviewer_id},
                    {"agent_id": "reviewer", "host_surface": "codex-app",
                     "thread_id": "historical-unreachable-fixture"},
                    {"agent_id": "requester", "host_surface": "codex-app",
                     "thread_id": requester_id},
                ],
            }}]}), encoding="utf-8")
        common = ("--peer-agent-id", "reviewer", "--operation-id", "exact-head-review",
                  "--brief-file", str(brief), "--require-host-route")
        refused = cli("requester", "request", *common, ok=False)
        assert "ambiguous" in refused["error"]
        selected = (*common, "--peer-thread-link", f"codex://threads/{reviewer_id}")
        sent = cli("requester", "request", *selected)
        rid = sent["request_id"]
        assert sent["host_delivery"]["status"] == "not_attempted"
        assert sent["host_delivery"]["thread_id"] == reviewer_id
        assert cli("requester", "request", *selected)["replayed"]
        # Resume only the pinned test thread through the owning host. This is an
        # explicitly authorized test submission, not authority inferred from a route.
        reviewer = start("reviewer", review_tools, resume=reviewer_id)

        def review_handler(name, arguments, identity):
            assert identity["thread_id"] == reviewer_id
            calls.append(name)
            if name == "peer_review_read":
                inbox = cli("reviewer", "read")
                row = next(item for item in inbox["items"] if item["request_id"] == rid)
                assert row["brief"]["inputs"][0]["sha256"] == digest
                assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
                return {"ok": True, "request": row, "artifact": json.loads(artifact.read_text()),
                        "sha256": digest}
            if name == "peer_review_adopt":
                assert "peer_review_read" in calls
                return cli("reviewer", "acknowledge", "--request-id", rid,
                    "--decision", "adopt", "--reason", "Independent exact-head review")
            if name == "peer_review_report":
                assert "peer_review_adopt" in calls
                assert arguments == {"head": head, "sha256": digest, "verdict": "accept"}
                return cli("reviewer", "report", "--request-id", rid,
                    "--phase", "conclusion", "--reply-text", f"ACCEPT head={head} sha256={digest}")
            raise ValueError("unsupported qualification tool")

        reviewer.bound_tool_handler = review_handler
        reviewer.send(sent["host_delivery"]["message"] +
            " Use peer_review_read, inspect the arithmetic independently, then "
            "peer_review_adopt and peer_review_report. Use only these three tools.",
            on_event=observe)
        reviewer.close()
        # A fresh process restores the requester's original identity for result
        # consumption; neither retry nor restart creates another peer request.
        replay = cli("requester", "request", *selected)
        assert replay["replayed"] and replay["request_id"] == rid
        requester = start("requester", requester_tools, resume=requester_id)

        def return_handler(name, arguments, identity):
            assert identity["thread_id"] == requester_id
            calls.append(name)
            if name == "peer_return_read":
                result = cli("requester", "read")
                item = next(item for item in result["peer_returns"]["items"]
                            if item["request_id"] == rid)
                assert item["text"] == f"ACCEPT head={head} sha256={digest}"
                return {"ok": True, "result": item}
            if name == "peer_return_consume":
                assert "peer_return_read" in calls
                return cli("requester", "acknowledge-return", "--request-id", rid)
            raise ValueError("unsupported qualification tool")

        requester.bound_tool_handler = return_handler
        requester.send("The existing peer has returned its review. Use peer_return_read "
            "then peer_return_consume; summarize its exact head and digest. "
            "Use only these two tools.", on_event=observe)
        required = {entry["name"] for entry in review_tools + requester_tools}
        assert required <= set(calls), "real peers did not complete the exchange"
        assert not cli("requester", "read").get("peer_returns", {}).get("items", [])
        assert not {"collabAgentToolCall", "commandExecution"} & set(events)
        status = cli("reviewer", "status", "--request-id", rid)
        return {"ok": True, "host": "codex_app_server", "peers": 2,
            "exact_thread_resumed": True, "request_replayed": True,
            "receiver_adopted": True, "result_returned_and_consumed": True,
            "replacement_workers": 0, "host_delivery_preview": "not_attempted",
            "tracked_requests": len(status["rows"])}
    finally:
        for session in sessions:
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-real-host", action="store_true",
                        help="Authorize two synthetic Codex sessions and model usage")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser())
    args = parser.parse_args()
    if not args.execute_real_host:
        parser.error("--execute-real-host is required; this smoke uses a real authenticated host")
    with tempfile.TemporaryDirectory(prefix="lxp-") as folder:
        # CLI reads start a reusable typed runtime whose Windows cwd keeps the
        # workspace open. Give this smoke its own locator, then stop that owner
        # before deleting the workspace; never stop a shared user's runtime.
        runtime_temp = Path(folder) / "tmp"
        runtime_temp.mkdir()
        previous_temp = tempfile.tempdir
        previous_env = {key: os.environ.get(key) for key in ("TMPDIR", "TEMP", "TMP")}
        try:
            tempfile.tempdir = str(runtime_temp)
            os.environ.update({key: str(runtime_temp) for key in previous_env})
            try:
                result = qualify(Path(folder), codex_bin=args.codex_bin,
                                 codex_home=args.codex_home.resolve())
            finally:
                restart = restart_effect_runtime()
                if restart["status"] == "shutdown_pending":
                    raise RuntimeError("isolated typed runtime shutdown did not complete")
        finally:
            tempfile.tempdir = previous_temp
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
