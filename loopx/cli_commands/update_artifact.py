"""Bootstrap IO for prebuilt snapshots (stdlib only, including before Node exists).

The website exports a standalone copy through ``standalone_source``. Shared
metadata grammars are bound at export time, so that copy needs only stdlib
before LoopX or Node exists. Candidate validation and activation still reuse
install-local.sh.
"""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..control_plane.content_digest import BARE_SHA256_PATTERN as HASH
from ..capabilities.issue_fix.repository_snapshot import _REPO_PATTERN as REPOSITORY_PATTERN

RUNTIME_ARTIFACT_SCHEMA_VERSION = "loopx_runtime_artifact_v1"
SHA = re.compile(r"[0-9a-f]{40}")
MAX_MANIFEST = 256 * 1024
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024


def standalone_source() -> str:
    """Bind existing grammar owners into the independent bootstrap artifact."""
    source = Path(__file__).read_text(encoding="utf-8")
    for statement, owner_path, symbol, alias in (
        ("from ..control_plane.content_digest import BARE_SHA256_PATTERN as HASH",
         "control_plane/content_digest.py", "BARE_SHA256_PATTERN", "HASH"),
        ("from ..capabilities.issue_fix.repository_snapshot import _REPO_PATTERN as REPOSITORY_PATTERN",
         "capabilities/issue_fix/repository_snapshot.py", "_REPO_PATTERN", "REPOSITORY_PATTERN"),
    ):
        owner = (Path(__file__).resolve().parents[1] / owner_path).read_text(encoding="utf-8")
        assignments = [node for node in ast.parse(owner).body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == symbol for target in node.targets)]
        if len(assignments) != 1:
            raise ValueError("Bootstrap grammar owner no longer has one source binding")
        # Copy the canonical binding verbatim. A consumer must not rebuild a
        # regex by reading and recompiling the compiled owner's pattern text.
        binding = ast.get_source_segment(owner, assignments[0]) + f"\n{alias} = {symbol}"
        # Match import lines only, never this generator's statement literals.
        old = "\n" + statement + "\n"
        if source.count(old) != 1:
            raise ValueError("Bootstrap grammar binding no longer matches its source import")
        source = source.replace(old, "\n" + binding + "\n", 1)
    return source


def safe_url(url: str) -> str:
    parsed = urlsplit(url)
    # HTTP is only useful for disposable loopback qualification servers.
    if (parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    )) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Update sources require HTTPS without embedded credentials")
    return url


class UpdateRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        safe_url(new_url)
        if request.type == "https" and urlsplit(new_url).scheme != "https":
            raise ValueError("Update redirects must preserve TLS")
        return super().redirect_request(request, response, code, message, headers, new_url)


def read_json(url: str) -> dict[str, Any]:
    request = Request(safe_url(url), headers={"User-Agent": "LoopX-update", "Accept": "application/json"})
    try:
        with build_opener(UpdateRedirectHandler()).open(request, timeout=10) as response:
            raw = response.read(MAX_MANIFEST + 1)
        if len(raw) > MAX_MANIFEST:
            raise ValueError("Update metadata exceeds its size limit")
        result = json.loads(raw)
    except (OSError, ValueError, HTTPException) as exc:
        # Do not echo proxy credentials, signed URLs or response bodies.
        raise ValueError("Update metadata unavailable; retry online or explicitly use --source") from exc
    if not isinstance(result, dict):
        raise ValueError("Update metadata must be an object")
    return result


def validate_manifest(value: dict[str, Any], *, revision: str | None = None) -> dict[str, Any]:
    if value.get("schema_version") != RUNTIME_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("Unsupported runtime artifact metadata")
    if not SHA.fullmatch(str(value.get("source_revision", ""))):
        raise ValueError("Runtime artifact has no immutable source revision")
    if revision and value["source_revision"] != revision:
        raise ValueError("Runtime artifact belongs to another revision")
    if not HASH.fullmatch(str(value.get("sha256", ""))):
        raise ValueError("Runtime artifact has no SHA-256")
    if type(value.get("size")) is not int or not 0 < value["size"] <= MAX_ARCHIVE:
        raise ValueError("Runtime artifact has an invalid byte length")
    if not isinstance(value.get("package_version"), str) or not value["package_version"]:
        raise ValueError("Runtime artifact has no package version")
    urls = value.get("urls")
    if not isinstance(urls, list) or not urls or len(urls) > 8 or not all(isinstance(u, str) for u in urls):
        raise ValueError("Runtime artifact has no bounded download sources")
    for url in urls:
        safe_url(url)
    return dict(value)


