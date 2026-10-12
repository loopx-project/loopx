from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.content_ops.item_lifecycle import (
    apply_content_ops_item_event,
    build_content_ops_item,
    build_content_ops_queue_status_packet,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64


def _item(item_id: str, item_kind: str = "post") -> dict[str, object]:
    return build_content_ops_item(
        item_id=item_id,
        item_kind=item_kind,
        channel="x",
        content_digest=DIGEST,
        content_ref=f"draft:{item_id}",
        source_refs=["source:public-feed"],
        created_at="2026-08-10T02:00:00+08:00",
    )


def _review_ready(item: dict[str, object]) -> dict[str, object]:
    return apply_content_ops_item_event(
        item,
        {
            "event_id": f"review:{item['item_id']}",
            "action": "submit_review",
            "expected_state": item["state"],
            "expected_revision": item["revision"],
            "occurred_at": "2026-08-10T02:05:00+08:00",
            "payload": {},
        },
    )["item"]


def _draft(item: dict[str, object]) -> dict[str, object]:
    return apply_content_ops_item_event(
        item,
        {
            "event_id": f"draft:{item['item_id']}",
            "action": "revise",
            "expected_state": item["state"],
            "expected_revision": item["revision"],
            "occurred_at": "2026-08-10T02:04:00+08:00",
            "payload": {
                "content_digest": item["content_digest"],
                "content_ref": item["content_ref"],
            },
        },
    )["item"]


def test_queue_projection_orders_actionable_items_and_counts_states() -> None:
    review = _review_ready(_item("community-recap-v1"))
    draft = _draft(_item("runta-post-v1"))
    packet = build_content_ops_queue_status_packet(
        items=[review, draft],
        queue_id="x-test-queue",
        generated_at="2026-08-10T02:10:00+08:00",
    )

    projection = packet["projection"]
    assert projection["queue_id"] == "x-test-queue"
    assert projection["item_count"] == 2
    assert projection["counts"] == {"review_ready": 1, "draft": 1}
    assert projection["terminal_count"] == 0
    assert projection["next_action"]["item_id"] == "community-recap-v1"
    assert projection["items"][0]["priority_index"] == 1
    assert projection["items"][1]["priority_index"] == 2
    assert packet["external_reads_performed"] is False
    assert packet["external_writes_performed"] is False
    assert packet["autopublish_allowed"] is False


def test_queue_projection_is_idle_when_all_items_are_terminal() -> None:
    item = apply_content_ops_item_event(
        _item("old-post-v1"),
        {
            "event_id": "skip:old-post-v1",
            "action": "skip",
            "expected_state": "captured",
            "expected_revision": 1,
            "occurred_at": "2026-08-10T02:05:00+08:00",
            "payload": {"reason": "Superseded by a newer draft."},
        },
    )["item"]

    projection = build_content_ops_queue_status_packet(
        items=[item],
        queue_id="x-test-queue",
        generated_at="2026-08-10T02:10:00+08:00",
    )["projection"]

    assert projection["terminal_count"] == 1
    assert projection["next_action"] is None


def test_queue_projection_rejects_unknown_item_fields() -> None:
    item = dict(_item("bad-item-v1"))
    item["draft_body"] = "must stay outside LoopX state"

    with pytest.raises(ValueError, match="unsupported fields"):
        build_content_ops_queue_status_packet(
            items=[item],
            queue_id="x-test-queue",
            generated_at="2026-08-10T02:10:00+08:00",
        )


def test_queue_status_cli_projects_items_without_external_effects(
    tmp_path: Path,
) -> None:
    item = _review_ready(_item("community-recap-v1"))
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "content-ops",
            "queue-status",
            "--item-json",
            str(item_path),
            "--queue-id",
            "x-test-queue",
            "--generated-at",
            "2026-08-10T02:10:00+08:00",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"
    assert payload["external_writes_performed"] is False


def _queue_status_from_stdin(stdin: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "content-ops",
            "queue-status",
            "--item-json",
            "-",
            "--queue-id",
            "x-test-queue",
            "--generated-at",
            "2026-08-10T02:10:00+08:00",
        ],
        cwd=REPO_ROOT,
        input=stdin,
        capture_output=True,
        check=False,
    )


def test_queue_status_cli_reads_stdin_within_the_input_limit() -> None:
    item = _review_ready(_item("community-recap-v1"))
    result = _queue_status_from_stdin(json.dumps(item).encode("utf-8"))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"


def test_queue_status_cli_refuses_oversized_stdin_before_parsing() -> None:
    # Not valid JSON either: the size refusal must come before json.loads.
    result = _queue_status_from_stdin(b"{" + b" " * (16 * 1024 * 1024))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == (
        "content-ops stdin JSON input exceeds the 16777216-byte limit"
    )


