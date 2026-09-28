"""The stats engine must stay pure: no database, web, or I/O imports (PRD §14).

An allowlist rather than a denylist, so a new import has to be a deliberate choice.
"""

import ast
from pathlib import Path

import abtest.stats

ALLOWED_TOP_LEVEL = {"abc", "collections", "dataclasses", "enum", "math", "typing", "scipy"}
STATS_DIR = Path(abtest.stats.__file__).parent


def imported_modules(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(), filename=str(source))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_stats_imports_only_the_standard_math_stack() -> None:
    sources = sorted(STATS_DIR.glob("*.py"))
    assert sources, f"no modules found in {STATS_DIR}"

    for source in sources:
        for module in imported_modules(source):
            within_engine = module == "abtest.stats" or module.startswith("abtest.stats.")
            allowed = module.split(".")[0] in ALLOWED_TOP_LEVEL
            assert within_engine or allowed, f"{source.name} imports {module}"
