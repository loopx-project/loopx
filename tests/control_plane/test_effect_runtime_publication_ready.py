"""A visible locator must not admit effects before its publication settles."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import time

import pytest

from loopx.control_plane import effect_runtime


@pytest.mark.parametrize("method", ["runtime.ping", "turn_journal.write"])
def test_first_request_waits_for_real_publication_cleanup_and_recovers(
    tmp_path: Path, monkeypatch, request: pytest.FixtureRequest, method: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    opened = tmp_path / "claim-opened"
    release = tmp_path / "release"
    preload = tmp_path / "pause-publication.mjs"
    # Pause the real Node file handle after exclusive claim creation, before
    # owner bytes or lock cleanup. No mocked transport, dispatch or lock owner.
    preload.write_text("""import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
const original = fs.promises.open;
fs.promises.open = async function(path, ...args) {
  const handle = await original.call(this, path, ...args);
  if (String(path).includes('.ts-effect.lock.claim.') &&
      String(path).includes('runtime-') &&
      !fs.existsSync(process.env.LOOPX_TEST_PUBLICATION_OPENED)) {
    fs.writeFileSync(process.env.LOOPX_TEST_PUBLICATION_OPENED, 'opened');
    while (!fs.existsSync(process.env.LOOPX_TEST_PUBLICATION_RELEASE)) {
      await new Promise(resolve => setTimeout(resolve, 5));
    }
  }
  return handle;
};
syncBuiltinESMExports();
""", encoding="utf-8")
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    request.addfinalizer(effect_runtime.restart_effect_runtime)
    monkeypatch.setenv("NODE_OPTIONS", f"--import={preload.as_uri()}")
    monkeypatch.setenv("LOOPX_TEST_PUBLICATION_OPENED", str(opened))
    monkeypatch.setenv("LOOPX_TEST_PUBLICATION_RELEASE", str(release))
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "60000")
    effect_id = "fixture-goal:fixture-agent:todo_fixture0001:publication"
    journal_path = tmp_path / "turn.json"
    journal = {
        "schema_version": "loopx_turn_journal_v0",
        "goal_id": "fixture-goal",
        "turn_key": "sha256:" + "a" * 64,
        "status": "in_progress",
        "completed_phases": [],
        "plan": {
            "turn_envelope": {
                "goal_id": "fixture-goal", "agent_id": "fixture-agent",
                "action": {"selected_todo": {"todo_id": "todo_fixture0001"}},
            },
            "transaction": {
                "turn_key": "sha256:" + "a" * 64,
                "turn_instance_id": "publication",
                "settlement_plan": {
                    "schema_version": "quota_settlement_plan_v1",
                    "identity": {
                        "schema_version": "quota_settlement_identity_v0",
                        "effect_id": effect_id, "goal_id": "fixture-goal",
                        "agent_id": "fixture-agent", "todo_id": "todo_fixture0001",
                        "turn_instance_id": "publication",
                    },
                },
            },
        },
    }
    params = {"path": str(journal_path), "journal": journal,
              "expected_effect_id": effect_id}
    info_path = effect_runtime._runtime_info_path(effect_runtime._runtime_fingerprint())
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(
            effect_runtime.effect_runtime_result, method,
            params if method == "turn_journal.write" else {}, retry_safe=False,
        )
        try:
            deadline = time.monotonic() + 5
            while not opened.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert opened.exists(), "real publication did not reach its cleanup claim"
            assert info_path.exists()
            lock_path = Path(str(info_path) + ".ts-effect.lock")
            claims = list(runtime_dir.glob("*.ts-effect.lock.claim.*"))
            assert lock_path.exists() and len(claims) == 1
            assert claims[0].stat().st_size == 0
            # The old implementation replies/commits while this claim is blank.
            time.sleep(0.2)
            assert not response.done(), "first request acknowledged unfinished publication"
            assert not journal_path.exists(), "typed write ran before publication settled"
        finally:
            release.touch()
        result = response.result(timeout=5)
    assert not lock_path.exists()
    assert not list(runtime_dir.glob("*.ts-effect.lock.claim.*"))
    if method == "turn_journal.write":
        assert result["appended"] is True and result["replayed"] is False
        assert json.loads(journal_path.read_text(encoding="utf-8")) == journal
    original = effect_runtime.effect_runtime_result("runtime.ping", {})
    try:
        os.kill(int(original["pid"]), signal.SIGTERM)
        time.sleep(0.1)
        recovered = effect_runtime.effect_runtime_result(
            "turn_journal.write", params, retry_safe=True,
        )
        assert recovered["replayed"] is (method == "turn_journal.write")
        assert recovered["appended"] is (method == "runtime.ping")
        assert json.loads(journal_path.read_text(encoding="utf-8")) == journal
        replacement = effect_runtime.effect_runtime_result("runtime.ping", {})
        assert replacement["pid"] != original["pid"]
    finally:
        effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)
