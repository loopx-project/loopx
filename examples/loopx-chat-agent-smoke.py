#!/usr/bin/env python3
"""Smoke-test the persistent read-only Codex app-server chat seam."""

from __future__ import annotations

import json
import os
import queue
import stat
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.chat import CHAT_REVIEW_OPEN_TAG, VisibleResponseStreamFilter, redact_local_paths  # noqa: E402
from loopx.chat_agent import (  # noqa: E402
    CodexChatAgentError,
    CodexChatAgentSession,
)


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

if sys.argv[1:3] == ["debug", "models"]:
    marker = os.environ.get("LOOPX_FAKE_DEBUG_MODELS_MARKER")
    if marker:
        Path(marker).write_text("invoked", encoding="utf-8")
    if os.environ.get("LOOPX_FAKE_DEBUG_MODELS_FAIL") == "1":
        raise SystemExit(2)
    print(json.dumps({"models": [{"slug": "fake-current", "base_instructions": "Current built-in instructions."}]}))
    raise SystemExit(0)

catalog_override = None
for index, value in enumerate(sys.argv):
    if value == "-c" and index + 1 < len(sys.argv):
        key, separator, encoded = sys.argv[index + 1].partition("=")
        if separator and key == "model_catalog_json":
            catalog_override = Path(json.loads(encoded))

turn_count = 0
assert "goals" not in sys.argv, sys.argv
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialized":
        continue
    if method == "initialize":
        result = {"serverInfo": {"name": "fake-codex"}}
    elif method in {"thread/start", "thread/resume"}:
        params = message.get("params", {})
        if os.environ.get("LOOPX_FAKE_OTHER_THREAD_ERROR") == "1":
            print(json.dumps({
                "id": request_id,
                "error": {"code": -32602, "message": "unrelated thread configuration error"},
            }), flush=True)
            continue
        if os.environ.get("LOOPX_FAKE_LEGACY_MODEL_CATALOG") == "1" and catalog_override is None:
            print(json.dumps({
                "id": request_id,
                "error": {
                    "code": -32600,
                    "message": "failed to parse model_catalog_json: missing field `base_instructions`",
                },
            }), flush=True)
            continue
        if catalog_override is not None:
            catalog = json.loads(catalog_override.read_text(encoding="utf-8"))
            if not catalog.get("models") or not all(model.get("base_instructions") for model in catalog["models"]):
                print(json.dumps({
                    "id": request_id,
                    "error": {"code": -32600, "message": "compatibility catalog is incomplete"},
                }), flush=True)
                continue
        if params.get("sandbox") not in {"read-only", "workspace-write"} or params.get("approvalPolicy") != "never":
            print(json.dumps({
                "id": request_id,
                "error": {"code": -32602, "message": "unsafe chat boundary"},
            }), flush=True)
            continue
        result = {"thread": {"id": "thread-loopx-chat"}}
    elif method in {"thread/goal/set", "thread/goal/get"}:
        print(json.dumps({
            "id": request_id,
            "error": {"code": -32601, "message": "Goal mode must not be used for Chat"},
        }), flush=True)
        continue
    elif method == "turn/start":
        turn_count += 1
        params = message.get("params", {})
        prompt = params.get("input", [{}])[0].get("text", "")
        if (
            "<loopx-review-json>" not in prompt
            or "protected_action" not in prompt
            or "user message" not in prompt
            or "loopx-chat-smoke: Keep the review loop bounded." not in prompt
        ):
            print(json.dumps({
                "id": request_id,
                "error": {"code": -32602, "message": "missing review contract"},
            }), flush=True)
            continue
        if "execute confirmed task" in prompt and "execution agent for a confirmed LoopX Task" not in prompt:
            print(json.dumps({
                "id": request_id,
                "error": {"code": -32602, "message": "missing execution contract"},
            }), flush=True)
            continue
        if "inspect attached image" in prompt:
            inputs = params.get("input", [])
            if len(inputs) != 2 or inputs[1] != {
                "type": "image",
                "url": "data:image/png;base64,aW1hZ2U=",
                "detail": "auto",
            }:
                print(json.dumps({
                    "id": request_id,
                    "error": {"code": -32602, "message": "missing image input"},
                }), flush=True)
                continue
        turn_id = f"turn-loopx-chat-{turn_count}"
        print(json.dumps({
            "method": "turn/started",
            "params": {
                "threadId": "thread-loopx-chat",
                "turn": {"id": turn_id, "status": "inProgress"},
            },
        }), flush=True)
        result = {"turn": {"id": turn_id, "status": "running"}}
        print(json.dumps({"id": request_id, "result": result}), flush=True)
        if "force idle timeout" in prompt:
            continue
        if "activity keeps alive" in prompt:
            for index in range(3):
                time.sleep(0.1)
                print(json.dumps({
                    "method": "item/started",
                    "params": {"threadId": "thread-loopx-chat", "turnId": turn_id, "item": {"id": f"work-{index}"}},
                }), flush=True)
        if "retryable error" in prompt:
            print(json.dumps({
                "method": "error",
                "params": {
                    "threadId": "thread-loopx-chat",
                    "turnId": turn_id,
                    "willRetry": True,
                    "error": {"message": "synthetic transient failure"},
                },
            }), flush=True)
        if "ends in terminal failure" in prompt:
            print(json.dumps({
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-loopx-chat",
                    "turn": {"id": turn_id, "status": "failed"},
                },
            }), flush=True)
            continue
        visible_answer = "Reviewed " + params.get("cwd", "") + ".\n"
        protected_action = ({
            "operation": "merge",
            "target": "PR #123",
            "summary": "Prepare the protected merge preview.",
        } if "explicit protected action" in prompt else None)
        response = (
            visible_answer + '<loopx-review-json>'
            + json.dumps({
                "schema_version": "loopx_chat_agent_response_v0",
                "message": visible_answer.strip(),
                "proposals": [{
                    "kind": "todo",
                    "text": "[P1] Add the selected review flow.",
                    "priority": "P1",
                    "rationale": "This is the smallest reviewable step.",
                }],
                "protected_action": protected_action,
                "gate": None,
            })
            + '</loopx-review-json>'
        )
        marker_split = response.index("<loopx-review-json>") + 7
        for chunk in (response[:marker_split], response[marker_split:]):
            print(json.dumps({
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-loopx-chat",
                    "turnId": turn_id,
                    "itemId": f"item-{turn_count}",
                    "delta": chunk,
                },
            }), flush=True)
        print(json.dumps({
            "method": "turn/completed",
            "params": {
                "threadId": "thread-loopx-chat",
                "turn": {"id": turn_id, "status": "completed"},
            },
        }), flush=True)
        continue
    elif method == "turn/interrupt":
        result = {}
    else:
        result = {}
    print(json.dumps({"id": request_id, "result": result}), flush=True)
