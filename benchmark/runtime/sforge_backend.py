"""Native Docker transport with private artifact collection and OAuth egress."""

from __future__ import annotations

import io
import inspect
import ipaddress
import json
import tarfile
from pathlib import Path, PurePosixPath

from sforge.harness.backend.docker_backend import DockerBackend
from sforge.harness.network_isolation import AllowedEndpoint, resolve_hostname

from .harbor import _CODEX_HOME, _CONTROL, _LOOPX_RUNTIME
from .provider_failure import observe_terminal_provider_failure


class RecordingDockerBackend(DockerBackend):
    def __init__(self, *, log_dir: Path, logger, oauth_proxy: bool = False,
                 blind_api_endpoint: tuple[str, int] | None = None, feedback=None, checkpoint=None, client=None,
                 docker_host_bridge: bool = False):
        if not isinstance(docker_host_bridge, bool):
            raise ValueError("docker_host_bridge must be an explicit boolean")
        super().__init__(client)
        self.docker_host_bridge = docker_host_bridge
        self.log_dir, self.logger = log_dir, logger
        self.blind_api_endpoint = blind_api_endpoint
        self.feedback = feedback
        self.checkpoint = checkpoint
        self.registration_bound = False
        self.execution_command = None
        self.execution_start_marker = None
        self.execution_receipt = None
        self.native_goal_receipt = None
        self.provider_failure_receipt = None
        self.auth_ips = [] if oauth_proxy else resolve_hostname("auth.openai.com", logger)
        if not oauth_proxy and not self.auth_ips:
            raise RuntimeError("Cannot resolve Codex OAuth endpoint")

    def registration_options(self, run_agent):
        # Older frozen providers expose registration through container env;
        # newer providers keep that credential exclusively on the host.
        if (self.feedback is not None or self.checkpoint is not None) and (
                "host_registration_callback" in inspect.signature(run_agent).parameters):
            return {"host_registration_callback": self.bind_registration}
        return {}

    def bind_registration(self, token):
        if self.registration_bound or not isinstance(token, str) or not token:
            raise ValueError("Observers require one native host registration")
        environment = {"SFORGE_TOKEN": token}
        for observer in (self.feedback, self.checkpoint):
            if observer is not None:
                observer.bind(environment)
        self.registration_bound = True

    def create_container(self, image, name, **kwargs):
        hosts = dict(kwargs.get("extra_hosts") or {})
        if self.docker_host_bridge:
            # Docker Desktop/VM runtimes can resolve `host-gateway` to the
            # desktop host, while a runner inside the VM serves its gateway
            # on the Docker bridge. Select that topology explicitly, without
            # guessing from the runner OS or hardcoding a bridge subnet.
            config = self.client.networks.get("bridge").attrs["IPAM"]["Config"]
            addresses = [ipaddress.ip_address(item["Gateway"])
                         for item in config if item.get("Gateway")]
            gateways = {str(ip) for ip in addresses if ip.version == 4
                        and not (ip.is_loopback or ip.is_unspecified or ip.is_multicast)}
            if len(gateways) != 1:
                raise RuntimeError("Docker host bridge requires one routable IPv4 gateway")
            hosts["host.docker.internal"] = gateways.pop()
        if self.auth_ips:
            hosts["auth.openai.com"] = self.auth_ips[0]
        kwargs["extra_hosts"] = hosts
        if not self.registration_bound and (self.feedback is not None or self.checkpoint is not None):
            self.bind_registration((kwargs.get("environment") or {}).get("SFORGE_TOKEN"))
        kwargs["environment"] = self._agent_environment(kwargs.get("environment"))
        return super().create_container(image, name, **kwargs)

    def _agent_environment(self, environment):
        if self.blind_api_endpoint is None or environment is None:
            return environment
        return {key: value for key, value in environment.items()
                if key not in {"SFORGE_TOKEN", "SFORGE_JUDGE_URL", "SFORGE_FEEDBACK_URL"}}

    def exec_run_with_timeout(self, handle, cmd, timeout=60, **kwargs):
        kwargs["environment"] = self._agent_environment(kwargs.get("environment"))
        is_solver = cmd == ["/bin/bash", "-c", getattr(self, "execution_command", None)]
        if is_solver and self.checkpoint is not None:
            self.checkpoint.start(handle.raw)
        try:
            result = super().exec_run_with_timeout(handle, cmd, timeout, **kwargs)
            if is_solver:
                # SForge's RunResult discards the process exit code. Preserve
                # structured transport evidence without inspecting output text.
                marker = getattr(self, "execution_start_marker", None)
                execution_started = marker is None or super().exec_run(
                    handle, ["test", "-f", marker], user="root").exit_code == 0
                self.execution_receipt = dict(exit_code=result.exit_code,
                    execution_started=execution_started,
                    timed_out=result.timed_out, elapsed_seconds=result.elapsed_seconds)
                target = self.log_dir.parent / "execution-receipt.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                pending = target.with_suffix(".tmp")
                pending.write_text(json.dumps(self.execution_receipt))
                pending.replace(target)
            return result
        finally:
            if is_solver and self.checkpoint is not None:
                self.checkpoint.close()
            if is_solver and self.feedback is not None:
                self.feedback.pause()

    def create_network_isolation(self, handle, allowed_endpoints, logger):
        # ChatGPT login may refresh during an 18h run. Preserve the native
        # host-side firewall and add only the OAuth endpoint, for every arm.
        endpoints = list(allowed_endpoints)
        if self.blind_api_endpoint is not None:
            # The native firewall remains host-owned. No judge route enters
            # either IPv4 or IPv6 policy, even if a task learns its address.
            endpoints = [endpoint for endpoint in endpoints
                         if (endpoint.ip, endpoint.port) == self.blind_api_endpoint]
            if len(endpoints) != 1:
                raise RuntimeError(
                    "Blind feedback requires one admitted API-only endpoint distinct from the judge"
                )
        endpoints = [*endpoints, *[
            AllowedEndpoint(ip=ip, port=443, hostname="auth.openai.com")
            for ip in self.auth_ips
        ]]
        return super().create_network_isolation(handle, endpoints, logger)

    def start_feedback(self, handle):
        if self.feedback is None:
            raise RuntimeError("Best-only publisher is unavailable")
        self.feedback.start(self, handle)

    def cleanup_container(self, handle, logger=None):
        try:
            if self.checkpoint is not None:
                self.checkpoint.close()
            if self.feedback is not None:
                self.feedback.close()
            if handle is not None:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                observations = []
                # Never copy the whole Codex home: auth.json must stay private in
                # the source home and disposable trial, outside collected artifacts.
                for remote, name in [("/logs/agent", "runtime"),
                                     ("/home/agent/.codex/sessions", "native-sessions"),
                                     (f"{_CODEX_HOME}/sessions", "worker-sessions"),
                                     (_CONTROL, "worker-control"),
                                     (_LOOPX_RUNTIME, "loopx-state")]:
                    try:
                        data = self.copy_from_container(handle, PurePosixPath(remote))
                        target = self.log_dir / name
                        target.mkdir(exist_ok=True)
                        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                            archive.extractall(target, filter="data")
                        observations.append({"artifact": name, "collected": True})
                    except Exception as error:
                        observations.append({"artifact": name, "collected": False,
                                             "error_kind": type(error).__name__})
                try:
                    self.provider_failure_receipt = observe_terminal_provider_failure(
                        self.log_dir / "native-sessions" / "sessions",
                        self.log_dir / "worker-sessions" / "sessions",
                    )
                except Exception as error:
                    # This receipt is optional diagnosis. A malformed future
                    # event must not change the run result or resource cleanup.
                    self.provider_failure_receipt = None
                    observations.append({"artifact": "provider-failure-observation",
                                         "collected": False,
                                         "error_kind": type(error).__name__})
                (self.log_dir / "artifact-collection.json").write_text(json.dumps(observations))
                # A native-Goal worker owns exactly one wake (including opt-in
                # continuation Goals). Read its retained receipt after collection;
                # don't infer completion from a shell exit or solver output prose.
                wakes = self.log_dir / "runtime" / "agent" / "wakes"
                native = []
                for path in wakes.glob("*/receipt.json"):
                    try:
                        receipt = json.loads(path.read_text())
                        if isinstance(receipt, dict) and receipt.get("mode") in {"native-goal", "loopx-goal"}:
                            native.append(receipt)
                    except (OSError, ValueError):
                        continue  # Collection gaps retain the transport failure.
                if len(native) == 1:
                    self.native_goal_receipt = {key: native[0][key] for key in
                        ("native_goal_outcome", "native_goal_rounds", "native_goal") if key in native[0]}
                elif native:
                    self.native_goal_receipt = {"native_goal_outcome": "unknown"}
        finally:
            try:
                super().cleanup_container(handle, logger)
            finally:
                if self.feedback is not None:
                    # Native cleanup logs some failures instead of raising. Verify the
                    # solver is absent before returning its capacity to another trial.
                    name = handle.name if handle is not None else (
                        f"sforge.run.{self.feedback.task_id}.{self.feedback.run_id}")
                    if self.container_exists(name):
                        raise RuntimeError("Worker remains present; online capacity was not released")
                    self.feedback.release_registration()
