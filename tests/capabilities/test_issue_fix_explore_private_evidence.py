from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.explore.activation import (
    sync_explore_graph_after_material_refresh,
)
from loopx.capabilities.explore.result_log import (
    build_explore_node_event,
    explore_result_log_path,
    load_explore_result_events,
)
from loopx.capabilities.issue_fix.explore_projection import (
    _refs,
    project_issue_fix_explore_graph,
)
from loopx.capabilities.issue_fix.feasibility import build_issue_fix_feasibility_packet
from loopx.cli_commands.project_lifecycle_sinks import lark_explore_graph_syncer
from loopx.domain_packs.issue_fix import (
    default_issue_fix_feasibility_ledger_path,
    upsert_issue_fix_feasibility_ledger_jsonl,
)
from loopx.rollout_event_log import (
    append_rollout_event,
    build_rollout_event,
    rollout_event_log_path,
)


@pytest.fixture
def private_source(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    state = tmp_path / "state.md"
    evidence = tmp_path / "private-evidence.md"
    evidence.write_text("Private local validation result.\n", encoding="utf-8")
    state.write_text(
        "## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n\n"
        "- [ ] [P1] Repair the projection\n"
        "  <!-- loopx: todo_id=todo_gap status=claimed claimed_by=fixture "
        "target_capabilities=issue_fix_explore_projection "
        f"explore_result_node_refs=cap_projection evidence={evidence} -->\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [
                    {"id": "fixture", "repo": str(tmp_path), "state_file": str(state)}
                ],
            }
        ),
        encoding="utf-8",
    )
    packet = build_issue_fix_feasibility_packet(
        url="https://github.com/public-fixture/widgets/issues/7",
        reproduction_status="confirmed",
        scope_class="bounded",
        reproduction_label="focused reproduction",
        validation_label="focused validation",
    )
    upsert_issue_fix_feasibility_ledger_jsonl(
        default_issue_fix_feasibility_ledger_path(project=tmp_path, goal_id="fixture"),
        packet,
    )
    return registry, state, evidence, runtime


def test_private_todo_evidence_projects_without_rewriting_source_and_retries_idempotently(
    private_source: tuple[Path, Path, Path, Path],
) -> None:
    registry, state, evidence, runtime = private_source
    original_state, original_evidence = state.read_bytes(), evidence.read_bytes()
    first = project_issue_fix_explore_graph(
        registry_path=registry, goal_id="fixture", execute=True
    )
    node = next(
        node
        for node in first["projection"]["nodes"]
        if node["node_id"] == "cap_projection"
    )
    assert node["evidence_refs"] == ["evidence:todo:todo_gap"]
    assert first["appended_event_count"] > 0
    log = explore_result_log_path(runtime, "fixture")
    first_log = log.read_bytes()
    second = project_issue_fix_explore_graph(
        registry_path=registry, goal_id="fixture", execute=True
    )
    assert second["appended_event_count"] == 0
    assert second["semantic_digest"] == first["semantic_digest"]
    assert log.read_bytes() == first_log
    assert state.read_bytes() == original_state
    assert evidence.read_bytes() == original_evidence
    assert str(evidence) not in json.dumps(load_explore_result_events(log))


def test_real_material_refresh_adapter_needs_no_external_provider_for_private_source(
    private_source: tuple[Path, Path, Path, Path],
) -> None:
    registry, state, evidence, runtime = private_source
    source_before = state.read_bytes()
    syncer = lark_explore_graph_syncer(None, registry_path=registry)
    disabled = sync_explore_graph_after_material_refresh(
        registry_path=registry, goal_id="fixture", syncer=syncer
    )
    assert disabled["status"] == "disabled"
    assert not explore_result_log_path(runtime, "fixture").exists()
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["goals"][0]["explore_graph"] = {"enabled": True}
    registry.write_text(json.dumps(payload), encoding="utf-8")
    enabled = sync_explore_graph_after_material_refresh(
        registry_path=registry, goal_id="fixture", syncer=syncer
    )
    assert enabled["ok"] is True, enabled
    assert enabled["status"] == "not_configured", enabled
    assert enabled["delivery_postcondition"]["satisfied"] is True
    assert (
        "extension_activation" not in enabled or enabled["extension_activation"] is None
    )
    assert state.read_bytes() == source_before
    assert evidence.exists()
    assert not (registry.parent / ".loopx" / "lark-explore.json").exists()


def test_private_rollout_evidence_retains_public_refs_and_local_provenance(
    private_source: tuple[Path, Path, Path, Path],
) -> None:
    registry, state, evidence, runtime = private_source
    private_ref = "/opt/fixture/private-evidence.md"
    event = build_rollout_event(
        goal_id="fixture",
        event_kind="capability_gap",
        todo_id="todo_gap",
        status="found",
        summary="Projection needs a bounded repair.",
        details={
            "target_capabilities": "issue_fix_explore_projection",
            "evidence": private_ref,
        },
        artifact_refs=["validation:public-fixture"],
    )
    log = rollout_event_log_path(runtime, "fixture")
    append_rollout_event(log, event)
    source_before = log.read_bytes(), state.read_bytes()
    projected = project_issue_fix_explore_graph(
        registry_path=registry, goal_id="fixture", execute=True
    )
    node = next(
        node
        for node in projected["projection"]["nodes"]
        if node["node_id"] == "cap_projection"
    )
    assert node["evidence_refs"] == [
        f"evidence:rollout:{event['event_id']}",
        "validation:public-fixture",
    ]
    assert private_ref not in json.dumps(projected["projection"])
    assert (log.read_bytes(), state.read_bytes()) == source_before


def test_projection_source_pointer_uses_the_same_privacy_validator() -> None:
    assert _refs(
        [
            "validation:public-fixture",
            "../report.md",
            "token=fixture-value",
            "validation:public-fixture",
        ],
        source_ref="evidence:issue-fix:fix_7_candidate",
    ) == ["validation:public-fixture", "evidence:issue-fix:fix_7_candidate"]
    with pytest.raises(ValueError):
        _refs("../report.md", source_ref="/opt/fixture/private-source")


@pytest.mark.parametrize(
    "private_ref",
    [
        "/opt/fixture/report.md",
        "~/report.md",
        "../report.md",
        "file:///fixture/report.md",
        "C:\\fixture\\report.md",
        "token=fixture-value",
    ],
)
def test_public_writer_keeps_rejecting_private_refs(private_ref: str) -> None:
    with pytest.raises(ValueError):
        build_explore_node_event(
            goal_id="fixture",
            node_id="node_fixture",
            title="Fixture",
            evidence_refs=[private_ref],
        )
