# tests/operations/test_pruning.py
"""Tests for the pruning operation.

Pruning is the toolkit's one deletion, so these lean on the boundary it
draws: an Opus album is removed only when a lossless album shares its
identity, and everything else -- a lone Opus, a lossless album, a title
that merely resembles another -- is left untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.operations import pruning

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def shelf(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory making an album folder holding one file of a format.

    The tier is read off the extension, so an empty file of the right
    suffix is enough to stand for a FLAC or an Opus album.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking a folder name and an extension, returning the
        album folder.
    """

    def build(name: str, suffix: str = ".flac") -> Path:
        album: Path = tmp_path / name
        album.mkdir(parents=True, exist_ok=True)
        _ = (album / f"01{suffix}").write_bytes(b"")
        return album

    return build


# ==================================================================================== #
#                                       MATCHING                                       #
# ==================================================================================== #
def test_an_opus_album_with_a_flac_twin_is_pruned(shelf: Callable[..., Path]) -> None:
    """The same album in both formats: the Opus copy is marked to delete.

    Args:
        shelf: Factory making album folders.
    """
    opus: Path = shelf("02. (1999) - Mapouka [OPUS]", ".opus")
    flac: Path = shelf("01. (1999) - Mapouka [FLAC]", ".flac")

    result = pruning.plan([opus, flac])

    assert [prune.album for prune in result.prunes] == [opus]
    assert result.prunes[0].twin == flac


def test_an_opus_album_without_a_twin_is_left_alone(shelf: Callable[..., Path]) -> None:
    """An Opus-only album has nothing to regenerate it, so it stays.

    Args:
        shelf: Factory making album folders.
    """
    opus: Path = shelf("09. (2020) - Opus Only [OPUS]", ".opus")

    assert pruning.plan([opus]).prunes == ()


def test_a_lossless_album_is_never_pruned(shelf: Callable[..., Path]) -> None:
    """Two lossless albums, even sharing an identity, are both kept.

    Args:
        shelf: Factory making album folders.
    """
    first: Path = shelf("01. (1999) - Mapouka [FLAC]", ".flac")
    second: Path = shelf("02. (1999) - Mapouka", ".flac")

    assert pruning.plan([first, second]).prunes == ()


def test_the_match_ignores_index_pin_and_tag(shelf: Callable[..., Path]) -> None:
    """A twin is found past the index, the pin and the quality tag.

    Args:
        shelf: Factory making album folders.
    """
    opus: Path = shelf("Mapouka [OPUS]", ".opus")  # no index
    flac: Path = shelf("©05. (1999) - Mapouka [FLAC]", ".flac")  # pinned, indexed
    opus_dated: Path = shelf("(1999) - Mapouka [OPUS]", ".opus")

    # The undated Opus does not match the dated FLAC; the dated one does.
    result = pruning.plan([opus, flac, opus_dated])

    assert [prune.album for prune in result.prunes] == [opus_dated]


def test_a_different_year_is_a_different_album(shelf: Callable[..., Path]) -> None:
    """Same title, different year: two releases, and the Opus stays.

    Args:
        shelf: Factory making album folders.
    """
    opus: Path = shelf("(1999) - Live [OPUS]", ".opus")
    flac: Path = shelf("(2005) - Live [FLAC]", ".flac")

    assert pruning.plan([opus, flac]).prunes == ()


def test_a_different_title_is_a_different_album(shelf: Callable[..., Path]) -> None:
    """Same year, different title: not the same record, so the Opus stays.

    Args:
        shelf: Factory making album folders.
    """
    opus: Path = shelf("(1999) - Alpha [OPUS]", ".opus")
    flac: Path = shelf("(1999) - Beta [FLAC]", ".flac")

    assert pruning.plan([opus, flac]).prunes == ()


# ==================================================================================== #
#                                       DELETING                                       #
# ==================================================================================== #
def test_applying_deletes_the_opus_folder(shelf: Callable[..., Path]) -> None:
    """The pruned folder and its contents are gone; the twin remains.

    Args:
        shelf: Factory making album folders.
    """
    opus: Path = shelf("02. (1999) - Mapouka [OPUS]", ".opus")
    flac: Path = shelf("01. (1999) - Mapouka [FLAC]", ".flac")

    report = pruning.apply(pruning.plan([opus, flac]))

    assert report.deleted == 1
    assert not opus.exists()
    assert flac.exists()


def test_nothing_to_prune_deletes_nothing(shelf: Callable[..., Path]) -> None:
    """A clean artist loses no folders.

    Args:
        shelf: Factory making album folders.
    """
    flac: Path = shelf("01. (1999) - Mapouka [FLAC]", ".flac")

    report = pruning.apply(pruning.plan([flac]))

    assert report.deleted == 0
    assert flac.exists()
