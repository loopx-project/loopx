"""Experiment-local SForge selection, before importing the native provider.

This research adapter owns a dependency choice, not scoring or LoopX policy.
One atomic source directory freezes the worker/judge cohort's revision.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

DEFAULT_FILE = Path(__file__).with_name("source-default.json")
STATE_DIR = ".edgebench-source"
RECEIPT_ENV = "LOOPX_EDGEBENCH_SOURCE_RECEIPT"
FROZEN_PATCH = "compatibility.patch"
_DIFF_ARGS = ("--no-ext-diff", "--no-textconv", "--binary", "--full-index",
              "--no-color", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/",
              "--inter-hunk-context=0", "-O/dev/null",
              "--diff-algorithm=myers", "--indent-heuristic")
# Keep the original v2 cohort representation verifiable without its current asset.
# New assets use Git's compact context format; neither form relaxes byte equality.
_DIFF_FORMATS = (("true", "--unified=1"), ("false", "--unified=3"))


def _git_bytes(checkout: Path, *args: str, input: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(checkout), *args],
                                capture_output=True, input=input, timeout=180,
                                env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"})
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"EdgeBench git {args[0]} timed out") from None
    if result.returncode:
        # A transport error can contain a credential-bearing remote URL.
        raise RuntimeError(f"EdgeBench git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def _git(checkout: Path, *args: str) -> str:
    return _git_bytes(checkout, *args).decode().strip()


def _patch_identity(value: object) -> bool:
    return (isinstance(value, dict)
            and set(value) == {"id", "sha256"}
            and isinstance(value["id"], str)
            and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value["id"]) is not None
            and isinstance(value["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)


def _safe_path(value: object) -> bool:
    return (isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value) is not None
            and all(part not in {".", ".."} and part.lower() != ".git"
                    for part in value.split("/")))


def _bundled_patch(value: object) -> tuple[dict, bytes]:
    if (not isinstance(value, dict) or set(value) != {"id", "sha256", "relative_path"}
            or not _safe_path(value.get("relative_path"))):
        raise ValueError("Invalid default EdgeBench compatibility patch")
    identity = {key: value[key] for key in ("id", "sha256")}
    root = DEFAULT_FILE.parent.resolve()
    path = root / value["relative_path"]
    if not _patch_identity(identity) or path.resolve() != path or not path.is_file():
        raise ValueError("Invalid default EdgeBench compatibility patch")
    patch = path.read_bytes()
    if hashlib.sha256(patch).hexdigest() != identity["sha256"]:
        raise ValueError("Default EdgeBench compatibility patch digest differs")
    return identity, patch


def _check_preimages(checkout: Path, upstream: str, patch: bytes) -> None:
    """Accept a full-index patch only against its exact regular-file preimages."""
    blocks = re.split(rb"(?m)^diff --git ", patch)
    if blocks[0] or len(blocks) == 1 or not patch.endswith(b"\n"):
        raise ValueError("Invalid EdgeBench compatibility patch format")
    paths = set()
    for block in blocks[1:]:
        header = block.split(b"\n@@", 1)[0].split(b"\nGIT binary patch", 1)[0]
        lines = header.splitlines()
        names = re.fullmatch(rb"a/(\S+) b/(\S+)", lines[0])
        indexes = re.findall(rb"(?m)^index ([0-9a-f]{40})\.\.([0-9a-f]{40})(?: (100644|100755))?$", header)
        if names is None or names[1] != names[2] or len(indexes) != 1:
            raise ValueError("Invalid EdgeBench compatibility patch file header")
        try:
            path = names[1].decode("ascii")
        except UnicodeDecodeError:
            raise ValueError("Invalid EdgeBench compatibility patch path") from None
        if not _safe_path(path) or path in paths:
            raise ValueError("Invalid EdgeBench compatibility patch path")
        paths.add(path)
        before, after, mode = (part.decode() for part in indexes[0])
        entry = _git(checkout, "ls-tree", upstream, "--", path)
        if before == "0" * 40:
            matches = (not entry and after != "0" * 40
                       and re.search(rb"(?m)^new file mode (100644|100755)$", header))
        else:
            old_mode = mode or next((line.removeprefix(b"deleted file mode ").decode()
                                    for line in lines if line.startswith(b"deleted file mode ")), "")
            matches = (old_mode in {"100644", "100755"}
                       and entry == f"{old_mode} blob {before}\t{path}")
        if not matches:
            raise ValueError("EdgeBench compatibility patch preimage differs; use a new reviewed patch "
                             "or explicitly select an unmodified source")


def _commit_payload(checkout: Path, upstream: str, tree: str, identity: dict) -> bytes:
    epoch = _git(checkout, "show", "-s", "--format=%ct", upstream)
    if not re.fullmatch(r"[0-9]+", epoch):
        raise ValueError("Invalid EdgeBench upstream commit time")
    author = f"LoopX Compatibility <loopx-compatibility@example.invalid> {epoch} +0000"
    return (f"tree {tree}\nparent {upstream}\nauthor {author}\ncommitter {author}\n\n"
            f"LoopX compatibility: {identity['id']}\n\n"
            f"Upstream-Commit: {upstream}\nPatch-SHA256: {identity['sha256']}\n").encode()


def _validate_patched(state: Path, receipt: dict) -> None:
    identity = receipt.get("compatibility_patch")
    if (set(receipt) != {"schema_version", "repository", "ref", "commit",
                        "upstream_commit", "compatibility_patch"}
            or not isinstance(receipt.get("upstream_commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", receipt["upstream_commit"])
            or not _patch_identity(identity)):
        raise ValueError("Invalid patched EdgeBench source receipt")
    path = state / FROZEN_PATCH
    if path.is_symlink() or not path.is_file():
        raise ValueError("Frozen EdgeBench compatibility patch is missing or changed")
    patch = path.read_bytes()
    if hashlib.sha256(patch).hexdigest() != identity["sha256"]:
        raise ValueError("Frozen EdgeBench compatibility patch digest differs")
    checkout, upstream, commit = state / "checkout", receipt["upstream_commit"], receipt["commit"]
    _check_preimages(checkout, upstream, patch)
    if not any(_git_bytes(checkout, "-c", f"diff.suppressBlankEmpty={blank}", "diff",
                          *_DIFF_ARGS, context, upstream, commit, "--") == patch
               for blank, context in _DIFF_FORMATS):
        raise ValueError("Frozen EdgeBench source differs from its compatibility patch")
    tree = _git(checkout, "rev-parse", f"{commit}^{{tree}}")
    payload = _commit_payload(checkout, upstream, tree, identity)
    expected = hashlib.sha1(b"commit " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
    if commit != expected or _git_bytes(checkout, "cat-file", "commit", commit) != payload:
        raise ValueError("Frozen EdgeBench compatibility commit differs from its provenance")


def _apply_patch(state: Path, upstream: str, identity: dict, patch: bytes) -> str:
    checkout = state / "checkout"
    _check_preimages(checkout, upstream, patch)
    _git_bytes(checkout, "apply", "--check", "--index", "--whitespace=error", "-", input=patch)
    _git_bytes(checkout, "apply", "--index", "--whitespace=error", "-", input=patch)
    tree = _git(checkout, "write-tree")
    payload = _commit_payload(checkout, upstream, tree, identity)
    # A deterministic dependency snapshot, with no hooks, signing or ambient author identity.
    commit = _git_bytes(checkout, "hash-object", "-t", "commit", "-w", "--stdin", input=payload).decode().strip()
    _git(checkout, "checkout", "--quiet", "--detach", commit)
    (state / FROZEN_PATCH).write_bytes(patch)
    return commit


def _validate(state: Path) -> dict:
    receipt = json.loads((state / "receipt.json").read_text())
    if (not isinstance(receipt, dict)
            or receipt.get("schema_version") not in {"edgebench_source_v1", "edgebench_source_v2"}
            or not isinstance(receipt.get("commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", receipt["commit"])
            or not all(isinstance(receipt.get(k), str) and receipt[k]
                       for k in ("repository", "ref"))):
        raise ValueError("Invalid EdgeBench source receipt; preserve it and use a new log directory")
    checkout = state / "checkout"
    if (_git(checkout, "rev-parse", "HEAD") != receipt["commit"]
            or _git(checkout, "remote", "get-url", "origin") != receipt["repository"]
            or _git(checkout, "status", "--porcelain", "--untracked-files=normal")):
        raise ValueError("Frozen EdgeBench source changed; restore it or use a new log directory")
    if not (checkout / "sforge/__init__.py").is_file():
        raise ValueError("Selected source does not contain the SForge package")
    if receipt["schema_version"] == "edgebench_source_v2":
        _validate_patched(state, receipt)
    elif (state / FROZEN_PATCH).exists() or {"upstream_commit", "compatibility_patch"} & receipt.keys():
        raise ValueError("Patched EdgeBench source requires its original v2 receipt")
    return receipt


def prepare_source(log_dir: Path, *, repository: str | None = None,
                   ref: str | None = None, revision: str | None = None) -> tuple[Path, dict]:
    """Resolve once; an existing cohort can be reused offline, never refreshed."""
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("--sforge-revision requires a full lowercase commit SHA")
    if ref is not None and revision is not None:
        raise ValueError("Select either --sforge-ref or --sforge-revision")
    if ref is not None and (not ref or ref.startswith("-")):
        raise ValueError("--sforge-ref must be a nonempty Git reference")
    if repository is not None and ref is None and revision is None:
        raise ValueError("An alternate repository requires --sforge-ref or --sforge-revision")
    default_selection = repository is None and ref is None and revision is None
    log_dir = log_dir.resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    state = log_dir / STATE_DIR
    with (log_dir / f"{STATE_DIR}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if state.exists():
            receipt = _validate(state)
            for key, requested in (("repository", repository), ("ref", ref), ("commit", revision)):
                if requested is not None and receipt[key] != requested:
                    raise ValueError("EdgeBench selection conflicts with this cohort; use a new log directory")
            return state / "checkout", receipt
        if (log_dir / "runs").exists() and any((log_dir / "runs").iterdir()):
            raise ValueError("Existing runs have no source lock; preserve them and use a new log directory")
        defaults = json.loads(DEFAULT_FILE.read_text())
        compatibility = (_bundled_patch(defaults["compatibility_patch"])
                         if default_selection and "compatibility_patch" in defaults else None)
        repository = repository or defaults["repository"]
        ref = ref or (revision if revision else defaults["ref"])
        with tempfile.TemporaryDirectory(prefix=".edgebench-source-", dir=log_dir) as temporary:
            staged = Path(temporary) / "source"
            checkout = staged / "checkout"
            checkout.mkdir(parents=True)
            _git(checkout, "init", "--quiet")
            _git(checkout, "remote", "add", "origin", repository)
            _git(checkout, "fetch", "--quiet", "--depth=1", "--", "origin", revision or ref)
            commit = _git(checkout, "rev-parse", "FETCH_HEAD^{commit}")
            if revision is not None and commit != revision:
                raise ValueError("Fetched EdgeBench commit differs from the requested revision")
            _git(checkout, "checkout", "--quiet", "--detach", commit)
            receipt = dict(schema_version="edgebench_source_v1", repository=repository,
                           ref=ref, commit=commit)
            if compatibility is not None:
                identity, patch = compatibility
                receipt.update(schema_version="edgebench_source_v2", upstream_commit=commit,
                               compatibility_patch=identity,
                               commit=_apply_patch(staged, commit, identity, patch))
            (staged / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            _validate(staged)
            staged.rename(state)
        return state / "checkout", receipt


def bootstrap(*, keep_log_arg: bool = True) -> None:
    """CLI-only bootstrap; imported Python APIs retain their explicit environment."""
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False,
        description="Select and freeze the shared EdgeBench source for a log directory.")
    parser.add_argument("--log-dir", type=Path, default=os.environ.get("SFORGE_LOG_DIR"))
    parser.add_argument("--sforge-repository")
    parser.add_argument("--sforge-ref")
    parser.add_argument("--sforge-revision")
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        parser.print_help()
        return
    args, remaining = parser.parse_known_args()
    if args.log_dir is None:
        parser.error("provide --log-dir or SFORGE_LOG_DIR to freeze the EdgeBench source")
    try:
        checkout, _ = prepare_source(args.log_dir, repository=args.sforge_repository,
                                     ref=args.sforge_ref, revision=args.sforge_revision)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    sys.path.insert(0, str(checkout))
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (str(checkout), os.environ.get("PYTHONPATH"))))
    os.environ["SFORGE_LOG_DIR"] = str(args.log_dir.resolve())
    os.environ[RECEIPT_ENV] = str(checkout.parent / "receipt.json")
    sys.argv[1:] = remaining + (["--log-dir", str(args.log_dir)] if keep_log_arg else [])
    active_source_receipt()


def active_source_receipt() -> dict | None:
    """Prove the module Python will load belongs to the frozen source."""
    path = os.environ.get(RECEIPT_ENV)
    if not path:
        return None  # Legacy imported API: the embedding caller owns its source.
    state = Path(path).resolve().parent
    receipt = _validate(state)
    spec = importlib.util.find_spec("sforge")
    if spec is None or not spec.origin or Path(spec.origin).resolve() != state / "checkout/sforge/__init__.py":
        raise ValueError("Imported SForge differs from the experiment's source receipt")
    return receipt


if __name__ == "__main__":
    # Prepare dependencies without launching a worker, judge or model call.
    bootstrap(keep_log_arg=False)
    if "--help" not in sys.argv[1:] and "-h" not in sys.argv[1:]:
        print(json.dumps(active_source_receipt(), indent=2))
