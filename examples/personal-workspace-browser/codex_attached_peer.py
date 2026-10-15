"""Disposable local Desktop owner for HTTP and installed-package UI validation.

This is a standard-library fixture, not a production transport or executor.
"""

from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.capabilities.native_chat import codex_attached as provider

THREAD = "11111111-1111-4111-8111-111111111111"


def frame(connection):
    def take(size):
        value = b""
        while len(value) < size:
            data = connection.recv(size - len(value))
            if not data:
                raise EOFError
            value += data
        return value

    size = struct.unpack("<I", take(4))[0]
    return json.loads(take(size))


def emit(connection, value):
    raw = json.dumps(value).encode()
    connection.sendall(struct.pack("<I", len(raw)) + raw)


def append_message(path, role, text):
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "role": role,
                        "content": [
                            {
                                "type": "input_text"
                                if role == "user"
                                else "output_text",
                                "text": text,
                            }
                        ],
                    },
                }
            )
            + "\n"
        )


@contextmanager
def fixture(tmp_path, *, uncertain=False, goal_id="sample-goal"):
    if os.name != "posix":
        raise unittest.SkipTest("The native Desktop peer requires a Unix owner socket")
    with tempfile.TemporaryDirectory(prefix="lxca-", dir="/tmp") as directory:
        home = Path(directory)
        with patch.object(provider, "codex_homes", lambda: [home]):
            (home / "sessions").mkdir(parents=True)
            (home / "ipc").mkdir(mode=0o700)
            rollout = home / "sessions" / "bound.jsonl"
            append_message(rollout, "user", "Original request")
            append_message(rollout, "assistant", "Original result")
            with rollout.open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "timestamp": "2026-01-01T00:00:01Z",
                            "payload": {"type": "task_complete"},
                        }
                    )
                    + "\n"
                )
            connection = sqlite3.connect(home / "state_5.sqlite")
            connection.execute(
                "CREATE TABLE threads(id TEXT, rollout_path TEXT, archived INTEGER)"
            )
            connection.execute(
                "INSERT INTO threads VALUES(?,?,0)", (THREAD, str(rollout))
            )
            connection.commit()
            connection.close()
            registry = tmp_path / "registry.json"
            registry.write_text(
                json.dumps(
                    {
                        "goals": [
                            {
                                "id": goal_id,
                                "repo": str(tmp_path),
                                "coordination": {
                                    "registered_agents": ["worker"],
                                    "thread_agent_bindings": [
                                        {
                                            "agent_id": "worker",
                                            "host_surface": "codex-app",
                                            "thread_id": THREAD,
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                )
            )
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(home / "ipc" / "ipc.sock"))
            (home / "ipc" / "ipc.sock").chmod(0o600)
            listener.listen()
            listener.settimeout(0.1)
            methods = []
            inputs = []
            stop = threading.Event()

            def host():
                while not stop.is_set():
                    try:
                        client, _ = listener.accept()
                    except TimeoutError:
                        continue
                    except OSError:
                        return
                    with client:
                        client.settimeout(2)
                        try:
                            while True:
                                message = frame(client)
                                methods.append(message["method"])
                                if message["method"] == "initialize":
                                    result = {"clientId": "loopx-client"}
                                elif message["method"] == "thread-owner-discovery":
                                    result = {"supportsUntrustedAppInput": True}
                                elif message["method"] == "thread-follower-start-turn":
                                    inputs.append(message["params"])
                                    assert message["targetClientId"] == "original-owner"
                                    if uncertain:
                                        break
                                    append_message(
                                        rollout,
                                        "user",
                                        message["params"]["turnStart"]["request"][
                                            "input"
                                        ][0]["text"],
                                    )
                                    result = {
                                        "result": {
                                            "turn": {"id": "original-native-turn"}
                                        }
                                    }
                                else:
                                    raise AssertionError("Unexpected host operation")
                                emit(
                                    client,
                                    {
                                        "type": "response",
                                        "requestId": message["requestId"],
                                        "resultType": "success",
                                        "handledByClientId": "original-owner",
                                        "result": result,
                                    },
                                )
                        except (EOFError, TimeoutError, OSError):
                            pass

            host_thread = threading.Thread(target=host, daemon=True)
            host_thread.start()
            server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
            server.registry_path = registry
            server.runtime_root = tmp_path / "runtime"
            server.verbose = False
            http_thread = threading.Thread(target=server.serve_forever, daemon=True)
            http_thread.start()

            def request(
                method="GET",
                body=None,
                query="?goal_id=sample-goal",
                origin=None,
                path="/api/chat/codex-attached",
            ):
                client = http.client.HTTPConnection(*server.server_address, timeout=8)
                headers = {"Content-Type": "application/json"}
                if origin:
                    headers["Origin"] = origin
                client.request(
                    method,
                    path + query,
                    json.dumps(body) if body else None,
                    headers,
                )
                response = client.getresponse()
                status = response.status
                value = json.loads(response.read())
                client.close()
                return status, value

            request.server = server
            try:
                yield request, rollout, methods, inputs, registry, home
            finally:
                server.shutdown()
                server.server_close()
                http_thread.join()
                stop.set()
                listener.close()
                host_thread.join(timeout=3)
