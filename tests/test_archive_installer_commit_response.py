"""Exercise the shipped shell installer with a large GitHub commit response."""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import threading
import time

import pytest


def _start_partial_archive_server(payload, *, complete_resume):
    requests = []
    partial_size = max(1, len(payload) // 3)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            range_header = self.headers.get("Range")
            requests.append(range_header)
            if len(requests) == 1:
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload[:partial_size])
                self.wfile.flush()
                time.sleep(3)
                return
            if not complete_resume:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            expected_range = f"bytes={partial_size}-"
            if range_header != expected_range:
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Length", str(len(payload) - partial_size))
            self.send_header(
                "Content-Range",
                f"bytes {partial_size}-{len(payload) - 1}/{len(payload)}",
            )
            self.end_headers()
            self.wfile.write(payload[partial_size:])

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, requests, partial_size


@pytest.mark.skipif(os.name == "nt", reason="POSIX archive installer")
def test_invalid_commit_override_precedes_temp_directory_failure(tmp_path):
    source = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOOPX_")}
    env.update(
        TMPDIR=str(tmp_path / "missing"),
        LOOPX_PYTHON=sys.executable,
        LOOPX_RESOLVED_SOURCE_GIT_COMMIT="not-a-full-sha",
    )

    result = subprocess.run(
        ["bash", str(source / "scripts/install-from-github.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert (
        "LOOPX_RESOLVED_SOURCE_GIT_COMMIT must be a full Git commit SHA"
        in result.stderr
    )
    assert "mktemp" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX archive installer")
@pytest.mark.parametrize("valid", [True, False, "nonhex"])
@pytest.mark.parametrize("ref_kind", ["branch", "sha"])
@pytest.mark.parametrize("api_mode", ["public", "authenticated", "unavailable"])
def test_commit_response_uses_file_transport_and_cleans_up(tmp_path, valid, ref_kind, api_mode):
    source = Path(__file__).resolve().parents[1]
    sha = "a" * 40
    fixture = tmp_path / "response.json"
    fixture.write_text(
        json.dumps({"sha": "z" * 40 if valid == "nonhex" else sha if valid else None,
                    "patch": "x" * 524288})
    )
    package = tmp_path / "package" / "scripts"
    package.mkdir(parents=True)
    installer = package / "install-local.sh"
    installer.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$LOOPX_RESOLVED_SOURCE_GIT_COMMIT" > "$TEST_RECEIPT"\n'
    )
    installer.chmod(0o755)
    archive = tmp_path / "package.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(package.parent, arcname="package")
    binary = tmp_path / "bin"
    binary.mkdir()
    curl = binary / "curl"
    curl.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -c "
        + shlex.quote(
            "import json,os,sys,shutil; "
            "args=sys.argv[1:]; "
            "is_api=any('api.github.com' in a for a in args); "
            "open(os.environ['TEST_CALLS'],'a').write('api\\n' if is_api else 'archive\\n'); "
            "open(os.environ['TEST_ARCHIVE_ARGS'],'w').write(json.dumps(args)) if not is_api else None; "
            "sys.exit(22) if is_api and (os.environ['TEST_REF_KIND']=='sha' or os.environ['TEST_API_MODE']!='public') else None; "
            "source=os.environ['TEST_RESPONSE'] if any('api.github.com' in a for a in args) "
            "else os.environ['TEST_ARCHIVE']; "
            "target=args[args.index('-o')+1] if '-o' in args else None; "
            "shutil.copyfile(source,target) if target else sys.stdout.write(open(source).read())"
        )
        + ' "$@"\n'
    )
    curl.chmod(0o755)
    gh = binary / "gh"
    gh.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(
        "import os,sys; "
        "assert sys.argv[1:]==['api','--hostname','github.com','/repos/loopx-project/loopx/commits/stable']; "
        "open(os.environ['TEST_CALLS'],'a').write('authenticated\\n'); "
        "sys.exit(1) if os.environ['TEST_API_MODE']=='unavailable' else None; "
        "sys.stdout.write(open(os.environ['TEST_RESPONSE']).read())"
    ) + ' "$@"\n')
    gh.chmod(0o755)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    receipt = tmp_path / "receipt"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOOPX_")}
    env.update(
        PATH=f"{binary}{os.pathsep}{env['PATH']}",
        TMPDIR=str(scratch),
        LOOPX_PYTHON=sys.executable,
        LOOPX_REF=sha if ref_kind == "sha" else "stable",
        LOOPX_INSTALLER_TIMEOUT_SECONDS="480",
        TEST_REF_KIND=ref_kind,
        TEST_API_MODE=api_mode,
        TEST_ARCHIVE_ARGS=str(tmp_path / "archive-args.json"),
        TEST_CALLS=str(tmp_path / "calls"),
        TEST_RESPONSE=str(fixture),
        TEST_ARCHIVE=str(archive),
        TEST_RECEIPT=str(receipt),
    )
    result = subprocess.run(
        ["bash", str(source / "scripts/install-from-github.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if ref_kind == "branch" and api_mode == "unavailable":
        assert result.returncode != 0
        assert "verified full commit SHA" in result.stderr
        assert not receipt.exists()
    elif valid is True or ref_kind == "sha":
        assert result.returncode == 0, result.stderr
        assert receipt.read_text().strip() == sha
    else:
        assert result.returncode != 0
        assert "did not include a full SHA" in result.stderr
        assert not receipt.exists()
    assert list(scratch.iterdir()) == []
    calls = (tmp_path / "calls").read_text().splitlines()
    if ref_kind == "sha":
        assert calls == ["archive"]
    else:
        assert calls[0] == "api"
        assert ("authenticated" in calls) == (api_mode != "public")
    if "archive" in calls:
        archive_args = json.loads((tmp_path / "archive-args.json").read_text())
        assert archive_args[archive_args.index("--max-time") + 1] == "120"
        assert "--retry" not in archive_args
        assert archive_args[archive_args.index("--continue-at") + 1] == "-"


@pytest.mark.skipif(os.name == "nt", reason="POSIX archive installer")
def test_archive_download_resumes_after_attempt_timeout(tmp_path):
    source = Path(__file__).resolve().parents[1]
    package = tmp_path / "package"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    installer = scripts / "install-local.sh"
    installer.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$LOOPX_ARCHIVE_SHA256" > "$TEST_RECEIPT"\n'
    )
    installer.chmod(0o755)
    (package / "payload.bin").write_bytes(bytes(range(256)) * 4096)
    archive = tmp_path / "package.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(package, arcname="package")
    payload = archive.read_bytes()
    server, thread, requests, partial_size = _start_partial_archive_server(
        payload, complete_resume=True
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    receipt = tmp_path / "receipt"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOOPX_")}
    env.update(
        TMPDIR=str(scratch),
        LOOPX_PYTHON=sys.executable,
        LOOPX_ARCHIVE_URL=f"http://127.0.0.1:{server.server_port}/archive.tar.gz",
        LOOPX_INSTALLER_TIMEOUT_SECONDS="4",
        TEST_RECEIPT=str(receipt),
    )
    try:
        result = subprocess.run(
            ["bash", str(source / "scripts/install-from-github.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert requests[:2] == [None, f"bytes={partial_size}-"]
    assert receipt.read_text().strip() == hashlib.sha256(payload).hexdigest()
    assert list(scratch.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX archive installer")
def test_archive_timeout_failure_never_extracts_partial_file(tmp_path):
    source = Path(__file__).resolve().parents[1]
    payload = bytes(range(256)) * 4096
    server, thread, requests, partial_size = _start_partial_archive_server(
        payload, complete_resume=False
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    tar_marker = tmp_path / "tar-called"
    tar = binary / "tar"
    tar.write_text(
        '#!/bin/sh\nprintf called > "$TEST_TAR_MARKER"\nexit 99\n'
    )
    tar.chmod(0o755)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOOPX_")}
    env.update(
        PATH=f"{binary}{os.pathsep}{env['PATH']}",
        TMPDIR=str(scratch),
        LOOPX_PYTHON=sys.executable,
        LOOPX_ARCHIVE_URL=f"http://127.0.0.1:{server.server_port}/archive.tar.gz",
        LOOPX_INSTALLER_TIMEOUT_SECONDS="4",
        TEST_TAR_MARKER=str(tar_marker),
    )
    try:
        result = subprocess.run(
            ["bash", str(source / "scripts/install-from-github.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode != 0
    assert requests[:2] == [None, f"bytes={partial_size}-"]
    assert not tar_marker.exists()
    assert list(scratch.iterdir()) == []
