"""Bounded archive-installer download; never execute a partial response."""

import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def run_archive_installer(
    url: str, *, env: dict[str, str], timeout_seconds: int
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    download_deadline = min(deadline, time.monotonic() + 60)
    observation: dict[str, Any] = {"stage": "installer_download", "attempts": []}
    # Deliberately omit URLs, headers, bodies and raw curl errors: any of these
    # can carry proxy credentials or signed URL parameters.
    with TemporaryDirectory(prefix="loopx-update-") as raw:
        script = Path(raw) / "install.sh"
        script.touch(mode=0o600)
        code = 28
        for attempt in range(1, 4):
            remaining = download_deadline - time.monotonic()
            if remaining <= 0:
                break
            # Remove bytes from a failed transfer before the next attempt.
            script.write_bytes(b"")
            args = [
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--location",
                "--connect-timeout",
                "10",
                "--max-time",
                str(min(20, remaining)),
                "--output",
                str(script),
                "--write-out",
                "%{http_code}",
                url,
            ]
            try:
                result = subprocess.run(
                    args,
                    text=True, encoding="utf-8", errors="replace",
                    capture_output=True,
                    env=env,
                    timeout=remaining,
                    check=False,
                )
                code = result.returncode
                status = (
                    int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
                )
            except subprocess.TimeoutExpired:
                code, status = 28, 0
            except OSError:
                code, status = 127, 0
            observation["attempts"].append(
                {"attempt": attempt, "curl_returncode": code, "http_status": status}
            )
            if code == 0 and 200 <= status < 300 and script.stat().st_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    code = 28
                    break
                observation["stage"] = "installer_execution"
                installer_env = dict(env)
                installer_env["LOOPX_INSTALLER_TIMEOUT_SECONDS"] = str(
                    max(1, int(remaining))
                )
                try:
                    return subprocess.run(
                        ["bash", str(script)],
                        check=False,
                        text=True, encoding="utf-8", errors="replace",
                        capture_output=True,
                        env=installer_env,
                        timeout=remaining,
                    ), observation
                except subprocess.TimeoutExpired:
                    return subprocess.CompletedProcess(
                        [],
                        124,
                        "",
                        "Installer execution timed out; it was not retried.",
                    ), observation
            # 403 can be transient at an edge/proxy; retry only within this
            # budget. Never change credentials, endpoint, or TLS verification.
            retryable = status in {403, 408, 429, 500, 502, 503, 504} or code in {
                5,
                6,
                7,
                18,
                28,
                35,
                52,
                55,
                56,
            }
            code = code or 22
            if not retryable or attempt == 3:
                break
            remaining = download_deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(attempt, remaining))
    return subprocess.CompletedProcess(
        [], code, "", "Installer download failed; see installer_download diagnostics."
    ), observation
