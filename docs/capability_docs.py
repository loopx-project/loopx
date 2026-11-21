from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
import posixpath
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from loopx.capabilities.catalog import BUILTIN_CAPABILITIES
from loopx.extensions.manifest import load_extension_manifest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_BLOB_ROOT = "https://github.com/loopx-project/loopx/blob/main"
MARKDOWN_LINK = re.compile(
    r'(?P<prefix>!?\[[^\]\n]*\]\()(?P<dest><[^>\n]+>|[^)\s\n]+)(?P<suffix>(?:\s+["\'][^)\n]*["\'])?\))'
)
HTML_LINK = re.compile(
    r'(?P<prefix>\b(?:href|src)=["\'])(?P<dest>[^"\']+)(?P<suffix>["\'])'
)


def documentation_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "source_root": "loopx/capabilities",
            "site_root": "capabilities",
            "canonical": "README.md",
            "root_only": True,
            "aliases": [],
        }
    ]
    for record in BUILTIN_CAPABILITIES:
        documentation = record.get("documentation")
        if not isinstance(documentation, Mapping):
            raise ValueError(
                f"built-in capability `{record.get('id')}` has no documentation owner"
            )
        specs.append(dict(documentation))

    for manifest_path in sorted(
        (REPOSITORY_ROOT / "loopx/extensions").glob("*/extension.toml")
    ):
        manifest = load_extension_manifest(manifest_path)
        documentation = manifest.get("documentation")
        if isinstance(documentation, Mapping):
            specs.append(dict(documentation))
    return specs


def source_pages(spec: Mapping[str, Any]) -> list[tuple[Path, PurePosixPath]]:
    source_root = REPOSITORY_ROOT / str(spec["source_root"])
    site_root = PurePosixPath(str(spec["site_root"]))
    canonical = source_root / str(spec["canonical"])
    if not canonical.is_file():
        raise ValueError(f"canonical capability documentation is missing: {canonical}")

    candidates = [canonical]
    if not spec.get("root_only"):
        candidates.extend(
            path
            for path in sorted(source_root.glob("*.md"))
            if path != canonical
        )
        docs_root = source_root / "docs"
        if docs_root.is_dir():
            candidates.extend(sorted(docs_root.rglob("*.md")))

    pages: list[tuple[Path, PurePosixPath]] = []
    for source in candidates:
        relative = source.relative_to(source_root)
        if relative.parts and relative.parts[0] == "docs":
            relative = Path(*relative.parts[1:])
        pages.append((source, site_root / PurePosixPath(relative.as_posix())))

    for alias in spec.get("aliases", []):
        if not isinstance(alias, Mapping):
            raise ValueError("documentation alias must be a mapping")
        source = source_root / str(alias["source"])
        if not source.is_file():
            raise ValueError(f"documentation alias source is missing: {source}")
        pages.append((source, PurePosixPath(str(alias["site"]))))
    return pages


def documentation_maps() -> tuple[
    dict[PurePosixPath, Path],
    dict[Path, PurePosixPath],
]:
    page_pairs: list[tuple[Path, PurePosixPath]] = []
    for spec in documentation_specs():
        page_pairs.extend(source_pages(spec))

    site_to_source: dict[PurePosixPath, Path] = {}
    source_to_site: dict[Path, PurePosixPath] = {}
    for source, site in page_pairs:
        existing_source = site_to_source.get(site)
        if existing_source is not None and existing_source != source:
            raise ValueError(
                f"documentation route `{site}` is owned by both "
                f"{existing_source} and {source}"
            )
        site_to_source[site] = source
        source_to_site.setdefault(source.resolve(), site)
    return site_to_source, source_to_site


def _render_target(
    target: str,
    *,
    source: Path,
    site: PurePosixPath,
    source_to_site: Mapping[Path, PurePosixPath],
) -> str:
    bracketed = target.startswith("<") and target.endswith(">")
    raw = target[1:-1] if bracketed else target
    split = urlsplit(raw)
    if split.scheme or split.netloc or not split.path or split.path.startswith("/"):
        return target

    resolved = (source.parent / split.path).resolve()
    target_site = source_to_site.get(resolved)
    if target_site is None:
        try:
            repository_relative = resolved.relative_to(REPOSITORY_ROOT)
        except ValueError:
            return target
        if repository_relative.parts and repository_relative.parts[0] == "docs":
            target_site = PurePosixPath(*repository_relative.parts[1:])
        elif resolved.exists():
            rendered = f"{REPOSITORY_BLOB_ROOT}/{repository_relative.as_posix()}"
            if split.query:
                rendered = f"{rendered}?{split.query}"
            if split.fragment:
                rendered = f"{rendered}#{split.fragment}"
            return f"<{rendered}>" if bracketed else rendered
        else:
            return target

    relative = posixpath.relpath(target_site.as_posix(), site.parent.as_posix())
    rendered = urlunsplit(("", "", relative, split.query, split.fragment))
    return f"<{rendered}>" if bracketed else rendered


def render_markdown(
    markdown: str,
    *,
    source: Path,
    site: PurePosixPath,
    source_to_site: Mapping[Path, PurePosixPath],
) -> str:
    def replace_markdown(match: re.Match[str]) -> str:
        return (
            match.group("prefix")
            + _render_target(
                match.group("dest"),
                source=source,
                site=site,
                source_to_site=source_to_site,
            )
            + match.group("suffix")
        )

    def replace_html(match: re.Match[str]) -> str:
        return (
            match.group("prefix")
            + _render_target(
                match.group("dest"),
                source=source,
                site=site,
                source_to_site=source_to_site,
            )
            + match.group("suffix")
        )

    return HTML_LINK.sub(replace_html, MARKDOWN_LINK.sub(replace_markdown, markdown))
