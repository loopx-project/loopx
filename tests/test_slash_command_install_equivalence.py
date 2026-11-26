"""Paired behavioral equivalence tests comparing origin/main baseline vs HEAD."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import subprocess
from types import ModuleType
from typing import cast

import pytest

from loopx import slash_command_install as head_mod

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "e5d5945f6dfa7bca340c7f9473432d9f1ba6b1f6"
TARGET_RELPATH = "loopx/slash_command_install.py"


def _load_baseline_module(
    *,
    commit: str = BASELINE_COMMIT,
    relpath: str = TARGET_RELPATH,
    source_override: str | None = None,
) -> ModuleType:
    """Load `loopx/slash_command_install.py` from the pinned git commit, failing loudly."""
    if source_override is None:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relpath}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            stderr = result.stderr.strip() or "empty git show output"
            raise RuntimeError(
                f"Failed to load baseline {relpath} from commit {commit}: {stderr}"
            )
        source = result.stdout
    else:
        source = source_override

    module = ModuleType("loopx._baseline_slash_command_install")
    module.__file__ = str(REPOSITORY_ROOT / relpath)
    module.__package__ = "loopx"
    # Historical commit e5d5945f6 imported kiro_cli_goal_mode, opencode_goal_mode,
    # pi_goal_mode, zcode_goal_mode, and claude_goal_mode before those packages
    # moved under loopx/hosts/; agy_goal_mode continues resolving via its
    # compatibility shim.
    compiled_source = (
        source.replace("from .kiro_cli_goal_mode", "from .hosts.kiro_cli")
        .replace("from .opencode_goal_mode", "from .hosts.opencode")
        .replace("from .pi_goal_mode", "from .hosts.pi")
        .replace("from .zcode_goal_mode", "from .hosts.zcode")
        .replace("from loopx.claude_goal_mode", "from loopx.hosts.claude")
        .replace("from .claude_goal_mode", "from .hosts.claude")
        if source_override is None
        else source
    )
    try:
        code = compile(compiled_source, f"{commit}:{relpath}", "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to execute baseline {relpath} from commit {commit}: {exc}"
        ) from exc
    return module


_BASELINE_MOD: ModuleType | None = None


def _paired_modules() -> tuple[ModuleType, ModuleType]:
    global _BASELINE_MOD
    if _BASELINE_MOD is None:
        _BASELINE_MOD = _load_baseline_module()
    assert _BASELINE_MOD is not head_mod
    return _BASELINE_MOD, head_mod


def _run_paired(
    fn: Callable[[ModuleType, int], object],
) -> tuple[object, object]:
    modules = _paired_modules()
    assert len(modules) == 2
    outputs = [fn(mod, idx) for idx, mod in enumerate(modules)]
    assert len(outputs) == 2
    return outputs[0], outputs[1]


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_missing_or_unreadable_baseline_fails_explicitly() -> None:
    with pytest.raises(RuntimeError, match="Failed to load baseline"):
        _load_baseline_module(commit="0000000000000000000000000000000000000000")

    with pytest.raises(RuntimeError, match="Failed to load baseline"):
        _load_baseline_module(relpath="loopx/does_not_exist_slash_command.py")

    with pytest.raises(RuntimeError, match="Failed to execute baseline"):
        _load_baseline_module(source_override="def broken(:\n")


def test_exports_constants_and_surface_normalization_equivalence() -> None:
    def inspect_exports(mod: ModuleType, _idx: int) -> object:
        surface_cases: list[list[str] | None] = [
            None,
            [],
            ["all"],
            ["codex-cli", "codex-app", "codex-ide-plugin", "codex"],
            [
                "gemini-cli",
                "cursor-agent",
                "z-code",
                "antigravity",
                "agy-app",
                "kiro",
                "pi",
            ],
            ["unknown-custom-surface", "codex", "unknown-custom-surface"],
        ]
        return {
            "constants": {
                "SCHEMA_VERSION": mod.SCHEMA_VERSION,
                "OPENCODE_GOAL_DEPENDENCIES": mod.OPENCODE_GOAL_DEPENDENCIES,
                "MANAGED_MCP_KEY": mod.MANAGED_MCP_KEY,
                "CURSOR_MCP_MARKER_NAME": mod.CURSOR_MCP_MARKER_NAME,
                "CURSOR_MCP_MARKER_SCHEMA_VERSION": mod.CURSOR_MCP_MARKER_SCHEMA_VERSION,
                "KIRO_CLI_MCP_MARKER_SCHEMA_VERSION": mod.KIRO_CLI_MCP_MARKER_SCHEMA_VERSION,
                "ANTIGRAVITY_MCP_MARKER_SCHEMA_VERSION": mod.ANTIGRAVITY_MCP_MARKER_SCHEMA_VERSION,
                "ANTIGRAVITY_HOOKS_MARKER_NAME": mod.ANTIGRAVITY_HOOKS_MARKER_NAME,
                "ANTIGRAVITY_HOOKS_MARKER_SCHEMA_VERSION": mod.ANTIGRAVITY_HOOKS_MARKER_SCHEMA_VERSION,
                "readback_enum": [status.value for status in mod.SkillFacadeReadbackStatus],
            },
            "normalized": [mod._normalize_surfaces(case) for case in surface_cases],
        }

    baseline_out, head_out = _run_paired(inspect_exports)
    assert baseline_out == head_out


def test_host_command_and_entry_skill_rendering_equivalence(tmp_path: Path) -> None:
    def render_all(mod: ModuleType, idx: int) -> object:
        root = tmp_path / f"variant_{idx}"
        prompts = {
            f"{aliases}_{host}": mod._command_prompt_specs(
                cli_bin="custom-loopx",
                include_legacy_aliases=aliases,
                host_surface=host,
            )
            for aliases in (False, True)
            for host in (None, "codex-app", "agy", "antigravity-app")
        }
        canonical = prompts["False_None"]
        skill_renders = [
            (
                mod._command_skill_content(spec, surface=surface),
                mod._opencode_command_body(spec),
            )
            for spec in canonical
            for surface in ("codex-skills", "claude-skills")
        ]
        meta = mod._openai_skill_metadata(
            command="/loopx",
            display_name="LoopX",
            short_description="Inspect LoopX state",
        )
        entry_results: list[object] = []
        for host in (
            None,
            "ark-managed-agent",
            "deepseek-harness-native",
            "agy",
            "antigravity-app",
        ):
            skills_dir = root / f"skills_{host or 'default'}"
            dry = mod.materialize_loopx_entry_skill(
                skills_dir=skills_dir,
                execute=False,
                cli_bin="custom-loopx",
                host_surface=host,
            )
            created = mod.materialize_loopx_entry_skill(
                skills_dir=skills_dir,
                execute=True,
                cli_bin="custom-loopx",
                host_surface=host,
            )
            unchanged = mod.materialize_loopx_entry_skill(
                skills_dir=skills_dir,
                execute=True,
                cli_bin="custom-loopx",
                host_surface=host,
            )
            entry_results.append(
                [
                    {
                        k: v.replace(str(root), "<ROOT>")
                        for k, v in row.items()
                    }
                    for row in (dry, created, unchanged)
                ]
            )
        user_dir = root / "user_owned"
        user_skill = user_dir / "loopx" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("# User skill\n", encoding="utf-8")
        preserved = mod.materialize_loopx_entry_skill(
            skills_dir=user_dir, execute=True
        )
        with pytest.raises(ValueError, match="unsupported fixed LoopX entry host surface"):
            mod.materialize_loopx_entry_skill(
                skills_dir=user_dir,
                execute=False,
                host_surface="bad-surface",
            )
        return {
            "prompts": prompts,
            "skill_renders": skill_renders,
            "meta": meta,
            "entry_results": entry_results,
            "preserved_status": preserved["status"],
            "tree": _snapshot_tree(root),
        }

    baseline_out, head_out = _run_paired(render_all)
    assert baseline_out == head_out


def test_default_surfaces_lifecycle_and_markdown_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_lifecycle(mod: ModuleType, idx: int) -> object:
        root = tmp_path / f"variant_{idx}"
        codex = root / "codex"
        claude = root / "claude"
        opencode = root / "opencode"

        # Seed legacy managed aliases, legacy custom prompt, duplicate skill, and user file.
        legacy_skill = codex / "skills" / "loop-global-summary" / "SKILL.md"
        legacy_skill.parent.mkdir(parents=True)
        legacy_skill.write_text(
            "<!-- loopx-managed-slash-command:v1 command=/loop-global-summary surface=codex-skills -->\nold\n",
            encoding="utf-8",
        )
        legacy_prompt = codex / "prompts" / "loopx.md"
        legacy_prompt.parent.mkdir(parents=True)
        legacy_prompt.write_text(
            "<!-- loopx-managed-slash-command:v1 command=/loopx surface=codex-prompt -->\nold prompt\n",
            encoding="utf-8",
        )
        user_claude_skill = claude / "skills" / "loopx-global-gates" / "SKILL.md"
        user_claude_skill.parent.mkdir(parents=True)
        user_claude_skill.write_text("# My custom gates\n", encoding="utf-8")

        def normalize_payload(payload: dict[str, object]) -> object:
            raw = json.dumps(payload, sort_keys=True).replace(str(root), "<ROOT>")
            markdown = mod.render_slash_command_install_markdown(payload).replace(
                str(root), "<ROOT>"
            )
            return json.loads(raw), markdown

        monkeypatch.setenv("HOME", str(root / "home"))
        steps = []
        for uninstall, execute in (
            (False, False),
            (False, True),
            (False, True),
            (True, False),
            (True, True),
            (True, True),
        ):
            res = cast(
                dict[str, object],
                mod.install_slash_commands(
                    execute=execute,
                    uninstall=uninstall,
                    codex_home=str(codex),
                    claude_home=str(claude),
                    opencode_home=str(opencode),
                ),
            )
            steps.append(normalize_payload(res))
        return steps, _snapshot_tree(root)

    baseline_out, head_out = _run_paired(run_lifecycle)
    assert baseline_out == head_out


def test_mcp_and_antigravity_hooks_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_mcp_and_hooks(mod: ModuleType, idx: int) -> object:
        root = tmp_path / f"variant_{idx}"
        cursor = root / "cursor"
        kiro = root / "kiro"
        agy = root / "agy"
        provision_calls = 0

        def fake_mcp() -> tuple[str, str]:
            nonlocal provision_calls
            provision_calls += 1
            return "/opt/venv/bin/python", "/opt/loopx/mcp.py"

        monkeypatch.setattr(mod, "_loopx_mcp_command", fake_mcp)
        monkeypatch.setattr(
            mod,
            "_kiro_cli_mcp_command",
            lambda: ("/opt/venv/bin/python", "/opt/loopx/kiro_mcp.py"),
        )

        # Dry run must not provision MCP.
        dry = mod.install_slash_commands(
            execute=False,
            surfaces=["cursor", "kiro-cli", "antigravity-app"],
            cursor_home=str(cursor),
            kiro_home=str(kiro),
            agy_home=str(agy),
            with_agy_mcp=True,
            with_agy_hooks=True,
            agy_goal_id="g1",
            agy_agent_id="a1",
        )
        assert provision_calls == 0

        installed = mod.install_slash_commands(
            execute=True,
            surfaces=["cursor", "kiro-cli", "antigravity-app"],
            cursor_home=str(cursor),
            kiro_home=str(kiro),
            agy_home=str(agy),
            with_agy_mcp=True,
            with_agy_hooks=True,
            agy_goal_id="g1",
            agy_agent_id="a1",
        )
        assert provision_calls >= 1

        # Idempotent re-run.
        idem = mod.install_slash_commands(
            execute=True,
            surfaces=["cursor", "kiro-cli", "antigravity-app"],
            cursor_home=str(cursor),
            kiro_home=str(kiro),
            agy_home=str(agy),
            with_agy_mcp=True,
            with_agy_hooks=True,
            agy_goal_id="g1",
            agy_agent_id="a1",
        )

        # Hand-edit Cursor MCP to become user-owned -> dry-run preserves marker, execute expires marker.
        cursor_cfg = json.loads((cursor / "mcp.json").read_text(encoding="utf-8"))
        cursor_cfg["mcpServers"]["loopx"]["args"] = ["/custom/mcp.py"]
        (cursor / "mcp.json").write_text(json.dumps(cursor_cfg), encoding="utf-8")
        user_owned_dry = mod.install_slash_commands(
            execute=False, surfaces=["cursor"], cursor_home=str(cursor)
        )
        marker_after_dry = (cursor / mod.CURSOR_MCP_MARKER_NAME).exists()
        user_owned_exec = mod.install_slash_commands(
            execute=True, surfaces=["cursor"], cursor_home=str(cursor)
        )
        marker_after_exec = (cursor / mod.CURSOR_MCP_MARKER_NAME).exists()

        # Invalid JSON shapes and missing dependency fail closed cleanly.
        bad_cursor = root / "bad_cursor"
        bad_cursor.mkdir()
        (bad_cursor / "mcp.json").write_text('{"mcpServers": []}', encoding="utf-8")
        bad_cursor_res = mod.install_slash_commands(
            execute=True, surfaces=["cursor"], cursor_home=str(bad_cursor)
        )
        monkeypatch.setattr(mod, "_loopx_mcp_command", lambda: None)
        no_dep_res = mod.install_slash_commands(
            execute=True,
            surfaces=["antigravity-app"],
            agy_home=str(root / "agy_no_dep"),
            with_agy_mcp=True,
        )

        # Legacy hooks.loopx migration vs user-owned hooks.loopx preservation + direct sync.
        hooks_legacy = root / "agy_legacy"
        hooks_cfg = hooks_legacy / "hooks.json"
        hooks_marker = hooks_legacy / mod.ANTIGRAVITY_HOOKS_MARKER_NAME
        legacy_entry = {"command": ["loopx", "stop-hook"]}
        hooks_cfg.parent.mkdir(parents=True)
        hooks_cfg.write_text(json.dumps({"hooks": {"loopx": legacy_entry}}), encoding="utf-8")
        hooks_marker.write_text(
            json.dumps({"schema_version": "old", "hooks": {"loopx": legacy_entry}}),
            encoding="utf-8",
        )
        migrated_dry = mod.sync_antigravity_hooks(agy_home=str(hooks_legacy), execute=False)
        migrated_exec = mod.sync_antigravity_hooks(agy_home=str(hooks_legacy), execute=True)
        retired_hooks = mod.sync_antigravity_hooks(
            agy_home=str(hooks_legacy), uninstall=True, execute=True
        )

        # User-owned legacy hook is preserved.
        hooks_user = root / "agy_user"
        hooks_user.mkdir()
        (hooks_user / "hooks.json").write_text(
            json.dumps({"hooks": {"loopx": {"command": ["custom"]}}}), encoding="utf-8"
        )
        preserved_user_hook = mod.sync_antigravity_hooks(
            agy_home=str(hooks_user), execute=True
        )

        # Uninstall cleans up managed entries.
        uninstalled = mod.install_slash_commands(
            execute=True,
            uninstall=True,
            surfaces=["cursor", "kiro-cli", "antigravity-app"],
            cursor_home=str(cursor),
            kiro_home=str(kiro),
            agy_home=str(agy),
            with_agy_mcp=True,
            with_agy_hooks=True,
            agy_goal_id="g1",
            agy_agent_id="a1",
        )

        def clean(obj: object) -> object:
            return json.loads(json.dumps(obj, sort_keys=True).replace(str(root), "<ROOT>"))

        return {
            "dry": clean(dry),
            "installed": clean(installed),
            "idem": clean(idem),
            "user_owned_dry": clean(user_owned_dry),
            "marker_after_dry": marker_after_dry,
            "user_owned_exec": clean(user_owned_exec),
            "marker_after_exec": marker_after_exec,
            "bad_cursor_res": clean(bad_cursor_res),
            "no_dep_res": clean(no_dep_res),
            "migrated_dry": clean(migrated_dry),
            "migrated_exec": clean(migrated_exec),
            "retired_hooks": clean(retired_hooks),
            "preserved_user_hook": clean(preserved_user_hook),
            "uninstalled": clean(uninstalled),
            "tree": _snapshot_tree(root),
        }

    baseline_out, head_out = _run_paired(run_mcp_and_hooks)
    assert baseline_out == head_out


def test_opencode_bridge_jsonc_and_pi_atomic_install_equivalence(
    tmp_path: Path,
) -> None:
    def run_opencode_and_pi(mod: ModuleType, idx: int) -> object:
        root = tmp_path / f"variant_{idx}"

        # 1. Blocked bridge without opencode surface.
        no_opencode = mod.install_slash_commands(
            execute=False, surfaces=["codex"], with_goal_bridge=True, codex_home=str(root / "codex")
        )

        # 2. Commented-out JSONC should NOT conflict, trailing comma + string '//' intact.
        oc_ok = root / "oc_ok"
        oc_ok.mkdir(parents=True)
        (oc_ok / "opencode.jsonc").write_text(
            '{\n  // "plugin": ["opencode-goal-plugin"],\n  /* block "loopx" */\n'
            '  "label": "https://example.invalid//kept,}",\n  "plugin": ["other-plugin",],\n}\n',
            encoding="utf-8",
        )
        oc_installed = mod.install_slash_commands(
            execute=True,
            surfaces=["opencode"],
            opencode_home=str(oc_ok),
            with_goal_bridge=True,
        )
        oc_uninstalled = mod.install_slash_commands(
            execute=True,
            uninstall=True,
            surfaces=["opencode"],
            opencode_home=str(oc_ok),
            with_goal_bridge=True,
        )

        # 3. Direct tuple plugin registration in JSONC blocks safely.
        oc_conflict = root / "oc_conflict"
        oc_conflict.mkdir(parents=True)
        (oc_conflict / "opencode.jsonc").write_text(
            '{"plugin": [["@heimoshuiyu/opencode-goal-plugin@1.0.0", {}],]}',
            encoding="utf-8",
        )
        oc_conflict_res = mod.install_slash_commands(
            execute=True,
            surfaces=["opencode"],
            opencode_home=str(oc_conflict),
            with_goal_bridge=True,
        )

        # 4. Invalid config, user-owned bridge file, invalid package.json.
        oc_bad_cfg = root / "oc_bad_cfg"
        oc_bad_cfg.mkdir(parents=True)
        (oc_bad_cfg / "opencode.json").write_text("{bad", encoding="utf-8")
        oc_bad_cfg_res = mod.install_slash_commands(
            execute=True,
            surfaces=["opencode"],
            opencode_home=str(oc_bad_cfg),
            with_goal_bridge=True,
        )

        oc_bad_pkg = root / "oc_bad_pkg"
        oc_bad_pkg.mkdir(parents=True)
        (oc_bad_pkg / "package.json").write_text("[]", encoding="utf-8")
        oc_bad_pkg_res = mod.install_slash_commands(
            execute=True,
            surfaces=["opencode"],
            opencode_home=str(oc_bad_pkg),
            with_goal_bridge=True,
        )

        oc_user_bridge = root / "oc_user_bridge"
        (oc_user_bridge / "plugins").mkdir(parents=True)
        (oc_user_bridge / "plugins" / "loopx-goal.js").write_text("// user\n", encoding="utf-8")
        oc_user_bridge_res = mod.install_slash_commands(
            execute=True,
            surfaces=["opencode"],
            opencode_home=str(oc_user_bridge),
            with_goal_bridge=True,
        )

        # 5. Pi atomic install, user-owned runtime block, user scope, and invalid scope.
        pi_proj = root / "pi_proj"
        pi_proj.mkdir(parents=True)
        pi_user = root / "pi_user"
        pi_ok = mod.install_slash_commands(
            execute=True,
            surfaces=["pi"],
            pi_project=str(pi_proj),
            pi_scope="project",
        )
        pi_user_res = mod.install_slash_commands(
            execute=True,
            surfaces=["pi"],
            pi_project=str(pi_proj),
            pi_scope="user",
            pi_user_home=str(pi_user),
        )
        pi_uninstall = mod.install_slash_commands(
            execute=True,
            uninstall=True,
            surfaces=["pi"],
            pi_project=str(pi_proj),
            pi_scope="project",
        )
        pi_blocked = root / "pi_blocked"
        runtime_block = pi_blocked / ".pi" / "extensions" / "loopx" / "pi-goal-loop-runtime.mjs"
        runtime_block.parent.mkdir(parents=True)
        runtime_block.write_text("// user runtime\n", encoding="utf-8")
        pi_blocked_res = mod.install_slash_commands(
            execute=True,
            surfaces=["pi"],
            pi_project=str(pi_blocked),
            pi_scope="project",
        )
        with pytest.raises(ValueError, match="pi_scope must be 'project' or 'user'"):
            mod.install_slash_commands(execute=False, pi_scope="global")

        def clean(obj: object) -> object:
            return json.loads(json.dumps(obj, sort_keys=True).replace(str(root), "<ROOT>"))

        return {
            "no_opencode": clean(no_opencode),
            "oc_installed": clean(oc_installed),
            "oc_uninstalled": clean(oc_uninstalled),
            "oc_conflict_res": clean(oc_conflict_res),
            "oc_bad_cfg_res": clean(oc_bad_cfg_res),
            "oc_bad_pkg_res": clean(oc_bad_pkg_res),
            "oc_user_bridge_res": clean(oc_user_bridge_res),
            "pi_ok": clean(pi_ok),
            "pi_user_res": clean(pi_user_res),
            "pi_uninstall": clean(pi_uninstall),
            "pi_blocked_res": clean(pi_blocked_res),
            "tree": _snapshot_tree(root),
        }

    baseline_out, head_out = _run_paired(run_opencode_and_pi)
    assert baseline_out == head_out


def test_inspect_skill_facades_readback_edge_paths_equivalence(
    tmp_path: Path,
) -> None:
    def inspect_all(mod: ModuleType, idx: int) -> object:
        root = tmp_path / f"variant_{idx}"
        skills = root / "skills"
        mod.install_slash_commands(
            execute=True,
            surfaces=["zcode"],
            zcode_home=str(root),
            cli_bin="loopx-custom",
        )
        # Mutate three facades to trigger stale, user_owned, invalid UTF-8, and >1 MiB unreadable.
        stale = skills / "loopx-global-summary" / "SKILL.md"
        stale.write_text(
            stale.read_text(encoding="utf-8") + "\nextra stale line\n",
            encoding="utf-8",
        )
        user_owned = skills / "loopx-global-gates" / "SKILL.md"
        user_owned.write_text("# User gates\n", encoding="utf-8")
        bad_utf8 = skills / "loopx-global-todos" / "SKILL.md"
        bad_utf8.write_bytes(b"\xff\xfe\xfd")
        oversized = skills / "loopx-global-risks" / "SKILL.md"
        oversized.write_bytes(b"a" * (1024 * 1024 + 8))
        (skills / "loopx-pr-review" / "SKILL.md").unlink()

        res_default = mod.inspect_skill_facades(skills)
        res_agy = mod.inspect_skill_facades(skills, host_surface="antigravity-app")
        raw = json.dumps([res_default, res_agy], sort_keys=True).replace(str(root), "<ROOT>")
        return json.loads(raw)

    baseline_out, head_out = _run_paired(inspect_all)
    assert baseline_out == head_out
