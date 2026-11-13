"""Installed CLI context continuity; native events are protocol fixtures, not a model."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from test_core_distribution import AGENT, GOAL, _command, _environment
from test_core_distribution import core_python as core_python


def test_installed_context_recovers_partial_delivery_revisions_and_compaction(core_python, tmp_path):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    source = tmp_path / "original.json"
    event = {"session_id": "core-context-session", "cwd": str(tmp_path)}
    environment = _environment(core_python, {"CODEX_THREAD_ID": event["session_id"]})
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    def command(arguments, *, stdin=None, expected=0):
        result = subprocess.run(_command(core_python, [*prefix, *arguments]),
            input=None if stdin is None else json.dumps(stdin), env=environment,
            cwd=tmp_path, capture_output=True, text=True, timeout=45)
        assert result.returncode == expected, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    def hook(name, **fields):
        return command(["quota", "context-hook", "--context-delivery", "on", "--codex-app",
            "--codex-context-dir", str(directory)], stdin={**event, "hook_event_name": name, **fields})

    def state():
        return json.loads((directory / "session.json").read_bytes())

    def acknowledge(pages):
        for page in pages:
            assert hook("PostToolUse", tool_use_id=f"page-{page['offset']}",
                tool_response=json.dumps(page)) == {}

    row = {"schema_version": "todo_item_v0", "todo_id": "todo_context", "text": "Verify the report.",
        "note": "Preserve the complete source and its constraints. " * 150 + "Stop before publication.",
        "content_revision": "revision:one", "claimed_by": AGENT}
    original = {"ok": True, "goal_id": GOAL, "agent_identity": {"agent_id": AGENT},
        "heartbeat_receipt": {"turn_instance_id": "context-fixture"},
        "stop": "Stop before publication.", "opaque_counter": 9007199254740993,
        "agent_todo_summary": {"schema_version": "todo_summary_v0",
            "first_open_items": [row], "backlog_items": [copy.deepcopy(row)],
            "claimed_open_items": [copy.deepcopy(row)]}}

    def project(payload, setting="on"):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        source.write_bytes(raw)
        arguments = ["quota", "context-project", "--capture-file", str(source),
            "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", "context-fixture",
            "--context-delivery", setting, "--codex-app", "--codex-context-dir", str(directory)]
        if setting == "on":
            arguments += ["--max-page-output-bytes", "4000"]
        return command(arguments)

    def read_required(wire, payload, *, budget=4000):
        arguments = shlex.split(wire.get("recovery", wire)["command"])
        pages, contents = [], b""
        while True:
            page = command(arguments[arguments.index("quota"):])
            assert page["ok"] and page["offset"] == len(contents)
            if budget is not None:
                assert len((json.dumps(page, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= budget
            contents += page["content"].encode()
            pages.append(page)
            if page["eof"]:
                break
            arguments = shlex.split(page["next_command"])
        required = json.loads(contents)
        assert required["stop"] == payload["stop"]
        assert required["opaque_counter"] == 9007199254740993
        for lane, expected in payload["agent_todo_summary"].items():
            if lane == "schema_version":
                continue
            values = []
            for item in required["agent_todo_summary"][lane]:
                if "context_ref" in item:
                    item_source = required
                    for part in item["context_ref"].split("/")[1:]:
                        item_source = item_source[int(part)] if isinstance(item_source, list) else item_source[part]
                    values.append(item_source)
                else:
                    values.append(item)
            assert values == expected
        return pages

    assert hook("SessionStart", source="startup") == {}
    # Feature-off preserves even unknown semantics, without acknowledging them.
    off_pages = read_required(project(original, "off"), original, budget=None)
    assert json.loads("".join(page["content"] for page in off_pages)) == original
    assert "candidate" not in state()
    first = project(original)
    assert first["mode"] == "full"
    pages = read_required(first, original)
    assert len(pages) > 1
    acknowledge(pages[:1])
    assert "candidate" not in state()
    # Lost ACK: publishing another view cannot make an incomplete baseline valid.
    unacknowledged = project(original)
    assert unacknowledged["mode"] == "full"
    acknowledge(read_required(unacknowledged, original))
    assert "candidate" in state()
    warm = project(original)
    assert warm["mode"] in {"delta", "full"}  # Complete framing may make full cheaper.
    acknowledge(read_required(warm, original))

    revised = copy.deepcopy(original)
    revised["stop"] = "Stop all publication and await revised approval."
    for lane in ("first_open_items", "backlog_items", "claimed_open_items"):
        revised["agent_todo_summary"][lane][0].update(content_revision="revision:two",
            note="Contrary evidence changed the work. Preserve the revised constraint.")
    changed = project(revised)
    acknowledge(read_required(changed, revised))
    before = state()
    assert hook("PostCompact") == {}
    assert "candidate" not in state() and state()["recovery"] == before["recovery"]
    compacted = project(revised)
    assert compacted["mode"] == "full"
    acknowledge(read_required(compacted, revised))

    # The advertised reader rejects changed source bytes rather than trusting a digest label.
    arguments = shlex.split(compacted["recovery"]["command"])
    capture = state()["recovery"]["path"]
    pinned = Path(capture)
    pinned.write_bytes(pinned.read_bytes() + b" ")
    refused = command(arguments[arguments.index("quota"):], expected=1)
    assert refused["ok"] is False and "content" not in refused
    assert not registry.exists() and not runtime.exists()
