"""Keep release and recovery entry points usable after the organization transfer."""

from pathlib import Path
import tomllib

from loopx.install_contract import NO_CLONE_INSTALL_URL
from loopx.self_update import DEFAULT_UPDATE_REPO, _source_config


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "loopx-project/loopx"


def test_default_recovery_and_package_identity():
    assert DEFAULT_UPDATE_REPO == REPOSITORY
    assert NO_CLONE_INSTALL_URL == "https://loopx-project.github.io/loopx/install.sh"
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["urls"]["Repository"] == f"https://github.com/{REPOSITORY}"
    assert metadata["project"]["urls"]["Homepage"] == "https://loopx-project.github.io/loopx/"
    installer = (ROOT / "scripts/install-from-github.sh").read_text()
    assert f"${{LOOPX_REPO:-{REPOSITORY}}}" in installer


def test_explicit_archive_source_remains_operator_owned(monkeypatch):
    for key in ("LOOPX_REPO", "LOOPX_REF", "LOOPX_ARCHIVE_URL"):
        monkeypatch.delenv(key, raising=False)
    source = _source_config(repo="example/fork", ref="custom", archive_url=None)
    assert source["archive_url"] == "https://codeload.github.com/example/fork/tar.gz/custom"
