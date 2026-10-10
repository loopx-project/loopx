from __future__ import annotations

from itertools import product
from pathlib import Path
import subprocess
from typing import Any

import pytest

from loopx import contract


@pytest.mark.parametrize("tracked,pruned", [(False, False), (True, False), (True, True)])
def test_scan_enumeration_uses_resolved_symlink_suffix_and_local_name(
    tmp_path: Path, tracked: bool, pruned: bool
) -> None:
    scan_root = tmp_path / ("node_modules" if pruned else "public")
    scan_root.mkdir()
    target = tmp_path / "target.md"
    target.write_text("public", encoding="utf-8")
    distinct = tmp_path / "distinct.md"
    distinct.write_text("public", encoding="utf-8")
    unsupported = tmp_path / "target.ts"
    unsupported.write_text("not a directory scan input", encoding="utf-8")
    local = tmp_path / "target.local.json"
    local.write_text("local", encoding="utf-8")
    regular = scan_root / "regular.md"
    regular.write_text("public", encoding="utf-8")
    (scan_root / "regular.ts").write_text("unsupported", encoding="utf-8")
    (scan_root / "alias.ts").symlink_to(target)
    (scan_root / "distinct.txt").symlink_to(distinct)
    (scan_root / "alias.local.json").symlink_to(target)
    (scan_root / "unsupported.md").symlink_to(unsupported)
    (scan_root / "local.md").symlink_to(local)
    (scan_root / "broken.ts").symlink_to(tmp_path / "absent.md")
    if tracked:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "--", scan_root.name], check=True)

    # Git ownership includes tracked files under otherwise pruned directories.
    # Directory eligibility follows the canonical target, not the alias name.
    # The pruned-root fast path preserves Git's per-alias observations.
    expected = [distinct, regular, target, target] if pruned else [distinct, regular, target]
    assert contract.iter_scan_files(scan_root) == sorted(expected)
    if tracked:
        assert set(contract._tracked_scan_files(scan_root)) == {distinct, regular, target}

    # A later retarget must be observed; no eligibility result may be cached.
    (scan_root / "alias.ts").unlink()
    (scan_root / "alias.ts").symlink_to(local)
    (scan_root / "alias.local.json").unlink()
    assert contract.iter_scan_files(scan_root) == sorted([distinct, regular])


