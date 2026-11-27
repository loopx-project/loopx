"""Contract, zero-Any, MappingProxyType, and parity tests for loopx.diagnose."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import MappingProxyType
import pytest

from loopx import diagnose
from loopx.cli import main
from loopx.control_plane.status import contract_projection, diagnose_projection
from loopx.control_plane.testing.canary_harness import import_fixture_authority
from loopx.presentation.renderers import diagnose_markdown


def test_diagnose_zero_any_budgets_and_identity() -> None:
    for rel, max_loc in (
        ("loopx/diagnose.py", 550),
        ("loopx/control_plane/status/diagnose_projection.py", 750),
        ("loopx/presentation/renderers/diagnose_markdown.py", 750),
        ("tests/control_plane/test_diagnose_contract.py", 400),
    ):
        src = (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")
        assert len(src.splitlines()) <= max_loc, rel
        tree = ast.parse(src)
        anys = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "Any"]
        assert anys == [], rel
        assert ("type: " + "ignore") not in src, rel
        assert ("no" + "qa") not in src, rel

    assert diagnose.STATUS_CONTRACT_SIGNAL_LIMIT is contract_projection.STATUS_CONTRACT_SIGNAL_LIMIT
    assert diagnose.USER_DIAGNOSE_PROMPT is diagnose_markdown.USER_DIAGNOSE_PROMPT
    assert diagnose.render_diagnosis_markdown is diagnose_markdown.render_diagnosis_markdown
    assert diagnose._scheduler_hint_line is diagnose_markdown._scheduler_hint_line
    assert diagnose._goal_frontier_projection_line is diagnose_markdown._goal_frontier_projection_line
    assert diagnose._compact_scheduler_hint is diagnose_projection._compact_scheduler_hint
    assert diagnose._first_agent_todo_text is diagnose_projection._first_agent_todo_text


def test_mapping_proxy_projection_and_non_mutating_compaction() -> None:
    assert diagnose._open_count(MappingProxyType({"open_count": True, "items": [{"text": "a"}]})) == 1
    assert diagnose._open_count(MappingProxyType({"open": 4})) == 4

    source_agent = {"required_reads": ["GOAL.md"], "action_portfolio_ref": "drop", "suggested_action_count": 2}
    source_cli = {"command": "loopx status", "selection_policy_ref": "drop", "selection_command": "drop"}
    contract = MappingProxyType({"agent_channel": source_agent, "cli_channel": source_cli})
    compacted = diagnose._compact_interaction_contract(contract)
    assert "action_portfolio_ref" in source_agent
    assert "selection_policy_ref" in source_cli
    assert compacted["agent_channel"] == {"required_reads": ["GOAL.md"]}
    assert compacted["cli_channel"] == {"command": "loopx status"}

    packet: dict[str, object] = {"goal_id": "g1", "interaction_contract": compacted}
    overview = diagnose._goal_list_view(packet, selected=True)
    orig_ic = packet["interaction_contract"]
    ov_ic = overview["interaction_contract"]
    assert isinstance(orig_ic, dict) and orig_ic["agent_channel"] == {"required_reads": ["GOAL.md"]}
    assert isinstance(ov_ic, dict) and ov_ic == {
        "agent_channel": {},
        "cli_channel": {"command": "loopx status"},
    }

    hint = MappingProxyType(
        {
            "action": "hold",
            "cadence_class": "idle",
            "reason": "no new work",
            "app_automation": {
                "apply": "none",
                "host_action": "keep",
                "recommended_rrule": "FREQ=HOURLY",
                "stateful_backoff": {"current_rrule": "FREQ=HOURLY", "apply_needed": False},
                "no_spend_for_cadence_change": True,
            },
            "unchanged_poll": {"final_quota_replan_check_enabled": True, "final_quota_replan_check_action": "none"},
            "reset_policy": {"reset_token": "tok-1", "app_automation_initial_rrule": "FREQ=MINUTELY"},
        }
    )
    compact_hint = diagnose._compact_scheduler_hint(hint)
    assert "app_automation" in compact_hint and "codex_app" not in compact_hint
    line = diagnose._scheduler_hint_line(compact_hint)
    assert line is not None and "app_automation_apply=none" in line

    md = diagnose.render_diagnosis_markdown(
        MappingProxyType(
            {
                "ok": False,
                "packet_kind": diagnose.PACKET_KIND,
                "agent_must_reason": True,
                "registry": "registry.json",
                "runtime_root": "rt",
                "selected_goal_id": "g1",
                "goal_count": 2,
                "goal_packet_count": 1,
                "run_count": 5,
                "selected": {
                    "machine_signal": "user_or_controller_attention",
                    "status": "blocked",
                    "waiting_on": "operator",
                    "severity": "high",
                    "recommended_action": "Ask | operator",
                    "user_question": "Confirm?",
                    "todo_evidence": {"user_open_count": 1, "agent_open_count": 0, "first_user_todo": "Approve"},
                    "quota_signals": {
                        "state": "hold",
                        "should_run": False,
                        "action_required": True,
                        "open_count": 1,
                        "goal_frontier_projection": {
                            "replan_required": False,
                            "normalized_progress": {"user_open_count": 1, "agent_open_count": 0},
                            "acceptance_gaps": ("gap-1",),
                            "autonomy_blockers": (),
                        },
                        "scheduler_hint": compact_hint,
                    },
                    "orphaned_goal_state": {
                        "reason": "orphan",
                        "state_file_routes": ("ACTIVE_GOAL_STATE.md",),
                        "forbidden_until_resolved": ("bootstrap",),
                    },
                    "agent_reasoning_checklist": ("step 1",),
                    "agent_commands": ("loopx status",),
                },
                "status_summary": {
                    "contract_errors": ("err-1",),
                    "contract_errors_truncated": True,
                    "contract_errors_total_count": 4,
                    "contract_warnings": ("warn-1",),
                    "contract_warnings_truncated": True,
                    "contract_warnings_total_count": 5,
                },
                "goals": ({"goal_id": "g1", "machine_signal": "user_or_controller_attention", "todo_evidence": {"user_open_count": 1, "agent_open_count": 0}},),
            }
        )
    )
    assert "goal_frontier_projection: replan_required=False" in md
    assert "Ask \\| operator" in md
    assert "contract_errors_truncated: total=4" in md
    assert "## Orphaned Goal State" in md


def test_collect_diagnosis_isolates_quota_errors_and_handles_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text('{"goals": []}\n', encoding="utf-8")

    monkeypatch.setattr(
        diagnose,
        "collect_status",
        lambda **_kw: {
            "ok": True,
            "runtime_root": "rt",
            "goal_count": 2,
            "run_count": 1,
            "attention_queue": {
                "item_count": 2,
                "items": [
                    {"goal_id": "g-good", "status": "ready", "waiting_on": "none", "severity": "info"},
                    {"goal_id": "g-orphan", "status": "blocked", "waiting_on": "operator", "severity": "high"},
                ],
            },
            "run_history": {"goals": [{"id": "g-orphan", "registry_member": False}]},
            "contract": {"summary": "ok", "errors": ["e1", "e2", "e3", "e4"], "warnings": []},
            "global_registry": {"summary": {"total": 1}},
        },
    )

    def fake_quota(
        _status: dict[str, object],
        *,
        goal_id: str,
        **_kw: object,
    ) -> dict[str, object]:
        if goal_id == "g-good":
            return {
                "ok": True,
                "goal_id": goal_id,
                "should_run": True,
                "interaction_contract": {
                    "agent_channel": {"required_reads": ["GOAL.md"], "action_portfolio_ref": "x"},
                    "cli_channel": {"selection_policy_ref": "y"},
                },
            }
        raise RuntimeError("boom quota")

    monkeypatch.setattr(diagnose, "build_quota_should_run", fake_quota)
    monkeypatch.setattr(
        diagnose,
        "orphaned_goal_state_projection",
        lambda _root, gid: {"reason": "orphan", "goal_id": gid} if gid == "g-orphan" else None,
    )
    monkeypatch.setattr(
        diagnose,
        "orphaned_goal_state_gate",
        lambda proj, **_kw: {
            "reason": proj["reason"],
            "state_file_routes": ["ACTIVE.md"],
            "forbidden_until_resolved": ["bootstrap"],
            "resolution_routes": [{"command": "loopx doctor"}],
        },
    )

    payload = diagnose.collect_diagnosis(
        registry_path=registry_path,
        runtime_root_override=None,
        limit=5,
        available_capabilities="web_search, web_search",
    )
    assert payload["ok"] is False
    assert payload["diagnosis_blocked_by"] == diagnose.ORPHANED_GOAL_STATE_CONNECTION
    assert payload["available_capabilities"] == ["web_search"]
    selected = payload["selected"]
    assert isinstance(selected, dict)
    sel_ic = selected["interaction_contract"]
    assert isinstance(sel_ic, dict) and isinstance(sel_ic["agent_channel"], dict)
    assert sel_ic["agent_channel"]["required_reads"] == ["GOAL.md"]
    goals = payload["goals"]
    assert isinstance(goals, list) and len(goals) == 2
    g0_ic = goals[0]["interaction_contract"]
    assert isinstance(g0_ic, dict) and isinstance(g0_ic["agent_channel"], dict)
    assert "required_reads" not in g0_ic["agent_channel"]
    assert goals[1]["machine_signal"] == diagnose.ORPHANED_GOAL_STATE_CONNECTION
    summary = payload["status_summary"]
    assert isinstance(summary, dict)
    assert summary["contract_errors_truncated"] is True

    # None projection on unregistered goal must fail safe without blocking normal packet.
    orphan_calls: list[str] = []

    def _none_projection(_root: Path, gid: str) -> dict[str, object] | None:
        orphan_calls.append(gid)
        return None

    monkeypatch.setattr(diagnose, "orphaned_goal_state_projection", _none_projection)
    non_blocking = diagnose.collect_diagnosis(
        registry_path=registry_path,
        runtime_root_override=None,
        limit=5,
    )
    assert orphan_calls == ["g-orphan"]
    assert non_blocking["ok"] is True
    assert "diagnosis_blocked_by" not in non_blocking


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_diagnose_cli_keeps_active_goal_user_gate_over_health_finding(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    provider: str,
) -> None:
    goal_id = "diagnose-precedence-goal"
    user_gate_text = "[P0] Confirm user gate before health alert override."
    project = tmp_path / f"project-{provider}"
    runtime = tmp_path / f"runtime-{provider}"
    project.mkdir(parents=True)
    (project / "README.md").write_text("# Fixture\n", encoding="utf-8")
    state_rel = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_rel
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        "# Fixture\n\n## User Todo\n\n"
        f"- [ ] {user_gate_text}\n"
        "  <!-- loopx:todo todo_id=todo_user_gate_prec status=open "
        "task_class=user_gate action_kind=owner_decision priority=P0 -->\n",
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": goal_id,
                        "domain": "diagnose-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_rel,
                        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
                        "quota": {"compute": 1.0, "window_hours": 24},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    import_fixture_authority(registry, goal_id, provider=provider)
    (runtime / "registry.global.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": goal_id,
                        "state_file": str(tmp_path / "stale-missing.md"),
                        "source_registry": str(registry),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    assert main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", "status", "--goal-id", goal_id]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    goal_rows = [row for row in status_payload["attention_queue"]["items"] if row.get("goal_id") == goal_id]
    assert [row.get("status") for row in goal_rows] == ["state_file_missing", "active_state_user_gate"]

    for args in (["diagnose", "--goal-id", goal_id], ["diagnose"]):
        assert main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args]) == 0
        diag_payload = json.loads(capsys.readouterr().out)
        selected = diag_payload["selected"]
        assert selected["status"] == "active_state_user_gate"
        assert selected["waiting_on"] == "controller"
        assert user_gate_text in str(selected["user_question"])
