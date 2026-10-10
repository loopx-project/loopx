"""Compilation reuse cannot replace the original runtime or its admission."""
from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath
import time
from types import SimpleNamespace

import pytest

from loopx.control_plane import effect_runtime


@pytest.mark.parametrize(("root", "expected_preload"), [
    (PureWindowsPath("D:/checkout # %/control_plane"),
     "file:///D:/checkout%20%23%20%25/control_plane/effect_runtime_compile_cache.ts"),
    (PureWindowsPath("//server/share/checkout # %/control_plane"),
     "file://server/share/checkout%20%23%20%25/control_plane/effect_runtime_compile_cache.ts"),
    (PurePosixPath("/checkout # %/control_plane"),
     "file:///checkout%20%23%20%25/control_plane/effect_runtime_compile_cache.ts"),
])
def test_launcher_uses_an_encoded_file_url_for_the_esm_preload(
    tmp_path: Path, monkeypatch, root, expected_preload: str,
) -> None:
    ready = {"ready": True}
    observations = iter([None, ready])
    launches = []
    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: root)
    monkeypatch.setattr(effect_runtime, "_node_executable", lambda: "node")
    monkeypatch.setattr(effect_runtime, "_read_info", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(effect_runtime.subprocess, "Popen", lambda args, **_kwargs:
                        launches.append(args) or SimpleNamespace(poll=lambda: None))
    info = tmp_path / "runtime" / "runtime.json"
    assert effect_runtime._start_runtime(fingerprint="f" * 64, info_path=info) == ready
    argv = launches[0]
    assert argv[argv.index("--import") + 1] == expected_preload
    # The positional script and info argument are filesystem paths, not ESM specifiers.
    assert str(root / "effect_runtime_server.ts") in argv
    assert argv[argv.index("--info") + 1] == str(info)


@pytest.mark.parametrize("cache_mode", ["enabled", "disabled", "unavailable"])
def test_original_launcher_serves_and_restarts_with_optional_cache(
    tmp_path: Path, monkeypatch, cache_mode: str,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    cache = runtime / "compile-cache"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime)
    for name in ("NODE_COMPILE_CACHE", "NODE_DISABLE_COMPILE_CACHE", "NODE_V8_COVERAGE"):
        monkeypatch.delenv(name, raising=False)
    if cache_mode == "disabled":
        monkeypatch.setenv("NODE_DISABLE_COMPILE_CACHE", "1")
    elif cache_mode == "unavailable":
        cache.write_text("not a cache", encoding="utf-8")
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "60000")
    launches: list[list[str]] = []
    original_popen = effect_runtime.subprocess.Popen

    def observed_popen(args, *rest, **kwargs):
        launches.append(list(args))
        return original_popen(args, *rest, **kwargs)

    monkeypatch.setattr(effect_runtime.subprocess, "Popen", observed_popen)
    try:
        first = effect_runtime.effect_runtime_result("runtime.ping", {})
        assert effect_runtime.effect_runtime_result("runtime.ping", {}) == first
        argv = next(args for args in launches if "--info" in args)
        assert argv[argv.index("--import") + 1] == (
            effect_runtime._control_plane_root() / "effect_runtime_compile_cache.ts"
        ).as_uri()
        assert str(effect_runtime._runtime_server_path()) in argv
        fingerprint = effect_runtime._runtime_fingerprint()
        assert argv[argv.index("--fingerprint") + 1] == fingerprint
        info = effect_runtime._read_info(effect_runtime._runtime_info_path(fingerprint),
                                         fingerprint=fingerprint)
        assert info is not None
        with pytest.raises(effect_runtime.EffectRuntimeRejected) as rejected:
            effect_runtime._request_with_info(
                {**info, "token": "not-the-serving-token"}, request_id="wrong-token",
                method="runtime.ping", params={}, timeout=2,
            )
        assert rejected.value.diagnostic_code == "authentication_failed"
        assert effect_runtime.effect_runtime_result("runtime.ping", {}) == first
        assert effect_runtime.restart_effect_runtime()["status"] == "stopped"
        if cache_mode == "enabled":
            deadline = time.monotonic() + 5
            while not any(p.is_file() for p in cache.rglob("*")):
                assert time.monotonic() < deadline, "normal shutdown must populate code cache"
                time.sleep(0.01)
            if hasattr(os, "getuid"):
                assert cache.stat().st_mode & 0o077 == 0
        elif cache_mode == "disabled":
            assert not cache.exists()
        else:
            assert cache.read_text(encoding="utf-8") == "not a cache"
        second = effect_runtime.effect_runtime_result("runtime.ping", {})
        assert second["pid"] != first["pid"]
    finally:
        effect_runtime.restart_effect_runtime()


