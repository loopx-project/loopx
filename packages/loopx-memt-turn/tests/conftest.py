from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest
from tokenizers import Tokenizer, models
from transformers import PreTrainedTokenizerFast
from memt_sglang.models.common.snapshot_identity import snapshot_identity

from loopx_memt_turn.config import Config


class Provider:
    """Protocol fixture, not a memory-quality or inference emulator."""
    def __init__(self):
        self.requests = []
        self.snapshots = {}
        self.sessions = {}
        self.replies = []
        self.fail_action = None
        self.finish = "stop"
        self.finishes = []
        self.epoch = "fixture-epoch"
        self.candidate = json.dumps({"result_kind": "validated_progress", "classification": "recorded",
                                     "summary": "Synthetic observation recorded.", "next_action": "Validate the artifact."})

    def respond(self, path: str, body: dict):
        self.requests.append((path, body))
        if path == "/memt/capabilities":
            return {"protocol": "memt-http-v1", "epoch": self.epoch,
                    "snapshot_identity": self.snapshot_identity,
                    "chunk_tokens": 2048, "max_prefill_tokens": 65_536}
        if path == "/generate":
            answer = self.replies.pop(0) if self.replies else self.candidate
            ids = [] if "|commit:" in body["rid"] else self.tokenizer.encode(answer) + [self.tokenizer.eos_token_id]
            finish = self.finishes.pop(0) if self.finishes else self.finish
            return {"output_ids": ids, "meta_info": {"id": body["rid"], "finish_reason": {"type": finish}}}
        action, session_id = body["action"], body["session"]
        if action == self.fail_action:
            return {"error": "synthetic-private-provider-error"}
        if action == "create":
            self.sessions[session_id] = {"snapshot": None}
            return {"epoch": self.epoch}
        if action == "acquire":
            return {"lease": "fixture-lease"}
        if action == "import":
            assert body["file"] in self.snapshots
            self.sessions[session_id]["snapshot"] = body["file"]
            return {"has_memory": True}
        if action == "export":
            snapshot = f"snapshot-{len(self.snapshots)}.safetensors"
            self.snapshots[snapshot] = session_id
            return {"snapshot_id": snapshot}
        return {}


@pytest.fixture
def provider():
    fixture = Provider()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply({})

        def do_POST(self):
            self.reply(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

        def reply(self, body):
            payload = json.dumps(fixture.respond(self.path, body)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    fixture.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield fixture
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.fixture
def config(tmp_path, provider):
    workspace, tokenizer = tmp_path / "workspace", tmp_path / "tokenizer"
    workspace.mkdir()
    tokenizer.mkdir()
    model_config = {"model_type": "memt_qwen3_5_moe", "memt": {}, "backbone_configs": {}}
    (tokenizer / "config.json").write_text(json.dumps(model_config))
    vocabulary = ["[UNK]", "[EOS]", provider.candidate, "not JSON", '{"result_kind":"wait"}']
    vocabulary += [json.dumps({"result_kind": "validated_progress", "classification": "recorded",
                              "next_action": "Validate the artifact.", "summary": summary})
                   for summary in ("x" * 401, {"invalid": "type"})]
    vocabulary += [f"<tool_call><function=record_observation><parameter=observation>{text}</parameter></function></tool_call>"
                   for text in ("first", "one", "two")]
    codec = Tokenizer(models.WordLevel({text: index for index, text in enumerate(vocabulary)}, unk_token="[UNK]"))
    provider.tokenizer = PreTrainedTokenizerFast(tokenizer_object=codec, eos_token="[EOS]", unk_token="[UNK]",
        chat_template="{% for message in messages %}{{ message['role'] }}:{{ message['content'] }}\n{% endfor %}")
    provider.tokenizer.save_pretrained(tokenizer)
    provider.snapshot_identity = snapshot_identity(model_config, provider.tokenizer)
    return Config(workspace=str(workspace), state_dir=str(tmp_path / "private"), tokenizer=str(tokenizer),
                  base_url=provider.url, enabled=True, timeout_seconds=10)