def test_explicit_scan_file_keeps_unsupported_suffix(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.ts"
    explicit.write_text("explicit input", encoding="utf-8")
    assert contract.iter_scan_files(explicit) == [explicit]


@pytest.mark.parametrize(
    ("rule_name", "line"),
    [
        ("private_doc_url", "https://tenant.lark" + "office.com/wiki/example"),
        ("private_doc_url", "https://docs" + ".internal/example"),
        ("credential", "Bear" + "er literal"),
        ("credential", "AK" + "IA1234567890ABCDEF"),
        ("credential", "tok" + "en=literal"),
        ("credential", "pass" + "word=literal"),
        ("credential", "Author" + "ization: literal"),
        ("local_private_path", "/" + "Users/alice/Documents/example.md"),
        ("local_private_path", "/" + "Users/alice/code-reading/example.md"),
        ("local_private_path", "/ext" + "_data/example.md"),
        ("local_private_path", "\\" + "Users\\alice\\Documents\\example.md"),
        ("local_private_path", "\\" + "Users\\alice\\code-reading\\example.md"),
        ("local_private_path", "\\ext" + "_data\\example.md"),
        # Either separator at each junction: a Windows host mixes them when
        # it appends a POSIX-style segment to a native home directory, and a
        # serialized document double-escapes each backslash.
        ("local_private_path", "\\" + "Users/alice/Documents/example.md"),
        ("local_private_path", "/Users" + "\\alice\\Documents\\example.md"),
        ("local_private_path", "C:" + "\\\\" + "Users" + "\\\\" + "alice" + "\\\\" + "Documents" + "\\\\" + "example.md"),
        ("internal_task_id", "ticket t-" + "20260828123456-example"),
        ("private_ip", "host 10" + ".1.2.3"),
        ("private_ip", "host 172" + ".31.2.3"),
        ("private_ip", "host 192" + ".168.2.3"),
    ],
)
def test_every_authoritative_leak_pattern_has_a_matching_prefilter(
    rule_name: str,
    line: str,
) -> None:
    rule = contract.LEAK_RULES[rule_name]

    assert rule.pattern.search(line)
    assert rule.is_candidate(contract._prefilter_fold(line))


def test_prefilter_is_a_necessary_condition_for_every_separator_spelling() -> None:
    # The prefilter may be looser than the pattern but never stricter: every
    # root spelling the authoritative pattern classifies, whatever separator
    # or escape run the host or serializer chose, must remain a candidate.
    rule = contract.LEAK_RULES["local_private_path"]
    separators = ("/", "\\", "//", "\\\\")
    for outer, inner in product(separators, repeat=2):
        line = f"C:{outer}Users{inner}alice{inner}Documents{inner}report.md"
        assert rule.pattern.search(line), line
        assert rule.is_candidate(contract._prefilter_fold(line)), line
    for separator in separators:
        line = f"Mounted at {separator}ext_data{separator}report.md"
        assert rule.pattern.search(line), line
        assert rule.is_candidate(contract._prefilter_fold(line)), line


@pytest.mark.parametrize(
    ("rule_name", "line"),
    [
        ("private_doc_url", "https://tenant.larkoff\u0130ce.com/wiki/example"),
        ("credential", "Author\u0131zation: literal"),
        ("credential", "pa\u017f\u017fword=literal"),
        ("credential", "A\u212aIA1234567890ABCDEF"),
    ],
)
def test_unicode_ignorecase_matches_are_never_filtered_out(
    rule_name: str, line: str, tmp_path: Path
) -> None:
    rule = contract.LEAK_RULES[rule_name]
    assert rule.pattern.search(line)
    assert rule.is_candidate(contract._prefilter_fold(line))

    (tmp_path / "sample.md").write_text(line + "\n", encoding="utf-8")
    payload = contract.scan_public_boundary([tmp_path])
    assert f"sample.md:1: {rule_name}" in payload["hits"]


def test_prefilter_only_runs_regex_for_candidate_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingPattern:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def search(self, line: str) -> None:
            self.lines.append(line)

    pattern = RecordingPattern()
    monkeypatch.setattr(
        contract,
        "LEAK_RULES",
        {
            "private_ip": contract.LeakRule(
                pattern=pattern,  # type: ignore[arg-type]
                required_literals=("candidate",),
            )
        },
    )
    (tmp_path / "sample.md").write_text(
        "ordinary public line\ncandidate without a regex hit\nanother ordinary line\n",
        encoding="utf-8",
    )

    payload = contract.scan_public_boundary([tmp_path])

    assert payload["ok"] is True
    assert pattern.lines == ["candidate without a regex hit"]


def test_prefilter_preserves_all_boundary_hit_categories(tmp_path: Path) -> None:
    lines = [
        "https://tenant.lark" + "office.com/wiki/example",
        "tok" + "en=literal",
        "/" + "Users/alice/Documents/example.md",
        "ticket t-" + "20260828123456-example",
        "host 10" + ".1.2.3",
    ]
    (tmp_path / "sample.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload: dict[str, Any] = contract.scan_public_boundary([tmp_path])

    assert payload["hits"] == [
        "sample.md:1: private_doc_url",
        "sample.md:2: credential",
        "sample.md:3: local_private_path",
        "sample.md:4: internal_task_id",
        "sample.md:5: private_ip",
    ]


@pytest.mark.parametrize(
    "separator",
    [
        "\n",
        "\r",
        "\r\n",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        "\u2028",
        "\u2029",
    ],
)
def test_prefilter_preserves_unicode_line_numbers_and_multiple_rules(
    tmp_path: Path, separator: str
) -> None:
    # The folded necessary condition cannot change original line coordinates,
    # rule order, the public-host exception, or credential-reference handling.
    lines = [
        "ordinary " * 50,
        "Author\u0131zation: literal host 10" + ".1.2.3",
        "pa\u017f\u017fword=${EXAMPLE_KEY}",
        "https://open.lark" + "office.com host 172" + ".31.2.3",
        "pa\u00dfword=literal",  # casefold expands; the authoritative regex refuses it
        "ordinary again",
    ]
    (tmp_path / "sample.md").write_text(separator.join(lines), encoding="utf-8")
    payload = contract.scan_public_boundary([tmp_path])
    assert payload["hits"] == [
        "sample.md:2: credential",
        "sample.md:2: private_ip",
        "sample.md:4: private_ip",
    ]
    assert payload["credential_reference_hits"] == ["sample.md:3: credential"]


def test_prefilter_literals_are_substrings_not_regex_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RecordingPattern:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def search(self, line: str) -> None:
            self.lines.append(line)

    pattern = RecordingPattern()
    monkeypatch.setattr(
        contract,
        "LEAK_RULES",
        {
            "private_ip": contract.LeakRule(
                pattern=pattern,  # type: ignore[arg-type]
                required_literals=("a|b", "10."),
            ),
        },
    )
    lines = ["a or b", "10x", "actual a|b", "actual 10.", "ordinary"]
    (tmp_path / "sample.md").write_text("\n".join(lines), encoding="utf-8")
    assert contract.scan_public_boundary([tmp_path])["ok"] is True
    assert pattern.lines == ["actual a|b", "actual 10."]


@pytest.mark.parametrize(
    "line",
    [
        "See C:" + "\\" + "Users\\alice\\Documents\\report.md",
        "See C:" + "\\" + "Users\\alice\\code-reading\\report.md",
        "Mounted at " + "\\ext" + "_data\\report.md",
        "See C:" + "\\" + "Users/alice/Documents/report.md",
        'json: {"path": "C:' + "\\\\" + 'Users\\\\alice\\\\Documents\\\\report.md"}',
    ],
)
def test_native_windows_private_paths_are_blocked(line: str, tmp_path: Path) -> None:
    # The same private path class must be blocked when a Windows host spells it
    # with the native separator, not only in its POSIX form.
    (tmp_path / "sample.md").write_text(line + "\n", encoding="utf-8")

    payload = contract.scan_public_boundary([tmp_path])

    assert payload["ok"] is False
    assert payload["hits"] == ["sample.md:1: local_private_path"]


def test_windows_paths_outside_the_private_roots_stay_public(tmp_path: Path) -> None:
    # Accepting the Windows separator must not widen the private roots the rule
    # names, so an unrelated profile subdirectory stays public.
    line = "Cache at C:" + "\\" + "Users\\alice\\AppData\\Local\\Temp\\report.md"
    (tmp_path / "sample.md").write_text(line + "\n", encoding="utf-8")

    assert contract.scan_public_boundary([tmp_path])["ok"] is True
