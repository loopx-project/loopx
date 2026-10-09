"""Physical Node launch observation for the existing Effect transport.

This adapter does not select capabilities or authorize work. Startup and doctor
share its observations and public-safe remediation; neither interprets a failed
version probe as an unsupported version.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum

MINIMUM_NODE_VERSION = (22, 22, 3)
MINIMUM_NODE_VERSION_TEXT = ".".join(str(part) for part in MINIMUM_NODE_VERSION)
STARTUP_READY_TIMEOUT_SECONDS = 15.0
HOST_PERMISSION_RECOMMENDATION = (
    "retry the same registry, Goal, Agent and Turn through host-approved "
    "local runtime access; do not enable optional capabilities, replace "
    "authority or spend until the guard succeeds"
)
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(.*?))?(?:\+.*)?$")


class NodeProbeOutcome(StrEnum):
    """Local process facts, projected onto the existing readiness statuses."""

    READY = "ready"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    PERMISSION_DENIED = "permission_denied"
    LAUNCH_FAILED = "launch_failed"
    EXIT_FAILED = "exit_failed"
    INVALID_VERSION = "invalid_version"


_FAILURES = {
    NodeProbeOutcome.MISSING: (
        "node_unavailable", f"LoopX Effect runtime requires Node.js {MINIMUM_NODE_VERSION_TEXT} or newer on PATH",
    ),
    NodeProbeOutcome.UNSUPPORTED: (
        "node_unavailable", f"LoopX Effect runtime requires Node.js {MINIMUM_NODE_VERSION_TEXT} or newer",
    ),
    NodeProbeOutcome.TIMED_OUT: (
        "node_probe_timeout", "Node.js version probe exceeded the bounded startup budget; compatibility is unknown",
    ),
    NodeProbeOutcome.PERMISSION_DENIED: (
        "runtime_host_permission_denied", "Host permission denied the Node.js version probe before request dispatch",
    ),
    NodeProbeOutcome.LAUNCH_FAILED: (
        "node_probe_launch_failed", "Node.js version probe could not be launched; compatibility is unknown",
    ),
    NodeProbeOutcome.EXIT_FAILED: (
        "node_probe_exit_failed", "Node.js version probe exited unsuccessfully; compatibility is unknown",
    ),
    NodeProbeOutcome.INVALID_VERSION: (
        "node_probe_invalid_version", "Node.js version probe returned no valid version; compatibility is unknown",
    ),
}
_REMEDIATION = {
    "node_unavailable": f"Install or activate Node.js {MINIMUM_NODE_VERSION_TEXT} or newer on PATH, then run `loopx doctor --deep`.",
    "node_probe_timeout": "Check host load and the Node.js launcher on PATH, then rerun `loopx doctor --deep`. A timed-out probe does not establish version incompatibility.",
    "node_probe_launch_failed": "Repair the Node.js launcher on PATH, then rerun `loopx doctor --deep`.",
    "node_probe_exit_failed": "Repair the Node.js launcher on PATH, then rerun `loopx doctor --deep`.",
    "node_probe_invalid_version": "Verify that the Node.js launcher on PATH returns a valid version, then rerun `loopx doctor --deep`.",
    "runtime_host_permission_denied": HOST_PERMISSION_RECOMMENDATION,
}


def node_probe_remediation(diagnostic_code: str) -> str | None:
    return _REMEDIATION.get(diagnostic_code)


@dataclass(frozen=True)
class NodeProbe:
    outcome: NodeProbeOutcome
    executable: str | None = None
    version: str | None = None

    @property
    def ready(self) -> bool:
        return self.outcome is NodeProbeOutcome.READY

    @property
    def status(self) -> str:
        if self.outcome in {NodeProbeOutcome.READY, NodeProbeOutcome.MISSING, NodeProbeOutcome.UNSUPPORTED}:
            return self.outcome.value
        return "probe_failed"

    @property
    def diagnostic_code(self) -> str | None:
        failure = _FAILURES.get(self.outcome)
        return failure[0] if failure else None

    @property
    def failure_message(self) -> str:
        return _FAILURES[self.outcome][1]

    @property
    def recommended_action(self) -> str | None:
        return node_probe_remediation(self.diagnostic_code or "")


def probe_node(*, timeout: float = STARTUP_READY_TIMEOUT_SECONDS) -> NodeProbe:
    executable = shutil.which("node")
    if executable is None:
        return NodeProbe(NodeProbeOutcome.MISSING)
    try:
        # run kills and waits for the probe child on timeout/cancellation. No
        # retry, version cache, shell or semantic request is involved.
        completed = subprocess.run(
            [executable, "--version"], check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return NodeProbe(NodeProbeOutcome.TIMED_OUT, executable)
    except PermissionError:
        return NodeProbe(NodeProbeOutcome.PERMISSION_DENIED, executable)
    except OSError:
        return NodeProbe(NodeProbeOutcome.LAUNCH_FAILED, executable)
    if completed.returncode != 0:
        return NodeProbe(NodeProbeOutcome.EXIT_FAILED, executable)
    match = _VERSION_RE.fullmatch(completed.stdout.strip())
    if match is None:
        return NodeProbe(NodeProbeOutcome.INVALID_VERSION, executable)
    version = tuple(int(part) for part in match.groups()[:3])
    prerelease = match.group(4)
    below_minimum = version < MINIMUM_NODE_VERSION or (
        version == MINIMUM_NODE_VERSION and prerelease is not None
    )
    outcome = NodeProbeOutcome.UNSUPPORTED if below_minimum else NodeProbeOutcome.READY
    version_text = ".".join(str(part) for part in version)
    if prerelease is not None:
        version_text += f"-{prerelease}"
    return NodeProbe(outcome, executable, version_text)
