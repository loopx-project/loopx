from __future__ import annotations

from pathlib import Path

import pytest

from loopx.capabilities.anti_entropy import (
    ANTI_ENTROPY_CAPABILITY_ID,
    ANTI_ENTROPY_CATALOG_ENTRY,
    ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION,
    ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION,
    RetirementBasis,
    audit_ghost_code,
    evaluate_diff_hygiene,
    run_anti_entropy_verify,
    verify_delete_first,
)
from loopx.capabilities.catalog import BUILTIN_CAPABILITIES


def test_catalog_registration() -> None:
    """Verify anti-entropy capability is registered in builtin capabilities catalog."""
    matching = [
        c for c in BUILTIN_CAPABILITIES if c["id"] == ANTI_ENTROPY_CAPABILITY_ID
    ]
    assert len(matching) == 1
    record = matching[0]
    assert record["origin"] == "builtin"
    assert record["status"] == "experimental"
    assert record["default_enabled"] is False
    assert "delete-first" in record["real_world_anchor"] or "refactoring" in record["real_world_anchor"]


def _make_workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create an explicitly bound workspace containing post-change sources."""
    workspace = tmp_path / "workspace"
    for rel_path, content in files.items():
        target = workspace / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workspace


def test_verify_delete_first_passed(tmp_path: Path) -> None:
    """Verify clean retirement where old symbol is cleanly deleted from bound sources."""
    diff = """
--- a/legacy_module.py
+++ b/legacy_module.py
@@ -1,5 +1,5 @@
-def old_deprecated_calculator(x, y):
-    return x + y
+def modern_calculator(x: int, y: int) -> int:
+    return x + y
"""
    workspace = _make_workspace(
        tmp_path,
        {"legacy_module.py": "def modern_calculator(x: int, y: int) -> int:\n    return x + y\n"},
    )
    basis = RetirementBasis(
        target_symbols=("old_deprecated_calculator",),
        rationale="Replaced by modern_calculator",
    )
    findings = verify_delete_first(diff, basis, workspace=workspace)
    assert len(findings) == 0

    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "passed"
    assert "old_deprecated_calculator" in receipt.retired_symbols


def test_verify_delete_first_violation(tmp_path: Path) -> None:
    """Verify rejection when old symbol is kept instead of delete-first."""
    diff = """
--- a/legacy_module.py
+++ b/legacy_module.py
@@ -1,3 +1,6 @@
+def modern_calculator(x: int, y: int) -> int:
+    return x + y
 def old_deprecated_calculator(x, y):
     return x + y
"""
    workspace = _make_workspace(
        tmp_path,
        {
            "legacy_module.py": (
                "def modern_calculator(x: int, y: int) -> int:\n"
                "    return x + y\n"
                "def old_deprecated_calculator(x, y):\n"
                "    return x + y\n"
            )
        },
    )
    basis = RetirementBasis(
        target_symbols=("old_deprecated_calculator",),
        allow_compat_seam=False,
    )
    findings = verify_delete_first(diff, basis, workspace=workspace)
    assert len(findings) == 1
    assert findings[0].rule_id == "AE001-DELETE_FIRST_VIOLATION"
    assert findings[0].severity == "blocking"

    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"


def test_verify_delete_first_zombie_reintroduced() -> None:
    """Verify rejection when symbol is deleted and reintroduced simultaneously in the diff."""
    diff = """
--- a/legacy_module.py
+++ b/legacy_module.py
@@ -1,3 +1,3 @@
-def legacy_sink():
+def legacy_sink():  # wrapper
     pass
"""
    basis = RetirementBasis(target_symbols=("legacy_sink",))
    findings = verify_delete_first(diff, basis)
    assert any(f.rule_id == "AE002-ZOMBIE_SYMBOL_REINTRODUCED" for f in findings)


def test_target_paths_deletion() -> None:
    """Verify target_paths require complete file deletion headers in the diff."""
    # File cleanly deleted -> pass (diff-only evidence is sufficient for paths)
    deleted_diff = """
--- a/legacy/old_file.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def dead():
-    pass
"""
    basis = RetirementBasis(target_paths=("legacy/old_file.py",))
    receipt = evaluate_diff_hygiene(deleted_diff, basis)
    assert receipt.verdict == "passed"
    assert len(receipt.findings) == 0

    # File only partially modified instead of deleted -> fail
    partial_diff = """
--- a/legacy/old_file.py
+++ b/legacy/old_file.py
@@ -1,3 +1,3 @@
-def dead():
+def modified():
     pass
"""
    receipt_partial = evaluate_diff_hygiene(partial_diff, basis)
    assert receipt_partial.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" for f in receipt_partial.findings)


def test_compat_seam_does_not_falsely_retire(tmp_path: Path) -> None:
    """Verify allow_compat_seam allows passing but does NOT claim un-deleted symbols as retired."""
    diff = """
--- a/legacy_module.py
+++ b/legacy_module.py
@@ -1,3 +1,4 @@
+# some edit
 def old_api():
     return 1
