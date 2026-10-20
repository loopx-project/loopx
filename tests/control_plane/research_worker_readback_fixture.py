"""Disposable real worker/CLI authority for the packaged dashboard smoke."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

GOAL = "successor-review-fixture"
AGENT = "fixture-agent"


def _write_authority(root: Path, *, provider: str = "file") -> tuple[Path, Path]:
    """Prepare canonical worker state without importing pytest-only modules."""
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.goals.goal_vision import compact_goal_vision_packet, normalize_goal_vision_packet
    from loopx.control_plane.status.autonomous_replan_projection import AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD

    project, runtime = root / "project", root / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n# Synthetic Goal\n\n## Objective\nDeliver the independently accepted source outcome.\n\n## Agent Todo\n")
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    evidence = index.parent / "synthetic-artifact.json"
    report = index.parent / "synthetic-artifact.md"
    evidence.write_text(json.dumps({"ok": True, "fixture": "public-safe-replan"}))
    report.write_text("# Synthetic source audit\n")
    runs = [dict(classification="evidence_validated", agent_id=AGENT,
        generated_at=f"2026-08-01T00:{i:02d}:00Z", json_path=str(evidence), markdown_path=str(report))
        for i in reversed(range(AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD))]
    runs[0]["agent_vision"] = compact_goal_vision_packet(normalize_goal_vision_packet({
        "goal_id": GOAL, "agent_id": AGENT, "state": "vision_drift_detected",
        "vision_patch": {"acceptance_summary": "Independently validate a source artifact.",
                         "advancement_policy": "repeat_until_closed"},
    }, goal_id=GOAL, agent_id=AGENT))
    index.write_text("".join(json.dumps(row) + "\n" for row in reversed(runs)))
    (root / "registry.json").write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": "active", "repo": str(project), "state_file": state.name,
        "domain": "synthetic-replan",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT, "different-agent"]},
    }]}))
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=[], handoff_mode="soft_claim", leases=[],
    ), state_path=state, provider=provider)
    return runtime, index


def _unexpected_timer(_seconds: float) -> None:
    raise AssertionError("unexpected timer")


def serve(root: Path, mode: str) -> None:
    if mode not in {"paused", "unknown"}:
        raise ValueError("unknown smoke mode")
    # This helper also runs directly from the browser smoke, outside pytest's
    # autouse fixtures. Resolve default device settings inside its disposable
    # home before importing any runtime owner, as the live readiness smoke does.
    isolated_home = root / "home"
    isolated_home.mkdir()
    with ExitStack() as cleanup:
        cleanup.enter_context(patch.dict(os.environ, {
            "HOME": str(isolated_home), "USERPROFILE": str(isolated_home),
            "TMPDIR": str(root), "TEMP": str(root), "TMP": str(root), "LOOPX_USAGE_PING": "0",
        }))
        cleanup.enter_context(patch.object(tempfile, "tempdir", str(root)))
        for variable in ("CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(variable, None)
        import external_scheduler_worker as worker
        from managed_effect_runtime_fixture import stop_disposable_effect_runtimes
        from loopx.chat_server import ChatHTTPServer, default_chat_assets_dir, serve_chat
        from loopx.control_plane.quota.host_poll_receipts import receipt_path_for_goal
        from loopx.global_registry import sync_project_registry_to_global

        # Source changes can create several runtime fingerprints. Retire all
        # verified owners before restoring the environment or removing the root.
        cleanup.callback(stop_disposable_effect_runtimes, root)
        runtime, index = _write_authority(root)
        before = index.read_bytes()
        host = root / "controlled_host.py"
        dispatches = root / "dispatches.json"
        host.write_text(
            "import json, pathlib, sys\n"
            "path = pathlib.Path(sys.argv[1])\n"
            "count = json.loads(path.read_text()) if path.exists() else 0\n"
            "path.write_text(json.dumps(count + 1))\n"
            "print('Controlled model boundary: one request; no semantic delta.')\n"
            "raise SystemExit(int(sys.argv[2]))\n"
        )
        args = argparse.Namespace(cli_bin=str(Path(sys.executable).parent / "loopx"),
            registry=str(root / "registry.json"), runtime_root=str(runtime), runtime_profile="generic_cli",
            goal_id=GOAL, agent_id=AGENT, state_file=str(root / "worker.json"),
            wake_cmd=shlex.join([sys.executable, str(host), str(dispatches), "1" if mode == "unknown" else "0"]),
            once=mode == "unknown", error_backoff_seconds=5.0, quota_timeout_seconds=60, wake_timeout_seconds=15)
        with redirect_stdout(io.StringIO()):
            returned = worker.run_worker(args, sleep=_unexpected_timer)
        assert returned == (1 if mode == "unknown" else 0)
        assert json.loads(dispatches.read_text()) == 1
        assert index.read_bytes() == before
        state = json.loads(Path(args.state_file).read_text())
        original = state["replan_attempts"][0]["obligation_id"]
        receipt = receipt_path_for_goal({"id": GOAL}, root / "project" / "ACTIVE_GOAL_STATE.md")
        synced = sync_project_registry_to_global(registry_path=Path(args.registry),
            runtime_root_override=args.runtime_root, goal_id=GOAL)
        assert synced["ok"] is True
        dashboard_registry = Path(synced["global_registry"])
        output = sys.stdout

        class ObservableChatServer(ChatHTTPServer):
            def serve_forever(self, poll_interval=0.5):
                print(json.dumps({"port": self.server_address[1], "registry": args.registry,
                    "runtime": args.runtime_root, "goal_id": GOAL, "agent_id": AGENT,
                    "obligation_id": original, "receipt": str(receipt), "dispatches": str(dispatches),
                    "dashboard_registry": str(dashboard_registry),
                    "history": str(index)}),
                    file=output, flush=True)
                return super().serve_forever(poll_interval)

        cleanup.enter_context(patch("loopx.chat_server.ChatHTTPServer", ObservableChatServer))

        def terminate(*_):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
        with redirect_stdout(sys.stderr):
            serve_chat(registry_path=dashboard_registry, runtime_root_override=runtime,
                scan_roots=[root / "project"], goal_id=GOAL, host="127.0.0.1", port=0,
                codex_bin=str(root / "no-provider-codex"), claude_bin=str(root / "no-provider-claude"),
                kiro_cli_bin=str(root / "no-provider-kiro"), assets_dir=default_chat_assets_dir(), open_browser=False)


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2])
