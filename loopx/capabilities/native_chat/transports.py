"""Compose locally installed transports around one native Chat runtime.

Providers own authentication, addressing and delivery evidence. Bindings, grants,
Sessions, Turns and worker returns continue to use their existing owners. This
composition is supplied by the local host, never by an HTTP request or message.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
import re
from typing import Any

from ..manager_context.roundtrip import ReturnResolutionBlocked
from .conversation_bindings import ChatConversationBindings


def close_transports(transports: Sequence[Any]) -> None:
    for transport in reversed(transports):
        try:
            transport.close()
        except Exception as exc:
            # Finish native runtime shutdown; provider exceptions may contain
            # credentials, so retain only their type in the diagnostic.
            logging.getLogger(__name__).warning("Conversation transport close failed: %s", type(exc).__name__)


def install_conversation_transports(server: Any, *, observe_default: Callable[[str], dict[str, Any]],
                                    factories: Sequence[Callable[[Any], Any]],
                                    observe_group: Callable[[str], dict[str, Any]] | None = None) -> None:
    if not factories:
        server.runtime_controller.project_contexts.conversation_bindings = ChatConversationBindings(
            root=server.chat_store.root, project_contexts=server.runtime_controller.project_contexts,
            observe=observe_default, observe_group=observe_group)
        return
    transports = []
    try:
        for factory in factories:
            transports.append(factory(server))
        server.conversation_transports = ChatConversationTransports(
            observe_default=observe_default, transports=transports)
        bindings = ChatConversationBindings(root=server.chat_store.root,
            project_contexts=server.runtime_controller.project_contexts, observe=server.conversation_transports.observe,
            observe_group=observe_group)
        server.runtime_controller.project_contexts.conversation_bindings = bindings
        server.conversation_transports.bindings = bindings
    except Exception:
        if not hasattr(server, "conversation_transports"):
            close_transports(transports)
        server.server_close()
        raise


class ChatConversationTransports:
    def __init__(self, *, observe_default: Callable[[str], dict[str, Any]],
                 transports: Sequence[Any] = ()) -> None:
        self.observe_default = observe_default
        self.transports: dict[str, Any] = {}
        self.default_return: Any = None
        self.bindings: Any = None
        self._cancelled: Callable[[], bool] = lambda: False
        self._started = False
        self._closed = False
        for transport in transports:
            ref = transport.transport_ref
            if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", ref):
                raise ValueError("invalid conversation transport reference")
            if ref in self.transports:
                raise ValueError("duplicate conversation transport reference")
            self.transports[ref] = transport

    def observe(self, transport_ref: str) -> dict[str, Any]:
        transport = self.transports.get(transport_ref)
        result = transport.observe() if transport is not None else self.observe_default(transport_ref)
        if result.get("transport_ref") != transport_ref or result.get("verified") is not True:
            raise ValueError("conversation transport identity is unverified")
        return result

    def _return_transport(self, route: dict[str, Any], session: dict[str, Any]) -> Any:
        saved = session.get("steward_context")
        if isinstance(saved, dict):
            # Re-resolve the original audience even for verification-only replay.
            # A provider name saved in a transcript cannot authorize disclosure.
            selected = self.bindings.session_context(saved)
            if (selected["channel_id"] != session.get("channel_id")
                    or selected["channel_id"] != route.get("channel_id")
                    or route.get("session_id") != session.get("session_id")):
                raise ReturnResolutionBlocked("original_route_unavailable", "original return audience changed")
            transport = self.transports.get(selected["binding"]["transport_ref"])
            if transport is not None:
                return transport
        if self.default_return is None:
            raise ReturnResolutionBlocked("original_route_unavailable", "return transport unavailable")
        return self.default_return

    # The per-provider check below preserves Lark files without advertising
    # file delivery for another transport that has not qualified it.
    supports_result_files = True

    def send_with_attempt(self, route: dict[str, Any], session: dict[str, Any], turn: dict[str, Any],
                          text: str, record_attempt: Callable[[Mapping[str, str | None]], None]) -> dict[str, Any]:
        transport = self._return_transport(route, session)
        if route.get("result_attachments") and not getattr(transport, "supports_result_files", False):
            raise ReturnResolutionBlocked("original_route_unavailable", "this transport does not support result files")
        return transport.send_with_attempt(route, session, turn, text, record_attempt)

    def verify(self, route: dict[str, Any], session: dict[str, Any], turn: dict[str, Any],
               text: str, attempt: Mapping[str, str | None]) -> dict[str, Any]:
        transport = self._return_transport(route, session)
        if route.get("result_attachments") and not getattr(transport, "supports_result_files", False):
            raise ReturnResolutionBlocked("original_route_unavailable", "this transport does not support result files")
        return transport.verify(route, session, turn, text, attempt)

    @property
    def cancelled(self) -> Callable[[], bool]:
        return self._cancelled

    @cancelled.setter
    def cancelled(self, value: Callable[[], bool]) -> None:
        self._cancelled = value
        for transport in [self.default_return, *self.transports.values()]:
            if transport is not None:
                transport.cancelled = value

    def start(self, server: Any) -> None:
        if self._started or self._closed:
            return
        self._started = True
        try:
            for transport in self.transports.values():
                transport.start(server)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close_transports(list(self.transports.values()))