"""
    workspace = _make_workspace(tmp_path, {"legacy_module.py": "def old_api():\n    return 1\n"})
    basis = RetirementBasis(
        target_symbols=("old_api",),
        allow_compat_seam=True,
    )
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "passed"
    seam_findings = [f for f in receipt.findings if f.location == "old_api"]
    assert seam_findings and all(f.severity == "advisory" for f in seam_findings)
    # old_api was NOT deleted, so it must NOT be in retired_symbols!
    assert "old_api" not in receipt.retired_symbols


def test_retained_definition_is_blocking_without_compat_seam(tmp_path: Path) -> None:
    """A retained definition without an authorized compat seam stays a hard AE001 block."""
    diff = """
--- a/legacy_module.py
+++ b/legacy_module.py
@@ -1,3 +1,4 @@
+# some edit
 def old_api():
     return 1
"""
    workspace = _make_workspace(tmp_path, {"legacy_module.py": "def old_api():\n    return 1\n"})
    basis = RetirementBasis(target_symbols=("old_api",), allow_compat_seam=False)
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(
        f.rule_id == "AE001-DELETE_FIRST_VIOLATION" and f.severity == "blocking"
        for f in receipt.findings
    )


def test_false_positive_prevention() -> None:
    """Ensure normal English comments or documentation mentioning words like fallback are not blocked."""
    diff = """
--- a/README.md
+++ b/README.md
@@ -10,3 +10,5 @@
+# if the connection is closed, the next call reconnects.
+# Please see documentation for details.
+# Delete fallback_v4 after migrating all callers.
"""
    basis = RetirementBasis()
    receipt = evaluate_diff_hygiene(diff, basis)
    assert receipt.verdict == "passed"
    # Should not produce blocking findings
    assert all(f.severity != "blocking" for f in receipt.findings)


def test_audit_ghost_code_commented_block() -> None:
    """Verify advisory finding when dead code blocks are left behind as comments."""
    diff = """
--- a/service.py
+++ b/service.py
@@ -10,3 +10,8 @@
+def active_fn():
+    # def old_worker():
+    #     x = 1
+    #     return x
+    return True
"""
    findings = audit_ghost_code(diff)
    assert len(findings) == 1
    assert findings[0].rule_id == "AE003-COMMENTED_CODE_ADVISORY"
    assert findings[0].severity == "advisory"


def test_cli_runner(tmp_path: Path) -> None:
    """Verify CLI verify function generates a complete, valid payload."""
    clean_diff = """
--- a/service.py
+++ b/service.py
@@ -1,3 +1,3 @@
-def legacy_call():
+def modern_call():
     return 42
"""
    workspace = _make_workspace(tmp_path, {"service.py": "def modern_call():\n    return 42\n"})
    payload = run_anti_entropy_verify(
        diff_text=clean_diff,
        basis_data={"target_symbols": ["legacy_call"]},
        goal_id="test-goal-id",
        workspace=workspace,
    )
    assert payload["ok"] is True
    assert payload["verdict"] == "passed"
    assert payload["blocking_finding_count"] == 0
    assert "legacy_call" in payload["retired_symbols"]


def test_retirement_basis_type_validation() -> None:
    """Verify strict type validation on RetirementBasis.from_dict to prevent truthiness loopholes."""
    import pytest

    # allow_compat_seam must be a real boolean, not truthy string "false"
    with pytest.raises(TypeError, match="allow_compat_seam must be a boolean"):
        RetirementBasis.from_dict({"allow_compat_seam": "false"})

    with pytest.raises(TypeError, match="allow_compat_seam must be a boolean"):
        RetirementBasis.from_dict({"allow_compat_seam": "true"})

    with pytest.raises(TypeError, match="allow_compat_seam must be a boolean"):
        RetirementBasis.from_dict({"allow_compat_seam": 1})

    # basis data must be a Mapping, not an array
    with pytest.raises(TypeError, match="must be a JSON object"):
        RetirementBasis.from_dict([])

    with pytest.raises(TypeError, match="must be a JSON object"):
        RetirementBasis.from_dict("not_a_dict")

    # target_symbols must be a collection of strings
    with pytest.raises(TypeError, match="target_symbols must be a list of strings"):
        RetirementBasis.from_dict({"target_symbols": "old_api"})

    with pytest.raises(TypeError, match="target_symbols elements must be strings"):
        RetirementBasis.from_dict({"target_symbols": [123]})

    # target_paths must be a collection of strings
    with pytest.raises(TypeError, match="target_paths must be a list of strings"):
        RetirementBasis.from_dict({"target_paths": "legacy.py"})

    with pytest.raises(TypeError, match="target_paths elements must be strings"):
        RetirementBasis.from_dict({"target_paths": [456]})

    # schema_version and rationale must be strings
    with pytest.raises(TypeError, match="schema_version must be a string"):
        RetirementBasis.from_dict({"schema_version": 123})

    with pytest.raises(TypeError, match="rationale must be a string"):
        RetirementBasis.from_dict({"rationale": 123})


def test_cli_exit_code_and_subprocess(tmp_path: Path) -> None:
    """Verify CLI returns exit code 1 on rejection, 0 on pass, and 2 on invalid basis JSON."""
    import subprocess
    import sys

    workspace = _make_workspace(
        tmp_path,
        {"service.py": "def old_api():\n    return 1\n"},
    )

    # 1. Rejected verification (definition retained in bound workspace) -> exit code 1
    diff_fail = """
