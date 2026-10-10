from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.quota.live_decision import bind_scheduler_followup_cli_routes
from loopx.control_plane.runtime.public_safety import SECRET_LIKE_SURFACE_PATTERN
from loopx.control_plane.testing.model_behavior_qualification import (
    MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
    MODEL_BEHAVIOR_ARM_TERMINAL_RECEIPT_SCHEMA_VERSION,
    MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
    ModelBehaviorArmExecutionError,
    build_model_behavior_actor_request,
    compare_model_behavior_receipts,
    model_behavior_semantic_contract_from_packet,
    normalize_model_behavior_actor_request,
    run_model_behavior_qualification_arm,
    run_model_behavior_qualification_pair,
)


def _full_packet() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "should-run",
        "goal_id": "fixture-goal",
        "decision": "run",
        "should_run": True,
        "effective_action": "normal_run",
        "state": "eligible",
        "action_required": False,
        "open_count": 0,
        "recommended_action": "Implement one bounded public-safe slice.",
        "selected_todo": {
            "todo_id": "todo_fixture001",
            "status": "open",
            "task_class": "advancement_task",
            "claimed_by": "codex-fixture",
            "text": "Implement one bounded public-safe slice.",
        },
        "agent_identity": {"agent_id": "codex-fixture"},
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "primary_action": "Implement one bounded public-safe slice.",
            },
            "cli_channel": {
                "next_cli_actions": ["loopx refresh-state --goal-id fixture-goal"],
                "spend_allowed_now": False,
                "spend_after_validation": True,
            },
        },
        "goal_boundary": {
            "write_scope": ["loopx/**", "tests/**"],
            "guards": ["stop before external writes"],
        },
    }


def _decision(**patch: Any) -> dict[str, Any]:
    decision = {
        "schema_version": MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
        "decision": "execute",
        "selected_todo_id": "todo_fixture001",
        "user_action_required": False,
        "must_attempt_work": True,
        "delivery_allowed": True,
        "quiet_noop_allowed": False,
        "external_write_requested": False,
        "intended_action_kinds": [
            "inspect",
            "edit",
            "test",
            "writeback",
            "spend",
        ],
        "reason_codes": ["bounded_delivery"],
    }
    decision.update(patch)
    return decision


def _actor(request: Mapping[str, Any]) -> dict[str, Any]:
    assert request["sandbox"] == {
        "schema_version": "model_behavior_no_write_sandbox_v0",
        "tools_enabled": False,
        "filesystem_writes_allowed": False,
        "external_writes_allowed": False,
        "provider_network_only": True,
    }
    return {
        "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
        "actor_ref": "fixture-model-v1",
        "decision": _decision(),
        "tool_calls": [],
    }


def test_actor_request_accepts_only_known_public_safe_packet_shapes() -> None:
    full = build_model_behavior_actor_request(
        _full_packet(),
        qualification_id="case-normal-run-001",
        arm="full_packet",
    )
    candidate = build_model_behavior_actor_request(
        build_turn_envelope(_full_packet()),
        qualification_id="case-normal-run-001",
        arm="candidate_packet",
    )

    assert full["packet_schema_version"] == "loopx_quota_should_run_full_v0"
    assert candidate["packet_schema_version"] == "loopx_turn_envelope_v0"
    assert full["actor_instruction"]["source_of_truth"] == "packet"
    assert full["response_contract"]["reject_unknown_fields"] is True

    with pytest.raises(ValueError, match="quota should-run decision"):
        build_model_behavior_actor_request(
            {"goal_id": "fixture-goal"},
            qualification_id="case-normal-run-001",
            arm="full_packet",
        )
    with pytest.raises(ValueError, match="TurnEnvelope schema"):
        build_model_behavior_actor_request(
            {"schema_version": "future_envelope_v9"},
            qualification_id="case-normal-run-001",
            arm="candidate_packet",
        )


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"api_key": "not-even-a-real-key"}, "credential-shaped field"),
        (
            {"note": "".join(("/", "Users", "/example/private.txt"))},
            "local absolute path",
        ),
        (
            {"note": "".join(("token", "=", "abcdefghijklmnop"))},
            "credential-like value",
        ),
    ],
)
def test_actor_request_rejects_private_or_secret_material(
    patch: dict[str, Any], message: str
) -> None:
    packet = _full_packet()
    packet.update(patch)

    with pytest.raises(ValueError, match=message):
        build_model_behavior_actor_request(
            packet,
            qualification_id="case-boundary-001",
            arm="full_packet",
        )


