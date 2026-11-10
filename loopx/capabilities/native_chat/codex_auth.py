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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()
TRANSPORT_PROVIDERS = frozenset({"openai", "loopx_account_api", "loopx_host_chatgpt_http"})


class CodexHostAuthUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Trusted-host Codex model authentication is unavailable.")


def recorded_provider(home: Path, thread_id: str | None) -> str | None:
    """Read only the original thread's metadata in its already bound store.

    Provider identity survives account transport changes. Native Codex checks
    both the process and resume against it; rebuilding/importing history is
    neither necessary nor an authentication fallback.
    """
    if not thread_id:
        return None
    from .codex_attached import DesktopUnavailable, locate_thread
    from .codex_history import RECORD_BYTES
    from ...hosts.codex_rollout_file import open_rollout
    try:
        _, path = locate_thread(thread_id, homes=[home])
    except DesktopUnavailable:
        return None  # Let native resume report the original missing thread.
    try:
        with open_rollout(path) as stream:
            raw = stream.readline(RECORD_BYTES + 1)
        if len(raw) > RECORD_BYTES or not raw.endswith(b"\n"):
            raise ValueError("invalid metadata record")
        record = json.loads(raw)
        meta = record["payload"]
        provider = meta["model_provider"]
        if (record["type"] != "session_meta" or meta["id"] != thread_id
                or not isinstance(provider, str) or not provider.strip()):
            raise ValueError("invalid native identity")
        return provider
    except (OSError, ValueError, KeyError, TypeError):
        raise CodexHostAuthUnavailable() from None


def lower_transport_configuration(result: dict[str, Any]) -> dict[str, Any]:
    """Check alias collisions below our public process flags, not against them."""
    layers = result.get("layers")
    if not isinstance(layers, list):
        return result.get("config", {})  # Older native/fake clients fail closed.
    providers = {}
    for layer in layers:
        if layer.get("disabledReason") or layer.get("name", {}).get("type") == "sessionFlags":
            continue
        providers.update(layer.get("config", {}).get("model_providers", {}))
    return {"model_providers": providers}


def transport_arguments(config: dict[str, Any]) -> list[str]:
    """Launch the same native provider without putting any credential in argv."""
    provider = config.get("model_provider")
    if provider not in TRANSPORT_PROVIDERS:
        raise CodexHostAuthUnavailable()
    values = {"model_provider": provider}
    if provider == "openai":
        if "openai_base_url" in config:
            values["openai_base_url"] = config["openai_base_url"]
            values["cli_auth_credentials_store"] = "ephemeral"
    else:
        values.update({f"model_providers.{provider}.{key}": value
                       for key, value in config["model_providers"][provider].items()})
    return [arg for key, value in values.items() for arg in ("-c", f"{key}={json.dumps(value)}")]


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


@dataclass(repr=False)
class CodexApiTransport:
    """Only a consumer-scoped loopback token reaches the model process."""
    endpoint: str
    token: str = field(repr=False)
    provider: str = "loopx_account_api"
    env_key: str = "LOOPX_MODEL_RELAY_TOKEN"

    def for_provider(self, provider: str) -> CodexApiTransport:
        if provider not in TRANSPORT_PROVIDERS:
            raise CodexHostAuthUnavailable()
        return replace(self, provider=provider,
                       env_key="OPENAI_API_KEY" if provider == "openai" else "LOOPX_MODEL_RELAY_TOKEN")

    def configuration(self, current: dict[str, Any], effective: dict[str, Any] | None = None) -> dict[str, Any]:
        # Native overlays merge maps, so a lower-layer alias must not supply
        # another URL/header/env key beneath our model-only transport.
        if self.provider in (effective or {}).get("model_providers", {}):
            raise CodexHostAuthUnavailable()
        environment = current.get("shell_environment_policy", {})
        exclude = [*environment.get("exclude", [])]
        if self.env_key not in exclude:
            exclude.append(self.env_key)
        config = {**current, "model_provider": self.provider,
                  "shell_environment_policy": {**environment, "exclude": exclude}}
        if self.provider == "openai":
            # Codex reserves this built-in ID: model_providers.openai is invalid.
            # Its supported base-URL setting and ephemeral native login preserve it.
            return {**config, "openai_base_url": self.endpoint}
        return {**config, "model_providers": {
            **current.get("model_providers", {}), self.provider: {
                "name": "LoopX account API", "base_url": self.endpoint,
                "env_key": self.env_key, "wire_api": "responses",
                "requires_openai_auth": False, "supports_websockets": False}}}


