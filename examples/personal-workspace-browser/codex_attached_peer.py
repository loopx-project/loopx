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
            rollout.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": THREAD}})
                + "\n"
            )
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

            peer_clients = {}
            followers = set()
            peer_lock = threading.Lock()
            peer_threads = []
            follow_events = []
            live_state = {"id": THREAD, "turns": []}
            live_revision = 0

            def send_live(client, change, *, owner="original-owner", version=11):
                with peer_clients[client]:
                    emit(
                        client,
                        {
                            "type": "broadcast",
                            "method": "thread-stream-state-changed",
                            "version": version,
                            "sourceClientId": owner,
                            "params": {
                                "conversationId": THREAD,
                                "hostId": "local",
                                "change": change,
                            },
                        },
                    )

            def publish_live(change, *, owner="original-owner", version=11):
                nonlocal live_state, live_revision
                if change.get("type") == "snapshot":
                    live_state = change["conversationState"]
                live_revision = change["revision"]
                with peer_lock:
                    recipients = list(followers)
                for client in recipients:
                    try:
                        send_live(client, change, owner=owner, version=version)
                    except (KeyError, OSError):
                        pass

            def serve_peer(client):
                with client:
                    client.settimeout(0.2)
                    try:
                        while not stop.is_set():
                            try:
                                message = frame(client)
                            except TimeoutError:
                                continue
                            methods.append(message["method"])
                            if message["method"] == "thread-stream-following-changed":
                                assert message["version"] == 1
                                assert message["targetClientIds"] == ["original-owner"]
                                assert message["params"]["conversationId"] == THREAD
                                assert message["params"]["hostId"] == "local"
                                following = message["params"]["following"]
                                follow_events.append(following)
                                with peer_lock:
                                    if following:
                                        followers.add(client)
                                    else:
                                        followers.discard(client)
                                if following:
                                    send_live(
                                        client,
                                        {
                                            "type": "snapshot",
                                            "revision": live_revision,
                                            "conversationState": live_state,
                                        },
                                    )
                                continue
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
                                    message["params"]["turnStart"]["request"]["input"][
                                        0
                                    ]["text"],
                                )
                                result = {
                                    "result": {"turn": {"id": "original-native-turn"}}
                                }
                            else:
                                raise AssertionError("Unexpected host operation")
                            with peer_clients[client]:
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
                    except (EOFError, OSError):
                        pass
                    finally:
                        with peer_lock:
                            followers.discard(client)
                            peer_clients.pop(client, None)

            def host():
                while not stop.is_set():
                    try:
                        client, _ = listener.accept()
                    except TimeoutError:
                        continue
                    except OSError:
                        return
                    with peer_lock:
                        peer_clients[client] = threading.Lock()
                    thread = threading.Thread(
                        target=serve_peer, args=(client,), daemon=True
                    )
                    peer_threads.append(thread)
                    thread.start()

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
            request.publish_live = publish_live
            request.follow_events = follow_events
            try:
                yield request, rollout, methods, inputs, registry, home
            finally:
                server.shutdown()
                server.server_close()
                http_thread.join()
                stop.set()
                with peer_lock:
                    clients = list(peer_clients)
                for client in clients:
                    try:
                        client.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                listener.close()
                for thread in peer_threads:
                    thread.join(timeout=1)
                host_thread.join(timeout=3)