def test_compile_preload_is_part_of_the_source_fingerprint(tmp_path: Path, monkeypatch) -> None:
    preload = tmp_path / "effect_runtime_compile_cache.ts"
    preload.write_text("export const version = 1;\n", encoding="utf-8")
    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: tmp_path)
    original = effect_runtime._runtime_fingerprint()
    preload.write_text("export const version = 2;\n", encoding="utf-8")
    assert effect_runtime._runtime_fingerprint() != original


@pytest.mark.parametrize("retirement_delay", ["none", "claim_open", "claim_cleanup"])
def test_shutdown_flushes_compilation_before_retiring_the_locator(
    tmp_path: Path, monkeypatch, retirement_delay: str,
) -> None:
    """A retired locator lets its fixture clean up; exit must not write later."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime)
    for name in ("NODE_COMPILE_CACHE", "NODE_DISABLE_COMPILE_CACHE", "NODE_V8_COVERAGE"):
        monkeypatch.delenv(name, raising=False)
    delay = tmp_path / "hold-exit.mjs"
    release = tmp_path / "release-exit"
    delay.write_text(
        "import { existsSync } from 'node:fs';\n"
        "const exit = process.exit.bind(process);\n"
        "const release = new URL('./release-exit', import.meta.url);\n"
        "process.exit = code => {\n"
        "  const poll = setInterval(() => {\n"
        "    if (existsSync(release)) { clearInterval(poll); exit(code); }\n"
        "  }, 10);\n"
        "  setTimeout(() => exit(code), 5000).unref();\n"
        "};\n",
        encoding="utf-8",
    )
    if retirement_delay != "none":
        # Expose both creation and deletion after locator retirement.
        operation = "open" if retirement_delay == "claim_open" else "readFile"
        with delay.open("a", encoding="utf-8") as preload:
            preload.write(
                "import fsp from 'node:fs/promises';\n"
                "import { syncBuiltinESMExports } from 'node:module';\n"
                f"const savedRm = fsp.rm, savedOperation = fsp.{operation};\n"
                "let retired = false;\n"
                "fsp.rm = async (path, ...args) => {\n"
                "  const result = await savedRm(path, ...args);\n"
                "  if (String(path).endsWith('.json')) retired = true;\n"
                "  return result;\n"
                "};\n"
                f"fsp.{operation} = async (path, ...args) => {{\n"
                "  if (retired && String(path).includes('.ts-effect.lock.claim.'))\n"
                "    await new Promise(resolve => setTimeout(resolve, 200));\n"
                "  return savedOperation(path, ...args);\n"
                "};\n"
                "syncBuiltinESMExports();\n"
            )
    monkeypatch.setenv("NODE_OPTIONS", "--import=" + delay.as_uri())
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "60000")
    children = []
    original_popen = effect_runtime.subprocess.Popen

    def capture(args, *rest, **kwargs):
        child = original_popen(args, *rest, **kwargs)
        if "--info" in args:
            children.append(child)
        return child

    monkeypatch.setattr(effect_runtime.subprocess, "Popen", capture)
    try:
        effect_runtime.effect_runtime_result("runtime.ping", {})
        assert len(children) == 1
        assert effect_runtime.restart_effect_runtime()["status"] == "stopped"
        assert children[0].poll() is None, "the fixture must expose the pre-exit interval"
        assert not list(runtime.glob("*.ts-effect.lock*")), (
            "stop must wait until locator retirement and claim cleanup complete"
        )
        assert any(p.is_file() for p in (runtime / "compile-cache").rglob("*")), (
            "cache must be flushed before the locator authorizes directory cleanup"
        )
        # Only this newly created fixture namespace is removed, while the
        # owned Node child is still held before exit. No late cache may revive it.
        shutil.rmtree(runtime)
        release.touch()
        children[0].wait(timeout=5)
        assert not runtime.exists(), "exit-time cache writes must not recreate the namespace"
    finally:
        effect_runtime.restart_effect_runtime()
        release.touch()
        for child in children:
            child.wait(timeout=5)
