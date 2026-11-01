"""Adapt one native invocation to the existing Goal/Turn admission owner.

This is transport, not a second quota policy. An invocation never rebinds its
authority, process or gateway; checkpoint continuations create new instances.
"""
from __future__ import annotations

import copy
import json
import math
import os
import signal
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import proxy_bypass_environment

from loopx.capabilities.benchmark_toolkit.provider_gateway import (
    ProviderGatewayRequestRejected,
    serve_runner_owned_provider_gateway,
)
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected


def request_admission_enabled(env, mode):
    value = env.get("LOOPX_PROVIDER_REQUEST_ADMISSION", "off")
    if value not in {"off", "on"}:
        raise ValueError("LOOPX_PROVIDER_REQUEST_ADMISSION supports only off or on")
    if value == "off":
        return False
    if (mode != "heartbeat" or not env.get("OPENAI_BASE_URL") or not env.get("OPENAI_API_KEY")
            or env.get("CODEX_WIRE_API", "responses") != "responses"):
        raise ValueError("provider request admission requires a custom Responses heartbeat provider")
    return True


@dataclass(frozen=True)
class _Invocation:
    admission: object
    turn_id: str
    session_id: str | None
    deadline: float


class ProviderRequestAdmission:
    """One immutable owner plus a one-shot process attachment."""

    def __init__(self, env, session_wake, receipt, wake, *, timeout=None, original_quota=None):
        from .worker import PHASE_WORK_RESERVE_SECONDS, heartbeat_launch_check

        if session_wake is None or session_wake.admission is None:
            raise ValueError("provider request admission requires the original heartbeat session")
        captured_env = dict(env)
        captured_session = SimpleNamespace(goal_ref=copy.deepcopy(session_wake.goal_ref))
        check = heartbeat_launch_check(captured_env, captured_session,
            original_quota=copy.deepcopy(original_quota))
        deadline = math.inf
        if timeout is not None:
            if not math.isfinite(timeout) or timeout < 0:
                raise ValueError("provider request timeout must be finite and non-negative")
            deadline = time.monotonic() + timeout
        if captured_env.get("LOOPX_PHASE_DEADLINE_EPOCH"):
            epoch = float(captured_env["LOOPX_PHASE_DEADLINE_EPOCH"])
            if not math.isfinite(epoch):
                raise ValueError("provider phase deadline must be finite")
            deadline = min(deadline,
                time.monotonic() + epoch - PHASE_WORK_RESERVE_SECONDS - time.time())
        self._invocation = _Invocation(
            session_wake.admission.with_launch_decision(check), captured_env["LOOPX_TURN"],
            session_wake.session_id, deadline)
        self._env = captured_env
        self._process = None
        self._retired = False
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._log_lock = threading.Lock()
        self._request_events = {}
        self.refusal_code = None
        self._wake = wake
        self._summary = {
            "invocation_id": uuid.uuid4().hex, "turn_id": self._invocation.turn_id,
            "session_id": self._invocation.session_id, "requests": 0,
            "pending": 0, "sent": 0, "unknown": 0, "refused": 0,
        }
        receipt.setdefault("provider_request_invocations", []).append(self._summary)

    def started(self, process):
        with self._lock:
            if self._process is not None or self._retired:
                raise ValueError("provider request invocation cannot be rebound")
            self._process = process
            self._ready.set()

    def retire(self):
        with self._lock:
            self._retired = True
            self._ready.set()

    def before_cleanup(self, _process, _scope_error):
        self.retire()

    def signal_requested(self, _signal):
        pass

    def after_cleanup(self, _process, _cleanup_error):
        pass

    def _reject(self, code):
        with self._lock:
            # A delayed request from a retired invocation cannot poison the
            # receipt or process of a checkpoint replacement.
            if not self._retired:
                self.refusal_code = self.refusal_code or code
                self._summary["refusal_code"] = self.refusal_code
                if self._process is not None and self._process.poll() is None:
                    try:
                        os.killpg(self._process.pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
        raise ProviderGatewayRequestRejected(code)

    def _require_live(self):
        if self.refusal_code is not None:
            self._reject(self.refusal_code)
        if self._retired or self._process is None or self._process.poll() is not None:
            self._reject("provider_request_without_live_owner")
        if time.monotonic() >= self._invocation.deadline:
            self._reject("provider_request_budget_exhausted")

    @contextmanager
    def lifetime(self):
        # Popen may start a request before the parent receives its handle. Wait
        # only for this invocation's single attachment, never for a successor.
        self._ready.wait(timeout=max(0, min(2, self._invocation.deadline - time.monotonic())))
        with self._lock:
            self._require_live()
        entered = False
        try:
            with self._invocation.admission.launch_lifetime(operation="provider_request_dispatch"):
                entered = True
                with self._lock:
                    # Authority/registry contention can outlive the process or
                    # phase. Revalidate after every wait and before any POST.
                    self._require_live()
                    yield min(time.monotonic() + 2, self._invocation.deadline)
        except ProviderGatewayRequestRejected:
            raise
        except FirstPartyHostRuntimeRejected as error:
            self._reject(error.code)
        except Exception:
            if entered:
                raise  # A transport failure is not a quota refusal or a retry grant.
            self._reject("provider_request_admission_unavailable")

    def observe(self, request):
        with self._lock:
            request_id, phase = request["request_id"], request["event"]
            prior = self._request_events.get(request_id)
            if phase == "started" and prior is None:
                self._summary["requests"] += 1
                self._summary["pending"] += 1
            elif phase == "dispatch" and prior == "started":
                self._summary["sent"] += 1
            elif phase == "finished" and prior in {"started", "dispatch"}:
                self._summary["pending"] -= 1
                if request.get("forwarded") is None:
                    self._summary["unknown"] += 1
                if "refusal_code" in request:
                    self._summary["refused"] += 1
            else:
                return  # Re-observing a phase cannot count one request twice.
            self._request_events[request_id] = phase
            event = {"invocation_id": self._summary["invocation_id"],
                "turn_id": self._invocation.turn_id, "session_id": self._invocation.session_id,
                **request}
        # A slow filesystem must not hold the invocation state lock, which a
        # later request also needs inside its canonical admission lifetime.
        with self._log_lock:
            with (self._wake / "provider-requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, sort_keys=True) + "\n")

    @contextmanager
    def serve(self, command):
        upstream = urlsplit(self._env["OPENAI_BASE_URL"])
        proxy = None
        bypass = self._env.get("no_proxy", self._env.get("NO_PROXY", ""))
        if upstream.scheme == "https" and not proxy_bypass_environment(upstream.netloc, {"no": bypass}):
            proxy = self._env.get("https_proxy", self._env.get("HTTPS_PROXY")) or None
        try:
            with serve_runner_owned_provider_gateway(
                    upstream_base_url=self._env["OPENAI_BASE_URL"],
                    upstream_bearer_token=self._env["OPENAI_API_KEY"],
                    request_lifetime=self.lifetime, observe_request=self.observe,
                    upstream_proxy_url=proxy,
                    # SForge can supply a private host credential gateway over
                    # HTTP. Preserve the runner's effective route; do not move
                    # credentials or silently replace it with the public URL.
                    allow_http_upstream=True) as gateway:
                self.base_url = gateway.base_url
                # Keep the durable home on its actual provider for checkpoint
                # compatibility; only this native invocation gets the gateway.
                routed = list(command)
                routed[1:1] = ["-c", "model_providers.harbor.base_url=" + json.dumps(gateway.base_url)]
                try:
                    yield routed
                finally:
                    self.retire()
        finally:
            self.retire()
