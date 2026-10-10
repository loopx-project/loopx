from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from loopx.pi_goal_mode import extension_source, runtime_source


def test_pi_extension_source_is_managed_and_self_contained() -> None:
    text = extension_source()
    # Managed marker lets install/uninstall retire the file safely.
    assert "loopx-managed-slash-command:v1 command=/loopx surface=pi-extension" in text
    # Self-contained: only type-only imports from the pi package (erased at
    # runtime); runtime imports are node builtins and typebox (loader-aliased).
    pi_package_lines = [
        line
        for line in text.splitlines()
        if "@earendil-works/pi-coding-agent" in line
    ]
    assert pi_package_lines, "extension must type-import the pi package"
    assert all(line.strip().startswith("import type") for line in pi_package_lines), (
        pi_package_lines
    )
    assert "node:" in text


def test_pi_extension_registers_command_tool_and_events() -> None:
    text = extension_source()
    assert 'pi.registerCommand("loopx"' in text
    assert "loopx_goal_activate" in text
    assert "pi.on(\"agent_settled\"" in text
    assert "pi.on(\"before_agent_start\"" in text
    assert "pi.on(\"session_shutdown\"" in text
    # The extension drives the agent through Pi's message API, never by
    # fabricating host-level goal completion.
    assert "sendUserMessage" in text


def test_pi_guided_start_goal_delivers_packet_without_editor_box() -> None:
    text = extension_source()
    # /loopx <goal text> must hand the guided start-goal packet straight to the
    # agent (same followUp/triggerTurn pattern as heartbeat injection) instead
    # of prefilling the editor: no popup box, no manual Enter step before the
    # agent follows the ordered transaction.
    assert 'pi.sendUserMessage(JSON.stringify(deliveredPacket), {' in text
    assert 'deliverAs: "followUp"' in text
    assert 'triggerTurn: true' in text
    # The editor-prefill flow must not remain anywhere in the extension: it is
    # the mechanism that pops a box and forces the user to press Enter.
    assert "setEditorText" not in text


def test_pi_extension_disposes_the_loop_on_session_shutdown() -> None:
    adapter = extension_source()
    runtime = runtime_source()
    # session_shutdown must atomically invalidate the extension instance: the
    # adapter disposes the whole loop instead of only cancelling the current
    # session timer.
    assert "loop.dispose()" in adapter
    assert 'pi.on("session_shutdown"' in adapter
    # The runtime owns the instance-wide disposed guard after an in-flight
    # quota probe returns, and re-checks the same epoch after every await that
    # can yield to the event loop (post-probe read, writes). Goal identity is
    # enforced through the persisted generation: activate() bumps it and every
    # evaluation write commits via the store's compare-and-swap.
    assert "disposed = true" in runtime
    assert "cancelAll()" in runtime
    assert "if (disposed) return" in runtime
    assert "epoch !== instanceEpoch" in runtime
    assert "capturedGen" in runtime
    assert "casMatches" in runtime
    assert "expected" in runtime


def test_pi_extension_never_self_declares_closure() -> None:
    adapter = extension_source()
    runtime = runtime_source()
    # Continuation authority stays with LoopX quota should-run.
    assert "--runtime-profile" in runtime
    assert "generic_cli" in runtime
    assert "should-run" in runtime
    assert "terminal_no_followup" in runtime
    assert "validated_goal_closure" in runtime
    # The adapter wires the loop to pi.sendUserMessage; neither side fakes a
    # native goal completion object.
    assert "sendUserMessage" in adapter
    assert "goal_complete" not in runtime


def test_pi_extension_runtime_is_managed_and_directly_executable() -> None:
    text = runtime_source()
    assert "loopx-managed-slash-command:v1 command=/loopx surface=pi-extension-runtime" in text
    assert "node:" in text
    assert "createGoalLoop" in text
    assert "export function createBindingStore" in text
    assert "export function createMemoryBindingStore" in text
    assert "export function createEphemeralSessionIdentity" in text
    assert "export function waitPlan" in text