def test_queue_status_main_reads_text_only_stdin_up_to_the_byte_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from loopx.capabilities.content_ops import cli as content_ops_cli
    from loopx.cli import main

    item = _review_ready(_item("community-recap-v1"))
    text = json.dumps(item)
    budget = len(text.encode("utf-8"))
    argv = [
        "--format",
        "json",
        "content-ops",
        "queue-status",
        "--item-json",
        "-",
        "--queue-id",
        "x-test-queue",
        "--generated-at",
        "2026-08-10T02:10:00+08:00",
    ]

    # In-process callers may replace stdin with a plain text stream.
    monkeypatch.setattr(content_ops_cli, "MAX_INPUT_BYTES", budget)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"

    monkeypatch.setattr(content_ops_cli, "MAX_INPUT_BYTES", budget - 1)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    assert main(argv) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == (
        f"content-ops stdin JSON input exceeds the {budget - 1}-byte limit"
    )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-8-sig"])
def test_queue_status_main_charges_a_stdin_bom_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    encoding: str,
) -> None:
    from loopx.capabilities.content_ops import cli as content_ops_cli
    from loopx.cli import main

    raw = json.dumps(_review_ready(_item("community-recap-v1"))).encode(encoding)
    argv = [
        "--format",
        "json",
        "content-ops",
        "queue-status",
        "--item-json",
        "-",
        "--queue-id",
        "x-test-queue",
        "--generated-at",
        "2026-08-10T02:10:00+08:00",
    ]
    for limit, expected in [(len(raw) + 1, 0), (len(raw), 0), (len(raw) - 1, 1)]:
        monkeypatch.setattr(content_ops_cli, "MAX_INPUT_BYTES", limit)
        monkeypatch.setattr(
            sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding=encoding)
        )
        assert main(argv) == expected
        payload = json.loads(capsys.readouterr().out)
        if expected == 0:
            assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"
        else:
            assert payload["error"] == (
                f"content-ops stdin JSON input exceeds the {limit}-byte limit"
            )


_QUEUE_ARGV = [
    "--format",
    "json",
    "content-ops",
    "queue-status",
    "--queue-id",
    "x-test-queue",
    "--generated-at",
    "2026-08-10T02:10:00+08:00",
    "--item-json",
]


def _queue_main(monkeypatch, capsys, source: str, stdin: str = "", *extra: str):
    from loopx.cli import main

    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = main([*_QUEUE_ARGV, source, *extra])
    return code, json.loads(capsys.readouterr().out)


def test_queue_status_max_input_bytes_bounds_stdin_and_recovers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    item = _review_ready(_item("community-recap-v1"))
    text = json.dumps(item)
    size = len(text.encode("utf-8"))
    for limit, expected in [(size + 1, 0), (size, 0), (size - 1, 1)]:
        code, payload = _queue_main(monkeypatch, capsys, "-", text, "--max-input-bytes", str(limit))
        assert code == expected
        if expected:
            assert payload["error"] == f"content-ops stdin JSON input exceeds the {limit}-byte limit"
        else:
            assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"

    compact = json.dumps(item, separators=(",", ":"))
    assert len(compact.encode("utf-8")) <= size - 1
    code, payload = _queue_main(monkeypatch, capsys, "-", compact, "--max-input-bytes", str(size - 1))
    assert code == 0
    assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"


def test_queue_status_max_input_bytes_raises_the_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from loopx.capabilities.content_ops import cli as content_ops_cli

    text = json.dumps(_review_ready(_item("community-recap-v1")))
    monkeypatch.setattr(content_ops_cli, "MAX_INPUT_BYTES", 64)
    code, payload = _queue_main(monkeypatch, capsys, "-", text)
    assert code == 1 and payload["error"].endswith("exceeds the 64-byte limit")
    code, payload = _queue_main(monkeypatch, capsys, "-", text, "--max-input-bytes", str(len(text)))
    assert code == 0
    assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"


def test_queue_status_max_input_bytes_leaves_item_files_unbounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The content-ops limit covers stdin only; file inputs keep their prior scope.
    path = tmp_path / "item.json"
    path.write_text(json.dumps(_review_ready(_item("community-recap-v1"))), encoding="utf-8")
    code, payload = _queue_main(monkeypatch, capsys, str(path), "", "--max-input-bytes", "8")
    assert code == 0
    assert payload["projection"]["next_action"]["item_id"] == "community-recap-v1"


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "1e6", "+5", "", "unlimited"])
def test_queue_status_rejects_invalid_max_input_bytes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    from loopx.cli import main

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    with pytest.raises(SystemExit) as exited:
        main([*_QUEUE_ARGV, "-", f"--max-input-bytes={value}"])
    assert exited.value.code == 2
    assert "must be a positive whole number of bytes" in capsys.readouterr().err
