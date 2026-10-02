from __future__ import annotations

import json
import re
import sys

import pytest
from pathlib import Path

from loopx.chat_activity import (
    COMMAND_VERBS,
    REASONING_UPDATE_INTERVAL_SEC,
    STEP_KINDS,
    STEP_STATES,
    CodexActivitySteps,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _command(command: str, actions: list[dict] | None = None, **extra) -> dict:
    return {"type": "commandExecution", "id": "exec-1", "command": command,
            "commandActions": actions or [{"type": "unknown", "command": command}], **extra}


def test_command_step_names_the_intent_and_keeps_the_full_command_behind_details():
    steps = CodexActivitySteps(protected_paths=["/work/project"])
    item = _command(
        "/bin/zsh -lc \"rg -n owner /work/project/docs | head\"",
        [{"type": "search", "command": "rg -n owner /work/project/docs", "query": "owner", "path": "/work/project/docs"}],
        status="inProgress",
    )
    running = steps.started(item)
    assert running == {"id": "exec-1", "kind": "command", "state": "running", "verb": "search",
                       "title": "owner", "detail": "rg -n owner docs | head"}
    done = steps.completed({**item, "status": "completed", "exitCode": 0, "durationMs": 42})
    assert (done["state"], done["exit_code"], done["duration_ms"]) == ("completed", 0, 42)


def test_paths_outside_the_project_stay_withheld():
    title = CodexActivitySteps(protected_paths=["/work/project"]).started(_command("diff /work/project/a.md /home/other/b.md"))["title"]
    assert title == "diff a.md [local-path]"
    assert CodexActivitySteps(protected_paths=["/work/project/"]).started(_command("cd /work/project && ls"))["title"] == "cd . && ls"


def test_unparsed_command_shows_the_unwrapped_command_itself():
    step = CodexActivitySteps().started(_command("bash -lc 'npm run build && npm test'"))
    assert (step["verb"], step["title"]) == ("run", "npm run build && npm test")
    assert "detail" not in step, "A short command needs no second copy"


def test_failed_items_are_failed_steps_not_completions():
    steps = CodexActivitySteps()
    assert steps.completed({**_command("false"), "status": "completed", "exitCode": 1})["state"] == "failed"
    assert steps.completed({"type": "mcpToolCall", "id": "t", "server": "s", "tool": "x", "status": "failed"})["state"] == "failed"
    assert steps.completed({"type": "dynamicToolCall", "id": "d", "tool": "x", "success": False})["state"] == "failed"
    assert steps.completed({"type": "fileChange", "id": "f", "changes": [], "status": "declined"})["state"] == "failed"


def test_credentials_in_commands_are_masked_before_replay():
    title = CodexActivitySteps().started(_command(
        "curl -H 'Authorization: Bearer abc.def' --api-key=k123 https://x.test?q=1 "
        "&& GH_TOKEN=ghp_abcdefghijklmnop123 gh pr list --password hunter2 sk-proj-abcdefghijklmnop"
    ))["title"]
    for secret in ("abc.def", "k123", "ghp_abcdefghijklmnop123", "hunter2", "sk-proj-abcdefghijklmnop"):
        assert secret not in title
    assert "gh pr list" in title and "https://x.test" in title


def test_tools_searches_and_file_changes_carry_names_not_payloads():
    steps = CodexActivitySteps(protected_paths=["/work/project"])
    tool = steps.started({"type": "mcpToolCall", "id": "t1", "server": "loopx", "tool": "todo_list",
                          "arguments": {"secret": "do-not-show"}, "status": "inProgress"})
    assert tool == {"id": "t1", "kind": "tool", "state": "running", "title": "loopx · todo_list"}
    search = steps.started({"type": "webSearch", "id": "w1", "query": "release owner policy"})
    assert search["title"] == "release owner policy"
    files = steps.completed({"type": "fileChange", "id": "f1", "status": "completed", "changes": [
        {"path": "/work/project/a.md", "kind": {"type": "update"}, "diff": "+secret line"},
        {"path": "/work/project/b.md", "kind": {"type": "add"}, "diff": "+other"}]})
    assert (files["title"], files["count"], files["detail"]) == ("a.md", 2, "a.md\nb.md")
    assert "secret" not in str(files) and "do-not-show" not in str(tool)


def test_user_and_answer_items_are_not_steps():
    steps = CodexActivitySteps()
    for item in ({"type": "userMessage", "id": "u"}, {"type": "agentMessage", "id": "m", "text": "hi"},
                 {"type": "futureItem", "id": "z"}, None, "text"):
        assert steps.started(item) is None and steps.completed(item) is None


def test_reasoning_streams_throttled_and_prefers_the_model_summary():
    clock = _Clock()
    steps = CodexActivitySteps(clock=clock)
    assert steps.started({"type": "reasoning", "id": "rs_1", "summary": [], "content": []}) == {
        "id": "rs_1", "kind": "reasoning", "state": "running", "title": ""}
    assert steps.reasoning_delta("rs_1", "Checking", summary=False, index=0) is None, "No single-token flash"
    clock.now += REASONING_UPDATE_INTERVAL_SEC
    first = steps.reasoning_delta("rs_1", " the owner", summary=False, index=0)
    assert (first["title"], first["detail"]) == ("Checking the owner", "Checking the owner")
    assert steps.reasoning_delta("rs_1", " list.\nThen compare. Done.", summary=False, index=0) is None
    steps.reasoning_delta("rs_1", "**Owner check**\n\nComparing release owners.", summary=True, index=0)
    clock.now += 2
    done = steps.completed({"type": "reasoning", "id": "rs_1", "summary": [], "content": []})
    assert (done["state"], done["title"], done["duration_ms"]) == ("completed", "Owner check", 3500)
    assert done["detail"] == "**Owner check**\n\nComparing release owners."


def test_reasoning_without_exposed_text_is_a_timed_step_only():
    clock = _Clock()
    steps = CodexActivitySteps(clock=clock)
    steps.started({"type": "reasoning", "id": "rs_2", "summary": [], "content": []})
    clock.now += 12
    assert steps.completed({"type": "reasoning", "id": "rs_2", "summary": [], "content": []}) == {
        "id": "rs_2", "kind": "reasoning", "state": "completed", "title": "", "duration_ms": 12000}


def test_step_ids_and_text_are_bounded():
    step = CodexActivitySteps().started(_command("echo " + "x" * 5000) | {"id": "bad id/" * 40})
    assert step["id"].startswith("step_") and len(step["id"]) == 21
    assert len(step["title"]) == 160 and len(step["detail"]) == 4000


def test_step_vocabulary_is_closed():
    steps = CodexActivitySteps()
    produced = [
        steps.started(_command("ls")),
        steps.started({"type": "reasoning", "id": "r"}),
        steps.started({"type": "mcpToolCall", "id": "t", "tool": "x"}),
        steps.started({"type": "webSearch", "id": "w", "query": "q"}),
        steps.completed({"type": "fileChange", "id": "f", "changes": []}),
    ]
    assert {step["kind"] for step in produced} == set(STEP_KINDS)
    assert all(step["state"] in STEP_STATES for step in produced)
    assert produced[0]["verb"] in COMMAND_VERBS


def test_dashboard_reader_mirrors_the_step_vocabulary():
    source = (Path(__file__).resolve().parents[1] / "apps/presentation/dashboard/src/data/turn-steps.ts").read_text()

    def mirrored(name: str) -> tuple[str, ...]:
        match = re.search(rf"export const {name} = \[([^\]]*)\] as const;", source)
        assert match, name
        return tuple(re.findall(r'"([^"]+)"', match.group(1)))

    assert mirrored("TURN_STEP_KINDS") == STEP_KINDS
    assert mirrored("TURN_STEP_STATES") == STEP_STATES
    assert mirrored("TURN_COMMAND_VERBS") == COMMAND_VERBS


@pytest.mark.parametrize("value", [
    "--token 'example value'", '--password="example value"',
    'API_KEY="example value"', "--secret 'example'\\ value", "--token 'example 'value",
    "--token 'example value", '--token "example value',
])
def test_quoted_credentials_are_masked_in_every_visible_text(value):
    steps = CodexActivitySteps()
    text = "curl " + value + " https://example.test/notes"
    command = steps.started(_command(text, [{"type": "search", "query": text}]))
    thought = steps.completed({"type": "reasoning", "id": "r", "content": [text]})
    for step in (command, thought):
        assert all(part not in step[field] for part in ("example ", "value") for field in ("title", "detail"))
        assert "***" in step["title"] and "***" in step["detail"]


@pytest.mark.parametrize("path", [
    "/opt/example/notes.md", "/etc/example/config", "/srv/example/data", "path:/etc/example/config",
    '"/opt/example/private notes.md"', "'/etc/example/private notes.md'",
    r"C:\example\notes.md", "file:///opt/example/notes.md", "/work/project-other/notes.md",
])
def test_nonproject_paths_are_withheld_across_commands_files_and_thoughts(path):
    steps = CodexActivitySteps(protected_paths=["/work/project"])
    text = "Inspect " + path
    projections = [
        steps.started(_command(text)),
        steps.completed({"type": "reasoning", "id": "r", "summary": [text]}),
        steps.completed({"type": "fileChange", "id": "f", "changes": [{"path": path}]}),
    ]
    for projection in projections:
        visible = json.dumps(projection)
        assert "example" not in visible and "project-other" not in visible
        assert "[local-path]" in visible


def test_step_redaction_preserves_urls_relative_paths_and_project_path_boundaries():
    steps = CodexActivitySteps(protected_paths=["/work/project"])
    text = "cat /work/project/docs/notes.md docs/relative.md ../notes.md https://example.test/work/project/notes"
    assert steps.started(_command(text))["title"] == (
        "cat docs/notes.md docs/relative.md ../notes.md https://example.test/work/project/notes"
    )
    assert steps.started(_command('cat "/work/project/private notes.md"'))["title"] == 'cat "private notes.md"'


def test_private_step_text_is_clean_before_real_transport_store_and_reopened_replay(monkeypatch, tmp_path):
    from loopx import chat_agent
    from loopx.chat_store import ChatSessionStore

    cases = [
        _command("curl --token 'example value' https://example.test/notes"),
        _command('API_KEY="example value" curl https://example.test/notes'),
        _command("cat /opt/example/notes.md /etc/example/config"),
        _command(f"cat {tmp_path}/notes.md docs/relative.md https://example.test/notes"),
        {"type": "reasoning", "id": "r", "summary": ['Inspect /etc/example/config with --password "example value"']},
        {"type": "fileChange", "id": "f", "changes": [{"path": "/opt/example/notes.md"}]},
    ]
    peer = tmp_path / "peer.py"
    peer.write_text("import json, sys\ncases = " + repr(cases) + "\n" + r'''
position = 0
for raw in sys.stdin:
    request = json.loads(raw)
    if "id" not in request:
        continue
    def emit(value):
        print(json.dumps(value), flush=True)
    if request["method"] == "turn/start":
        item = cases[position % len(cases)]
        position += 1
        turn = "turn-" + str(position)
        emit({"id": request["id"], "result": {"turn": {"id": turn}}})
        for phase in ("started", "completed"):
            emit({"method": "item/" + phase, "params": {"threadId": "thread", "turnId": turn,
                 "item": {**item, "status": "completed" if phase == "completed" else "inProgress"}}})
        emit({"method": "item/agentMessage/delta", "params": {"delta": "Ready."}})
        emit({"method": "turn/completed", "params": {"turn": {"id": turn, "status": "completed"}}})
    else:
        emit({"id": request["id"], "result": {"thread": {"id": "thread"}}})
''', encoding="utf-8")
    # Only select a portable fixture host; all stdio, event and file-store code is real.
    popen = chat_agent.subprocess.Popen
    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda command, **kwargs:
                        popen([sys.executable, str(peer), *command[1:]], **kwargs))
    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="fixture-host", work_dir=tmp_path, goal_id="fixture",
        objective="Inspect synthetic steps.", codex_home=tmp_path / "host-home",
        idle_timeout_sec=5, hard_timeout_sec=10,
    )
    root = tmp_path / "store"
    try:
        for position in range(2 * len(cases)):
            key = ("session", str(position))
            store = ChatSessionStore(root)
            events = []
            def on_event(kind, payload):
                events.append((kind, payload))
                store.append_event(*key, kind=kind, payload=payload)
            session.send("Inspect the synthetic fixture.", on_event=on_event)
            replay = ChatSessionStore(root).events_after(*key, None)
            assert [(row["kind"], row["payload"]) for row in replay] == events
            steps = [row["payload"]["step"] for row in replay if row["kind"] == "agent.phase" and "step" in row["payload"]]
            assert len(steps) == 2 and steps[0]["id"] == steps[1]["id"]
            serialized = json.dumps(steps)
            assert "example value" not in serialized and "/opt/example" not in serialized and "/etc/example" not in serialized
            if position % len(cases) in (0, 1, 3):
                assert "https://example.test/notes" in serialized
            if position % len(cases) == 3:
                assert "notes.md docs/relative.md" in serialized and str(tmp_path) not in serialized
        # The file itself must already be clean, before a replay reader or UI can filter it.
        logs = "\n".join(path.read_text() for path in root.rglob("*.events.jsonl"))
        assert "example value" not in logs and "/opt/example" not in logs and "/etc/example" not in logs
    finally:
        session.close()
