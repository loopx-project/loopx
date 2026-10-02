"""Keep the book's learning checks attached to real tests, without executing them.

This checks references and bilingual command parity, not implementation behavior
or the correctness of prose. The referenced tests remain the behavioral oracles.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
BOOK = REPO / "docs" / "book"
PAGES = (BOOK / "chapters/12-control-plane-course.md",
         BOOK / "en/chapters/12-control-plane-course.md")
CHECKPOINTS = ("state", "lease", "settlement", "monitor")
TEACHING_PAGES = (
    "00-reading-guide.md", "02b-long-horizon-requirements.md", "03-one-turn.md",
    "04-runtime-boundaries.md", "04b-budget-and-admission.md", "05-connect-existing-project.md",
    "work-graph-and-authority.md", "workspace-v1.md", "12-control-plane-course.md",
    "appendix-reference.md",
)
JUDGMENT_PARTS = ("facts", "reason", "evidence", "next")


def checkpoint_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    previous_end = -1
    for name in CHECKPOINTS:
        start = f"<!-- reader-checkpoint:{name}:start -->"
        end = f"<!-- reader-checkpoint:{name}:end -->"
        if markdown.count(start) != 1 or markdown.count(end) != 1:
            raise ValueError(f"{name}: expected one start and one end marker")
        start_at, end_at = markdown.index(start), markdown.index(end)
        if start_at <= previous_end or end_at < start_at:
            raise ValueError(f"{name}: checkpoint markers are out of order")
        sections[name] = markdown[start_at + len(start):end_at]
        previous_end = end_at + len(end)
    return sections


def pytest_nodes(section: str) -> tuple[str, ...]:
    nodes: list[str] = []
    for block in re.findall(r"(?ms)^```bash\n(.*?)^```[ \t]*$", section):
        # Split where the shell does, so a selector after a missing `\` is not
        # credited to the pytest command before it.
        for line in block.replace("\\\n", " ").splitlines():
            argv = shlex.split(line, comments=True)
            if not argv:
                continue
            if "pytest" not in argv:
                if argv[0].startswith("tests/"):
                    raise ValueError(f"selector is not a pytest argument: {argv[0]}")
                continue
            nodes.extend(arg for arg in argv[argv.index("pytest") + 1:]
                         if arg.startswith("tests/"))
    if not nodes:
        raise ValueError("checkpoint has no pytest test selectors")
    return tuple(nodes)


def require_test_node(root: Path, node: str) -> None:
    """Resolve only the top-level test functions used by this short workbook."""
    match = re.fullmatch(r"(tests/[A-Za-z0-9_/-]+\.py)::(test_[A-Za-z0-9_]+)", node)
    if match is None:
        raise ValueError(f"unsupported test selector: {node}")
    source, symbol = match.groups()
    path = (root / source).resolve()
    if not path.is_relative_to((root / "tests").resolve()) or not path.is_file():
        raise ValueError(f"missing or out-of-scope test source: {source}")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=source)
    functions = {item.name for item in tree.body
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if symbol not in functions:
        raise ValueError(f"missing top-level test: {node}")


def heading_anchors(markdown: str) -> set[str]:
    """Read explicit heading ids, ignoring teaching examples inside fences."""
    anchors: set[str] = set()
    fence: str | None = None
    for line in markdown.splitlines():
        stripped = line.lstrip()
        opening = re.match(r"(`{3,}|~{3,})", stripped)
        if opening:
            marker = opening.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        match = re.match(r"^#{1,6} .+ \{#([a-z0-9-]+)\}\s*$", line)
        if match:
            anchor = match.group(1)
            if anchor in anchors:
                raise ValueError(f"duplicate heading anchor: {anchor}")
            anchors.add(anchor)
    return anchors


def require_heading(markdown: str, anchor: str) -> None:
    if anchor not in heading_anchors(markdown):
        raise ValueError(f"missing heading anchor: {anchor}")


class ReaderCheckpointReferences(unittest.TestCase):
    def test_bilingual_checkpoint_commands_match(self) -> None:
        zh, en = (checkpoint_sections(page.read_text(encoding="utf-8")) for page in PAGES)
        for name in CHECKPOINTS:
            with self.subTest(checkpoint=name):
                self.assertEqual(pytest_nodes(zh[name]), pytest_nodes(en[name]))

    def test_each_documented_selector_resolves_in_current_checkout(self) -> None:
        for page in PAGES:
            for name, section in checkpoint_sections(page.read_text(encoding="utf-8")).items():
                for node in pytest_nodes(section):
                    with self.subTest(page=str(page), checkpoint=name, node=node):
                        require_test_node(REPO, node)


    def test_each_checkpoint_has_four_judgment_sections(self) -> None:
        for page in PAGES:
            for name, section in checkpoint_sections(page.read_text(encoding="utf-8")).items():
                for part in JUDGMENT_PARTS:
                    with self.subTest(page=page, checkpoint=name, part=part):
                        require_heading(section, f"{name}-{part}")

    def test_bilingual_teaching_anchors_match(self) -> None:
        for name in TEACHING_PAGES:
            zh = (BOOK / "chapters" / name).read_text(encoding="utf-8")
            en = (BOOK / "en/chapters" / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertEqual(heading_anchors(zh), heading_anchors(en))

    def test_cross_links_between_teaching_pages_resolve(self) -> None:
        for locale in ("chapters", "en/chapters"):
            directory = BOOK / locale
            for name in TEACHING_PAGES:
                markdown = (directory / name).read_text(encoding="utf-8")
                for target, fragment in re.findall(r"\]\(([^)#]*)(?:#([a-z0-9-]+))?\)", markdown):
                    target = target.removeprefix("./")
                    if target and target not in TEACHING_PAGES:
                        continue
                    path = directory / (target or name)
                    with self.subTest(page=name, target=target, fragment=fragment):
                        self.assertTrue(path.is_file(), str(path))
                        if fragment:
                            require_heading(path.read_text(encoding="utf-8"), fragment)


    def test_bilingual_fixed_source_references_match(self) -> None:
        # Matching references is a drift check, not proof of translation quality.
        pattern = r"https://github\.com/[^/]+/loopx/blob/[0-9a-f]{40}/[^)\s]+"
        for name in TEACHING_PAGES:
            zh = (BOOK / "chapters" / name).read_text(encoding="utf-8")
            en = (BOOK / "en/chapters" / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertEqual(set(re.findall(pattern, zh)), set(re.findall(pattern, en)))

    def test_all_teaching_code_fences_close(self) -> None:
        for locale in ("chapters", "en/chapters"):
            for name in TEACHING_PAGES:
                fence: str | None = None
                for line in (BOOK / locale / name).read_text(encoding="utf-8").splitlines():
                    match = re.match(r"^\s*(`{3,}|~{3,})", line)
                    if match:
                        marker = match.group(1)
                        if fence is None:
                            fence = marker
                        elif marker[0] == fence[0] and len(marker) >= len(fence):
                            fence = None
                with self.subTest(locale=locale, page=name):
                    self.assertIsNone(fence, "unclosed fenced example")


class ReaderCheckpointGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.markdown = "\n".join(
            f"<!-- reader-checkpoint:{name}:start -->\n"
            "```bash\nuv run --extra test pytest -q \\\n"
            "  tests/test_example.py::test_example\n```\n"
            f"<!-- reader-checkpoint:{name}:end -->" for name in CHECKPOINTS
        )

    def test_multiline_command_selector(self) -> None:
        sections = checkpoint_sections(self.markdown)
        self.assertEqual(pytest_nodes(sections["state"]),
                         ("tests/test_example.py::test_example",))

    def test_missing_or_duplicate_marker_is_rejected(self) -> None:
        marker = "<!-- reader-checkpoint:state:start -->"
        for malformed in (self.markdown.replace(marker, ""), self.markdown + marker):
            with self.subTest(markdown=malformed):
                with self.assertRaisesRegex(ValueError, "one start and one end"):
                    checkpoint_sections(malformed)

    def test_reordered_checkpoints_are_rejected(self) -> None:
        malformed = (self.markdown.replace("checkpoint:state:", "checkpoint:temporary:")
                     .replace("checkpoint:lease:", "checkpoint:state:")
                     .replace("checkpoint:temporary:", "checkpoint:lease:"))
        with self.assertRaisesRegex(ValueError, "out of order"):
            checkpoint_sections(malformed)

    def test_selector_after_missing_continuation_is_rejected(self) -> None:
        block = ("```bash\nuv run --extra test pytest -q \\\n"
                 "  tests/test_example.py::test_first\n"
                 "  tests/test_example.py::test_second\n```\n")
        with self.assertRaisesRegex(ValueError, "not a pytest argument"):
            pytest_nodes(block)

    def test_no_test_command_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "no pytest"):
            pytest_nodes("```text\ntests/test_example.py::test_example\n```\n")

    def test_test_body_is_not_imported_and_missing_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests/test_example.py").write_text(
                "raise RuntimeError('must not import')\n\ndef test_example():\n    pass\n",
                encoding="utf-8",
            )
            require_test_node(root, "tests/test_example.py::test_example")
            with self.assertRaisesRegex(ValueError, "missing top-level"):
                require_test_node(root, "tests/test_example.py::test_renamed")
            with self.assertRaisesRegex(ValueError, "missing or out-of-scope"):
                require_test_node(root, "tests/test_missing.py::test_example")

    def test_unsupported_selector_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported test selector"):
            require_test_node(REPO, "tests/../private.py::test_example")


    def test_code_examples_do_not_define_heading_anchors(self) -> None:
        text = "```markdown\n## Example {#not-a-heading}\n```\n## Actual {#actual}\n"
        self.assertEqual(heading_anchors(text), {"actual"})

    def test_duplicate_heading_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate heading"):
            heading_anchors("## A {#same}\n## B {#same}\n")

    def test_missing_heading_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing heading"):
            require_heading("## A {#present}\n", "missing")


@unittest.skipUnless(shutil.which("git"), "Git is required for the isolated onboarding example")
class GitOnboardingEvidence(unittest.TestCase):
    def test_ignore_match_does_not_remove_an_index_entry(self) -> None:
        """Exercise the documented distinction, never a user's worktree."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("GIT_")}
            environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                "GIT_CONFIG_SYSTEM": os.devnull})

            def git(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(["git", "-C", str(root), *args],
                                      capture_output=True, text=True, timeout=15, check=False,
                                      env=environment)

            self.assertEqual(git("init", "-q").returncode, 0)
            (root / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
            path = ".loopx/registry.json"
            # Ignore matching also works before the path exists.
            self.assertEqual(git("check-ignore", "-v", path).returncode, 0)
            (root / ".loopx").mkdir()
            (root / path).write_text("{}\n", encoding="utf-8")
            self.assertEqual(git("add", "-f", "--", path).returncode, 0)
            self.assertEqual(git("check-ignore", "-v", path).returncode, 1)
            self.assertEqual(git("check-ignore", "-v", "--no-index", path).returncode, 0)
            self.assertEqual(git("ls-files", "--", ".loopx").stdout.strip(), path)
            # No commit is made: an index entry does not prove publication.
            self.assertNotEqual(git("rev-parse", "--verify", "HEAD").returncode, 0)


if __name__ == "__main__":
    unittest.main()
