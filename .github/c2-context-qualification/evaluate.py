from __future__ import annotations
import os
import sys
import json
import subprocess
import time
import pathlib
import hashlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "evaluation_config.json").read_text())
if "cpu_affinity" in CONFIG:
    os.sched_setaffinity(0, set(CONFIG["cpu_affinity"]))
if "process_nice" in CONFIG:
    os.setpriority(os.PRIO_PROCESS, 0, CONFIG["process_nice"])
SOURCES = {
    "base": pathlib.Path(CONFIG["baseline_source"]),
    "head": pathlib.Path(CONFIG["candidate_source"]),
}
TMP = ROOT / "evaluation-tmp"
TMP.mkdir(exist_ok=True)
for arm, key in [("base", "baseline"), ("head", "candidate_head")]:
    src = SOURCES[arm]
    if (src / ".git").exists():
        observed = subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
        assert observed == CONFIG[key], (arm, observed, CONFIG[key])
        subprocess.run(["git", "-C", str(src), "diff", "--quiet", "HEAD", "--", "loopx"], check=True)
    else:
        manifest = json.loads((ROOT / f"{arm}-product-manifest.json").read_text())
        assert all(hashlib.sha256((src / name).read_bytes()).hexdigest() == digest
                   for name, digest in manifest.items()), "immutable baseline source drift"


def environment(arm, namespace=None):
    src = SOURCES[arm]
    runtime_tmp = TMP / (namespace or arm)
    runtime_tmp.mkdir(exist_ok=True)
    return {
        **os.environ,
        "PATH": str(CONFIG["node_bin"])
        + ":"
        + str(ROOT / "bin")
        + ":"
        + str(src / ".venv/bin")
        + ":"
        + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "clock-hooks")
        + ":"
        + str(src / "tests/control_plane")
        + ":"
        + str(src),
        "PYTHONHASHSEED": "0",
        "TMPDIR": str(runtime_tmp),
        "TEMP": str(runtime_tmp),
        "TMP": str(runtime_tmp),
        "NODE_OPTIONS": "--experimental-sqlite --import "
        + (ROOT / "clock-hooks/clock.mjs").as_uri(),
        "LOOPX_USAGE_PING": "0",
        "CODEX_THREAD_ID": "c2-eval-native-session",
        "C2_CAPTURE_LABEL": "measured",
        "LOOPX_EFFECT_RUNTIME_IDLE_MS": "600000",
        "LOOPX_EVALUATION_PYTHON": str(src / ".venv/bin/python"),
    }


def prepare():
    for provider in CONFIG["providers"]:
        for count in CONFIG["history_counts"]:
            case = ROOT / "evaluation-fixtures" / f"{provider}-{count}"
            if (case / "manifest.json").exists():
                continue
            p = subprocess.run(
                [
                    str(SOURCES["base"] / ".venv/bin/python"),
                    str(ROOT / "prepare_evaluation.py"),
                    str(case),
                    provider,
                    str(count),
                ],
                capture_output=True,
                text=True,
                env=environment("base"),
                cwd=SOURCES["base"],
                timeout=90,
            )
            assert p.returncode == 0, p.stderr
            (case / "manifest.json").write_text(p.stdout)
            print("PREPARED", provider, count, flush=True)


def command(manifest, arm):
    return [
        str(SOURCES[arm] / ".venv/bin/python"),
        "-m",
        "loopx.entrypoint",
        "--cli-bin",
        "loopx-eval",
        "--registry",
        manifest["registry"],
        "--runtime-root",
        manifest["runtime"],
        "--format",
        "json",
        "quota",
        "should-run",
        "--goal-id",
        "goal-a",
        "--agent-id",
        "agent-a",
        "--turn-instance-id", "c2-performance-original-turn",
        "--codex-app", "--context-delivery", "on",
        "--codex-context-dir", manifest["native"],
        "--decision-output-root", manifest["captures"],
    ]


