#!/usr/bin/env python3
"""Shared fixtures for quota plan smoke tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.quota import (  # noqa: E402
    build_quota_slot_void_event,
    build_quota_slot_void_preview,
    goal_quota_with_spend_ledger,
    render_quota_slot_preview_markdown,
    void_quota_slot,
)


SCOPED_AGENT_ID = "codex-side-bypass"


def _nested_value(payload: dict, path: str):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def scheduler_reset_profile_snapshot(scheduler: dict) -> dict:
    codex_app = scheduler["codex_app"]
    unchanged_poll = scheduler["unchanged_poll"]
    limits = unchanged_poll["limits"]
    return {
        "cadence_class": scheduler["cadence_class"],
        "codex_app_initial_interval_minutes": codex_app["recommended_interval_minutes"],
        "codex_app_initial_rrule": codex_app["recommended_rrule"],
        "codex_app_max_interval_minutes": codex_app["max_interval_minutes"],
        "codex_app_progression_minutes": codex_app["example_progression_minutes"],
        "unchanged_poll_backoff_multiplier": codex_app["unchanged_poll_backoff_multiplier"],
        "local_scheduler_unchanged_poll_limit": limits["local_scheduler"],
        "claude_code_loop_unchanged_poll_limit": limits["claude_code_loop"],
    }


def _short_hash(value: dict, length: int) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:length]


def expected_scheduler_reset_token(scheduler: dict, payload: dict) -> str:
    identity_snapshot = {
        key: _nested_value(payload, key)
        for key in scheduler["unchanged_identity_keys"]
    }
    profile_snapshot = scheduler_reset_profile_snapshot(scheduler)
    token_payload = {
        "action": scheduler["action"],
        "identity_snapshot": identity_snapshot,
        "profile_snapshot": profile_snapshot,
    }
    return _short_hash(token_payload, 16)


def goal(
    goal_id: str,
    *,
    compute: float,
    spent_slots: int = 0,
    allowed_slots: int | None = None,
) -> dict:
    allowed_slots = round(24 * 60 * compute) if allowed_slots is None else allowed_slots
    return {
        "id": goal_id,
        "status": "active",
        "registry_member": True,
        "lifecycle_phase": "refreshed",
        "adapter_kind": "read_only_project_map_v0",
        "adapter_status": "connected-read-only",
        "quota": {
            "compute": compute,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": allowed_slots,
            "spent_slots": spent_slots,
        },
        "latest_runs": [
            {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "classification": "state_refreshed",
                "recommended_action": f"continue {goal_id}",
            }
        ],
    }


def attention(
    goal_id: str,
    *,
    compute: float,
    state: str = "eligible",
    waiting_on: str = "codex",
    spent_slots: int = 0,
    allowed_slots: int | None = None,
) -> dict:
    allowed_slots = round(24 * 60 * compute) if allowed_slots is None else allowed_slots
    if state == "operator_gate":
        reason = "operator gate blocks gated delivery; safe non-gated steering may continue"
    elif state == "throttled":
        reason = f"{compute:g} compute quota spent {spent_slots}/{allowed_slots} slots in this window"
    elif state == "focus_wait":
        reason = (
            "focus wait: delivery lane has a continuation boundary or missing novelty; "
            "wait for new evidence, owner input, external eval, or a clean baseline before "
            "spending delivery compute"
        )
    else:
        reason = f"{compute:g} compute quota; eligible for the next automatic agent turn"
    return {
        "goal_id": goal_id,
        "status": "state_refreshed" if waiting_on == "codex" else "operator_gate_deferred",
        "waiting_on": waiting_on,
        "severity": "action",
        "recommended_action": f"continue {goal_id}",
        "source": "fixture",
        **(
            {
                "operator_question": f"是否同意 {goal_id} 继续 gated delivery？",
                "next_handoff_condition": "operator decision recorded",
                "user_todos": {
                    "source_section": "User Todo",
                    "total_count": 1,
                    "open_count": 1,
                    "done_count": 0,
                    "items": [
                        {
                            "index": 1,
                            "done": False,
                            "text": "Confirm the operator gate.",
                            "task_class": "user_gate",
                            "global_gate": True,
                        }
                    ],
                },
                "agent_todos": {
                    "source_section": "Agent Todo",
                    "total_count": 1,
                    "open_count": 1,
                    "done_count": 0,
                    "items": [
                        {
                            "index": 1,
                            "done": False,
                            "text": "Run the safe follow-up after gate approval.",
                        }
                    ],
                },
            }
            if state == "operator_gate"
            else {}
        ),
        "quota": {
            "compute": compute,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": allowed_slots,
            "spent_slots": spent_slots,
            "state": state,
            "reason": reason,
            **(
                {
                    "blocked_action_scope": "gated_delivery",
                    "safe_bypass_allowed": True,
                    "safe_bypass_policy": "Do not execute agent_command; safe steering only.",
                }
                if state == "operator_gate"
                else {}
            ),
            **(
                {
                    "blocked_action_scope": "delivery_focus",
                    "focus_wait": True,
                }
                if state == "focus_wait"
                else {}
            ),
        },
    }


def build_status_fixture() -> dict:
    goals = [
        goal("half-speed", compute=0.5),
        goal("near-limit-half", compute=0.5, spent_slots=11, allowed_slots=12),
        goal("full-speed", compute=1.0),
        goal("low-speed", compute=0.3),
        goal("throttled-half", compute=0.5, spent_slots=12, allowed_slots=12),
        goal("needs-operator", compute=1.0),
    ]
    queue_items = [
        attention("half-speed", compute=0.5),
        attention("near-limit-half", compute=0.5, spent_slots=11, allowed_slots=12),
        attention("full-speed", compute=1.0),
        attention("low-speed", compute=0.3),
        attention(
            "throttled-half",
            compute=0.5,
            state="throttled",
            spent_slots=12,
            allowed_slots=12,
        ),
        attention(
            "needs-operator",
            compute=1.0,
            state="operator_gate",
            waiting_on="user_or_controller",
        ),
    ]
    return {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": len(goals),
        "run_count": len(goals),
        "attention_queue": {"items": queue_items},
        "run_history": {"goals": goals},
    }


def append_quota_slot_spend_fixture(
    run_dir: Path,
    *,
    goal_id: str,
    compute: float,
    slot_index: int,
    generated_at: str,
    allowed_slots: int,
) -> None:
    before_spent = slot_index
    after_spent = slot_index + 1
    after_state = "throttled" if after_spent >= allowed_slots else "eligible"
    after_should_run = after_state == "eligible"
    stem = generated_at.replace("-", "").replace(":", "").replace("+", "")
    json_path = run_dir / f"{stem}-quota-slot-{slot_index + 1}.json"
    markdown_path = run_dir / f"{stem}-quota-slot-{slot_index + 1}.md"
    record = {
        "generated_at": generated_at,
        "goal_id": goal_id,
        "classification": "quota_slot_spent",
        "recommended_action": f"continue {goal_id}",
        "health_check": "fixture quota slot spend event",
        "quota_event": {
            "event_type": "quota_slot_spent",
            "source": "heartbeat",
            "slots": 1,
            "reason_summary": "fixture automatic agent slot completed under an eligible quota guard",
            "before": {
                "should_run": True,
                "state": "eligible",
                "compute": compute,
                "window_hours": 24,
                "slot_minutes": 1,
                "spent_slots": before_spent,
                "allowed_slots": allowed_slots,
            },
            "after": {
                "should_run": after_should_run,
                "state": after_state,
                "compute": compute,
                "window_hours": 24,
                "slot_minutes": 1,
                "spent_slots": after_spent,
                "allowed_slots": allowed_slots,
            },
        },
    }
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(f"# {goal_id} quota slot fixture\n", encoding="utf-8")
    with (run_dir / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "generated_at": generated_at,
                    "goal_id": goal_id,
                    "classification": "quota_slot_spent",
                    "recommended_action": f"continue {goal_id}",
                    "health_check": "fixture quota slot spend event",
                    "json_path": str(json_path),
                    "markdown_path": str(markdown_path),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def write_codex_heartbeat_fixture(root: Path, *, goal_id: str, agent_id: str, rrule: str) -> None:
    """Synthetic manifest for CLI readback tests; never a real App update proof."""
    path = root / "automations" / "scheduler-fixture" / "automation.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "id": "scheduler-fixture", "kind": "heartbeat", "status": "ACTIVE",
        "prompt": f"Advance `{goal_id}` from active state. Agent: `{agent_id}`.",
        "rrule": rrule, "target_thread_id": "scheduler-fixture",
    }
    path.write_text("\n".join(f"{key} = {json.dumps(value)}" for key, value in fields.items()) + "\n", encoding="utf-8")


def write_cli_fixture(root: Path, *, scoped_agents: bool = False) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    base_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    goal_specs = [
        ("half-speed", 0.5, "state_refreshed", 0, None),
        ("near-limit-half", 0.5, "state_refreshed", 11, 12),
        ("full-speed", 1.0, "state_refreshed", 0, None),
        ("low-speed", 0.3, "state_refreshed", 0, None),
        ("throttled-half", 0.5, "state_refreshed", 12, 12),
        ("needs-operator", 1.0, "operator_gate_deferred", 0, None),
    ]
    registry_goals = []
    for goal_id, compute, classification, spent_slots, allowed_slots in goal_specs:
        state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
        state_path = project / state_file
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            "---\n"
            "status: active\n"
            "updated_at: 2026-01-01T00:00:00+00:00\n"
            "---\n\n"
            f"# {goal_id}\n\n"
            "## Next Action\n\n"
            f"- continue {goal_id}\n\n"
            "## Agent Todo\n",
            encoding="utf-8",
        )
        goal_record = {
            "id": goal_id,
            "domain": "quota-fixture",
            "status": "active",
            "repo": str(project),
            "state_file": state_file,
            "adapter": {
                "kind": "read_only_project_map_v0",
                "status": "connected-read-only",
            },
            "authority_sources": [],
            "quota": {
                key: value
                for key, value in {
                    "compute": compute,
                    "window_hours": 24,
                    "allowed_slots": allowed_slots,
                }.items()
                if value is not None
            },
        }
        if scoped_agents:
            goal_record["coordination"] = {
                "registered_agents": ["codex-main-control", SCOPED_AGENT_ID],
                "agent_model": "peer_v1",
            }
        registry_goals.append(goal_record)
        runs_dir = runtime / "goals" / goal_id / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        json_path = runs_dir / "20260101T000000-run.json"
        markdown_path = runs_dir / "20260101T000000-run.md"
        generated_at = (base_time - timedelta(seconds=1)).isoformat()
        record = {
            "generated_at": generated_at,
            "goal_id": goal_id,
            "classification": classification,
            "recommended_action": f"continue {goal_id}",
            "health_check": "fixture 1/1",
        }
        if classification == "operator_gate_deferred":
            record["operator_gate"] = {
                "recorded_at": generated_at,
                "gate": "read_only_map_opt_in",
                "decision": "defer",
                "operator_question": f"是否同意 `{goal_id}` 先执行 read-only map opt-in？",
                "reason_summary": "need more fixture evidence",
            }
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown_path.write_text(f"# {goal_id} fixture run\n", encoding="utf-8")
        with (runs_dir / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        **record,
                        "json_path": str(json_path),
                        "markdown_path": str(markdown_path),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        effective_allowed_slots = round(24 * 60 * compute) if allowed_slots is None else allowed_slots
        for slot_index in range(spent_slots):
            append_quota_slot_spend_fixture(
                runs_dir,
                goal_id=goal_id,
                compute=compute,
                slot_index=slot_index,
                generated_at=(base_time + timedelta(seconds=slot_index)).isoformat(),
                allowed_slots=effective_allowed_slots,
            )

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": registry_goals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    # Production reads require canonical authority; the run-ledger operator
    # gate remains independent of a manually authored user Todo.
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    for record in registry_goals:
        initialize_canonical_authority(runtime, record["id"],
            build_todo_runtime_shadow_projection(goal_id=record["id"], todos=[], handoff_mode="soft_claim"),
            state_path=project / record["state_file"], provider="sqlite")
    return registry_path, runtime, project


def run_cli_quota_plan(root: Path) -> tuple[dict, str]:
    registry_path, runtime, project = write_cli_fixture(root)
    base_args = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
    ]
    json_result = subprocess.run(
        [*base_args, "--format", "json", "quota", "plan"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    markdown_result = subprocess.run(
        [*base_args, "quota", "plan"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(json_result.stdout), markdown_result.stdout


def run_cli_throttled_should_run(root: Path) -> dict:
    registry_path, runtime, project = write_cli_fixture(root)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "quota",
            "should-run",
            "--goal-id",
            "throttled-half",
            "--runtime-profile",
            "outer_controller",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def run_cli_slot_preview(root: Path) -> tuple[dict, dict]:
    registry_path, runtime, project = write_cli_fixture(root)
    base_args = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "quota",
    ]
    preview_result = subprocess.run(
        [
            *base_args,
            "spend-slot",
            "--goal-id",
            "near-limit-half",
            "--slots",
            "1",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    should_run_result = subprocess.run(
        [
            *base_args,
            "should-run",
            "--goal-id",
            "near-limit-half",
            "--runtime-profile",
            "outer_controller",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(preview_result.stdout), json.loads(should_run_result.stdout)


def run_cli_slot_spend_execute(root: Path) -> tuple[dict, dict, str, str]:
    registry_path, runtime, project = write_cli_fixture(root, scoped_agents=True)
    registry_before = registry_path.read_text(encoding="utf-8")
    base_args = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "quota",
    ]
    result = subprocess.run(
        [
            *base_args,
            "spend-slot",
            "--goal-id",
            "near-limit-half",
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
            "--agent-id",
            SCOPED_AGENT_ID,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    should_run_result = subprocess.run(
        [
            *base_args,
            "should-run",
            "--goal-id",
            "near-limit-half",
            "--runtime-profile",
            "outer_controller",
            "--agent-id",
            SCOPED_AGENT_ID,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return (
        json.loads(result.stdout),
        json.loads(should_run_result.stdout),
        registry_before,
        registry_path.read_text(encoding="utf-8"),
    )


def run_cli_slot_void_execute(root: Path) -> tuple[dict, dict, dict, str, str]:
    registry_path, runtime, project = write_cli_fixture(root, scoped_agents=True)
    registry_before = registry_path.read_text(encoding="utf-8")
    base_args = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "quota",
    ]
    spend_result = subprocess.run(
        [
            *base_args,
            "spend-slot",
            "--goal-id",
            "near-limit-half",
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
            "--agent-id",
            SCOPED_AGENT_ID,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    spend_payload = json.loads(spend_result.stdout)
    void_result = subprocess.run(
        [
            *base_args,
            "void-slot",
            "--goal-id",
            "near-limit-half",
            "--void-generated-at",
            str(spend_payload["generated_at"]),
            "--source",
            "heartbeat",
            "--reason-summary",
            "duplicate heartbeat spend",
            "--execute",
            "--agent-id",
            SCOPED_AGENT_ID,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    should_run_result = subprocess.run(
        [
            *base_args,
            "should-run",
            "--goal-id",
            "near-limit-half",
            "--runtime-profile",
            "outer_controller",
            "--agent-id",
            SCOPED_AGENT_ID,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return (
        spend_payload,
        json.loads(void_result.stdout),
        json.loads(should_run_result.stdout),
        registry_before,
        registry_path.read_text(encoding="utf-8"),
    )


def assert_outer_controller_scheduler_context(payload: dict) -> None:
    scheduler_context = payload["scheduler_hint"]["execution_context"]
    assert scheduler_context["valid"] is True, payload
    assert scheduler_context["host_surface"] == "generic_cli", payload
    assert scheduler_context["scheduler_owner"] == "outer_controller", payload
    assert scheduler_context["execution_mode"] == "isolated_headless", payload


def assert_throttled_cli_should_run(payload: dict) -> None:
    quota = payload["quota"]

    assert payload["ok"] is True, payload
    assert payload["goal_id"] == "throttled-half", payload
    assert payload["decision"] == "skip", payload
    assert payload["should_run"] is False, payload
    assert payload["state"] == "throttled", payload
    assert payload["waiting_on"] == "codex", payload
    assert payload["plan_summary"]["registered_goals"] == 1, payload
    assert payload["plan_summary"]["next_automatic_turn"] is None, payload
    assert_outer_controller_scheduler_context(payload)
    assert quota["compute"] == 0.5, payload
    assert quota["spent_slots"] == 12, payload
    assert quota["allowed_slots"] == 12, payload
    assert "spent 12/12" in payload["reason"], payload
    assert "agent_command" not in payload, payload


def assert_slot_preview(payload: dict, *, goal_scoped: bool = False) -> None:
    before = payload["before"]
    after = payload["after"]
    markdown = render_quota_slot_preview_markdown(payload)

    assert payload["ok"] is True, payload
    assert payload["dry_run"] is True, payload
    assert payload["appended"] is False, payload
    assert payload["registry_mutated"] is False, payload
    assert payload["goal_id"] == "near-limit-half", payload
    assert payload["slots"] == 1, payload
    assert payload["would_throttle"] is True, payload
    assert before["state"] == "eligible", payload
    assert before["should_run"] is True, payload
    assert before["quota"]["spent_slots"] == 11, payload
    assert before["quota"]["allowed_slots"] == 12, payload
    assert after["state"] == "throttled", payload
    assert after["should_run"] is False, payload
    assert after["decision"] == "skip", payload
    assert after["quota"]["spent_slots"] == 12, payload
    assert after["quota"]["allowed_slots"] == 12, payload
    if goal_scoped:
        assert after["plan_summary"]["registered_goals"] == 1, payload
        assert after["plan_summary"]["next_automatic_turn"] is None, payload
    else:
        assert after["plan_summary"]["next_automatic_turn"] == "full-speed", payload
    assert "rolling_window_note" in payload, payload
    assert "same-status-payload projection" in payload["rolling_window_note"], payload
    assert "rolling_window_note" in markdown, markdown


def assert_dry_run_left_cli_fixture_unchanged(payload: dict) -> None:
    assert payload["goal_id"] == "near-limit-half", payload
    assert payload["state"] == "eligible", payload
    assert payload["should_run"] is True, payload
    assert payload["quota"]["spent_slots"] == 11, payload
    assert payload["quota"]["allowed_slots"] == 12, payload
    assert_outer_controller_scheduler_context(payload)


def assert_slot_spend_execute(payload: dict, next_should_run: dict, registry_before: str, registry_after: str) -> None:
    quota_event = payload["quota_event"]
    before = quota_event["before"]
    after = quota_event["after"]
    json_path = Path(payload["json_path"])
    index_path = Path(payload["index_path"])

    assert payload["ok"] is True, payload
    assert payload["dry_run"] is False, payload
    assert payload["appended"] is True, payload
    assert payload["registry_mutated"] is False, payload
    assert payload["classification"] == "quota_slot_spent", payload
    assert payload["agent_id"] == SCOPED_AGENT_ID, payload
    assert payload["source"] == "heartbeat", payload
    assert registry_after == registry_before
    assert '"spent_slots"' not in registry_after
    assert json_path.exists(), payload
    assert index_path.exists(), payload
    assert quota_event["event_type"] == "quota_slot_spent", payload
    assert quota_event["agent_id"] == SCOPED_AGENT_ID, payload
    assert quota_event["slots"] == 1, payload
    assert before["should_run"] is True, payload
    assert before["state"] == "eligible", payload
    assert before["spent_slots"] == 11, payload
    assert after["spent_slots"] == 12, payload
    assert after["allowed_slots"] == 12, payload
    assert after["state"] == "throttled", payload
    assert after["should_run"] is False, payload

    record = json.loads(json_path.read_text(encoding="utf-8"))
    assert record["classification"] == "quota_slot_spent", record
    assert record["agent_id"] == SCOPED_AGENT_ID, record
    assert record["quota_event"] == quota_event, record
    forbidden = {"human_reward", "operator_gate", "write_control", "private_evidence", "agent_command"}
    assert forbidden.isdisjoint(record), record
    assert forbidden.isdisjoint(record["quota_event"]), record
    index_records = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    quota_records = [
        item
        for item in index_records
        if item.get("classification") == "quota_slot_spent"
    ]
    assert quota_records, index_records
    assert any(
        item.get("agent_id") == SCOPED_AGENT_ID for item in quota_records
    ), index_records

    assert next_should_run["goal_id"] == "near-limit-half", next_should_run
    assert next_should_run["should_run"] is False, next_should_run
    assert next_should_run["state"] == "throttled", next_should_run
    assert next_should_run["quota"]["spent_slots"] == 12, next_should_run
    assert next_should_run["quota"]["allowed_slots"] == 12, next_should_run
    assert_outer_controller_scheduler_context(next_should_run)


def assert_slot_void_execute(
    spend_payload: dict,
    void_payload: dict,
    next_should_run: dict,
    registry_before: str,
    registry_after: str,
) -> None:
    quota_event = void_payload["quota_event"]
    json_path = Path(void_payload["json_path"])
    index_path = Path(void_payload["index_path"])
    markdown = render_quota_slot_preview_markdown(void_payload)

    assert spend_payload["classification"] == "quota_slot_spent", spend_payload
    assert void_payload["ok"] is True, void_payload
    assert void_payload["dry_run"] is False, void_payload
    assert void_payload["appended"] is True, void_payload
    assert void_payload["registry_mutated"] is False, void_payload
    assert void_payload["classification"] == "quota_slot_voided", void_payload
    assert void_payload["agent_id"] == SCOPED_AGENT_ID, void_payload
    assert void_payload["source"] == "heartbeat", void_payload
    assert registry_after == registry_before
    assert json_path.exists(), void_payload
    assert index_path.exists(), void_payload
    assert quota_event["event_type"] == "quota_slot_voided", void_payload
    assert quota_event["agent_id"] == SCOPED_AGENT_ID, void_payload
    assert quota_event["slots"] == 1, void_payload
    assert quota_event["voided_run_generated_at"] == spend_payload["generated_at"], void_payload
    assert "duplicate heartbeat spend" in quota_event["reason_summary"], void_payload
    assert "quota_slot_voided" in markdown, markdown

    record = json.loads(json_path.read_text(encoding="utf-8"))
    assert record["classification"] == "quota_slot_voided", record
    assert record["agent_id"] == SCOPED_AGENT_ID, record
    assert record["quota_event"] == quota_event, record
    forbidden = {"human_reward", "operator_gate", "write_control", "private_evidence", "agent_command"}
    assert forbidden.isdisjoint(record), record
    assert forbidden.isdisjoint(record["quota_event"]), record
    index_lines = index_path.read_text(encoding="utf-8").splitlines()
    index_records = [json.loads(line) for line in index_lines]
    assert any(
        item.get("classification") == "quota_slot_spent"
        for item in index_records
    ), index_records
    assert any(
        item.get("classification") == "quota_slot_voided"
        for item in index_records
    ), index_records
    assert any(
        item.get("agent_id") == SCOPED_AGENT_ID
        for item in index_records
    ), index_records

    assert next_should_run["goal_id"] == "near-limit-half", next_should_run
    assert next_should_run["should_run"] is True, next_should_run
    assert next_should_run["state"] == "eligible", next_should_run
    assert next_should_run["quota"]["spent_slots"] == 11, next_should_run
    assert next_should_run["quota"]["allowed_slots"] == 12, next_should_run
    assert_outer_controller_scheduler_context(next_should_run)


def assert_quota_void_event_net_ledger() -> None:
    goal_id = "void-ledger-goal"
    with tempfile.TemporaryDirectory(prefix="loopx-quota-void-ledger-") as tmp:
        runtime = Path(tmp) / "runtime"
        run_dir = runtime / "goals" / goal_id / "runs"
        run_dir.mkdir(parents=True)
        spend_at = (datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)).isoformat()
        append_quota_slot_spend_fixture(
            run_dir,
            goal_id=goal_id,
            compute=1.0,
            slot_index=0,
            generated_at=spend_at,
            allowed_slots=1440,
        )
        records = [json.loads(line) for line in (run_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        goal_record = goal(goal_id, compute=1.0)
        before = goal_quota_with_spend_ledger(goal_record, records)
        assert before["spent_slots"] == 1, before

        status_payload = {
            "ok": True,
            "registry": "./fixtures/registry.json",
            "runtime_root": str(runtime),
            "goal_count": 1,
            "run_count": 1,
            "attention_queue": {"items": [attention(goal_id, compute=1.0)]},
            "run_history": {"goals": [goal_record]},
        }
        preview = build_quota_slot_void_preview(status_payload, goal_id=goal_id, voided_run_generated_at=spend_at)
        event = build_quota_slot_void_event(preview, source="heartbeat", reason_summary="duplicate fixture spend")
        assert preview["classification"] == "quota_slot_voided", preview
        assert event["quota_event"]["event_type"] == "quota_slot_voided", event
        void_payload = void_quota_slot(
            status_payload,
            goal_id=goal_id,
            voided_run_generated_at=spend_at,
            execute=True,
            source="heartbeat",
            reason_summary="duplicate fixture spend",
        )
        assert void_payload["appended"] is True, void_payload

        records = [json.loads(line) for line in (run_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        after = goal_quota_with_spend_ledger(goal_record, records)
        assert after["spent_slots"] == 0, after
        assert after["spend_event_count"] == 1, after
        assert after["void_event_count"] == 1, after
