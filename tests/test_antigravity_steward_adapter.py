"""Focused tests for Antigravity (Gemini 4 Argon) Steward & Chat adapter."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading
from typing import Any

from loopx.capabilities.native_chat.antigravity_adapter import (
    ANTIGRAVITY_DEFAULT_MODEL,
    AntigravityAdapter,
    probe_antigravity_endpoint,
    resolve_antigravity_model_placeholder,
)
from loopx.capabilities.steward_executor import (
    STEWARD_EXECUTOR_SCHEMA,
    effective_steward_executor_defaults,
    normalize_steward_executor_machine_defaults,
    steward_executor_endpoints,
)
from loopx.chat_manager import (
    MANAGER_MODEL_SOURCE_MACHINE_CONFIGURATION,
    MANAGER_MODEL_SOURCE_VENDOR_DEFAULT,
    manager_channel_binding,
    manager_model_resolution,
)


def test_antigravity_is_registered_in_steward_executor_vocabulary() -> None:
    assert "antigravity" in steward_executor_endpoints()

    normalized = normalize_steward_executor_machine_defaults(
        {
            "schema_version": STEWARD_EXECUTOR_SCHEMA,
            "selection_policy": "preferred",
            "executor_endpoint": "antigravity",
            "eligible_endpoints": [],
            "executor_model": "gemini-4-argon-high",
            "executor_reasoning_effort": "high",
        }
    )
    assert normalized["executor_endpoint"] == "antigravity"
    assert normalized["executor_model"] == "gemini-4-argon-high"

    model, source = manager_model_resolution({}, endpoint="antigravity")
    assert model == ANTIGRAVITY_DEFAULT_MODEL == "gemini-4-argon-high"
    assert source == MANAGER_MODEL_SOURCE_VENDOR_DEFAULT

    binding = manager_channel_binding(
        {},
        machine_defaults=effective_steward_executor_defaults(
            {
                "schema_version": "loopx_machine_configuration_v0",
                "namespaces": {"steward_executor": normalized},
            }
        ),
    )
    assert binding["executor_endpoint"] == "antigravity"
    assert binding["model"] == "gemini-4-argon-high"
    assert binding["model_source"] == MANAGER_MODEL_SOURCE_MACHINE_CONFIGURATION


def test_antigravity_model_placeholder_resolution() -> None:
    assert resolve_antigravity_model_placeholder("gemini-4-argon-high") == (
        "gemini-4-argon-high",
        "MODEL_PLACEHOLDER_M417",
    )
    assert resolve_antigravity_model_placeholder("gemini-4-argon-medium") == (
        "gemini-4-argon-medium",
        "MODEL_PLACEHOLDER_M418",
    )
    assert resolve_antigravity_model_placeholder("gemini-4-argon-low") == (
        "gemini-4-argon-low",
        "MODEL_PLACEHOLDER_M419",
    )
    assert resolve_antigravity_model_placeholder("argon", "medium") == (
        "gemini-4-argon-medium",
        "MODEL_PLACEHOLDER_M418",
    )
    assert resolve_antigravity_model_placeholder("gemini-4-argon", "low") == (
        "gemini-4-argon-low",
        "MODEL_PLACEHOLDER_M419",
    )


def test_antigravity_adapter_executes_turn_and_streams_response(tmp_path: Path) -> None:
    recorded_calls: list[tuple[str, dict[str, Any]]] = []
    csrf_token = "test-csrf-token-argon"

    class FakeLSHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            if self.headers.get("x-codeium-csrf-token") != csrf_token:
                self.send_response(403)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            method = self.path.rsplit("/", 1)[-1]
            recorded_calls.append((method, body))

            if method == "GetCascadeModelConfigData":
                resp: dict[str, Any] = {
                    "clientModelConfigs": [
                        {
                            "label": "Gemini 4 Argon (High)",
                            "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"},
                        }
                    ]
                }
            elif method == "StartCascade":
                assert body.get("source") == "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT"
                resp = {"cascadeId": "cascade-argon-001"}
            elif method == "GetCascadeTrajectory":
                sent = any(m == "SendUserCascadeMessage" for m, _ in recorded_calls)
                resp = {
                    "trajectory": {"cascadeId": "cascade-argon-001", "trajectoryId": "traj-001"},
                    "status": "CASCADE_RUN_STATUS_IDLE",
                    "numTotalSteps": 3 if sent else 1,
                }
            elif method == "SendUserCascadeMessage":
                resp = {}
            elif method == "GetCascadeTrajectorySteps":
                resp = {
                    "steps": [
                        {
                            "type": "CORTEX_STEP_TYPE_USER_INPUT",
                            "status": "CORTEX_STEP_STATUS_DONE",
                        },
                        {
                            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "plannerResponse": {
                                "thinking": "Checking LoopX portfolio state.",
                                "response": (
                                    "<loopx-review-json>"
                                    '{"message": "Hello from Gemini 4 Argon Steward!", '
                                    '"proposals": [], "context_handoff": [], "gate": null}'
                                    "</loopx-review-json>"
                                ),
                            },
                        },
                    ]
                }
            else:
                resp = {}

            payload = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), FakeLSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ls_url = f"http://127.0.0.1:{server.server_port}"
        env = {
            "ANTIGRAVITY_LS_URL": ls_url,
            "ANTIGRAVITY_CSRF_TOKEN": csrf_token,
        }
        available, executable, detail = probe_antigravity_endpoint(env=env)
        assert available is True
        assert executable == "antigravity-ls"
        assert ls_url in detail

        adapter = AntigravityAdapter.start(
            work_dir=tmp_path,
            objective="Act as LoopX Steward.",
            executor_model={"model": "gemini-4-argon-high", "reasoning_effort": "high"},
            env=env,
        )
        assert adapter.upstream_thread_id == "cascade-argon-001"
        assert adapter.current_model_id == "gemini-4-argon-high"
        assert adapter.model_placeholder == "MODEL_PLACEHOLDER_M417"

        events: list[tuple[str, dict[str, Any]]] = []
        result = adapter.start_turn(
            "Summarize active goals",
            lambda kind, data: events.append((kind, data)),
        )
        assert result["message"] == "Hello from Gemini 4 Argon Steward!"
        assert any(kind == "turn.started" for kind, _ in events)
        assert any(kind == "agent.phase" and data.get("phase") == "thinking" for kind, data in events)
        assert any(kind == "answer.delta" for kind, _ in events)
        assert any(kind == "answer.final" for kind, _ in events)

        send_calls = [body for method, body in recorded_calls if method == "SendUserCascadeMessage"]
        assert len(send_calls) == 1
        planner_cfg = send_calls[0]["cascadeConfig"]["plannerConfig"]
        assert planner_cfg["planModel"] == "MODEL_PLACEHOLDER_M417"
        assert planner_cfg["requestedModel"] == {"model": "MODEL_PLACEHOLDER_M417"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
