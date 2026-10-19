"""Named source reads must not substitute or acknowledge another inbox page."""

import json
import os
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.control_plane.collaboration.inbox import _root, acknowledge
from loopx.control_plane.collaboration.peers import read_inbox, request, return_result


@pytest.fixture(params=["file", "sqlite"])
def inbox(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, _ = promoted_create_fixture(tmp_path, provider=request.param)
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"] += ["reviewer", "third"]
    registry.write_text(json.dumps(data))
    return registry, root


def brief(purpose):
    return {
        "schema_version": "collaboration_brief_v0",
        "purpose": purpose,
        "context": "\n".join(["完整原始来源：保留约束与结论。"] * 300),
        "constraints": ["Read only; do not launch work or change its owner."],
        "inputs": [],
        "acceptance": ["Inspect the exact original request."],
        "return_requirement": "Keep the original return route.",
    }


def create(inbox, operation, *, source="agent-a", target="reviewer"):
    registry, root = inbox
    content = brief(operation)
    receipt = request(root, registry, "goal-a", source, target, operation, content)
    return receipt["request_id"], content


def conclude(inbox, rid, *, agent="reviewer"):
    registry, root = inbox
    read_inbox(root, registry, "goal-a", agent)
    acknowledge(root, "goal-a", agent, rid, "adopt", "Inspected the original request", registry=registry)
    return_result(root, "goal-a", agent, rid, "The original inspection is complete.", registry=registry)


def cli(inbox, *extra, agent="reviewer"):
    registry, root = inbox
    process = subprocess.run(
        [os.environ.get("LOOPX_TEST_CLI_PYTHON", sys.executable), "-I", "-m", "loopx.cli", "--registry", str(registry),
         "--runtime-root", str(root), "--format", "json", "manager-inbox", "read",
         "--goal-id", "goal-a", "--agent-id", agent, *extra],
        cwd=registry.parent, text=True, capture_output=True, timeout=30,
    )
    return process.returncode, json.loads(process.stdout)


def test_exact_cli_recovers_concluded_source_without_reopening_or_reading_pending(inbox):
    _, root = inbox
    original, content = create(inbox, "original-source")
    conclude(inbox, original)
    other, _ = create(inbox, "unrelated-pending")
    owner = _root(root)
    retained = {p: p.read_bytes() for p in owner.rglob("*.json")
                if "entries" in p.relative_to(owner).parts
                or p.parent.name == "decisions" or p.name == "conclusion.json"}

    code, result = cli(inbox, "--request-id", original)

    assert code == 0, result
    assert [item["request_id"] for item in result["items"]] == [original]
    item = result["items"][0]
    assert item["brief"] == content and item["inbox_state"] == "settled"
    assert "does not reopen" in item["next_action"]
    assert result["has_more"] is False and result["next_cursor"] is None
    assert not (owner / "reads" / f"{other}.json").exists()
    assert {p: p.read_bytes() for p in retained} == retained
    code, pending = cli(inbox)
    assert code == 0 and [item["request_id"] for item in pending["items"]] == [other]


@pytest.mark.parametrize("extra,agent", [
    (["--request-id", "f" * 64], "reviewer"),
    (["--request-id", "../invalid"], "reviewer"),
    (["--request-id", "{original}"], "third"),
    (["--request-id", "{original}", "--cursor", "invalid"], "reviewer"),
    (["--request-id", "{original}", "--operation-cursor", "invalid"], "reviewer"),
])
def test_invalid_or_ambiguous_exact_read_never_falls_back_to_pending(inbox, extra, agent):
    _, root = inbox
    original, _ = create(inbox, "pending-source")
    before = {p: p.read_bytes() for p in _root(root).rglob("*.json")}
    code, result = cli(inbox, *(value.replace("{original}", original) for value in extra), agent=agent)
    assert code == 1 and result["ok"] is False, result
    assert "items" not in result
    assert {p: p.read_bytes() for p in _root(root).rglob("*.json")} == before
    assert not (_root(root) / "reads" / f"{original}.json").exists()


def test_exact_read_does_not_deliver_unrelated_peer_results(inbox):
    _, root = inbox
    outgoing, _ = create(inbox, "awaiting-peer-result")
    conclude(inbox, outgoing)
    incoming, content = create(inbox, "named-incoming", source="third", target="agent-a")
    before = {p: p.read_bytes() for p in _root(root).rglob("*.delivery.json")}

    code, result = cli(inbox, "--request-id", incoming, agent="agent-a")

    assert code == 0, result
    assert [item["request_id"] for item in result["items"]] == [incoming]
    assert result["items"][0]["brief"] == content
    assert "peer_returns" not in result and "operation_handoffs" not in result
    assert {p: p.read_bytes() for p in _root(root).rglob("*.delivery.json")} == before
    code, pending = cli(inbox, agent="agent-a")
    assert code == 0 and pending["peer_returns"]["items"][0]["request_id"] == outgoing


def test_exact_read_keeps_source_goal_lifetime(tmp_path, monkeypatch):
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root = tmp_path / "registry.json", tmp_path / "runtime"
    with source_session_registry_transaction(registry, operation="create_fixture", create=lambda: {
        "profile_id": "source_session_v1",
        "goals": [{"id": "goal-a", "repo": str(tmp_path), "status": "active",
                   "goal_instance_id": "ginst_" + "a" * 32,
                   "coordination": {"registered_agents": ["agent-a", "reviewer"]}}],
        "session_bindings": [], "session_receipts": [], "lifetime_receipts": [],
    }) as transaction:
        transaction.commit(transaction.payload_copy())
    original, content = create((registry, root), "source-lifetime")
    code, observed = cli((registry, root), "--request-id", original)
    assert code == 0 and observed["items"][0]["brief"] == content
    assert observed["items"][0]["goal_ref"] == {
        "goal_id": "goal-a", "goal_instance_id": "ginst_" + "a" * 32,
    }
    with source_session_registry_transaction(registry, operation="replace_fixture") as transaction:
        payload = transaction.payload_copy()
        payload["goals"][0]["goal_instance_id"] = "ginst_" + "b" * 32
        transaction.commit(payload)
    before = {p: p.read_bytes() for p in _root(root).rglob("*.json")}
    code, rejected = cli((registry, root), "--request-id", original)
    assert code == 1 and rejected["ok"] is False and "items" not in rejected
    assert {p: p.read_bytes() for p in _root(root).rglob("*.json")} == before
