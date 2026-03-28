"""JS/TS import detection via regex.

Handles:
- import X from 'target' / import { X } from 'target'
- import('target')
- require('target')
- export { X } from 'target'

Known limitation: does not skip comments or string literals.
Tree-sitter upgrade path for v1 when deeper analysis is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

IMPORT_PATTERNS = [
    re.compile(r"""(?:import|export)\s+.*?\s+from\s+['"](.+?)['"]"""),
    re.compile(r"""(?:import|export)\s*\(?\s*['"](.+?)['"]"""),
    re.compile(r"""require\s*\(\s*['"](.+?)['"]"""),
]


def find_imports(source: str) -> set[str]:
    """Return all imported module specifiers from JS/TS source."""
    modules: set[str] = set()
    for pattern in IMPORT_PATTERNS:
        modules.update(pattern.findall(source))
    return modules


def has_import(path: Path, target: str) -> bool:
    """Check if a JS/TS file imports the target module."""
    source = path.read_text()
    imports = find_imports(source)
    return any(target in imp or imp.startswith(target + "/") for imp in imports)
