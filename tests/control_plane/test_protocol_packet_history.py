"""Frozen release objects prove format rejection without rewriting evidence."""

from argparse import Namespace
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from loopx.cli_commands.quota import _project_quota_cli_payload
from loopx.control_plane.effect_program import interpret_quota_should_run_packet
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope,
    quota_action_signature_document,
    turn_envelope_action_signature_document,
    turn_envelope_replan_settlement_identity,
    bind_turn_decision_capture,
)
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority
from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown

from loopx.presentation.renderers.turn_envelope_markdown import (
    render_turn_envelope_markdown,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "protocol_packet_history_v0.json"
HISTORY = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = HISTORY["cases"]
RETIRED = [case for case in CASES if case["id"] != "missing_packet"]
MODERN = next(case for case in CASES if case["id"] == "missing_packet")
ERROR = "unsupported format: protocol_action_packet"


def test_history_bytes_are_unchanged():
    assert HISTORY["source"]["git_sha"] == "607c11d75e9d608e44d9caf6b675b32c0abb8267"
    assert (
        sha256(FIXTURE.read_bytes()).hexdigest()
        == "da0520a7f1b41402229e31a690b6b5a5e1e83144cb21f9b2960d4aebd4dccb14"
    )


@pytest.mark.parametrize("case", RETIRED, ids=[case["id"] for case in RETIRED])
def test_retired_release_objects_are_rejected_without_resigning(case):
    decision, envelope = deepcopy(case["decision"]), deepcopy(case["envelope"])
    before = deepcopy((decision, envelope))
    for reader in (
        interpret_quota_should_run_packet,
        build_turn_envelope,
        quota_action_signature_document,
        render_quota_should_run_markdown,
    ):
        with pytest.raises(EffectRuntimeRejected, match=ERROR):
            reader(decision)
    for use_envelope in (False, True):
        with pytest.raises(EffectRuntimeRejected, match=ERROR):
            _project_quota_cli_payload(
                decision,
                Namespace(turn_envelope=use_envelope, quota_command="should-run"),
                frozenset(),
                None,
            )
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        turn_envelope_action_signature_document(envelope)
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        extract_turn_authority({"turn_envelope": envelope})
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        render_turn_envelope_markdown(envelope)
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        bind_turn_decision_capture(
            MODERN["decision"],
            envelope,
            captured_decision_path="capture.json",
            turn_instance_id="fixture-turn",
            turn_key="sha256:" + "0" * 64,
        )
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        bind_turn_decision_capture(
            decision,
            envelope,
            captured_decision_path="capture.json",
            turn_instance_id="fixture-turn",
            turn_key="sha256:" + "0" * 64,
        )
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        turn_envelope_replan_settlement_identity(envelope, turn_instance_id="fixture-turn")
    assert (decision, envelope) == before


def test_packet_free_release_record_keeps_original_signature_and_authority():
    decision, envelope = deepcopy(MODERN["decision"]), deepcopy(MODERN["envelope"])
    before = deepcopy((decision, envelope))
    document = turn_envelope_action_signature_document(envelope)
    digest = (
        "sha256:"
        + sha256(
            json.dumps(
                document, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert quota_action_signature_document(decision) == document
    assert digest == envelope["action_signature"]["envelope_hash"]
    assert (
        extract_turn_authority({"turn_envelope": envelope})["primary_action"]
        == "quiet no-op; no material transition"
    )
    assert (
        interpret_quota_should_run_packet(decision).observation.protocol_summary is None
    )
    assert (decision, envelope) == before


@pytest.mark.parametrize("location", ["top_level", "contract_capsule"])
@pytest.mark.parametrize(
    "value", [None, {}, [], "", False, {"schema_version": "unknown"}]
)
def test_presence_is_rejected_in_decision_or_witness_even_when_empty(value, location):
    decision, envelope = deepcopy(MODERN["decision"]), deepcopy(MODERN["envelope"])
    source = decision if location == "top_level" else decision.setdefault(location, {})
    source["protocol_action_packet"] = value
    envelope["contract_capsule"]["protocol_action_packet"] = value
    before = deepcopy((decision, envelope))
    for reader in (
        interpret_quota_should_run_packet,
        build_turn_envelope,
        quota_action_signature_document,
        render_quota_should_run_markdown,
    ):
        with pytest.raises(EffectRuntimeRejected, match=ERROR):
            reader(decision)
    for use_envelope in (False, True):
        with pytest.raises(EffectRuntimeRejected, match=ERROR):
            _project_quota_cli_payload(
                decision,
                Namespace(turn_envelope=use_envelope, quota_command="should-run"),
                frozenset(),
                None,
            )
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        bind_turn_decision_capture(
            decision,
            MODERN["envelope"],
            captured_decision_path="capture.json",
            turn_instance_id="fixture-turn",
            turn_key="sha256:" + "0" * 64,
        )
    with pytest.raises(EffectRuntimeRejected, match=ERROR):
        extract_turn_authority({"turn_envelope": envelope})
    assert (decision, envelope) == before


@pytest.mark.parametrize(
    "corruption",
    ["missing_signature", "mismatch", "forged_equal_hashes", "action", "spend"],
)
def test_modern_release_host_signature_negative_cases(corruption):
    envelope = deepcopy(MODERN["envelope"])
    if corruption == "missing_signature":
        envelope.pop("action_signature")
    elif corruption == "mismatch":
        envelope["action_signature"]["matches"] = False
    elif corruption == "forged_equal_hashes":
        envelope["action_signature"].update(
            source_hash="sha256:" + "0" * 64, envelope_hash="sha256:" + "0" * 64
        )
    elif corruption == "action":
        envelope["action"]["primary_action"] = "unsigned replacement"
    else:
        envelope["writeback"]["spend_allowed_now"] = True
    before = deepcopy(envelope)
    with pytest.raises(
        ValueError, match="action signature is missing or does not match"
    ):
        extract_turn_authority({"turn_envelope": envelope})
    assert envelope == before
