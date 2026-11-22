#!/usr/bin/env python3
"""Offline smoke for Claude Code and direct-key Chat adapters."""

from __future__ import annotations

import json
import stat
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.chat.providers import (  # noqa: E402
    ClaudeCodeAdapter,
    DirectModelAdapter,
    _execute_read_only_tool,
)


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import sys

tools_at = sys.argv.index("--tools")
assert sys.argv[tools_at + 1] == "Read,Glob,Grep", sys.argv
assert "Bash" not in sys.argv and "Edit" not in sys.argv and "Write" not in sys.argv, sys.argv
assert "--verbose" in sys.argv, sys.argv

answer = "已确认。请完成两项操作。\n1. 更新 MR 描述\n2. 指定 reviewer"
envelope = '<loopx-review-json>' + json.dumps({
    "schema_version": "loopx_chat_agent_response_v0",
    "message": answer,
    "proposals": [],
    "gate": None,
}) + '</loopx-review-json>'
print(json.dumps({"type": "system", "session_id": "11111111-1111-4111-8111-111111111111"}), flush=True)
print(json.dumps({"type": "stream_event", "event": {
    "type": "content_block_delta",
    "delta": {"type": "text_delta", "text": answer + "\n" + envelope},
}}), flush=True)
print(json.dumps({"type": "result", "result": envelope}), flush=True)
'''


class FakeDirectAdapter(DirectModelAdapter):
    def _post(self, url, body, headers):  # type: ignore[no-untyped-def]
        assert self.api_key not in json.dumps(body), body
        assert {tool["name"] for tool in body["tools"]} == {"list_files", "search_text", "read_file"}, body
        if self.provider == "anthropic":
            assert url.endswith("/v1/messages")
            assert headers["x-api-key"] == self.api_key
            return {"content": [{"type": "text", "text": "Anthropic 管家回答。"}]}
        assert url.endswith("/v1/responses")
        assert headers["authorization"] == f"Bearer {self.api_key}"
        return {"output_text": "OpenAI 管家回答。"}


class FakeToolDirectAdapter(DirectModelAdapter):
    rounds: int = 0

    def _post(self, url, body, headers):  # type: ignore[no-untyped-def]
        del url, headers
        self.rounds += 1
        assert self.api_key not in json.dumps(body), body
        if self.provider == "anthropic":
            if self.rounds == 1:
                return {"content": [{"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {"path": "README.md"}}]}
            tool_results = body["messages"][-1]["content"]
            assert tool_results[0]["type"] == "tool_result", tool_results
            assert "public fixture" in tool_results[0]["content"], tool_results
            return {"content": [{"type": "text", "text": "已通过只读工具核对项目。"}]}
        if self.rounds == 1:
            return {"output": [{"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": '{"path":"README.md"}'}]}
        result = next(item for item in body["input"] if item.get("type") == "function_call_output")
        assert "public fixture" in result["output"], result
        return {"output_text": "已通过只读工具核对项目。"}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-chat-providers-") as raw_tmp:
        root = Path(raw_tmp)
        (root / "README.md").write_text("public fixture\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=private\n", encoding="utf-8")
        blocked = json.loads(_execute_read_only_tool(root, "read_file", {"path": ".env"}))
        assert blocked["ok"] is False and "SECRET" not in json.dumps(blocked), blocked
        fake = root / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        observed: list[tuple[str, dict[str, object]]] = []
        claude = ClaudeCodeAdapter.start(claude_bin=str(fake), work_dir=root)
        assert claude.capabilities()["tool_scope"] == "read_only"
        response = claude.start_turn("告诉我下一步", lambda kind, payload: observed.append((kind, payload)))
        assert response["message"].startswith("已确认"), response
        assert any(kind == "answer.delta" for kind, _ in observed), observed
        visible = "".join(
            str(payload.get("text") or "")
            for kind, payload in observed
            if kind == "answer.delta"
        )
        assert visible.strip() == response["message"], observed
        assert "<loopx-review-json>" not in visible, visible
        assert not any(kind == "assistant.delta" for kind, _ in observed), observed
        assert claude.upstream_thread_id == "11111111-1111-4111-8111-111111111111"

        for provider, expected in (("anthropic", "Anthropic 管家回答。"), ("openai", "OpenAI 管家回答。")):
            adapter = FakeDirectAdapter(
                provider=provider,
                model="fixture-model",
                api_key="private-fixture-key",
                work_dir=root,
            )
            assert "private-fixture-key" not in repr(adapter)
            events: list[tuple[str, dict[str, object]]] = []
            result = adapter.start_turn("下一步是什么", lambda kind, payload: events.append((kind, payload)))
            assert result["message"] == expected, result
            assert events[-1][0] == "answer.final", events
            assert adapter.capabilities()["tool_calls"] is True

        for provider in ("anthropic", "openai"):
            adapter = FakeToolDirectAdapter(
                provider=provider,
                model="fixture-model",
                api_key="private-fixture-key",
                work_dir=root,
            )
            events = []
            result = adapter.start_turn("读取 README", lambda kind, payload: events.append((kind, payload)))
            assert result["message"] == "已通过只读工具核对项目。", result
            assert adapter.rounds == 2, adapter.rounds
            assert any(kind == "agent.phase" and "检查项目上下文" in str(payload.get("label")) for kind, payload in events), events
            assert "private" not in json.dumps(events, ensure_ascii=False), events

    print("loopx-chat-provider-adapters-smoke: ok")


if __name__ == "__main__":
    main()
