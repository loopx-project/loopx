"""Native Docker transport with private artifact collection and OAuth egress."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path, PurePosixPath

from sforge.harness.backend.docker_backend import DockerBackend
from sforge.harness.network_isolation import AllowedEndpoint, resolve_hostname


class RecordingDockerBackend(DockerBackend):
    def __init__(self, *, log_dir: Path, logger, oauth_proxy: bool = False, client=None):
        super().__init__(client)
        self.log_dir, self.logger = log_dir, logger
        self.auth_ips = [] if oauth_proxy else resolve_hostname("auth.openai.com", logger)
        if not oauth_proxy and not self.auth_ips:
            raise RuntimeError("Cannot resolve Codex OAuth endpoint")

    def create_container(self, image, name, **kwargs):
        hosts = dict(kwargs.get("extra_hosts") or {})
        if self.auth_ips:
            hosts["auth.openai.com"] = self.auth_ips[0]
        kwargs["extra_hosts"] = hosts
        return super().create_container(image, name, **kwargs)

    def create_network_isolation(self, handle, allowed_endpoints, logger):
        # ChatGPT login may refresh during an 18h run. Preserve the native
        # host-side firewall and add only the OAuth endpoint, for every arm.
        endpoints = [*allowed_endpoints, *[
            AllowedEndpoint(ip=ip, port=443, hostname="auth.openai.com")
            for ip in self.auth_ips
        ]]
        return super().create_network_isolation(handle, endpoints, logger)

    def cleanup_container(self, handle, logger=None):
        if handle is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            observations = []
            # Never copy the whole Codex home: auth.json must stay private in
            # the source home and disposable trial, outside collected artifacts.
            for remote, name in [("/logs/agent", "runtime"),
                                 ("/home/agent/.codex/sessions", "native-sessions")]:
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
            (self.log_dir / "artifact-collection.json").write_text(json.dumps(observations))
        super().cleanup_container(handle, logger)
