"""Export and verify exact immutable source arms before matched measurement."""
import hashlib
import json
import os
import platform
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
prepare_started_monotonic_ns = time.monotonic_ns()
for target in (ROOT, Path.home()):
    available = shutil.disk_usage(target).free
    if available < 5 * 1024**3:
        raise SystemExit(f"Need4GiB estimated peak plus1GiB reserve at {target}; available={available}")
repository = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
baseline = os.environ["BASE_SHA"]
candidate = os.environ["HEAD_SHA"]
if (baseline != "04c5799b88f6c4cf12a224b7fa679f272e1e794e"
        or candidate != "e42c536e609ba383217a3e83b8cf07c72542944c"):
    raise SystemExit("Qualification source pins differ from the frozen protocol")
replica = int(os.environ["CALIBRATION_REPLICA"])
if replica not in range(4):
    raise SystemExit("Exactly four declared replicas percase")
provider = os.environ["CASE_PROVIDER"]
history = os.environ["CASE_HISTORY"]
mode = os.environ["CASE_MODE"]
node_path = shutil.which("node")
if node_path is None:
    raise SystemExit("Node executable was not found on the QA checkout PATH")
node_bin = Path(node_path).resolve().parent
if provider not in ("file", "sqlite") or mode not in ("cold-daemon", "warm"):
    raise SystemExit("unsupported provider or mode")
history = int(history)
if history not in (0, 500):
    raise SystemExit("unsupported history count")
manifest = json.loads((ROOT / "qualification-manifest.json").read_text())
if manifest.get("status") != "ready_for_candidate_measurement":
    raise SystemExit("Qualification manifest is not execution-ready")
slots = [slot for slot in manifest["slots"]
         if (slot["provider"], slot["history"], slot["mode"], slot["replica"])
         == (provider, history, mode, replica)]
if len(slots) != 1:
    raise SystemExit("Missing or duplicate declared case/session")
source_slot_by_arm = slots[0]["source_slot_by_arm"]
if source_slot_by_arm not in ({"base": "base", "head": "head"},
                             {"base": "head", "head": "base"}):
    raise SystemExit("Invalid physical source assignment")
baseline_path = ROOT / "sources" / source_slot_by_arm["base"]
candidate_path = ROOT / "sources" / source_slot_by_arm["head"]
user = pwd.getpwuid(os.getuid())
environment_keys = (
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "LD_LIBRARY_PATH",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "CODEX_HOME", "CODEX_CONFIG_HOME",
    "CI", "GITHUB_ACTIONS", "GITHUB_WORKSPACE", "RUNNER_TEMP", "RUNNER_TOOL_CACHE",
    "PYTHONUNBUFFERED", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "LOOPX_USAGE_PING",
)
benchmark_identity = {
    "uid": os.getuid(),
    "gid": os.getgid(),
    "extra_groups": sorted(os.getgroups()),
    "environment": {key: os.environ.get(key) for key in environment_keys if os.environ.get(key) is not None},
}
qualification_job_started = float(os.environ["C2_QUAL_JOB_STARTED_EPOCH"])
benchmark_identity["environment"].setdefault("HOME", user.pw_dir)
benchmark_identity["environment"].setdefault("USER", user.pw_name)
benchmark_identity["environment"].setdefault("LOGNAME", user.pw_name)
benchmark_identity["environment"].setdefault("PATH", os.defpath)


def run(argv, **kwargs):
    subprocess.run(argv, check=True, **kwargs)


for arm, revision in [("base", baseline), ("head", candidate)]:
    source = ROOT / "sources" / source_slot_by_arm[arm]
    source.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "-C", str(repository), "worktree", "add", "--detach", str(source), revision])
    entries = subprocess.check_output(["git", "-C", str(repository), "ls-tree", "-rz", revision, "--", "loopx"])
    product_manifest = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        file_mode, kind, blob = metadata.split()
        assert kind == b"blob" and file_mode in (b"100644", b"100755"), entry
        name = path.decode()
        data = (source / name).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        assert actual == blob.decode(), (arm, name)
        product_manifest[name] = hashlib.sha256(data).hexdigest()
    (ROOT / f"{arm}-product-manifest.json").write_text(json.dumps(product_manifest))
    python = source / ".venv/bin/python"
    run(["uv", "venv", "--python", sys.executable, str(source / ".venv")])
    run(["uv", "pip", "install", "--python", str(python), "-r", str(ROOT / "requirements.txt")])
    run(["uv", "pip", "install", "--python", str(python), "--no-deps", "-e", str(source)])
    run(["npm", "ci", "--ignore-scripts"], cwd=source)
    run(["npm", "ci", "--prefix", "apps/presentation/dashboard"], cwd=source)
    run(["npm", "run", "--prefix", "apps/presentation/dashboard", "build:chat"], cwd=source)

