"""Production Chat/API and canonical authority in disposable synthetic state."""

from __future__ import annotations
import json
from pathlib import Path
import sys
from loopx.chat_server import serve_chat
from loopx.control_plane.testing.canary_harness import write_fixture_registry
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/control_plane"))
from canonical_authority_fixture import initialize_canonical_authority  # noqa: E402


def main():
    root, port = Path(sys.argv[1]).resolve(), int(sys.argv[2])
    project = root / "project"
    project.mkdir()
    state = project / "GOAL.md"
    state.write_text(
        "---\nstatus: active\n---\n# Persistent ZCode\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    registry, runtime = root / "registry.json", root / "runtime"
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id="persistent-zcode",
        domain="general",
        adapter_kind="generic_project_goal_v0",
        state_file=str(state),
        registered_agents=["zcode-worker"],
        extra_goal_fields={
            "name": "Persistent ZCode",
            "goal_instance_id": "ginst_" + "a" * 32,
            "coordination": {
                "registered_agents": ["zcode-worker"],
                "handoff_mode": "soft_claim",
                "agent_profiles": {"zcode-worker": {"agent_type": "zcode"}},
            },
        },
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="persistent-zcode",
        handoff_mode="soft_claim",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_persistent_work",
                "role": "agent",
                "text": "Implement the synthetic export",
                "status": "open",
                "done": False,
                "task_class": "advancement_task",
                "action_kind": "implement",
                "index": 1,
                "source_section": "Agent Todo",
                "archive_state": "active",
                "claimed_by": "zcode-worker",
            }
        ],
    )
    initialize_canonical_authority(
        runtime, "persistent-zcode", projection, state_path=state
    )
    from loopx.status import collect_status

    collect_status(
        registry_path=registry,
        runtime_root_override=str(runtime),
        limit=5,
    )
    print(
        json.dumps(
            {"root": str(root), "registry": str(registry), "runtime": str(runtime)}
        ),
        flush=True,
    )
    serve_chat(
        registry_path=registry,
        runtime_root_override=runtime,
        scan_roots=[],
        goal_id="persistent-zcode",
        host="127.0.0.1",
        port=port,
        lark_cli_bin="isolated-missing-lark",
        codex_bin="isolated-missing-codex",
    )


if __name__ == "__main__":
    main()