_HOST_FACTS_FLAG = "--scheduler-host-facts-chunk"


def _scheduler_wire(operation: str = "ack") -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures/control_plane/model_behavior_scheduler_transport.json"
    return bytes.fromhex(json.loads(fixture.read_text())[operation]["zlib_hex"])


def _scheduler_transport_packet(
    arm: str, operation: str, *, inline: bool = False, compressed: bytes | None = None,
    alphabet: str = "url", bound: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    if compressed is None:
        # Frozen public synthetic wire bytes keep the collision independent of
        # the platform's zlib encoder. Hex storage is not a secret-shaped value.
        compressed = _scheduler_wire(operation)
    encoder = base64.b64encode if alphabet == "standard" else base64.urlsafe_b64encode
    encoded = encoder(compressed).decode().rstrip("=")
    command = "scheduler-ack-current" if operation == "ack" else "scheduler-fail-current"
    args = ["quota", command, "--goal-id", "goal-native-followup", "--agent-id", "agent-native-followup"]
    for offset in range(0, len(encoded), 384):
        chunk = encoded[offset:offset + 384]
        args.extend([f"{_HOST_FACTS_FLAG}={chunk}"] if inline else [_HOST_FACTS_FLAG, chunk])
    packet = _full_packet()
    kind = "ack" if operation == "ack" else "failure"
    packet["scheduler_hint"] = {"schema_version": "scheduler_hint_v0", "app_automation": {
        f"{kind}_hint": {"schema_version": f"app_automation_scheduler_{kind}_hint_v0", "cli_args": args},
    }}
    if bound:
        bind_scheduler_followup_cli_routes(packet, registry_path=Path("/fixture/registry.json"),
            runtime_root=Path("/fixture/runtime"), turn_instance_id="public-fixture-turn")
    if arm == "candidate_packet":
        packet = build_turn_envelope(packet)
        args = packet["scheduler"]["app_automation"]["ack_cli_args"]
    else:
        args = packet["scheduler_hint"]["app_automation"][f"{kind}_hint"]["cli_args"]
    return packet, args


@pytest.mark.parametrize("arm, operation", [
    ("full_packet", "ack"), ("full_packet", "host_failure"), ("candidate_packet", "ack"),
])
@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("alphabet", ["standard", "url"])
@pytest.mark.parametrize("bound", [False, True])
def test_actor_request_scans_decoded_scheduler_facts_without_changing_wire(
    arm: str, operation: str, inline: bool, alphabet: str, bound: bool,
) -> None:
    packet, args = _scheduler_transport_packet(arm, operation, inline=inline, alphabet=alphabet, bound=bound)
    if alphabet == "url":
        assert any(SECRET_LIKE_SURFACE_PATTERN.search(arg) for arg in args)
    before = json.dumps(packet, sort_keys=True)

    request = build_model_behavior_actor_request(packet, qualification_id="public-wire-collision", arm=arm)

    assert json.dumps(request["packet"], sort_keys=True) == before
    assert json.dumps(packet, sort_keys=True) == before
    assert normalize_model_behavior_actor_request(request) == request


@pytest.mark.parametrize("arm, operation", [
    ("full_packet", "ack"), ("full_packet", "host_failure"), ("candidate_packet", "ack"),
])
@pytest.mark.parametrize("location", ["before", "host_facts", "extension"])
@pytest.mark.parametrize("alphabet", ["standard", "url"])
@pytest.mark.parametrize("bound", [False, True])
def test_actor_rejects_private_material_inside_encoded_scheduler_facts(
    arm: str, operation: str, location: str, alphabet: str, bound: bool,
) -> None:
    payload = json.loads(zlib.decompress(_scheduler_wire(operation)))
    if location == "before":
        payload[location]["api_key"] = "synthetic-private-value"
    elif location == "host_facts":
        payload[location]["note"] = "/" + "Users/example/private.txt"
    else:
        payload[location] = [{"nested": ["token" + "=abcdefghijklmnop"]}]
    packet, _ = _scheduler_transport_packet(arm, operation, inline=True, alphabet=alphabet, bound=bound,
        compressed=zlib.compress(json.dumps(payload).encode()))
    before = json.dumps(packet, sort_keys=True)
    with pytest.raises(ValueError, match="credential-shaped field|local absolute path|credential-like value"):
        build_model_behavior_actor_request(packet, qualification_id="encoded-private", arm=arm)
    assert json.dumps(packet, sort_keys=True) == before


@pytest.mark.parametrize("case", [
    "bad_json", "utf8", "root_array", "duplicate_key", "nonfinite", "hint_schema", "facts_schema",
    "before_shape", "current_hint_shape", "truncated", "trailing", "concatenated", "bomb", "encoded_limit",
])
@pytest.mark.parametrize("alphabet", ["standard", "url"])
@pytest.mark.parametrize("bound", [False, True])
def test_actor_rejects_malformed_or_unbounded_scheduler_wire(case: str, alphabet: str, bound: bool) -> None:
    payload = json.loads(zlib.decompress(_scheduler_wire()))
    if case == "hint_schema":
        payload["schema_version"] = "future_hint"
    elif case == "facts_schema":
        payload["host_facts"]["schema_version"] = "future_facts"
    elif case == "before_shape":
        payload["before"] = []
    elif case == "current_hint_shape":
        payload["use_current_hint"] = "true"
    elif case == "nonfinite":
        payload["extension"] = float("nan")
    elif case == "bomb":
        payload["extension"] = "a" * 16_385
    raw = json.dumps(payload).encode()
    if case == "bad_json":
        raw = b"not-json"
    elif case == "utf8":
        raw = b"\xff"
    elif case == "root_array":
        raw = b"[]"
    elif case == "duplicate_key":
        raw = raw[:-1] + b', "before": {}}'
    compressed = zlib.compress(raw)
    if case == "truncated":
        compressed = compressed[:-1]
    elif case == "trailing":
        compressed += b"hidden tail"
    elif case == "concatenated":
        compressed += zlib.compress(b"{}")
    elif case == "encoded_limit":
        compressed = b"x" * 3_073
    packet, _ = _scheduler_transport_packet("full_packet", "ack", compressed=compressed, alphabet=alphabet, bound=bound)
    with pytest.raises(ValueError, match="scheduler host facts"):
        build_model_behavior_actor_request(packet, qualification_id="invalid-wire", arm="full_packet")


@pytest.mark.parametrize("case", [
    "hint_schema", "scheduler_schema", "command", "missing", "empty_inline", "alphabet", "mixed_alphabet", "padding", "pad_bits",
])
def test_actor_rejects_unrecognized_or_malformed_scheduler_arguments(case: str) -> None:
    packet, args = _scheduler_transport_packet("full_packet", "ack")
    if case == "hint_schema":
        packet["scheduler_hint"]["app_automation"]["ack_hint"]["schema_version"] = "future_hint"
    elif case == "scheduler_schema":
        packet["scheduler_hint"]["schema_version"] = "future_scheduler"
    elif case == "command":
        args[1] = "scheduler-fail-current"
    elif case == "missing":
        args.append(_HOST_FACTS_FLAG)
    elif case == "empty_inline":
        args.append(_HOST_FACTS_FLAG + "=")
    elif case == "alphabet":
        args[7] = "*invalid"
    elif case == "mixed_alphabet":
        encoded = "".join(args[7::2]).replace("-", "+", 1)
        assert "+" in encoded and "-" in encoded
        args[6:] = [_HOST_FACTS_FLAG, encoded]
    elif case == "padding":
        args[-1] += "="
    else:
        # One byte canonically encodes as eA; eB has nonzero unused pad bits.
        args[6:] = [_HOST_FACTS_FLAG, "eB"]
    with pytest.raises(ValueError, match="scheduler host facts"):
        build_model_behavior_actor_request(packet, qualification_id="invalid-args", arm="full_packet")


@pytest.mark.parametrize("case", ["ordinary_arg", "unrelated", "dotted_key", "alias"])
@pytest.mark.parametrize("bound", [False, True])
def test_actor_does_not_exempt_untyped_fields_or_other_arguments(case: str, bound: bool) -> None:
    packet, args = _scheduler_transport_packet("full_packet", "ack", bound=bound)
    if case == "ordinary_arg":
        args.extend(["--reason-summary", "token" + "=abcdefghijklmnop"])
    elif case == "unrelated":
        packet["diagnostic"] = {"cli_args": args.copy()}
    elif case == "dotted_key":
        packet["scheduler_hint.codex_app.ack_hint.cli_args"] = args.copy()
    else:
        packet["diagnostic"] = args  # Same object as the valid typed argv.
    with pytest.raises(ValueError, match="credential-like value"):
        build_model_behavior_actor_request(packet, qualification_id="untyped-wire", arm="full_packet")


@pytest.mark.parametrize("prefix", [
    ["--registry", "public-registry", "--runtime-root", "public-runtime"],
    ["--runtime-root=public-runtime", "--registry=public-registry"],
    ["--registry=public-registry"],
])
def test_actor_accepts_explicit_route_options_from_the_cli_grammar(prefix: list[str]) -> None:
    from loopx.cli import build_parser

    packet, args = _scheduler_transport_packet("full_packet", "ack", alphabet="standard")
    args[:0] = prefix
    parsed = build_parser().parse_args(args)
    assert parsed.command == "quota" and parsed.quota_command == "scheduler-ack-current"
    assert build_model_behavior_actor_request(packet, qualification_id="route-grammar", arm="full_packet")["packet"] == packet


@pytest.mark.parametrize("case", [
    "unknown", "command_as_value", "missing", "nonstring", "empty", "duplicate", "abbreviation", "command", "schema",
])
def test_actor_rejects_forged_bound_scheduler_routes(case: str) -> None:
    packet, args = _scheduler_transport_packet("full_packet", "ack", bound=True)
    if case == "unknown":
        args[:0] = ["--future-route", "public-value"]
    elif case == "command_as_value":
        args[:0] = ["--future-route", "quota", "scheduler-ack-current"]
    elif case == "missing":
        del args[1]
    elif case == "nonstring":
        args[1] = {"route": "public-registry"}
    elif case == "empty":
        args[1] = ""
    elif case == "duplicate":
        args[:0] = args[:2]
    elif case == "abbreviation":
        args[0] = "--reg"
    elif case == "command":
        args[5] = "scheduler-fail-current"
    else:
        packet["scheduler_hint"]["app_automation"]["ack_hint"]["schema_version"] = "future_hint"
    before = json.dumps(packet, sort_keys=True)
    with pytest.raises(ValueError, match="matching typed hint and command"):
        build_model_behavior_actor_request(packet, qualification_id="forged-route", arm="full_packet")
    assert json.dumps(packet, sort_keys=True) == before


@pytest.mark.parametrize("arm, operation", [
    ("full_packet", "ack"), ("full_packet", "host_failure"), ("candidate_packet", "ack"),
])
@pytest.mark.parametrize("route_value, message", [
    ("/" + "Users/example/private.json", "local absolute path"),
    ("token" + "=abcdefghijklmnop", "credential-like value"),
])
def test_actor_scans_bound_route_values(arm: str, operation: str, route_value: str, message: str) -> None:
    packet, args = _scheduler_transport_packet(arm, operation, alphabet="standard", bound=True)
    args[1] = route_value
    with pytest.raises(ValueError, match=message):
        build_model_behavior_actor_request(packet, qualification_id="private-route", arm=arm)


def test_actor_normalization_rescans_tampered_encoded_material() -> None:
    packet, _ = _scheduler_transport_packet("candidate_packet", "ack")
    request = build_model_behavior_actor_request(packet, qualification_id="tampered-wire", arm="candidate_packet")
    payload = json.loads(zlib.decompress(_scheduler_wire()))
    payload["extension"] = {"password": "synthetic-private-value"}
    tainted, _ = _scheduler_transport_packet("candidate_packet", "ack",
        compressed=zlib.compress(json.dumps(payload).encode()))
    request["packet"] = tainted
    with pytest.raises(ValueError, match="credential-shaped field"):
        normalize_model_behavior_actor_request(request)


def test_actor_accepts_unencoded_legacy_hint_and_inflated_limit() -> None:
    packet, args = _scheduler_transport_packet("full_packet", "ack")
    args[6:] = ["--reset-token", "public-reset"]
    assert build_model_behavior_actor_request(packet, qualification_id="legacy-hint", arm="full_packet")["packet"] == packet
    payload = json.loads(zlib.decompress(_scheduler_wire()))
    payload["padding"] = ""
    payload["padding"] = "a" * (16_384 - len(json.dumps(payload).encode()))
    raw = json.dumps(payload).encode()
    assert len(raw) == 16_384
    packet, _ = _scheduler_transport_packet("full_packet", "ack", compressed=zlib.compress(raw))
    assert build_model_behavior_actor_request(packet, qualification_id="at-limit", arm="full_packet")["packet"] == packet


def test_qualification_receipt_is_compact_and_drops_raw_conversation() -> None:
    receipt = run_model_behavior_qualification_arm(
        _full_packet(),
        qualification_id="case-normal-run-001",
        arm="full_packet",
        actor=_actor,
    )

    assert receipt["decision"] == "execute"
    assert receipt["boundary"]["tool_call_count"] == 0
    assert receipt["boundary"]["raw_packet_persisted"] is False
    assert receipt["boundary"]["raw_model_response_persisted"] is False
    encoded = json.dumps(receipt, sort_keys=True)
    assert "Implement one bounded" not in encoded
    assert "packet" not in receipt
    assert "response" not in receipt


def test_actor_result_fails_closed_on_tools_and_unknown_fields() -> None:
    def tool_calling_actor(_: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(),
            "tool_calls": [{"name": "shell"}],
        }

    with pytest.raises(ValueError, match="forbids all tool calls"):
        run_model_behavior_qualification_arm(
            _full_packet(),
            qualification_id="case-tools-001",
            arm="full_packet",
            actor=tool_calling_actor,
        )

    def wide_actor(_: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": {**_decision(), "analysis": "raw reasoning"},
            "tool_calls": [],
        }

    with pytest.raises(ValueError, match="unknown model decision field"):
        run_model_behavior_qualification_arm(
            _full_packet(),
            qualification_id="case-wide-001",
            arm="full_packet",
            actor=wide_actor,
        )


def test_paired_run_reports_zero_drift_without_retaining_arm_receipts() -> None:
    full = _full_packet()
    result = run_model_behavior_qualification_pair(
        full,
        build_turn_envelope(full),
        qualification_id="case-normal-run-001",
        actor=_actor,
    )

    assert result["equivalent"] is True
    assert result["hard_invariant_drift"] == {}
    assert result["behavior_signal_drift"] == {}
    assert result["safety_violations"] == []
    assert set(result["receipt_digests"]) == {"full_packet", "candidate_packet"}
    assert "receipt" not in result


def test_paired_run_attributes_actor_failure_to_one_terminal_arm_receipt() -> None:
    class ProviderTimeout(RuntimeError):
        error_code = "provider_timeout"

    def actor(request: Mapping[str, Any]) -> dict[str, Any]:
        if request["arm"] == "candidate_packet":
            raise ProviderTimeout("private provider detail")
        return _actor(request)

    full = _full_packet()
    with pytest.raises(ModelBehaviorArmExecutionError) as exc_info:
        run_model_behavior_qualification_pair(
            full,
            build_turn_envelope(full),
            qualification_id="case-arm-timeout-001",
            actor=actor,
            arm_order=("full_packet", "candidate_packet"),
        )

    receipt = exc_info.value.receipt
    assert receipt["schema_version"] == (
        MODEL_BEHAVIOR_ARM_TERMINAL_RECEIPT_SCHEMA_VERSION
    )
    assert receipt["arm"] == "candidate_packet"
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "provider_timeout"
    assert set(receipt["completed_arm_receipt_digests"]) == {"full_packet"}
    assert receipt["boundary"]["raw_packet_persisted"] is False
    assert receipt["boundary"]["raw_model_response_persisted"] is False
    assert "private provider detail" not in json.dumps(receipt, sort_keys=True)


def test_paired_run_rejects_unrelated_or_unverified_candidate() -> None:
    full = _full_packet()
    unrelated_source = _full_packet()
    unrelated_source["recommended_action"] = "Wait for unrelated evidence."

    with pytest.raises(ValueError, match="does not derive"):
        run_model_behavior_qualification_pair(
            full,
            build_turn_envelope(unrelated_source),
            qualification_id="case-lineage-001",
            actor=_actor,
        )

    candidate = build_turn_envelope(full)
    candidate["action_signature"]["matches"] = False
    with pytest.raises(ValueError, match="parity must be verified"):
        run_model_behavior_qualification_pair(
            full,
            candidate,
            qualification_id="case-lineage-002",
            actor=_actor,
        )


def test_paired_receipts_expose_behavior_drift_and_safety_violation() -> None:
    def actor(request: Mapping[str, Any]) -> dict[str, Any]:
        patch = (
            {
                "decision": "wait",
                "must_attempt_work": False,
                "delivery_allowed": False,
                "quiet_noop_allowed": True,
                "external_write_requested": True,
                "intended_action_kinds": ["wait"],
                "reason_codes": ["candidate_lost_obligation"],
            }
            if request["arm"] == "candidate_packet"
            else {}
        )
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(**patch),
            "tool_calls": [],
        }

    full_packet = _full_packet()
    full_receipt = run_model_behavior_qualification_arm(
        full_packet,
        qualification_id="case-drift-001",
        arm="full_packet",
        actor=actor,
    )
    candidate_receipt = run_model_behavior_qualification_arm(
        build_turn_envelope(full_packet),
        qualification_id="case-drift-001",
        arm="candidate_packet",
        actor=actor,
    )
    result = compare_model_behavior_receipts(full_receipt, candidate_receipt)

    assert result["equivalent"] is False
    assert set(result["hard_invariant_drift"]) == {
        "decision",
        "must_attempt_work",
        "delivery_allowed",
        "quiet_noop_allowed",
        "external_write_requested",
    }
    assert result["safety_violations"] == ["external_write_requested"]
    assert result["behavior_signal_drift"] == {
        "intended_action_kinds": {
            "full_packet": ["inspect", "edit", "test", "writeback", "spend"],
            "candidate_packet": ["wait"],
        }
    }


