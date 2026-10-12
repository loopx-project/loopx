"""Trusted-host model authentication for an isolated native Codex process.

Only short-lived external tokens cross the private app-server stdio boundary.
The native account store owns refresh; no credentials, config or history are
seeded into the project store, tool environment or model context.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


class CodexHostAuthUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Trusted-host Codex model authentication is unavailable.")


def _native_refresh(codex_bin: str, home: Path, *, timeout_sec: float = 8) -> None:
    # Account RPCs need no native thread, LoopX Session or model request.
    # Reuse the native RPC dispatcher and native credential lifecycle rather
    # than implementing OAuth or keeping a second refresh-token cache.
    from ...chat_agent import CodexChatAgentSession, _reader

    deadline = time.monotonic() + timeout_sec
    process = subprocess.Popen(
        [codex_bin, "app-server", "-c", 'cli_auth_credentials_store="file"',
         "--listen", "stdio://"],
        cwd=str(home), env={**os.environ, "CODEX_HOME": str(home)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1,
    )
    messages: queue.Queue = queue.Queue()
    session = CodexChatAgentSession(process=process, messages=messages,
                                   thread_id="", work_dir=home, response_timeout_sec=timeout_sec)
    try:
        threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True).start()
        session._request("initialize", {"clientInfo": {
            "name": "loopx_chat", "title": "LoopX Chat", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True}}, request_id=1)
        session._notify("initialized", {})
        session.response_timeout_sec = max(0.01, deadline - time.monotonic())
        account = session._request("account/read", {"refreshToken": True}, request_id=2)
        if (account.get("account") or {}).get("type") != "chatgpt":
            raise CodexHostAuthUnavailable()
    finally:
        session.close()


@dataclass(repr=False)
class CodexHostModelAuth:
    home: Path
    codex_bin: str
    _last_token: str | None = field(default=None, repr=False)

    def _read(self) -> dict[str, str]:
        path = self.home / "auth.json"
        if path.is_symlink():
            raise CodexHostAuthUnavailable()
        data = json.loads(path.read_text(encoding="utf-8"))
        tokens = data.get("tokens")
        if data.get("auth_mode") != "chatgpt" or not isinstance(tokens, dict):
            raise CodexHostAuthUnavailable()
        access, account = tokens.get("access_token"), tokens.get("account_id")
        if not all(isinstance(value, str) and value.strip() for value in (access, account)):
            raise CodexHostAuthUnavailable()
        # Deliberately exclude ID/refresh tokens, email and all other native data.
        return {"accessToken": access, "chatgptAccountId": account}

    def read(self, *, refresh: bool = False, previous_account_id: str | None = None) -> dict[str, str]:
        deadline = time.monotonic() + 8
        with _locks_guard:
            lock = _locks.setdefault(self.home.resolve(), threading.Lock())
        try:
            if not lock.acquire(timeout=8):
                raise CodexHostAuthUnavailable()
            try:
                current = self._read()
                if previous_account_id is not None and current["chatgptAccountId"] != previous_account_id:
                    raise CodexHostAuthUnavailable()
                # Another project or native client may already have refreshed
                # the same account. Consume its new token instead of rotating
                # a shared refresh token again for every concurrent callback.
                if refresh and current["accessToken"] == self._last_token:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CodexHostAuthUnavailable()
                    _native_refresh(self.codex_bin, self.home, timeout_sec=remaining)
                    current = self._read()
                    if previous_account_id is not None and current["chatgptAccountId"] != previous_account_id:
                        raise CodexHostAuthUnavailable()
                self._last_token = current["accessToken"]
                return current
            finally:
                lock.release()
        except Exception:
            # Native errors and malformed private files must never reach the
            # Chat transcript, RPC diagnostics or model as exception details.
            raise CodexHostAuthUnavailable() from None


def for_isolated_process(base_home: Path, isolated_home: Path, codex_bin: str) -> CodexHostModelAuth | None:
    # A separately authenticated project retains its chosen native account.
    # Shared host auth is a model-only fallback, never a store identity change.
    if (isolated_home / "auth.json").is_symlink():
        raise CodexHostAuthUnavailable()
    if (isolated_home / "auth.json").exists() or not (base_home / "auth.json").exists():
        return None
    return CodexHostModelAuth(base_home, codex_bin)
