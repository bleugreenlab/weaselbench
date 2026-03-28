"""Python AST-based import detection."""

from __future__ import annotations

import ast
from pathlib import Path


def find_imports(source: str) -> set[str]:
    """Return all imported module names from Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def has_import(path: Path, target: str) -> bool:
    """Check if a Python file imports the target module."""
    source = path.read_text()
    imports = find_imports(source)
    return any(target in imp or imp.startswith(target + ".") for imp in imports)