def authenticate_api_transport(session, transport: CodexApiTransport | None) -> None:
    if transport is not None and transport.provider == "openai":
        # Built-in OpenAI ignores a custom provider env_key. Its native login
        # is process-only because launch pins the ephemeral credential backend;
        # never persist the scoped relay token over the owner's real auth.json.
        try:
            login = session._request("account/login/start", {
                "type": "apiKey", "apiKey": transport.token}, request_id=4)
            if login.get("type") != "apiKey":
                raise ValueError("unexpected native API authentication mode")
        except Exception:
            raise session._runtime_error("Native account API authentication is unavailable.") from None


def native_api_transport(runtime_root: Path, base_home: Path, *, consumer: str,
                         model: str | None = None) -> CodexApiTransport | None:
    try:
        from loopx_codex_provider_routing.native import NativeAccounts
    except ImportError:
        return None  # Optional provider absence preserves native authentication.
    try:
        prepare = getattr(NativeAccounts(runtime_root), 'model_transport', None)
        endpoint = prepare(codex_home=base_home, consumer=consumer, model=model) if callable(prepare) else None
        return CodexApiTransport(endpoint['base_url'], endpoint['api_key']) if endpoint else None
    except Exception:
        raise CodexHostAuthUnavailable() from None


def prepare_api_transport(runtime_root: Path | None, home: Path, *, isolated_home: Path | None,
                          eligible: bool, consumer: str, model: str | None) -> CodexApiTransport | None:
    if (runtime_root is None or not eligible or isolated_home is not None
            and ((isolated_home / "auth.json").exists() or (isolated_home / "auth.json").is_symlink())):
        return None
    try:
        return native_api_transport(runtime_root, home, consumer=consumer, model=model)
    except CodexHostAuthUnavailable:
        from ...chat_agent import CodexChatAgentError
        raise CodexChatAgentError("Native account API transport is unavailable.",
            error_code="model_auth_unavailable", gate=None) from None


def prepare_conversation_transport(runtime_root: Path | None, base_home: Path, runtime_home: Path,
        thread_id: str | None, *, current: dict[str, Any], provider: str, eligible: bool,
        consumer: str, model: str | None, environment: dict[str, str]):
    """Keep native identity fixed; only the account owner's transport can change."""
    from .codex_context import shared_chatgpt_transport
    eligible = eligible and not provider and not current.get("model_provider")
    original = recorded_provider(runtime_home, thread_id) if eligible else None
    project_auth = runtime_home / "auth.json"
    retain = (eligible and original in TRANSPORT_PROVIDERS and (runtime_home == base_home
              or not (project_auth.exists() or project_auth.is_symlink())))
    api = prepare_api_transport(runtime_root, base_home,
        isolated_home=runtime_home if runtime_home != base_home else None,
        eligible=eligible, consumer=consumer, model=model)
    if api is not None:
        if retain:
            api = api.for_provider(original)
        if api.provider != "openai":
            environment[api.env_key] = api.token
        current, provider = api.configuration(current), api.provider
    elif retain:
        current = shared_chatgpt_transport(current, {}, provider=original)
        provider = original
    return current, provider, api, retain


def authenticate_isolated(session, home: Path, isolated: Path, binary: str, *, api_transport) -> CodexHostModelAuth | None:
    if api_transport is not None:
        return None
    try:
        broker = for_isolated_process(home, isolated, binary)
        if broker is not None:
            login = session._request("account/login/start", {
                "type": "chatgptAuthTokens", **broker.read()}, request_id=4)
            if login.get("type") != "chatgptAuthTokens":
                raise ValueError("unexpected native model authentication mode")
        return broker
    except Exception:
        raise session._runtime_error(
            "Trusted-host Codex model authentication is unavailable. "
            "Restore the host account and retry this same Session.") from None


def conversation_account_epoch(runtime_root: Path, home: Path, **consumer) -> tuple[str, str] | None:
    try:
        from loopx_codex_provider_routing.native import NativeAccounts
        owner = NativeAccounts(runtime_root)
        return owner.conversation_epoch(codex_home=home,
            **(consumer if callable(getattr(owner, 'model_transport', None)) else {}))
    except ImportError:
        return None
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Native account epoch observation held (%s)", type(exc).__name__)
        return None