--- a/service.py
+++ b/service.py
@@ -1,3 +1,3 @@
+# keep old_api
 def old_api():
     return 1
"""
    proc_fail = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "anti-entropy",
            "verify",
            "--diff-file",
            "-",
            "--basis-json",
            "{\"target_symbols\": [\"old_api\"]}",
            "--workspace",
            str(workspace),
            "--format",
            "json",
        ],
        input=diff_fail.encode("utf-8"),
        capture_output=True,
    )
    assert proc_fail.returncode == 1

    # 2. Passed verification -> exit code 0
    clean_workspace = _make_workspace(tmp_path / "clean", {"service.py": "def new_api():\n    return 2\n"})
    diff_pass = """
--- a/service.py
+++ b/service.py
@@ -1,3 +1,3 @@
-def old_api():
-    return 1
+def new_api():
+    return 2
"""
    proc_pass = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "anti-entropy",
            "verify",
            "--diff-file",
            "-",
            "--basis-json",
            "{\"target_symbols\": [\"old_api\"]}",
            "--workspace",
            str(clean_workspace),
            "--format",
            "json",
        ],
        input=diff_pass.encode("utf-8"),
        capture_output=True,
    )
    assert proc_pass.returncode == 0

    # 3. Invalid basis JSON array -> exit code 2
    proc_err = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "anti-entropy",
            "verify",
            "--diff-file",
            "-",
            "--basis-json",
            "[]",
            "--format",
            "json",
        ],
        input=b"",
        capture_output=True,
    )
    assert proc_err.returncode == 2

    # 4. Nonexistent workspace directory -> exit code 2
    proc_ws = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "anti-entropy",
            "verify",
            "--diff-file",
            "-",
            "--basis-json",
            "{\"target_symbols\": [\"old_api\"]}",
            "--workspace",
            str(tmp_path / "does_not_exist"),
            "--format",
            "json",
        ],
        input=diff_pass.encode("utf-8"),
        capture_output=True,
    )
    assert proc_ws.returncode == 2

    # 5. verify without any basis -> input error, never a silent pass
    proc_no_basis = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "anti-entropy",
            "verify",
            "--diff-file",
            "-",
            "--format",
            "json",
        ],
        input=b"",
        capture_output=True,
    )
    assert proc_no_basis.returncode == 2
    assert b"retirement basis" in proc_no_basis.stderr

    # 6. verify with a basis that declares no retirement target -> input error
    proc_no_target = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "anti-entropy",
            "verify",
            "--diff-file",
            "-",
            "--basis-json",
            "{}",
            "--format",
            "json",
        ],
        input=b"",
        capture_output=True,
    )
    assert proc_no_target.returncode == 2


def test_docstring_example_deletion_does_not_retire_real_definition(tmp_path: Path) -> None:
    """Verify deleting a code example in a docstring does not falsely pass a retained real definition."""
    diff = (
        "--- a/api.py\n"
        "+++ b/api.py\n"
        "@@ -1,6 +1,3 @@\n"
        " \"\"\"\n"
        "-Example:\n"
        "-    def old_api():\n"
        "-        return 1\n"
        " \"\"\"\n"
        " def old_api():\n"
        "     return 2\n"
    )
    workspace = _make_workspace(
        tmp_path,
        {"api.py": '"""\nExample:\n    def old_api():\n        return 1\n"""\n\ndef old_api():\n    return 2\n'},
    )
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id in ("AE001-DELETE_FIRST_VIOLATION", "AE002-ZOMBIE_SYMBOL_REINTRODUCED") for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols


def test_exact_git_path_confirmation_and_encoding() -> None:
    """Verify exact path matching without suffix guesswork, plus empty and quoted path deletion."""
    # 1. Suffix mismatch: deleting legacy_api.py must NOT satisfy target_paths=("api.py",)
    diff_suffix = """
--- a/legacy_api.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def f(): pass
"""
    basis_suffix = RetirementBasis(target_paths=("api.py",))
    receipt_suffix = evaluate_diff_hygiene(diff_suffix, basis_suffix)
    assert receipt_suffix.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" for f in receipt_suffix.findings)

    # 2. Empty file deletion metadata (deleted file mode 100644 without +/- hunks)
    diff_empty = """
diff --git a/legacy/__init__.py b/legacy/__init__.py
deleted file mode 100644
index e69de29..0000000
"""
    basis_empty = RetirementBasis(target_paths=("legacy/__init__.py",))
    receipt_empty = evaluate_diff_hygiene(diff_empty, basis_empty)
    assert receipt_empty.verdict == "passed"
    assert len(receipt_empty.findings) == 0

    # 3. Quoted path with C-style octal escaping for non-ASCII paths
    diff_quoted = """