'''


def main() -> None:
    class EmptyThenMessageQueue:
        def __init__(self) -> None:
            self.empty_reads = 0

        def get(self, *, timeout: float) -> dict[str, object]:
            assert timeout > 0
            self.empty_reads += 1
            if self.empty_reads <= 1_200:
                raise queue.Empty
            return {"kind": "ready"}

    polling_session = object.__new__(CodexChatAgentSession)
    polling_session.messages = EmptyThenMessageQueue()
    assert polling_session._next_message(deadline=time.monotonic() + 5) == {"kind": "ready"}

    stream_filter = VisibleResponseStreamFilter()
    long_sentence_chunks = ["This is a deliberately long visible response segment " for _ in range(5)]
    early_visible = "".join(stream_filter.feed(chunk) for chunk in long_sentence_chunks)
    assert early_visible, "long prose must become visible before sentence punctuation"
    assert early_visible + stream_filter.finish() == "".join(long_sentence_chunks)

    chinese_filter = VisibleResponseStreamFilter()
    long_chinese = "持续输出让用户更早看到有效结果并及时调整方向" * 20
    chinese_early = "".join(
        chinese_filter.feed(long_chinese[index : index + 12])
        for index in range(0, len(long_chinese), 12)
    )
    assert chinese_early, "Chinese prose must stream without whitespace or sentence punctuation"
    assert chinese_early + chinese_filter.finish() == long_chinese

    english_filter = VisibleResponseStreamFilter()
    english_chunks = ["Version 1.2 is ready", ".", " See example.com for notes! Next,", " run the check? Done"]
    english_visible = [english_filter.feed(chunk) for chunk in english_chunks]
    assert english_visible[0] == "", "a decimal or version point must not end a sentence"
    assert english_visible[1] == "", "sentence punctuation waits for the following whitespace"
    assert english_visible[2] == "Version 1.2 is ready. See example.com for notes! ", english_visible
    assert english_visible[3] == "Next, run the check? ", english_visible
    assert "".join(english_visible) + english_filter.finish() == "".join(english_chunks)

    # An early sentence end must not hold back a long tail in the same chunk:
    # the tail still streams through the length fallback before the provider
    # sends anything else.
    tail_filter = VisibleResponseStreamFilter()
    tail_chunk = "Ready. " + "word " * 100
    tail_visible = tail_filter.feed(tail_chunk)
    assert tail_visible.startswith("Ready. word "), tail_visible[:40]
    assert len(tail_chunk) - len(tail_visible) < 160, len(tail_visible)
    assert tail_visible + tail_filter.finish() == tail_chunk

    protected_path = "/home/example/project"
    tail_path_filter = VisibleResponseStreamFilter(protected_paths=[protected_path])
    tail_path_text = "Ready. " + "word " * 40 + f"see {protected_path}/notes.txt " + "word " * 40
    tail_path_chunks = [tail_path_text, CHAT_REVIEW_OPEN_TAG + '{"hidden": true}']
    tail_path_early = tail_path_filter.feed(tail_path_chunks[0])
    assert len(tail_path_text) - len(tail_path_early) < 160, len(tail_path_early)
    tail_path_visible = tail_path_early + tail_path_filter.feed(tail_path_chunks[1]) + tail_path_filter.finish()
    assert protected_path not in tail_path_visible, tail_path_visible
    assert "hidden" not in tail_path_visible, tail_path_visible
    assert tail_path_visible == redact_local_paths(tail_path_text, protected_paths=[protected_path]), tail_path_visible

    english_path_filter = VisibleResponseStreamFilter(protected_paths=[protected_path])
    english_path_text = f"The report is in {protected_path}/notes.txt. Review it next. "
    english_path_visible = "".join(
        english_path_filter.feed(english_path_text[index : index + 7])
        for index in range(0, len(english_path_text), 7)
    ) + english_path_filter.finish()
    assert protected_path not in english_path_visible, english_path_visible
    # Splitting at sentences must not change what redaction produces.
    assert english_path_visible == redact_local_paths(english_path_text, protected_paths=[protected_path]), english_path_visible

    path_filter = VisibleResponseStreamFilter(protected_paths=[protected_path])
    path_text = ("前" * 150) + protected_path + "/secret.txt 后续内容"
    path_early = "".join(
        path_filter.feed(path_text[index : index + 12])
        for index in range(0, len(path_text), 12)
    )
    path_visible = path_early + path_filter.finish()
    assert path_early, "a protected path crossing the segment boundary must not stall the stream"
    assert protected_path not in path_visible, path_visible
    assert "[project]" in path_visible, path_visible

    with tempfile.TemporaryDirectory(prefix="loopx-chat-agent-") as raw_tmp:
        root = Path(raw_tmp)
        fake_codex = root / "codex"
        fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

        session = CodexChatAgentSession.start(
            codex_bin=str(fake_codex),
            work_dir=root,
            goal_id="loopx-chat-smoke",
            objective="Keep the review loop bounded.",
            response_timeout_sec=3.0,
        )
        try:
            observed = []
            result = session.send(
                "user message: suggest the next bounded step",
                on_event=lambda kind, payload: observed.append((kind, payload)),
            )
            image_result = session.send(
                "inspect attached image",
                attachments=[{"data_url": "data:image/png;base64,aW1hZ2U="}],
            )
            protected_result = session.send("explicit protected action")
            retry_events = []
            retry_result = session.send(
                "recover after retryable error",
                on_event=lambda kind, payload: retry_events.append((kind, payload)),
            )
            try:
                session.send("retryable error ends in terminal failure")
            except CodexChatAgentError as exc:
                assert str(exc) == "Codex app-server reported a terminal turn failure.", exc
            else:
                raise AssertionError("terminal failed status should fail the Chat turn")
        finally:
            session.close()

        assert result["message"] == "Reviewed [project].", result
        assert result["proposals"] == [
            {
                "kind": "todo",
                "text": "[P1] Add the selected review flow.",
                "priority": "P1",
                "rationale": "This is the smallest reviewable step.",
            }
        ], result
        assert result["gate"] is None, result
        assert result["protected_action"] is None, result
        assert protected_result["protected_action"] == {
            "operation": "merge",
            "target": "PR #123",
            "summary": "Prepare the protected merge preview.",
        }, protected_result
        assert image_result["message"] == "Reviewed [project].", image_result
        assert retry_result["message"] == "Reviewed [project].", retry_result
        assert any(
            kind == "agent.phase"
            and payload.get("method") == "error"
            and payload.get("label") == "Codex 正在重试"
            for kind, payload in retry_events
        ), retry_events
        assert not any(kind == "turn.failed" for kind, _ in retry_events), retry_events
        assert str(root) not in json.dumps(result), result
        visible = "".join(
            str(payload.get("text") or "")
            for kind, payload in observed
            if kind == "answer.delta"
        )
        assert visible.strip() == "Reviewed [project].", observed
        assert "<loopx-review-json>" not in visible, visible
        assert not any(kind == "assistant.delta" for kind, _ in observed), observed
        delta_index = next(index for index, item in enumerate(observed) if item[0] == "answer.delta")
        completed_phase_index = next(
            index
            for index, item in enumerate(observed)
            if item[0] == "agent.phase" and item[1].get("method") == "turn/completed"
        )
        assert delta_index < completed_phase_index, observed

        resumed = CodexChatAgentSession.start(
            codex_bin=str(fake_codex),
            work_dir=root,
            goal_id="loopx-chat-smoke",
            objective="Keep the review loop bounded.",
            resume_thread_id="thread-loopx-chat",
            response_timeout_sec=3.0,
        )
        assert resumed.thread_id == "thread-loopx-chat"
        resumed.close()

        execution_session = CodexChatAgentSession.start(
            codex_bin=str(fake_codex),
            work_dir=root,
            goal_id="loopx-chat-smoke",
            objective="Keep the review loop bounded.",
            execution_mode=True,
            response_timeout_sec=3.0,
        )
        try:
            execution_result = execution_session.send("execute confirmed task")
        finally:
            execution_session.close()
        assert execution_result["message"] == "Reviewed [project].", execution_result

        with patch.dict(os.environ, {"LOOPX_FAKE_LEGACY_MODEL_CATALOG": "1"}):
            compatibility_session = CodexChatAgentSession.start(
                codex_bin=str(fake_codex),
                work_dir=root,
                goal_id="loopx-chat-smoke",
                objective="Keep the review loop bounded.",
                response_timeout_sec=3.0,
            )
            try:
                compatibility_result = compatibility_session.send("user message after catalog repair")
            finally:
                compatibility_session.close()
        assert compatibility_session.model_catalog_compatibility_applied is True
        assert compatibility_result["message"] == "Reviewed [project].", compatibility_result

        debug_models_marker = root / "debug-models-invoked"
        with patch.dict(
            os.environ,
            {
                "LOOPX_FAKE_DEBUG_MODELS_MARKER": str(debug_models_marker),
                "LOOPX_FAKE_OTHER_THREAD_ERROR": "1",
            },
        ):
            try:
                CodexChatAgentSession.start(
                    codex_bin=str(fake_codex),
                    work_dir=root,
                    goal_id="loopx-chat-smoke",
                    objective="Keep the review loop bounded.",
                    response_timeout_sec=3.0,
                )
            except CodexChatAgentError as exc:
                assert "rejected thread/start" in str(exc), exc
            else:
                raise AssertionError("unrelated thread errors must fail without a catalog retry")
        assert not debug_models_marker.exists(), debug_models_marker

        with patch.dict(
            os.environ,
            {
                "LOOPX_FAKE_DEBUG_MODELS_FAIL": "1",
                "LOOPX_FAKE_LEGACY_MODEL_CATALOG": "1",
            },
        ):
            try:
                CodexChatAgentSession.start(
                    codex_bin=str(fake_codex),
                    work_dir=root,
                    goal_id="loopx-chat-smoke",
                    objective="Keep the review loop bounded.",
                    response_timeout_sec=3.0,
                )
            except CodexChatAgentError as exc:
                assert exc.gate["kind"] == "host_tool_gate", exc.gate
                assert "compatible model catalog" in exc.gate["summary"], exc.gate
                assert "legacy override" in exc.gate["next_action"], exc.gate
            else:
                raise AssertionError("catalog generation failures must surface a host-tool gate")

        active_session = CodexChatAgentSession.start(
            codex_bin=str(fake_codex),
            work_dir=root,
            goal_id="loopx-chat-smoke",
            objective="Keep the review loop bounded.",
            response_timeout_sec=3.0,
            idle_timeout_sec=0.15,
            hard_timeout_sec=2.0,
        )
        try:
            active_result = active_session.send("activity keeps alive")
        finally:
            active_session.close()
        assert active_result["proposals"], active_result

        timeout_session = CodexChatAgentSession.start(
            codex_bin=str(fake_codex),
            work_dir=root,
            goal_id="loopx-chat-smoke",
            objective="Keep the review loop bounded.",
            response_timeout_sec=3.0,
            idle_timeout_sec=0.2,
            hard_timeout_sec=2.0,
        )
        try:
            timeout_session.send("force idle timeout")
        except CodexChatAgentError as exc:
            assert exc.gate["kind"] == "host_tool_gate", exc.gate
            assert exc.error_code == "idle_timeout", exc.error_code
            assert "activity" in exc.gate["summary"], exc.gate
        else:
            raise AssertionError("Codex timeout must stop at a recoverable host-tool gate")
        finally:
            timeout_session.close()

        try:
            CodexChatAgentSession.start(
                codex_bin=str(root / "missing-codex"),
                work_dir=root,
                goal_id="loopx-chat-smoke",
                objective="Keep the review loop bounded.",
                response_timeout_sec=0.2,
            )
        except CodexChatAgentError as exc:
            assert exc.gate["kind"] == "host_tool_gate", exc.gate
            assert "Codex" in exc.gate["summary"], exc.gate
        else:
            raise AssertionError("missing Codex binary must stop at a host-tool gate")

    print("loopx-chat-agent-smoke: ok")


if __name__ == "__main__":
    main()
