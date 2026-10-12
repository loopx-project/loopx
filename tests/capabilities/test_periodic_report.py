from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
from pathlib import Path

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


class _MeteredStdin(io.StringIO):
    """Text stdin that records how many UTF-8 bytes the reader pulled."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.bytes_read = 0

    def read(self, size: int | None = -1) -> str:
        chunk = super().read(size)
        self.bytes_read += len(chunk.encode("utf-8"))
        return chunk


def _object_of_size(size: int, fill: str = "x") -> str:
    # {"k":"<fill...>"} encoded to exactly `size` UTF-8 bytes.
    room = size - len(b'{"k":""}')
    width = len(fill.encode("utf-8"))
    assert room >= 0 and room % width == 0
    return '{"k":"' + fill * (room // width) + '"}'


@pytest.mark.parametrize("fill", ["x", "\u20ac"])
def test_periodic_report_stdin_input_is_bounded_by_encoded_bytes(
    monkeypatch, fill
) -> None:

    monkeypatch.setattr(sys, "stdin", _MeteredStdin(_object_of_size(32, fill)))
    assert periodic_report_cli._load_json_object("-", 32) == {"k": fill * (24 // len(fill.encode()))}

    # 18 characters but 38 bytes when fill is multibyte: the budget is bytes.
    oversized = _MeteredStdin(_object_of_size(35 if fill == "x" else 38, fill))
    monkeypatch.setattr(sys, "stdin", oversized)
    with pytest.raises(ValueError, match="exceeds the 32-byte limit"):
        periodic_report_cli._load_json_object("-", 32)
    # Refusal happens within one character of the budget, not after buffering.
    assert oversized.bytes_read <= 32 + 4


def test_periodic_report_file_input_is_bounded_and_keeps_strict_utf8(tmp_path) -> None:
    request_path = tmp_path / "request.json"

    request_path.write_text(_object_of_size(32, "\u20ac"), encoding="utf-8")
    assert periodic_report_cli._load_json_object(str(request_path), 32) == {"k": "\u20ac" * 8}

    request_path.write_text(_object_of_size(33), encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds the 32-byte limit"):
        periodic_report_cli._load_json_object(str(request_path), 32)

    # The size guard must not widen the accepted encodings of request files.
    for raw, error in [
        ('{"k":1}'.encode("utf-16"), UnicodeDecodeError),
        (b'{"k":"\xff"}', UnicodeDecodeError),
        (b"\xef\xbb\xbf" + b'{"k":1}', json.JSONDecodeError),
    ]:
        request_path.write_bytes(raw)
        with pytest.raises(error):
            periodic_report_cli._load_json_object(str(request_path), 32)


def test_periodic_report_cli_reads_stdin_and_refuses_oversized_pipe() -> None:
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "periodic-report",
        "compose-run",
        "--request-json",
        "-",
    ]
    repo_root = Path(__file__).resolve().parents[2]

    accepted = subprocess.run(
        command,
        cwd=repo_root,
        input=json.dumps(_request()),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )
    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout)["schema_version"] == "periodic_report_v0"

    refused = subprocess.run(
        command,
        cwd=repo_root,
        input=_object_of_size(16 * 1024 * 1024 + 1).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert refused.returncode == 1
    payload = json.loads(refused.stdout)
    assert payload["ok"] is False
    assert payload["error"] == (
        "periodic-report JSON input exceeds the 16777216-byte limit"
    )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-8-sig"])
def test_periodic_report_main_charges_a_stdin_bom_once(
    monkeypatch, capsys, encoding
) -> None:
    # Embedded main(argv) callers may hand in a BOM-bearing text stream; the
    # budget must equal the stream's real byte size, not one BOM per chunk.
    raw = json.dumps(_request()).encode(encoding)
    argv = ["--format", "json", "periodic-report", "compose-run", "--request-json", "-"]
    for limit, expected in [(len(raw) + 1, 0), (len(raw), 0), (len(raw) - 1, 1)]:
        monkeypatch.setattr(periodic_report_cli, "MAX_INPUT_BYTES", limit)
        monkeypatch.setattr(
            sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding=encoding)
        )
        assert main(argv) == expected
        payload = json.loads(capsys.readouterr().out)
        if expected == 0:
            assert payload["schema_version"] == "periodic_report_v0"
        else:
            assert payload["error"] == (
                f"periodic-report JSON input exceeds the {limit}-byte limit"
            )


_COMPOSE_ARGV = ["--format", "json", "periodic-report", "compose-run", "--request-json"]


def _compose(monkeypatch, capsys, source: str, stdin: str = "", *extra: str):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = main([*_COMPOSE_ARGV, source, *extra])
    return code, json.loads(capsys.readouterr().out)


def test_periodic_report_default_limit_admits_exactly_16_mib(monkeypatch, capsys) -> None:
    limit = 16 * 1024 * 1024
    # Exactly the default passes the size gate; only request validation remains.
    _, payload = _compose(monkeypatch, capsys, "-", _object_of_size(limit))
    assert "byte limit" not in payload.get("error", "")
    code, payload = _compose(monkeypatch, capsys, "-", _object_of_size(limit + 1))
    assert code == 1
    assert payload["error"] == f"periodic-report JSON input exceeds the {limit}-byte limit"


@pytest.mark.parametrize("source", ["stdin", "file"])
def test_periodic_report_max_input_bytes_bounds_both_sources_and_recovers(
    tmp_path, monkeypatch, capsys, source
) -> None:
    def run(text: str, limit: int):
        path = tmp_path / "request.json"
        path.write_text(text, encoding="utf-8")
        if source == "stdin":
            return _compose(monkeypatch, capsys, "-", text, "--max-input-bytes", str(limit))
        return _compose(monkeypatch, capsys, str(path), "", "--max-input-bytes", str(limit))

    text = json.dumps(_request())
    size = len(text.encode("utf-8"))
    for limit, expected in [(size + 1, 0), (size, 0), (size - 1, 1)]:
        code, payload = run(text, limit)
        assert code == expected
        if expected:
            assert payload["error"] == f"periodic-report JSON input exceeds the {limit}-byte limit"
        else:
            assert payload["schema_version"] == "periodic_report_v0"

    # The same request, written compactly, fits the limit it just exceeded.
    compact = json.dumps(_request(), separators=(",", ":"))
    assert len(compact.encode("utf-8")) <= size - 1
    code, payload = run(compact, size - 1)
    assert code == 0 and payload["schema_version"] == "periodic_report_v0"


def test_periodic_report_max_input_bytes_raises_the_default(monkeypatch, capsys) -> None:
    text = json.dumps(_request())
    monkeypatch.setattr(periodic_report_cli, "MAX_INPUT_BYTES", 64)
    code, payload = _compose(monkeypatch, capsys, "-", text)
    assert code == 1 and payload["error"].endswith("exceeds the 64-byte limit")
    code, payload = _compose(monkeypatch, capsys, "-", text, "--max-input-bytes", str(len(text)))
    assert code == 0 and payload["schema_version"] == "periodic_report_v0"


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "1e6", "+5", "", "unlimited"])
def test_periodic_report_rejects_invalid_max_input_bytes(monkeypatch, capsys, value) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_request())))
    with pytest.raises(SystemExit) as exited:
        main([*_COMPOSE_ARGV, "-", f"--max-input-bytes={value}"])
    assert exited.value.code == 2
    assert "must be a positive whole number of bytes" in capsys.readouterr().err
