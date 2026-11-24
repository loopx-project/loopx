from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime

import pytest

from loopx.capabilities.reward_memory import outbound
from loopx.capabilities.reward_memory.experiment import (
    load_reward_memory_experiment_config,
)
from tests.capabilities import test_agent_turn_recall as turn
from tests.extensions.test_lark_inbox_reactions import _fixture, ReplyRunner
from loopx.extensions.lark.inbox_reply import (
    send_lark_inbox_message,
    reply_lark_event_inbox,
)


def configure(tmp_path, monkeypatch, *, unavailable=False):
    raw = turn.raw_config()
    raw["corpora"][0]["corpus"]["scope"]["surface_ids"] = [outbound.SURFACE]
    raw["surfaces"][0]["surface_id"] = outbound.SURFACE
    path = tmp_path / "memory.json"
    path.write_text(json.dumps(raw))
    config = load_reward_memory_experiment_config(
        project=tmp_path, config_path="memory.json"
    )
    corpus = config["corpora"]["agent_turn_preferences"]["corpus"]
    record = json.dumps(
        {
            "schema_version": "reward_memory_active_record_v0",
            "corpus_id": corpus["corpus_id"],
            "candidate_ref": "candidate:guidance",
            "target_class": "soft_preference",
            "content_summary": "Try safe alternatives before requesting help.",
            "scope": corpus["scope"],
            "lifecycle": {"state": "active"},
        }
    )
    provider = turn.RecallProvider(record, unavailable=unavailable)
    monkeypatch.setattr(
        outbound, "resolve_reward_memory_experiment", lambda **kw: ({}, config)
    )
    from loopx.capabilities.reward_memory import application

    monkeypatch.setattr(application, "build_context_provider", lambda value: provider)
    return config, provider


@pytest.mark.parametrize("purpose", ["help", "progress", "unspecified"])
def test_real_recall_review_is_intent_bound(tmp_path, monkeypatch, purpose):
    _, provider = configure(tmp_path, monkeypatch)
    kwargs = dict(
        registry_path=tmp_path / "registry.json",
        goal_id="goal",
        agent_id="pilot",
        purpose=purpose,
    )
    first = outbound.outbound_guidance_hook(**kwargs)("sha256:first")
    assert first["status"] == "applied"
    assert first["agent_review_required"] and not first["continue_delivery"]
    assert first["application"]["receipt"]["result_readback_verified"]
    reviewed = outbound.outbound_guidance_hook(
        **kwargs, reviewed_digest=first["review_digest"]
    )
    assert reviewed("sha256:first")["continue_delivery"]
    assert not reviewed("sha256:changed")["continue_delivery"]
    assert provider.calls == 3
    assert all("sha256:first" not in query for query in provider.queries)


def test_disabled_urgent_failure_and_wrong_peer(tmp_path, monkeypatch):
    config, _ = configure(tmp_path, monkeypatch)
    kwargs = dict(
        registry_path=tmp_path / "registry.json", goal_id="goal", agent_id="pilot"
    )
    assert outbound.outbound_guidance_hook(**kwargs, purpose="urgent")("intent")[
        "continue_delivery"
    ]
    wrong_peer = outbound.outbound_guidance_hook(**(kwargs | {"agent_id": "other"}))
    assert wrong_peer("intent")["status"] == "configuration_error"
    assert not wrong_peer("intent")["continue_delivery"]
    config["automation"]["automatic_recall"] = False
    assert outbound.outbound_guidance_hook(**kwargs) is None
    configure(tmp_path, monkeypatch, unavailable=True)
    failed = outbound.outbound_guidance_hook(**kwargs)("intent")
    assert failed["status"] == "provider_unavailable"
    assert failed["continue_delivery"] and not failed["provider_failure_is_user_gate"]


def test_unconfigured_surface_preserves_existing_sender(tmp_path, monkeypatch):
    config, _ = configure(tmp_path, monkeypatch)
    del config["surfaces"][outbound.SURFACE]
    assert (
        outbound.outbound_guidance_hook(
            registry_path=tmp_path / "registry.json",
            goal_id="goal",
            agent_id="pilot",
        )
        is None
    )


