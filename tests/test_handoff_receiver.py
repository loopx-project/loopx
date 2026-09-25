"""Receiver contracts: whole content, strict transport, no ownership effects."""

from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from loopx.control_plane.handoff.project_agent_context import (
    build_project_agent_handoff,
)
from loopx.control_plane.handoff.handoff_fragments import (
    ENVELOPE_PREFIX,
    render_handoff_transport,
    split_handoff_text,
)
from loopx.review_packet import build_review_packet


def status_fixture():
    return {
        "attention_queue": {
            "items": [
                {
                    "goal_id": "handoff-contract",
                    "waiting_on": "codex",
                    "status": "operator_gate_approved",
                    "agent_command": "touch handoff-should-not-execute; printf '%s' '"
                    + "evidence " * 350
                    + "RETURN-VALIDATION'",
                    "project_asset": {
                        "agent_todos": {
                            "items": [
                                {"text": "Preserve the source citation."},
                                {"text": "Return validation and remaining limits."},
                            ]
                        },
                        "execution_profile": {
                            "minimum_scale": "implementation",
                            "must_include": ["targeted_validation", "state_writeback"],
                        },
                    },
                    "handoff_readiness": {"post_handoff_small_scale_streak": 3},
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": "handoff-contract",
                    "authority_registry": {
                        "declared": True,
                        "project_material_count": 7,
                        "topic_authority_count": 2,
                    },
                }
            ]
        },
    }


def receive(tmp_path, value, *, input_format="json", extra=()):
    text = json.dumps(value, ensure_ascii=False) if input_format == "json" else value
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(tmp_path / "absent-registry.json"),
            "--runtime-root",
            str(tmp_path / "absent-runtime"),
            "--format",
            "json",
            "handoff",
            "restore",
            "--input",
            "-",
            "--input-format",
            input_format,
            *extra,
        ],
        cwd=tmp_path,
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    assert not list(tmp_path.iterdir()), (
        "content restore must not create registry/runtime/work state"
    )
    return result.returncode, json.loads(result.stdout)


def test_common_context_and_human_display_are_independent(monkeypatch):
    source = status_fixture()
    source["attention_queue"]["items"][0]["status"] = "active"
    before = copy.deepcopy(source)
    context = build_project_agent_handoff(source, goal_id="handoff-contract")
    monkeypatch.setattr(
        "loopx.review_packet.human_prompt",
        lambda _: {
            "question": "CHANGED HUMAN LABEL",
            "reply": "DISPLAY ONLY",
            "boundary": "DISPLAY BOUNDARY",
        },
    )
    full = build_review_packet(source, goal_id="handoff-contract")
    assert full["project_agent_handoff"] == context["project_agent_handoff"]
    text = context["project_agent_handoff"]
    for required in (
        "Preserve the source citation.",
        "Return validation and remaining limits.",
        "materials=7",
        "targeted validation",
        "state writeback",
        "RETURN-VALIDATION",
    ):
        assert required in text
    assert "CHANGED HUMAN LABEL" in full["packet"]
    assert "CHANGED HUMAN LABEL" not in text
    assert context["project_agent_handoff_fragments"][0] != text
    assert source == before


def test_restore_cli_success_does_not_execute_or_adopt(tmp_path):
    packet = build_review_packet(status_fixture(), goal_id="handoff-contract")
    code, payload = receive(tmp_path, packet)
    assert code == 0
    assert payload == {"ok": True, "handoff_text": packet["project_agent_handoff"]}
    assert "RETURN-VALIDATION" in payload["handoff_text"]


def test_restore_cli_accepts_complete_markdown_transport(tmp_path):
    packet = build_review_packet(status_fixture(), goal_id="handoff-contract")
    markdown = render_handoff_transport(
        packet["project_agent_handoff"], packet["project_agent_handoff_fragments"]
    )
    code, payload = receive(tmp_path, markdown, input_format="markdown")
    assert code == 0
    assert payload == {"ok": True, "handoff_text": packet["project_agent_handoff"]}


def test_restore_cli_keeps_unframed_markdown_without_transport_titles(tmp_path):
    text = "目标校验：g\n交接分片只是普通讨论文字，不是传输标题"
    code, payload = receive(tmp_path, text, input_format="markdown")
    assert code == 0
    assert payload == {"ok": True, "handoff_text": text}


