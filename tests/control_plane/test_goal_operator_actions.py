from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys
from typing import Any

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from loopx.control_plane.goals.activation import build_goal_activation
from loopx.global_registry import sync_project_registry_to_global
from loopx.history import load_registry
from loopx.registry import registry_goals


REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "goal-actions-fixture"
INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _run_projected_argv(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the catalog's complete argv without injecting hidden context."""
    assert argv and argv[0] == "loopx"
    # Use the checkout's launcher as the executable while preserving every
    # argument emitted by the public action contract verbatim.
    command = [str(REPO_ROOT / "scripts" / "loopx"), *argv[1:]]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LOOPX_PYTHON"] = sys.executable
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _write_registry(
    tmp_path: Path,
    *,
    activation_state: str = "active",
    goal_instance_id: str | None = None,
) -> tuple[Path, Path]:
    project = tmp_path / "project"
    runtime_root = tmp_path / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    goal: dict[str, Any] = {
        "id": GOAL_ID,
        "display_name": "Goal actions fixture",
        "repo": str(project),
        "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
    }
    if activation_state == "stopped":
        goal["activation"] = build_goal_activation(
            state="stopped",
            updated_at="2026-04-01T00:00:00Z",
            reason="Fixture goal stopped.",
        )
    if goal_instance_id is not None:
        goal["goal_instance_id"] = goal_instance_id
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime_root),
                "goals": [goal],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, registry_path


