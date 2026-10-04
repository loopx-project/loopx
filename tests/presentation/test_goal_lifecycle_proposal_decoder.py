"""Real Goal lifecycle proposals must survive the shipped frontend decoder."""

import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.global_registry import sync_project_registry_to_global


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "apps/presentation/dashboard"


def test_real_lifecycle_previews_and_readback_decode(tmp_path):
    vite = DASHBOARD / "node_modules/vite/bin/vite.js"
    if not shutil.which("node") or not vite.exists():
        pytest.skip("Install dashboard dependencies with npm ci to run frontend integration")
    proposals = []
    for operation in ("stop", "resume", "delete"):
        for stale in (False, True):
            fixture = tmp_path / f"{operation}-{stale}"
            runtime = fixture / "runtime"
            source = fixture / "project/.loopx/registry.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({
                "schema_version": "0.1", "common_runtime_root": str(runtime),
                "goals": [{"id": "fixture-goal", "display_name": "Fixture Goal", "repo": str(fixture / "project")}],
            }))
            assert sync_project_registry_to_global(
                registry_path=source, runtime_root_override=str(runtime),
                goal_id="fixture-goal", dry_run=False,
            )["ok"]
            registry = runtime / "registry.global.json"
            if operation != "stop":
                assert set_goal_activation_state(
                    registry_path=registry, goal_id="fixture-goal", state="stopped",
                    actor_kind="owner", execute=True,
                )["ok"]
            service = ChatActionService(store=ChatActionStore(fixture / "actions"), registry_path=registry)
            before = source.read_bytes()
            proposal = service.preview({
                "action_kind": "goal.lifecycle", "summary": f"{operation} fixture Goal",
                "normalized_parameters": {"goal_id": "fixture-goal", "operation": operation},
                "context": {"kind": "goal_directory"}, "idempotency_key": f"{operation}-{stale}",
            })
            assert source.read_bytes() == before, "preview must not perform the lifecycle effect"
            proposals.append(proposal)
            if stale:
                payload = json.loads(source.read_text())
                payload["goals"][0]["display_name"] = "Changed after preview"
                source.write_text(json.dumps(payload))
            outcome = service.apply(proposal["proposal_id"])["proposal"]
            assert outcome["status"] == ("stale" if stale else "applied")
            if stale:
                assert outcome["receipt"] is None
            else:
                assert outcome["receipt"]["projection_verified"] is True
                for path in (source, registry):
                    goals = json.loads(path.read_text())["goals"]
                    if operation == "delete":
                        assert not goals
                    else:
                        assert goals[0]["activation"]["state"] == ("stopped" if operation == "stop" else "active")
            proposals.append(outcome)
    # Keep the disposable ESM runner under node_modules so externalized packages
    # resolve on Windows too, without requiring symlink privileges.
    cache = DASHBOARD / "node_modules/.cache"
    cache.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="goal-lifecycle-", dir=cache) as directory:
        bundle = Path(directory)
        subprocess.run([
            "node", str(vite), "build", "--ssr",
            "smoke/goal-lifecycle-proposal-smoke.ts", "--outDir", str(bundle), "--emptyOutDir",
        ], cwd=DASHBOARD, check=True, capture_output=True, text=True, timeout=60)
        (bundle / "package.json").write_text('{"type":"module"}')
        result = subprocess.run([
            "node", str(bundle / "goal-lifecycle-proposal-smoke.js"),
        ], input=json.dumps(proposals), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PASS 12 real lifecycle proposals" in result.stdout
