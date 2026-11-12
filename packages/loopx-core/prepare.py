"""Prepare a standalone core source distribution from the single source tree.

Build the returned directory with a PEP 517 frontend. No runtime code is copied
back into the repository and the ordinary distribution remains unchanged.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
# These are distribution exclusions, not an authority or activation policy.
EXCLUDED_TREES = ("loopx/chat/", "loopx/web/", "loopx/canary/")
EXCLUDED_MODULES = {"loopx/cli.py", "loopx/chat.py"}
SOURCE_SUFFIXES = {".py", ".ts", ".json", ".mjs", ".js", ".md"}


def prepare_core_source(output: Path, *, repository: Path = REPOSITORY) -> Path:
    repository = repository.resolve()
    output = output.resolve()
    if output == repository or output.is_relative_to(repository):
        raise ValueError("prepare the core distribution outside the source checkout")
    if output.exists() and any(output.iterdir()):
        raise ValueError("the output directory must be empty")
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "-z", "--", "loopx"], cwd=repository,
    ).split(b"\0")
    sources = [Path(name.decode("utf-8")) for name in names if name]
    sources = [path for path in sources if path.suffix in SOURCE_SUFFIXES
               and path.as_posix() not in EXCLUDED_MODULES
               and not (path.parent == Path("loopx") and path.name.startswith("chat_"))
               and not path.as_posix().startswith(EXCLUDED_TREES)]
    if not sources or not (repository / "loopx/entrypoint.py").is_file():
        raise ValueError("a complete LoopX source checkout is required")
    output.mkdir(parents=True, exist_ok=True)
    for relative in sources:
        source = repository / relative
        if source.is_symlink() or not source.resolve().is_relative_to(repository):
            raise ValueError("distribution source must stay inside the checkout")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    package = repository / "packages/loopx-core"
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(package / name, output / name)
    for name in ("LICENSE", "LICENSE-MIT", "NOTICE"):
        shutil.copyfile(repository / name, output / name)
    (output / "MANIFEST.in").write_text(
        "recursive-include loopx *.py *.ts *.json *.mjs *.js *.md\n", encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(prepare_core_source(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
