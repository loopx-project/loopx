from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]


def default_state_file(goal_id: str) -> str:
    # Legacy route is intentional: existing characterization fixtures create
    # state there and declare it explicitly in their synthetic registries.
    return f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"


def project_state_path(project: Path, goal_id: str, *, state_file: str | None = None) -> Path:
    return project / (state_file or default_state_file(goal_id))


def run_json_cli(
    *args: str,
    registry_path: Path,
    runtime_root: Path | None = None,
    cwd: Path | None = None,
    include_returncode: bool = True,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--format",
        "json",
        *args,
    ]
    if runtime_root is not None:
        command[5:5] = ["--runtime-root", str(runtime_root)]
    result = subprocess.run(
        command,
        cwd=cwd or REPO_ROOT,
        check=False,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    if not result.stdout.strip():
        raise AssertionError(f"empty CLI output for {command!r}: {result.stderr}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object for {command!r}: {payload!r}")
    if include_returncode:
        payload["_returncode"] = result.returncode
    return payload


def run_json_cli_result(
    *args: str,
    registry_path: Path,
    runtime_root: Path | None = None,
    cwd: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return process status separately from the producer's unchanged JSON.

    Callers can validate or replay the returned wire without test metadata
    changing content commitments or overwriting a producer-owned field.
    """
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--format",
        "json",
        *args,
    ]
    if runtime_root is not None:
        command[5:5] = ["--runtime-root", str(runtime_root)]
    result = subprocess.run(
        command,
        cwd=cwd or REPO_ROOT,
        check=False,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
    )
    if not result.stdout.strip():
        raise AssertionError(f"empty CLI output for {command!r}: {result.stderr}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object for {command!r}: {payload!r}")
    return result.returncode, payload


def runtime_root_from_registry(registry_path: Path) -> Path | None:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    runtime_root = payload.get("common_runtime_root") if isinstance(payload, dict) else None
    return Path(runtime_root) if runtime_root else None


def write_fixture_registry(
    *,
    project: Path,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    domain: str,
    adapter_kind: str,
    adapter_status: str = "connected",
    status: str = "active",
    state_file: str | None = None,
    state_event_log: str | None = None,
    registered_agents: Iterable[str] | None = (),
    quota_allowed_slots: int | None = 10,
    peer_independent_worktree_required: bool | None = None,
    extra_goal_fields: dict[str, Any] | None = None,
) -> Path:
    state_file_value = state_file or default_state_file(goal_id)
    coordination: dict[str, Any] | None = None
    if registered_agents is not None:
        coordination = {
            "registered_agents": list(registered_agents),
            "agent_model": "peer_v1",
        }

    goal: dict[str, Any] = {
        "id": goal_id,
        "domain": domain,
        "status": status,
        "repo": str(project),
        "state_file": state_file_value,
        "adapter": {
            "kind": adapter_kind,
            "status": adapter_status,
        },
        "authority_sources": [],
    }
    if coordination is not None:
        goal["coordination"] = coordination
    if quota_allowed_slots is not None:
        goal["quota"] = {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": quota_allowed_slots,
        }
    if state_event_log:
        goal["state_event_log"] = state_event_log
    if peer_independent_worktree_required is not None:
        goal["workspace_guard_policy"] = {
            "peer_independent_worktree_required": peer_independent_worktree_required,
        }
    if extra_goal_fields:
        goal.update(extra_goal_fields)

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime_root),
                "goals": [goal],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def read_run_index(runtime_root: Path, goal_id: str) -> list[dict[str, Any]]:
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    if not index_path.exists():
        return []
    return [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def import_fixture_authority(registry_path: Path, goal_id: str, *, provider: str = "file") -> None:
    """Import a disposable smoke's complete source through the real saved plan.

    Fixture writers have finished before this call. Historical metadata stays
    intact; normal writes afterwards use the selected canonical/soft-claim owner.
    Existing unpromoted/refusal fixtures opt in explicitly, never by default.
    """
    from ...state_backup import build_state_backup_plan, execute_state_backup_plan

    registry_path = registry_path.resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    goal = next(item for item in registry["goals"] if item["id"] == goal_id)
    project = Path(goal["repo"]).resolve()
    runtime = Path(registry["common_runtime_root"]).resolve()
    # Bind the physical source consistently on hosts whose temp path is a link.
    goal["repo"] = str(project)
    state_file = Path(goal["state_file"]).expanduser()
    if not state_file.is_absolute():
        state_file = project / state_file
    goal["state_file"] = str(state_file.resolve())
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    runtime.mkdir(parents=True, exist_ok=True)
    backup = execute_state_backup_plan(build_state_backup_plan(
        project=project, runtime_root=runtime,
        output_dir=runtime.parent / f"{runtime.name}-fixture-backups", backup_id=goal_id,
        include_automations=False, include_skills=False,
        include_registry_projects=False, registry_path=registry_path,
    ))
    assert backup["ok"], backup
    prefix = ("coordination-shadow",)
    identity = ("--goal-id", goal_id, "--operation-id", f"fixture-import:{goal_id}")
    prepared = run_json_cli(
        *prefix, "prepare-import", *identity,
        "--backup-manifest", backup["manifest_path"],
        "--provider", provider, "--target-handoff-mode", "soft_claim",
        registry_path=registry_path, runtime_root=runtime,
    )["cold_import"]
    assert prepared["status"] == "prepared", prepared
    # Read the saved carrier rather than manufacturing a digest/target.
    plan = json.loads(Path(prepared["plan_path"]).read_text(encoding="utf-8"))
    assert plan["plan_sha256"] == prepared["plan_sha256"], plan
    applied = run_json_cli(
        *prefix, "apply-import", *identity,
        "--plan-sha256", prepared["plan_sha256"], "--writers-stopped", "--execute",
        registry_path=registry_path, runtime_root=runtime,
    )["cold_import"]
    assert applied["status"] in {"applied", "recovered"}, applied
    readback = run_json_cli(
        "todo", "list", "--goal-id", goal_id,
        registry_path=registry_path, runtime_root=runtime,
    )["authority_read"]
    assert readback["source_authority"] == f"{provider}_v0", readback
    assert readback["decision_read_from_provider"] is True, readback
    assert readback["legacy_fallback_used"] is False, readback


def assert_todo_read_unchanged(current, previous):
    """Compare complete readback except validated evaluator/observation clocks."""
    def stable(payload):
        payload = without_resume_observation_time(payload)
        envelope = payload["projection_envelope"]
        assert envelope["schema_version"] == "loopx_projection_envelope_v0"
        for key in ("observed_at", "served_at"):
            assert datetime.fromisoformat(envelope.pop(key).replace("Z", "+00:00")).tzinfo is not None
        for source in envelope["sources"]:
            assert datetime.fromisoformat(source.pop("last_read_at").replace("Z", "+00:00")).tzinfo is not None
        return payload

    assert stable(current) == stable(previous)



def without_resume_observation_time(value):
    """Compare all readback facts except the typed evaluator's observation time."""
    if isinstance(value, list):
        return [without_resume_observation_time(item) for item in value]
    if not isinstance(value, dict):
        return value
    is_resume = value.get("schema_version") == "todo_resume_condition_v0"
    if is_resume and "evaluated_at" in value:
        assert datetime.fromisoformat(value["evaluated_at"].replace("Z", "+00:00")).tzinfo is not None
    return {key: without_resume_observation_time(item) for key, item in value.items()
            if not (is_resume and key == "evaluated_at")}


def advance_fixture_authority(
    runtime_root: Path, goal_id: str, projection: dict, *, operation_id: str,
) -> dict:
    """Advance an already initialized disposable store through its real CAS.

    Consumer fixtures may author a later synthetic state, but must not replace
    storage bytes, reset lineage, re-import Markdown or re-engage its fence.
    """
    module = REPO_ROOT / "loopx/control_plane/coordination/local_authority_provider.ts"
    script = (
        f"import {{openLocalAuthorityStore}} from {json.dumps(module.as_uri())};"
        "let input='';for await(const chunk of process.stdin)input+=chunk;"
        "const request=JSON.parse(input);"
        "const store=await openLocalAuthorityStore(request.root,request.goal);"
        "const head=await store.loadAuthority();"
        "if(head.status!=='loaded')throw new Error(JSON.stringify(head));"
        "const result=await store.commitAuthority({expected_provider_revision:head.provider_revision,"
        "operation_id:request.operation_id,events:[],next_projection:request.projection,receipts:[]});"
        "process.stdout.write(JSON.stringify(result));"
    )
    process = subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps({"root": str(runtime_root), "goal": goal_id,
                         "projection": projection, "operation_id": operation_id}),
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=45,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["status"] == "applied", result
    return result
