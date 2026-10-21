"""A fresh public demo must reach the real Turn admission without credentials."""

import json
from pathlib import Path
import subprocess
import sys


def test_demo_preparation_has_executable_independent_worktree(tmp_path):
    demo = (
        Path(__file__).resolve().parents[1] / "examples/collaboration-delivery/demo.py"
    )
    root = tmp_path / "demo"
    subprocess.run(
        [sys.executable, str(demo), "prepare", "--root", str(root)],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(root / "registry.json"),
            "--runtime-root",
            str(root / "runtime"),
            "--format",
            "json",
            "todo",
            "add",
            "--goal-id",
            "allocation-demo",
            "--role",
            "agent",
            "--claimed-by",
            "builder",
            "--text",
            "Implement the allocation planner",
            "--action-kind",
            "implement",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["ok"]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(root / "registry.json"),
            "--runtime-root",
            str(root / "runtime"),
            "--format",
            "json",
            "turn",
            "plan",
            "--goal-id",
            "allocation-demo",
            "--agent-id",
            "builder",
            "--host",
            "dsh",
            "--execution-mode",
            "isolated-headless",
        ],
        cwd=root / "agents/builder",
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    # The test supplies no provider credentials. Qualify the Goal/worktree
    # admission independently of provider availability and never invoke a host.
    assert plan["turn_envelope"]["execution_policy"]["normal_delivery_allowed"], plan[
        "turn_envelope"
    ]["reason"]
    assert not plan["effects"]["host_invoked"]
    # Preparation cannot overwrite an existing state; model runs need explicit opt-in.
    for args in (["prepare"], ["run", "--phase", "builder-1"]):
        result = subprocess.run(
            [sys.executable, str(demo), *args, "--root", str(root)], capture_output=True
        )
        assert result.returncode != 0


def test_demo_review_validation_requires_current_acceptance_version(tmp_path):
    import hashlib
    from loopx.control_plane.collaboration.peers import request

    demo = (
        Path(__file__).resolve().parents[1] / "examples/collaboration-delivery/demo.py"
    )
    root = tmp_path / "demo"
    workspace = root / "agents/builder"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "outputs").mkdir()
    refs = ["solver.py", "REQUIREMENTS.md", "inputs/scenario.json", "outputs/plan.json"]
    for ref in refs:
        (workspace / ref).write_text("initial")
    registry = root / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "allocation-demo",
                        "repo": str(root),
                        "coordination": {"registered_agents": ["builder", "reviewer"]},
                    }
                ]
            }
        )
    )
    (root / "demo.json").write_text(json.dumps({"schema": "allocation_demo_v1"}))

    def send(operation):
        return request(
            root / "runtime",
            registry,
            "allocation-demo",
            "builder",
            "reviewer",
            operation,
            {
                "schema_version": "collaboration_brief_v0",
                "purpose": "Review",
                "context": "Current acceptance",
                "constraints": [],
                "acceptance": ["Use the current contract"],
                "return_requirement": "Findings",
                "inputs": [
                    {
                        "ref": ref,
                        "description": ref,
                        "sha256": hashlib.sha256(
                            (workspace / ref).read_bytes()
                        ).hexdigest(),
                    }
                    for ref in refs
                ],
            },
        )

    send("old-review")
    (workspace / "REQUIREMENTS.md").write_text("corrected acceptance")
    send("new-review")
    command = [
        sys.executable,
        str(demo),
        "validate",
        "--root",
        str(root),
        "--phase",
        "builder-2",
    ]
    assert subprocess.run(command, capture_output=True).returncode == 0
    # Neither unchanged code nor an old review qualifies another contract revision.
    (workspace / "REQUIREMENTS.md").write_text("third acceptance version")
    assert subprocess.run(command, capture_output=True).returncode != 0
