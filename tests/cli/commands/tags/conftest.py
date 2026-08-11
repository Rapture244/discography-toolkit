# tests/cli/commands/tags/conftest.py
"""The artist folder the tag commands' tests are run against.

One fixture rather than one per module. Album, year and cover each built
the same "Miles Davis - [1 * 1F * 0L * 0M]" holding its albums under a
FLAC container, which is the shape every one of these commands walks --
and three copies of it is three places for it to drift from what the
commands actually expect.

A fixture rather than a helper taking `tmp_path`, so the modules already
asking for `artist` keep asking for it unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.helpers import silence

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture()
def artist(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an artist folder holding given albums.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking album names and a track count, returning the
        artist folder.
    """

    def build(*albums: str, tracks: int = 1) -> Path:
        root: Path = tmp_path / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
        for name in albums:
            folder: Path = root / "FLAC" / name
            folder.mkdir(parents=True)
            for index in range(tracks):
                silence(folder / f"{index + 1:02d}.flac")
        return root

    return build