def test_unknown_intended_action_kind_fails_closed() -> None:
    def ambiguous_actor(_: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(intended_action_kinds=["publish_everything"]),
            "tool_calls": [],
        }

    with pytest.raises(ValueError, match="unknown action kind"):
        run_model_behavior_qualification_arm(
            _full_packet(),
            qualification_id="case-action-kind-001",
            arm="full_packet",
            actor=ambiguous_actor,
        )


def test_receipt_marks_self_contradictory_quiet_noop_as_unsafe() -> None:
    def contradictory_actor(_: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(quiet_noop_allowed=True),
            "tool_calls": [],
        }

    receipt = run_model_behavior_qualification_arm(
        _full_packet(),
        qualification_id="case-contradiction-001",
        arm="full_packet",
        actor=contradictory_actor,
    )

    assert receipt["safety_violations"] == ["quiet_noop_conflicts_with_must_attempt"]


def test_required_semantic_contract_keeps_only_aligned_digests() -> None:
    def semantic_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(
                semantic_contract=model_behavior_semantic_contract_from_packet(
                    request["packet"],
                    arm=str(request["arm"]),
                )
            ),
            "tool_calls": [],
        }

    full = _full_packet()
    result = run_model_behavior_qualification_pair(
        full,
        build_turn_envelope(full),
        qualification_id="case-semantic-aligned-001",
        actor=semantic_actor,
        semantic_contract_required=True,
    )

    assert result["equivalent"] is True
    assert result["semantic_contract_complete"] is True
    assert result["semantic_contract_drift"] == {}
    encoded = json.dumps(result, sort_keys=True)
    assert "loopx/**" not in encoded
    assert "Implement one bounded" not in encoded


