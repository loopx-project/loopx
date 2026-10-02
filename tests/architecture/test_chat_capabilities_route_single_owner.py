"""The Chat capabilities route is decided once, by the server that dispatches on it.

``/api/chat/capabilities`` is a wire identity: the server routes on it, the
dashboard launcher probes it before a server exists, and the shipped web client
fetches it. A second Python binding can drift from the first while every test
that uses its own copy keeps passing, so the census here is the only thing that
connects the three spellings.

Two copies are deliberately *not* folded into the owner. The web client is a
separate runtime whose literal is declared below rather than ignored, and
tests/examples restate the expected route on purpose - if they read it from the
owner, a wrong owner would have nothing to fail against.
"""

from __future__ import annotations

import ast
import http.client
from pathlib import Path

import pytest

from loopx import chat_server, dashboard_launcher


REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/chat_server.py"
CONSTANT_NAME = "CHAT_CAPABILITIES_PATH"
ROUTE = "/api/chat/capabilities"
# The browser build is a second runtime. Pinned by file and count so a new
# hardcoded fetch fails here instead of going unnoticed.
DECLARED_WEB_CLIENT_RESTATEMENTS = {
    "apps/presentation/dashboard/src/data/chat.ts": 1,
}


def _python_modules(root: Path) -> list[Path]:
    return sorted(path for path in (root / "loopx").rglob("*.py"))


def _web_sources(root: Path) -> list[Path]:
    web = root / "apps"
    if not web.is_dir():
        return []
    return sorted(
        path
        for path in web.rglob("*.ts")
        if "node_modules" not in path.relative_to(root).parts
    )


def _module_bindings(root: Path) -> dict[str, int]:
    """Name every module that binds the constant at module level."""
    offenders: dict[str, int] = {}
    for path in _python_modules(root):
        source = path.read_text(encoding="utf-8")
        if CONSTANT_NAME not in source:
            continue
        tree = ast.parse(source)
        lines = [
            node.lineno
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == CONSTANT_NAME
                for target in node.targets
            )
        ]
        if lines:
            offenders[path.relative_to(root).as_posix()] = len(lines)
    return offenders


def _route_literals(root: Path) -> dict[str, int]:
    """Count source files that spell the route as a string literal."""
    counts: dict[str, int] = {}
    for path in _python_modules(root) + _web_sources(root):
        source = path.read_text(encoding="utf-8")
        hits = source.count(f'"{ROUTE}"') + source.count(f"'{ROUTE}'")
        if hits:
            counts[path.relative_to(root).as_posix()] = hits
    return counts


def test_owner_module_is_the_only_python_binding_of_the_route() -> None:
    assert _module_bindings(REPO_ROOT) == {OWNER_MODULE: 1}


def test_owner_value_is_the_route_the_protocol_documents() -> None:
    assert chat_server.CHAT_CAPABILITIES_PATH == ROUTE


def test_python_route_literals_are_only_the_owner_binding() -> None:
    census = _route_literals(REPO_ROOT)
    assert census[OWNER_MODULE] == 1
    assert {k: v for k, v in census.items() if not k.startswith("apps/")} == {
        OWNER_MODULE: 1
    }


def test_web_client_restatements_are_declared_not_ignored() -> None:
    census = _route_literals(REPO_ROOT)
    assert {k: v for k, v in census.items() if k.startswith("apps/")} == (
        DECLARED_WEB_CLIENT_RESTATEMENTS
    )


def test_launcher_probes_the_owners_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe is the consumer, so it must ask for the owner's path."""
    requested: dict[str, str] = {}

    class RecordingConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def request(self, method: str, path: str, headers: object = None) -> None:
            requested["method"] = method
            requested["path"] = path

        def getresponse(self) -> object:
            class Response:
                status = 599

                def read(self, _limit: int) -> bytes:
                    return b"{}"

            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", RecordingConnection)
    assert dashboard_launcher._probe_existing_chat("127.0.0.1", 1) == "foreign"
    assert requested == {
        "method": "GET",
        "path": chat_server.CHAT_CAPABILITIES_PATH,
    }


def test_launcher_holds_no_private_copy_of_the_route() -> None:
    assert CONSTANT_NAME not in vars(dashboard_launcher)


def test_generated_inventory_no_longer_counts_the_route_as_a_fork() -> None:
    from loopx.semantics.inventory import build_inventory

    forks = build_inventory(REPO_ROOT)["duplicate_definitions"]["same_runtime_forks"]
    assert CONSTANT_NAME not in {entry["name"] for entry in forks}


@pytest.fixture
def planted_tree(tmp_path: Path) -> Path:
    owner_dir = tmp_path / "loopx"
    owner_dir.mkdir(parents=True)
    (owner_dir / "chat_server.py").write_text(
        f'CHAT_CAPABILITIES_PATH = "{ROUTE}"\n', encoding="utf-8"
    )
    (owner_dir / "launcher.py").write_text(
        f'CHAT_CAPABILITIES_PATH = "{ROUTE}"\nREQUEST = "{ROUTE}"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_census_flags_a_planted_second_owner(planted_tree: Path) -> None:
    assert _module_bindings(planted_tree) == {
        "loopx/chat_server.py": 1,
        "loopx/launcher.py": 1,
    }
    assert _route_literals(planted_tree) == {
        "loopx/chat_server.py": 1,
        "loopx/launcher.py": 2,
    }


def test_census_is_quiet_when_the_consumer_imports_the_owner(
    planted_tree: Path,
) -> None:
    (planted_tree / "loopx" / "launcher.py").write_text(
        "from .chat_server import CHAT_CAPABILITIES_PATH\n\n"
        "REQUEST = CHAT_CAPABILITIES_PATH\n",
        encoding="utf-8",
    )
    assert _module_bindings(planted_tree) == {"loopx/chat_server.py": 1}
    assert _route_literals(planted_tree) == {"loopx/chat_server.py": 1}
