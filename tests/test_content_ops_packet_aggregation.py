from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from loopx.capabilities.content_ops import surface
from loopx.control_plane.runtime import time as runtime_time


@pytest.fixture
def connector_packets():
    return {
        "public_handle_packets": [
            surface.build_content_ops_public_handle_observation_packet(
                url="https://x.com/OpenAI",
                source_item_id="source_public_timestamp_test",
                fetch=False,
            )
        ],
        "private_connector_gate_packets": [
            surface.build_content_ops_private_connector_gate_packet()
        ],
    }


@pytest.mark.parametrize(
    "builder",
    [
        surface.build_content_ops_surface_from_connector_packets,
        surface.build_content_ops_packet_aggregation_packet,
    ],
)
@pytest.mark.parametrize(
    "timestamp_args",
    [{}, {"generated_at": None}, {"generated_at": "2026-06-23T00:00:00Z"}],
    ids=["omitted", "none", "explicit"],
)
def test_aggregation_timestamp_resolves_at_call_time(
    connector_packets, monkeypatch, builder, timestamp_args
):
    for hour in (10, 11):
        now = datetime(2030, 1, 2, hour, tzinfo=timezone.utc)
        monkeypatch.setattr(runtime_time, "now_utc", lambda: now)
        payload = builder(**connector_packets, **timestamp_args)
        aggregate_surface = payload.get("surface", payload)
        expected = timestamp_args.get("generated_at") or f"2030-01-02T{hour}:00:00Z"
        assert aggregate_surface["generated_at"] == expected


@pytest.mark.parametrize(
    "builder, extra_args, report_key",
    [
        (surface.build_content_ops_chatview_report_packet, {}, "chatview_report"),
        (
            surface.build_content_ops_walkthrough_artifact_packet,
            {"public_handle_url": "https://x.com/OpenAI"},
            None,
        ),
    ],
)
@pytest.mark.parametrize(
    "timestamp_args",
    [{}, {"generated_at": None}, {"generated_at": "2024-02-03T04:05:06+02:00"}],
    ids=["omitted", "none", "explicit"],
)
def test_report_timestamp_resolves_at_call_time(
    monkeypatch, builder, extra_args, report_key, timestamp_args
):
    for hour in (10, 11):
        clock = Mock(return_value=datetime(2030, 1, 2, hour, tzinfo=timezone.utc))
        monkeypatch.setattr(runtime_time, "now_utc", clock)
        payload = builder(
            channel_count=2, recent_record_count=50, report_count=5, api_request_count=54,
            **extra_args, **timestamp_args,
        )
        report = payload[report_key] if report_key else payload
        expected = timestamp_args.get("generated_at") or f"2030-01-02T{hour}:00:00Z"
        assert report["generated_at"] == expected
        # The walkthrough must share one timestamp with its nested report and aggregation.
        assert clock.call_count == (timestamp_args.get("generated_at") is None)


@pytest.mark.parametrize(
    "command", ["aggregate-packets", "project-chatview-report", "walkthrough-artifact"]
)
@pytest.mark.parametrize("generated_at", [None, "2026-06-23T00:00:00Z"])
def test_content_ops_cli_timestamp(connector_packets, tmp_path, generated_at, command):
    argv = [
        sys.executable, "-m", "loopx.cli", "--format", "json",
        "content-ops", command,
    ]
    if command == "aggregate-packets":
        for key, option in (
            ("public_handle_packets", "--public-packet-json"),
            ("private_connector_gate_packets", "--private-gate-packet-json"),
        ):
            packet_path = tmp_path / f"{key}.json"
            packet_path.write_text(json.dumps(connector_packets[key][0]), encoding="utf-8")
            argv.extend([option, str(packet_path)])
    else:
        argv.extend([
            "--channel-count", "2", "--recent-record-count", "50",
            "--report-count", "5", "--api-request-count", "54",
        ])
    if generated_at is not None:
        argv.extend(["--generated-at", generated_at])

    before = datetime.now(timezone.utc).replace(microsecond=0)
    result = subprocess.run(
        argv, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True
    )
    after = datetime.now(timezone.utc).replace(microsecond=0)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    if command == "aggregate-packets":
        actual = payload["surface"]["generated_at"]
    elif command == "project-chatview-report":
        actual = payload["chatview_report"]["generated_at"]
    else:
        actual = payload["generated_at"]
    if generated_at is None:
        assert actual.endswith("Z")
        assert before <= datetime.fromisoformat(actual.replace("Z", "+00:00")) <= after
    else:
        assert actual == generated_at
    assert payload["external_reads_performed"] is False
    assert payload["private_source_content_read"] is False
    assert payload["autopublish_allowed"] is False
