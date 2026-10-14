from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_RUNTIME_INTEGRITY_ATTESTATION_SCHEMA_VERSION,
    REQUIRED_RUNTIME_ATTESTATIONS,
    build_benchmark_integrity_qualification,
    build_traex_model_route_receipt,
    capture_traex_benchmark_evidence,
    convert_traex_events_to_atif,
)
from tests.source_cli import run_source_cli

def _route_event(model: str, provider: str = "trae") -> dict[str, object]:
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "context": {
                "model": model,
                "modelProviderId": provider,
                "modelBackendVariant": "stable",
            },
        },
    }


def _session_meta(thread_id: str = "thread-1") -> dict[str, object]:
    return {"type": "session_meta", "payload": {"id": thread_id}}


def _stdout_events(
    private_value: str = "private-task-value",
) -> list[dict[str, object]]:
    return [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {"id": "warning", "type": "error", "message": "warning"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "command": f"printf {private_value}",
                "aggregated_output": private_value,
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "file-1",
                "type": "file_change",
                "changes": [{"path": "private-file.txt", "kind": "add"}],
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "message-1", "type": "agent_message", "text": "done"},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 1}},
    ]


def test_stdout_items_convert_to_private_atif() -> None:
    trajectory = convert_traex_events_to_atif(_stdout_events())

    assert trajectory["schema_version"] == "ATIF-v1.7"
    assert trajectory["steps"] == [
        {
            "step_id": "1",
            "source": "agent",
            "message": "",
            "tool_calls": [
                {
                    "function_name": "exec_command",
                    "arguments": {"cmd": "printf private-task-value"},
                }
            ],
            "observation": {"output": "private-task-value", "exit_code": 0},
        },
        {
            "step_id": "2",
            "source": "agent",
            "message": "",
            "tool_calls": [
                {
                    "function_name": "apply_patch",
                    "arguments": {
                        "changes": [{"path": "private-file.txt", "kind": "add"}]
                    },
                }
            ],
            "observation": {"status": "completed"},
        },
        {
            "step_id": "3",
            "source": "agent",
            "message": "done",
            "tool_calls": [],
        },
    ]


def test_converted_stdout_is_accepted_by_integrity_qualification() -> None:
    trajectory = convert_traex_events_to_atif(_stdout_events())
    attestation = {
        "schema_version": BENCHMARK_RUNTIME_INTEGRITY_ATTESTATION_SCHEMA_VERSION,
        "authority": "runner",
        "benchmark_id": "fixture@v0",
        "case_id": "case-1",
        **{field: True for field in REQUIRED_RUNTIME_ATTESTATIONS},
    }

    receipt = build_benchmark_integrity_qualification(
        trajectory=trajectory,
        runtime_attestation=attestation,
    )

    assert receipt["integrity_qualified"] is True
    assert receipt["score_claim_eligible"] is True


def test_archive_prefers_response_items_over_duplicate_history_mutations() -> None:
    call = {
        "type": "function_call",
        "name": "exec",
        "arguments": '{"cmd":"pwd"}',
        "call_id": "call-1",
    }
    output = {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "/workspace\n",
    }
    events = [
        {"type": "response_item", "payload": call},
        {"type": "response_item", "payload": output},
        {
            "type": "history_mutation",
            "payload": {"operation": "append", "items": [call, output]},
        },
    ]

    trajectory = convert_traex_events_to_atif(events)

    assert len(trajectory["steps"]) == 1
    assert trajectory["steps"][0]["tool_calls"][0] == {
        "function_name": "exec",
        "arguments": {"cmd": "pwd"},
    }


def test_archive_history_mutation_is_supported_when_response_items_are_absent() -> None:
    trajectory = convert_traex_events_to_atif(
        [
            {
                "type": "history_mutation",
                "payload": {
                    "operation": "append",
                    "items": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ],
                },
            }
        ]
    )

    assert trajectory["steps"][0]["message"] == "done"


def test_archive_custom_tool_pair_converts_without_losing_action() -> None:
    trajectory = convert_traex_events_to_atif(
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** End Patch",
                    "call_id": "call-custom-1",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-custom-1",
                    "output": "Done!",
                },
            },
        ]
    )

    assert trajectory["steps"] == [
        {
            "step_id": "1",
            "source": "agent",
            "message": "",
            "tool_calls": [
                {
                    "function_name": "apply_patch",
                    "arguments": "*** Begin Patch\n*** End Patch",
                }
            ],
            "observation": "Done!",
        }
    ]


def test_archive_unknown_action_fails_closed_before_integrity_qualification() -> None:
    with pytest.raises(ValueError, match="traex_archive_action_unsupported"):
        convert_traex_events_to_atif(
            [
                {
                    "type": "response_item",
                    "payload": {"type": "computer_call", "id": "action-1"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"text": "done"}],
                    },
                },
            ]
        )


def test_archive_malformed_response_item_fails_closed() -> None:
    with pytest.raises(ValueError, match="traex_archive_response_item_invalid"):
        convert_traex_events_to_atif(
            [
                {"type": "response_item", "payload": "unparsed-action"},
                {
                    "type": "history_mutation",
                    "payload": {
                        "operation": "append",
                        "items": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"text": "done"}],
                            }
                        ],
                    },
                },
            ]
        )