def resolve_artifact(repo: str, ref: str) -> dict[str, Any]:
    if not REPOSITORY_PATTERN.fullmatch(repo):
        raise ValueError("Update repo must use GitHub owner/name syntax")
    if ref == "stable":
        url = f"https://github.com/{repo}/releases/latest/download/loopx-runtime.json"
    elif ref == "main":
        url = f"https://github.com/{repo}/releases/download/runtime-main/loopx-runtime.json"
    elif SHA.fullmatch(ref):
        url = f"https://github.com/{repo}/releases/download/runtime-main/loopx-runtime-{ref}.json"
    elif re.fullmatch(r"v[0-9][A-Za-z0-9.+-]*", ref):
        url = f"https://github.com/{repo}/releases/download/{ref}/loopx-runtime.json"
    else:
        raise ValueError("This ref has no runtime feed; explicitly use --source to build it")
    artifact = validate_manifest(read_json(url), revision=ref if SHA.fullmatch(ref) else None)
    if ref in {"main", "stable"} and artifact.get("channel") != ref:
        raise ValueError("Runtime artifact belongs to another channel")
    return artifact


def target_direction(repo: str, current: str | None, target: str) -> str:
    if current == target:
        return "identical"
    if not SHA.fullmatch(current or ""):
        return "unknown"
    try:
        value = read_json(f"https://api.github.com/repos/{repo}/compare/{current}...{target}")
        return str(value.get("status")) if value.get("status") in {"ahead", "behind", "diverged", "identical"} else "unknown"
    except ValueError:
        return "unknown"


def cache_root() -> Path:
    return Path(os.environ.get("LOOPX_UPDATE_CACHE") or (
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "loopx/updates"
    )).expanduser()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".update-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(raw, path)
    finally:
        Path(raw).unlink(missing_ok=True)


