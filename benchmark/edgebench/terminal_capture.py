"""Opt-in failure preservation at the native solver's terminal boundary.

This Docker adapter reuses the existing joint-file/native-capsule capture owner.
It does not qualify a failed conversation, settle a Turn, or authorize recovery.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import PurePosixPath

from loopx.file_lock import write_json_atomic
from benchmark.runtime.layout import _CODEX_HOME, _CONTROL, _LOOPX_RUNTIME, _PHASE_DEADLINE, _REGISTRY


def terminal_components(declaration: dict, workspace: str) -> list[dict[str, str]]:
    """Explicit full-capture participants, without the credential-bearing home."""
    from benchmark.runtime.research_artifacts import validate_research_scope

    validate_research_scope(workspace, ["."])
    native_home = PurePosixPath(_CODEX_HOME)
    paths = [PurePosixPath(workspace), *(PurePosixPath(row["source_path"]) for row in declaration["components"])]
    if any(path == native_home or path in native_home.parents or native_home in path.parents for path in paths):
        raise ValueError("Terminal native participants are allowlisted separately; never capture the credential home")
    components = [{"id": "terminal_workspace", "source_path": workspace}]
    for entry in declaration["components"]:
        path = PurePosixPath(entry["source_path"])
        if path == PurePosixPath(workspace) or PurePosixPath(workspace) in path.parents:
            continue  # The complete workspace owns a declared submitted subset too.
        components.append(dict(entry))
    components.extend({"id": name, "source_path": path} for name, path in (
        ("terminal_registry", _REGISTRY),
        ("terminal_authority", _LOOPX_RUNTIME),
        ("terminal_scheduler", f"{_CONTROL}/scheduler-state.json"),
        ("terminal_wakes", "/logs/agent/wakes"),
        ("terminal_sessions", f"{_CODEX_HOME}/sessions"),
        ("terminal_native_state", f"{_CODEX_HOME}/state_5.sqlite"),
        ("terminal_native_history", f"{_CODEX_HOME}/thread_history_1.sqlite"),
        ("terminal_native_config", f"{_CODEX_HOME}/config.toml"),
    ))
    # Let the shared transport reject aliases/overlaps before launch, rather
    # than silently dropping an explicit external research participant.
    from benchmark.edgebench.checkpoint_worker import _validate_request
    _validate_request({"container_id": "0" * 64, "components": components,
                       "phase_deadline_path": _PHASE_DEADLINE})
    return components


class FailurePreservation:
    """One terminal attempt; a stopped source remains until explicit disposal."""
    def __init__(self, supervisor, components, receipt_path):
        self.supervisor = supervisor
        self.components = components
        self.receipt_path = receipt_path
        self.receipt = None
        self.retain_source = False

    def _write(self):
        write_json_atomic(self.receipt_path,
                          self.receipt, mode=0o600, fsync_directory=True)

    def _persist_receipt(self):
        """Optional diagnosis cannot replace the solver or stop outcome."""
        try:
            self._write()
        except OSError as error:
            self.receipt["receipt_write_error_kind"] = type(error).__name__
            logging.getLogger(__name__).warning(
                "Terminal preservation receipt could not be saved (%s); "
                "source retention and verified solver/stop outcomes remain authoritative.",
                type(error).__name__,
            )

    def finish(self, execution):
        if self.receipt is not None:
            return  # Repeated cleanup cannot capture later state under this point.
        if execution is not None and execution.get("exit_code") == 0 and not execution.get("timed_out"):
            try:
                self.supervisor.close()
                return
            except Exception:
                # An unreleased observer barrier is itself an uncertain terminal
                # outcome. Never delete its source via the ordinary finally.
                pass
        # Arm retention before any IO, observer join or optional export can fail.
        self.retain_source = True
        started = time.monotonic()
        supervisor = self.supervisor
        self.receipt = {"container_id": self.supervisor.container.id,
            "observed_at_epoch": time.time(),
            "capture_id": uuid.uuid4().hex, "execution": execution,
            "capture_attempted": False, "capture_succeeded": False,
            "restore_qualified": False, "source_retained": None, "source_stopped": False}
        try:
            self._persist_receipt()
            self.supervisor.close()
            maintenance = getattr(self.supervisor, "maintenance", None)
            if maintenance is not None and maintenance.is_alive():
                raise RuntimeError("Checkpoint retention has not settled")
            if execution is None or execution.get("execution_started") is not True:
                return  # A failed setup has no original phase/native session to seal.
            from .checkpoint_worker import capture_worker
            from benchmark.runtime.checkpoint_handoff import CHECKPOINT_CAPTURE_SECONDS
            self.receipt["capture_attempted"] = True
            self._persist_receipt()
            request = self.supervisor.destination / "terminal-request.json"
            write_json_atomic(request, {"container_id": self.supervisor.container.id,
                "components": self.components, "phase_deadline_path": supervisor.capture_phase_deadline_path()},
                mode=0o600, fsync_directory=True)
            point = capture_worker(supervisor.controller_file, token=supervisor.token,
                capture_id=self.receipt["capture_id"],
                destination=self.supervisor.destination / self.receipt["capture_id"],
                request_file=request, timeout=CHECKPOINT_CAPTURE_SECONDS)
            self.receipt.update(capture_succeeded=True, capture=point)
        except Exception as error:
            # No remote message, output, account information or token is retained.
            self.receipt["error_kind"] = type(error).__name__
        finally:
            # Native final-archive extraction and grading drain occur before its
            # cleanup callback. Stop here so orphaned work cannot run through
            # that interval; later cleanup only verifies this same source.
            try:
                self.stop_source(supervisor.container)
            finally:
                self.receipt["preservation_elapsed_seconds"] = time.monotonic() - started
                self._persist_receipt()

    def stop_source(self, container):
        """Release compute, never remove the only source after an uncertain save."""
        try:
            _stop_retained_worker(container, self.receipt)
        finally:
            self._persist_receipt()


def _stop_retained_worker(container, receipt):
    if container.id != receipt["container_id"]:
        raise ValueError("Failure preservation requires the original worker")
    try:
        container.reload()
        if container.attrs["State"].get("Running"):
            try:
                container.stop(timeout=15)
            except Exception:
                container.kill()  # Bounded daemon operation; never kill a guessed host PID.
        container.reload()
        state = container.attrs["State"]
        stopped = (state.get("Running") is False and state.get("Paused") is False
                   and state.get("Restarting") is False and state.get("Pid") == 0)
        receipt.update(source_retained=True, source_stopped=stopped)
        if not stopped:
            raise RuntimeError("Preserved worker is not confirmed stopped")
    except Exception as error:
        receipt["stop_error_kind"] = type(error).__name__
        raise


def retain_returned_native_worker(container, *, evidence: dict, receipt_path):
    """Preserve the original Official worker after its confirmed final failure."""
    receipt = {**evidence, "observed_at_epoch": time.time(),
               "capture_attempted": False, "capture_succeeded": False,
               "restore_qualified": False, "source_retained": None, "source_stopped": False}
    logger = logging.getLogger(__name__)
    try:
        _stop_retained_worker(container, receipt)
    except Exception as error:
        receipt.setdefault("stop_error_kind", type(error).__name__)
        logger.warning("Native failed worker stop is unconfirmed (%s); retaining the source.", type(error).__name__)
    finally:
        try:
            write_json_atomic(receipt_path, receipt, mode=0o600, fsync_directory=True)
        except OSError as error:
            logger.warning("Native failed worker receipt unavailable (%s); retaining the source.", type(error).__name__)