diff --git "a/legacy/\\346\\227\\247\\345\\256\\236\\347\\214\\233.py" "b/legacy/\\346\\227\\247\\345\\256\\236\\347\\214\\233.py"
deleted file mode 100644
--- "a/legacy/\\346\\227\\247\\345\\256\\236\\347\\214\\233.py"
+++ /dev/null
"""
    basis_quoted = RetirementBasis(target_paths=("legacy/旧实猛.py",))
    receipt_quoted = evaluate_diff_hygiene(diff_quoted, basis_quoted)
    assert receipt_quoted.verdict == "passed"
    assert len(receipt_quoted.findings) == 0


def test_reintroduced_symbol_excluded_from_retired_symbols_with_partial_retirement(tmp_path: Path) -> None:
    """Verify modified/reintroduced symbol is excluded from retired_symbols while cleanly retired symbol is kept."""
    diff = """
--- a/service.py
+++ b/service.py
@@ -1,6 +1,4 @@
-def old_helper():
-    return 1
-def old_api(x):
-    return x
+def old_api(x=1):
+    return x
"""
    workspace = _make_workspace(tmp_path, {"service.py": "def old_api(x=1):\n    return x\n"})
    basis = RetirementBasis(target_symbols=("old_helper", "old_api"))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE002-ZOMBIE_SYMBOL_REINTRODUCED" and f.location == "old_api" for f in receipt.findings)
    # old_api is excluded from retired_symbols, but old_helper is accurately certified!
    assert receipt.retired_symbols == ("old_helper",)


def test_typescript_clean_delete_first(tmp_path: Path) -> None:
    """Verify TypeScript functions, classes, interfaces, and types are accurately recognized as retired."""
    diff = """
--- a/control_plane.ts
+++ b/control_plane.ts
@@ -1,15 +1,2 @@
-export function oldLeaseAcquire(taskId: string): boolean {
-    return false;
-}
-export class OldScheduler {
-    run(): void {}
-}
-export interface OldLeaseConfig {
-    timeoutMs: number;
-}
-export type OldLeaseState = "active" | "expired";
-export const oldArrowHelper = (x: number) => x * 2;
+export function newLeaseAcquire(): boolean { return true; }
"""
    workspace = _make_workspace(
        tmp_path,
        {"control_plane.ts": "export function newLeaseAcquire(): boolean { return true; }\n"},
    )
    basis = RetirementBasis(
        target_symbols=(
            "oldLeaseAcquire",
            "OldScheduler",
            "OldLeaseConfig",
            "OldLeaseState",
            "oldArrowHelper",
        ),
    )
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "passed", f"findings: {receipt.findings}"
    assert set(receipt.retired_symbols) == {
        "oldLeaseAcquire",
        "OldScheduler",
        "OldLeaseConfig",
        "OldLeaseState",
        "oldArrowHelper",
    }


def test_typescript_call_deletion_does_not_retire_definition(tmp_path: Path) -> None:
    """Verify deleting a TypeScript function call without deleting definition triggers AE001."""
    diff = """
--- a/control_plane.ts
+++ b/control_plane.ts
@@ -1,7 +1,7 @@
 export function oldLeaseAcquire(): boolean {
     return false;
 }
 export function run() {
-    return oldLeaseAcquire();
+    return newLeaseAcquire();
 }
"""
    workspace = _make_workspace(
        tmp_path,
        {
            "control_plane.ts": (
                "export function oldLeaseAcquire(): boolean {\n"
                "    return false;\n"
                "}\n"
                "export function run() {\n"
                "    return newLeaseAcquire();\n"
                "}\n"
            )
        },
    )
    basis = RetirementBasis(target_symbols=("oldLeaseAcquire",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" for f in receipt.findings)
    assert "oldLeaseAcquire" not in receipt.retired_symbols


def test_typescript_zombie_symbol_reintroduced() -> None:
    """Verify reintroducing an old TypeScript function as a wrapper triggers AE002."""
    diff = """
--- a/control_plane.ts
+++ b/control_plane.ts
@@ -1,4 +1,4 @@
-export function oldTaskLease() { return 1; }
+export function oldTaskLease() { return newTaskLease(); }
"""
    basis = RetirementBasis(target_symbols=("oldTaskLease",))
    receipt = evaluate_diff_hygiene(diff, basis)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE002-ZOMBIE_SYMBOL_REINTRODUCED" for f in receipt.findings)


def test_diff_only_verification_cannot_certify_retirement() -> None:
    """Without a bound workspace the verdict may reject but never certifies retirement (AE004)."""
    diff = """
--- a/non_existent_dir/virtual_module.py
+++ b/non_existent_dir/virtual_module.py
@@ -1,5 +1,2 @@
-def obsolete_worker():
-    return None
+def active_worker(): return 1
"""
    basis = RetirementBasis(target_symbols=("obsolete_worker",))
    receipt = evaluate_diff_hygiene(diff, basis)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE004-INSUFFICIENT_SOURCE_EVIDENCE" for f in receipt.findings)
    assert receipt.retired_symbols == ()
    assert receipt.source_evidence == "diff_only"


def test_agent_tool_inspect_interface(tmp_path: Path) -> None:
    """Verify the structured agent tool anti_entropy_inspect returns complete, valid payload."""
    from loopx.capabilities.anti_entropy.agent_tool import anti_entropy_inspect

    diff = """
