"""First-read presentation does not replace exact Goal source recovery."""
import hashlib
from html import escape
import json
import shlex

import pytest

from loopx.control_plane.turn_driver.codex_context import _state, codex_context_hook
from loopx.control_plane.turn_driver.native_context_selection import verified_context_selection
from tests.control_plane.test_quota_settlement_cli import _run_cli


@pytest.mark.parametrize("conflict", [False, True], ids=["generated-duplicate", "different-objectives"])
def test_saved_goal_narrative_presentation_replays_and_preserves_literal_source(tmp_path, monkeypatch, conflict):
    runtime, registry, journal = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "native"
    journal.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "narrative-session")
    codex_context_hook(str(journal), {"hook_event_name": "SessionStart", "source": "startup",
        "session_id": "narrative-session", "cwd": str(tmp_path)})
    objective = "Retain constraints on A & B > C. 保留原要求🙂\n" * 98
    frontmatter = json.dumps(objective, ensure_ascii=False)
    body = "\n".join("> " + escape(line, quote=False) for line in objective.splitlines())
    if conflict:
        body += "\n> Stop before writing outside the worktree."
    expected_text = ("---\nstatus: active\n---\n\n# Active Goal State\n\n## Objective\n\n"
        + body + "\n\n## Stop\n\nWait if there is no approved action.\n")
    original_text = expected_text.replace("status: active\n", f"status: active\nobjective: {frontmatter}\n", 1)
    receipt = {"turn_instance_id": "original-turn"}
    content = {"text": original_text, "full_source": True, "state_file": "goal.md",
        "revision": "sha256:" + hashlib.sha256(original_text.encode()).hexdigest()}
    capture = tmp_path / "original.json"
    capture.write_text(json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": receipt, "effective_action": "normal_run",
        "interaction_contract": {"schema_version": "loopx_interaction_contract_v0", "agent_channel": {
            "work_context": {"sources": [{"source": "goal_state", "ordering": "before_work", "content": content}]}}}},
        ensure_ascii=False))
    raw = capture.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    source = ["--capture-file", str(capture), "--capture-sha256", digest]
    project = ["quota", "context-project", *source,
        "--goal-id", "g", "--agent-id", "a", "--turn-instance-id", "original-turn",
        "--codex-app", "--context-delivery", "on", "--codex-context-dir", str(journal),
        "--max-page-output-bytes", "6000"]

    def cli(*args):
        code, result = _run_cli(registry, runtime, *args, cwd=tmp_path)
        assert code == 0 and result["ok"], result
        return result

    def follow(command):
        argv = shlex.split(command)
        return cli(*argv[argv.index("quota"):])

    def finish(page):
        joined = bytearray()
        while True:
            assert page["offset"] == len(joined)
            joined.extend(page["content"].encode())
            if page["eof"]:
                assert len(joined) == page["total_bytes"]
                return bytes(joined)
            page = follow(page["next_command"])

    pointer = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    first = cli(*project, "--initial-read")
    assert not first["eof"], "the 6000-byte response must exercise continuation"
    selection = finish(first)
    assert finish(follow(first["navigation"]["first_read_command"])) == selection
    rows = {row["pointer"]: row for row in json.loads(selection)["selections"]}
    assert rows["/heartbeat_receipt"]["value"] == receipt
    assert rows[pointer]["value"] == (original_text if conflict else expected_text)
    assert ("presentation" in rows[pointer]) is not conflict

    observed = verified_context_selection(_state(journal), first)
    assert observed is not None and observed["read_sections"]["/heartbeat_receipt"]
    assert (pointer in observed["read_sections"]) is conflict
    literal = [{"pointer": pointer, "value": original_text}]
    for page in [cli("quota", "context-read", *source, "--select", pointer), cli(*project, "--select", pointer)]:
        assert json.loads(finish(page))["selections"] == literal
    assert capture.read_bytes() == raw
    assert not registry.exists() and not runtime.exists()
