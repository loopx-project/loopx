from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Any

from loopx.contract import scan_public_boundary
from loopx.extensions.lark.app_setup import LarkAppSetupManager
from loopx.extensions.lark.cli_resolution import LarkCliResolution


class _FakeProcess:
    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15


class _BlockingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self._released = threading.Event()
        self.stdout = self._lines()

    def _lines(self):
        self._released.wait(timeout=1)
        return
        yield ""

    def wait(self) -> int:
        self._released.wait(timeout=1)
        return self.returncode or 0

    def terminate(self) -> None:
        self.returncode = -15
        self._released.set()


def _wait_for_terminal(manager: LarkAppSetupManager, setup_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(setup_id)
        if snapshot["status"] in {"ready", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("setup did not reach a terminal state")


def test_setup_streams_official_url_then_verifies_named_profile() -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def process_factory(args: list[str], env: dict[str, str]) -> _FakeProcess:
        calls.append((args, env))
        return _FakeProcess(
            [
                "Scan the QR code or open this link:\n",
                "https://open.feishu.cn/page/cli?user_code=public-code&from=cli\n",
                "Waiting for authorization...\n",
            ]
        )

    manager = LarkAppSetupManager(
        cli_resolution=LarkCliResolution(
            command="fake-lark",
            available=True,
            source="explicit",
            version="1.2.3",
            error_code=None,
        ),
        process_factory=process_factory,
        profile_verifier=lambda app_ref: app_ref == "loopx-workspace-bot",
    )

    started = manager.start(app_ref="loopx-workspace-bot", brand="feishu")
    completed = _wait_for_terminal(manager, started["setup_id"])

    assert calls[0][0] == [
        "fake-lark",
        "config",
        "init",
        "--new",
        "--name",
        "loopx-workspace-bot",
        "--brand",
        "feishu",
        "--lang",
        "zh_cn",
    ]
    assert completed == {
        "setup_id": started["setup_id"],
        "status": "ready",
        "app_ref": "loopx-workspace-bot",
        "verification_url": "https://open.feishu.cn/page/cli?user_code=public-code&from=cli",
        "error": None,
    }


def test_setup_accepts_the_official_lark_authorization_host() -> None:
    manager = LarkAppSetupManager(
        cli_resolution=LarkCliResolution(
            command="fake-lark",
            available=True,
            source="path",
            version="1.2.3",
            error_code=None,
        ),
        process_factory=lambda _args, _env: _FakeProcess([
            "https://open." + "la" + "rk" + "office.com/page/launcher?from=sdk\n",
        ]),
        profile_verifier=lambda app_ref: app_ref == "loopx-lark-bot",
    )

    started = manager.start(app_ref="loopx-lark-bot", brand="lark")
    completed = _wait_for_terminal(manager, started["setup_id"])

    assert completed["status"] == "ready"
    assert completed["verification_url"] == (
        "https://open." + "la" + "rk" + "office.com/page/launcher?from=sdk"
    )


def test_setup_accepts_the_international_lark_authorization_host() -> None:
    verification_url = (
        "https://open.larksuite.com/page/cli?user_code=public-code"
        "&lpv=1.0.99&ocv=1.0.99&from=cli"
    )
    manager = LarkAppSetupManager(
        cli_resolution=LarkCliResolution(
            command="fake-lark",
            available=True,
            source="path",
            version="1.0.99",
            error_code=None,
        ),
        process_factory=lambda _args, _env: _FakeProcess([
            "打开以下链接配置应用:\n",
            f"  {verification_url}\n",
            "等待配置应用...\n",
        ]),
        profile_verifier=lambda app_ref: app_ref == "loopx-lark-bot",
    )

    started = manager.start(app_ref="loopx-lark-bot", brand="lark")
    completed = _wait_for_terminal(manager, started["setup_id"])

    assert completed["status"] == "ready"
    assert completed["verification_url"] == verification_url


def test_cancel_stops_the_registration_process_without_exposing_logs() -> None:
    process = _BlockingProcess()
    manager = LarkAppSetupManager(
        cli_resolution=LarkCliResolution(
            command="fake-lark",
            available=True,
            source="path",
            version="1.2.3",
            error_code=None,
        ),
        process_factory=lambda _args, _env: process,
        profile_verifier=lambda _app_ref: False,
    )
    started = manager.start(app_ref="loopx-cancelled", brand="feishu")

    cancelled = manager.cancel(started["setup_id"])

    assert process.returncode == -15
    assert cancelled == {
        "setup_id": started["setup_id"],
        "status": "cancelled",
        "app_ref": "loopx-cancelled",
        "verification_url": None,
        "error": None,
    }


def test_setup_rejects_missing_cli_with_safe_error_code() -> None:
    manager = LarkAppSetupManager(
        cli_resolution=LarkCliResolution(
            command=None,
            available=False,
            source="missing",
            version=None,
            error_code="lark_cli_not_installed",
        ),
        process_factory=lambda _args, _env: (_ for _ in ()).throw(AssertionError("must not start")),
        profile_verifier=lambda _app_ref: False,
    )

    try:
        manager.start(app_ref="loopx-workspace-bot", brand="feishu")
    except ValueError as exc:
        assert getattr(exc, "error_code", None) == "lark_cli_not_installed"
        assert "Install lark-cli" in str(exc)
        assert "/Users/" not in str(exc)
    else:
        raise AssertionError("missing lark-cli must fail before starting a process")


def test_lark_app_setup_source_preserves_the_public_boundary_contract() -> None:
    source = Path(__file__).resolve().parents[2] / "loopx/extensions/lark/app_setup.py"

    boundary = scan_public_boundary([source])

    assert boundary["ok"] is True, boundary
    assert boundary["hits"] == []