@pytest.mark.parametrize("mutation", ["stripped", "indented", "one_missing"])
def test_restore_cli_rejects_markdown_with_unverifiable_envelopes(tmp_path, mutation):
    packet = build_review_packet(status_fixture(), goal_id="handoff-contract")
    markdown = render_handoff_transport(
        packet["project_agent_handoff"], packet["project_agent_handoff_fragments"]
    )
    assert len(packet["project_agent_handoff_fragments"]) > 1
    lines = markdown.split("\n")
    if mutation == "stripped":
        lines = [line for line in lines if not line.startswith(ENVELOPE_PREFIX)]
    elif mutation == "indented":
        lines = [
            "  " + line if line.startswith(ENVELOPE_PREFIX) else line for line in lines
        ]
    else:
        lines.remove(next(line for line in lines if line.startswith(ENVELOPE_PREFIX)))
    code, payload = receive(tmp_path, "\n".join(lines), input_format="markdown")
    assert code == 1
    assert payload["error_code"] == "envelope"
    assert "handoff_text" not in payload


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("first", "missing"),
        ("missing", "missing"),
        ("mixed", "set_mismatch"),
        ("tamper", "integrity"),
        ("reverse", "out_of_order"),
        ("duplicate", "duplicate"),
        ("complete_field", "integrity"),
        ("manifest", "manifest"),
        ("no_fragments", "missing"),
    ],
)
def test_restore_cli_rejects_incomplete_or_inconsistent_input(
    tmp_path, mutation, expected
):
    packet = build_review_packet(status_fixture(), goal_id="handoff-contract")
    shards = packet["project_agent_handoff_fragments"]
    if mutation == "first":
        packet["project_agent_handoff_fragments"] = shards[:1]
    elif mutation == "missing":
        packet["project_agent_handoff_fragments"] = shards[1:]
    elif mutation == "mixed":
        shards[1] = split_handoff_text("foreign\n" * 30 + "end")[1]
    elif mutation == "tamper":
        shards[0] += "tampered"
    elif mutation == "reverse":
        shards.reverse()
    elif mutation == "duplicate":
        shards.append(shards[-1])
    elif mutation == "complete_field":
        packet["project_agent_handoff"] += "tampered"
    elif mutation == "manifest":
        packet["handoff_fragment_manifest"]["total"] += 1
    elif mutation == "no_fragments":
        del packet["project_agent_handoff_fragments"]
    code, payload = receive(tmp_path, packet)
    assert code == 1
    assert payload["error_code"] == expected, payload
    assert "handoff_text" not in payload


@pytest.mark.parametrize(
    "kind,expected",
    [("first", "missing"), ("malformed", "envelope"), ("tamper", "integrity")],
)
def test_restore_cli_raw_markdown_failure(tmp_path, kind, expected):
    packet = build_review_packet(status_fixture(), goal_id="handoff-contract")
    text = packet["project_agent_handoff_fragments"][0]
    if kind == "malformed":
        text = text.replace("v=1", "v=broken")
    if kind == "tamper":
        text = text.replace("目标校验", "changed")
    code, payload = receive(tmp_path, text, input_format="markdown")
    assert code == 1 and payload["error_code"] == expected
    assert "handoff_text" not in payload


def test_restore_cli_does_not_accept_ownership_options(tmp_path):
    code, payload = receive(tmp_path, {}, extra=("--agent-id", "receiver"))
    assert code == 1 and payload["error_code"] == "input"


def test_handoff_only_does_not_construct_review_packet(monkeypatch, capsys, tmp_path):
    from argparse import Namespace
    from loopx.cli_commands import status

    monkeypatch.setattr(status, "collect_status", lambda **_: status_fixture())

    def forbidden(*args, **kwargs):
        raise AssertionError("handoff-only must not build a human packet")

    monkeypatch.setattr(status, "build_review_packet", forbidden)
    args = Namespace(
        handoff_only=True,
        goal_id="handoff-contract",
        action_kind=None,
        review_url=None,
        agent_id=None,
        scan_path=[],
        scan_root=".",
        limit=5,
        available_capabilities=None,
    )
    result = status.handle_review_packet_command(
        args,
        registry_path=tmp_path / "unused",
        runtime_root_arg=None,
        output_format=lambda *args: "json",
        print_payload=lambda value, *_: print(json.dumps(value)),
    )
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["handoff_text"] == output["project_agent_handoff"]
    assert "question" not in output


def test_hostile_declared_count_is_rejected_without_expanding_it(tmp_path):
    packet = build_review_packet(status_fixture(), goal_id="handoff-contract")
    shard = packet["project_agent_handoff_fragments"][0]
    import re

    shard = re.sub(r" n=\d+ ", " n=999999999 ", shard)
    code, payload = receive(tmp_path, shard, input_format="markdown")
    assert code == 1 and payload["error_code"] == "missing"


@pytest.mark.parametrize("action", ["prepare", "inspect", "adopt"])
def test_ownership_actions_still_require_identity_before_opening_registry(
    tmp_path, action
):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(tmp_path / "absent"),
            "--format",
            "json",
            "handoff",
            action,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["reason_code"] == "invalid_continuation_request"
    assert "require --goal-id" in payload["reason"]
    assert not list(tmp_path.iterdir())
