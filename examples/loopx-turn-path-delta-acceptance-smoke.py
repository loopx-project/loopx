#!/usr/bin/env python3
"""Prove material Turn replans retain a path delta while routine replans stay light."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402
from loopx.control_plane.coordination.local_authority import (  # noqa: E402
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402


GOAL_ID = "loopx-turn-path-delta-fixture"
AGENT_ID = "codex-path-delta-fixture"
TODO_ID = "todo_pathdelta001"
MARKER = "docs/path-delta-route.txt"


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    workspace = project
    runtime.mkdir(parents=True)
    (workspace / "docs").mkdir(parents=True)

    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "# LoopX Turn Path Delta Fixture",
                "",
                "## Next Action",
                "",
                "Validate the original fixture route.",
                "",
                "## Agent Todo",
                "",
                "- [ ] [P0] Validate one material route change and one routine continuation.",
                (
                    f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
                    "task_class=advancement_task action_kind=path_delta_acceptance "
                    f"claimed_by={AGENT_ID} priority=P0 -->"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "loopx-turn-public-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "adapter": {"kind": "fixture_v0", "status": "connected-delivery"},
                        "quota": {"compute": 10.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                            "agent_profiles": {
                                AGENT_ID: {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "fixture",
                                    "scope": "public qualification",
                                }
                            },
                            "write_scope": ["docs/**"],
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, runtime, workspace, registry


def _write_host(root: Path) -> Path:
    host = root / "path-delta-host.py"
    host.write_text(
        f"""
import json
import pathlib
import sys

request = json.load(sys.stdin)
mode = sys.argv[1]
marker = pathlib.Path({MARKER!r})
marker.write_text(mode, encoding="utf-8")
with marker.with_suffix(".calls").open("a", encoding="utf-8") as calls:
    calls.write(mode + "\\n")
