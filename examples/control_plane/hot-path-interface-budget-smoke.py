#!/usr/bin/env python3
"""Smoke-test the current hot-path interface budgets.

The goal is not to freeze every payload field forever. It is to make growth in
heartbeat, handoff, quota, and dashboard status payloads visible before a short
worker prompt has to absorb the extra detail.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.cli_commands.status import review_packet_handoff_only_payload  # noqa: E402
from loopx.extensions.presentation import (  # noqa: E402
    collect_active_extension_presentation_surfaces,
    publish_extension_projection,
)
from loopx.extensions.runtime import install_extension  # noqa: E402
from loopx.heartbeat_prompt import (  # noqa: E402
    build_heartbeat_prompt,
    project_heartbeat_agent_input,
)
from loopx.interface_budget import build_interface_budget_cadence  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.review_packet import build_review_packet  # noqa: E402
from loopx.status import collect_status  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)


GOAL_ID = "interface-budget-goal"
APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)
CONTRACT_DOC = REPO_ROOT / "docs" / "reference" / "contracts" / "interface-budget-contract.md"
SURFACE_BUDGETS = {
    "heartbeat_prompt_json": {
        "owner": "heartbeat automation",
        "consumer": "wake and route one bounded turn",
        "cold_path": "quota should-run, status, or review-packet --handoff-only",
        # Includes generator metadata and scoped commands, not just task_body.
        # The independent 2,500-character thin body cap remains unchanged.
        "max_json_chars": 5_400,
        "max_nested_keys": 40,
        "max_top_level_keys": 30,
        "budget_field": "interface_budget",
    },
    "review_packet_handoff_only_json": {
        "owner": "project-agent handoff",
        "consumer": "forward the smallest sufficient task packet",
        "cold_path": "full review-packet or run-history artifact",
        "max_json_chars": 3_000,
        "max_nested_keys": 40,
        "max_top_level_keys": 18,
        "budget_field": "handoff_interface_budget",
    },
    "quota_should_run_json": {
        "owner": "quota guard",
        "consumer": "decide whether the selected goal may spend compute",
        "cold_path": "status, history, or active state",
        # Codex keeps a lossless codex_app compatibility alias while the
        # provider-neutral app_automation packet becomes canonical.
        # Pre-limit work counts add useful scope/completeness evidence; allow
        # modest headroom after removing the redundant observed-row count.
        "max_json_chars": 14_500,
        "max_nested_keys": 360,
        "max_top_level_keys": 52,
    },
    "dashboard_status_json": {
        "owner": "operator dashboard",
        "consumer": "render first-screen operator state",
        "cold_path": "history, run artifacts, or project-local adapter output",
        "max_json_chars": 22_500,
        "max_nested_keys": 350,
        "max_top_level_keys": 27,
    },
}


def write_registry(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Interface Budget Goal\n\n"
        "## Agent Todo\n\n"
        "- [ ] Keep the handoff compact and query cold-path details only on demand.\n",
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
                        "domain": "interface-budget-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "fixture_connected_readonly_v0",
                            "status": "connected-read-only",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                        },
                        "authority_sources": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, project


def append_run(
    root: Path,
    *,
    generated_at: str = "2026-01-01T00:05:00+00:00",
    classification: str = "state_refreshed",
    recommended_action: str = "Continue compact interface-budget validation.",
    interface_budget_cadence: dict[str, Any] | None = None,
) -> None:
    compact = generated_at.replace("-", "").replace(":", "")
    run_dir = root / "runtime" / "goals" / GOAL_ID / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / f"{compact}-interface-budget.json"
    markdown_path = run_dir / f"{compact}-interface-budget.md"
    record = {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": classification,
        "recommended_action": recommended_action,
        "health_check": "fixture hot-path budget run",
    }
    if interface_budget_cadence:
        record["interface_budget_cadence"] = interface_budget_cadence
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text("# Fixture hot-path budget run\n", encoding="utf-8")
    with (run_dir / "index.jsonl").open("a", encoding="utf-8") as f:
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


def install_ready_presentation_surface(root: Path) -> None:
    provider = root / "presentation-provider"
    provider.write_text(
        f"""#!{sys.executable}
