"""Live observation must not rescan an unbounded Codex rollout."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from benchmark.runtime.session_observation import observe_codex_session_tail


def _row(kind: str, payload: dict, *, timestamp: str) -> bytes:
    return (
        json.dumps(
            {"type": kind, "timestamp": timestamp, "payload": payload},
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def _usage(total: int, *, timestamp: str) -> bytes:
    return _row(
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": total,
                    "output_tokens": 7,
                    "cached_input_tokens": 3,
                }
            },
        },
        timestamp=timestamp,
    )


def test_large_rollout_observes_latest_usage_from_a_bounded_tail(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    private_text = "SYNTHETIC-CONVERSATION-CONTENT"
    with rollout.open("wb") as stream:
        stream.write(_usage(10, timestamp="2026-10-31T00:00:00Z"))
        for index in range(128):
            stream.write(
                _row(
                    "response_item",
                    {"type": "message", "role": "assistant", "content": private_text},
                    timestamp=f"2026-10-31T00:01:{index % 60:02d}Z",
                )
            )
        stream.write(_usage(20, timestamp="2026-10-31T00:03:00Z"))

    observation = observe_codex_session_tail(rollout, max_bytes=2048)

    assert observation["tail_truncated"] is True
    assert observation["sampled_bytes"] <= 2048
    assert observation["usage_cumulative"] == {
        "input_tokens": 20,
        "output_tokens": 7,
        "cached_input_tokens": 3,
    }
    assert observation["last_event_at"] == "2026-10-31T00:03:00Z"
    assert private_text not in json.dumps(observation)


def test_module_entrypoint_emits_the_same_content_free_projection(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_bytes(_usage(42, timestamp="2026-10-31T00:00:00Z"))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmark.runtime.session_observation",
            "--max-bytes",
            "4096",
            str(rollout),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == observe_codex_session_tail(
        rollout, max_bytes=4096
    )


def test_module_entrypoint_does_not_echo_an_unreadable_rollout_path(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "SYNTHETIC-PRIVATE-PATH.jsonl"

    result = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.session_observation", str(missing)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert "CodexSessionObservationError" in result.stderr
    assert str(missing) not in result.stderr


def test_partial_boundary_records_are_ignored_without_hiding_complete_usage(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    complete = _usage(42, timestamp="2026-10-31T00:02:00Z")
    rollout.write_bytes(
        b'{"type":"response_item","payload":{"type":"message","text":"'
        + b"x" * 512
        + b'"}}\n'
        + complete
        + b'{"type":"event_msg","payload":'
    )

    observation = observe_codex_session_tail(rollout, max_bytes=len(complete) + 80)

    assert observation["tail_truncated"] is True
    assert observation["leading_record_discarded"] is True
    assert observation["trailing_record_discarded"] is True
    assert observation["usage_cumulative"]["input_tokens"] == 42


def test_tail_starting_at_record_boundary_keeps_the_first_complete_record(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    retained = _usage(42, timestamp="2026-10-31T00:02:00Z")
    rollout.write_bytes(
        _row(
            "response_item",
            {"type": "message", "role": "assistant", "content": "discarded"},
            timestamp="2026-10-31T00:01:00Z",
        )
        + retained
    )

    observation = observe_codex_session_tail(rollout, max_bytes=len(retained))

    assert observation["tail_truncated"] is True
    assert observation["leading_record_discarded"] is False
    assert observation["complete_record_count"] == 1
    assert observation["usage_cumulative"]["input_tokens"] == 42


def test_tail_smaller_than_one_record_reports_unknown_usage(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_bytes(_usage(42, timestamp="2026-10-31T00:00:00Z"))

    observation = observe_codex_session_tail(rollout, max_bytes=16)

    assert observation["usage_cumulative"] is None
    assert observation["complete_record_count"] == 0
    assert observation["leading_record_discarded"] is True


def test_malformed_fields_cannot_enter_the_content_free_projection(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    private_text = "SYNTHETIC-PRIVATE-FIELD"
    rollout.write_bytes(
        _row(
            "response_item",
            {"type": private_text, "message": private_text},
            timestamp=private_text,
        )
        + _row(
            "event_msg",
            {
                "type": "token_count",
                "info": {"total_token_usage": {"input_tokens": float("nan")}},
            },
            timestamp="2026-10-31T00:03:00Z",
        )
    )

    observation = observe_codex_session_tail(rollout)

    assert observation["event_counts"] == {"other": 1}
    assert observation["usage_cumulative"] is None
    assert observation["last_event_at"] == "2026-10-31T00:03:00Z"
    assert private_text not in json.dumps(observation)