@pytest.mark.parametrize("reply", [False, True])
def test_sender_stops_before_write_then_preserves_readback(
    tmp_path, monkeypatch, reply
):
    configure(tmp_path, monkeypatch)
    config, _, project = _fixture(tmp_path, lifecycle=False)
    kwargs = dict(
        registry_path=tmp_path / "registry.json",
        goal_id="goal",
        agent_id="pilot",
        purpose="help",
    )
    sender = reply_lark_event_inbox if reply else send_lark_inbox_message
    send_args = dict(project=project, config_path=config, text="done", execute=True)
    if reply:
        send_args["message_id"] = "om_reaction_fixture"
    runner = ReplyRunner(readback_text="done")
    first = sender(
        **send_args,
        runner=runner,
        before_send=outbound.outbound_guidance_hook(**kwargs),
    )
    assert first["status"] == "agent_review_required"
    assert not first["external_write_performed"]
    assert not any(
        ("+messages-send" in c or "+messages-reply" in c) and "--dry-run" not in c
        for c in runner.calls
    )
    hook = outbound.outbound_guidance_hook(
        **kwargs, reviewed_digest=first["outbound_guidance"]["review_digest"]
    )
    sent = sender(**send_args, runner=runner, before_send=hook)
    assert sent["reply_verified"] and sent["external_write_performed"]
    assert not sent["outbound_guidance"]["grants_new_action_authority"]


def test_cli_opaque_turn_uses_real_timestamp(tmp_path, monkeypatch):
    from loopx.capabilities.agent_turn_recall import cli

    config = turn.normalized_config(tmp_path)
    quota = turn.quota_decision() | {
        "mode": "should-run",
        "goal_id": "goal",
        "agent_identity": {"agent_id": "pilot"},
        "heartbeat_receipt": {"turn_instance_id": "opaque-turn", "status": "committed"},
    }
    monkeypatch.setattr(cli, "_goal_repo", lambda *a: tmp_path)
    monkeypatch.setattr(
        cli, "resolve_reward_memory_experiment", lambda **kw: ({}, config)
    )
    monkeypatch.setattr(cli, "_quota_decision", lambda path: quota)
    observed = []

    def run(config, situation, **kw):
        observed.append(datetime.fromisoformat(kw["observed_at"]))
        return {"ok": True, "status": "empty"}

    monkeypatch.setattr(cli, "run_agent_turn_recall", run)
    args = argparse.Namespace(
        command="agent-turn-recall",
        goal_id="goal",
        agent_id="pilot",
        turn_instance_id="opaque-turn",
        quota_decision_json="unused",
        session_ref=None,
        force_refresh=True,
        execute=True,
    )
    assert (
        cli.handle_agent_turn_recall_command(
            args,
            registry_path=tmp_path / "registry.json",
            output_format=lambda *a: "json",
            print_payload=lambda *a: None,
        )
        == 0
    )
    assert len(observed) == 1 and observed[0].tzinfo is not None


@pytest.mark.parametrize("command", ["send", "reply"])
def test_cli_legacy_namespace_preserves_default_off_sender(
    tmp_path, monkeypatch, command
):
    from loopx.cli_commands import lark_inbox as cli

    config, _, project = _fixture(tmp_path, lifecycle=False)
    monkeypatch.setattr(cli, "_inbox_context", lambda *a: (project, config))
    monkeypatch.setattr(cli, "_resolve_lark_activation", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "resolve_routed_lark_inbox_config", lambda **kw: config)
    monkeypatch.setattr(cli, "resolve_routed_lark_inbox_route", lambda **kw: config)
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "status": "preview_ready",
            "external_write_performed": False,
        }

    monkeypatch.setattr(
        cli,
        "reply_lark_event_inbox" if command == "reply" else "send_lark_inbox_message",
        send,
    )
    # Direct callers predating outbound recall do not have the new parser fields.
    args = argparse.Namespace(
        command="lark-inbox",
        lark_inbox_command=command,
        goal_id=None,
        agent_id=None,
        message_id="om_reaction_fixture",
        route_key="example",
        text="done",
        execute=False,
        provider_preflight=True,
    )
    results = []
    assert (
        cli.handle_lark_inbox_command(
            args,
            registry_path=tmp_path / "registry.json",
            runtime_root_arg=None,
            output_format=lambda *a: "json",
            print_payload=lambda payload, *a: results.append(payload),
        )
        == 0
    )
    assert len(calls) == 1
    assert calls[0] == {
        "project": project,
        "config_path": config,
        "text": "done",
        "execute": False,
        "provider_preflight": True,
        "before_send": None,
        **({"message_id": "om_reaction_fixture"} if command == "reply" else {}),
    }
    assert results[0]["status"] == "preview_ready"
    assert results[0]["external_write_performed"] is False