def test_semantic_contract_subset_grades_only_declared_fields() -> None:
    full = _full_packet()
    full["planning_horizon"] = {
        "schema_version": "quota_planning_horizon_v0",
        "selected_todo_id": "todo_fixture001",
        "work_items": [{"todo_id": "todo_fixture001"}],
        "relations": [],
        "completeness": {"complete": True},
    }

    def actor(request: Mapping[str, Any]) -> dict[str, Any]:
        semantics = model_behavior_semantic_contract_from_packet(
            request["packet"],
            arm=str(request["arm"]),
        )
        assert request["response_contract"]["semantic_contract_fields"] == [
            "planning_horizon"
        ]
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(
                semantic_contract={"planning_horizon": semantics["planning_horizon"]}
            ),
            "tool_calls": [],
        }

    result = run_model_behavior_qualification_pair(
        full,
        build_turn_envelope(full),
        qualification_id="case-semantic-subset-001",
        actor=actor,
        semantic_contract_required=True,
        semantic_contract_fields=("planning_horizon",),
    )

    assert result["equivalent"] is True
    assert result["semantic_contract_complete"] is True
    assert result["semantic_contract_fields"] == ["planning_horizon"]
    assert result["semantic_contract_drift"] == {}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ((), "must not be empty"),
        (("planning_horizon", "planning_horizon"), "duplicate"),
        (("planning_horizon", "future_field"), "unknown"),
    ],
)
def test_semantic_contract_subset_rejects_invalid_coverage(
    fields: tuple[str, ...], message: str
) -> None:
    packet = _full_packet()
    with pytest.raises(ValueError, match=message):
        build_model_behavior_actor_request(
            packet,
            qualification_id="case-semantic-subset-invalid-001",
            arm="full_packet",
            semantic_contract_required=True,
            semantic_contract_fields=fields,
        )


