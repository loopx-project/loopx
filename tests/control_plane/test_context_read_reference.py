"""Reader references hide repeated digests without weakening byte verification."""
from argparse import Namespace
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from loopx.cli_commands.quota_capture import model_context_reference, native_context_capture_view, read_context_capture
from loopx.control_plane.turn_driver.codex_context import _context_pages
from loopx.control_plane.turn_driver.decision_capture import (
    capture_page_range, read_context_read_reference, write_context_read_reference,
)


def read(command, **overrides):
    words = shlex.split(command)
    args = dict(format="json", offset=0, page_bytes=64000, max_page_output_bytes=64000)
    for name in ("reference-file", "capture-file", "capture-sha256", "offset", "page-bytes", "max-page-output-bytes"):
        if "--" + name in words:
            value = words[words.index("--" + name) + 1]
            args[name.replace("-", "_")] = int(value) if name in {"offset", "page-bytes", "max-page-output-bytes"} else value
    args["initial_read"] = "--initial-read" in words
    args.update(overrides)
    return read_context_capture(Namespace(**args))


def fixture(tmp_path):
    payload = {"ok": True, "interaction_contract": {"schema_version": "loopx_interaction_contract_v0"},
        "goal_boundary": {"stop": "Do not extend the original budget."},
        "selected_todo": {"todo_id": "todo_fixture", "text": "Compare 原始条件。" * 900},
        "criterion": {"value": 9007199254740993, "stop": "No publication is authorized."}}
    view = native_context_capture_view(payload, tmp_path)
    return payload, view, model_context_reference(view)


def test_native_navigation_keeps_digests_in_immutable_reference(tmp_path):
    payload, machine, model = fixture(tmp_path)
    raw = Path(machine["path"]).read_bytes()
    assert json.loads(raw) == payload
    assert machine["sha256"] == hashlib.sha256(raw).hexdigest()
    assert machine["sha256"] not in json.dumps(model)
    assert "--capture-sha256" not in json.dumps(model)
    _, descriptor = read_context_read_reference(model["reference_file"])
    assert descriptor["path"] == machine["path"] and descriptor["sha256"] == machine["sha256"]
    assert descriptor["navigation"] == machine  # complete machine metadata remains recoverable
    assert Path(model["reference_file"]).stat().st_mode & 0o077 == 0
    # Same immutable source and full-page observer compatibility.
    page = read(model["command"])
    assert page["schema_version"] == "context_capture_page_v1"
    assert capture_page_range(page, raw=raw, digest=machine["sha256"]) == (0, len(raw))


def test_selected_continuations_hide_both_digests_and_preserve_exact_values(tmp_path):
    payload, machine, model = fixture(tmp_path)
    page = read(model["recovery"]["command"], select=["/selected_todo", "/criterion"],
        max_page_output_bytes=3000, page_bytes=3000)
    parts = []
    while True:
        assert page["ok"] and page["schema_version"] == "context_capture_selection_page_v1"
        assert not any(key in page for key in ("sha256", "selection_sha256", "selected_pointers"))
        assert _context_pages(page) == []  # selected EOF is never a full-input receipt
        parts.append(page["content"])
        if page["eof"]:
            break
        command = page["next_command"]
        assert "--selection-sha256" not in command and "--capture-sha256" not in command
        assert page["next_offset"] == len("".join(parts).encode())
        page = read(command)
    values = {item["pointer"]: item["value"] for item in json.loads("".join(parts))["selections"]}
    assert values == {"/selected_todo": payload["selected_todo"], "/criterion": payload["criterion"]}
    _, descriptor = read_context_read_reference(page["reference_file"])
    assert descriptor["sha256"] == machine["sha256"] and len(descriptor["selection"]["sha256"]) == 64
    changed = read(model["command"], reference_file=page["reference_file"], select=["/criterion"])
    assert changed["ok"] is False and "cannot change selectors" in changed["reason"]


def test_initial_reference_rejects_changed_selection_profile(tmp_path, monkeypatch):
    _, _, model = fixture(tmp_path)
    first = read(model["first_read_command"], max_page_output_bytes=3000, page_bytes=3000)
    assert first["ok"] and not first["eof"]
    from loopx.control_plane.quota import turn_envelope

    original = turn_envelope._turn_envelope_result

    def changed(operation, **kwargs):
        result = original(operation, **kwargs)
        result["selections"].pop()
        return result

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", changed)
    rejected = read(first["next_command"])
    assert rejected["ok"] is False and "digest changed" in rejected["reason"]
    assert "content" not in rejected


@pytest.mark.parametrize("damage", ["capture", "selection", "symlink", "permissions"])
def test_reference_damage_fails_closed(tmp_path, damage):
    _, machine, model = fixture(tmp_path)
    page = read(model["command"], select=["/criterion"])
    reference = Path(page["reference_file"])
    if damage == "capture":
        Path(machine["path"]).write_text('{"changed":true}\n')
    elif damage == "selection":
        payload = json.loads(reference.read_text())
        payload["selection"]["sha256"] = "0" * 64
        reference.write_text(json.dumps(payload))
    elif damage == "symlink":
        original = reference.with_suffix(".saved")
        reference.rename(original)
        reference.symlink_to(original)
    else:
        reference.chmod(0o644)
    rejected = read(model["command"], reference_file=str(reference))
    assert rejected["ok"] is False and "content" not in rejected


def test_reference_publication_never_overwrites_an_existing_document(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text('{}\n')
    original = write_context_read_reference(path, "a" * 64)
    assert write_context_read_reference(path, "a" * 64) == original
    with pytest.raises(ValueError, match="reference changed"):
        write_context_read_reference(path, "b" * 64)