@pytest.mark.parametrize("command", ["send", "reply"])
def test_cli_installs_hook_at_real_sender(tmp_path, monkeypatch, command):
    from loopx.cli_commands import lark_inbox as cli

    configure(tmp_path, monkeypatch)
    config, _, project = _fixture(tmp_path, lifecycle=False)
    monkeypatch.setattr(cli, "_inbox_context", lambda *a: (project, config))
    monkeypatch.setattr(cli, "_resolve_lark_activation", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "resolve_routed_lark_inbox_config", lambda **kw: config)
    monkeypatch.setattr(cli, "resolve_routed_lark_inbox_route", lambda **kw: config)
    runner = ReplyRunner(readback_text="done")
    sender = reply_lark_event_inbox if command == "reply" else send_lark_inbox_message
    monkeypatch.setattr(
        cli,
        "reply_lark_event_inbox" if command == "reply" else "send_lark_inbox_message",
        lambda **kw: sender(**kw, runner=runner),
    )
    args = argparse.Namespace(
        command="lark-inbox",
        lark_inbox_command=command,
        goal_id="goal",
        agent_id="pilot",
        message_id="om_reaction_fixture",
        route_key="example",
        text="done",
        execute=True,
        provider_preflight=False,
        message_purpose="help",
        reviewed_guidance_digest=None,
    )
    results = []
    cli.handle_lark_inbox_command(
        args,
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        output_format=lambda *a: "json",
        print_payload=lambda payload, *a: results.append(payload),
    )
    assert results[0]["status"] == "agent_review_required"
    assert results[0]["external_write_performed"] is False
    assert "not a request for user approval" in cli._render(results[0])


def test_agent_id_normalization_preserves_valid_scope(tmp_path, monkeypatch):
    """Equivalent agent identifiers retain the valid scoped hook."""

    configure(tmp_path, monkeypatch)
    kwargs = dict(registry_path=tmp_path / "registry.json", goal_id="goal")
    for raw_agent_id in ("pilot", "pilot ", "Pilot"):
        hook = outbound.outbound_guidance_hook(
            **kwargs, agent_id=raw_agent_id, purpose="progress"
        )
        assert callable(hook), (
            f"agent_id={raw_agent_id!r} must normalize to the configured scope"
        )
        assert hook("sha256:intent")["status"] == "applied"


def test_destination_match_is_observable_when_digest_drifts(tmp_path, monkeypatch):
    config, provider = configure(tmp_path, monkeypatch)
    chat = "oc_example_group"
    digest = hashlib.sha256(chat.encode()).hexdigest()
    config["surfaces"][outbound.SURFACE]["destinations"] = {
        digest: {
            "query_label": "Example Team",
            "required_candidate_refs": ["candidate:rule"],
        }
    }
    hook = outbound.outbound_guidance_hook(
        registry_path=tmp_path / "registry.json", goal_id="goal", agent_id="pilot"
    )
    # The bound chat resolves its configured destination.
    matched = hook.for_destination(chat)("intent")
    assert matched["destination_configured"] is True
    # A digest that matches no configured destination (for example, the
    # display name was hashed instead of the chat id) must not masquerade as
    # a satisfied required-read check.
    drifted = hook.for_destination("oc_other_group")("intent")
    assert drifted["destination_configured"] is False
    assert drifted["required_guidance_complete"] is True
    assert drifted["missing_required_candidate_refs"] == []


