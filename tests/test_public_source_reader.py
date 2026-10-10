"""Public evidence must not acquire account, local-network or file authority."""
import io
import ipaddress
import socket
import time
from pathlib import Path

import pytest
from PIL import Image

from loopx.extensions import public_source_reader as reader
from loopx.capabilities.native_chat import codex_context


# Synthetic RFC1918 range fixtures are expressed as integers; these are not operator addresses.
@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.org/", "https://user:pass@example.org/",
    "https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/",
    f"https://{ipaddress.IPv4Address(0x0A000001)}/",
    "https://example.org:8443/", "https://example.org/\nheader", "https://example.org\\@127.0.0.1/",
    "https://224.0.0.1/", "https://[2002:7f00:1::]/"])
def test_nonpublic_or_authenticated_urls_rejected_before_network(monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("DNS must not be called"))
    with pytest.raises(ValueError):
        reader.public_url(url)


@pytest.mark.parametrize("values", [["127.0.0.1"], ["8.8.8.8", str(ipaddress.IPv4Address(0xC0A80102))], ["::ffff:127.0.0.1"], ["100.64.0.1"]])
def test_dns_private_and_mixed_results_rejected(monkeypatch, values):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (v, 443)) for v in values])
    with pytest.raises(ValueError):
        reader.public_addresses("public-name.example")


def test_checked_address_is_the_connected_address_and_tls_hostname_stays_original(monkeypatch):
    raw = object()
    observed = []
    monkeypatch.setattr(socket, "create_connection", lambda target, timeout: observed.append(target) or raw)
    connection = reader._PinnedHTTPS("example.org", ["8.8.8.8"], 2)
    class TLS:
        def wrap_socket(self, sock, *, server_hostname):
            assert sock is raw and server_hostname == "example.org"
            return "tls-socket"
    connection._context = TLS()
    connection.connect()
    assert observed == [("8.8.8.8", 443)] and connection.sock == "tls-socket"


def test_redirect_cannot_reach_private_network_and_cookies_are_not_reused(monkeypatch):
    calls = []
    monkeypatch.setattr(reader, "public_addresses", lambda host: ["8.8.8.8"])
    class Response:
        status = 302
        def getheader(self, name, default=None):
            return {"Location": "https://127.0.0.1/private", "Set-Cookie": "private=fixture"}.get(name, default)
    class Connection:
        def __init__(self, *a): pass
        def request(self, method, path, *, headers): calls.append((method, path, headers))
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(reader, "_PinnedHTTPS", Connection)
    with pytest.raises(ValueError):
        reader.fetch("https://example.org/start", deadline=time.monotonic() + 5)
    assert len(calls) == 1 and calls[0][:2] == ("GET", "/start")
    assert set(calls[0][2]) == {"Accept-Encoding", "User-Agent"}


def test_static_html_coverage_and_images_are_explicit(monkeypatch):
    body = b'<html><h1>Public title</h1><script>private script</script><p>Final paragraph.</p><img src="/figure.svg" alt="Chart"></html>'
    monkeypatch.setattr(reader, "fetch", lambda *a, **k: ("https://example.org/article", "text/html", body))
    result = reader.article("https://example.org/article", deadline=time.monotonic() + 5)
    assert result["text"] == "Public title\nFinal paragraph."
    assert result["images"] == [{"index": 0, "url": "https://example.org/figure.svg", "alt": "Chart"}]
    assert result["images_read"] is False and "static response" in result["coverage"]


@pytest.mark.parametrize("element", [
    '<image href="file:///etc/passwd"/>', '<use href="https://example.org/a.svg#shape"/>',
    '<rect fill="url(file:///private/secret)"/>', '<style>@import "https://example.org/private";</style>',
    '<rect style="fill:u\\72l(file:///private/secret)"/>', '<foreignObject/>', '<script/>',
    '<use href="#shape" xml:base="file:///private/secret"/>',
])
def test_svg_cannot_fetch_external_or_local_resources(element):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">{element}</svg>'.encode()
    with pytest.raises(ValueError):
        reader.image_png(svg, "image/svg+xml")


def test_actual_svg_pixels_preserve_geometry_and_arrow_direction():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="white"/><path d="M10 20H60L50 10M60 20L50 30" fill="none" stroke="black" stroke-width="4"/></svg>'
    png = reader.image_png(svg, "image/svg+xml")
    image = Image.open(io.BytesIO(png))
    assert image.size == (80, 40)
    assert image.getpixel((60, 20)) == (0, 0, 0, 255)
    assert image.getpixel((70, 20)) == (255, 255, 255, 255)