def test_pi_task_lease_facade_exposes_typed_cli_boundary() -> None:
    text = runtime_source()
    # The Pi host owns only binding/transport and argv construction. Lease
    # state, transitions, and conflict decisions stay in the CLI contract.
    assert "export function buildTaskLeaseArgs" in text
    assert "export async function runPiTaskLease" in text
    assert '"task-lease"' in text
    assert '"task_lease_v0"' in text
    assert "availableCapabilities" in text
    assert "write_scope_conflict" not in text
    assert "lease_cas_mismatch" not in text
    assert "returncode" in text


def test_pi_extension_registers_task_lease_as_an_explicit_tool() -> None:
    text = extension_source()
    assert 'name: "loopx_task_lease"' in text
    assert "runPiTaskLease" in text
    assert "registerTool" in text
    assert "task_lease_v0" in text


def test_pi_activation_uses_host_locked_session_authority() -> None:
    text = extension_source()
    assert "pi_session_authority" in text
    assert "activationToken" in text
    assert "establishSessionAuthority" in text
    assert "LoopX host authority cannot change within one Pi session" in text


def test_pi_extension_uses_ephemeral_identity_without_a_session_file() -> None:
    adapter = extension_source()
    runtime = runtime_source()
    # pi --no-session runs are ephemeral: they must use a unique in-memory
    # identity per extension instance instead of one persistent 'session' key.
    assert "createEphemeralSessionIdentity" in runtime
    assert "createEphemeralSessionIdentity()" in adapter
    assert "ephemeral.store" in adapter
    assert "ephemeral.key" in adapter


def test_pi_extension_uses_collision_resistant_session_keys() -> None:
    adapter = extension_source()
    runtime = runtime_source()
    # Durable session keys must digest the full path, not truncate to 160
    # characters, and the digest must survive filename sanitization.
    assert "sessionKey" in adapter
    assert "export function sessionKey" in runtime
    assert "createHash" in runtime
    assert "sha256" in runtime
    assert "SESSION_KEY_LABEL_MAX" in runtime


def test_pi_extension_binding_state_stays_private_and_scoped() -> None:
    runtime = runtime_source()
    # Bindings persist under the gitignored project .loopx/ tree.
    assert ".loopx" in runtime
    assert "LOOPX_PI_STATE_DIR" in runtime
    assert "0o600" in runtime
    assert "0o700" in runtime


def test_pi_extension_separates_host_prompts_from_user_input() -> None:
    adapter = extension_source()
    runtime = runtime_source()
    # Pi restarts a run with its own prompt after context compaction. That prompt
    # must not pause the loop the way real user input does, so the adapter marks
    # it and the runtime skips the pause for it.
    assert 'pi.on("session_compact"' in adapter
    assert "hostGeneratedPromptPending" in adapter
    assert "{ hostGenerated }" in adapter
    assert "options?.hostGenerated === true" in runtime
    # A pause keeps a durable reason, and every pause path reports it: the
    # previous silent user-prompt pause is what made a stalled loop look healthy.
    assert 'pauseReason: "user_prompt"' in runtime
    assert 'pauseReason: "aborted"' in runtime
    assert 'pauseReason: ""' in runtime
    assert "auto-continuation paused by user input" in runtime


def test_pi_extension_status_widget_stays_compact() -> None:
    adapter = extension_source()
    # A widget is a fixed-height area near the editor: the full packet preview
    # must not be dumped into it (it was truncated mid-sentence and covered the
    # working indicator). Keep a summarized status instead and store the full
    # text in the session entry.
    assert "buildLoopxStatusWidget" in adapter
    assert 'placement: "belowEditor"' in adapter
    assert "setWidget(\"loopx\", undefined)" in adapter
    assert 'pi.appendEntry("loopx-packet"' in adapter
    assert 'setWidget("loopx", display.split' not in adapter


def test_pi_goal_loop_runtime_contract() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Pi goal loop runtime contract")
    test_file = Path(__file__).with_name("pi_goal_loop_runtime.test.mjs")
    subprocess.run([node, "--test", str(test_file)], check=True)