def test_destination_and_purpose_queries_merge_without_private_id(
    tmp_path, monkeypatch
):
    config, provider = configure(tmp_path, monkeypatch)
    chat = "oc_example_group"
    digest = hashlib.sha256(chat.encode()).hexdigest()
    config["surfaces"][outbound.SURFACE]["destinations"] = {
        digest: {"query_label": "Example Team", "required_candidate_refs": []}
    }
    hook = outbound.outbound_guidance_hook(
        registry_path=tmp_path / "registry.json", goal_id="goal", agent_id="pilot"
    )
    result = hook.for_destination(chat)("intent")
    assert provider.calls == 2
    assert "Example Team" in provider.queries[0]
    assert digest in provider.queries[0]
    assert all(chat not in q for q in provider.queries)
    assert len(result["guidance"]) == 1
    assert result["telemetry"]["provider_call_count"] == 2
    other = hook.for_destination("oc_other_group")("intent")
    assert "Example Team" not in provider.queries[2]
    assert other["review_digest"] != result["review_digest"]


@pytest.mark.parametrize("unavailable", [False, True])
def test_required_guidance_missing_blocks_urgent_and_review_override(
    tmp_path, monkeypatch, unavailable
):
    config, _ = configure(tmp_path, monkeypatch, unavailable=unavailable)
    chat = "oc_example_group"
    config["surfaces"][outbound.SURFACE]["destinations"] = {
        hashlib.sha256(chat.encode()).hexdigest(): {
            "query_label": "Example Team",
            "required_candidate_refs": ["candidate:missing"],
        }
    }
    kwargs = dict(
        registry_path=tmp_path / "registry.json",
        goal_id="goal",
        agent_id="pilot",
        purpose="urgent",
    )
    first = outbound.outbound_guidance_hook(**kwargs).for_destination(chat)("intent")
    assert first["status"] == "required_guidance_missing"
    assert not first["continue_delivery"]
    again = outbound.outbound_guidance_hook(
        **kwargs, reviewed_digest=first["review_digest"]
    ).for_destination(chat)("intent")
    assert not again["continue_delivery"]
    # A restriction on one destination cannot silently constrain another.
    assert outbound.outbound_guidance_hook(**kwargs).for_destination("oc_other")(
        "intent"
    )["continue_delivery"]


def test_required_guidance_has_separate_lookup_and_review(tmp_path, monkeypatch):
    config, provider = configure(tmp_path, monkeypatch)
    chat = "oc_example_group"
    config["surfaces"][outbound.SURFACE]["destinations"] = {
        hashlib.sha256(chat.encode()).hexdigest(): {
            "query_label": "Example Team",
            "required_candidate_refs": ["candidate:guidance"],
        }
    }
    hook = outbound.outbound_guidance_hook(
        registry_path=tmp_path / "registry.json",
        goal_id="goal",
        agent_id="pilot",
        purpose="urgent",
    )
    result = hook.for_destination(chat)("intent")
    assert provider.queries[0] == "candidate:guidance"
    assert result["required_guidance_complete"]
    assert result["agent_review_required"]
    assert len(result["guidance"]) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"destination_digest": "group-name"},
        {
            "destination_digest": "a" * 64,
            "required_candidate_refs": ["not-a-candidate"],
        },
        {
            "destination_digest": "a" * 64,
            "required_candidate_refs": ["candidate:x"] * 2,
        },
        # Refs outside the application token charset can never match an
        # emitted candidate_ref, so requiring them would permanently block
        # the destination.
        {
            "destination_digest": "a" * 64,
            "required_candidate_refs": ["candidate:中文规则"],
        },
        {
            "destination_digest": "a" * 64,
            "required_candidate_refs": ["candidate:a$b"],
        },
    ],
)
def test_destination_config_rejects_ambiguous_identity(tmp_path, invalid):
    raw = turn.raw_config()
    raw["corpora"][0]["corpus"]["scope"]["surface_ids"] = [outbound.SURFACE]
    raw["surfaces"][0]["surface_id"] = outbound.SURFACE
    raw["surfaces"][0]["destinations"] = [invalid]
    (tmp_path / "memory.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_reward_memory_experiment_config(
            project=tmp_path, config_path="memory.json"
        )


