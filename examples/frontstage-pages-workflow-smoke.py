#!/usr/bin/env python3
"""Smoke-test the public-safe GitHub Pages frontstage workflow source."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "frontstage-pages.yml"


def assert_contains(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing workflow contract: {needle}")


def assert_absent(text: str, needle: str) -> None:
    if needle in text:
        raise AssertionError(f"workflow must not reference {needle!r}")


def _extract_paths_block(text: str, event: str) -> str:
    """Extract the paths list under a given event (pull_request or push)."""
    marker = f"\n  {event}:"
    idx = text.find(marker)
    if idx == -1:
        return ""
    rest = text[idx + len(marker):]
    paths_marker = "\n    paths:"
    pi = rest.find(paths_marker)
    if pi == -1:
        return ""
    block = rest[pi + len(paths_marker):]
    # Stop at the next top-level key (indented with exactly 2 spaces, not 6)
    lines = block.split("\n")
    collected = []
    for line in lines:
        if line and not line.startswith("      ") and not line.startswith("        "):
            break
        collected.append(line)
    return "\n".join(collected)


def assert_pr_and_push_trigger(trigger_text: str, path: str) -> None:
    marker = f'      - "{path}"'
    pr_block = _extract_paths_block(trigger_text, "pull_request")
    push_block = _extract_paths_block(trigger_text, "push")
    pr_count = pr_block.count(marker)
    push_count = push_block.count(marker)
    if pr_count != 1:
        raise AssertionError(
            f"workflow must trigger pull_request exactly once for {path!r}; found {pr_count}"
        )
    if push_count != 1:
        raise AssertionError(
            f"workflow must trigger push exactly once for {path!r}; found {push_count}"
        )


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_text = text.split("\npermissions:", 1)[0]

    for needle in [
        "workflow_dispatch:",
        "schedule:",
        'cron: "37 */6 * * *"',
        "pull_request:",
        "branches:",
        "actions: read",
        "contents: read",
        "pages: write",
        "id-token: write",
        "github.event_name == 'pull_request'",
        "github.event.pull_request.number",
        "|| 'deployment'",
        "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
        'node-version: "24"',
        "npm install -g npm@11",
        "npm ci --include=dev --no-audit --no-fund --registry=https://registry.npmjs.org",
        "./node_modules/.bin/playwright install --with-deps chromium",
        "node examples/blog-bilingual-index-smoke.mjs",
        "node apps/presentation/site/scripts/analytics-smoke.mjs output/frontstage-pages/site",
        "node apps/presentation/site/scripts/seo-smoke.mjs output/frontstage-pages/site --with-docs",
        'LOOPX_PUBLIC_SITE_DIR="$PWD/output/frontstage-pages/site" npm --prefix apps/presentation/dashboard run smoke:frontstage-browser',
        "docs/showcases/**",
        "docs/assets/long-running-loop-openviking-trajectory.png",
        "docs/assets/long-running-loop-ml-experiment-trajectory.png",
        "apps/presentation/site/**",
        "examples/showcase-catalog-smoke.py",
        "python3 examples/showcase-catalog-smoke.py",
        "examples/readme-star-history-smoke.py",
        "python3 examples/readme-star-history-smoke.py",
        "examples/frontstage-stargazer-fetch-smoke.py",
        "python3 examples/frontstage-stargazer-fetch-smoke.py",
        "scripts/fetch-github-stargazers.sh",
        "scripts/render-star-history.py",
        "GH_TOKEN: ${{ secrets.STAR_HISTORY_READ_TOKEN }}",
        '"$GITHUB_REPOSITORY"',
        '"$RUNNER_TEMP/loopx-stargazers.json"',
        '"$RUNNER_TEMP/loopx-stargazers-count"',
        "--expected-count",
        "output/frontstage-pages/site/site-assets/star-history.svg",
        "python3 examples/dev-book-publication-smoke.py",
        "python3 examples/dev-book-publication-smoke.py --site-dir output/frontstage-pages/site/docs/book",
        "examples/dev-book-welcome-wagon-smoke.py",
        "python3 examples/dev-book-welcome-wagon-smoke.py --site-dir output/frontstage-pages/site/docs/book",
        "npm run smoke:frontstage-share-bundle",
        "examples/dev-book-browser-smoke.mjs",
        "node examples/dev-book-browser-smoke.mjs",
        "node examples/export-frontstage-share-bundle.mjs --restore-case-pages --out-dir output/frontstage-pages",
        "npm run export:frontstage-share -- --base /loopx/ --out-dir ../../../output/frontstage-pages",
        "mkdocs build --strict --site-dir output/frontstage-pages/site/docs",
        "mkdocs build --strict --config-file docs/book/mkdocs.zh.yaml --site-dir ../../output/frontstage-pages/site/docs/book",
        "mkdocs build --strict --config-file docs/book/mkdocs.en.yaml --site-dir ../../output/frontstage-pages/site/docs/book/en",
        "actions/configure-pages@v6",
        "enablement: true",
        "actions/upload-pages-artifact@v5",
        "path: output/frontstage-pages/site",
        "actions/deploy-pages@v5",
        "if: github.event_name != 'pull_request'",
    ]:
        assert_contains(text, needle)

    for forbidden in [
        "serve-status",
        "status.local.json",
        ".codex/goals",
        ".goal-" + "harness/",
        "registry.global.json",
        "GH_TOKEN: ${{ github.token }}",
        "enable-reward-write-api",
        "npm run dev",
        "npm run preview",
        "stargazerCount",
        "gh api graphql",
        "pageInfo { hasNextPage endCursor }",
        "\n  group: frontstage-pages\n",
        "npm run build:chat",
        "smoke:personal-workspace-packaged",
        "frontstage-chat-bundle-",
        "path: loopx/web/chat/",
    ]:
        assert_absent(text, forbidden)

    # Detaching a non-published product must preserve its own qualification lane.
    python_tests = (WORKFLOW.parent / "python-tests.yml").read_text(encoding="utf-8")
    for required in [
        "npm run build:chat",
        "scripts/chat_bundle.py verify --source",
        "npm run smoke:personal-workspace-packaged",
        "npm run smoke:chat-turn-acceptance-retry",
        "npm run smoke:chat-upgrade",
    ]:
        assert_contains(python_tests, required)

    for path in [
        "docs/book/mkdocs.zh.yaml",
        "docs/book/mkdocs.en.yaml",
        "examples/dev-book-publication-smoke.py",
        "examples/dev-book-welcome-wagon-smoke.py",
        "examples/dev-book-browser-smoke.mjs",
    ]:
        assert_pr_and_push_trigger(trigger_text, path)

    print("frontstage-pages-workflow-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
