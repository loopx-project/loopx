#!/usr/bin/env python3
"""Execute the offline recipe on disposable state, using real CLI and producer I/O.

--installed requires a non-editable Python package. The producer is the
checkout's existing TypeScript adapter; no model or native worker is launched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = "capabilities/reliability_diagnostics/docs/local-retention-v0.md"
AS_OF = "2026-09-01T12:00:00+00:00"
PRODUCER = """
import { pathToFileURL } from 'node:url';
const {resolveShadowObserverConfig, ShadowObserver} = await import(pathToFileURL(process.argv[1]));
const config = resolveShadowObserverConfig({
  LOOPX_DSH_SHADOW_OBSERVER_GOAL_ID: process.argv[2],
  LOOPX_DSH_SHADOW_OBSERVER_SESSION_ID: 'session-producer-fixture',
  LOOPX_DSH_SHADOW_OBSERVER_LEDGER_DIR: process.argv[3],
  LOOPX_DSH_SHADOW_OBSERVER_RUN_IDENTITY_JSON: JSON.stringify(Object.fromEntries(
    ['worker_id', 'model_id', 'task_id', 'environment_id', 'tools_id', 'budget_id',
     'adapter_revision', 'observer_revision'].map(key => [key, 'fixture-' + key]))),
});
if (!config) throw new Error('fixture configuration rejected');
const observer = new ShadowObserver({config, observerId: 'producer-fixture', now: () => 1788256800000});
const session = {id: config.sessionId};
observer.observeSessionCreated(session);
observer.observeSessionEvent(session, {type: 'turn/start', seq: 0, time: 1788256800000, data: {turn: 1}});
await observer.dispose();
process.stdout.write(JSON.stringify({path: observer.path, stats: observer.stats()}));
"""


def executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    if not args.installed:
        sys.path.insert(0, str(REPO_ROOT))
    import loopx
    from loopx.capabilities.reliability_diagnostics import (
        FIXTURE_GOAL_ID, append_ledger_records, ledger_ref, run_dsh_fixture,
    )

    package_root = Path(loopx.__file__).resolve().parent
    if args.installed:
        assert not package_root.is_relative_to(REPO_ROOT), "installed mode refuses checkout imports"
    blocks = re.findall(r"```sh\n(set -eu\n.*?)\n```", (package_root / GUIDE).read_text(), re.S)
    assert len(blocks) == 1, "guide must expose one executable retention recipe"
    recipe = blocks[0]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["LOOPX_USAGE_PING"] = "0"
    if not args.installed:
        env["PYTHONPATH"] = str(REPO_ROOT)
    ref = ledger_ref(FIXTURE_GOAL_ID)
    fixture = run_dsh_fixture()

    def cli(runtime: Path, *options: str, goal: str = FIXTURE_GOAL_ID) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--runtime-root", str(runtime),
             "--format", "json", "reliability-diagnostics", *options,
             "--goal-id", goal],
            cwd=runtime.parent, env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def readback(runtime: Path, goal: str = FIXTURE_GOAL_ID) -> dict:
        return cli(runtime, "status", "--with-receipt", "--as-of", AS_OF, goal=goal)

    def shell_env(base: Path, runtime: Path, provider: Path, goal: str = FIXTURE_GOAL_ID) -> dict:
        bindir = base / "bin"
        bindir.mkdir()
        (base / "tmp").mkdir()
        (bindir / "python3").symlink_to(sys.executable)
        executable(bindir / "loopx", "#!/bin/sh\nexec " + shlex.quote(sys.executable)
                   + ' -m loopx.cli "$@"\n')
        return {**env, "PATH": str(bindir) + os.pathsep + env["PATH"],
                "TMPDIR": str(base / "tmp"), "diagnostic_runtime": str(runtime),
                "diagnostic_goal": goal, "diagnostic_as_of": AS_OF,
                "diagnostic_provider_ledger_dir": str(provider)}

    with tempfile.TemporaryDirectory(prefix="loopx-retention-smoke-") as tmp:
        root = Path(tmp)
        cases = ("degraded", "invalid", "symlink", "namespace-symlink", "foreign", "mixed", "malformed",
                 "collision", "copy-tamper", "source-tamper", "restore-tamper", "occupied")
        for case in cases:
            base = root / case
            runtime = base / "runtime"
            goal = "goal:alias" if case == "collision" else FIXTURE_GOAL_ID
            case_ref = ("reliability_diagnostics/goal_alias.ndjson"
                        if case == "collision" else ledger_ref(goal))
            ledger = runtime / case_ref
            ledger.parent.mkdir(parents=True)
            seed = json.loads(json.dumps(fixture["ledger_records"]))
            for row in seed:
                row["goal_id"] = goal
            append_ledger_records(ledger, seed)
            if case == "invalid":
                refused = base / "refused.ndjson"
                refused.write_text(json.dumps(dict(seed[0], command={"kind": "stop"})) + "\n")
                rejection = cli(runtime, "ingest", "--input", str(refused), goal=goal)
                assert rejection["ingest_gate_recorded"]
                assert rejection["rejected_by_reason"] == {"control_field_rejected": 1}
            elif case in {"foreign", "mixed", "collision"}:
                rows = json.loads(json.dumps(fixture["ledger_records"]))
                foreign_id = "foreign-goal" if case != "collision" else "goal_alias"
                if case == "collision":
                    assert ledger_ref(foreign_id) != ledger_ref(goal)
                    assert foreign_id == goal.replace(":", "_")
                for row in rows:
                    row["goal_id"] = foreign_id
                if case == "foreign":
                    ledger.write_text("", encoding="utf-8")
                append_ledger_records(ledger, rows)
            elif case == "malformed":
                with ledger.open("a") as handle:
                    handle.write("not-json\n")
            content = ledger.read_bytes()
            before = readback(runtime, goal)
            backing = base / "backing.ndjson"
            if case == "symlink":
                ledger.rename(backing)
                ledger.symlink_to(backing)
            elif case == "namespace-symlink":
                backing = base / "outside-ledgers"
                ledger.parent.rename(backing)
                ledger.parent.symlink_to(backing, target_is_directory=True)
            sentinel = runtime / "authority-sibling.json"
            sentinel.write_bytes(b'{"synthetic":"unchanged"}\n')
            case_env = shell_env(base, runtime, runtime / "reliability_diagnostics", goal)
            bindir = base / "bin"
            if case == "copy-tamper":
                executable(bindir / "cp", f"#!{sys.executable}\nimport pathlib,subprocess,sys\n"
                           f"subprocess.run([{shutil.which('cp')!r}, *sys.argv[1:]],check=True)\n"
                           "p=pathlib.Path(sys.argv[-1]); p.write_bytes(p.read_bytes()+b'{}\\n')\n")
            if case in {"source-tamper", "restore-tamper", "occupied"}:
                executable(bindir / "shasum", f"#!{sys.executable}\nimport pathlib,subprocess,sys\n"
                           f"counter=pathlib.Path({str(base / 'hash-count')!r})\n"
                           "n=int(counter.read_text())+1 if counter.exists() else 1; counter.write_text(str(n))\n"
                           f"ledger=pathlib.Path({str(ledger)!r}); mode={case!r}\n"
                           "if n==2 and mode=='source-tamper': ledger.write_bytes(ledger.read_bytes()+b'{}\\n')\n"
                           "if n==3 and mode=='occupied': ledger.write_bytes(b'occupied-destination\\n')\n"
                           "if n==3 and mode=='restore-tamper':\n"
                           f" p=next(pathlib.Path({str(base / 'tmp')!r}).glob('*/readback-runtime/{case_ref}'))\n"
                           " p.write_bytes(p.read_bytes()+b'{}\\n')\n"
                           f"raise SystemExit(subprocess.run([{shutil.which('shasum')!r}, *sys.argv[1:]]).returncode)\n")
            result = subprocess.run(["sh", "-c", recipe], cwd=base, env=case_env,
                                    capture_output=True, text=True, timeout=60)
            archives = list((base / "tmp").iterdir())
            exports = [p / "readback-runtime" / case_ref for p in archives]
            if case in {"degraded", "invalid"}:
                assert result.returncode == 0, (case, result.stderr)
                assert ledger.read_bytes() == content and readback(runtime, goal) == before
                assert before["receipt"]["status"] == case
                assert before["receipt"]["lost_event_count"] == 2
                assert before["receipt"]["backpressure_drop_count"] == 3
                assert before["receipt"]["clock"]["max_uncertainty_ms"] == 1500
                assert len(archives) == 1 and exports[0].read_bytes() == content
                after_delete = json.loads((archives[0] / "after-delete.json").read_text())["receipt"]
                assert after_delete["status"] == "invalid"
                assert "no_observations" in after_delete["reason_codes"]
                assert after_delete["persisted_event_count"] == 0
                assert before["projection"]["authority"] == "none"
                assert before["receipt"]["outbound_endpoints"] == []
            else:
                assert result.returncode != 0, (case, "recipe incorrectly accepted boundary", result.stdout)
                if case == "symlink":
                    assert ledger.is_symlink() and backing.read_bytes() == content
                    assert not any(p.exists() for p in exports)
                elif case == "namespace-symlink":
                    assert "symlink ledger namespace" in result.stderr
                    assert (backing / ledger.name).read_bytes() == content
                    assert ledger.parent.is_symlink() and not archives
                elif case in {"foreign", "mixed", "malformed", "collision"}:
                    assert ledger.read_bytes() == content and not any(p.exists() for p in exports)
                elif case == "source-tamper":
                    assert ledger.read_bytes() == content + b"{}\n"
                elif case == "copy-tamper":
                    assert ledger.read_bytes() == content
                elif case == "restore-tamper":
                    assert not ledger.exists(), "corrupt export must not be restored"
                elif case == "occupied":
                    assert ledger.read_bytes() == b"occupied-destination\n"
            assert sentinel.read_bytes() == b'{"synthetic":"unchanged"}\n'

        assert args.node, "native producer path check requires Node.js"
        for layout in ("canonical", "custom", "directory-symlink"):
            base = root / ("producer-" + layout)
            runtime = base / "runtime"
            provider_dir = runtime / "reliability_diagnostics" if layout == "canonical" else base / "custom-ledgers"
            if layout == "directory-symlink":
                canonical = runtime / "reliability_diagnostics"
                canonical.mkdir(parents=True)
                provider_dir = base / "linked-ledgers"
                provider_dir.symlink_to(canonical, target_is_directory=True)
            produced = subprocess.run(
                [args.node, "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", PRODUCER,
                 str(REPO_ROOT / "packages/dsh-loopx-plugin/src/observer.ts"), FIXTURE_GOAL_ID, str(provider_dir)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
            )
            assert produced.returncode == 0, produced.stderr
            producer = json.loads(produced.stdout)
            path = Path(producer["path"])
            assert path.is_file() and producer["stats"]["accepted_event_count"] == 2
            actual_runtime = provider_dir.parent if layout == "custom" else runtime
            receipt = readback(actual_runtime)["receipt"]
            if layout != "custom":
                assert path.resolve() == (actual_runtime / ref).resolve()
                assert receipt["status"] == "valid" and receipt["persisted_event_count"] == 2
            else:
                assert path != actual_runtime / ref
                assert receipt["status"] == "invalid" and receipt["persisted_event_count"] == 0
            content = path.read_bytes()
            case_env = shell_env(base, actual_runtime, provider_dir)
            result = subprocess.run(["sh", "-c", recipe], cwd=base, env=case_env,
                                    capture_output=True, text=True, timeout=60)
            assert path.read_bytes() == content
            if layout == "canonical":
                assert result.returncode == 0, result.stderr
                assert readback(actual_runtime)["receipt"] == receipt
            else:
                assert result.returncode != 0
                message = "unsupported provider ledger directory" if layout == "custom" else "symlink ledger directory"
                assert message in result.stderr
                assert not list((base / "tmp").iterdir()), "directory mismatch must stop before export"
    print(f"ledger-retention-smoke: literal shell and producer/CLI boundaries passed (installed={args.installed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
