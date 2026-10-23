"""Native selection packets reach recall without changing their bound work."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from loopx.capabilities.agent_turn_recall.runtime import agent_turn_recall_receipt_path
from loopx.capabilities.reward_memory.experiment import load_reward_memory_experiment_config
from tests.capabilities.test_agent_turn_recall import active_record, raw_config
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    ALTERNATIVE_TODO_ID,
    GOAL_ID,
    _configure_selectable_alternative,
    _heartbeat_receipt_count,
    _initialize_fixture_authority,
    _run_cli,
    _spend_run_count,
    _write_fixture,
)


def _enable_fixture_memory(project: Path, registry: Path) -> Path:
    """Use the real provider adapter with a credential-free subprocess fixture."""
    binary = project / "memory-provider"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\nfrom pathlib import Path\n"
        "root=Path(__file__).parent\nargs=sys.argv[1:]\nactor=None\n"
        "if args[0]=='--actor-peer-id': actor,args=args[1],args[2:]\n"
        "op=args[0]\n"
        "if op=='--version': print('OpenViking 0.4.23')\n"
        "elif op=='version': print('Server: 0.5.0')\n"
        "elif op=='status': print('{}')\n"
        "elif op=='search':\n"
        " with (root/'search-count').open('a') as f: f.write('search\\n')\n"
        " scope=args[args.index('-u')+1]\n"
        " assert actor==scope.split('/peers/')[1].split('/')[0]\n"
        " print(json.dumps({'resources':[{'uri':scope+'/guidance.json','level':2}]}))\n"
        "elif op=='read': print(json.dumps({'content':(root/'guidance.json').read_text()}))\n"
        "else: raise SystemExit(2)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    config = raw_config(goal_id=GOAL_ID, agent_id=AGENT_ID, peer_ref=f"agent:{AGENT_ID}")
    config["project_provider_binding"]["provider_binary"] = str(binary)
    relative = ".loopx/config/reward-memory/recall.json"
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    normalized = load_reward_memory_experiment_config(project=project, config_path=relative)
    (project / "guidance.json").write_text(
        active_record(normalized, expires_at="2099-01-01T00:00:00Z"), encoding="utf-8"
    )
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    data = json.loads(registry.read_text())
    data["goals"][0]["control_plane"] = {"reward_memory": {
        "enabled": True, "experimental": True, "config_path": relative,
        "config_digest": digest, "enabled_agents": [AGENT_ID],
        "enablement_receipts": {AGENT_ID: {
            "schema_version": "reward_memory_enablement_receipt_v0", "status": "verified",
            "goal_id": GOAL_ID, "agent_id": AGENT_ID, "config_digest": digest,
            "provider_id": "openviking", "isolation_mode": "goal_scoped_agent_private",
            "actor_binding_verified": True, "writability_verified": True,
            "exact_readback_verified": True, "probe_count": 1, "write_count": 1,
            "external_writes_performed": True, "observed_at": "2026-01-01T00:00:00Z",
        }},
    }}
    registry.write_text(json.dumps(data), encoding="utf-8")
    return project / "search-count"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_selected_packet_recall_and_rejection_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "turn-selected-memory"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn)
    rc, first = _run_cli(registry, runtime, *guard)
    assert rc == 0, first
    assert first["heartbeat_receipt"]["settlement_binding_owed"] is True
    rc, selected = _run_cli(registry, runtime, *guard, "--todo-id", ALTERNATIVE_TODO_ID)
    assert rc == 0, selected
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    # Bind the synthetic provider after selection so quota has not recalled yet.
    searches = _enable_fixture_memory(project, registry)
    packet = project / "selected.json"
    packet.write_text(json.dumps(selected), encoding="utf-8")
    recall = ("agent-turn-recall", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
              "--turn-instance-id", turn, "--quota-decision-json", str(packet))
    rc, preview = _run_cli(registry, runtime, *recall)
    assert rc == 0, json.dumps(preview)
    assert preview["status"] == "preview" and not searches.exists()
    assert preview["context"]["situation"]["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    rc, applied = _run_cli(registry, runtime, *recall, "--execute")
    assert rc == 0, applied
    assert applied["status"] == "applied" and applied["provider_call_count"] == 1
    receipt = agent_turn_recall_receipt_path(project, goal_id=GOAL_ID, agent_id=AGENT_ID)
    persisted = receipt.read_bytes()
    rc, replay = _run_cli(registry, runtime, *recall, "--execute")
    assert rc == 0 and replay["status"] == "deduplicated", replay
    assert replay["context"] == applied["context"] and replay["provider_call_count"] == 0
    assert receipt.read_bytes() == persisted

    for field in ("goal_id", "agent_identity", "turn", "todo", "effect", "status", "binding"):
        invalid = json.loads(json.dumps(selected))
        if field == "goal_id":
            invalid[field] = "other-goal"
        elif field == "agent_identity":
            invalid[field]["agent_id"] = "other-agent"
        elif field == "turn":
            invalid["heartbeat_receipt"]["turn_instance_id"] = "other-turn"
        elif field == "todo":
            invalid["heartbeat_receipt"]["settlement_identity"]["todo_id"] = "todo_other"
        elif field == "effect":
            invalid["heartbeat_receipt"]["settlement_identity"]["effect_id"] = "unrelated-effect"
        elif field == "status":
            invalid["heartbeat_receipt"]["status"] = "write_failed"
        else:
            del invalid["heartbeat_receipt"]["settlement_identity"]
        packet.write_text(json.dumps(invalid), encoding="utf-8")
        rc, refused = _run_cli(registry, runtime, *recall, "--execute")
        assert rc == 2 and refused["status"] == "invalid_request", refused
        assert refused["provider_call_count"] == 0 and receipt.read_bytes() == persisted

    selected["decision_freshness_warning"] = {"rebase_required_count": 1}
    packet.write_text(json.dumps(selected), encoding="utf-8")
    rc, conflict = _run_cli(registry, runtime, *recall, "--execute")
    assert rc == 0 and conflict["status"] == "not_available", conflict
    assert conflict["provider_call_count"] == 0 and conflict["context"]["guidance"] == []
    assert conflict["context"]["situation"]["conflict_state"] == "unresolved"
    assert searches.read_text().splitlines() == ["search"]
    assert _heartbeat_receipt_count(runtime, turn) == 2
    assert _spend_run_count(runtime) == 0

    data = json.loads(registry.read_text())
    data["goals"][0]["control_plane"]["reward_memory"]["enabled"] = False
    registry.write_text(json.dumps(data), encoding="utf-8")
    rc, disabled = _run_cli(registry, runtime, *recall, "--execute")
    assert rc == 0 and disabled["provider_call_count"] == 0, disabled
    assert disabled["status"] == "disabled"
    assert searches.read_text().splitlines() == ["search"]
