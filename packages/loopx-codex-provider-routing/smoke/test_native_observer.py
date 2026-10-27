"""Real process ownership; synthetic allowance never accesses native credentials."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
native = importlib.import_module("loopx_codex_provider_routing.native")

CHILD = r'''
import json, os, sys, time
from pathlib import Path
from loopx_codex_provider_routing.native import NativeAccounts, Watcher
root = Path(sys.argv[1])
def transport(adapter, operation, **parameters):
    if operation == "status":
        return {"slots": ["a", "b"], "active_slot": "a", "selection_revision": "fixture",
                "current_login_matches": True, "recovery_required": False}
    if operation == "observe":
        with (root / "observations").open("a") as stream:
            stream.write(str(os.getpid()) + "\n")
        return {"observations": [{"slot": s, "status": "ok", "observed_at": time.time(),
                "usage": {"rate_limit": {"primary_window": {"used_percent": 0,
                          "reset_at": time.time() + 3600}}, "credits": {"has_credits": False}}}
                for s in parameters["slots"]]}
    raise AssertionError("No native account effect is authorized: " + operation)
provider = NativeAccounts(root, transport=transport)
tick = provider.tick
def observed_tick():
    with (root / "ticks").open("a") as stream:
        stream.write(str(os.getpid()) + "\n")
    value = tick()
    value["retry_at"] = time.time() + 5  # Bound the fixture's scheduling wait only.
    return value
provider.tick = observed_tick
Watcher(provider).run()
'''


@unittest.skipUnless(os.name == "posix", "Native account ownership uses POSIX locks")
class NativeObserverOwnership(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native-observer-")
        self.root = Path(self.temporary.name).resolve()
        self.children = []

    def tearDown(self):
        for process in self.children:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.temporary.cleanup()

    def policy(self, name):
        root = self.root / name
        provider = native.NativeAccounts(root)
        provider.root.mkdir(parents=True, mode=0o700)
        native.atomic(provider.path, {
            "schema_version": native.SCHEMA, "revision": 1,
            "policy": {"enabled": True, "slots": ["a", "b"], "cooldown_seconds": 300,
                       "restart_clients": False, "force_quit_clients": False},
            "adapter": {"synthetic": True}, "observations": [], "last_switch_at": None,
            "action": "checking",
        })
        return root, provider

    def start(self, root):
        env = {**os.environ, "PYTHONPATH": str(SOURCE)}
        process = subprocess.Popen([sys.executable, "-c", CHILD, str(root)], env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self.children.append(process)
        return process

    def ticks(self, root):
        path = root / "ticks"
        return path.read_text().splitlines() if path.exists() else []

    def wait_for_ticks(self, root, count):
        deadline = time.monotonic() + 9
        while len(self.ticks(root)) < count and time.monotonic() < deadline:
            time.sleep(0.025)
        self.assertEqual(len(self.ticks(root)), count)

    def test_only_one_process_observes_and_owner_exit_allows_takeover(self):
        root, _ = self.policy("shared")
        first = self.start(root)
        self.wait_for_ticks(root, 1)
        second = self.start(root)
        time.sleep(0.4)
        self.assertIsNone(first.poll())
        self.assertIsNone(second.poll())
        self.assertEqual(self.ticks(root), [str(first.pid)])
        first.terminate()
        first.wait(timeout=5)
        self.wait_for_ticks(root, 2)
        self.assertEqual(self.ticks(root), [str(first.pid), str(second.pid)])

    def test_explicit_separate_policy_owners_do_not_block_each_other(self):
        left, _ = self.policy("left")
        right, _ = self.policy("right")
        first, second = self.start(left), self.start(right)
        self.wait_for_ticks(left, 1)
        self.wait_for_ticks(right, 1)
        self.assertEqual(self.ticks(left), [str(first.pid)])
        self.assertEqual(self.ticks(right), [str(second.pid)])

    def test_disable_from_another_process_stops_native_observation(self):
        root, provider = self.policy("disable")
        process = self.start(root)
        deadline = time.monotonic() + 9
        observations = root / "observations"
        while not observations.exists() and time.monotonic() < deadline:
            time.sleep(0.025)
        self.assertEqual(observations.read_text().splitlines(), [str(process.pid)])
        provider.configure(expected_revision=1, enabled=False, slots=["a", "b"])
        self.wait_for_ticks(root, 2)
        self.assertEqual(observations.read_text().splitlines(), [str(process.pid)])
        self.assertFalse(provider._load()["policy"]["enabled"])

    def test_untrusted_observer_lock_is_not_followed(self):
        root, provider = self.policy("untrusted")
        target = self.root / "unrelated"
        target.write_text("untouched")
        (provider.root / "observer.lock").symlink_to(target)
        process = self.start(root)
        time.sleep(0.4)
        self.assertIsNone(process.poll())
        self.assertEqual(self.ticks(root), [])
        self.assertEqual(target.read_text(), "untouched")


if __name__ == "__main__":
    unittest.main()