--- a/service.ts
+++ b/service.ts
@@ -1,4 +1,2 @@
-export function legacyHandler() {}
+export function modernHandler() {}
"""
    workspace = _make_workspace(tmp_path, {"service.ts": "export function modernHandler() {}\n"})
    payload = anti_entropy_inspect(
        diff_text=diff,
        retirement_basis={"target_symbols": ["legacyHandler"]},
        goal_id="goal-test-tool",
        workspace=str(workspace),
    )
    assert payload["ok"] is True
    assert payload["command"] == "verify"
    assert payload["goal_id"] == "goal-test-tool"
    assert payload["retired_symbols"] == ["legacyHandler"]
    assert payload["verdict"] == "passed"


def test_path_normalization_distinguishes_a_dir_and_dot_files() -> None:
    """Verify a/api.py, api.py, and .api.py are never conflated."""
    diff_delete_root = """
--- a/api.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def f(): pass
"""
    basis_a = RetirementBasis(target_paths=("a/api.py",))
    receipt_a = evaluate_diff_hygiene(diff_delete_root, basis_a)
    assert receipt_a.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" and f.location == "a/api.py" for f in receipt_a.findings)

    basis_dot = RetirementBasis(target_paths=(".api.py",))
    receipt_dot = evaluate_diff_hygiene(diff_delete_root, basis_dot)
    assert receipt_dot.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" and f.location == ".api.py" for f in receipt_dot.findings)

    diff_delete_nested_a = """
--- a/a/api.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def f(): pass
"""
    receipt_nested = evaluate_diff_hygiene(diff_delete_nested_a, basis_a)
    assert receipt_nested.verdict == "passed"
    assert len(receipt_nested.findings) == 0

    diff_delete_dot = """
--- a/.api.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def f(): pass
"""
    receipt_dot_pass = evaluate_diff_hygiene(diff_delete_dot, basis_dot)
    assert receipt_dot_pass.verdict == "passed"
    assert len(receipt_dot_pass.findings) == 0


def test_empty_file_deletion_with_spaces_in_filename() -> None:
    """Verify unquoted git diff headers for empty files with spaces in names are correctly recognized."""
    diff_spaces = """
diff --git a/legacy/old api.py b/legacy/old api.py
deleted file mode 100644
index e69de29..0000000
"""
    basis = RetirementBasis(target_paths=("legacy/old api.py",))
    receipt = evaluate_diff_hygiene(diff_spaces, basis)
    assert receipt.verdict == "passed", f"findings: {receipt.findings}"
    assert len(receipt.findings) == 0


def test_docstring_sample_not_certified_when_function_retained_on_disk(tmp_path: Path) -> None:
    """Deleting docstring example lines must not certify a function retained in the bound workspace."""
    workspace = _make_workspace(
        tmp_path,
        {"service.py": '"""\nExample:\n    def old_api():\n        pass\n"""\n\ndef old_api():\n    return 42\n'},
    )

    diff_docstring = """
--- a/service.py
+++ b/service.py
@@ -3,2 +3,0 @@
-    def old_api():
-        pass
"""
    basis = RetirementBasis(target_symbols=("old_api",))

    receipt = evaluate_diff_hygiene(diff_docstring, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols

    # The same diff and workspace bound from a different CWD yields the same result:
    # conclusions depend on the explicit binding, not the caller's working directory.
    receipt_again = evaluate_diff_hygiene(diff_docstring, basis, workspace=workspace)
    assert receipt_again.verdict == receipt.verdict
    assert receipt_again.diff_hash == receipt.diff_hash


def test_string_literal_mention_does_not_block_retirement(tmp_path: Path) -> None:
    """A string mentioning the retired name is not an active reference (AE002 must not fire)."""
    diff = """
--- a/service.py
+++ b/service.py
@@ -1,4 +1,4 @@
-def old_api():
-    return 1
+def new_api():
+    return "old_api has been removed"
"""
    workspace = _make_workspace(
        tmp_path,
        {"service.py": 'def new_api():\n    return "old_api has been removed"\n'},
    )
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "passed", f"findings: {receipt.findings}"
    assert not any(f.rule_id == "AE002-ZOMBIE_SYMBOL_REINTRODUCED" for f in receipt.findings)
    assert receipt.retired_symbols == ("old_api",)


def test_readme_fenced_example_is_not_symbol_evidence(tmp_path: Path) -> None:
    """Deleting a fenced def example from docs must not certify a definition still in real sources."""
    diff = """
--- a/README.md
+++ b/README.md
@@ -5,4 +5,0 @@
-```python
-def old_api():
-    pass
-```
"""
    workspace = _make_workspace(
        tmp_path,
        {
            "README.md": "# Docs\n\nUsage examples live elsewhere.\n",
            "service.py": "def old_api():\n    return 42\n",
        },
    )
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols


def test_definition_in_untouched_file_is_caught_by_workspace_scan(tmp_path: Path) -> None:
    """A definition living outside the diff still blocks certification via the bound workspace."""
    diff = """
--- a/client.py
+++ b/client.py
@@ -1,3 +1,3 @@
-import old_api
 pass
