"""§5 / §2: the decoder is a PURE package — no network, no DB, no clock, no
framework imports. `resources.py` is the single sanctioned exception.

Enforced by AST inspection rather than trust, because the guarantee's value is
that the same function runs identically in the API, the eval CLI and the tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "storyforge/decoder"

# Anything that makes the validator depend on the world outside its arguments.
FORBIDDEN_MODULES = {
    "socket", "urllib", "http", "requests", "httpx", "aiohttp",
    "sqlalchemy", "alembic", "psycopg", "asyncpg",
    "fastapi", "starlette", "uvicorn",
    "time", "datetime", "random", "os", "subprocess", "pathlib", "shutil",
    "cmudict",  # a build-time vendoring tool; never a runtime dependency
}
FORBIDDEN_CALLS = {"open"}

# resources.py is the ONLY module permitted to read the vendored artifacts.
IO_EXEMPT = {"resources.py"}

MODULES = sorted(p for p in PKG.glob("*.py"))


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_module_is_pure(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    exempt = path.name in IO_EXEMPT

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            names = []

        for name in names:
            if name in FORBIDDEN_MODULES:
                # importlib.resources in resources.py is the sanctioned reader.
                if exempt and name in {"importlib", "csv"}:
                    continue
                pytest.fail(f"{path.name} imports forbidden module {name!r}")

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS and not exempt:
                pytest.fail(f"{path.name} calls {node.func.id}() — I/O outside resources.py")


def test_resources_is_the_only_io_module():
    """Guards the exemption list itself: if someone adds a second I/O module they
    must justify it here rather than silently widening the surface."""
    assert IO_EXEMPT == {"resources.py"}


def test_decoder_imports_without_database_or_network():
    """A smoke check that importing the package touches nothing external."""
    import storyforge.decoder as d

    assert d.lexicon_size() > 100_000
    assert d.inventory_size() > 50