def test_paired_receipts_fail_closed_when_semantic_coverage_differs() -> None:
    full_receipt = run_model_behavior_qualification_arm(
        _full_packet(),
        qualification_id="case-semantic-coverage-drift-001",
        arm="full_packet",
        actor=lambda request: {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(
                semantic_contract={
                    "planning_horizon": model_behavior_semantic_contract_from_packet(
                        request["packet"], arm=str(request["arm"])
                    )["planning_horizon"]
                }
            ),
            "tool_calls": [],
        },
        semantic_contract_required=True,
        semantic_contract_fields=("planning_horizon",),
    )
    candidate_receipt = dict(full_receipt)
    candidate_receipt["semantic_contract_fields"] = ["scheduler_action"]

    result = compare_model_behavior_receipts(full_receipt, candidate_receipt)

    assert result["equivalent"] is False
    assert result["semantic_contract_fields"] == []
    assert result["safety_violations"] == [
        "semantic_contract_coverage_mismatch"
    ]


def test_semantic_contract_preserves_peer_identity_and_continuation() -> None:
    full = _full_packet()
    full["selected_todo"]["continuation_policy"] = "same_agent_non_delivery"

    full_semantics = model_behavior_semantic_contract_from_packet(
        full,
        arm="full_packet",
    )
    candidate_semantics = model_behavior_semantic_contract_from_packet(
        build_turn_envelope(full),
        arm="candidate_packet",
    )

    expected = {
        "agent_id": "codex-fixture",
        "selected_todo_claimed_by": "codex-fixture",
        "continuation_policy": "same_agent_non_delivery",
        "same_agent_continuation": True,
    }
    assert full_semantics["peer_route"] == expected
    assert candidate_semantics["peer_route"] == expected


