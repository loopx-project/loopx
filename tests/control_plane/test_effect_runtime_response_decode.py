from __future__ import annotations

import hashlib
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from loopx.control_plane import effect_runtime


@pytest.mark.parametrize("snapshot", [False, True], ids=["inline", "snapshot"])
@pytest.mark.parametrize(
    "result_bytes",
    [b"[" * 100_000 + b"0" + b"]" * 100_000, b'"\xff"'],
    ids=["excessive-depth", "invalid-utf8"],
)
def test_undecodable_response_is_ambiguous_without_redispatch(
    tmp_path: Path, monkeypatch, snapshot: bool, result_bytes: bytes,
) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path)
    fingerprint = effect_runtime._runtime_fingerprint_for_request()
    finished = Event()
    received_requests = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.05)
        info = {
            "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "pid": os.getpid(),
            "host": "127.0.0.1",
            "port": listener.getsockname()[1],
            "token": "fixture-token",
        }
        locator = effect_runtime._runtime_info_path(fingerprint)
        locator.write_text(json.dumps(info), encoding="utf-8")

        def serve_responses() -> None:
            while not finished.is_set():
                try:
                    connection = listener.accept()[0]
                except TimeoutError:
                    continue
                with connection:
                    connection.settimeout(2)
                    request_bytes = b""
                    while b"\n" not in request_bytes:
                        chunk = connection.recv(65536)
                        if not chunk:
                            raise AssertionError("request ended before its frame")
                        request_bytes += chunk
                    request = json.loads(request_bytes)
                    received_requests.append(request)
                    envelope = {
                        "schema_version": effect_runtime.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,
                        "request_id": request["request_id"],
                        "ok": True,
                    }
                    response = json.dumps(envelope).encode()[:-1] + b',"result":' + result_bytes + b"}\n"
                    if snapshot:
                        Path(request["response_sink"]).write_bytes(response)
                        envelope["result_ref"] = {
                            "byte_count": len(response),
                            "sha256": hashlib.sha256(response).hexdigest(),
                        }
                        response = json.dumps(envelope).encode() + b"\n"
                    connection.sendall(response)

        with ThreadPoolExecutor(max_workers=1) as executor:
            server = executor.submit(serve_responses)
            try:
                with pytest.raises(effect_runtime.EffectRuntimeResponseAmbiguous) as error:
                    effect_runtime.effect_runtime_request(
                        "goal.checkpoint_read_context.commit" if snapshot else "turn_journal.write",
                        {"operation_id": "fixture-operation"},
                        timeout=2,
                        large_local_snapshot=snapshot,
                    )
            finally:
                finished.set()
                server.result(timeout=3)

    assert error.value.diagnostic_code == "runtime_response_ambiguous"
    assert len(received_requests) == 1
    assert json.loads(locator.read_text(encoding="utf-8")) == info
