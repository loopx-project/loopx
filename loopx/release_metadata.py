"""Static source identity without bootstrapping the candidate package."""

from __future__ import annotations

import ast
from pathlib import Path
import tomllib


def checkout_release_metadata(project_root: Path) -> dict[str, str]:
    """Read build identity without importing or executing the candidate package."""
    pyproject_path = project_root / "pyproject.toml"
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = payload.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"missing [project] table in {pyproject_path}")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError("project.name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ValueError("project.version must be a non-empty string")
    module_path = project_root / "loopx" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if value.value != version:
                raise ValueError(
                    f"version mismatch: pyproject.toml={version!r}, loopx.__version__={value.value!r}"
                )
            return {"name": name, "version": version}
    raise ValueError(f"missing literal __version__ in {module_path}")