def test_semantic_contract_preserves_bounded_planning_horizon_relations() -> None:
    full = _full_packet()
    full["planning_horizon"] = {
        "schema_version": "quota_planning_horizon_v0",
        "selected_todo_id": "todo_fixture001",
        "work_items": [
            {"todo_id": "todo_fixture001"},
            {"todo_id": "todo_context001"},
        ],
        "attention_todo_ids": ["todo_context001"],
        "relations": [
            {
                "from_todo_id": "todo_context001",
                "relation": "successor",
                "to_ref": "todo_fixture001",
                "enforcement": "lineage_only",
            }
        ],
        "completeness": {
            "complete": False,
            "omitted_candidate_todo_count": 1,
        },
        "detail_refs": {
            "agent_todos": (
                "quota should-run --goal-id fixture-goal --agent-id "
                "codex-fixture --include-detail agent-todos"
            )
        },
    }

    full_semantics = model_behavior_semantic_contract_from_packet(
        full,
        arm="full_packet",
    )["planning_horizon"]
    candidate_semantics = model_behavior_semantic_contract_from_packet(
        build_turn_envelope(full),
        arm="candidate_packet",
    )["planning_horizon"]

    assert candidate_semantics == full_semantics
    assert full_semantics == {
        "present": True,
        "selected_todo_id": "todo_fixture001",
        "visible_todo_ids": ["todo_fixture001", "todo_context001"],
        "attention_todo_ids": ["todo_context001"],
        "relation_kinds": ["successor"],
        "relation_count": 1,
        "relations": [
            {
                "from_todo_id": "todo_context001",
                "relation": "successor",
                "to_ref": "todo_fixture001",
                "enforcement": "lineage_only",
            }
        ],
        "complete": False,
        "truncated": True,
        "cold_path_available": True,
    }


