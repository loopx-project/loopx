from __future__ import annotations

from loopx.control_plane.coordination.configuration import (
    fold_write_scope_subpaths,
    normalize_goal_write_scope,
)


def test_goal_write_scope_normalization_preserves_configuration_semantics() -> None:
    assert normalize_goal_write_scope(None) is None
    assert normalize_goal_write_scope([]) == []
    assert normalize_goal_write_scope(
        [" docs/**, tests/** ", "docs/**", "src/**;generated/**", ""]
    ) == ["docs/**", "tests/**", "src/**;generated/**"]


def test_fold_write_scope_subpaths_collapses_redundancy_and_preserves_boundaries() -> None:
    scopes = [
        "**",
        "src/**",
        "src/sub/file.ts",
        "/repos/example/loopx/docs/**",
        "/repos/other/**",
        "/repos/other/sub/**",
        "~/.ssh/**",
        "config/.aws/**",
        "deploy/secrets/token.pem",
    ]
    assert fold_write_scope_subpaths(scopes, goal_repo="/repos/example/loopx") == [
        "**",
        "/repos/other/**",
        "~/.ssh/**",
        "config/.aws/**",
        "deploy/secrets/token.pem",
    ]
    assert fold_write_scope_subpaths(
        ["src/**", "src/a/b.ts", "src/sub/**", "docs/*.md", "docs/**"],
    ) == ["src/**", "docs/**"]
    # Exact directory grants ("plans") must survive alongside descendant globs
    # ("plans/**") because the normalized matcher requires a slash for "plans/**".
    assert fold_write_scope_subpaths(["plans", "plans/**"]) == ["plans", "plans/**"]
    assert fold_write_scope_subpaths(["plans", "plans/", "plans/**", "plans/draft.md"]) == [
        "plans",
        "plans/**",
    ]
    assert fold_write_scope_subpaths(
        ["/repos/other", "/repos/other/**", "/repos/other/sub/**"]
    ) == ["/repos/other", "/repos/other/**"]
    # Bare repo root without trailing `/**` does not fold relative directory globs.
    assert fold_write_scope_subpaths(
        ["/repos/example/loopx", "docs/**"],
        goal_repo="/repos/example/loopx",
    ) == ["/repos/example/loopx", "docs/**"]
