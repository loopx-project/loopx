"""The shared Turn argument must render for the shell that runs it.

Native Windows has no POSIX ``${NAME:?}`` expansion, so a command built there
has to name the PowerShell variable that carries the Turn. The same helper owns
the settlement binding arguments and the heartbeat-receipt retry hint, so both
inherit one rule instead of one copy each.
"""

from __future__ import annotations

import os

import pytest

from loopx.control_plane.quota import effect_program as quota_effect_program
from loopx.control_plane.quota.effect_program import turn_instance_arg

POSIX_REF = "${LOOPX_TURN:?}"
WINDOWS_REF = "$LOOPX_TURN"
POSIX_ARG = f'--turn-instance-id "{POSIX_REF}"'
WINDOWS_ARG = f"--turn-instance-id {WINDOWS_REF}"


def _host(monkeypatch: pytest.MonkeyPatch, windows: bool) -> None:
    monkeypatch.setattr(quota_effect_program, "_is_windows_host", lambda: windows)


@pytest.mark.parametrize(
    ("windows", "expected"),
    ((False, POSIX_ARG), (True, WINDOWS_ARG)),
)
def test_a_host_default_reference_matches_the_running_shell(
    monkeypatch: pytest.MonkeyPatch, windows: bool, expected: str
) -> None:
    _host(monkeypatch, windows)

    assert turn_instance_arg() == expected


@pytest.mark.parametrize("windows", [False, True])
def test_an_explicit_turn_is_quoted_unchanged(
    monkeypatch: pytest.MonkeyPatch, windows: bool
) -> None:
    _host(monkeypatch, windows)

    assert turn_instance_arg("turn-settlement-host-1") == (
        "--turn-instance-id turn-settlement-host-1"
    )


def test_a_posix_reference_on_windows_names_the_powershell_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(monkeypatch, True)

    assert turn_instance_arg(POSIX_REF) == WINDOWS_ARG


def test_the_default_matches_the_running_host_shell() -> None:
    expected = WINDOWS_ARG if os.name == "nt" else POSIX_ARG

    assert turn_instance_arg() == expected
