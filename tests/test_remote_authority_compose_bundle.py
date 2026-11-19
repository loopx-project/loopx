"""The public self-hosting artifact preserves provenance without runtime secrets."""

import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "examples/coordination/remote-authority-compose/build_bundle.py"


def build(output: Path, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUILDER), "--source-revision", revision, "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )


def test_bundle_is_reproducible_and_preserves_exact_source_and_license(tmp_path: Path) -> None:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    assert build(first, revision).returncode == 0
    assert build(second, revision).returncode == 0
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        prefix = "loopx-remote-authority-compose/"
        for name in ("LICENSE", "NOTICE"):
            assert archive.extractfile(prefix + name).read() == subprocess.check_output(
                ["git", "show", f"{revision}:{name}"], cwd=ROOT,
            )
        metadata = dict(line.split("=", 1) for line in archive.extractfile(prefix + ".env").read().decode().splitlines())
        source = archive.extractfile(prefix + "loopx-source.tar.gz").read()
        assert metadata["LOOPX_REVISION"] == revision
        assert hashlib.sha256(source).hexdigest() == metadata["LOOPX_SOURCE_SHA256"]
        with tarfile.open(fileobj=io.BytesIO(source)) as code:
            path = "loopx/control_plane/coordination/remote_authority_policy.ts"
            assert code.extractfile(path).read() == subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT)
        assert archive.getmember(prefix + "start.sh").mode == 0o755
        assert all(member.isfile() and not member.name.startswith("/") and ".." not in Path(member.name).parts
                   for member in archive.getmembers())
        assert not any(Path(member.name).name in {"credential", "policy.json", "key.pem", "generation.json"}
                       for member in archive.getmembers())


def test_builder_refuses_bad_revision_and_preserves_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "bundle.tar.gz"
    assert build(output, "main").returncode != 0
    assert not output.exists()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    output.write_bytes(b"existing artifact")
    assert build(output, revision).returncode != 0
    assert output.read_bytes() == b"existing artifact"
