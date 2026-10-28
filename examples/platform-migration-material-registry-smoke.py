#!/usr/bin/env python3
"""Smoke-test a sanitized platform-migration authority/material registry."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "platform-migration-material-registry"
EXPECTED_USER_TODO = "Confirm whether owner review is fresh enough to resume delivery."
EXPECTED_AGENT_TODO = "Run read-only map and report material freshness without internal links."
EXPECTED_NEXT_ACTION = "Refresh the public-safe material registry summary."
EXPECTED_STOP_CONDITION = "stop until the controller or owner resolves this gate"
EXPECTED_MATERIAL_CONTEXT = (
    "authority/material: topics=3, materials=6, repositories=2, owner_review_required=1, "
    "stale=1, current_authority=1, risk=medium"
)


def write_platform_migration_fixture(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / "docs" / "meta").mkdir(parents=True, exist_ok=True)
    for rel, text in {
        "README.md": "# Sanitized Platform Migration\n",
        "docs/MIGRATION_GOAL.md": "# Migration Goal\n",
        "docs/OWNER_REVIEW.md": "# Owner Review\n",
        "docs/VALIDATION.md": "# Validation\n",
        "docs/meta/DOC_REGISTRY.yaml": "topics: {}\n",
    }.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    (project / state_file).write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Platform Migration Material Registry\n\n"
        "## User Todo\n\n"
        "- [ ] Confirm whether owner review is fresh enough to resume delivery.\n"
        "  <!-- loopx:todo todo_id=todo_owner_review_gate status=open "
        "task_class=user_gate action_kind=owner_review_freshness global_gate=true -->\n\n"
        "## Agent Todo\n\n"
        "- [ ] Run read-only map and report material freshness without internal links.\n"
        "  <!-- loopx:todo todo_id=todo_material_registry_map status=open "
        "task_class=advancement_task action_kind=read_only_map -->\n\n"
        "## Next Action\n\n"
        "- Refresh the public-safe material registry summary.\n\n"
        "## Platform Migration Pilot Boundary\n\n"
        "Status: pre-evidence scaffold. Do not read material bodies, internal repositories, "
        "or owner discussion text before this projection is validated.\n\n"
        "- Goal identity: complex migration authority/material registry pilot.\n"
        "- Evidence classes: current authority, owner review, source repository, "
        "target repository, generated artifact, validation report, historical note.\n"
        "- Public projection: role, freshness, missing gate, owner-review-required, "
        "next public-safe action, validation surface, quota state, stop condition, todo counts.\n"
        "- Private retention: source links, repository paths, raw command output, owner discussion, "
        "generated diffs, business/team/person details.\n"
        "- Write scope: local state and read-only projection only.\n"
        "- Stop condition: private material, company repository drilldown, owner conclusion, "
        "configuration generation, upload, or production action.\n",
        encoding="utf-8",
    )

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "complex-migration",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "requires_authority_registry": True,
                        "authority_sources": [
                            {"kind": "doc", "role": "primary", "path": "docs/MIGRATION_GOAL.md"}
                        ],
                        "authority_registry": {
                            "path": "docs/meta/DOC_REGISTRY.yaml",
                            "read_status": "read",
                            "default_entry_docs": [
                                "docs/MIGRATION_GOAL.md",
                                "docs/OWNER_REVIEW.md",
                                "docs/VALIDATION.md",
                            ],
                            "topic_authority": {
                                "goal": "docs/MIGRATION_GOAL.md",
                                "owner_review": "docs/OWNER_REVIEW.md",
                                "validation": "docs/VALIDATION.md",
                            },
                            "project_materials": {
                                "migration_strategy": {
                                    "role": "current_authority",
                                    "source_kind": "external_doc",
                                    "freshness": "current",
                                },
                                "source_repo": {
                                    "role": "source_surface",
                                    "source_kind": "repository",
                                    "freshness": "read_only_status_ok",
                                },
                                "target_repo": {
                                    "role": "implementation_surface",
                                    "source_kind": "repository",
                                    "freshness": "read_only_status_ok",
                                },
                                "owner_review_packet": {
                                    "role": "owner_review_surface",
                                    "source_kind": "review_doc",
                                    "freshness": "owner_review_required",
                                },
                                "legacy_decision_note": {
                                    "role": "historical_reference",
                                    "source_kind": "external_doc",
                                    "freshness": "stale",
                                },
                                "validation_snapshot": {
                                    "role": "validation_evidence",
                                    "source_kind": "report",
                                    "freshness": "current",
                                },
                            },
                            "deprecated_source_count": 1,
                            "conflict_risk": "medium",
                        },
                        "adapter": {
                            "kind": "complex_project_read_only_map_v0",
                            "status": "connected-read-only",
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    import_fixture_authority(registry_path, GOAL_ID, provider="sqlite")
    return registry_path


def run_cli(root: Path, registry_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(root / "runtime"),
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def assert_public_safe(text: str) -> None:
    forbidden = [
        "byte" + "dance",
        "lark" + "office",
        "company-internal",
        "token",
        "AKIA",
        "/" + "Users/",
    ]
    lowered = text.lower()
    for marker in forbidden:
        assert marker.lower() not in lowered, (marker, text)


def assert_no_evidence_boundary_scaffold(state_text: str) -> None:
    assert "## Platform Migration Pilot Boundary" in state_text, state_text
    required = [
        "pre-evidence scaffold",
        "Goal identity",
        "Evidence classes",
        "Public projection",
        "Private retention",
        "Write scope",
        "Stop condition",
        "owner-review-required",
        "read-only projection only",
    ]
    for marker in required:
        assert marker in state_text, marker
    assert "Do not read material bodies" in state_text, state_text
    assert "company repository drilldown" in state_text, state_text
    assert_public_safe(state_text)


def assert_material_counts(
    registry: dict[str, object],
    *,
    expect_required: bool = True,
    expect_deprecated_count: bool = True,
    expected_default_entries_present: int = 3,
) -> None:
    assert registry["declared"] is True, registry
    if expect_required:
        assert registry["required"] is True, registry
    assert registry["default_entry_count"] == 3, registry
    assert registry["default_entries_present"] == expected_default_entries_present, registry
    assert registry["topic_authority_count"] == 3, registry
    assert registry["project_material_count"] == 6, registry
    assert registry["project_material_repository_count"] == 2, registry
    assert registry["project_material_owner_review_required_count"] == 1, registry
    assert registry["project_material_stale_count"] == 1, registry
    assert registry["project_material_current_authority_count"] == 1, registry
    if expect_deprecated_count:
        assert registry["deprecated_source_count"] == 1, registry
    assert registry["conflict_risk"] == "medium", registry


def assert_no_evidence_project_asset(project_asset: dict[str, object]) -> None:
    assert project_asset["owner"] == "controller", project_asset
    assert project_asset["gate"] == "active_state_user_gate", project_asset
    assert project_asset["next_action"] == EXPECTED_USER_TODO, project_asset
    assert project_asset["active_state_next_action"] == EXPECTED_NEXT_ACTION, project_asset
    assert project_asset["stop_condition"] == EXPECTED_STOP_CONDITION, project_asset
    assert project_asset["user_todos"]["open"] == 1, project_asset
    assert project_asset["user_todos"]["next"] == EXPECTED_USER_TODO, project_asset
    assert project_asset["agent_todos"]["open"] == 1, project_asset
    assert project_asset["agent_todos"]["next"] == EXPECTED_AGENT_TODO, project_asset
    assert project_asset["quota"]["compute"] == 1.0, project_asset
    assert project_asset["quota"]["state"] == "operator_gate", project_asset
    assert project_asset["quota"]["spent_slots"] == 0, project_asset
    assert project_asset["latest_validation"]["classification"] == "read_only_project_map", project_asset
    assert_public_safe(json.dumps(project_asset, ensure_ascii=False))


def assert_no_evidence_handoff_readiness(readiness: dict[str, object]) -> None:
    assert readiness["ready"] is False, readiness
    assert readiness["codex_ready"] is False, readiness
    assert readiness["source"] == "project_asset", readiness
    assert readiness["quota_state"] == "operator_gate", readiness
    assert readiness["handoff_status"] == "ready_waiting_for_run", readiness
    assert readiness["post_handoff_run_seen"] is False, readiness
    assert "post_handoff_latest_run" not in readiness, readiness
    assert readiness["next_probe"] == f"loopx review-packet --goal-id {GOAL_ID} --handoff-only", readiness
    checks = readiness["checks"]
    assert checks["project_asset_backed"] is True, readiness
    assert checks["same_source_should_run"] is True, readiness
    assert checks["codex_ready"] is False, readiness
    assert checks["handoff_has_next_action"] is True, readiness
    assert checks["handoff_has_stop_condition"] is True, readiness
    assert checks["handoff_sanitized_surface"] is True, readiness
    assert_public_safe(json.dumps(readiness, ensure_ascii=False))


def assert_status_markdown_no_evidence_projection(status_markdown: str) -> None:
    expected_lines = [
        "project_asset_source: project_asset",
        f"project_asset: owner=controller gate=active_state_user_gate stop={EXPECTED_STOP_CONDITION}",
        f"asset_next_action: {EXPECTED_USER_TODO}",
        "asset_todos: user_open=1 agent_open=1",
        f"asset_user_todo: {EXPECTED_USER_TODO}",
        f"asset_agent_todo: {EXPECTED_AGENT_TODO}",
        "asset_active_state_next_action: Refresh the public-safe material registry summary.",
        "asset_latest_run_recommended_action: Refresh the public-safe material registry summary.",
        "asset_quota: compute=1.0 state=operator_gate slots=0/1440",
        "handoff_readiness: ready=False codex_ready=False source=project_asset quota_state=operator_gate",
        "handoff_interface_budget: mode=project_agent_handoff max_lines=16 max_chars=1800",
        (
            "handoff_checks: "
            "pass=project_asset_backed,same_source_should_run,handoff_has_next_action,"
            "handoff_has_stop_condition,handoff_sanitized_surface fail=codex_ready"
        ),
        "handoff_state: status=ready_waiting_for_run post_handoff_run_seen=False ready_at=",
        f"handoff_probe: `loopx review-packet --goal-id {GOAL_ID} --handoff-only`",
        "authority_material: entries=0/3 topics=3 materials=6 repositories=2",
        "owner_review_required=1 stale=1 current_authority=1 risk=medium",
    ]
    for line in expected_lines:
        assert line in status_markdown, (line, status_markdown)
    assert_public_safe(status_markdown)


def assert_review_packet_no_evidence_projection(packet: str) -> None:
    expected_lines = [
        "来源：project_asset（owner/gate/next/stop 来自 attention_queue.project_asset）",
        "项目资产来源：project_asset（owner/gate/next/stop 来自 attention_queue.project_asset）",
        f"Agent 待办：{EXPECTED_AGENT_TODO}",
        f"材料上下文：{EXPECTED_MATERIAL_CONTEXT}",
        "停止条件：需要真实 approval、write-control、run history append、生产动作或命令失败时，停下等明确授权。",
        "不含内部链接、路径或正文",
    ]
    for line in expected_lines:
        assert line in packet, (line, packet)
    assert_public_safe(packet)


def assert_handoff_only_no_evidence_projection(handoff: str) -> None:
    assert handoff.startswith(f"目标校验：本段只适用于 goal_id=`{GOAL_ID}`"), handoff
    assert len(handoff.splitlines()) == 16, handoff
    assert len(handoff) <= 900, handoff
    assert "【LoopX Review Packet】" not in handoff, handoff
    assert "【人只需判断】" not in handoff, handoff
    assert "【用户本地 Gate 记录草稿】" not in handoff, handoff
    expected_lines = [
        "项目资产来源：project_asset（owner/gate/next/stop 来自 attention_queue.project_asset）",
        f"Agent 待办：{EXPECTED_AGENT_TODO}",
        f"材料上下文：{EXPECTED_MATERIAL_CONTEXT}",
        "停止条件：需要真实 approval、write-control、run history append、生产动作或命令失败时，停下等明确授权。",
        f"--goal-id {GOAL_ID}",
    ]
    for line in expected_lines:
        assert line in handoff, (line, handoff)
    assert_public_safe(handoff)


def assert_handoff_only_json_projection(payload: dict[str, object]) -> None:
    assert payload["ok"] is True, payload
    assert payload["goal_id"] == GOAL_ID, payload
    assert payload["handoff_only"] is True, payload
    assert payload["handoff_text"] == payload["project_agent_handoff"], payload
    assert "packet" not in payload, payload
    assert "operator_gate_preview" not in payload, payload
    assert payload["kind"] == "controller", payload
    assert payload["status"] == "active_state_user_gate", payload
    assert payload["waiting_on"] == "controller", payload
    assert_handoff_only_no_evidence_projection(payload["handoff_text"])
    assert_public_safe(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-platform-migration-") as tmp:
        root = Path(tmp)
        registry_path = write_platform_migration_fixture(root)
        state_text = (registry_path.parent.parent / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md").read_text(
            encoding="utf-8"
        )
        assert_no_evidence_boundary_scaffold(state_text)

        mapped = json.loads(
            run_cli(root, registry_path, "--format", "json", "read-only-map", "--goal-id", GOAL_ID).stdout
        )
        assert mapped["ok"] is True, mapped
        assert mapped["appended"] is True, mapped
        assert_material_counts(
            {
                key.removeprefix("authority_registry_"): value
                for key, value in mapped["project_map"].items()
                if key.startswith("authority_registry_")
            }
            | {
                key: mapped["project_map"][key]
                for key in mapped["project_map"]
                if key == "topic_authority_count" or key.startswith("project_material_")
            },
            expect_required=False,
            expect_deprecated_count=False,
        )

        status = json.loads(run_cli(root, registry_path, "--format", "json", "status", "--limit", "20").stdout)
        assert status["ok"] is True, status
        goal = next(goal for goal in status["run_history"]["goals"] if goal["id"] == GOAL_ID)
        assert_material_counts(goal["authority_registry"], expected_default_entries_present=0)
        queue_item = next(item for item in status["attention_queue"]["items"] if item["goal_id"] == GOAL_ID)
        assert_no_evidence_project_asset(queue_item["project_asset"])
        assert_no_evidence_handoff_readiness(queue_item["handoff_readiness"])

        status_markdown = run_cli(root, registry_path, "status", "--limit", "20").stdout
        assert_status_markdown_no_evidence_projection(status_markdown)

        packet = run_cli(root, registry_path, "review-packet", "--goal-id", GOAL_ID).stdout
        assert_review_packet_no_evidence_projection(packet)

        handoff = run_cli(root, registry_path, "review-packet", "--goal-id", GOAL_ID, "--handoff-only").stdout
        assert_handoff_only_no_evidence_projection(handoff)

        handoff_payload = json.loads(
            run_cli(
                root,
                registry_path,
                "--format",
                "json",
                "review-packet",
                "--goal-id",
                GOAL_ID,
                "--handoff-only",
            ).stdout
        )
        assert_handoff_only_json_projection(handoff_payload)

        status_path = root / "status.json"
        html_path = root / "status.html"
        status_path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        subprocess.run(
            [sys.executable, "examples/render-status-dashboard.py", str(status_path), str(html_path)],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        html = html_path.read_text(encoding="utf-8")
        assert "Authority" in html, html
        assert "materials 6; repos 2; owner review 1; stale 1; risk medium" in html, html
        assert "Public-safe counts only; no source links or raw material text." in html, html
        assert "Project Asset" in html, html
        assert "owner controller; gate active_state_user_gate" in html, html
        assert f"<b>Next</b> {EXPECTED_USER_TODO}" in html, html
        assert f"<b>Stop</b> {EXPECTED_STOP_CONDITION}" in html, html
        assert f"User todo: 1/1 open; next {EXPECTED_USER_TODO}" in html, html
        assert f"Agent todo: 1/1 open; next {EXPECTED_AGENT_TODO}" in html, html
        assert "<b>Quota</b> compute 1.0; operator_gate; slots 0/1440" in html, html
        assert "<b>Validation</b> read_only_project_map" in html, html
        assert "Handoff readiness" in html, html
        assert "not ready; codex_ready False; source project_asset; quota operator_gate" in html, html
        assert "<b>Failed checks</b> codex_ready" in html, html
        assert "<b>Handoff state</b> status ready_waiting_for_run; post_handoff_run_seen False; ready_at" in html, html
        assert f"<b>Probe</b> loopx review-packet --goal-id {GOAL_ID} --handoff-only" in html, html
        assert_public_safe(html)

    print("platform-migration-material-registry-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
