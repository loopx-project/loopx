from __future__ import annotations

import copy
import io
import json
from types import SimpleNamespace

import pytest

from loopx.capabilities.periodic_report import build_periodic_report_run
from loopx.capabilities.periodic_report import cli as periodic_report_cli
from loopx.cli import main


def _request() -> dict[str, object]:
    return {
        "schema_version": "periodic_report_run_request_v0",
        "generated_at": "2026-07-20T01:00:00Z",
        "period_window": {
            "start_at": "2026-07-13T00:00:00+08:00",
            "end_at": "2026-07-20T00:00:00+08:00",
        },
        "profile": {
            "profile_id": "maintainer_weekly",
            "profile_version": "v1",
            "profile_ref": "profile:maintainer-weekly/v1",
        },
        "source_snapshots": [
            {
                "source_id": "work_items",
                "source_kind": "project_activity",
                "status": "complete",
                "observed_at": "2026-07-20T00:30:00Z",
                "snapshot_digest": "sha256:work-items",
                "snapshot_ref": "snapshot:work-items/2026-w29",
                "item_count": 8,
            },
            {
                "source_id": "discussion_signals",
                "source_kind": "discussion_activity",
                "status": "complete",
                "observed_at": "2026-07-20T00:35:00Z",
                "snapshot_digest": "sha256:discussion-signals",
                "snapshot_ref": "snapshot:discussion/2026-w29",
                "item_count": 3,
            },
        ],
        "artifact_receipt": {
            "artifact_id": "weekly_digest",
            "renderer_id": "structured_markdown",
            "renderer_kind": "markdown",
            "status": "rendered",
            "artifact_digest": "sha256:artifact",
            "artifact_ref": "artifact:weekly/2026-w29",
        },
        "sink_receipts": [
            {
                "sink_id": "project_archive",
                "sink_kind": "resource_store",
                "sink_role": "archive",
                "status": "pending",
            },
            {
                "sink_id": "team_delivery",
                "sink_kind": "message_channel",
                "sink_role": "delivery",
                "status": "pending",
            },
        ],
        "retry_policy": {"attempt": 1, "max_attempts": 3},
    }


def _successful_request() -> dict[str, object]:
    request = _request()
    planned = build_periodic_report_run(request)
    keys = {
        (receipt["sink_role"], receipt["sink_id"]): receipt["idempotency_key"]
        for receipt in planned["sink_receipts"]
    }
    for receipt in request["sink_receipts"]:  # type: ignore[index]
        identity = (receipt["sink_role"], receipt["sink_id"])
        receipt.update(
            {
                "status": "sent",
                "idempotency_key": keys[identity],
                "receipt_ref": f"receipt:{receipt['sink_role']}/2026-w29",
                "readback_verified": True,
            }
        )
    return request


def test_periodic_report_run_is_deterministic_and_provider_neutral() -> None:
    request = _request()
    first = build_periodic_report_run(request)
    reordered = copy.deepcopy(request)
    reordered["source_snapshots"].reverse()  # type: ignore[union-attr]
    reordered["sink_receipts"].reverse()  # type: ignore[union-attr]
    second = build_periodic_report_run(reordered)

    assert first == second
    assert first["schema_version"] == "periodic_report_v0"
    assert first["run_state"] == {
        "status": "pending",
        "terminal": False,
        "partial": False,
        "unknown": False,
    }
    assert first["boundary"] == {
        "provider_neutral": True,
        "schedule_policy_owned_by_profile": True,
        "source_collection_executes_outside_core": True,
        "rendering_executes_outside_core": True,
        "sink_delivery_executes_outside_core": True,
        "external_writes_performed": False,
        "raw_content_persisted": False,
    }
    encoded = json.dumps(first, sort_keys=True)
    assert "issue" not in encoded.lower()
    assert "pull_request" not in encoded.lower()
    assert "monday" not in encoded.lower()


def test_report_window_uses_chronological_timestamp_order() -> None:
    request = _request()
    request["period_window"] = {
        "start_at": "2026-07-20T00:00:00.100000Z",
        "end_at": "2026-07-20T00:00:00Z",
    }

    with pytest.raises(ValueError, match="start_at must be earlier"):
        build_periodic_report_run(request)


