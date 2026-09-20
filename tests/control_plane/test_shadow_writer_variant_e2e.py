"""Public writer combinations with real persistence and scheduling-only seams."""
from __future__ import annotations

import json
from pathlib import Path
import select
import subprocess
import sys

import pytest

from shadow_e2e_fixture import ShadowWorkspace, workspace as make_workspace
from loopx.control_plane.coordination.coordination_state_contract_generated import TASK_LEASE_ACQUIRE_REQUEST_SCHEMA
from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path
from loopx.control_plane.work_items.task_lease_acquire_adapter import task_lease_acquire_authority_facts

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.stage2c_e2e


def workspace(path: Path) -> ShadowWorkspace:
    result = make_workspace(path, bootstrap=False)
    result.state.write_text(result.state.read_text() + "\n## Progress Ledger\n\n## Next Action\n\n- Inspect.\n")
    return result


def command(ws: ShadowWorkspace, *args: str, goal: str | None = None) -> list[str]:
    return [sys.executable, "-m", "loopx.cli", "--registry", str(ws.registry),
            "--runtime-root", str(ws.runtime), "--format", "json", *args, "--goal-id", goal or ws.goal]


def public(ws: ShadowWorkspace, *args: str, goal: str | None = None) -> dict:
    result = subprocess.run(command(ws, *args, goal=goal), cwd=REPO, capture_output=True, text=True, timeout=30)
    assert "Traceback" not in result.stderr, result.stderr
    return json.loads(result.stdout)