def test_destination_config_roundtrip(tmp_path):
    raw = turn.raw_config()
    raw["corpora"][0]["corpus"]["scope"]["surface_ids"] = [outbound.SURFACE]
    raw["surfaces"][0]["surface_id"] = outbound.SURFACE
    raw["surfaces"][0]["destinations"] = [
        {
            "destination_digest": "a" * 64,
            "query_label": "Example Team",
            "required_candidate_refs": ["candidate:rule"],
        }
    ]
    (tmp_path / "memory.json").write_text(json.dumps(raw))
    config = load_reward_memory_experiment_config(
        project=tmp_path, config_path="memory.json"
    )
    assert config["surfaces"][outbound.SURFACE]["destinations"]["a" * 64] == {
        "query_label": "Example Team",
        "required_candidate_refs": ["candidate:rule"],
    }


@pytest.mark.parametrize("reply", [False, True])
@pytest.mark.parametrize("purpose", ["help", "urgent"])
@pytest.mark.parametrize(
    "state",
    [
        "valid",
        "invalid",
        "missing",
        "disabled",
        "agent_off",
        "recall_off",
        "surface_off",
        "scope_mismatch",
    ],
)
def test_real_resolver_sender_configuration_boundary(
    tmp_path, monkeypatch, reply, purpose, state
):
    from loopx.capabilities.reward_memory import application

    raw = turn.raw_config()
    raw["corpora"][0]["corpus"]["scope"]["surface_ids"] = [outbound.SURFACE]
    raw["surfaces"][0]["surface_id"] = outbound.SURFACE
    raw["surfaces"][0]["destinations"] = [
        {
            "destination_digest": hashlib.sha256(b"oc_project_review").hexdigest(),
            "query_label": "x" * 121 if state == "invalid" else "Example Team",
            "required_candidate_refs": ["candidate:missing"],
        }
    ]
    if state == "recall_off":
        raw["automation"]["automatic_recall"] = False
    if state == "surface_off":
        raw = turn.raw_config()
    if state == "scope_mismatch":
        raw["corpora"][0]["corpus"]["scope"]["peer_ref"] = "agent:other"
    memory_path = tmp_path / "memory.json"
    memory_path.write_text(json.dumps(raw))
    config_digest = f"sha256:{hashlib.sha256(memory_path.read_bytes()).hexdigest()}"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["pilot"]},
                        "control_plane": {
                            "reward_memory": {
                                "enabled": state != "disabled",
                                "experimental": True,
                                "enabled_agents": []
                                if state == "agent_off"
                                else ["pilot"],
                                "config_path": None
                                if state == "missing"
                                else "memory.json",
                                "config_digest": config_digest,
                                "enablement_receipts": {
                                    "pilot": {
                                        "schema_version": (
                                            "reward_memory_enablement_receipt_v0"
                                        ),
                                        "status": "verified",
                                        "goal_id": "goal",
                                        "agent_id": "pilot",
                                        "config_digest": config_digest,
                                        "provider_id": "openviking",
                                        "isolation_mode": ("goal_scoped_agent_private"),
                                        "actor_binding_verified": True,
                                        "writability_verified": True,
                                        "exact_readback_verified": True,
                                    }
                                },
                            }
                        },
                    }
                ]
            }
        )
    )
    provider = turn.RecallProvider("{}")
    monkeypatch.setattr(application, "build_context_provider", lambda value: provider)
    config, _, project = _fixture(tmp_path, lifecycle=False)
    sender = reply_lark_event_inbox if reply else send_lark_inbox_message
    runner = ReplyRunner(readback_text="done")
    hook = outbound.outbound_guidance_hook(
        registry_path=registry,
        goal_id="goal",
        agent_id="pilot",
        purpose=purpose,
        reviewed_digest="sha256:previously-reviewed",
    )
    result = sender(
        project=project,
        config_path=config,
        text="done",
        execute=True,
        runner=runner,
        before_send=hook,
        **({"message_id": "om_reaction_fixture"} if reply else {}),
    )
    disabled = state in {"disabled", "agent_off", "recall_off", "surface_off"}
    assert result["external_write_performed"] is disabled
    if disabled:
        assert result["reply_verified"] and "outbound_guidance" not in result
        assert provider.calls == 0
    else:
        guidance = result["outbound_guidance"]
        assert guidance["status"] == (
            "required_guidance_missing" if state == "valid" else "configuration_error"
        )
        assert not guidance["continue_delivery"]
        assert not any(
            ("+messages-send" in c or "+messages-reply" in c) and "--dry-run" not in c
            for c in runner.calls
        )
        if state != "valid":
            assert provider.calls == 0
