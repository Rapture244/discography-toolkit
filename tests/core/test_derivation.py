# tests/core/test_derivation.py
"""Tests for what each folder-derived tag should hold.

No filesystem: `owning_folder` reads a path's parents rather than the
disk, so an album folder here is a path that need never exist. That is
what lets these run as unit tests over the rules themselves, where the
command tests exercise the same rules through real files and a runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.core.derivation import album_artist_of, album_of, date_of

import pytest

if TYPE_CHECKING:
    from pathlib import Path


# ==================================================================================== #
#                                        ALBUM                                         #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("01. (1959) - Kind of Blue [FLAC]", "Kind of Blue"),
        # Everything describing this shelf comes off: pin, index, marker,
        # quality tag. What leaves here should say what the album is.
        ("\u00a927. (1977) - October [OPUS]", "October"),
        ("05. (1980) - M - Lost Record", "Lost Record"),
        ("12. (1980) - \u26a0 - Decoy", "Decoy"),
        ("00. Singles", "Singles"),
        # An "(EP)" is not one of those things. It says what the release
        # is rather than where it sits, and no other tag written here
        # could carry it.
        ("03. (2013) - Summer Knights (EP) [FLAC]", "Summer Knights (EP)"),
    ],
)
def test_album_of_reads_the_title(tmp_path: Path, folder: str, expected: str) -> None:
    """The Album tag is the title alone, past the shelf's own bookkeeping.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        folder: The album folder's name.
        expected: The title that should be written.
    """
    album: Path = tmp_path / folder

    assert album_of(album / "01.flac", [album]) == expected


def test_album_of_reaches_through_a_disc_folder(tmp_path: Path) -> None:
    """A track one disc down still belongs to the album above it.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "01. (1970) - Bitches Brew [FLAC]"

    assert album_of(album / "CD 2" / "01.flac", [album]) == "Bitches Brew"


def test_album_of_picks_the_owning_album(tmp_path: Path) -> None:
    """With several albums in scope, a track takes the one holding it.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    first: Path = tmp_path / "01. (1959) - Alpha [FLAC]"
    second: Path = tmp_path / "02. (1970) - Bravo [FLAC]"

    assert album_of(second / "01.flac", [first, second]) == "Bravo"


def test_album_of_is_none_under_no_album(tmp_path: Path) -> None:
    """Nothing to take a name from means nothing to write.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "01. (1959) - Kind of Blue [FLAC]"

    assert album_of(tmp_path / "loose.flac", [album]) is None


# ==================================================================================== #
#                                         DATE                                         #
# ==================================================================================== #
def test_date_of_reads_the_year(tmp_path: Path) -> None:
    """The Date comes from the year in the album folder's name.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "01. (1959) - Kind of Blue [FLAC]"

    assert date_of(album / "01.flac", [album]) == "1959"


def test_date_of_prefers_the_release_over_a_reissue(tmp_path: Path) -> None:
    """A year in its own brackets beside the title is what it reissues.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "90. (2013) - In India [1973] [FLAC]"

    assert date_of(album / "01.flac", [album]) == "2013"


def test_date_of_clears_an_approximate_year(tmp_path: Path) -> None:
    """An approximation is a value -- the empty one -- not a missing answer.

    "199x" is not a date, so the field is cleared rather than filled with
    a non-date. The distinction from `None` matters to every caller: an
    empty string is written, `None` leaves the tag alone.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "13. (199x) - Unknown Decade [FLAC]"

    value: str | None = date_of(album / "01.flac", [album])

    assert value == ""
    assert value is not None


@pytest.mark.parametrize(
    ("folder", "track"),
    [
        # The album is found, but its name carries no year to read.
        ("03. - No Year Here [FLAC]", "03. - No Year Here [FLAC]/01.flac"),
        # No album folder holds the track at all.
        ("01. (1959) - Kind of Blue [FLAC]", "loose.flac"),
    ],
)
def test_date_of_is_none_without_a_year_to_read(tmp_path: Path, folder: str, track: str) -> None:
    """Two ways to have no year, and both leave the tag untouched.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        folder: The album folder in scope.
        track: The track, relative to the temporary directory.
    """
    album: Path = tmp_path / folder

    assert date_of(tmp_path / track, [album]) is None


# ==================================================================================== #
#                                     ALBUM ARTIST                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("Miles Davis - [65 \u2022 65F \u2022 0L \u2022 0M]", "Miles Davis"),
        # A prefix the name genuinely carries survives: splitting on " - "
        # would cut this to "(Nigeria)".
        ("(Nigeria) - Fela Kuti - [49 \u2022 49F \u2022 0L \u2022 0M]", "(Nigeria) - Fela Kuti"),
        # An older label form is stripped the same way.
        ("Charlie Mariano - [M31 on 90]", "Charlie Mariano"),
    ],
)
def test_album_artist_of_strips_the_count_label(tmp_path: Path, folder: str, expected: str) -> None:
    """The discography a track belongs to, without the shelf's own tally.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        folder: The artist folder's name.
        expected: The name that should be written.
    """
    artist: Path = tmp_path / folder
    track: Path = artist / "FLAC" / "01. (1959) - Kind of Blue [FLAC]" / "01.flac"

    assert album_artist_of(track, [artist]) == expected


def test_album_artist_of_is_none_under_no_artist(tmp_path: Path) -> None:
    """Guessing a name from an unlabelled parent would invent a discography.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"

    assert album_artist_of(tmp_path / "Unsorted" / "loose.flac", [artist]) is None


def test_album_artist_of_is_none_when_the_folder_carries_no_label(tmp_path: Path) -> None:
    """A folder found but unlabelled has no agreed value to derive.

    Unreachable from the commands, which feed this `find_artist_folders`
    and so only ever see labelled folders. It is reachable by anything
    that passes its own list, and an unlabelled name is exactly what
    `strip_artist_label` refuses to guess at.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"

    assert album_artist_of(artist / "01. (1959) - X" / "01.flac", [artist]) is None
