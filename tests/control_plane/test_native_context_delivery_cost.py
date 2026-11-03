"""Native delivery compares emitted references and pages without losing recovery."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from loopx.cli import build_parser
from loopx.cli_commands.quota_capture import native_context_capture_view, read_context_capture
from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError
from loopx.control_plane.quota.turn_envelope import prepare_context_delivery, restore_context_delivery
from loopx.control_plane.turn_driver import codex_context as context
from test_codex_context_lifecycle import deliver, project, setup


def _payload(repeats=24):
    return {
        "ok": True,
        "goal_id": "fixture",
        "requirements": "Keep every acceptance condition and stop boundary. " * repeats,
        "unknown_constraint": {"review_required": True, "threshold": 7},
        "stop": "Stop before publication.",
    }


def _pages(reference):
    """Follow the actual advertised CLI reader, including its page budgets."""
    parser = build_parser()
    command = reference["command"]
    pages = []
    while True:
        args = parser.parse_args(shlex.split(command)[1:])
        page = read_context_capture(args)
        assert page["ok"], page
        pages.append(page)
        if page["eof"]:
            return pages
        command = page["next_command"]


def _emitted_bytes(value):
    # The public JSON printer emits compact UTF-8 plus one newline.
    return len((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode())


def _delivery_cost(reference, pages):
    return _emitted_bytes(reference) + sum(_emitted_bytes(page) for page in pages)


def _full_alternative(payload, projected):
    """Ask the shared codec for a full alternative with the same immutable target."""
    full = prepare_context_delivery(
        payload,
        scope={key: projected["target"][key]
               for key in ("goal_id", "agent_id", "session_id", "context_epoch")},
        capture_id=projected["target"]["capture_id"],
        surface="quota_packet",
    )
    assert full["mode"] == "full" and full["target"] == projected["target"]
    full["recovery"] = projected["recovery"]
    return full


def _same_capture_full(payload, projected, directory):
    return native_context_capture_view(_full_alternative(payload, projected), directory)


def _assert_recovery(view, projected, expected):
    recovery = view["recovery"]
    assert {key: recovery[key] for key in ("path", "sha256")} == {
        key: projected["recovery"][key] for key in ("path", "sha256")
    }
    raw = Path(recovery["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == recovery["sha256"]
    recovered = b"".join(page["content"].encode() for page in _pages(recovery))
    assert recovered == raw
    # JSON text distinguishes 1 from 1.0 and retains the sign of zero.
    assert json.dumps(json.loads(recovered), sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_small_codec_delta_uses_cheaper_complete_native_delivery(tmp_path, monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    event = setup(tmp_path, monkeypatch)
    payload = _payload()
    _, first = project(tmp_path, payload=payload)
    _, projected = project(tmp_path, "warm", payload=payload)
    assert projected["mode"] == "delta", "The counterexample must reach the codec's delta path"
    original = copy.deepcopy(projected)
    before = context._state(tmp_path)
    full = _same_capture_full(payload, projected, tmp_path / "warm")
    full_pages = _pages(full)
    registrations = []
    register = context.register_native_context_view
    index_requests = []
    request = turn_envelope._turn_envelope_result

    def observe_registration(directory, view):
        registrations.append((directory, copy.deepcopy(view)))
        return register(directory, view)

    def observe_index_request(operation, **kwargs):
        if operation == "context_select":
            index_requests.append(copy.deepcopy(kwargs))
        return request(operation, **kwargs)

    with monkeypatch.context() as observing:
        observing.setattr(context, "register_native_context_view", observe_registration)
        observing.setattr(turn_envelope, "_turn_envelope_result", observe_index_request)
        chosen = native_context_capture_view(
            projected, tmp_path / "warm", context_directory=str(tmp_path),
        )
    pages = _pages(chosen)
    assert "read_instruction" in pages[0], "Count the actual first-page guidance too"
    assert _delivery_cost(chosen, pages) <= _delivery_cost(full, full_pages)
    assert chosen["mode"] == "full"
    assert chosen["target"] == projected["target"]
    assert (chosen["path"], chosen["sha256"]) == (full["path"], full["sha256"])
    assert "baseline_reference" not in chosen
    assert projected == original
    assert len(registrations) == 1
    assert len(index_requests) == 1, "Comparing two transports must not plan the same field index twice"
    assert chosen["field_index"] == full["field_index"]
    state = context._state(tmp_path)
    assert state["recovery"] == before["recovery"]
    assert state["pending"]["target"] == projected["target"]
    assert state["pending"]["ancestry"] == 0
    assert state["pending"]["path"] == chosen["path"]
    assert state["pending"]["sha256"] == chosen["sha256"]
    assert state["pending"]["total_bytes"] == chosen["total_bytes"]
    _assert_recovery(chosen, projected, payload)
    deliver(tmp_path, event, pages)
    candidate = context._state(tmp_path)["candidate"]
    assert candidate["ancestry"] == 0 and candidate["target"] == projected["target"]
    assert candidate["capture_file"] == projected["recovery"]["path"]
    assert Path(first["recovery"]["path"]).exists()


def test_large_native_delta_keeps_exact_reconstruction_and_pending_ancestry(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    payload = _payload(2000)
    _, first = project(tmp_path, payload=payload)
    _, projected = project(tmp_path, "warm", payload=payload)
    assert projected["mode"] == "delta"
    original = copy.deepcopy(projected)
    full = _same_capture_full(payload, projected, tmp_path / "warm")
    chosen = native_context_capture_view(
        projected, tmp_path / "warm", context_directory=str(tmp_path),
    )
    pages = _pages(chosen)
    assert chosen["mode"] == "delta"
    assert _delivery_cost(chosen, pages) < _delivery_cost(full, _pages(full))
    assert chosen["target"] == projected["target"] and projected == original
    wire = json.loads("".join(page["content"] for page in pages))
    restored = restore_context_delivery(
        wire,
        baseline={"schema_version": "context_delivery_v1", "mode": "full", "ancestry": 0,
                  "target": first["target"], "context": payload},
        current={"schema_version": "context_delivery_v1", "mode": "full", "ancestry": 0,
                 "target": projected["target"], "context": payload},
    )
    assert restored["mode"] == "delta" and restored["context"] == payload
    state = context._state(tmp_path)
    assert state["pending"]["ancestry"] == projected["ancestry"]
    assert state["pending"]["path"] == chosen["path"]
    assert state["pending"]["sha256"] == chosen["sha256"]
    _assert_recovery(chosen, projected, payload)
    deliver(tmp_path, event, pages)
    assert context._state(tmp_path)["candidate"]["target"] == projected["target"]


def test_compaction_starts_full_delivery_in_a_new_epoch(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    payload = _payload(2000)
    project(tmp_path, payload=payload)
    _, warm = project(tmp_path, "warm", payload=payload)
    before = native_context_capture_view(warm, tmp_path / "warm", context_directory=str(tmp_path))
    assert before["mode"] == "delta"
    deliver(tmp_path, event, _pages(before))
    old_epoch = before["target"]["context_epoch"]
    context.codex_context_hook(str(tmp_path), {**event, "source": "compact"})
    changed = {**payload, "stop": "Stop all work until the new evidence is reviewed."}
    _, projected = project(tmp_path, "after-compact", payload=changed)
    chosen = native_context_capture_view(
        projected, tmp_path / "after-compact", context_directory=str(tmp_path),
    )
    assert chosen["mode"] == "full"
    assert chosen["target"]["context_epoch"] != old_epoch
    assert "baseline_reference" not in chosen
    assert context._state(tmp_path)["pending"]["ancestry"] == 0
    _assert_recovery(chosen, projected, changed)


@pytest.mark.parametrize("number", [1.0, -0.0, 2**60 + 1])
def test_native_cost_selection_keeps_exact_numbers_and_unknown_constraints(tmp_path, monkeypatch, number):
    setup(tmp_path, monkeypatch)
    payload = {**_payload(), "measurement": number,
               "future_obligation": {"enabled": False, "requires": ["Keep this unknown constraint."]}}
    project(tmp_path, payload=payload)
    _, projected = project(tmp_path, "warm", payload=payload)
    chosen = native_context_capture_view(
        projected, tmp_path / "warm", context_directory=str(tmp_path),
    )
    _assert_recovery(chosen, projected, payload)


def test_disabled_native_context_stays_unwrapped_and_exact(tmp_path):
    payload = {**_payload(), "measurement": 2**60 + 1}
    unused = tmp_path / "unused"
    projected = context.project_codex_context(
        payload, directory=None, capture_directory=unused,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="off",
    )
    assert projected is payload and not unused.exists()
    view = native_context_capture_view(projected, tmp_path)
    assert view["mode"] == "full" and "target" not in view and "baseline_reference" not in view
    assert json.loads("".join(page["content"] for page in _pages(view))) == payload
    assert not (tmp_path / "session.json").exists()


def test_readable_full_fallback_keeps_original_metadata_under_a_small_ceiling(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    payload = _payload()
    _, first = project(tmp_path, payload=payload)
    changed = {**payload, "stop": "Wait for independent review before any publication."}
    _, projected = project(tmp_path, "warm", payload=changed)
    assert projected["mode"] == "delta"
    metadata = {"capture_sha256": first["recovery"]["sha256"],
                "recovery": copy.deepcopy(projected["recovery"])}
    original_metadata = copy.deepcopy(metadata)
    full = _full_alternative(changed, projected)

    # Find the public full reference's smallest supported ceiling. Deriving it
    # through the API keeps this failure boundary independent of temp-path length.
    high = _emitted_bytes(native_context_capture_view(full, tmp_path / "warm", metadata=metadata))
    low = 1
    while low + 1 < high:
        middle = (low + high) // 2
        try:
            native_context_capture_view(full, tmp_path / "warm", metadata=metadata,
                                        max_page_output_bytes=middle)
        except QuotaDecisionTransportError:
            low = middle
        else:
            high = middle
    chosen = native_context_capture_view(
        projected, tmp_path / "warm", context_directory=str(tmp_path),
        metadata=metadata, max_page_output_bytes=high,
    )
    assert chosen["mode"] == "full" and chosen["target"] == projected["target"]
    assert chosen["capture_sha256"] == first["recovery"]["sha256"]
    assert chosen["capture_sha256"] != chosen["recovery"]["sha256"]
    assert metadata == original_metadata
    assert all(_emitted_bytes(value) <= high for value in [chosen, *_pages(chosen)])
    assert not (tmp_path / "warm" / "delivery.json").exists()
    _assert_recovery(chosen, projected, changed)


def test_unreadable_alternatives_do_not_publish_or_replace_current_recovery(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    payload = _payload()
    project(tmp_path, payload=payload)
    _, projected = project(tmp_path, "warm", payload=payload)
    assert projected["mode"] == "delta"
    before = (tmp_path / "session.json").read_bytes()
    original = copy.deepcopy(projected)
    with pytest.raises(QuotaDecisionTransportError):
        native_context_capture_view(
            projected, tmp_path / "warm", context_directory=str(tmp_path), max_page_output_bytes=1,
        )
    assert (tmp_path / "session.json").read_bytes() == before
    assert projected == original and not (tmp_path / "warm" / "delivery.json").exists()
    _assert_recovery({"recovery": projected["recovery"]}, projected, payload)


def test_native_cost_matches_public_pages_through_a_parent_alias(tmp_path):
    from loopx.cli_commands.quota_capture import _native_context_read_bytes

    physical = tmp_path / ("physical-capture-parent-" * 8)
    physical.mkdir(mode=0o700)
    alias = tmp_path / "a"
    alias.symlink_to(physical, target_is_directory=True)
    payload = {"ok": True, "requirements": 'Preserve \"exact\" 条件 🧪.\n' * 4000,
               "stop": "Keep the last constraint after every page."}
    reference = native_context_capture_view(payload, alias, max_page_output_bytes=5000)
    assert Path(reference["path"]) == alias / "delivery.json"
    capture = physical / "delivery.json"
    assert not capture.is_symlink()
    raw = capture.read_bytes()
    args = build_parser().parse_args(shlex.split(reference["command"])[1:])
    pages = _pages(reference)
    assert len(pages) > 1 and "read_instruction" in pages[0]
    # The first command preserves the alias, but real continuation commands use
    # the longer physical parent. Cost all of them with the public reader.
    continuation = build_parser().parse_args(shlex.split(pages[0]["next_command"])[1:])
    assert Path(continuation.capture_file) == capture
    assert b"".join(page["content"].encode() for page in pages) == raw
    assert _native_context_read_bytes(
        reference, raw, page_bytes=args.page_bytes, output_bytes=args.max_page_output_bytes,
    ) == _delivery_cost(reference, pages)


def test_readable_delta_is_kept_when_the_full_capture_exceeds_the_reader_limit(tmp_path, monkeypatch):
    from loopx.control_plane.effect_runtime import MAX_REQUEST_BYTES

    def project_boundary(directory, unchanged):
        directory.mkdir(mode=0o700)
        setup(directory, monkeypatch)
        before = {"ok": True, "requirements": "x" * unchanged, "change": "before"}
        project(directory, "base", payload=before)
        current = {**before, "change": ""}
        current["change"] = "y" * (MAX_REQUEST_BYTES + 100 - _emitted_bytes(current))
        _, wire = project(directory, "warm", payload=current)
        assert wire["mode"] == "delta"
        return current, wire

    # Equal-length directories keep capture bindings the same size. Measure the
    # real codec once so different temporary parent lengths cannot accidentally
    # turn this into two unreadable captures or a substantially smaller delta.
    _, probe = project_boundary(tmp_path / "sizing", 4000)
    unchanged = 4000 + _emitted_bytes(probe) - (MAX_REQUEST_BYTES - 128)
    directory = tmp_path / "actual"
    current, projected = project_boundary(directory, unchanged)
    assert _emitted_bytes(current) == MAX_REQUEST_BYTES + 100
    assert MAX_REQUEST_BYTES - 256 < _emitted_bytes(projected) <= MAX_REQUEST_BYTES
    capture = Path(projected["recovery"]["path"])
    original = capture.read_bytes()

    chosen = native_context_capture_view(
        projected, directory / "warm", context_directory=str(directory),
    )
    assert chosen["mode"] == "delta" and chosen["target"] == projected["target"]
    wire = b"".join(page["content"].encode() for page in _pages(chosen))
    assert json.loads(wire) == projected
    assert capture.read_bytes() == original
    args = build_parser().parse_args(shlex.split(chosen["recovery"]["command"])[1:])
    assert not read_context_capture(args)["ok"], "The full-file limit must remain enforced"
