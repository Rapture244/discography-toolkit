# tests/test_conventions.py
"""Project-wide conventions that are easy to break and easy to check."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parent.parent

MODULES: list[Path] = sorted(
    path
    for folder in ("src", "tests")
    for path in (ROOT / folder).rglob("*.py")
    if "__pycache__" not in path.parts
)


def _module_id(module: Path) -> str:
    """Name a parametrized case after the file it checks.

    Args:
        module: The module under test.

    Returns:
        Its path relative to the repository root.
    """
    return module.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("module", MODULES, ids=_module_id)
def test_every_module_states_its_own_path(module: Path) -> None:
    """Line one is the file's path from the repository root.

    Files are moved and renamed often enough that a stale stamp is worse
    than none: it says with confidence where a file is not.

    Args:
        module: The module to check.
    """
    expected: str = f"# {module.relative_to(ROOT).as_posix()}"
    lines: list[str] = module.read_text(encoding="utf-8").splitlines()

    assert lines, "file is empty"
    assert lines[0] == expected
