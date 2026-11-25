"""A directory boundary scan reads every text file it is pointed at.

The scan used to select candidates by a seven-entry suffix allow-list
(``.md``/``.py``/``.toml``/``.json``/``.yaml``/``.yml``/``.sh``). Every other
file in a selected directory was dropped before it was opened: on this
repository that excluded the whole TypeScript control plane plus ``.mjs``,
``.tsx``, ``.html``, ``.txt``, ``.svg`` and extensionless files, while the
command still printed a clean boundary receipt. The same bytes were rejected
when the file was named directly, so one gate certified or rejected the same
content depending on the input shape. The fixtures below use a split token so
the scanner's own repository pass stays clean.
"""

from __future__ import annotations

from pathlib import Path

from loopx.contract import scan_public_boundary


def _synthetic_token() -> str:
    return "gh" + "p_" + "A" * 36


def _hit_names(payload: dict[str, list[str]]) -> list[str]:
    return [hit.split(": ", 1)[0] for hit in payload["blocking_hits"]]


def test_directory_scan_reads_text_outside_the_old_suffix_allow_list(tmp_path: Path) -> None:
    token = _synthetic_token()
    (tmp_path / "README.md").write_text("# clean\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text(f"token: {token}\n", encoding="utf-8")
    (tmp_path / "module.ts").write_text(f'const token = "{token}";\n', encoding="utf-8")
    (tmp_path / "page.html").write_text(f'<meta content="{token}">\n', encoding="utf-8")
    (tmp_path / "LICENSE-NOTES").write_text(f"token: {token}\n", encoding="utf-8")

    payload = scan_public_boundary([tmp_path])

    assert payload["ok"] is False
    assert payload["scanned_files"] == 5
    assert _hit_names(payload) == [
        "LICENSE-NOTES:1",
        "module.ts:1",
        "notes.txt:1",
        "page.html:1",
    ]


def test_explicit_file_and_its_directory_reach_the_same_verdict(tmp_path: Path) -> None:
    token = _synthetic_token()
    notes = tmp_path / "notes.txt"
    notes.write_text(f"token: {token}\n", encoding="utf-8")

    direct = scan_public_boundary([notes])
    directory = scan_public_boundary([tmp_path])

    assert direct["blocking_hits"] == ["notes.txt:1: credential"]
    assert directory["blocking_hits"] == direct["blocking_hits"]


def test_directory_scan_skips_binary_media_without_failing(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# clean\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    payload = scan_public_boundary([tmp_path])

    assert payload["ok"] is True
    assert payload["scanned_files"] == 1
    assert payload["unreadable_files"] == []


def test_undecodable_file_with_an_unknown_suffix_stays_unexamined(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# clean\n", encoding="utf-8")
    (tmp_path / "payload.unknown").write_bytes(b"\xff\xfe\x00binary")

    payload = scan_public_boundary([tmp_path])

    assert payload["ok"] is False
    assert payload["unreadable_files"] == ["payload.unknown: invalid UTF-8"]