def start(args: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(args, cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def barrier(child: subprocess.Popen[str], phase: str) -> None:
    assert child.stdout is not None
    ready, _, _ = select.select([child.stdout], [], [], 10)
    assert ready, "public writer did not reach its real scheduling boundary"
    line = child.stdout.readline().strip()
    assert line == "BARRIER " + phase, line


def finish(child: subprocess.Popen[str], *, resume: bool = False) -> dict:
    output, error = child.communicate("continue\n" if resume else None, timeout=30)
    assert "Traceback" not in error, output + error
    return json.loads(output)


def stop(child: subprocess.Popen[str] | None) -> None:
    if child is not None:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


NATIVE = r"""
import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
import {once} from 'node:events';
const input = JSON.parse(process.argv[1]);
if (input.wait_lock) {
  const actual = fs.promises.open;
  let notified = false;
  fs.promises.open = async (path, flags, ...args) => {
    if (String(path) === input.wait_lock + '.ts-effect.lock' && flags === 'wx' && !notified) {
      notified = true; process.stdout.write('BARRIER native-lock\n');
    }
    return await actual(path, flags, ...args);
  };
  syncBuiltinESMExports();
}
const owner = await import(input.module);
const dependencies = input.pause_write ? {beforeWrite:async () => {
  process.stdout.write('BARRIER native-write\n'); await once(process.stdin, 'data');
}} : {};
process.stdout.write(JSON.stringify(await owner[input.function](input.request, dependencies)) + '\n');
"""


def native(module: str, function: str, request: dict, **options: object) -> list[str]:
    return ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", NATIVE,
            json.dumps({"module": (REPO / module).as_uri(), "function": function, "request": request, **options})]


def fence_request(ws: ShadowWorkspace) -> dict:
    return {"schema_version": "loopx_legacy_coordination_writer_fence_engage_request_v0",
            "runtime_root": str(ws.runtime), "goal_id": ws.goal, "state_path": str(ws.state),
            "fence": {"schema_version": "loopx_legacy_coordination_writer_fence_v0", "state": "engaged",
                "goal_id": ws.goal, "fence_id": "variant-fence", "source_version": "variant-source",
                "source_projection_sha256": "a" * 64,
                "expected_shadow_provider_revision": "file:1:aaaaaaaaaaaaaaaaaaaaaaaa"}}


def fence_command(ws: ShadowWorkspace, **options: object) -> list[str]:
    return native("loopx/control_plane/coordination/legacy_writer_fence.ts",
                  "engageLegacyCoordinationWriterFence", fence_request(ws), **options)


@pytest.mark.parametrize("authority", ["fence", "active_capture"])
def test_other_goal_cannot_write_a_protected_goal_source_via_state_override(tmp_path: Path, authority: str) -> None:
    ws = workspace(tmp_path)
    other_state = tmp_path / "OTHER_GOAL_STATE.md"
    other_state.write_text("---\ngoal_id: goal-other\n---\n\n## Agent Todo\n")
    registry = json.loads(ws.registry.read_text())
    registry["goals"].append({"id": "goal-other", "repo": str(tmp_path), "state_file": other_state.name,
                              "coordination": {"registered_agents": ["agent-a"]}})
    ws.registry.write_text(json.dumps(registry))
    # Preserve the existing opt-out contract before any source authority exists.
    legacy = public(ws, "todo", "add", "--state-file", str(ws.state), "--role", "agent",
                    "--text", "An unbound shared-state write remains supported.",
                    "--evidence", "Legacy compatibility control.", goal="goal-other")
    assert legacy["ok"] is True and legacy["added"] is True, legacy
    if authority == "active_capture":
        # A registered source stays protected even without a frontmatter owner.
        ws.state.write_text(ws.state.read_text().replace(f"goal_id: {ws.goal}\n", ""))
        assert public(ws, "coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    else:
        result = subprocess.run(fence_command(ws), cwd=REPO, capture_output=True, text=True, check=True, timeout=20)
        assert json.loads(result.stdout)["status"] == "applied"
    before = ws.state.read_bytes(), other_state.read_bytes()
    result = public(ws, "todo", "add", "--state-file", str(ws.state), "--role", "agent",
                    "--text", "Must not bypass another goal's source authority.",
                    "--evidence", "Cross-goal source boundary.", goal="goal-other")
    assert not result.get("ok"), json.dumps(result, indent=2)
    expected = "shadow_source_goal_mismatch" if authority == "active_capture" else "legacy_coordination_writer_fenced"
    assert result["error_code"] == expected, result
    assert (ws.state.read_bytes(), other_state.read_bytes()) == before
    assert not (ws.runtime / "authority-shadow" / "outbox" / "goal-other").exists()
    if authority == "active_capture":
        unknown = public(ws, "todo", "add", "--state-file", str(ws.state), "--role", "agent",
                         "--text", "An unregistered goal cannot bypass source ownership.",
                         "--evidence", "Unregistered goal control.", goal="unregistered-goal")
        assert not unknown.get("ok"), unknown
        assert (ws.state.read_bytes(), other_state.read_bytes()) == before


PUBLIC_WORKER = r"""
import sys
from contextlib import contextmanager
from pathlib import Path
stage = sys.argv[1]
def pause(label):
    print('BARRIER ' + label, flush=True)
    sys.stdin.readline()
if stage == 'reward_plan':
    from loopx import feedback
    original = feedback.plan_active_state_update
    def planned(*args, **kwargs):
        result = original(*args, **kwargs)
        pause('reward-plan')
        return result
    feedback.plan_active_state_update = planned
elif stage == 'reward_write':
    from loopx import feedback
    original = feedback.atomic_write_state_text
    def write(*args, **kwargs):
        pause('reward-write')
        return original(*args, **kwargs)
    feedback.atomic_write_state_text = write
elif stage == 'handoff_write':
    from loopx.control_plane.todos import active_state_editing
    original = active_state_editing.atomic_write_state_text
    def write(*args, **kwargs):
        pause('handoff-write')
        return original(*args, **kwargs)
    active_state_editing.atomic_write_state_text = write
elif stage == 'handoff_k':
    from loopx import file_lock
    original = file_lock.exclusive_cross_runtime_file_lock
    @contextmanager
    def lock(path, *args, **kwargs):
        if Path(path).name == '.task-leases':
            print('BARRIER handoff-lock', flush=True)
        with original(path, *args, **kwargs) as held:
            yield held
    file_lock.exclusive_cross_runtime_file_lock = lock
from loopx.entrypoint import main
raise SystemExit(main(sys.argv[2:]))
"""


def paused_public(ws: ShadowWorkspace, stage: str, *args: str) -> subprocess.Popen[str]:
    return start([sys.executable, "-c", PUBLIC_WORKER, stage, *command(ws, *args)[3:]])


@pytest.mark.parametrize("first", ["lease", "handoff"])
def test_handoff_and_native_acquire_serialize_quiescence_and_source_evidence(tmp_path: Path, first: str) -> None:
    ws = workspace(tmp_path)
    added = ws.add("Unclaimed work eligible for a lease.")
    todo_id = added["todo_id"]
    assert public(ws, "coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    request = {"schema_version": TASK_LEASE_ACQUIRE_REQUEST_SCHEMA, "runtime_root": str(ws.runtime), "goal_id": ws.goal,
        "todo_id": todo_id, "owner": "agent-a", "idempotency_key": "native-variant-acquire", "ttl_seconds": 120,
        "write_scopes": [], "expected_version": None,
        "authority": task_lease_acquire_authority_facts(registry_path=ws.registry, goal_id=ws.goal, todo_id=todo_id)}
    lease_dir = ws.runtime / "goals" / ws.goal / "task-leases"
    def lease_command(**options: object) -> list[str]:
        return native("loopx/control_plane/work_items/task_lease_acquire.ts",
                      "executeTaskLeaseAcquire", request, **options)
    handoff_args = ("handoff-mode", "set", "--mode", "soft_claim")
    lease = handoff = None
    original = ws.state.read_bytes()
    try:
        if first == "lease":
            lease = start(lease_command(pause_write=True))
            barrier(lease, "native-write")
            handoff = paused_public(ws, "handoff_k", *handoff_args)
            barrier(handoff, "handoff-lock")
            assert handoff.poll() is None
            acquired = finish(lease, resume=True)
            refused = finish(handoff)
            assert acquired["acquired"] is True, acquired
            assert refused["error_code"] == "handoff_mode_not_quiescent", refused
            assert ws.state.read_bytes() == original
            assert json.loads((lease_dir / f"{todo_id}.json").read_text())["owner"] == "agent-a"
        else:
            handoff = paused_public(ws, "handoff_write", *handoff_args)
            barrier(handoff, "handoff-write")
            lease = start(lease_command(wait_lock=str(lease_dir / ".task-leases")))
            barrier(lease, "native-lock")
            assert lease.poll() is None
            changed = finish(handoff, resume=True)
            refused = finish(lease)
            assert changed["changed"] is True, changed
            assert refused["error_code"] == "authority_source_changed", refused
            assert "handoff_mode: soft_claim" in ws.state.read_text()
            assert not (lease_dir / f"{todo_id}.json").exists()
        public(ws, "authority-shadow", "drain")
        inspected = public(ws, "coordination-shadow", "inspect")
        assert inspected["inspection"]["status"] == "matched", inspected
        assert inspected["inspection"]["cursor"] == "2", inspected
    finally:
        stop(lease)
        stop(handoff)


def prepare_rewards(ws: ShadowWorkspace) -> Path:
    index = ws.runtime / "goals" / ws.goal / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({"generated_at": "2026-09-05T00:00:00Z", "json_path": "run.json",
                                "markdown_path": "run.md", "classification": "continue"}) + "\n")
    return index


def reward_args(reason: str, timestamp: str) -> tuple[str, ...]:
    return ("reward", "--actor-kind", "owner", "--decision", "continue", "--reward", "positive",
            "--reason-summary", reason, "--recorded-at", timestamp, "--write-active-state-summary")


def test_concurrent_public_rewards_preserve_both_summaries_and_run_overlays(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    index = prepare_rewards(ws)
    assert public(ws, "coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    first = paused_public(ws, "reward_plan", *reward_args("First independent review.", "2026-09-05T00:01:00Z"))
    try:
        barrier(first, "reward-plan")
        second = public(ws, *reward_args("Second independent review.", "2026-09-05T00:02:00Z"))
        assert second["ok"] is True, second
        result = finish(first, resume=True)
        assert result["ok"] is True, result
        text = ws.state.read_text()
        assert text.count("First independent review.") == 1
        assert text.count("Second independent review.") == 1
        overlays = [json.loads(line)["human_reward"]["reason_summary"] for line in index.read_text().splitlines()[1:]]
        assert sorted(overlays) == ["First independent review.", "Second independent review."]
        inspection = public(ws, "coordination-shadow", "inspect")["inspection"]
        assert inspection["status"] == "matched", inspection
        assert inspection["cursor"] == "1", inspection
    finally:
        stop(first)


def test_native_fence_waits_for_public_prose_and_then_blocks_todo_writes(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    index = prepare_rewards(ws)
    prose = paused_public(ws, "reward_write", *reward_args("Prose committed before the fence.", "2026-09-05T00:01:00Z"))
    fence = None
    try:
        barrier(prose, "reward-write")
        fence = start(fence_command(ws, wait_lock=str(ws.state)))
        barrier(fence, "native-lock")
        fence_path = legacy_coordination_writer_fence_path(runtime_root=ws.runtime, goal_id=ws.goal)
        assert not fence_path.exists()
        assert fence.poll() is None
        assert finish(prose, resume=True)["ok"] is True
        assert finish(fence)["status"] == "applied"
        before = ws.state.read_bytes()
        result = public(
            ws, "todo", "add", "--role", "agent", "--text", "Must now be fenced.",
            "--evidence", "Boundary check.",
        )
        assert result["error_code"] == "local_authority_todo_list_unavailable", result
        assert ws.state.read_bytes() == before
        assert "Prose committed before the fence." in ws.state.read_text()
        assert len(index.read_text().splitlines()) == 2
    finally:
        stop(prose)
        stop(fence)
