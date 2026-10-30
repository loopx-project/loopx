import sys
import os
import json
from pathlib import Path
import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    empty_todo_display,
)
from test_resume_planning_projection import _quota_goal
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)

root = Path(sys.argv[1])
provider = sys.argv[2]
count = int(sys.argv[3])
root.mkdir(parents=True, exist_ok=True)

mp = pytest.MonkeyPatch()
runtime, registry, state = _quota_goal(root, mp, "", None)
history = [
    {
        "todo_id": f"todo_history_{i:04d}",
        "schema_version": "todo_item_v0",
        "role": "agent",
        "source_section": "Agent Todo",
        "archive_state": "active",
        "status": "done",
        "done": True,
        "task_class": "advancement_task",
        "text": ("Complete synthetic description " * 40).rstrip(),
    }
    for i in range(count)
]
tail = [
    {
        "todo_id": "todo_tail_owned",
        "schema_version": "todo_item_v0",
        "role": "agent",
        "source_section": "Agent Todo",
        "archive_state": "active",
        "status": "open",
        "done": False,
        "task_class": "advancement_task",
        "claimed_by": "agent-a",
        "text": "Complete the final selected work.",
    }
]
projection = build_todo_runtime_shadow_projection(
    goal_id="goal-a", todos=history + tail, handoff_mode="soft_claim"
)
initialize_canonical_authority(
    runtime, "goal-a", projection, state_path=state, provider=provider
)
empty_todo_display(state)
for path in root.rglob("*"):
    if path.is_file():
        os.utime(path, (1793235600, 1793235600))

# Use the real acceptance owner so every cell exercises required-view assembly,
# not only the no-op plan/codec route. This is outside all timing windows.
import subprocess
cli = [sys.executable, "-m", "loopx.entrypoint", "--cli-bin", "loopx-eval", "--registry", str(registry),
       "--runtime-root", str(runtime), "--format", "json", "goal-acceptance"]
def acceptance(*arguments):
    result = subprocess.run([*cli, *arguments, "--goal-id", "goal-a"], capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout[-2000:]
    return json.loads(result.stdout)

document = root / "outcome.json"
for index in range(8):
    document.write_text(json.dumps({"outcome_id": "checked-change", "owner_agent_id": "agent-a",
        "todo_ids": ["todo_tail_owned"], "objective": "Complete the checked change.",
        "requirements": [{"id": "preservation", "description": "Retain the complete original constraints."},
                         {"id": "stop", "description": "Stop before publication without independent acceptance."}],
        "source": {"ref": f"message:original-{index}", "summary": ("Original supporting material. " * 16).strip()}}))
    acceptance("refine", "--agent-id", "agent-a", "--document", str(document),
               "--expected-provider-revision", acceptance("inspect")["provider_revision"],
               "--operation-id", f"source-refinement-{index}", "--execute")

from loopx.control_plane.turn_driver import codex_context
from evaluate import command, environment

native = root / "native"
native.mkdir(mode=0o700)
captures = root / "captures"
captures.mkdir(mode=0o700)
os.environ["CODEX_THREAD_ID"] = "c2-eval-native-session"
codex_context.codex_context_hook(str(native), dict(session_id="c2-eval-native-session", cwd=str(root),
    hook_event_name="SessionStart", source="startup"))
value = codex_context._state(native)
value["context_epoch"] = "c2-eval-fixed-epoch"
codex_context._save(native, value)
(root / "cold-state.json").write_bytes((native / "session.json").read_bytes())
manifest = {"registry": str(registry), "runtime": str(runtime), "project": str(root),
            "provider": provider, "history": count, "native": str(native), "captures": str(captures)}
env = environment("base", "fixture-setup")
env["C2_CAPTURE_LABEL"] = "baseline"
result = __import__("subprocess").run(command(manifest, "base"), env=env, cwd=root,
    capture_output=True, timeout=120)
(root / "setup.stdout.json").write_bytes(result.stdout)
(root / "setup.stderr").write_bytes(result.stderr)
assert result.returncode == 0, (result.returncode, result.stdout[-2000:], result.stderr)
response = json.loads(result.stdout)
assert response["ok"] and response["mode"] == "full", response
assert Path(response["recovery"]["path"]).name == "required-context.json", "staging must be active"
(root / "warm-state.json").write_bytes((native / "session.json").read_bytes())
print(json.dumps(manifest))
