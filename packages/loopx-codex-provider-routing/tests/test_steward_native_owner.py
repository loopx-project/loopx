"""Read-only consumers reuse the native owner and exact host profile."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from loopx_codex_provider_routing.native import NativeAccounts, atomic


def test_steward_and_chat_observe_only_the_existing_matching_native_owner(tmp_path):
    now = 1767225600
    home = tmp_path / "codex-home"
    calls = []
    native = {"active_slot": "subscription-a", "selection_revision": "revision-a",
        "current_login_matches": True, "recovery_required": False}
    def transport(adapter, operation):
        calls.append(operation)
        assert adapter["profile"]["home"] == str(home)
        return native
    owner = NativeAccounts(tmp_path / "runtime", transport=transport, clock=lambda: now)
    owner.root.mkdir(parents=True)
    state = owner._load()
    state.update(policy={"enabled": True, "slots": ["subscription-b"], "api_fallback": False},
        adapter={"profile": {"home": str(home)}}, action="ready", accounts=[{
            "slot": "subscription-a", "availability": "available", "observed_at": now}])
    atomic(owner.path, state)
    before = owner.path.read_bytes()
    capacity = {"observed_at": now, "account_ref": "a" * 64, "model_id": "fixture-argon",
        "execution_id": "quota-failure", "remaining_fraction": 0, "reset_time": "2026-01-02T00:00:00Z",
        "quota_exhausted": True, "normal_completion": False}
    args = dict(primary="antigravity", pool=["antigravity", "codex"], current="antigravity", primary_capacity=capacity)
    assert owner.conversation_epoch(codex_home=home) == ("revision-a", "subscription-a")
    assert owner.steward_route(codex_home=home, **args)["endpoint"] == "codex"
    assert owner.conversation_epoch(codex_home=tmp_path / "other-home") is None
    assert owner.steward_route(codex_home=tmp_path / "other-home", **args)["endpoint"] is None
    assert calls == ["status", "status"]
    assert owner.path.read_bytes() == before
    assert sorted(p.name for p in owner.root.iterdir()) == ["state.json"]
    state["policy"]["enabled"] = False
    atomic(owner.path, state)
    assert owner.conversation_epoch(codex_home=home) is None
    assert owner.steward_route(codex_home=home, **args)["endpoint"] is None
    assert calls == ["status", "status"]

    state["policy"]["enabled"] = True
    state["action"] = "handoff_interrupted"
    atomic(owner.path, state)
    assert owner.conversation_epoch(codex_home=home) is None


def test_codex_primary_steward_route_switches_to_antigravity_fallback_when_exhausted(tmp_path):
    now = 1767225600
    home = tmp_path / "codex-home"
    native = {
        "active_slot": "slot-a",
        "selection_revision": "revision-a",
        "current_login_matches": True,
        "recovery_required": False,
        "action": "selection_changed",
    }
    owner = NativeAccounts(
        tmp_path / "runtime",
        transport=lambda _adapter, _op: native,
        clock=lambda: now,
    )
    owner.root.mkdir(parents=True)
    state = owner._load()
    state.update(
        policy={"enabled": True, "slots": ["slot-a", "slot-b"], "api_fallback": False},
        adapter={"profile": {"home": str(home)}},
        action="selection_changed",
        accounts=[
            {"slot": "slot-a", "availability": "exhausted", "observed_at": now - 600, "reset_at": now + 3600},
            {"slot": "slot-b", "availability": "exhausted", "observed_at": now - 600, "reset_at": now + 3600},
        ],
    )
    atomic(owner.path, state)
    fallback = {
        "observed_at": now,
        "account_ref": "b" * 64,
        "model_id": "gemini-4-carbon-high",
        "execution_id": "endpoint-probe:ok",
        "remaining_fraction": 0.99,
        "reset_time": "2026-01-02T00:00:00Z",
        "quota_exhausted": False,
        "normal_completion": True,
    }
    assert owner.steward_route(
        codex_home=home,
        primary="codex",
        pool=["codex", "antigravity"],
        current="codex",
        primary_capacity=None,
        fallback_capacity=fallback,
        model="gpt-6.2-sol",
    )["endpoint"] == "antigravity"
    state.update(action="ready", accounts=[
        {"slot": "slot-a", "availability": "available", "observed_at": now},
        state["accounts"][1],
    ])
    atomic(owner.path, state)
    assert owner.steward_route(
        codex_home=home,
        primary="codex",
        pool=["codex", "antigravity"],
        current="antigravity",
        primary_capacity=None,
        fallback_capacity=fallback,
        model="gpt-6.2-sol",
    )["endpoint"] == "codex"