def stop_runtime(arm, namespace=None):
    stopped = subprocess.run(
        [
            str(SOURCES[arm] / ".venv/bin/python"),
            "-c",
            "import json;from loopx.control_plane.effect_runtime import restart_effect_runtime; print(json.dumps(restart_effect_runtime(timeout=5,wait_for_exit=True)))",
        ],
        capture_output=True,
        text=True,
        env=environment(arm, namespace),
        timeout=20,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(stopped.stdout)["status"] in ("stopped", "not_running"), (
        stopped.stdout
    )


def run(manifest, arm, mode, namespace=None):
    if mode == "cold-daemon":
        stop_runtime(arm, namespace)
    import shutil
    captures = pathlib.Path(manifest["captures"])
    for generated in captures.glob("decision-measured-*"):
        assert generated.parent == captures and generated.is_dir()
        shutil.rmtree(generated)
    seed = "cold-state.json" if mode == "cold-daemon" else "warm-state.json"
    (pathlib.Path(manifest["native"]) / "session.json").write_bytes(
        (pathlib.Path(manifest["project"]) / seed).read_bytes())
    env = environment(arm, namespace)
    started = time.perf_counter_ns()
    p = subprocess.run(
        command(manifest, arm),
        cwd=manifest["project"],
        env=env,
        capture_output=True,
        timeout=120,
    )
    elapsed = (time.perf_counter_ns() - started) / 1e6
    assert p.returncode == 0, (p.stdout[-1500:], p.stderr[-1500:])
    payload = json.loads(p.stdout)
    assert payload["ok"] and payload["mode"] in {"full", "delta"}, payload
    captured = pathlib.Path(payload["recovery"]["path"]).parent / "decision.json"
    original = json.loads(captured.read_bytes())
    assert original["ok"] and original["should_run"], original
    assert original["agent_todo_summary"]["first_executable_items"][0]["todo_id"] == "todo_tail_owned"
    assert original["agent_todo_summary"]["done_count"] == manifest["history"]
    assert payload["mode"] == ("full" if mode == "cold-daemon" else "delta"), payload["mode"]
    artifacts = ROOT / "observed-blobs"
    artifacts.mkdir(exist_ok=True)
    for raw in (p.stdout, captured.read_bytes()):
        blob = artifacts / (hashlib.sha256(raw).hexdigest() + ".json")
        if not blob.exists(): blob.write_bytes(raw)
    runtime_infos = [
        json.loads(path.read_text())
        for path in (TMP / (namespace or arm)).glob("*/runtime-*.json")
    ]
    runtime_pids = {
        info["pid"] for info in runtime_infos if isinstance(info.get("pid"), int)
    }
    assert len(runtime_pids) == 1, runtime_pids
    return {
        "arm": arm,
        "runtime_namespace": namespace or arm,
        "runtime_pid": runtime_pids.pop(),
        "ms": elapsed,
        "stdout_sha256": hashlib.sha256(p.stdout).hexdigest(),
    }, p.stdout


def preflight():
    for path in sorted((ROOT / "evaluation-fixtures").glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        for mode in CONFIG["modes"]:
            case = path.parent.name + "-" + mode
            rows, outputs = [], []
            for index, arm in enumerate(["base", "base", "head"]):
                row, out = run(manifest, arm, mode)
                rows.append(row)
                outputs.append(out)
                (ROOT / f"preflight-{case}-{index}.json").write_bytes(out)
            assert outputs[0] == outputs[1] == outputs[2], (case, rows)
            print("PREFLIGHT", case, "AA_EQUAL", True, "AB_EQUAL", True, flush=True)


def percentile(xs, q):
    xs = sorted(xs)
    position = (len(xs) - 1) * q
    low = int(position)
    return xs[low] + (xs[min(low + 1, len(xs) - 1)] - xs[low]) * (position - low)


def benchmark():
    ledger = ROOT / "performance-rows.jsonl"
    assert not ledger.exists(), (
        "preserve previous run; do not overwrite or select samples"
    )
    for path in sorted((ROOT / "evaluation-fixtures").glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        case = path.parent.name
        for mode in CONFIG["modes"]:
            for arm in ["base", "head"]:
                stop_runtime(arm)
            for phase, n in [("aa", CONFIG["aa_pairs"]), ("ab", CONFIG["ab_pairs"])]:
                targets = {
                    label: ("base" if phase == "aa" else label, f"{phase}-{label}")
                    for label in ["base", "head"]
                }
                for arm, namespace in targets.values():
                    stop_runtime(arm, namespace)
                for warmup in range(CONFIG.get("warmups_per_arm_per_phase", 3)):
                    for arm, namespace in targets.values():
                        run(manifest, arm, "warm", namespace)
                collected = {"base": [], "head": []}
                for pair in range(n):
                    order = ["base", "head"] if pair % 2 == 0 else ["head", "base"]
                    outputs = []
                    runtime_pids = set()
                    for label in order:
                        arm, namespace = targets[label]
                        row, out = run(manifest, arm, mode, namespace)
                        runtime_pids.add(row["runtime_pid"])
                        outputs.append(out)
                        row.update(
                            case=case, mode=mode, phase=phase, pair=pair, label=label
                        )
                        with ledger.open("a") as file:
                            file.write(json.dumps(row) + "\n")
                        collected[label].append(row["ms"])
                    assert len(runtime_pids) == 2, (
                        "controls and treatment require separate daemons"
                    )
                    assert outputs[0] == outputs[1], (
                        f"raw output changed at {case}/{mode}/{phase}/{pair}"
                    )
                    if (pair + 1) % 20 == 0:
                        print("PROGRESS", case, mode, phase, pair + 1, n, flush=True)
                stats = {
                    arm: {
                        "p50": statistics.median(xs),
                        "p95": percentile(xs, 0.95),
                        "n": len(xs),
                    }
                    for arm, xs in collected.items()
                }
                print("PERFORMANCE", case, mode, phase, json.dumps(stats), flush=True)
                for arm, namespace in targets.values():
                    stop_runtime(arm, namespace)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "prepare":
        prepare()
    elif mode == "preflight":
        preflight()
    elif mode == "benchmark":
        benchmark()
    else:
        raise SystemExit("unknown evaluation phase")