def _strict_envelope(payload: dict[str, Any]) -> list[object]:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return [
        {
            "schema_version": "loopx_project_registry_envelope_v1",
            "minimum_writer_protocol": "goal_instance_v1",
            "payload_sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        },
        payload,
    ]


def _write_orphaned_global_registry(
    tmp_path: Path,
    *,
    source_status: str,
) -> tuple[Path, Path]:
    source_registry = tmp_path / "removed-project" / ".loopx" / "registry.json"
    if source_status == "goal_missing":
        source_registry.parent.mkdir(parents=True)
        source_registry.write_text(
            json.dumps({"schema_version": "0.1", "goals": []}) + "\n",
            encoding="utf-8",
        )
    elif source_status == "registry_unreadable":
        source_registry.parent.mkdir(parents=True)
        source_registry.write_text("not-json\n", encoding="utf-8")
    elif source_status != "registry_missing":
        raise ValueError(f"unsupported source status fixture: {source_status}")
    global_registry = tmp_path / "runtime" / "registry.global.json"
    global_registry.parent.mkdir(parents=True)
    global_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "registry_role": "global-local",
                "goals": [
                    {
                        "id": GOAL_ID,
                        "goal_instance_id": INSTANCE_A,
                        "display_name": "Orphaned Goal",
                        "source_registry": str(source_registry),
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return source_registry, global_registry


def _run_cli(registry_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_typescript_projection_owns_legal_lifecycle_action() -> None:
    active = effect_runtime_result(
        "goal.operator_actions.project",
        {
            "schema_version": "loopx_goal_action_projection_request_v3",
            "goal_id": GOAL_ID,
            "registry_locator": "/tmp/registry.json",
            "runtime_root_locator": "/tmp/runtime",
            "activation_state": "active",
            "state_fingerprint": "a" * 64,
            "identity_observation": {
                "binding_owner": "source_registry",
                "authority": {
                    "kind": "present",
                    "goal": {"goal_id": GOAL_ID},
                },
                "binding": {"goal_id": GOAL_ID},
            },
        },
    )
    stopped = effect_runtime_result(
        "goal.operator_actions.project",
        {
            "schema_version": "loopx_goal_action_projection_request_v3",
            "goal_id": GOAL_ID,
            "registry_locator": "/tmp/registry.json",
            "runtime_root_locator": "/tmp/runtime",
            "activation_state": "stopped",
            "state_fingerprint": "b" * 64,
            "identity_observation": {
                "binding_owner": "source_registry",
                "authority": {
                    "kind": "present",
                    "goal": {"goal_id": GOAL_ID},
                },
                "binding": {"goal_id": GOAL_ID},
            },
        },
    )
    mismatched = effect_runtime_result(
        "goal.operator_actions.project",
        {
            "schema_version": "loopx_goal_action_projection_request_v3",
            "goal_id": GOAL_ID,
            "registry_locator": "/tmp/registry.json",
            "runtime_root_locator": "/tmp/runtime",
            "activation_state": "active",
            "state_fingerprint": "a" * 64,
            "identity_observation": {
                "binding_owner": "global_projection",
                "authority": {
                    "kind": "present",
                    "goal": {
                        "goal_id": GOAL_ID,
                        "goal_instance_id": INSTANCE_B,
                    },
                },
                "binding": {
                    "goal_id": GOAL_ID,
                    "goal_instance_id": INSTANCE_A,
                },
            },
        },
    )

    assert active["schema_version"] == "loopx_goal_action_catalog_v1"
    assert active["goal_binding"] == {
        "schema_version": "loopx_goal_binding_match_v1",
        "mode": "observe_only",
        "kind": "legacy_read_only",
        "binding_owner": "source_registry",
        "goal_ref": {"goal_id": GOAL_ID},
    }
    assert [action["action_id"] for action in active["actions"]] == ["goal.stop"]
    assert active["actions"][0]["target_activation_state"] == "stopped"
    assert active["actions"][0]["target_operator_state"] == "quiet"
    assert stopped["activation_state"] == "stopped"
    assert [action["action_id"] for action in stopped["actions"]] == ["goal.resume"]
    assert stopped["actions"][0]["target_activation_state"] == "active"
    assert mismatched["goal_binding"]["kind"] == "goal_instance_mismatch"
    assert mismatched["actions"] == active["actions"]
    without_binding = dict(active)
    without_binding.pop("goal_binding")
    assert without_binding == {
        "ok": True,
        "schema_version": "loopx_goal_action_catalog_v1",
        "authority_owner": "typescript_control_plane",
        "goal_id": GOAL_ID,
        "activation_state": "active",
        "state_fingerprint": "a" * 64,
        "actions": [
            {
                "schema_version": "loopx_goal_action_v1",
                "action_id": "goal.stop",
                "action_kind": "goal_lifecycle",
                "label": "Pause Goal",
                "goal_id": GOAL_ID,
                "requires_confirmation": True,
                "target_activation_state": "stopped",
                "target_operator_state": "quiet",
                "execution": {
                    "expected_state_fingerprint": "a" * 64,
                    "argv": [
                        "loopx",
                        "--registry",
                        "/tmp/registry.json",
                        "--runtime-root",
                        "/tmp/runtime",
                        "--format",
                        "json",
                        "goal-lifecycle",
                        "--goal-id",
                        GOAL_ID,
                        "--operation",
                        "stop",
                        "--actor-kind",
                        "owner",
                        "--expected-state-fingerprint",
                        "a" * 64,
                        "--execute",
                    ],
                },
            }
        ],
    }


def test_typescript_projection_rejects_invalid_state_and_fingerprint() -> None:
    with pytest.raises(EffectRuntimeRejected):
        effect_runtime_result(
            "goal.operator_actions.project",
            {
                "schema_version": "loopx_goal_action_projection_request_v3",
                "goal_id": GOAL_ID,
                "registry_locator": "/tmp/registry.json",
                "runtime_root_locator": "/tmp/runtime",
                "activation_state": "watching",
                "state_fingerprint": "not-a-digest",
                "identity_observation": {
                    "binding_owner": "source_registry",
                    "authority": {
                        "kind": "present",
                        "goal": {"goal_id": GOAL_ID},
                    },
                    "binding": {"goal_id": GOAL_ID},
                },
            },
        )


def test_catalog_contains_only_fresh_lifecycle_action(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.goals.operator_actions import build_goal_action_catalog

    _project, registry_path = _write_registry(tmp_path)

    packet = build_goal_action_catalog(
        registry_path=registry_path,
        goal_id=GOAL_ID,
    )

    assert [action["action_id"] for action in packet["actions"]] == ["goal.stop"]
    assert all(action["goal_id"] == GOAL_ID for action in packet["actions"])
    assert all(action["requires_confirmation"] is True for action in packet["actions"])
    assert packet["authority_owner"] == "typescript_control_plane"
    assert packet["goal_binding"] == {
        "schema_version": "loopx_goal_binding_match_v1",
        "mode": "observe_only",
        "kind": "legacy_read_only",
        "binding_owner": "source_registry",
        "goal_ref": {"goal_id": GOAL_ID},
    }


def test_global_projection_observes_fresh_then_recreated_source_in_one_runtime(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.goals.operator_actions import build_goal_action_catalog

    _project, source_registry = _write_registry(
        tmp_path,
        goal_instance_id=INSTANCE_A,
    )
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(tmp_path / "runtime"),
        goal_id=GOAL_ID,
        dry_run=False,
    )
    assert synced["ok"] is True
    global_registry = tmp_path / "runtime" / "registry.global.json"

    runtime_before = effect_runtime_result("runtime.ping", {})
    fresh = build_goal_action_catalog(
        registry_path=global_registry,
        goal_id=GOAL_ID,
    )
    source_payload = load_registry(source_registry)
    registry_goals(source_payload)[0]["goal_instance_id"] = INSTANCE_B
    source_registry.write_text(
        json.dumps(source_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stale = build_goal_action_catalog(
        registry_path=global_registry,
        goal_id=GOAL_ID,
    )
    runtime_after = effect_runtime_result("runtime.ping", {})

    assert runtime_after["pid"] == runtime_before["pid"]
    assert fresh["goal_binding"] == {
        "schema_version": "loopx_goal_binding_match_v1",
        "mode": "observe_only",
        "kind": "current",
        "binding_owner": "global_projection",
        "goal_ref": {
            "goal_id": GOAL_ID,
            "goal_instance_id": INSTANCE_A,
        },
    }
    assert stale["goal_binding"] == {
        "schema_version": "loopx_goal_binding_match_v1",
        "mode": "observe_only",
        "kind": "goal_instance_mismatch",
        "binding_owner": "global_projection",
        "mismatch": {
            "kind": "different_instance",
            "expected": {
                "goal_id": GOAL_ID,
                "goal_instance_id": INSTANCE_B,
            },
            "observed": {
                "goal_id": GOAL_ID,
                "goal_instance_id": INSTANCE_A,
            },
        },
    }
    assert [action["action_id"] for action in fresh["actions"]] == ["goal.stop"]
    assert [action["action_id"] for action in stale["actions"]] == ["goal.stop"]


@pytest.mark.parametrize(
    ("source_status", "expected_kind", "expected_issues"),
    [
        ("goal_missing", "goal_not_registered", None),
        (
            "registry_missing",
            "invalid",
            [{"kind": "authority_unavailable", "reason": "registry_missing"}],
        ),
        (
            "registry_unreadable",
            "invalid",
            [{"kind": "authority_unavailable", "reason": "registry_unreadable"}],
        ),
    ],
)
def test_orphaned_global_projection_preserves_authoritative_source_status(
    tmp_path: Path,
    source_status: str,
    expected_kind: str,
    expected_issues: list[dict[str, str]] | None,
) -> None:
    from loopx.control_plane.goals.operator_actions import build_goal_action_catalog

    _source, global_registry = _write_orphaned_global_registry(
        tmp_path,
        source_status=source_status,
    )

    packet = build_goal_action_catalog(
        registry_path=global_registry,
        goal_id=GOAL_ID,
    )

    assert packet["goal_binding"]["kind"] == expected_kind
    assert packet["goal_binding"]["binding_owner"] == "global_projection"
    if expected_issues is None:
        assert packet["goal_binding"]["observed"] == {
            "goal_id": GOAL_ID,
            "goal_instance_id": INSTANCE_A,
        }
    else:
        assert packet["goal_binding"]["issues"] == expected_issues


@pytest.mark.parametrize("strict_envelope", [False, True])
def test_goal_binding_observation_preserves_registry_bytes(
    tmp_path: Path,
    strict_envelope: bool,
) -> None:
    from loopx.control_plane.goals.operator_actions import build_goal_action_catalog

    _project, registry_path = _write_registry(
        tmp_path,
        goal_instance_id=INSTANCE_A,
    )
    if strict_envelope:
        payload = load_registry(registry_path)
        registry_path.write_text(
            json.dumps(_strict_envelope(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    before = registry_path.read_bytes()

    packet = build_goal_action_catalog(
        registry_path=registry_path,
        goal_id=GOAL_ID,
    )

    assert packet["goal_binding"]["kind"] == "current"
    assert registry_path.read_bytes() == before


def test_goal_binding_observation_does_not_change_markdown() -> None:
    from loopx.control_plane.goals.operator_actions import (
        render_goal_action_catalog_markdown,
    )

    payload = {
        "ok": True,
        "goal_id": GOAL_ID,
        "activation_state": "active",
        "goal_binding": {
            "schema_version": "loopx_goal_binding_match_v1",
            "mode": "observe_only",
            "kind": "legacy_read_only",
            "binding_owner": "source_registry",
            "goal_ref": {"goal_id": GOAL_ID},
        },
        "actions": [{"action_id": "goal.stop", "label": "Pause Goal"}],
    }

    assert render_goal_action_catalog_markdown(payload) == (
        "# Goal Actions\n"
        "\n"
        "- ok: `true`\n"
        f"- goal: `{GOAL_ID}`\n"
        "- activation_state: `active`\n"
        "\n"
        "## Available actions\n"
        "\n"
        "- `goal.stop` — Pause Goal\n"
    )


def test_goal_actions_cli_projects_exact_fresh_execution_identity(
    tmp_path: Path,
) -> None:
    project, registry_path = _write_registry(tmp_path)

    result = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)

    assert result.returncode == 0, result.stderr
    packet = json.loads(result.stdout)
    assert packet["ok"] is True
    assert packet["goal_id"] == GOAL_ID
    assert len(packet["state_fingerprint"]) == 64
    action = next(
        item for item in packet["actions"] if item["action_id"] == "goal.stop"
    )
    assert action["action_id"] == "goal.stop"
    assert action["execution"]["expected_state_fingerprint"] == packet["state_fingerprint"]
    assert action["execution"]["argv"] == [
        "loopx",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(tmp_path / "runtime"),
        "--format",
        "json",
        "goal-lifecycle",
        "--goal-id",
        GOAL_ID,
        "--operation",
        "stop",
        "--actor-kind",
        "owner",
        "--expected-state-fingerprint",
        packet["state_fingerprint"],
        "--execute",
    ]
    assert project.exists()


def test_projected_action_runs_verbatim_against_non_default_registry(
    tmp_path: Path,
) -> None:
    _project, registry_path = _write_registry(tmp_path)
    projected = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    action = json.loads(projected.stdout)["actions"][0]

    executed = _run_projected_argv(action["execution"]["argv"])

    assert executed.returncode == 0, executed.stderr
    payload = json.loads(executed.stdout)
    assert payload["ok"] is True
    assert payload["written"] is True
    assert payload["readback"]["verified"] is True


def test_projected_lifecycle_action_rejects_stale_registry_without_writing(
    tmp_path: Path,
) -> None:
    _project, registry_path = _write_registry(tmp_path)
    projected = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    assert projected.returncode == 0, projected.stderr
    action = next(
        item
        for item in json.loads(projected.stdout)["actions"]
        if item["action_id"] == "goal.stop"
    )
    before = load_registry(registry_path)
    registry_goals(before)[0]["display_name"] = "Changed after projection"
    registry_path.write_text(
        json.dumps(before, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    changed_bytes = registry_path.read_bytes()

    executed = _run_projected_argv(action["execution"]["argv"])

    assert executed.returncode == 1
    payload = json.loads(executed.stdout)
    assert payload["ok"] is False
    assert payload["error_kind"] == "goal_action_stale"
    assert payload["written"] is False
    assert registry_path.read_bytes() == changed_bytes


def test_fresh_projected_lifecycle_action_applies_and_projects_resume(
    tmp_path: Path,
) -> None:
    _project, registry_path = _write_registry(tmp_path)
    projected = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    stop = next(
        item
        for item in json.loads(projected.stdout)["actions"]
        if item["action_id"] == "goal.stop"
    )

    executed = _run_projected_argv(stop["execution"]["argv"])

    assert executed.returncode == 0, executed.stderr
    applied = json.loads(executed.stdout)
    assert applied["readback"]["verified"] is True
    follow_up = _run_cli(registry_path, "goal-actions", "--goal-id", GOAL_ID)
    assert follow_up.returncode == 0, follow_up.stderr
    resume = next(
        item
        for item in json.loads(follow_up.stdout)["actions"]
        if item["action_id"] == "goal.resume"
    )
    assert resume["action_id"] == "goal.resume"
    assert resume["execution"]["argv"][-1] == "--execute"