def test_transparent_pixels_are_preserved_instead_of_becoming_black():
    source = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    result = Image.open(io.BytesIO(reader.image_png(buffer.getvalue(), "image/png")))
    assert result.getpixel((0, 0)) == (0, 0, 0, 0)


@pytest.mark.parametrize("size", [(5000, 10), (10, 5000)])
def test_oversized_svg_rejected_before_render(size):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{size[0]}" height="{size[1]}"/>'.encode()
    with pytest.raises(ValueError):
        reader.image_png(svg, "image/svg+xml")


def test_public_provider_disabled_by_default_and_operator_only(monkeypatch):
    effective = {"mcp_servers": {"personal": {"command": "private", "env": {"SECRET": "fixture"}}}}
    disabled = codex_context.disable_mcp_servers(effective, {})
    monkeypatch.delenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", raising=False)
    assert codex_context.public_source_reader(effective, disabled) == disabled
    monkeypatch.setenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", "on")
    enabled = codex_context.public_source_reader(effective, disabled)
    assert enabled["mcp_servers"]["personal"] == {"enabled": False}
    public = enabled["mcp_servers"]["loopx_public_source_read"]
    assert public["env_vars"] == [] and list(public["env"]) == ["PATH"]
    assert public["args"] == ["-I", str(Path(reader.__file__).resolve())]
    assert effective["mcp_servers"]["personal"]["command"] == "private"
    with pytest.raises(ValueError):
        codex_context.public_source_reader({"mcp_servers": {"loopx_public_source_read": {"env": {"SECRET": "fixture"}}}}, {})
    monkeypatch.setenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", "yes")
    with pytest.raises(ValueError):
        codex_context.public_source_reader({}, {})


def test_dependency_failure_prevents_provider_admission(monkeypatch):
    monkeypatch.setenv("LOOPX_CHAT_PUBLIC_SOURCE_READ", "on")
    monkeypatch.setattr(codex_context.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ValueError, match=r"Install loopx\[public-source-reader\]"):
        codex_context.public_source_reader({}, {})


def test_worker_deadline_and_environment_are_not_account_owned(monkeypatch):
    import subprocess
    def blocked(command, **kwargs):
        assert command[1:] == ["-I", str(Path(reader.__file__).resolve()), "--observe"]
        assert kwargs["timeout"] == 30 and list(kwargs["env"]) == ["PATH"]
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(reader.subprocess, "run", blocked)
    assert reader.read_public_url("https://example.org/")["ok"] is False


def test_writable_workspace_and_pythonpath_cannot_replace_trusted_provider(tmp_path):
    import json
    import os
    import subprocess
    import sys
    package = tmp_path / "loopx" / "extensions"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    marker = tmp_path / "workspace-code-ran"
    (package / "public_source_reader.py").write_text(
        "from pathlib import Path; Path('workspace-code-ran').write_text('untrusted')")
    result = subprocess.run([sys.executable, "-I", str(Path(reader.__file__).resolve()), "--observe"],
        cwd=tmp_path, env={"PATH": os.defpath, "PYTHONPATH": str(tmp_path)},
        input=json.dumps({"kind": "text", "url": "file:///etc/passwd"}).encode(),
        capture_output=True, timeout=5)
    assert result.returncode != 0 and b"invalid observation" not in result.stderr
    assert b"anonymous HTTPS source required" in result.stderr
    assert not marker.exists()


def test_source_only_host_worker_needs_no_site_installed_loopx(tmp_path, monkeypatch):
    import subprocess
    import sys
    import venv
    host = tmp_path / "source-only-host"
    venv.EnvBuilder(with_pip=False).create(host)
    interpreter = host / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    absent = subprocess.run([str(interpreter), "-I", "-c",
        "import importlib.util; assert importlib.util.find_spec('loopx') is None"],
        capture_output=True, check=True)
    assert absent.stdout == b""
    # The worker must reach its URL boundary through the host release, instead
    # of failing to import LoopX or needing another installed package revision.
    monkeypatch.setattr(reader.sys, "executable", str(interpreter))
    with pytest.raises(subprocess.CalledProcessError) as error:
        reader._observation("text", "file:///etc/passwd")
    assert b"anonymous HTTPS source required" in error.value.stderr
    assert b"No module named" not in error.value.stderr