"""
    workspace = _make_workspace(
        tmp_path,
        {
            "client.py": "pass\n",
            "service.py": "def old_api():\n    return 42\n",
        },
    )
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE001-DELETE_FIRST_VIOLATION" for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols


def test_missing_source_file_in_bound_workspace_cannot_certify(tmp_path: Path) -> None:
    """A diff-touched source file missing from the bound workspace means evidence is insufficient."""
    diff = """
--- a/service.py
+++ b/service.py
@@ -1,3 +1,2 @@
-def old_api():
-    return 1
+pass
"""
    # Bound workspace does NOT contain service.py at all.
    workspace = _make_workspace(tmp_path, {"other.py": "x = 1\n"})
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE004-INSUFFICIENT_SOURCE_EVIDENCE" for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols


def test_same_diff_bound_to_different_workspaces_diverge(tmp_path: Path) -> None:
    """Identical diff plus different bound workspaces yields correct, receipt-recorded verdicts."""
    diff = """
--- a/service.py
+++ b/service.py
@@ -1,3 +1,2 @@
-def old_api():
-    return 1
+pass
"""
    basis = RetirementBasis(target_symbols=("old_api",))

    ws_clean = _make_workspace(tmp_path / "clean", {"service.py": "pass\n"})
    receipt_clean = evaluate_diff_hygiene(diff, basis, workspace=ws_clean)
    assert receipt_clean.verdict == "passed"
    assert receipt_clean.retired_symbols == ("old_api",)
    assert receipt_clean.source_evidence == "workspace_sources"
    assert receipt_clean.source_root == str(ws_clean)
    assert receipt_clean.basis_hash

    ws_dirty = _make_workspace(tmp_path / "dirty", {"service.py": "def old_api():\n    return 1\n"})
    receipt_dirty = evaluate_diff_hygiene(diff, basis, workspace=ws_dirty)
    assert receipt_dirty.verdict == "rejected"
    assert "old_api" not in receipt_dirty.retired_symbols
    assert receipt_dirty.source_root == str(ws_dirty)
    assert receipt_dirty.basis_hash == receipt_clean.basis_hash


def test_path_deleted_but_still_on_disk_is_contradiction(tmp_path: Path) -> None:
    """A deleted-in-diff path that still exists in the bound workspace is an evidence mismatch."""
    diff = """
--- a/api.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def f(): pass
"""
    basis = RetirementBasis(target_paths=("api.py",))

    ws_stale = _make_workspace(tmp_path / "stale", {"api.py": "def f(): pass\n"})
    receipt_stale = evaluate_diff_hygiene(diff, basis, workspace=ws_stale)
    assert receipt_stale.verdict == "rejected"
    assert any(f.rule_id == "AE004-INSUFFICIENT_SOURCE_EVIDENCE" for f in receipt_stale.findings)

    ws_clean = _make_workspace(tmp_path / "clean", {"keep.py": "x = 1\n"})
    receipt_clean = evaluate_diff_hygiene(diff, basis, workspace=ws_clean)
    assert receipt_clean.verdict == "passed"
    assert len(receipt_clean.findings) == 0


def test_receipt_without_basis_has_empty_provenance(tmp_path: Path) -> None:
    """Receipts without a declared basis carry empty basis_hash and diff_only evidence."""
    receipt = evaluate_diff_hygiene("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert receipt.basis_hash == ""
    assert receipt.source_root is None
    assert receipt.source_evidence == "diff_only"
    assert ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION == receipt.schema_version
    assert ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION == RetirementBasis().schema_version
    assert ANTI_ENTROPY_CAPABILITY_ID == "anti-entropy-governance"
    assert ANTI_ENTROPY_CATALOG_ENTRY["id"] == ANTI_ENTROPY_CAPABILITY_ID


def test_typescript_cross_file_reference_rejects_and_prevents_false_retirement(tmp_path: Path) -> None:
    """Verify cross-file TS/JS member access (api.old_api()) blocks retirement certification."""
    diff = """
--- a/service.mjs
+++ b/service.mjs
@@ -1,2 +1,2 @@
-export function old_api() { return 1; }
+export function new_api() { return 2; }
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    ws = _make_workspace(
        tmp_path / "ws_unmigrated",
        {
            "service.mjs": "export function new_api() { return 2; }\n",
            "client.mjs": "import * as api from './service.mjs';\nexport function run() { return api.old_api(); }\n",
        },
    )

    receipt = evaluate_diff_hygiene(diff, basis, workspace=ws)
    assert receipt.verdict == "rejected"
    assert any(
        f.rule_id == "AE002-ZOMBIE_SYMBOL_REINTRODUCED" and "client.mjs" in f.message
        for f in receipt.findings
    )
    assert receipt.retired_symbols == ()