material_packet = {{
    "agent_id": {AGENT_ID!r},
    "state": "active",
    "vision_patch": {{
        "direction": "Validate the revised public fixture route.",
        "acceptance_summary": "The revised route and its routine continuation both pass independent validation.",
        "replan_trigger_summary": "Replan only if the revised route fails its validator.",
        "advancement_policy": "repeat_until_closed"
    }},
    "path_delta": {{
        "schema_version": "goal_path_delta_v0",
        "outcome": "replan",
        "prior_assumption": "The original fixture route would satisfy validation.",
        "observed_reality": "The route contract changed and the original path is no longer valid.",
        "retained": ["Keep the independent validator."],
        "changed": ["Use the revised fixture route."],
        "stopped": ["Stop executing the original route."],
        "evidence_refs": ["fixture:path-delta-route"]
    }}
}}
material = mode == "material"
json.dump({{
    "schema_version": "loopx_turn_result_v0",
    "turn_key": request["turn_key"],
    "result_kind": "replan_required",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": f"fixture_{{mode}}_replan",
    "recommended_action": "Continue on the revised public fixture route.",
    "next_action": (
        "Run one routine continuation on the revised route."
        if material else "Keep the revised route until new evidence appears."
    ),
    "delivery_batch_scale": "single_surface",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": (
        "The revised fixture vision remains valid." if not material else ""
    ),
    "path_delta_mode": "material_replan" if material else "unchanged",
    "agent_vision_json": json.dumps(material_packet) if material else "",
    "summary": f"The {{mode}} fixture route passed validation."
}}, sys.stdout)
""",
        encoding="utf-8",
    )
    return host


def _host_command(host: Path, mode: str) -> list[str]:
    return [sys.executable, str(host), mode]


def _validator_command(expected: str) -> list[str]:
    program = (
        "import json,pathlib,sys; "
        "json.load(sys.stdin); "
        f"p=pathlib.Path({MARKER!r}); "
        f"raise SystemExit(0 if p.read_text(encoding='utf-8') == {expected!r} else 9)"
    )
    return [sys.executable, "-c", program]


def _run_turn(
    *,
    registry: Path,
    runtime: Path,
    project: Path,
    workspace: Path,
    host: Path,
    mode: str,
    expect_canonical_refusal: bool = False,
    resume_turn_key: str | None = None,
) -> dict[str, Any]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "run-once",
                "--host",
                "generic-cli",
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                AGENT_ID,
                *(
                    ["--resume-turn-key", resume_turn_key]
                    if resume_turn_key else ["--turn-instance-id", f"path-delta-{mode}"]
                ),
                "--project",
                str(workspace),
                "--host-adapter-command-json",
                json.dumps(_host_command(host, mode)),
                "--validation-command-json",
                json.dumps(_validator_command(mode)),
                "--no-global-sync",
                "--execute",
            ]
        )
    payload = json.loads(output.getvalue())
    if expect_canonical_refusal:
        assert exit_code != 0, payload
        assert payload["reason_code"] == "canonical_authority_required", payload
        assert payload["write_admitted"] is False, payload
        assert payload["journal_observation"]["recorded_effects"]["host_invoked"] is True, payload
        assert payload["journal_observation"]["recorded_effects"]["quota_spent"] is False, payload
        assert payload["journal_observation"]["recovery_decision"]["reinvoke_host"] is False, payload
        return payload
    assert exit_code == 0, payload
    assert payload["status"] == "committed", payload
    assert payload["validation"]["status"] == "passed", payload
    return payload


def _run_records(runtime: Path) -> list[dict[str, Any]]:
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    return [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]


def assert_canonical_turn_path_delta(provider: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"loopx-turn-path-delta-{provider}-") as directory:
        root = Path(directory)
        project, runtime, workspace, registry = _write_fixture(root)
        host = _write_host(root)
        state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        source_bytes = state.read_bytes()
        refused = _run_turn(
            registry=registry,
            runtime=runtime,
            project=project,
            workspace=workspace,
            host=host,
            mode="material",
            expect_canonical_refusal=True,
        )
        # The Host has returned, but no Todo write/spend is admitted. Import
        # the complete stopped fixture and recover that original Turn.
        assert state.read_bytes() == source_bytes
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) is None
        calls = (project / MARKER).with_suffix(".calls")
        assert calls.read_text(encoding="utf-8").splitlines() == ["material"]
        import_fixture_authority(registry, GOAL_ID, provider=provider)
        original = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert original is not None
        assert original["source_authority"] == f"{provider}_v0", original
        assert {item["todo_id"] for item in original["todos"]} == {TODO_ID}, original
        recovered = _run_turn(
            registry=registry,
            runtime=runtime,
            project=project,
            workspace=workspace,
            host=host,
            mode="material",
            resume_turn_key=refused["resume_turn_key"],
        )
        assert recovered["resume_turn_key"] == refused["resume_turn_key"], recovered
        assert calls.read_text(encoding="utf-8").splitlines() == ["material"]
        _run_turn(
            registry=registry,
            runtime=runtime,
            project=project,
            workspace=workspace,
            host=host,
            mode="routine",
        )
        assert calls.read_text(encoding="utf-8").splitlines() == ["material", "routine"]
        canonical = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert canonical is not None
        assert canonical["source_authority"] == f"{provider}_v0", canonical
        assert {item["todo_id"] for item in canonical["todos"]} == {TODO_ID}, canonical
        before, after = original["todos"][0], canonical["todos"][0]
        for field in ("todo_id", "text", "role", "status", "claimed_by",
                      "task_class", "action_kind", "priority", "successor_todo_ids"):
            assert after.get(field) == before.get(field), (field, before, after)
        mutable_result_fields = {"note", "evidence", "updated_at", "last_actor_agent_id"}
        assert {k: v for k, v in after.items() if k not in mutable_result_fields} == {
            k: v for k, v in before.items() if k not in mutable_result_fields
        }, (before, after)

        records = _run_records(runtime)
        material = next(
            record
            for record in records
            if record.get("classification") == "fixture_material_replan"
        )
        routine = next(
            record
            for record in records
            if record.get("classification") == "replan_noop"
        )
        path_delta = material["agent_vision"]["path_delta"]
        assert path_delta["schema_version"] == "goal_path_delta_v0", path_delta
        assert path_delta["changed"] == ["Use the revised fixture route."], path_delta
        assert material["vision_checkpoint"]["decision"] == "patched", material
        assert "agent_vision" not in routine, routine
        assert routine["vision_checkpoint"]["decision"] == "unchanged_with_reason", routine


def main() -> int:
    for provider in ("file", "sqlite"):
        assert_canonical_turn_path_delta(provider)
    print("loopx-turn-path-delta-acceptance-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
