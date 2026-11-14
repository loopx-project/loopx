"""The review artifact must contain both the new note and archive change."""

import os
from pathlib import Path
import subprocess
import textwrap


def test_update_note_patch_applies_new_note_and_archive(tmp_path: Path) -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/update-notes.yml").read_text()
    step = workflow.split("- name: Package reviewable draft", 1)[1].split(
        "- name: Upload update-note draft", 1
    )[0]
    script = textwrap.dedent(step.split("run: |\n", 1)[1])
    repository = tmp_path / "source"
    repository.mkdir()

    def git(*args: str, cwd: Path = repository) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    notes = repository / "docs/update-notes"
    notes.mkdir(parents=True)
    (notes / "README.md").write_text("old archive\n")
    git("add", "docs/update-notes/README.md")
    git("commit", "-qm", "initial")
    clean = tmp_path / "clean"
    git("clone", "-q", str(repository), str(clean))
    note = "docs/update-notes/2026-01-01-to-2026-01-14.md"
    (repository / note).write_text("new article\n")
    (notes / "README.md").write_text("new archive\n")
    subprocess.run(["bash", "-e", "-c", script], cwd=repository, check=True,
                   env={**os.environ, "NOTE_FILE": note})
    git("apply", str(repository / "update-note.patch"), cwd=clean)
    assert (clean / note).read_text() == "new article\n"
    assert (clean / "docs/update-notes/README.md").read_text() == "new archive\n"