def test_archive_ignores_only_known_non_action_items() -> None:
    trajectory = convert_traex_events_to_atif(
        [
            {"type": "response_item", "payload": {"type": "reasoning"}},
            *[
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": role,
                        "content": [{"text": f"{role}-prompt"}],
                    },
                }
                for role in ("developer", "system", "user")
            ],
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"text": "done"}],
                },
            },
        ]
    )

    assert trajectory["steps"] == [
        {
            "step_id": "1",
            "source": "agent",
            "message": "done",
            "tool_calls": [],
        }
    ]


def test_archive_unknown_message_role_fails_closed() -> None:
    with pytest.raises(ValueError, match="traex_archive_message_role_unsupported"):
        convert_traex_events_to_atif(
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "tool",
                        "content": [{"text": "hidden action"}],
                    },
                }
            ]
        )


def test_archive_history_replace_supersedes_prior_items() -> None:
    trajectory = convert_traex_events_to_atif(
        [
            {
                "type": "history_mutation",
                "payload": {
                    "operation": "append",
                    "items": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"text": "superseded"}],
                        }
                    ],
                },
            },
            {
                "type": "history_mutation",
                "payload": {
                    "operation": "replace",
                    "items": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"text": "current"}],
                        }
                    ],
                },
            },
        ]
    )

    assert [step["message"] for step in trajectory["steps"]] == ["current"]


def test_stdout_unknown_completed_action_fails_closed() -> None:
    with pytest.raises(ValueError, match="traex_stdout_action_unsupported"):
        convert_traex_events_to_atif(
            [
                {
                    "type": "item.completed",
                    "item": {"id": "unknown", "type": "mystery_action"},
                }
            ]
        )


@pytest.mark.parametrize(
    ("events", "status", "audited", "matched"),
    [
        ([], "route_requested_not_runtime_audited", False, False),
        ([_route_event("GPT-5.4")], "runtime_route_verified", True, True),
        ([_route_event("GPT-5.5")], "runtime_route_mismatch", True, False),
        (
            [_route_event("GPT-5.4"), _route_event("GPT-5.5")],
            "runtime_route_ambiguous",
            True,
            False,
        ),
    ],
)
def test_model_route_receipt_has_explicit_audit_state(
    events: list[dict[str, object]],
    status: str,
    audited: bool,
    matched: bool,
) -> None:
    receipt = build_traex_model_route_receipt(
        events,
        requested_model="GPT-5.4",
    )

    assert receipt["status"] == status
    assert receipt["runtime_audited"] is audited
    assert receipt["matched"] is matched
    assert receipt["raw_content_recorded"] is False
    assert receipt["input_path_recorded"] is False


def test_capture_preview_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "stdout.jsonl"
    source.write_text(
        "\n".join(json.dumps(event) for event in _stdout_events()) + "\n",
        encoding="utf-8",
    )
    atif = tmp_path / "private" / "trajectory.json"
    receipt = tmp_path / "public" / "route.json"

    result = capture_traex_benchmark_evidence(
        source_jsonl=source,
        atif_output=atif,
        route_receipt_output=receipt,
        requested_model="GPT-5.4",
    )

    assert result["status"] == "previewed"
    assert result["write_performed"] is False
    assert not atif.exists()
    assert not receipt.exists()


def test_capture_rejects_unbound_route_source(tmp_path: Path) -> None:
    source = tmp_path / "stdout.jsonl"
    route_source = tmp_path / "route.jsonl"
    source.write_text(
        "\n".join(json.dumps(event) for event in _stdout_events()) + "\n",
        encoding="utf-8",
    )
    route_source.write_text(
        "\n".join(
            json.dumps(event)
            for event in [_session_meta("other-thread"), _route_event("GPT-5.4")]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="traex_route_source_identity_mismatch"):
        capture_traex_benchmark_evidence(
            source_jsonl=source,
            route_source_jsonl=route_source,
            atif_output=tmp_path / "trajectory.json",
            route_receipt_output=tmp_path / "receipt.json",
            requested_model="GPT-5.4",
        )


def test_cli_writes_private_atif_and_public_safe_route_receipt(tmp_path: Path) -> None:
    private_value = "private-cli-task-value"
    source = tmp_path / "private-source.jsonl"
    route_source = tmp_path / "private-route-source.jsonl"
    atif = tmp_path / "private-output" / "trajectory.json"
    route_receipt = tmp_path / "public-output" / "route.json"
    source.write_text(
        "\n".join(json.dumps(event) for event in _stdout_events(private_value)) + "\n",
        encoding="utf-8",
    )
    route_source.write_text(
        "\n".join(
            json.dumps(event) for event in [_session_meta(), _route_event("GPT-5.4")]
        )
        + "\n",
        encoding="utf-8",
    )

    completed = run_source_cli(
        [
            "benchmark",
            "traex-evidence",
            "--source-jsonl",
            str(source),
            "--route-source-jsonl",
            str(route_source),
            "--atif-output",
            str(atif),
            "--route-receipt-output",
            str(route_receipt),
            "--requested-model",
            "GPT-5.4",
            "--require-runtime-route",
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["status"] == "captured"
    assert payload["route_source_bound"] is True
    assert payload["model_route"]["status"] == "runtime_route_verified"
    assert payload["public_boundary"] == {
        "raw_content_recorded": False,
        "input_path_recorded": False,
        "output_path_recorded": False,
    }
    assert private_value not in completed.stdout
    assert str(tmp_path) not in completed.stdout
    assert private_value in atif.read_text(encoding="utf-8")
    public_receipt_text = route_receipt.read_text(encoding="utf-8")
    assert private_value not in public_receipt_text
    assert str(tmp_path) not in public_receipt_text
