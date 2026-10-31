"""Explicit Docker topology keeps container pins aligned with native isolation."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("enabled", [False, True], ids=["docker-default", "docker-bridge"])
def test_host_bridge_is_explicit_and_preserves_other_container_options(tmp_path, monkeypatch, enabled):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    queries, calls = [], []

    def network(name):
        queries.append(name)
        return SimpleNamespace(attrs={"IPAM": {"Config": [
            {"Subnet": "192.0.2.0/24", "Gateway": "192.0.2.1"},
            {"Subnet": "2001:db8::/64", "Gateway": "2001:db8::1"},
        ]}})

    backend = RecordingDockerBackend(log_dir=tmp_path, logger=None, oauth_proxy=True,
        client=SimpleNamespace(networks=SimpleNamespace(get=network)),
        **({"docker_host_bridge": True} if enabled else {}))
    backend.auth_ips = ["198.51.100.1"]
    monkeypatch.setattr(DockerBackend, "create_container",
                        lambda self, image, name, **kwargs: calls.append(kwargs))
    hosts = {"host.docker.internal": "host-gateway", "unrelated.invalid": "203.0.113.1"}
    environment = {"OPENAI_API_KEY": "synthetic-placeholder"}
    backend.create_container("fixture", "fixture", extra_hosts=hosts, environment=environment,
                             cap_drop=["NET_RAW"], mem_limit="256m")

    assert hosts["host.docker.internal"] == "host-gateway", "do not mutate caller configuration"
    assert queries == (["bridge"] if enabled else [])
    assert calls == [{"extra_hosts": {**hosts, "auth.openai.com": "198.51.100.1",
        "host.docker.internal": "192.0.2.1" if enabled else "host-gateway"},
        "environment": environment, "cap_drop": ["NET_RAW"], "mem_limit": "256m"}]


@pytest.mark.parametrize("gateways", [[], ["2001:db8::1"], ["0.0.0.0"], ["127.0.0.1"],
                                    ["224.0.0.1"], ["192.0.2.1", "198.51.100.1"]])
def test_unusable_or_ambiguous_bridge_fails_before_container_creation(tmp_path, monkeypatch, gateways):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    network = SimpleNamespace(attrs={"IPAM": {"Config": [{"Gateway": ip} for ip in gateways]}})
    backend = RecordingDockerBackend(log_dir=tmp_path, logger=None, oauth_proxy=True,
        docker_host_bridge=True, client=SimpleNamespace(networks=SimpleNamespace(get=lambda name: network)))
    monkeypatch.setattr(DockerBackend, "create_container", lambda *a, **k: pytest.fail("invalid route"))
    with pytest.raises(RuntimeError, match="one routable IPv4 gateway"):
        backend.create_container("fixture", "fixture")


def test_host_bridge_does_not_weaken_native_or_blind_endpoint_selection(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    from sforge.harness.network_isolation import build_allowed_endpoints
    import logging
    backend = RecordingDockerBackend(log_dir=tmp_path, logger=None, oauth_proxy=True,
                                    docker_host_bridge=True, client=SimpleNamespace())
    endpoints = build_allowed_endpoints("http://host.docker.internal:8080",
        "http://host.docker.internal:9090", "192.0.2.1", logging.getLogger(__name__))
    monkeypatch.setattr(DockerBackend, "create_network_isolation", lambda self, handle, allowed, logger: allowed)
    assert {(ep.ip, ep.port) for ep in backend.create_network_isolation(None, endpoints, None)} == {
        ("192.0.2.1", 8080), ("192.0.2.1", 9090)}
    backend.blind_api_endpoint = ("192.0.2.1", 9090)
    assert [(ep.ip, ep.port) for ep in backend.create_network_isolation(None, endpoints, None)] == [
        ("192.0.2.1", 9090)]
