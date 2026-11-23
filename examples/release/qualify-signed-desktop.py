"""Exercise signed update, native relaunch and manual backup restore in the isolated macOS runner."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from uuid import uuid4

assert os.environ.get("GITHUB_ACTIONS") == "true" and platform.system() == "Darwin"
assert not Path("/Applications/LoopX.app").exists()
root = Path(tempfile.mkdtemp(prefix="loopx-native-release-", dir=os.environ["RUNNER_TEMP"]))
old_archive, candidate_archive = (Path(x).resolve() for x in sys.argv[1:3])
target_rev, target_version, receipt_file = sys.argv[3:]
support = Path.home() / "Library/Application Support/io.loopx.control-plane"
assert not support.exists()
bundle = root / "LoopX.app"

owned_pids = set()
endpoint = "http://127.0.0.1:8767"


def own_listeners_stopped():
    for port in (8766, 8767):
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return False
    return True

def command(*args):
    subprocess.run([str(x) for x in args], check=True, timeout=55)

def identity(app):
    contents = app / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "io.loopx.control-plane"
    runtime = json.loads((contents / "Resources/runtime/identity.json").read_bytes())
    return info["CFBundleShortVersionString"], runtime["source_revision"], runtime.get("default_contract")

def worker_running(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5)
    return str(bundle / "Contents/MacOS") in result.stdout

def request(path, data=None):
    kwargs = {} if data is None else {"data": json.dumps(data).encode(), "headers": {"Content-Type": "application/json"}}
    with urllib.request.urlopen(urllib.request.Request(endpoint+path, **kwargs), timeout=8) as response:
        return json.load(response)

def wait_for(callback, seconds=240):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = callback()
        if result:
            return result
        time.sleep(1)
    raise TimeoutError("Signed Desktop lifecycle did not reach its expected readback")

def start(version, request_id):
    command("/usr/bin/open", "-n", "-a", bundle, "--args", f"--loopx-upgrade-request={request_id}",
        "--loopx-upgrade-channel=stable", f"--loopx-upgrade-target={version}")

def ready(request_id, version, revision, allowed_runtime=None):
    receipt = support / "upgrades" / f"{request_id}.json"
    def read():
        if not receipt.exists():
            return None
        value = json.loads(receipt.read_bytes())
        if worker_running(value["pid"]):
            owned_pids.add(value["pid"])
        if value.get("phase") in ("error","service_error","runtime_required","runtime_pairing_required"):
            raise RuntimeError(f"Native update readback: {value['phase']} {value.get('details')}")
        if value.get("phase") != "ready":
            return None
        assert value["app_version"] == version and value["bundled_source_revision"] == revision
        assert value["runtime_identity"]["source_revision"] in (allowed_runtime or (revision,))
        assert value["update_checked"] and worker_running(value["pid"])
        return value
    return wait_for(read)

def stop(value):
    pid = value["pid"]
    assert pid in owned_pids and worker_running(pid)
    process_rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True, timeout=5)
    children = {}
    for row in process_rows.splitlines():
        child_pid, parent_pid, invocation = row.strip().split(maxsplit=2)
        if int(parent_pid) == pid and str(support / "runtime") in invocation:
            children[int(child_pid)] = invocation
    os.kill(pid, signal.SIGTERM)
    wait_for(lambda: not worker_running(pid), 25)
    for child, invocation in children.items():
        probe = subprocess.run(["ps", "-p", str(child), "-o", "command="], capture_output=True, text=True, timeout=5)
        if probe.stdout.strip() == invocation:
            os.kill(child, signal.SIGTERM)
    wait_for(own_listeners_stopped, 25)

def unpack(archive, target):
    target.mkdir(parents=True, exist_ok=True)
    command("/usr/bin/tar", "-xzf", archive, "-C", target)
    command("/usr/bin/codesign", "--verify", "--deep", "--strict", target / "LoopX.app")

try:
    unpack(old_archive, root)
    old_version, old_revision, _ = identity(bundle)
    assert old_version == "2.4.0" and old_revision == "a6ffb3be6e7bdb48e02311ec58d618200fe0501c"
    unpack(candidate_archive, root / "candidate")
    assert identity(root / "candidate/LoopX.app")[:2] == (target_version, target_rev)

    # On the disposable runner the normal LaunchServices relaunch inherits
    # only this job's test user, without a development App identifier.
    command("/bin/launchctl", "setenv", "PATH", os.environ["PATH"])
    command("/bin/launchctl", "setenv", "LOOPX_USAGE_PING", "0")
    first_id = uuid4().hex
    start(old_version, first_id)
    original = ready(first_id, old_version, old_revision)
    print("Verified original v2.4.0 App and its services", flush=True)
    question = "Read this existing conversation after the upgrade."
    saved = request("/api/chat/projection-messages", {"context_kind":"manager", "question": question, "answer":"Offline status result."})
    session_url = f"/api/chat/sessions/{saved['session_id']}"
    assert saved["ok"]

    def verify_session():
        value = request(session_url)
        assert value["session"]["adapter_kind"] == "status_projection"
        assert any(row["message_id"] == saved["user_message_id"] and row["text"] == question for row in value["messages"])
        assert any(row["message_id"] == saved["answer_message_id"] for row in value["messages"])
    verify_session()
    stop(original)

    # A not-yet-published feed cannot be changed for QA. Provide the immutable
    # official archive/signature in the native per-request cache. The App owns
    # signature verification, selection, atomic replacement and relaunch.
    rid = uuid4().hex
    staging = support / "upgrades" / f"{rid}.prepared"
    staging.mkdir()
    digest = hashlib.sha256(candidate_archive.read_bytes()).hexdigest()
    shutil.copy2(candidate_archive, staging/f"{digest}.tar.gz")
    signature_text = candidate_archive.with_name(candidate_archive.name+".sig").read_text().strip()
    (staging/"prepared.json").write_text(json.dumps({"schema_version":"desktop_prepared_update_v1",
        "channel":"stable","version":target_version,"signature":signature_text,"sha256":digest}))
    start(target_version, rid)
    upgraded = ready(rid, target_version, target_rev)
    print("Verified signed candidate and automatic relaunch", flush=True)
    assert upgraded["pid"] != original["pid"]
    command("/usr/bin/codesign", "--verify", "--deep", "--strict", bundle)
    assert identity(bundle)[2]["surface"] == "complete_turn"
    verify_session()
    backup = support / "update-backup/previous/LoopX.app"
    command("/usr/bin/codesign", "--verify", "--deep", "--strict", backup)
    assert identity(backup)[:2] == (old_version, old_revision)
    stop(upgraded)

    # The verifier manually restores the native backup on the disposable volume.
    # This checks recoverable data and the preserved App, not the UI restore click.
    bundle.rename(root / "upgraded.app")
    command("/usr/bin/ditto", backup, bundle)
    rollback_id = uuid4().hex
    start(old_version, rollback_id)
    restored = ready(rollback_id, old_version, old_revision, (old_revision, target_rev))
    verify_session()
    stop(restored)
    print("Verified original conversation after backup restore", flush=True)
    Path(receipt_file).write_text(json.dumps({"ok":True, "source_revision":target_rev, "version":target_version,
        "archive_sha256":digest, "installed_signature_rechecked": True, "native_signed_activation":True,
        "automatic_relaunch_ready":True, "same_original_session":True, "manual_previous_app_restore":True,
        "exact_signed_desktop_scope":"native prepared-cache route on an isolated macOS runner",
        "distribution_pointer_moved":False}, indent=2))
finally:
    for pid in owned_pids:
        if worker_running(pid):
            os.kill(pid, signal.SIGTERM)
