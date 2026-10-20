"""Bind each request in a research wake to the existing live launch owner.

This is transport instrumentation, not an alternative quota/wait decision. No
prompt, response body, credential, or upstream URL is written to the journal.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlsplit
from urllib.request import proxy_bypass_environment

from loopx.capabilities.benchmark_toolkit.provider_gateway import (
    ProviderGatewayRequestRejected, serve_runner_owned_provider_gateway,
)
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected


class ResearchRequestGuard:
    def __init__(self, env, receipt, wake):
        if not env.get("OPENAI_BASE_URL") or not env.get("OPENAI_API_KEY"):
            raise ValueError("request admission requires the declared custom model endpoint")
        if env.get("CODEX_WIRE_API", "responses") != "responses":
            raise ValueError("request admission currently requires the Responses transport")
        self.env, self.receipt, self.wake = env, receipt, Path(wake)
        self.process = None
        self.admission = None
        self.refused = False
        self.admission_error = False
        self.lock = threading.Lock()
        self.binding = {}
        self.base_url = None

    def bind(self, process, env, session_wake, original_quota=None):
        from .worker import heartbeat_launch_check

        if session_wake is None:
            raise ValueError("research request admission requires the original session owner")
        with self.lock:
            self.process = process
            self.admission = session_wake.admission.with_launch_decision(
                heartbeat_launch_check(env, session_wake, original_quota=original_quota))
            self.binding = {"wake_id": self.receipt["turn_id"], "loopx_turn_id": env["LOOPX_TURN"],
                "goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"],
                "bound_native_session_id": session_wake.session_id}

    def lifetime(self, request):
        # Pure capture at connection acceptance, before any inbound read. The
        # POST context below owns validation and effects; an idle socket or GET
        # must neither hold authority nor latch a refusal for the running wake.
        with self.lock:
            owner, process = self.admission, self.process
            request.update(self.binding)
        return self._bound_lifetime(request, owner, process)

    def _require_live_binding(self, owner, process):
        with self.lock:
            if self.refused:
                raise FirstPartyHostRuntimeRejected("research_original_request_recovery_required")
            if (owner is None or process is None or self.admission is not owner
                    or self.process is not process or process.poll() is not None):
                raise FirstPartyHostRuntimeRejected("research_request_without_live_owner")
            if time.time() >= float(self.env["LOOPX_PHASE_DEADLINE_EPOCH"]):
                raise FirstPartyHostRuntimeRejected("research_phase_budget_exhausted")

    def _reject(self, request, owner, process, error):
        # Failure to read authority is not permission. Only admission calls
        # reach here; a connect/send error remains a transport observation.
        code = error.code if isinstance(error, FirstPartyHostRuntimeRejected) else "research_request_admission_unavailable"
        with self.lock:
            request["refusal_code"] = code
            current = self.admission is owner and self.process is process
            if current:
                # A late request retains its own rejection evidence, but must
                # not poison a checkpoint replacement's valid admission. A
                # genuine current refusal stays latched until the wake returns
                # to its scheduler; bind() cannot clear that recovery boundary.
                self.refused = True
                self.admission_error = self.admission_error or not isinstance(error, FirstPartyHostRuntimeRejected)
                self.receipt.setdefault("request_admission_refused", {"request_id": request["request_id"], "code": code})
            if process is not None and (current or self.process is not process) and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
        raise ProviderGatewayRequestRejected() from None

    @contextmanager
    def _bound_lifetime(self, request, owner, process):
        with ExitStack() as scope:
            try:
                self._require_live_binding(owner, process)
                scope.enter_context(owner.launch_lifetime(operation="benchmark_provider_request"))
                # Reading canonical admission can wait behind a writer. The
                # original process, phase clock and refusal must still be live
                # after that wait and immediately before the provider send.
                self._require_live_binding(owner, process)
            except Exception as error:
                self._reject(request, owner, process, error)
            yield

    def observe(self, event):
        with self.lock:
            with (self.wake / "provider-requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    @contextmanager
    def serve(self):
        target = urlsplit(self.env["OPENAI_BASE_URL"])
        proxy = None
        if not proxy_bypass_environment(target.netloc, {"no": self.env.get("NO_PROXY", self.env.get("no_proxy", ""))}):
            key = "HTTPS_PROXY" if target.scheme == "https" else "HTTP_PROXY"
            proxy = self.env.get(key, self.env.get(key.lower()))
        with serve_runner_owned_provider_gateway(upstream_base_url=self.env["OPENAI_BASE_URL"],
                upstream_bearer_token=self.env["OPENAI_API_KEY"],
                upstream_proxy_url=proxy,
                request_lifetime=self.lifetime, observe_request=self.observe) as gateway:
            self.base_url = gateway.base_url
            yield self

    def command(self, command):
        if self.base_url is None:
            raise ValueError("request gateway is not running")
        # Invocation-only routing; the pinned home and session profile retain
        # the actual upstream. Restart never persists an obsolete loopback port.
        return [command[0], "-c", "model_providers.harbor.base_url=" + json.dumps(self.base_url), *command[1:]]
