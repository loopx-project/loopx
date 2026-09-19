"""Exercise the shipped shell installer with a large GitHub commit response."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX archive installer")
def test_invalid_commit_override_precedes_temp_directory_failure(tmp_path):
    source = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOOPX_")}
    env.update(
        TMPDIR=str(tmp_path / "missing"),
        LOOPX_PYTHON=sys.executable,
        LOOPX_RESOLVED_SOURCE_GIT_COMMIT="not-a-full-sha",
    )

    result = subprocess.run(
        ["bash", str(source / "scripts/install-from-github.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert (
        "LOOPX_RESOLVED_SOURCE_GIT_COMMIT must be a full Git commit SHA"
        in result.stderr
    )
    assert "mktemp" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX archive installer")
@pytest.mark.parametrize("valid", [True, False, "nonhex"])
@pytest.mark.parametrize("ref_kind", ["branch", "sha"])
@pytest.mark.parametrize("api_mode", ["public", "authenticated", "unavailable"])
def test_commit_response_uses_file_transport_and_cleans_up(tmp_path, valid, ref_kind, api_mode):
    source = Path(__file__).resolve().parents[1]
    sha = "a" * 40
    fixture = tmp_path / "response.json"
    fixture.write_text(
        json.dumps({"sha": "z" * 40 if valid == "nonhex" else sha if valid else None,
                    "patch": "x" * 524288})
    )
    package = tmp_path / "package" / "scripts"
    package.mkdir(parents=True)
    installer = package / "install-local.sh"
    installer.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$LOOPX_RESOLVED_SOURCE_GIT_COMMIT" > "$TEST_RECEIPT"\n'
    )
    installer.chmod(0o755)
    archive = tmp_path / "package.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(package.parent, arcname="package")
    binary = tmp_path / "bin"
    binary.mkdir()
    curl = binary / "curl"
    curl.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -c "
        + shlex.quote(
            "import os,sys,shutil; "
            "args=sys.argv[1:]; "
            "is_api=any('api.github.com' in a for a in args); "
            "open(os.environ['TEST_CALLS'],'a').write('api\\n' if is_api else 'archive\\n'); "
            "sys.exit(22) if is_api and (os.environ['TEST_REF_KIND']=='sha' or os.environ['TEST_API_MODE']!='public') else None; "
            "source=os.environ['TEST_RESPONSE'] if any('api.github.com' in a for a in args) "
            "else os.environ['TEST_ARCHIVE']; "
            "target=args[args.index('-o')+1] if '-o' in args else None; "
            "shutil.copyfile(source,target) if target else sys.stdout.write(open(source).read())"
        )
        + ' "$@"\n'
    )
    curl.chmod(0o755)
    gh = binary / "gh"
    gh.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(
        "import os,sys; "
        "assert sys.argv[1:]==['api','--hostname','github.com','/repos/loopx-project/loopx/commits/stable']; "
        "open(os.environ['TEST_CALLS'],'a').write('authenticated\\n'); "
        "sys.exit(1) if os.environ['TEST_API_MODE']=='unavailable' else None; "
        "sys.stdout.write(open(os.environ['TEST_RESPONSE']).read())"
    ) + ' "$@"\n')
    gh.chmod(0o755)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    receipt = tmp_path / "receipt"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LOOPX_")}
    env.update(
        PATH=f"{binary}{os.pathsep}{env['PATH']}",
        TMPDIR=str(scratch),
        LOOPX_PYTHON=sys.executable,
        LOOPX_REF=sha if ref_kind == "sha" else "stable",
        TEST_REF_KIND=ref_kind,
        TEST_API_MODE=api_mode,
        TEST_CALLS=str(tmp_path / "calls"),
        TEST_RESPONSE=str(fixture),
        TEST_ARCHIVE=str(archive),
        TEST_RECEIPT=str(receipt),
    )
    result = subprocess.run(
        ["bash", str(source / "scripts/install-from-github.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if ref_kind == "branch" and api_mode == "unavailable":
        assert result.returncode != 0
        assert "verified full commit SHA" in result.stderr
        assert not receipt.exists()
    elif valid is True or ref_kind == "sha":
        assert result.returncode == 0, result.stderr
        assert receipt.read_text().strip() == sha
    else:
        assert result.returncode != 0
        assert "did not include a full SHA" in result.stderr
        assert not receipt.exists()
    assert list(scratch.iterdir()) == []
    calls = (tmp_path / "calls").read_text().splitlines()
    if ref_kind == "sha":
        assert calls == ["archive"]
    else:
        assert calls[0] == "api"
        assert ("authenticated" in calls) == (api_mode != "public")
