#!/usr/bin/env python3
"""Smoke-test status collection read-model orchestration parity."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx import status as status_module  # noqa: E402
from loopx.control_plane.status import collection as collection_read_model  # noqa: E402


GOAL_ID = "status-collection-fixture"


def write_registry(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"

    state_path = project / state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Status Collection Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Keep status collection orchestration behind the read-model boundary.\n"
        "  <!-- loopx:todo todo_id=todo_status_collection_fixture status=open "
        "task_class=advancement_task action_kind=status_collection_refactor "
        "claimed_by=codex-product-capability -->\n",
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
                        "domain": "loopx-platform",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "quota": {
                            "compute": 1,
                            "window_hours": 24,
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
    return registry_path


def scrub_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<generated_at>"
            if key == "generated_at"
            else scrub_volatile(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_volatile(item) for item in value]
    return value


def assert_wrapper_parity(registry_path: Path, runtime_root: Path, scan_root: Path) -> None:
    kwargs: dict[str, Any] = {
        "registry_path": registry_path,
        "runtime_root_override": str(runtime_root),
        "scan_roots": [scan_root],
        "limit": 3,
        "include_task_graph": True,
        "goal_id": GOAL_ID,
        "available_capabilities": ["network", "material_lifecycle"],
    }
    # Compare one observation time without hiding freshness or coverage fields.
    with patch.object(
        collection_read_model, "now_utc_iso", return_value="2026-01-01T00:00:00Z"
    ):
        wrapper = status_module.collect_status(**kwargs)
        direct = collection_read_model.collect_status(
            **kwargs,
            context=status_module.build_status_collection_context(),
        )

    assert scrub_volatile(wrapper) == scrub_volatile(direct), (wrapper, direct)
    assert wrapper["ok"] is True, wrapper
    assert wrapper["goal_filter"] == GOAL_ID, wrapper
    assert wrapper["attention_queue"]["item_count"] == 1, wrapper
    item = wrapper["attention_queue"]["items"][0]
    assert item["goal_id"] == GOAL_ID, item
    assert wrapper["todo_index"]["total_count"] >= 1, wrapper["todo_index"]


def assert_context_orchestration() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    runtime_root = Path("/tmp/status-collection-runtime")
    history_audit = object()

    def record(name: str, value: Any) -> Any:
        calls.append((name, value if isinstance(value, dict) else {"value": value}))
        return value

    def load_registry(path: Path) -> dict[str, Any]:
        return record("load_registry", {"common_runtime_root": str(runtime_root), "path": str(path)})

    def resolve_runtime_root(
        registry: dict[str, Any],
        override: str | None,
        *,
        registry_path: Path | None = None,
    ) -> Path:
        assert registry["common_runtime_root"] == str(runtime_root), registry
        assert override == "runtime-override", override
        assert registry_path == Path("registry.json"), registry_path
        return runtime_root

    def collect_status_history(**kwargs: Any) -> SimpleNamespace:
        assert kwargs["limit"] == 20, kwargs
        assert kwargs["goal_id"] == GOAL_ID, kwargs
        assert kwargs["status_include_runtime_goals"] is True, kwargs
        history = record(
            "collect_status_history",
            {
                "goal_count": 1,
                "run_count": 0,
                "goals": [],
                "activation_state_filter": kwargs.get("activation_state_filter"),
            },
        )
        return SimpleNamespace(
            status_history=history,
            contract_audit=history_audit,
        )

    def check_contract(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["limit"] == 2, kwargs
        assert kwargs["goal_id_filter"] == GOAL_ID, kwargs
        assert kwargs["history_audit"] is history_audit, kwargs
        return record(
            "check_contract",
            {
                "ok": True,
                "summary": "ok",
                "warnings": [],
                "errors": [],
                "checks": [],
                "activation_state_filter": kwargs.get("activation_state_filter"),
            },
        )

    def build_attention_queue(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["include_task_graph"] is False, kwargs
        assert kwargs["goal_id_filter"] == GOAL_ID, kwargs
        return record(
            "build_attention_queue",
            {
                "items": [],
                "item_count": 0,
                "include_stopped_goal_context": kwargs.get(
                    "include_stopped_goal_context"
                ),
            },
        )

    context = collection_read_model.StatusCollectionContext(
        load_registry=load_registry,
        resolve_runtime_root=resolve_runtime_root,
        collect_global_registry_health=lambda **kwargs: record(
            "collect_global_registry_health",
            {"ok": True, "current_registry_is_global": True},
        ),
        collect_status_history=collect_status_history,
        check_contract=check_contract,
        build_attention_queue=build_attention_queue,
        build_runtime_summaries=lambda **kwargs: record(
            "build_runtime_summaries",
            {
                "run_history": {"display_limit": kwargs.get("display_limit"), "goals": [], "recent_runs": []},
                "event_ledger_summary": {},
                "promotion_readiness_summary": {"goal_id_filter": kwargs.get("goal_id_filter")},
                "decision_freshness_summary": {},
                "usage_summary": {},
                "todo_index": {"limit": kwargs.get("todo_index_limit"), "items": []},
            },
        ),
        build_promotion_gate=lambda **kwargs: record("build_promotion_gate", {}),
        build_status_contract=lambda: record("build_status_contract", {"schema_version": "fixture"}),
        build_contract_health_projection=lambda contract: record("build_contract_health_projection", {}),
        build_agent_management_projection=lambda payload, **kwargs: record(
            "build_agent_management_projection",
            {
                "agents": [],
                "available_capabilities": kwargs.get("available_capabilities"),
            },
        ),
        build_goal_channel_notification_projection=lambda **kwargs: record(
            "build_goal_channel_notification_projection",
            {"goals": [], "goal_id": kwargs.get("goal_id")},
        ),
        status_control_plane_context_limit=20,
        max_todo_index_items=240,
    )

    payload = collection_read_model.collect_status(
        registry_path=Path("registry.json"),
        runtime_root_override="runtime-override",
        scan_roots=[Path("project")],
        limit=2,
        goal_id=GOAL_ID,
        available_capabilities=["network", "material_lifecycle"],
        activation_state_filter="active",
        context=context,
    )

    history_call = next(
        call for call in calls if call[0] == "collect_status_history"
    )
    assert history_call[1]["activation_state_filter"] == "active", history_call
    contract_call = next(call for call in calls if call[0] == "check_contract")
    assert contract_call[1]["activation_state_filter"] == "active", contract_call
    assert payload["goal_projection"]["scope"] == "active", payload["goal_projection"]
    assert payload["goal_projection"]["complete"] is False, payload["goal_projection"]
    assert payload["attention_queue"]["include_stopped_goal_context"] is False, payload
    assert payload["runtime_root"] == str(runtime_root), payload
    assert payload["run_history"]["display_limit"] == 2, payload
    assert payload["todo_index"]["limit"] == 240, payload
    assert "agent_management_projection" not in payload, payload
    assert "goal_channel_notification_projection" not in payload, payload
    agent_management_call = next(
        call for call in calls if call[0] == "build_agent_management_projection"
    )
    assert agent_management_call[1]["available_capabilities"] == [
        "network",
        "material_lifecycle",
    ], agent_management_call
    goal_channel_call = next(
        call
        for call in calls
        if call[0] == "build_goal_channel_notification_projection"
    )
    assert goal_channel_call[1]["goal_id"] == GOAL_ID, goal_channel_call
    assert [name for name, _ in calls][:4] == [
        "load_registry",
        "collect_global_registry_health",
        "collect_status_history",
        "check_contract",
    ], calls


def main() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        registry_path = write_registry(root)
        assert_wrapper_parity(registry_path, root / "runtime", root / "project")
    assert_context_orchestration()
    print("status-collection-readmodel-smoke ok")


if __name__ == "__main__":
    main()