@contextmanager
def artifact_lock(path: Path):
    # Kernel locks are released on process death. A killed downloader leaves
    # its partial bytes/validator, never an unbreakable sentinel lock.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_artifact(manifest: dict[str, Any], *, progress=lambda _event: None,
                      timeout: float = 600, cache: Path | None = None) -> Path:
    manifest = validate_manifest(manifest)
    directory = cache or cache_root()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = f"{manifest['source_revision']}-{manifest['sha256']}"
    final = directory / (key + ".tar.gz")
    partial = directory / (key + ".partial")
    metadata = directory / (key + ".json")
    deadline = time.monotonic() + timeout
    expected = manifest["size"]
    with artifact_lock(directory / (key + ".lock")):
        if final.is_file() and final.stat().st_size == expected and file_hash(final) == manifest["sha256"]:
            progress({"stage": "download", "received": expected, "total": expected, "cached": True})
            return final
        final.unlink(missing_ok=True)
        urls = list(manifest["urls"])
        # Mirrors are operator-configured or publisher-listed, never selected
        # by country/proxy guessing. All serve the same hash-pinned bytes.
        filename = Path(urlsplit(urls[0]).path).name
        for base in os.environ.get("LOOPX_DOWNLOAD_MIRRORS", "").split(","):
            if base.strip():
                urls.append(safe_url(base.strip().rstrip("/") + "/" + filename))
        errors: list[str] = []
        for attempt in range(3 * len(urls)):
            if time.monotonic() >= deadline:
                break
            url = urls[attempt % len(urls)]
            offset, etag = 0, None
            try:
                saved = json.loads(metadata.read_text(encoding="utf-8"))
                candidate = saved.get("etag")
                if (saved.get("url_hash") == hashlib.sha256(url.encode()).hexdigest()
                    and isinstance(candidate, str) and candidate.startswith('"') and not candidate.startswith('W/')
                    and partial.is_file() and partial.stat().st_size < expected):
                    offset, etag = partial.stat().st_size, candidate
            except (OSError, ValueError):
                pass
            headers = {"User-Agent": "LoopX-update", "Accept-Encoding": "identity"}
            if offset and etag:
                headers.update({"Range": f"bytes={offset}-", "If-Range": etag})
            progress({"stage": "download", "received": offset, "total": expected, "attempt": attempt + 1})
            try:
                with build_opener(UpdateRedirectHandler()).open(Request(safe_url(url), headers=headers), timeout=min(10, max(.1, deadline - time.monotonic()))) as response:
                    status = response.status
                    if status == 206:
                        content_range = response.headers.get("Content-Range", "")
                        if not offset or content_range != f"bytes {offset}-{expected - 1}/{expected}" or response.headers.get("ETag") != etag:
                            partial.unlink(missing_ok=True)
                            metadata.unlink(missing_ok=True)
                            raise ValueError("invalid_range")
                    elif status == 200:
                        offset = 0  # Range ignored or validator changed: replace, never append.
                    else:
                        raise ValueError("unexpected_status")
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) != expected - offset:
                        raise ValueError("invalid_length")
                    atomic_json(metadata, {"etag": response.headers.get("ETag"),
                        "url_hash": hashlib.sha256(url.encode()).hexdigest()})
                    received = offset
                    window_start, window_bytes, emitted = time.monotonic(), received, 0.0
                    with partial.open("ab" if offset else "wb") as output:
                        while True:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("download_deadline")
                            block = response.read1(64 * 1024)
                            if not block:
                                break
                            received += len(block)
                            if received > expected:
                                raise ValueError("invalid_length")
                            output.write(block)
                            now = time.monotonic()
                            if now - emitted >= 1:
                                progress({"stage": "download", "received": received, "total": expected})
                                emitted = now
                            # A sustained <16 KiB/s window can switch to another
                            # trusted source. A lone slow packet cannot trigger it.
                            if now - window_start >= 30:
                                if len(urls) > 1 and (received - window_bytes) / (now - window_start) < 16 * 1024:
                                    raise TimeoutError("slow_source")
                                window_start, window_bytes = now, received
                        output.flush()
                        os.fsync(output.fileno())
                if partial.stat().st_size < expected:
                    raise OSError("incomplete_transfer")
                if partial.stat().st_size != expected or file_hash(partial) != manifest["sha256"]:
                    partial.unlink(missing_ok=True)
                    metadata.unlink(missing_ok=True)
                    raise ValueError("integrity_mismatch")
                os.replace(partial, final)
                progress({"stage": "download", "received": expected, "total": expected, "cached": False})
                return final
            except (OSError, ValueError, HTTPException) as exc:
                code = str(exc) if isinstance(exc, ValueError) else (f"http_{exc.code}" if isinstance(exc, HTTPError) else "transport_interrupted")
                errors.append(code if code in {"invalid_range", "invalid_length", "unexpected_status", "integrity_mismatch"} or code.startswith("http_") else "transport_interrupted")
                # Reject stale/complete partials after 416; retries retain only
                # well-formed, validator-bound incomplete transfers.
                if isinstance(exc, HTTPError) and exc.code == 416:
                    partial.unlink(missing_ok=True)
                    metadata.unlink(missing_ok=True)
        raise ValueError("Runtime download failed (" + ", ".join(errors[-3:]) + "); cached partial retained when safe")


