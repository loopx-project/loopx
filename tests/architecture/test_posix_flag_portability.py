"""No module grows a bare reference to a POSIX-only `os` flag.

`os.O_NOFOLLOW`, `os.O_DIRECTORY` and `os.O_NONBLOCK` are POSIX-only
attributes. Spelling one directly raises `AttributeError` on a host that does
not define it, and it raises it before the bounded-regular-file check on the
next line can refuse anything: the caller reports an untyped crash in place of
its own refusal, or the diagnostic it was opened to produce never appears.
`from os import O_NOFOLLOW` is worse still: the module fails at import time.

The repository already reads these flags with `getattr(os, "<flag>", 0)` in
the peer, completion, decision-capture and effect-runtime readers, and in
`codex_input_trace`, `state_recovery_io` and `checkpoint_retention` after
this change. This guard keeps a new bare site out — including the sites in
files another change is reworking.

Ceilings live in `posix_flag_ceilings.json`, following
`test_package_kernel_import_ratchet`: an increase needs a recorded decision,
and a decrease is accepted as-is so that fixing one of these files does not
have to edit a fixture that would then describe a tree it no longer matches.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LOOPX_ROOT = REPO_ROOT / "loopx"
CEILINGS_FIXTURE = Path(__file__).with_name("posix_flag_ceilings.json")
CEILINGS_SCHEMA = "loopx_posix_flag_ceilings_v0"
POSIX_ONLY_FLAGS = frozenset({"O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"})
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def bare_flag_uses(source: str) -> list[tuple[int, str]]:
    """Every direct `os.<flag>` or `from os import <flag>` in one module."""
    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "os"
    }
    uses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
            and node.attr in POSIX_ONLY_FLAGS
        ):
            uses.append((node.lineno, f"{node.value.id}.{node.attr}"))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "os":
            uses.extend(
                (node.lineno, f"from os import {alias.name}")
                for alias in node.names
                if alias.name in POSIX_ONLY_FLAGS
            )
    return sorted(uses)


def observed_ceiling(loopx_root: Path | None = None) -> dict[str, list[tuple[int, str]]]:
    root = loopx_root or LOOPX_ROOT
    observed: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(root.rglob("*.py")):
        uses = bare_flag_uses(path.read_text(encoding="utf-8"))
        if uses:
            observed[path.relative_to(REPO_ROOT).as_posix()] = uses
    return observed


def _ceiling() -> dict[str, Any]:
    payload = json.loads(CEILINGS_FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema"] == CEILINGS_SCHEMA, (
        f"{CEILINGS_FIXTURE.name} schema is {payload['schema']!r}, expected {CEILINGS_SCHEMA!r}"
    )
    assert _COMMIT_SHA.match(str(payload["baseline_commit"])), (
        "baseline_commit must be the full 40-character commit the ceilings were measured at"
    )
    ceiling = payload["ceiling"]
    assert isinstance(ceiling, dict) and ceiling, "ceiling must map module path -> positive count"
    assert all(
        isinstance(path, str) and isinstance(count, int) and count > 0
        for path, count in ceiling.items()
    ), "every ceiling entry needs a module path and a positive count"
    assert list(ceiling) == sorted(ceiling), "ceiling entries must be sorted by module path"
    return payload


def test_no_module_exceeds_its_bare_posix_flag_ceiling() -> None:
    ceiling = _ceiling()["ceiling"]
    observed = observed_ceiling()

    exceeded = {
        path: {"ceiling": ceiling.get(path, 0), "observed": uses}
        for path, uses in observed.items()
        if len(uses) > ceiling.get(path, 0)
    }
    assert not exceeded, (
        "bare POSIX-only os flags grew: "
        + json.dumps(
            {path: [f"{line}: {what}" for line, what in uses] for path, uses in exceeded.items()},
            indent=2,
        )
        + ". Read the flag with `getattr(os, \"<flag>\", 0)` the way the peer, completion "
        "and decision-capture readers do, so a host without it refuses or degrades on its "
        "own terms instead of raising AttributeError first. Raising a ceiling in "
        f"{CEILINGS_FIXTURE.name} needs a recorded reason."
    )


def test_fixed_readers_keep_no_bare_posix_flag() -> None:
    """The readers whose fallback this guard protects stay at zero."""
    observed = observed_ceiling()
    fixed = (
        "loopx/control_plane/turn_driver/codex_input_trace.py",
        "loopx/control_plane/state_recovery_io.py",
        "loopx/hosts/checkpoint_retention.py",
    )
    remaining = {path: observed[path] for path in fixed if path in observed}
    assert not remaining, (
        f"these readers spell a POSIX-only flag directly again: {remaining}. They are "
        "reachable on hosts that do not define the flag, so the open must request it "
        "with `getattr(os, ...)`."
    )


def test_scan_sees_bare_alias_and_from_import_but_not_getattr() -> None:
    source = (
        "import os\n"
        "import os as host_os\n"
        "from os import O_DIRECTORY\n"
        "def f(path):\n"
        "    first = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)\n"
        "    second = host_os.open(path, host_os.O_RDONLY | host_os.O_NOFOLLOW)\n"
        "    third = getattr(host_os, \"O_NONBLOCK\", 0)\n"
        "    return first, second, third, os.O_RDONLY\n"
    )
    assert bare_flag_uses(source) == [
        (3, "from os import O_DIRECTORY"),
        (5, "os.O_NOFOLLOW"),
        (6, "host_os.O_NOFOLLOW"),
    ]


def test_scan_ignores_a_name_that_is_not_the_os_module() -> None:
    """A local `os` binding is not the module, and a flag name alone is not a use."""
    source = (
        "class Holder:\n"
        "    O_NOFOLLOW = 1\n"
        "def f(holder):\n"
        "    flag = \"O_DIRECTORY\"\n"
        "    return holder.O_NONBLOCK, Holder.O_NOFOLLOW, flag\n"
    )
    assert bare_flag_uses(source) == []