def test_typescript_cross_file_reference_passed_after_caller_migrated(tmp_path: Path) -> None:
    """Verify paired positive case: retirement certifies after caller migrates away from old_api."""
    diff = """
--- a/service.mjs
+++ b/service.mjs
@@ -1,2 +1,2 @@
-export function old_api() { return 1; }
+export function new_api() { return 2; }
--- a/client.mjs
+++ b/client.mjs
@@ -2,1 +2,1 @@
-export function run() { return api.old_api(); }
+export function run() { return api.new_api(); }
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    ws = _make_workspace(
        tmp_path / "ws_migrated",
        {
            "service.mjs": "export function new_api() { return 2; }\n",
            "client.mjs": "import * as api from './service.mjs';\nexport function run() { return api.new_api(); }\n",
        },
    )

    receipt = evaluate_diff_hygiene(diff, basis, workspace=ws)
    assert receipt.verdict == "passed", f"findings: {receipt.findings}"
    assert receipt.retired_symbols == ("old_api",)


def test_workspace_scan_truncation_on_large_file_fails_with_ae004(tmp_path: Path) -> None:
    """Verify files exceeding scan size limits block certification with AE004 rather than false passing."""
    diff = """
--- a/service.py
+++ b/service.py
@@ -1,2 +0,0 @@
-def old_api():
-    return 1
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    # Oversized file > 2_000_000 bytes
    large_comment = "# " + ("A" * 100) + "\n"
    oversized_content = "def old_api():\n    return 42\n" + (large_comment * 20_500)

    ws = _make_workspace(
        tmp_path / "ws_large",
        {
            "service.py": "# cleanly removed old_api\n",
            "retained.py": oversized_content,
        },
    )

    receipt = evaluate_diff_hygiene(diff, basis, workspace=ws)
    assert receipt.verdict == "rejected"
    assert any(
        f.rule_id == "AE004-INSUFFICIENT_SOURCE_EVIDENCE" and "exceeds size limit" in f.message
        for f in receipt.findings
    )
    assert receipt.retired_symbols == ()


def test_workspace_scan_file_count_limit_fails_with_ae004(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify exceeding the workspace file-count limit blocks certification with AE004.

    A truncated scan cannot prove absence: even when the diff cleanly removes
    the target, an unvisited source file might still define it.
    """
    from loopx.capabilities.anti_entropy import oracles as ae_oracles

    monkeypatch.setattr(ae_oracles, "_MAX_WORKSPACE_FILES", 2)

    diff = """
--- a/service.py
+++ b/service.py
@@ -1,2 +0,0 @@
-def old_api():
-    return 1
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    ws = _make_workspace(
        tmp_path / "ws_many",
        {
            "service.py": "# cleanly removed old_api\n",
            "a.py": "alpha = 1\n",
            "b.py": "beta = 2\n",
            "c.py": "gamma = 3\n",
        },
    )

    receipt = evaluate_diff_hygiene(diff, basis, workspace=ws)
    assert receipt.verdict == "rejected"
    assert any(
        f.rule_id == "AE004-INSUFFICIENT_SOURCE_EVIDENCE"
        and "file count exceeded limit" in f.message
        for f in receipt.findings
    )
    assert receipt.retired_symbols == ()


def test_typescript_class_method_retirement() -> None:
    """Verify TypeScript class methods (public, async, etc.) are recognized when cleanly deleted."""
    diff = """
--- a/controller.ts
+++ b/controller.ts
@@ -1,8 +1,2 @@
 export class TaskController {
-    public async oldTaskMethod(taskId: string): Promise<void> {
-        return;
-    }
-    private helperCalc(): number { return 1; }
     newTaskMethod(): void {}
 }
"""
    basis = RetirementBasis(target_symbols=("oldTaskMethod", "helperCalc"))
    receipt = evaluate_diff_hygiene(diff, basis)
    assert any(f.rule_id == "AE004-INSUFFICIENT_SOURCE_EVIDENCE" for f in receipt.findings)
    assert receipt.verdict == "rejected"


def test_ghost_code_ignores_natural_language_and_headings() -> None:
    """Verify plain words / title headings in comments do not trigger ghost code false positives."""
    diff = """
--- a/module.py
+++ b/module.py
@@ -1,2 +1,6 @@
 def compute():
+    # Overview
+    # Architecture
+    # Design
+    # Summary
     return 1
"""
    receipt = evaluate_diff_hygiene(diff)
    assert receipt.verdict == "passed"
    assert len(receipt.findings) == 0


def test_typescript_parenthesized_expression_is_not_function_def() -> None:
    """Verify const x = (a + b) * c is not misclassified as a function definition."""
    diff = """
--- a/calc.ts
+++ b/calc.ts
@@ -1,2 +1,3 @@
 function run() {
+    const result = (a + b) * c;
 }
"""
    basis = RetirementBasis(target_symbols=("result",))
    receipt = evaluate_diff_hygiene(diff, basis)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id == "AE004-INSUFFICIENT_SOURCE_EVIDENCE" for f in receipt.findings)
    assert not any(f.rule_id == "AE002-ZOMBIE_SYMBOL_REINTRODUCED" for f in receipt.findings)


