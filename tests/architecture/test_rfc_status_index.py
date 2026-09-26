"""Real-file CLI regression coverage for merged RFC acceptance."""

from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path
import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = (
        Path(__file__).resolve().parents[2] / "scripts/generate_rfc_status_index.py"
    )
    shutil.copyfile(source, scripts / source.name)
    rfcs = tmp_path / "docs/architecture/rfcs"
    rfcs.mkdir(parents=True)
    (rfcs / "README.md").write_text("- [Alpha](alpha.md)\n- [Beta](beta.md)\n")
    for name in ("alpha", "beta"):
        document(rfcs, name)
    assert run(tmp_path, "--write").returncode == 0
    return tmp_path


def document(
    rfcs: Path,
    name: str,
    *,
    state: str = "Accepted",
    closes: str = "none",
    successor: str | None = None,
    body: str = "",
    zh: bool = False,
) -> None:
    header = f"# RFC: {name}\n\n- **RFC status:** {state}\n"
    header += f"- **Supersedes / closes:** {closes}\n"
    if successor is not None:
        header += f"- **Superseded by:** {successor}\n"
    if zh:
        header = (
            header.replace("RFC status:", "RFC 状态：")
            .replace("Supersedes / closes:", "替代 / 关闭：")
            .replace("Superseded by:", "被替代为：")
        )
    (rfcs / f"{name}{'.zh-CN' if zh else ''}.md").write_text(header + "\n" + body)


def run(repo: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "scripts/generate_rfc_status_index.py"), mode],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
    )


@pytest.mark.parametrize(
    "state",
    [
        "Draft",
        "Under review",
        "Drafting notes",
        "Acceptedness",
        "Accepted notes",
        "",
        "Unknown",
    ],
)
def test_invalid_lifecycle_cannot_write_an_index(repo: Path, state: str) -> None:
    rfcs = repo / "docs/architecture/rfcs"
    before = {p.name: p.read_bytes() for p in rfcs.glob("STATUS*.md")}
    document(rfcs, "alpha", state=state)
    assert run(repo, "--write").returncode == 1
    assert run(repo, "--check").returncode == 1
    assert {p.name: p.read_bytes() for p in rfcs.glob("STATUS*.md")} == before


@pytest.mark.parametrize(
    "state",
    [
        "Accepted",
        "Accepted; partial implementation",
        "Accepted (qualification pending)",
        "已接受（实现待验收）",
    ],
)
def test_exact_lifecycle_with_punctuated_note_is_accepted(
    repo: Path, state: str
) -> None:
    document(repo / "docs/architecture/rfcs", "alpha", state=state)
    assert run(repo, "--write").returncode == 0
    assert run(repo, "--check").returncode == 0


def test_header_only_transition_regenerates_without_readme_state_edit(
    repo: Path,
) -> None:
    rfcs = repo / "docs/architecture/rfcs"
    before = (rfcs / "README.md").read_bytes()
    document(rfcs, "alpha", state="Retired")
    assert run(repo, "--check").returncode == 1
    assert run(repo, "--write").returncode == 0
    assert run(repo, "--check").returncode == 0
    assert (rfcs / "README.md").read_bytes() == before


def test_chinese_lifecycle_must_match_canonical_header(repo: Path) -> None:
    rfcs = repo / "docs/architecture/rfcs"
    document(rfcs, "alpha", state="Rejected", zh=True)
    assert run(repo, "--write").returncode == 1
    document(rfcs, "alpha", state="已接受", zh=True)
    assert run(repo, "--write").returncode == 0