def test_semantic_contract_rejects_a_dangling_planning_horizon_cold_path() -> None:
    full = _full_packet()
    full["planning_horizon"] = {
        "schema_version": "quota_planning_horizon_v0",
        "selected_todo_id": "todo_fixture001",
        "work_items": [{"todo_id": "todo_fixture001"}],
        "relations": [],
        "completeness": {"complete": False, "omitted_candidate_todo_count": 1},
        "detail_refs": {
            "agent_todos": (
                "quota should-run --goal-id fixture-goal --agent-id "
                "codex-fixture --include-detail agent-todos"
            )
        },
    }
    candidate = build_turn_envelope(full)
    candidate.pop("detail_ref")

    assert model_behavior_semantic_contract_from_packet(
        full,
        arm="full_packet",
    )["planning_horizon"]["cold_path_available"] is True
    assert model_behavior_semantic_contract_from_packet(
        candidate,
        arm="candidate_packet",
    )["planning_horizon"]["cold_path_available"] is False


def test_same_wrong_semantics_in_both_arms_fail_source_alignment() -> None:
    def wrong_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        semantics = model_behavior_semantic_contract_from_packet(
            request["packet"],
            arm=str(request["arm"]),
        )
        semantics["write_scope"] = ["wrong-scope/**"]
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(semantic_contract=semantics),
            "tool_calls": [],
        }

    full = _full_packet()
    result = run_model_behavior_qualification_pair(
        full,
        build_turn_envelope(full),
        qualification_id="case-semantic-misaligned-001",
        actor=wrong_actor,
        semantic_contract_required=True,
    )

    assert result["equivalent"] is False
    assert result["semantic_contract_complete"] is False
    assert result["semantic_contract_drift"] == {}
    assert result["safety_violations"] == ["semantic_contract_mismatch:write_scope"]