def test_fractional_retry_policy_integer_is_rejected() -> None:
    request = _request()
    request["retry_policy"] = {"attempt": 1.9, "max_attempts": 3}

    with pytest.raises(ValueError, match="must be an integer"):
        build_periodic_report_run(request)


def test_success_requires_exact_sink_idempotency_and_readback() -> None:
    payload = build_periodic_report_run(_successful_request())

    assert payload["run_state"]["status"] == "succeeded"
    assert payload["run_state"]["terminal"] is True
    assert payload["retry"]["allowed"] is False
    assert payload["retry"]["reason"] == "run_succeeded"

    stale = _successful_request()
    stale["sink_receipts"][0]["idempotency_key"] = "delivery_stale"  # type: ignore[index]
    with pytest.raises(ValueError, match="does not match run identity"):
        build_periodic_report_run(stale)

    missing_key = _successful_request()
    missing_key["sink_receipts"][0].pop("idempotency_key")  # type: ignore[index]
    with pytest.raises(ValueError, match="sent receipt requires idempotency_key"):
        build_periodic_report_run(missing_key)

    unread = _successful_request()
    unread["sink_receipts"][1]["readback_verified"] = False  # type: ignore[index]
    with pytest.raises(ValueError, match="verified readback"):
        build_periodic_report_run(unread)


def test_partial_and_unknown_states_preserve_retry_evidence() -> None:
    partial = _successful_request()
    delivery = partial["sink_receipts"][1]  # type: ignore[index]
    delivery.update(
        {
            "status": "failed",
            "retryable": True,
            "receipt_ref": None,
            "readback_verified": False,
        }
    )
    payload = build_periodic_report_run(partial)
    assert payload["run_state"]["status"] == "partial"
    assert payload["retry"]["allowed"] is True
    assert payload["retry"]["next_attempt"] == 2
    assert payload["retry"]["retryable_components"] == ["delivery:team_delivery"]

    unknown = _request()
    unknown["source_snapshots"][0] = {  # type: ignore[index]
        "source_id": "work_items",
        "source_kind": "project_activity",
        "status": "unknown",
        "retryable": True,
    }
    unknown_payload = build_periodic_report_run(unknown)
    assert unknown_payload["run_state"]["status"] == "unknown"
    assert unknown_payload["retry"]["allowed"] is True


def test_raw_content_and_incomplete_sink_roles_are_rejected() -> None:
    raw_request = _request()
    raw_request["source_snapshots"][0]["raw_body"] = "do not persist"  # type: ignore[index]
    with pytest.raises(ValueError, match="forbidden raw/private field"):
        build_periodic_report_run(raw_request)

    private_ref = _request()
    private_ref["source_snapshots"][0]["snapshot_ref"] = (  # type: ignore[index]
        "/private/tmp/secret.log"
    )
    with pytest.raises(ValueError, match="private path or credential-like value"):
        build_periodic_report_run(private_ref)

    missing_delivery = _request()
    missing_delivery["sink_receipts"] = [missing_delivery["sink_receipts"][0]]  # type: ignore[index]
    with pytest.raises(ValueError, match="archive and delivery"):
        build_periodic_report_run(missing_delivery)


def test_periodic_report_cli_composes_json(tmp_path, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")

    assert (
        main(
            [
                "--format",
                "json",
                "periodic-report",
                "compose-run",
                "--request-json",
                str(request_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "periodic_report_v0"
    assert payload["run_state"]["status"] == "pending"


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_periodic_report_json_input_is_bounded(tmp_path, monkeypatch, source):
    monkeypatch.setattr(
        periodic_report_cli,
        "MAX_PERIODIC_REPORT_INPUT_BYTES",
        8,
        raising=False,
    )
    if source == "file":
        request_path = tmp_path / "oversized.json"
        request_path.write_bytes(b'{"ok":1} ')
        source_text = str(request_path)
    else:
        monkeypatch.setattr(
            periodic_report_cli.sys,
            "stdin",
            SimpleNamespace(
                buffer=io.BytesIO(b'{"ok":1} '),
                read=lambda: '{"ok":1} ',
            ),
        )
        source_text = "-"

    with pytest.raises(ValueError, match="periodic report input exceeds"):
        periodic_report_cli._load_json_object(source_text)