import json
import sys

if "--doctor" in sys.argv:
    raise SystemExit(0)

json.load(sys.stdin)
json.dump(
    {{
        "presentation_projection": {{
            "schema_version": "extension_presentation_projection_v0",
            "surface_id": "budget-surface",
            "goal_id": "{GOAL_ID}",
            "generated_at": "2026-01-01T00:06:00+00:00",
            "review_due_at": None,
            "lineage": {{
                "source_id": "budget-surface-source",
                "version": 1,
                "row_lifecycle": "active",
                "supersedes": [],
                "superseded_by": None,
            }},
            "view_schema": "budget_surface_v0",
            "view": {{"headline": "Ready budget surface"}},
        }},
    }},
    sys.stdout,
)
""",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    manifest = root / "presentation-extension.toml"
    manifest.write_text(
        f"""\
schema_version = "loopx_extension_manifest_v0"
id = "budget-presentation-extension"
version = "1.0.0"
requires_loopx_api = ">=1,<2"
permissions = []

[runtime]
protocol = "budget_presentation_extension_v0"
entrypoint = {json.dumps(str(provider))}
doctor_args = ["--doctor"]
required_permissions = []
timeout_seconds = 5

[[presentation_surfaces]]
id = "budget-surface"
kind = "budget_surface"
title = "Budget Surface"
view_schema = "budget_surface_v0"
view_validator = "loopx.extensions.presentation:validate_opaque_presentation_view"
visibility = "public-safe"
empty_state_title = "No budget surface yet"
empty_state_detail = "Publish the bounded budget projection."
""",
        encoding="utf-8",
    )
    state_file = root / "runtime" / "extensions" / "state.json"
    install_extension(manifest, state_file=state_file, execute=True)
    publish_extension_projection(
        "budget-presentation-extension",
        "budget-surface",
        state_file=state_file,
        request={"schema_version": "budget_surface_request_v0"},
        execute=True,
    )
    surfaces = collect_active_extension_presentation_surfaces(
        state_file=state_file,
    )
    assert surfaces["ready_count"] == 1, surfaces
    assert surfaces["items"][0]["state"] == "ready", surfaces


def json_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def nested_key_count(value: Any, *, max_depth: int = 3) -> int:
    """Count bounded nested object keys so hot paths do not grow sideways."""
    if max_depth < 0:
        return 0
    if isinstance(value, dict):
        return len(value) + sum(
            nested_key_count(child, max_depth=max_depth - 1) for child in value.values()
        )
    if isinstance(value, list):
        return sum(nested_key_count(child, max_depth=max_depth - 1) for child in value[:20])
    return 0


def assert_surface(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    budget = SURFACE_BUDGETS[name]
    owner = str(budget.get("owner") or "")
    consumer = str(budget.get("consumer") or "")
    cold_path = str(budget.get("cold_path") or "")
    nested_keys = nested_key_count(payload)
    assert owner, (name, budget)
    assert consumer, (name, budget)
    assert cold_path, (name, budget)
    assert json_size(payload) <= int(budget["max_json_chars"]), (name, json_size(payload), budget)
    assert nested_keys <= int(budget["max_nested_keys"]), (name, nested_keys, budget)
    assert len(payload) <= int(budget["max_top_level_keys"]), (name, len(payload), budget)

    budget_field = budget.get("budget_field")
    if budget_field:
        interface_budget = payload.get(str(budget_field))
        assert isinstance(interface_budget, dict), (name, payload)
        assert interface_budget.get("within_budget") is True, (name, interface_budget)

    return {
        "surface": name,
        "owner": owner,
        "consumer": consumer,
        "cold_path": cold_path,
        "json_chars": json_size(payload),
        "nested_keys": nested_keys,
        "top_level_keys": len(payload),
        "max_json_chars": budget["max_json_chars"],
        "max_nested_keys": budget["max_nested_keys"],
        "max_top_level_keys": budget["max_top_level_keys"],
    }


def assert_contract_doc_matches_budget_table() -> None:
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    for surface, budget in SURFACE_BUDGETS.items():
        assert surface in text, surface
        assert str(budget["owner"]) in text, (surface, budget)
        assert str(budget["consumer"]) in text, (surface, budget)
        assert str(budget["cold_path"]) in text.replace("`", ""), (surface, budget)
        assert str(budget["max_json_chars"]) in text.replace(",", "").replace("_", ""), surface
        assert str(budget["max_nested_keys"]) in text, surface
        assert str(budget["max_top_level_keys"]) in text, surface
    for field in (
        "interface_budget_cadence",
        "checked_at",
        "next_check_due_at",
        "overdue",
        "within_budget",
        "minimum_headroom_ratio",
        "quiet_skip_until_next_check_due",
        "rerun_hot_path_interface_budget_smoke",
    ):
        assert field in text, field


def assert_cadence_projection(
    root: Path,
    registry_path: Path,
    project: Path,
    cadence: dict[str, Any],
) -> None:
    append_run(
        root,
        generated_at="2099-01-01T00:10:00+00:00",
        classification="interface_budget_cadence_check",
        recommended_action=(
            "Interface-budget drift check is clean; wait until the next cadence due time "
            "unless a prompt or hot-path contract changes."
        ),
        interface_budget_cadence=cadence,
    )
    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=str(root / "runtime"),
        scan_roots=[project],
        limit=5,
    )
    items = status_payload["attention_queue"]["items"]
    matches = [item for item in items if item.get("goal_id") == GOAL_ID]
    assert len(matches) == 1, items
    projected = matches[0]["project_asset"]["interface_budget_cadence"]
    assert projected["overdue"] is False, projected
    assert projected["within_budget"] is True, projected
    assert projected["next_check_due_at"] == "2099-01-02T00:10:00+00:00", projected
    assert projected["headroom_remaining"] == 7, projected
    assert projected["recommendation"] == "quiet_skip_until_next_check_due", projected

    quota_payload = build_quota_should_run(
        status_payload,
        goal_id=GOAL_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert quota_payload["should_run"] is True, quota_payload
    assert quota_payload["interface_budget_cadence"]["overdue"] is False, quota_payload
    assert quota_payload["interface_budget_cadence"]["tightest_surface"] == cadence["tightest_surface"], quota_payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-hot-path-budget-") as tmp:
        root = Path(tmp)
        registry_path, project = write_registry(root)
        append_run(root)
        install_ready_presentation_surface(root)
        status_payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(root / "runtime"),
            scan_roots=[project],
            limit=5,
        )
        assert "presentation_surfaces" not in status_payload, status_payload
        status_items = status_payload["attention_queue"]["items"]
        assert status_items, status_payload
        # Internal graph evaluations must not expand public status payloads.
        assert "succession_evaluation" not in json.dumps(status_items)
        assert "task_graph_projection" not in status_items[0], status_items[0]
        route_health = status_payload["runtime_projection_routes"]
        assert route_health["healthy"] is True, route_health
        assert route_health["goal_count"] >= 1, route_health
        quota_payload = build_quota_should_run(
            status_payload,
            goal_id=GOAL_ID,
            scheduler_execution_context=APP_SCHEDULER_CONTEXT,
        )
        review_packet = build_review_packet(status_payload, goal_id=GOAL_ID, action_kind="codex")
        handoff_payload = review_packet_handoff_only_payload(review_packet)
        heartbeat_payload = project_heartbeat_agent_input(
            build_heartbeat_prompt(
                goal_id=GOAL_ID,
                thin=True,
                runtime_profile="codex_app_heartbeat",
            )
        )
        # Real automations normally bind an agent; the unbound fixture alone
        # misses repeated identity/scope arguments in the generator envelope.
        for binding in (
            {"agent_id": "worker-a"},
            {"agent_id": "worker-a", "agent_scopes": ["implementation", "review"]},
        ):
            assert_surface("heartbeat_prompt_json", build_heartbeat_prompt(
                goal_id=GOAL_ID,
                thin=True,
                runtime_profile="codex_app_heartbeat",
                **binding,
            ))

        assert quota_payload["should_run"] is True, quota_payload
        reset_policy = quota_payload["scheduler_hint"]["reset_policy"]
        assert reset_policy["reset_token"], reset_policy
        assert reset_policy["app_automation_initial_rrule"], reset_policy
        assert reset_policy["host_state_key"] == "scheduler_hint.reset_policy.reset_token", reset_policy
        assert "identity_snapshot" not in reset_policy, reset_policy
        assert "profile_snapshot" not in reset_policy, reset_policy
        assert "identity_keys" not in reset_policy, reset_policy
        assert "profile" not in reset_policy, reset_policy
        assert len(reset_policy["identity_signature"]) == 12, reset_policy
        assert "profile_signature" not in reset_policy, reset_policy
        assert handoff_payload["within_budget"] is True, handoff_payload
        summaries = [
            assert_surface("heartbeat_prompt_json", heartbeat_payload),
            assert_surface("review_packet_handoff_only_json", handoff_payload),
            assert_surface("quota_should_run_json", quota_payload),
            assert_surface("dashboard_status_json", status_payload),
        ]
        cadence = build_interface_budget_cadence(
            summaries,
            checked_at="2099-01-01T00:10:00+00:00",
            now="2099-01-01T01:00:00+00:00",
            freshness_hours=24,
        )
        assert cadence["within_budget"] is True, cadence
        assert cadence["overdue"] is False, cadence
        assert cadence["surface_count"] == len(summaries), cadence
        assert cadence["next_check_due_at"] == "2099-01-02T00:10:00+00:00", cadence
        assert cadence["minimum_headroom_ratio"] is not None, cadence
        assert cadence["headroom_remaining"] == 7, cadence
        assert cadence["recommendation"] == "quiet_skip_until_next_check_due", cadence
        saturated_summaries = [dict(summary) for summary in summaries]
        saturated_summaries[0]["max_json_chars"] = saturated_summaries[0]["json_chars"]
        saturated_cadence = build_interface_budget_cadence(
            saturated_summaries,
            checked_at="2099-01-01T00:10:00+00:00",
            now="2099-01-01T01:00:00+00:00",
            freshness_hours=24,
        )
        assert saturated_cadence["headroom_remaining"] == 0, saturated_cadence
        assert saturated_cadence["recommendation"] == "rerun_hot_path_interface_budget_smoke", saturated_cadence
        stale_cadence = build_interface_budget_cadence(
            summaries,
            checked_at="2099-01-01T00:10:00+00:00",
            now="2099-01-02T00:10:00+00:00",
            freshness_hours=24,
        )
        assert stale_cadence["overdue"] is True, stale_cadence
        assert stale_cadence["recommendation"] == "rerun_hot_path_interface_budget_smoke", stale_cadence
        assert_cadence_projection(root, registry_path, project, cadence)
        assert_contract_doc_matches_budget_table()

        for summary in summaries:
            print(
                "{surface}: owner={owner} consumer={consumer} "
                "json_chars={json_chars}/{max_json_chars} "
                "nested_keys={nested_keys}/{max_nested_keys} "
                "top_level_keys={top_level_keys}/{max_top_level_keys}".format(**summary)
            )
        print(
            "interface_budget_cadence: checked_at={checked_at} next_check_due_at={next_check_due_at} "
            "tightest={tightest_surface}/{tightest_metric} headroom={headroom_remaining} "
            "overdue={overdue}".format(**cadence)
        )
    print("hot-path-interface-budget-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
