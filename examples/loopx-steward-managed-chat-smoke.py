#!/usr/bin/env python3
"""Run one real steward Chat turn on the managed host.

The steward channel answers on the managed host by running one bounded DeepSeek
Harness segment per Chat turn. This smoke starts the real bundled dsh runtime
behind a local mock OpenAI-compatible SSE endpoint, so it exercises the real
transport, the real segment boundary and the real Chat turn lifecycle without a
provider credential or a network model call. It asserts that the channel
resolved the managed execution profile, that the segment received it, that the
answer reached the persisted Chat turn, and that the parsed envelope survived
the segment boundary.

It does not claim a live provider result: the model endpoint is a fixture. Run
`python3 examples/loopx-steward-managed-chat-smoke.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.chat import CHAT_REVIEW_CLOSE_TAG, CHAT_REVIEW_OPEN_TAG  # noqa: E402
from loopx.chat_manager import (  # noqa: E402
    MANAGER_AGENT_GOAL_ID,
    MANAGER_AGENT_OBJECTIVE,
    MANAGER_ENDPOINT_ENV_VAR,
    manager_channel_binding,
    manager_workspace,
    open_manager_session,
)
from loopx.chat_dsh import STEWARD_SEGMENT_ENV  # noqa: E402
from loopx.chat_runtime import ChatRuntimeController  # noqa: E402
from loopx.chat_store import ChatSessionStore  # noqa: E402

CREDENTIAL_ENV = "DEEPSEEK_API_KEY"
BASE_URL_ENV = "DEEPSEEK_BASE_URL"
CREDENTIAL_VALUE = "fixture-operator-credential"
# The mock model echoes this marker, so the answer in the persisted Chat turn
# proves the text travelled through the real runtime rather than a Python stub.
ANSWER_MARKER = "managed steward answer"
PROPOSAL_TEXT = "Fixture bounded Todo from the managed steward."
CAPTURED_REQUESTS: list[dict[str, Any]] = []
# The second turn asks the mock model to try a write outside its workspace, so
# the smoke can prove the pinned read-only sandbox refuses it.
SANDBOX_PROBE_TARGET = "loopx-steward-managed-chat-sandbox-probe.txt"
MODE = {"value": "answer"}


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"steward managed chat smoke failed: {message}")


def _envelope_answer() -> str:
    envelope = {
        "message": f"{ANSWER_MARKER}: 已读取授权范围内的证据。",
        "proposals": [
            {
                "kind": "todo",
                "text": PROPOSAL_TEXT,
                "priority": "P1",
                "rationale": "The fixture proves the managed segment keeps the contract.",
            }
        ],
        "protected_action": None,
        "context_handoff": None,
        "gate": None,
    }
    return (
        "可见回答先于机器可读信封。\n"
        + CHAT_REVIEW_OPEN_TAG
        + json.dumps(envelope, ensure_ascii=False)
        + CHAT_REVIEW_CLOSE_TAG
    )


class _MockModelHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode("utf-8", "replace")
        CAPTURED_REQUESTS.append({"path": self.path, "body": body})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        if MODE["value"] == "sandbox_probe" and not _has_tool_result(body):
            call = {
                "index": 0,
                "id": "call_sandbox_probe",
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps(
                        {
                            "command": f"touch {_sandbox_probe_path()}",
                            "description": "probe the segment sandbox",
                        }
                    ),
                },
            }
            chunk = json.dumps(
                {
                    "choices": [
                        {
                            "delta": {"role": "assistant", "tool_calls": [call]},
                            "finish_reason": None,
                        }
                    ]
                }
            )
            self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
            self.wfile.write(
                b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            )
        else:
            chunk = json.dumps(
                {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "content": _envelope_answer(),
                            }
                        }
                    ]
                }
            )
            self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
            self.wfile.write(
                b'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
            )
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, _format: str, *args: object) -> None:
        return


def _has_tool_result(body: str) -> bool:
    """Whether this request already carries a tool result for the probe call."""

    return "call_sandbox_probe" in body


def _manager_evidence_text() -> str:
    """Return the decoded manager evidence prompt the segment received."""

    for item in CAPTURED_REQUESTS:
        try:
            payload = json.loads(item["body"])
        except ValueError:
            continue
        for message in payload.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text") if isinstance(part, dict) else part)
                    for part in content
                )
            if isinstance(content, str) and "Fresh Core evidence" in content:
                return content
    return ""


def _sandbox_probe_path() -> Path:
    return Path(tempfile.gettempdir()) / SANDBOX_PROBE_TARGET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()

    try:
        import deepseek_harness  # noqa: F401
    except ImportError:
        print("skip: deepseek-harness-sdk is not installed")
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockModelHandler)
    threading.Thread(
        target=server.serve_forever, name="steward-mock-llm", daemon=True
    ).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    ambient = {
        key: os.environ.get(key)
        for key in (
            CREDENTIAL_ENV,
            BASE_URL_ENV,
            MANAGER_ENDPOINT_ENV_VAR,
            "DSH_CWD",
            "DSH_HOME",
            "DSH_SESSION_ROOT",
        )
    }
    try:
        os.environ[CREDENTIAL_ENV] = CREDENTIAL_VALUE
        os.environ[BASE_URL_ENV] = base_url
        # The steward's shipped default is the interactive CLI endpoint, so the
        # managed host is reached here the way an operator reaches it: by
        # selecting it. The credential then authenticates that selection.
        os.environ[MANAGER_ENDPOINT_ENV_VAR] = "dsh"
        for key in ("DSH_CWD", "DSH_HOME", "DSH_SESSION_ROOT"):
            os.environ.pop(key, None)
        return _run_turn(args)
    finally:
        for key, value in ambient.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        server.shutdown()


def _run_turn(args: argparse.Namespace) -> int:
    binding = manager_channel_binding()
    _assert(
        binding["executor_endpoint"] == "dsh"
        and binding["executor_kind"] == "managed"
        and binding["available"] is True,
        "the selected managed host must be launchable with the configured credential",
    )
    _assert(
        binding["executor_endpoint_source"] == "explicit_config"
        and binding["executor_endpoint_default_reason"] == "",
        "the managed host must be reached by selection, not by discovering a credential",
    )
    _assert(
        binding["model"] == "deepseek-v4-flash"
        and binding["model_source"] == "managed_execution_profile"
        and binding["execution_profile"] == "deepseek-v4-flash@high",
        "the channel must run the managed execution profile",
    )

    with tempfile.TemporaryDirectory(prefix="loopx-steward-managed-chat-") as directory:
        root = Path(directory)
        store = ChatSessionStore(root / "store")
        # A real registry keeps this on the collected path the steward actually
        # runs, instead of the registry-unavailable envelope.
        registry = root / "registry.global.json"
        registry.write_text(json.dumps({"goals": []}), encoding="utf-8")
        runtime = ChatRuntimeController(
            store=store,
            codex_bin="fixture-codex",
            registry_path=registry,
            hard_timeout_sec=args.timeout_seconds,
        )
        try:
            session, _created = open_manager_session(
                controller=runtime,
                goal_id=MANAGER_AGENT_GOAL_ID,
                work_dir=manager_workspace(store.root, "manager"),
            )
            _assert(
                session["agent_id"] == "dsh",
                "the steward session must open on the resolved managed endpoint",
            )
            turn, _queued = runtime.submit_turn(
                session_id=str(session["session_id"]),
                client_turn_id="steward-managed-chat-fixture-1",
                message="现在哪些任务需要我决策？",
                work_dir=manager_workspace(store.root, "manager"),
                objective=MANAGER_AGENT_OBJECTIVE,
            )
            completed = runtime.wait_for_turn(
                session_id=str(session["session_id"]),
                turn_id=str(turn["turn_id"]),
                timeout_sec=args.timeout_seconds,
            )
        finally:
            runtime.close()

        _assert(
            completed.get("status") == "completed",
            f"the managed steward turn must complete (got {completed.get('status')!r}: "
            f"{completed.get('error_message') or completed.get('gate')})",
        )
        response = completed.get("response") or {}
        message = str(response.get("message") or "")
        _assert(
            ANSWER_MARKER in message and CHAT_REVIEW_OPEN_TAG not in message,
            "the persisted answer must be the model text, not its machine envelope",
        )
        _assert(
            [item.get("text") for item in response.get("proposals") or []]
            == [PROPOSAL_TEXT],
            "the parsed envelope must survive the managed segment boundary",
        )
        # The owner channel records the contract shape of its own answer, so a
        # reader can tell an order-following answer from a hedge-first one
        # without re-reading the text.
        answer_shape = response.get("answer_shape") or {}
        _assert(
            answer_shape.get("schema_version") == "manager_answer_contract_v0"
            and isinstance(answer_shape.get("state"), str),
            f"the persisted steward answer must carry its contract shape ({answer_shape})",
        )
        _assert(
            CAPTURED_REQUESTS,
            "the managed segment must have called the model endpoint",
        )
        _assert(
            any("Fresh Core evidence" in item["body"] for item in CAPTURED_REQUESTS),
            "the segment must receive the manager evidence context",
        )
        evidence_text = _manager_evidence_text()
        _assert(
            "manager_evidence_window_v0" in evidence_text
            and '"applies_to": "recent_delivery_history"' in evidence_text
            and '"receipt_detail_policy": "latest_full_per_goal"' in evidence_text
            and '"days_source": "product_default"' in evidence_text
            and '"days_bounds"' in evidence_text
            and '"remote_read": "inline_in_prompt"' in evidence_text
            and '"sources"' in evidence_text
            and '"declared_unread_sources"' in evidence_text,
            "the prompt-only segment must receive the bounded window and declared sources",
        )
        _assert(
            "manager_remote_evidence_v0" in evidence_text
            and '"read_status": "not_read"' in evidence_text
            and '"declared_source_count": 0' in evidence_text,
            "a prompt-only segment must receive the declared source read, and this "
            "fixture registers no host so the read stays empty instead of dialling",
        )
        payloads = [
            json.loads(item["body"])
            for item in CAPTURED_REQUESTS
            if item["body"].startswith("{")
        ]
        _assert(
            payloads
            and payloads[0].get("model") == binding["model"]
            and payloads[0].get("reasoning_effort") == "high",
            "the resolved managed profile must reach the provider request",
        )
        sandbox_evidence = _assert_segment_is_read_only(
            runtime=runtime,
            store=store,
            session=str(session["session_id"]),
            timeout_seconds=args.timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "schema_version": "loopx_steward_managed_chat_smoke_v1",
                    "executor_endpoint": binding["executor_endpoint"],
                    "model": binding["model"],
                    "execution_profile": binding["execution_profile"],
                    "turn_status": completed.get("status"),
                    "answer_marker": ANSWER_MARKER,
                    "proposal_count": len(response.get("proposals") or []),
                    "model_requests": len(CAPTURED_REQUESTS),
                    "sandbox": sandbox_evidence,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _assert_segment_is_read_only(
    *,
    runtime: ChatRuntimeController,
    store: ChatSessionStore,
    session: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Prove a segment's write attempt is refused by the dsh sandbox itself.

    The channel pins ``DSH_PERMISSION_MODE=read-only`` for its own segments. This
    asks the mock model to call the shell tool against a path outside the
    segment workspace, then asserts the tool *ran* and was refused, so the
    boundary is enforced rather than merely requested in the prompt.
    """

    target = _sandbox_probe_path()
    if target.exists():
        target.unlink()
    MODE["value"] = "sandbox_probe"
    try:
        turn, _queued = runtime.submit_turn(
            session_id=session,
            client_turn_id="steward-managed-chat-fixture-2",
            message="请直接写出对机器可读信封的回答。",
            work_dir=manager_workspace(store.root, "manager"),
            objective=MANAGER_AGENT_OBJECTIVE,
        )
        probe_turn = runtime.wait_for_turn(
            session_id=session,
            turn_id=str(turn["turn_id"]),
            timeout_sec=timeout_seconds,
        )
    finally:
        MODE["value"] = "answer"
    _assert(
        probe_turn.get("status") == "completed",
        f"the sandbox probe turn must still complete (got {probe_turn.get('status')!r})",
    )
    _assert(
        not target.exists(),
        "a read-only steward segment must not create a file outside its workspace",
    )
    tool_results = [
        message.get("content")
        for item in CAPTURED_REQUESTS
        if item["body"].startswith("{")
        for message in json.loads(item["body"]).get("messages", [])
        if isinstance(message, dict)
        and message.get("role") == "tool"
        and message.get("tool_call_id") == "call_sandbox_probe"
    ]
    _assert(
        any(
            "denied" in str(result) or "not permitted" in str(result)
            for result in tool_results
        ),
        "the sandbox must refuse the write instead of the model quietly skipping it",
    )
    return {
        "permission_mode": STEWARD_SEGMENT_ENV["DSH_PERMISSION_MODE"],
        "write_attempted": True,
        "write_denied": True,
    }


if __name__ == "__main__":
    raise SystemExit(main())