def extract_artifact(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if sum(member.size for member in members) > MAX_EXPANDED or len(members) > 100_000:
            raise ValueError("Runtime archive exceeds extraction limits")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name or ":" in member.name or not (member.isfile() or member.isdir()):
                raise ValueError("Runtime archive contains an unsafe entry")
        # No links, traversal, devices or platform-specific path escapes.
        options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        source.extractall(destination, members=members, **options)
    if not (destination / "scripts/install-local.sh").is_file():
        raise ValueError("Runtime archive has no snapshot installer")
    return destination


def run_installer(command: list[str], *, env: dict[str, str], timeout: float,
                  progress=lambda _event: None) -> subprocess.CompletedProcess[str]:
    """Stream only explicit installer events; retain bounded private diagnostics."""
    import threading
    tails = {"stdout": "", "stderr": ""}
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt")
    def consume(name, stream):
        for line in stream:
            if name == "stderr" and line.startswith("LOOPX_UPDATE_EVENT "):
                try:
                    event = json.loads(line[len("LOOPX_UPDATE_EVENT "):])
                    if isinstance(event, dict) and event.get("stage") in {"download", "frontend", "copy", "validate", "migrate", "activate", "skills"}:
                        safe = {"stage": event["stage"]}
                        for field in ("received", "total"):
                            if type(event.get(field)) is int and event[field] >= 0:
                                safe[field] = event[field]
                        progress(safe)
                except ValueError:
                    pass
            else:
                tails[name] = (tails[name] + line)[-16_384:]
    threads = [threading.Thread(target=consume, args=(name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for thread in threads:
        thread.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        import signal
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
        code = 124
    for thread in threads:
        thread.join(timeout=2)
    return subprocess.CompletedProcess(command, code, tails["stdout"], tails["stderr"])


def install_artifact(manifest: dict[str, Any], *, env: dict[str, str], timeout_seconds: int,
                     progress=lambda _event: None):
    started = time.monotonic()
    archive = download_artifact(manifest, progress=progress, timeout=timeout_seconds)
    progress({"stage": "verify"})
    with tempfile.TemporaryDirectory(prefix="loopx-runtime-") as raw:
        root = extract_artifact(archive, Path(raw))
        identity = json.loads((root / "runtime-artifact.json").read_text(encoding="utf-8"))
        if identity.get("source_revision") != manifest["source_revision"] or identity.get("package_version") != manifest["package_version"]:
            raise ValueError("Runtime identity disagrees with its release metadata")
        selected = dict(env)
        selected.update({"LOOPX_RESOLVED_SOURCE_GIT_COMMIT": manifest["source_revision"],
            "LOOPX_ARCHIVE_SHA256": manifest["sha256"], "LOOPX_ARCHIVE_URL": manifest["urls"][0],
            "LOOPX_PREBUILT_RUNTIME": "1", "LOOPX_INSTALL_CANARY": "0", "LOOPX_PROMOTE_DEFAULT": "1",
            "LOOPX_PROMOTION_MODE": "trusted_github_archive"})
        result = run_installer(["bash", str(root / "scripts/install-local.sh")], env=selected,
            timeout=max(.1, timeout_seconds - (time.monotonic() - started)), progress=progress)
    return result, {"stage": "runtime_artifact", "source_revision": manifest["source_revision"],
        "sha256": manifest["sha256"], "bytes": manifest["size"], "elapsed_seconds": round(time.monotonic() - started, 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    parser.add_argument("--ref")
    args = parser.parse_args()
    def report(event):
        print("LOOPX_UPDATE_EVENT " + json.dumps(event), file=sys.stderr, flush=True)
    try:
        report({"stage": "resolve"})
        installed = {}
        entry = Path(os.environ.get("LOOPX_BIN_DIR") or Path.home() / ".local/bin") / "loopx"
        if entry.is_symlink():
            try:
                installed = json.loads((entry.resolve().parents[1] / "release.json").read_text(encoding="utf-8")).get("source", {})
            except (OSError, ValueError):
                pass
        if (entry.exists() or entry.is_symlink()) and not installed:
            raise ValueError("An existing CLI has another or unknown owner; use its `loopx upgrade` or original package manager")
        selected_repo = args.repo or (os.environ.get("LOOPX_REPO") if os.environ.get("LOOPX_UPDATE_REPO_EXPLICIT") == "1" else None) or installed.get("repo") or "loopx-project/loopx"
        selected_ref = args.ref or (os.environ.get("LOOPX_REF") if os.environ.get("LOOPX_UPDATE_REF_EXPLICIT") == "1" else None) or installed.get("ref") or "stable"
        manifest = resolve_artifact(selected_repo, selected_ref)
        if installed and target_direction(selected_repo, installed.get("git_commit"), manifest["source_revision"]) not in {"ahead", "identical"} and os.environ.get("LOOPX_ALLOW_DOWNGRADE") != "1":
            raise ValueError("Older, diverged or unverified target; inspect it before explicitly setting LOOPX_ALLOW_DOWNGRADE=1")
        env = dict(os.environ, LOOPX_REPO=selected_repo, LOOPX_REF=selected_ref, LOOPX_UPDATE_CHANNEL=selected_ref, LOOPX_UPDATE_EVENTS="1")
        result, _ = install_artifact(manifest, env=env,
            timeout_seconds=int(os.environ.get("LOOPX_INSTALLER_TIMEOUT_SECONDS", "600")), progress=report)
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        return result.returncode
    except (OSError, ValueError) as exc:
        print(f"LoopX runtime update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
