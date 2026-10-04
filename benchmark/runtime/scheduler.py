"""The existing scheduler invocation shared by native benchmark transports."""

import shlex


def worker_command(env: dict[str, str], *, python: str, source: str,
                   state_file: str, host_timeout: float) -> list[str]:
    wake = [python, "-m", "benchmark.runtime.worker"]
    if env["LOOPX_EXECUTION_MODE"] not in {"heartbeat", "turn"}:
        return wake
    return [
        python, f"{source}/scripts/external_scheduler_worker.py",
        "--cli-bin", env["LOOPX_CLI"], "--registry", env["LOOPX_REGISTRY"],
        "--runtime-root", env["LOOPX_RUNTIME_ROOT"],
        "--runtime-profile", "generic_cli", "--goal-id", env["LOOPX_GOAL_ID"],
        "--agent-id", env["LOOPX_AGENT_ID"], "--state-file", state_file,
        "--wake-cmd", "exec " + shlex.join(wake),
        "--wake-timeout-seconds", str(host_timeout + 150),
        "--quota-timeout-seconds", "30", "--error-backoff-seconds", "15",
    ]
