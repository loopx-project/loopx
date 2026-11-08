"""Native policy/store transaction integration; no installed login or model IO."""
import importlib
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native policy uses POSIX locks")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "packages/loopx-codex-provider-routing/src"))
    native = importlib.import_module("loopx_codex_provider_routing.native")
    relay = importlib.import_module("loopx_codex_provider_routing.openai_relay")
    monkeypatch.setattr(native, "discover", lambda: {"synthetic": True})
    state = {"active": "a", "revision": "initial", "api_slot": None, "supported": True,
             "now": 1000, "b_used": 100, "b_status": "ok", "calls": [], "relay_starts": 0}
    def ensure(_owner):
        state["relay_starts"] += 1
        return {"slot": "openai-api", "api_key": "synthetic-loopback-token", "base_url": "http://127.0.0.1:32100/v1"}
    monkeypatch.setattr(relay, "ensure", ensure)
    def transport(adapter, operation, **parameters):
        state["calls"].append(operation)
        if operation == "status":
            return {"slots": ["a", "b"], "active_slot": state["active"], "selection_revision": state["revision"],
                    "current_login_matches": True, "recovery_required": False,
                    "api_slot": state["api_slot"], "api_fallback_supported": state["supported"]}
        if operation == "enroll_api":
            assert parameters["slot"] == "openai-api"
            state["api_slot"] = "openai-api"
            return transport(adapter, "status")
        if operation == "observe":
            assert "openai-api" not in parameters["slots"], "API keys never enter the subscription quota reader"
            return {"observations": [{"slot": slot, "observed_at": state["now"],
                    "status": state["b_status"] if slot == "b" else "ok", "usage": {
                        "rate_limit": {"primary_window": {"used_percent": 100 if slot == "a" else state["b_used"], "reset_at": 9000}},
                        "credits": {"has_credits": False}}} for slot in parameters["slots"]]}
        if operation == "switch":
            assert parameters["expected_revision"] == state["revision"]
            state["active"] = parameters["slot"]
            state["revision"] += "-switched"
            return transport(adapter, "status") | {"launch_failed": False}
        raise AssertionError(operation)
    provider = native.NativeAccounts(tmp_path, transport=transport, clock=lambda: state["now"])
    provider.api_keys().add(expected_revision=0, label="Fallback", api_key="sk-" + "synthetic-key-" * 3)
    return provider, state


def test_automatic_api_handoff_then_recovered_subscription_adoption(setup):
    provider, state = setup
    provider.configure(expected_revision=0, enabled=True, slots=["a", "b"], api_fallback=True)
    assert state["active"] == "a", "Enabling fallback never switches by itself"
    result = provider.tick(refresh=True)
    assert result["active_slot"] == "openai-api" and result["api_active"] is True
    assert result["policy"]["slots"] == ["a", "b"], "The paid route is a separate explicit permission"
    state.update(now=1301, b_used=10)
    result = provider.tick(refresh=True)
    assert result["active_slot"] == "b" and result["api_active"] is False
    assert state["calls"].count("switch") == 2


def test_exhausted_api_pool_returns_to_a_recovered_subscription(setup):
    provider, state = setup
    provider.configure(expected_revision=0, enabled=True, slots=["a", "b"], api_fallback=True)
    assert provider.tick(refresh=True)["active_slot"] == "openai-api"

    key = provider.api_keys().status()["keys"][0]
    provider.api_keys().record(key["id"], status=429, code="insufficient_quota",
                               model="selected-model", headers={"retry-after": "3600"})
    state["now"] = 1301  # Past the account-switch cooldown but not the key cooldown.
    blocked = provider.tick(refresh=True)
    assert blocked["active_slot"] == "openai-api"
    assert blocked["action"] == "all_unavailable"
    assert state["calls"].count("switch") == 1, "No fallback may claim readiness while every key is exhausted"

    state["b_used"] = 10
    recovered = provider.tick(refresh=True)
    assert recovered["active_slot"] == "b" and recovered["api_active"] is False
    assert state["calls"].count("switch") == 2


def test_available_or_unknown_subscription_never_starts_a_paid_handoff(setup):
    provider, state = setup
    provider.configure(expected_revision=0, enabled=True, slots=["a", "b"], api_fallback=True)
    for status, used in [("ok", 50), ("unavailable", 100)]:
        state.update(b_status=status, b_used=used)
        if status == "ok":
            # Available B must win over API.
            assert provider.tick(refresh=True)["active_slot"] == "b"
        else:
            # Simulate an external native return; preserve its selection fence.
            state.update(active="a", revision="manual-return")
            provider.tick(refresh=True)
            state["now"] += 301
            assert provider.tick(refresh=True)["active_slot"] == "a"
    assert state["calls"].count("switch") == 1


def test_saved_keys_do_not_opt_existing_subscription_policies_into_paid_usage(setup):
    provider, state = setup
    provider.configure(expected_revision=0, enabled=True, slots=["a", "b"])
    assert provider.tick(refresh=True)["action"] == "all_unavailable"
    assert state["relay_starts"] == 0 and "enroll_api" not in state["calls"] and "switch" not in state["calls"]


def test_old_owner_cannot_be_bypassed_by_fallback_configuration(setup):
    provider, state = setup
    state["supported"] = False
    with pytest.raises(Exception, match="api_owner_upgrade_required"):
        provider.configure(expected_revision=0, enabled=True, slots=["a", "b"], api_fallback=True)
    assert not provider.path.exists() and state["relay_starts"] == 0
