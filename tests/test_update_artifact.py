"""Qualification over real HTTP/files, including interrupted and hostile transfers."""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import subprocess
import sys
import tarfile
import threading

import pytest

from loopx.cli_commands import update_artifact as update


def test_exported_bootstrap_validates_metadata_without_an_installed_package(tmp_path):
    script = tmp_path / "bootstrap.py"
    script.write_text(update.standalone_source(), encoding="utf-8")
    descriptor = manifest(b"runtime", "https://publisher.invalid/runtime.tar.gz")
    probe = """
import json,runpy,sys
module=runpy.run_path(sys.argv[1])
value=json.load(sys.stdin)
assert module['validate_manifest'](value)==value
for invalid in ['A'*64,'a'*63,'a'*65,'sha256:'+('a'*64),'a'*63+'g']:
    try:module['validate_manifest']({**value,'sha256':invalid})
    except ValueError:pass
    else:raise AssertionError('invalid digest accepted')
assert not any(name=='loopx' or name.startswith('loopx.') for name in sys.modules)
print('standalone metadata boundary passed')
"""
    result = subprocess.run([sys.executable, "-I", "-c", probe, str(script)],
        cwd=tmp_path, input=json.dumps(descriptor), capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "standalone metadata boundary passed"


@contextmanager
def server(body: bytes):
    state = {"requests": [], "interrupt": False, "offline": False, "ignore_range": False,
             "etag": '"one"', "no_length": False, "wrong_range": False}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if state.get("redirect"):
                self.send_response(302)
                self.send_header("Location", state["redirect"])
                self.end_headers()
                return
            offset = int(self.headers.get("Range", "bytes=0-")[6:-1])
            state["requests"].append((self.path, self.headers.get("Range"), self.headers.get("If-Range")))
            if state["offline"] or self.path == "/bad":
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if state["ignore_range"]:
                offset = 0
            self.send_response(206 if offset else 200)
            self.send_header("ETag", state["etag"])
            if not state["no_length"]:
                self.send_header("Content-Length", str(len(body) - offset))
            self.send_header("Connection", "close")
            if offset:
                self.send_header("Content-Range", f"bytes {offset + int(state['wrong_range'])}-{len(body) - 1}/{len(body)}")
            self.end_headers()
            if state["interrupt"]:
                state["offline"] = True
                self.wfile.write(body[:len(body) // 2])
                self.wfile.flush()
                self.close_connection = True
            else:
                self.wfile.write(body[offset:])
        def log_message(self, *_args):
            pass
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}/runtime.tar.gz", state
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def manifest(body: bytes, url: str):
    return {"schema_version": update.RUNTIME_ARTIFACT_SCHEMA_VERSION, "source_revision": "a" * 40,
        "package_version": "1.2.3", "channel": "main", "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "urls": [url]}


def test_redirects_cannot_weaken_source_tls():
    from urllib.request import Request
    handler = update.UpdateRedirectHandler()
    with pytest.raises(ValueError, match="preserve TLS"):
        handler.redirect_request(Request("https://publisher.invalid/runtime"), None, 302, "", {}, "http://127.0.0.1/runtime")
    with server(b"{}") as (url, state):
        state["redirect"] = "http://publisher.invalid/runtime"
        with pytest.raises(ValueError, match="metadata unavailable") as caught:
            update.read_json(url)
        assert "require HTTPS" in str(caught.value.__cause__)


def test_pinned_runtime_metadata_is_revision_addressed_within_the_channel_release(monkeypatch):
    body = b"runtime"
    descriptor = manifest(body, "https://publisher.invalid/runtime.tar.gz")
    requests = []
    def metadata(url):
        requests.append(url)
        return descriptor
    monkeypatch.setattr(update, "read_json", metadata)
    assert update.resolve_artifact("example/loopx", "a" * 40)["source_revision"] == "a" * 40
    assert requests == ["https://github.com/example/loopx/releases/download/runtime-main/loopx-runtime-" + "a" * 40 + ".json"]


@pytest.mark.parametrize("ignore_range,new_etag,wrong_range", [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_cross_invocation_resume_validates_origin_range_and_etag(tmp_path, ignore_range, new_etag, wrong_range):
    body = os.urandom(192 * 1024)
    with server(body) as (url, state):
        descriptor = manifest(body, url)
        state["interrupt"] = True
        with pytest.raises(ValueError, match="download failed"):
            update.download_artifact(descriptor, cache=tmp_path)
        partial = next(tmp_path.glob("*.partial"))
        assert partial.stat().st_size == len(body) // 2
        state.update(offline=False, interrupt=False, ignore_range=ignore_range, wrong_range=wrong_range)
        if new_etag:
            state["etag"] = '"two"'
        boundary = len(state["requests"])
        result = update.download_artifact(descriptor, cache=tmp_path)
        assert result.read_bytes() == body
        assert state["requests"][boundary][1:] == (f"bytes={len(body) // 2}-", '"one"')
        if new_etag or wrong_range:
            assert state["requests"][boundary + 1][1] is None
        requests = len(state["requests"])
        state["offline"] = True
        assert update.download_artifact(descriptor, cache=tmp_path) == result
        assert len(state["requests"]) == requests  # Qualified cache is network-free.
        result.write_bytes(b"corrupt")
        with pytest.raises(ValueError):
            update.download_artifact(descriptor, cache=tmp_path)
        assert not result.exists()  # A damaged cache is never returned.


def test_unknown_http_length_has_real_byte_progress_and_expected_manifest_size(tmp_path):
    body = os.urandom(128 * 1024)
    with server(body) as (url, state):
        state["no_length"] = True
        events = []
        result = update.download_artifact(manifest(body, url), cache=tmp_path, progress=events.append)
        assert result.read_bytes() == body
        assert events[-1]["received"] == events[-1]["total"] == len(body)


def test_mirror_serves_only_the_identical_hash_and_bad_bytes_never_install(tmp_path):
    body = os.urandom(8192)
    with server(body) as (url, state):
        descriptor = manifest(body, url)
        descriptor["urls"].insert(0, url.replace("/runtime.tar.gz", "/bad"))
        result = update.download_artifact(descriptor, cache=tmp_path)
        assert result.read_bytes() == body
        assert [entry[0] for entry in state["requests"]] == ["/bad", "/runtime.tar.gz"]
        descriptor["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="integrity_mismatch"):
            update.download_artifact(descriptor, cache=tmp_path)
        assert len(list(tmp_path.glob("*.tar.gz"))) == 1


@pytest.mark.parametrize("url", ["http://example.com/update", "https://user:secret@example.com/update", "file:///tmp/archive"])
def test_update_source_policy_has_no_tls_or_credential_fallback(url):
    with pytest.raises(ValueError):
        update.validate_manifest(manifest(b"bytes", url))


@pytest.mark.parametrize("name,link", [("../escape", False), ("/escape", False), ("C:/escape", False), ("safe/link", True)])
def test_unsafe_archive_is_rejected_before_extraction(tmp_path, name, link):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo(name)
        member.size = 1
        if link:
            member.type, member.linkname = tarfile.SYMTYPE, "../../escape"
        output.addfile(member, None if link else io.BytesIO(b"x"))
    destination = tmp_path / "extracted"
    destination.mkdir()
    with pytest.raises(ValueError, match="unsafe entry"):
        update.extract_artifact(archive, destination)
    assert not list(destination.iterdir())


def test_resolution_pins_exact_artifact_and_never_falls_back_to_codeload(monkeypatch):
    descriptor = manifest(b"bytes", "https://example.com/runtime.tar.gz")
    calls = []
    monkeypatch.setattr(update, "read_json", lambda url: calls.append(url) or descriptor)
    assert update.resolve_artifact("loopx-project/loopx", "main")["source_revision"] == "a" * 40
    assert "/runtime-main/" in calls[0]
    with pytest.raises(ValueError, match="another revision"):
        update.resolve_artifact("loopx-project/loopx", "b" * 40)
    with pytest.raises(ValueError, match="explicitly use --source"):
        update.resolve_artifact("loopx-project/loopx", "developer-branch")


def test_real_installer_streams_only_explicit_events_and_bounds_diagnostics(tmp_path):
    import sys
    events = []
    script = tmp_path / "installer.py"
    script.write_text("import sys\nprint('private output'*3000)\nprint('LOOPX_UPDATE_EVENT {\"stage\":\"activate\"}',file=sys.stderr)\n")
    result = update.run_installer([sys.executable, str(script)], env=dict(os.environ), timeout=5, progress=events.append)
    assert result.returncode == 0
    assert events == [{"stage": "activate"}]
    assert len(result.stdout) <= 16384


def test_bootstrap_inherits_snapshot_choice_without_taking_over_a_package_owner(tmp_path, monkeypatch):
    import subprocess
    import sys
    binary = tmp_path / "bin"
    scripts = tmp_path / "release/scripts"
    scripts.mkdir(parents=True)
    binary.mkdir()
    (scripts / "loopx").touch()
    entry = binary / "loopx"
    entry.symlink_to(scripts / "loopx")
    (scripts.parent / "release.json").write_text(json.dumps({"source": {
        "repo": "example/fork", "ref": "main", "git_commit": "a" * 40}}))
    monkeypatch.setenv("LOOPX_BIN_DIR", str(binary))
    monkeypatch.setenv("LOOPX_UPDATE_REF_EXPLICIT", "0")
    monkeypatch.setenv("LOOPX_UPDATE_REPO_EXPLICIT", "0")
    monkeypatch.setattr(sys, "argv", ["update-artifact.py"])
    selected = []
    monkeypatch.setattr(update, "resolve_artifact", lambda repo, ref: selected.append((repo, ref)) or manifest(b"x", "https://example.com/runtime"))
    monkeypatch.setattr(update, "install_artifact", lambda *_a, **_kw: (subprocess.CompletedProcess([], 0, "", ""), {}))
    assert update.main() == 0
    assert selected == [("example/fork", "main")]
    entry.unlink()
    entry.touch()  # pip/another installer owns a regular console entry.
    assert update.main() == 1
    assert len(selected) == 1