def test_typescript_template_literal_interpolation_detected_as_active_ref(tmp_path: Path) -> None:
    """Verify TS expressions inside `${...}` template interpolations are detected as active refs."""
    legacy_file = tmp_path / "legacy.ts"
    legacy_file.write_text("export function new_api() { return 1; }\n", encoding="utf-8")
    consumer_file = tmp_path / "consumer.ts"
    consumer_file.write_text("console.log(`${api.old_api()}`);\n", encoding="utf-8")

    diff = """
--- a/legacy.ts
+++ b/legacy.ts
@@ -1,4 +1,1 @@
-export function old_api() {
-    return 0;
-}
 export function new_api() { return 1; }
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=tmp_path)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id.startswith("AE002") and "consumer.ts" in f.message for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols


def test_typescript_url_line_with_code_detected_as_active_ref(tmp_path: Path) -> None:
    """Verify // inside string literals (e.g. URLs) does not strip code on the same line."""
    legacy_file = tmp_path / "legacy.ts"
    legacy_file.write_text("export function new_api() { return 1; }\n", encoding="utf-8")
    consumer_file = tmp_path / "consumer.ts"
    consumer_file.write_text(
        "const url = 'https://example.invalid'; console.log(api.old_api());\n",
        encoding="utf-8",
    )

    diff = """
--- a/legacy.ts
+++ b/legacy.ts
@@ -1,4 +1,1 @@
-export function old_api() {
-    return 0;
-}
 export function new_api() { return 1; }
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=tmp_path)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id.startswith("AE002") and "consumer.ts" in f.message for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols


def test_typescript_migrated_template_string_without_interpolation_passes(tmp_path: Path) -> None:
    """Verify plain string text inside backticks mentioning the old symbol does not block retirement."""
    legacy_file = tmp_path / "legacy.ts"
    legacy_file.write_text("export function new_api() { return 1; }\n", encoding="utf-8")
    consumer_file = tmp_path / "consumer.ts"
    consumer_file.write_text(
        "console.log(`Retired old_api successfully, now running new_api`);\n",
        encoding="utf-8",
    )

    diff = """
--- a/legacy.ts
+++ b/legacy.ts
@@ -1,4 +1,1 @@
-export function old_api() {
-    return 0;
-}
 export function new_api() { return 1; }
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=tmp_path)
    assert receipt.verdict == "passed"
    assert "old_api" in receipt.retired_symbols
    assert len(receipt.findings) == 0


def test_typescript_unclosed_syntax_reports_insufficient_evidence(tmp_path: Path) -> None:
    """Verify unclosed/unparseable JS/TS syntax reports AE004 rather than false pass."""
    legacy_file = tmp_path / "legacy.ts"
    legacy_file.write_text("export function new_api() { return 1; }\n", encoding="utf-8")
    broken_file = tmp_path / "broken.ts"
    broken_file.write_text("const x = `hello ${old_api;\n", encoding="utf-8")

    diff = """
--- a/legacy.ts
+++ b/legacy.ts
@@ -1,4 +1,1 @@
-export function old_api() {
-    return 0;
-}
 export function new_api() { return 1; }
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    receipt = evaluate_diff_hygiene(diff, basis, workspace=tmp_path)
    assert receipt.verdict == "rejected"
    assert any(f.rule_id.startswith("AE004") for f in receipt.findings)
    assert "old_api" not in receipt.retired_symbols

def test_typescript_static_computed_member_reference_blocks_retirement(
    tmp_path: Path,
) -> None:
    """A statically named computed member access is an active reference."""
    diff = """
--- a/service.mjs
+++ b/service.mjs
@@ -1,2 +1,2 @@
-export function old_api() { return 1; }
+export function new_api() { return 2; }
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    workspace = _make_workspace(
        tmp_path / "ws_unmigrated",
        {
            "service.mjs": "export function new_api() { return 2; }\n",
            "client.mjs": (
                "import * as api from './service.mjs';\n"
                "const first = api['old_api']();\n"
                'const second = api?.["old_api"]();\n'
            ),
        },
    )

    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "rejected"
    assert any(
        finding.rule_id == "AE002-ZOMBIE_SYMBOL_REINTRODUCED"
        and "client.mjs" in finding.message
        for finding in receipt.findings
    )
    assert receipt.retired_symbols == ()


def test_typescript_computed_member_migrated_and_harmless_strings_pass(
    tmp_path: Path,
) -> None:
    """Migrated computed calls and plain string/array text never block retirement."""
    diff = """
--- a/service.mjs
+++ b/service.mjs
@@ -1,2 +1,2 @@
-export function old_api() { return 1; }
+export function new_api() { return 2; }
--- a/client.mjs
+++ b/client.mjs
@@ -2,1 +2,3 @@
-export function run() { return api.old_api(); }
+export function run() { return api['new_api'](); }
+export const allowed = ['old_api'];
+export const message = "old_api has been removed";
"""
    basis = RetirementBasis(target_symbols=("old_api",))
    workspace = _make_workspace(
        tmp_path / "ws_migrated",
        {
            "service.mjs": "export function new_api() { return 2; }\n",
            "client.mjs": (
                "import * as api from './service.mjs';\n"
                "export function run() { return api['new_api'](); }\n"
                "export const allowed = ['old_api'];\n"
                'export const message = "old_api has been removed";\n'
            ),
        },
    )

    receipt = evaluate_diff_hygiene(diff, basis, workspace=workspace)
    assert receipt.verdict == "passed", f"findings: {receipt.findings}"
    assert receipt.retired_symbols == ("old_api",)