config = {
    "experiment_kind": "fixed_session_candidate_qualification",
    "provider": provider,
    "history": history,
    "mode": mode,
    "replica": replica,
    "baseline_source_sha": baseline,
    "candidate_source_sha": candidate,
    "source_slot_by_arm": source_slot_by_arm,
    "latency_qualified": False,
    "series": "c2-native-context-transaction-v1",
    "control_design": "Fixed sessions; matched AA/AB calls; randomized balanced physical source positions",
    "baseline": baseline,
    "pr_target_main": baseline,
    "candidate_head": candidate,
    "baseline_source": str(baseline_path),
    "candidate_source": str(candidate_path),
    "node_bin": str(node_bin),
    "cache_python_base_lib": str(Path(sys.base_prefix) / "lib"),
    "cache_max_bytes": 2 * 1024**3,
    "cache_max_seconds": 60,
    "benchmark_identity": benchmark_identity,
    "qualification_job_started_epoch": qualification_job_started,
    "qualification_job_cap_seconds": 5400,
    "providers": [provider],
    "history_counts": [history],
    "modes": [mode],
    "aa_pairs": 160,
    "ab_pairs": 320,
    "warmups_per_arm_per_phase": 10,
    "cpu_affinity": [max(os.sched_getaffinity(0))],
    "performance_decision": {
        "bootstrap_seed": 20261030,
        "bootstrap_trials": 5000,
        "max_resolution_ms": 25,
        "max_resolution_fraction": 0.05,
        "control_requirement": "AA p50 and p95 repeatability within both original limits.",
        "candidate_requirement": "AB p50/p95 material-regression limits unchanged; paired-bootstrap upper95 must remain within measured qualified AA resolution. Preserve negative/unqualified rows; no selective sample pooling.",
        "interpretation": "Fixed-session engineering decision-score quantiles; not calibrated cross-VM confidence coverage",
    },
    "decision_scores": manifest["decision_scores"],
    "auxiliary_probes": manifest["auxiliary_probes"],
    "scope": "Complete native quota CLI process; cold means cold daemon/full context with warm disk cache. Warm uses one verified saved baseline. Fixed synthetic wall clocks and capture allocation; perf_counter remains real. Eight acceptance-source refinements exercise required-view assembly. No model requests or live billing measurement.",
}
(ROOT / "evaluation_config.json").write_text(json.dumps(config, indent=2) + "\n")
cpuinfo = Path("/proc/cpuinfo").read_text()
cpu_model = next((line.split(":", 1)[1].strip() for line in cpuinfo.splitlines()
                  if line.startswith("model name")), None)
boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
environment = {"purpose": "Calibrationenvironmentmetadata;noversionperformanceclaim",
               "os": platform.system(), "machine": platform.machine(),
               "kernel": platform.release(), "cpu_model": cpu_model,
               "boot_id_sha256": hashlib.sha256(boot_id.encode()).hexdigest(),
               "python": platform.python_version(),
               "node": subprocess.check_output([str(node_bin / "node"), "--version"], text=True).strip(),
               "allowed_cpus": sorted(os.sched_getaffinity(0)),
               "uid": os.getuid(), "gid": os.getgid(),
               "replica": replica, "case": [provider, history, mode],
               "job_started_epoch": qualification_job_started,
               "recorded_utc": datetime.now(timezone.utc).isoformat(),
               "prepare_started_monotonic_ns": prepare_started_monotonic_ns,
               "recorded_monotonic_ns": time.monotonic_ns(),
               "disk_free_bytes": {str(path): shutil.disk_usage(path).free
                                   for path in (ROOT, Path.home())},
               "cgroup": {str(path): path.read_text().strip()
                          for path in (Path("/proc/self/cgroup"),
                                       Path("/sys/fs/cgroup/cpu.max"),
                                       Path("/sys/fs/cgroup/memory.max"),
                                       Path("/sys/fs/cgroup/cpuset.cpus.effective"))
                          if path.is_file()},
               "mem_available_kib": next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                                          if line.startswith("MemAvailable:"))}
if environment["python"] != "3.13.12" or environment["node"] != "v24.21.0" or environment["machine"] != "x86_64":
    raise SystemExit("Unexpected frozen runtime/architecture")
(ROOT / "environment-record.json").write_text(json.dumps(environment, indent=2) + "\n")
print(json.dumps({"baseline": baseline, "candidate": candidate, "provider": provider,
                  "history": history, "mode": mode, "source_blobs_verified": True}), flush=True)
