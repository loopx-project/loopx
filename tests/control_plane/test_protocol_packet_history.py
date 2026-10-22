"""Read frozen release objects through TS and host admission, never today's writer.

These four synthetic inputs cover the v0 compatibility boundary, not a complete
external archive. Generation provenance and replay instructions live beside the
objects. Changing the pinned bytes requires reviewing the release provenance;
regenerating envelopes with the current writer would erase the regression oracle.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.effect_program import interpret_quota_should_run_packet
from loopx.control_plane.quota.turn_envelope import (
    quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority


FIXTURE = Path(__file__).parents[1] / "fixtures" / "protocol_packet_history_v0.json"
HISTORY = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = HISTORY["cases"]
CASE_IDS = [case["id"] for case in CASES]
VERIFIED_SUMMARY = (
    "actor=agent user_action_required=false agent_action_required=true "
    "quiet_noop_allowed=false llm=no_api agent_action=inspect the synthetic fixture"
)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def test_history_is_pinned_to_the_release_generated_bytes() -> None:
    assert HISTORY["source"]["git_sha"] == "607c11d75e9d608e44d9caf6b675b32c0abb8267"
    assert HISTORY["source"]["release"] == "v1.1.0"
    assert HISTORY["source"]["synthetic"] is True
    assert CASE_IDS == ["verified", "residue", "opaque", "missing_packet"]
    assert sha256(FIXTURE.read_bytes()).hexdigest() == (
        "da0520a7f1b41402229e31a690b6b5a5e1e83144cb21f9b2960d4aebd4dccb14"
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_release_decision_and_stored_envelope_keep_exact_signature(
    case: dict[str, Any],
) -> None:
    decision, envelope = deepcopy(case["decision"]), deepcopy(case["envelope"])
    before = deepcopy((decision, envelope))
    absent = case["id"] == "missing_packet"
    expected_action = {
        "verified": "inspect the synthetic fixture",
        "residue": "inspect the updated synthetic fixture",
        "opaque": "inspect the synthetic fixture",
        "missing_packet": "quiet no-op; no material transition",
    }[case["id"]]
    expected_summary = (
        None if absent else "synthetic opaque historical observation"
        if case["id"] == "opaque" else VERIFIED_SUMMARY
    )
    expected_commands = [] if absent else [
        "loopx status --goal-id history-fixture-goal"
    ]

    # These public Python adapters call the real TypeScript bridge. No mocks,
    # build_turn_envelope calls, or signature replacement occur in these tests.
    turn = interpret_quota_should_run_packet(decision)
    source_document = quota_action_signature_document(decision)
    envelope_document = turn_envelope_action_signature_document(envelope)
    signature = envelope["action_signature"]
    assert source_document == envelope_document
    assert _canonical_hash(decision) == signature["source_decision_hash"]
    assert _canonical_hash(source_document) == signature["source_hash"]
    assert _canonical_hash(envelope_document) == signature["envelope_hash"]
    assert signature["source_hash"] == signature["envelope_hash"]
    assert signature["matches"] is True
    assert signature["coverage"] == "turn_envelope_action_dimensions_v0"

    authority = extract_turn_authority({"turn_envelope": envelope})
    assert authority == {
        "primary_action": expected_action,
        "required_reads": [],
        "write_scope": ["tests/**"],
        "workspace_guard": {},
        "boundary": {"rule": "stay_in_scope_or_stop"},
    }
    assert turn.observation.protocol_summary == expected_summary
    assert turn.observation.should_run is (not absent)
    assert turn.observation.effective_action == ("quota_skip" if absent else "normal_run")
    assert list(turn.next_effect.cli_actions) == expected_commands
    assert envelope["action"]["must_attempt"] is (not absent)
    assert envelope["action"]["delivery_allowed"] is (not absent)
    assert envelope["action"]["quiet_noop_allowed"] is absent
    assert envelope["writeback"]["next_cli_actions"] == expected_commands
    assert envelope["writeback"]["spend_allowed_now"] is False
    assert envelope["writeback"]["spend_after_validation"] is (not absent)
    assert envelope["execution_policy"]["safe_bypass_allowed"] is False

    capsule = envelope["contract_capsule"]
    if absent:
        assert "protocol_action_packet" not in decision
        assert "protocol_action_packet" not in capsule
    else:
        witness = capsule["protocol_action_packet"]
        assert witness["derivation_status"] == {
            "verified": "verified",
            "residue": "verified_with_residue",
            "opaque": "unverified_retain_summary",
        }[case["id"]]
        assert witness["summary_hash"] == _canonical_hash(expected_summary)
        assert witness["reconstruction_verified"] is (case["id"] != "opaque")
        if case["id"] == "opaque":
            assert witness["summary"] == expected_summary
        else:
            assert "summary" not in witness
        if case["id"] == "residue":
            assert witness["residue"] == {"agent_action": "inspect the synthetic fixture"}
        else:
            assert "residue" not in witness
    assert (decision, envelope) == before


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
@pytest.mark.parametrize(
    "corruption",
    ["missing_signature", "mismatch", "forged_equal_hashes", "action", "spend", "witness"],
)
def test_host_rejects_corrupted_release_envelope_without_resigning(
    case: dict[str, Any], corruption: str,
) -> None:
    envelope = deepcopy(case["envelope"])
    if corruption == "missing_signature":
        envelope.pop("action_signature")
    elif corruption == "mismatch":
        envelope["action_signature"]["matches"] = False
    elif corruption == "forged_equal_hashes":
        envelope["action_signature"].update(
            source_hash="sha256:" + "0" * 64,
            envelope_hash="sha256:" + "0" * 64,
        )
    elif corruption == "action":
        envelope["action"]["primary_action"] = "unsigned replacement action"
    elif corruption == "spend":
        envelope["writeback"]["spend_allowed_now"] = True
    else:
        capsule = envelope["contract_capsule"]
        if case["id"] == "missing_packet":
            capsule["protocol_action_packet"] = {
                "schema_version": "protocol_action_packet_v0", "summary": VERIFIED_SUMMARY,
            }
        elif case["id"] == "residue":
            capsule["protocol_action_packet"].pop("residue")
        elif case["id"] == "opaque":
            capsule["protocol_action_packet"].pop("summary")
        else:
            capsule.pop("protocol_action_packet")
    before = deepcopy(envelope)
    with pytest.raises(ValueError, match="action signature is missing or does not match"):
        extract_turn_authority({"turn_envelope": envelope})
    assert envelope == before
