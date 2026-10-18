"""A manager's live evidence grant is not its initial bounded portfolio."""
import json

import pytest

from loopx.capabilities.manager_context import (
    POLICY_SCHEMA, _root, configure_evidence_scope, evidence_goal_scope,
)
from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME
from tests.control_plane.canonical_authority_fixture import (
    isolate_sqlite_runtime, promoted_create_fixture,
)

CHANNEL = "manager.external." + "a" * 24


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_live_scope_reads_a_real_provider_absent_from_the_turn_index(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, state = promoted_create_fixture(tmp_path, provider=provider)
    policy = _root(root) / "policy.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    grants = {"sender_ids": ["owner"], "targets": [], "blocked_targets": [{"goal_id": "goal-a"}]}
    policy.write_text(json.dumps({"schema_version": POLICY_SCHEMA, "sources": {CHANNEL: grants}}))
    configure_evidence_scope(root, registry, channel=CHANNEL, goal_ids=[], goal_scope="all_registered", execute=True)
    reader = ManagerInspection(context={"scope": "external_goal_scope", "goals": []},
        registry_path=registry, runtime_root=root, owner_scope=False, channel_id=CHANNEL,
        scope_valid=lambda: True, record=lambda _: None,
        discovery_scope=lambda: evidence_goal_scope(root, CHANNEL, registry_path=registry))
    before = state.read_bytes()
    result = reader.read(TOOL_NAME, {"view": "todos", "goal_id": "goal-a"})
    assert result["ok"] and result["source"]["status"] == "read"
    assert result["source"]["coverage"]["active"] == 0
    assert state.read_bytes() == before
    saved = json.loads(policy.read_text())["sources"][CHANNEL]
    assert all(saved[key] == value for key, value in grants.items())
    # Registration after configuration changes the next read, without rewriting policy.
    data = json.loads(registry.read_text())
    data["goals"].append({"id": "later", "repo": str(tmp_path), "state_file": str(state)})
    registry.write_text(json.dumps(data))
    assert evidence_goal_scope(root, CHANNEL, registry_path=registry) == ["goal-a", "later"]
    assert reader.read(TOOL_NAME, {"view": "agents", "goal_id": "later"})["ok"]
    assert json.loads(policy.read_text())["sources"][CHANNEL] == saved
    # Explicit empty selected scope revokes the broader read without changing delivery.
    configure_evidence_scope(root, registry, channel=CHANNEL, goal_ids=[], execute=True)
    assert reader.read(TOOL_NAME, {"view": "todos", "goal_id": "goal-a"})["error"] == "goal_outside_available_scope"


def test_portfolio_pages_current_permitted_inventory_before_collection(tmp_path, monkeypatch):
    import loopx.chat_manager_context as context
    registry = tmp_path / "registry.json"
    ids = [f"goal-{i:03}" for i in range(145)]
    registry.write_text(json.dumps({"goals": [{"id": gid} for gid in ids]}))
    calls = []

    def collect(_registry, _session, _root, **kwargs):
        selected = kwargs["requested_goal_ids"]
        calls.append(selected)
        assert len(selected) <= 12
        return {"goals": [{"goal_id": gid} for gid in selected], "coverage": {"complete": True}}

    monkeypatch.setattr(context, "manager_turn_context", collect)
    reader = ManagerInspection(context={"scope": "owner_global", "goals": []},
        registry_path=registry, runtime_root=tmp_path, owner_scope=True,
        scope_valid=lambda: True, record=lambda _: None)
    found, offset = [], 0
    while offset is not None:
        page = reader.read(TOOL_NAME, {"view": "portfolio", "offset": offset, "limit": 12})
        found.extend(row["goal_id"] for row in page["rows"])
        offset = page["next_offset"]
    assert found == ids
    assert calls[-1] == ids[-1:]
    reader.owner_scope = False
    reader.context["scope"] = "external_goal_scope"
    reader.discovery_scope = lambda: [ids[-1]]
    assert reader.read(TOOL_NAME, {"view": "portfolio"})["rows"] == [{"goal_id": ids[-1]}]
    assert calls[-1] == [ids[-1]]


def test_scope_change_during_a_detail_read_discards_the_result(tmp_path, monkeypatch):
    from loopx.capabilities.manager_context import inspection
    calls, records = [], []
    active = [True]

    def details(*a, **kw):
        calls.append(a[2])
        active[0] = False
        return {"status": "read", "todos": [], "coverage": {"active": 0}}

    monkeypatch.setattr(inspection, "read_manager_goal_details", details)
    reader = ManagerInspection(context={"scope": "external_goal_scope", "goals": []},
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path, owner_scope=False,
        discovery_scope=lambda: ["allowed"], scope_valid=lambda: active[0], record=records.append)
    assert reader.read(TOOL_NAME, {"view": "todos", "goal_id": "allowed"}) == {"ok": False, "error": "authorization_changed"}
    assert calls == ["allowed"] and records == []


def test_unreadable_inventory_is_not_an_empty_authorized_portfolio(tmp_path):
    registry = tmp_path / "registry.json"
    reader = ManagerInspection(context={"scope": "owner_global", "goals": []},
        registry_path=registry, runtime_root=tmp_path, owner_scope=True,
        scope_valid=lambda: True, record=lambda _: None)
    for content in [None, "{", '{"goals": [null]}']:
        if content is not None:
            registry.write_text(content)
        result = reader.read(TOOL_NAME, {"view": "portfolio"})
        assert result["ok"] is False and result["error"] == "goal_inventory_unavailable"
