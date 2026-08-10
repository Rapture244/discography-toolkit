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


# ==================================================================================== #
#                                      DUPLICATES                                      #
# ==================================================================================== #
def test_duplicates_groups_one_album_held_twice(shelf: Callable[..., Path]) -> None:
    """Two folders sharing a year and title are one album twice.

    Args:
        shelf: Factory making album folders.
    """
    first: Path = shelf("16. (2021) - The World Is Still Chaos [FLAC]")
    second: Path = shelf("17. (2021) - the world is still chaos [FLAC]")

    groups = pruning.duplicates([first, second])

    assert len(groups) == 1
    assert set(groups[0]) == {first, second}


def test_duplicates_finds_none_among_distinct_albums(shelf: Callable[..., Path]) -> None:
    """Different records are different, however alike they are indexed.

    Args:
        shelf: Factory making album folders.
    """
    first: Path = shelf("01. (1959) - Kind of Blue [FLAC]")
    second: Path = shelf("02. (1970) - Bitches Brew [FLAC]")

    assert pruning.duplicates([first, second]) == ()


def test_duplicates_settles_where_the_ep_marker_was_typed(
    shelf: Callable[..., Path],
) -> None:
    """Identity is the year and title; where the marker sat is not part of it.

    `album_title` lifts the marker from wherever it was written and puts
    it back in one place, so two folders marking the same EP differently
    resolve to one identity rather than two.

    Args:
        shelf: Factory making album folders.
    """
    first: Path = shelf("02. (2011) - Old Soul (EP) [FLAC]")
    second: Path = shelf("09. (2011) - EP Old Soul")

    assert len(pruning.duplicates([first, second])) == 1


def test_duplicates_tells_two_years_of_one_title_apart(shelf: Callable[..., Path]) -> None:
    """A rerecording is its own album, and the year is what says so.

    Args:
        shelf: Factory making album folders.
    """
    first: Path = shelf("01. (1959) - Kind of Blue [FLAC]")
    second: Path = shelf("02. (1997) - Kind of Blue [FLAC]")

    assert pruning.duplicates([first, second]) == ()


def test_duplicates_groups_three_copies_together(shelf: Callable[..., Path]) -> None:
    """One identity, one group, however many folders share it.

    Args:
        shelf: Factory making album folders.
    """
    copies: list[Path] = [
        shelf("01. (1959) - Kind of Blue [FLAC]"),
        shelf("02. (1959) - Kind of Blue"),
        shelf("03. (1959) - kind of blue [FLAC]"),
    ]

    groups = pruning.duplicates(copies)

    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_an_opus_copy_is_a_duplicate_too(shelf: Callable[..., Path]) -> None:
    """This only groups; excluding what pruning settles is the caller's job.

    Args:
        shelf: Factory making album folders.
    """
    opus_copy: Path = shelf("02. (1999) - Mapouka [OPUS]", ".opus")
    lossless: Path = shelf("01. (1999) - Mapouka [FLAC]", ".flac")

    assert len(pruning.duplicates([opus_copy, lossless])) == 1


def test_an_ep_is_not_the_album_that_shares_its_name(shelf: Callable[..., Path]) -> None:
    """An EP and an album of one year and name are two records, not one.

    Args:
        shelf: Factory making album folders.
    """
    ep: Path = shelf("03. (2013) - Summer Knights (EP) [FLAC]")
    album: Path = shelf("04. (2013) - Summer Knights [FLAC]")

    assert pruning.duplicates([ep, album]) == ()
