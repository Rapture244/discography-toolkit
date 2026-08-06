# tests/operations/test_discs.py
"""Tests for reading disc numbers and putting them on filenames.

The pass renames files and clears a tag, both on a judgement made from
what an album's tracks say between them. What matters most here is that
it is idempotent -- a second run must not stack "1.1." -- and that it
never guesses which disc a track belongs to, that number being the only
record left once the folders are flat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.core import metadata
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations import discs

import pytest
from tests.helpers import silence

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def album(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an album whose tracks carry disc numbers.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking the album's folder name and one disc number per
        track -- `None` writing no tag at all -- and returning the folder.
    """

    def build(name: str, *numbers: str | None) -> Path:
        folder: Path = tmp_path / name
        folder.mkdir(parents=True, exist_ok=True)
        for index, disc in enumerate(numbers, start=1):
            track: Path = folder / f"{index:02d} - Track.flac"
            silence(track)
            if disc is not None:
                metadata.write(track, {Tag.DISC: disc})
        return folder

    return build


# ==================================================================================== #
#                                      DECIDING                                        #
# ==================================================================================== #
def test_one_disc_is_cleared_from_every_track(album: Callable[..., Path]) -> None:
    """Saying an album has one disc tells nobody anything.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("01. (1959) - Kind of Blue [FLAC]", "1", "1")

    plan = discs.plan([folder], sorted(folder.glob("*.flac")))

    assert plan.clear == frozenset(folder.glob("*.flac"))
    assert plan.prefixes == ()
    assert plan.split == ()


def test_a_half_tagged_album_is_still_one_disc(album: Callable[..., Path]) -> None:
    """A half-tagged album is one disc, not two.

    Some tracks carrying "1" and the rest carrying nothing is one album
    with one disc, tagged carelessly. Treating it as split would leave it
    half-tagged forever, which is the inconsistency this exists to
    remove.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("01. (1959) - Kind of Blue [FLAC]", "1", None)

    plan = discs.plan([folder], sorted(folder.glob("*.flac")))

    assert len(plan.clear) == 1
    assert plan.split == ()


def test_an_album_with_no_disc_numbers_needs_nothing(album: Callable[..., Path]) -> None:
    """Nothing to clear where nothing was written.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("01. (1959) - Kind of Blue [FLAC]", None, None)

    plan = discs.plan([folder], sorted(folder.glob("*.flac")))

    assert plan.clear == frozenset()
    assert plan.prefixes == ()


def test_several_discs_keep_their_numbers(album: Callable[..., Path]) -> None:
    """The numbers are the only record of which disc a track came from.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("02. (1965) - Otis Blue [FLAC]", "1", "2")

    plan = discs.plan([folder], sorted(folder.glob("*.flac")))

    assert plan.clear == frozenset()
    assert [prefix.disc for prefix in plan.prefixes] == ["1", "2"]
    assert plan.split == ("'02. (1965) - Otis Blue [FLAC]' -- discs 1, 2",)


def test_a_disc_number_is_settled_without_padding(album: Callable[..., Path]) -> None:
    """A ripper writing "01" and "2" would prefix two widths on one album.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("02. (1965) - Otis Blue [FLAC]", "01", "2/2")

    plan = discs.plan([folder], sorted(folder.glob("*.flac")))

    assert [prefix.disc for prefix in plan.prefixes] == ["1", "2"]
    assert list(plan.settle.values()) == ["1", "2"]


def test_a_track_with_no_usable_disc_is_left_alone(album: Callable[..., Path]) -> None:
    """Guessing which disc it belongs to would scatter the album.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("02. (1965) - Otis Blue [FLAC]", "1", "2", "A")

    plan = discs.plan([folder], sorted(folder.glob("*.flac")))

    assert plan.unreadable == (folder / "03 - Track.flac",)
    assert len(plan.prefixes) == 2


# ==================================================================================== #
#                                     PREFIXING                                        #
# ==================================================================================== #
def test_applying_puts_the_disc_at_the_front(album: Callable[..., Path]) -> None:
    """A flat folder sorted by name has nothing else to go on.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("02. (1965) - Otis Blue [FLAC]", "1", "2")

    report = discs.apply(discs.plan([folder], sorted(folder.glob("*.flac"))))

    assert report.renamed == 2
    assert sorted(path.name for path in folder.glob("*.flac")) == [
        "1.01 - Track.flac",
        "2.02 - Track.flac",
    ]


def test_a_second_run_does_not_stack_the_prefix(album: Callable[..., Path]) -> None:
    """The whole hazard of prefixing: "1.1.01" on the second organize.

    The check is for this track's own number, not for any digit and a
    dot -- these filenames start with a track number already.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("02. (1965) - Otis Blue [FLAC]", "1", "2")
    _ = discs.apply(discs.plan([folder], sorted(folder.glob("*.flac"))))

    again = discs.plan([folder], sorted(folder.glob("*.flac")))
    report = discs.apply(again)

    assert again.pending == ()
    assert report.renamed == 0
    assert sorted(path.name for path in folder.glob("*.flac")) == [
        "1.01 - Track.flac",
        "2.02 - Track.flac",
    ]


def test_a_collision_is_refused_rather_than_overwritten(album: Callable[..., Path]) -> None:
    """Two discs holding a track of one name is why the prefix exists.

    Taking the name would destroy whichever file got there first, which
    is the very loss the prefix is meant to prevent.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("02. (1965) - Otis Blue [FLAC]", "1")
    # A second disc whose file already sits where the first one's prefix
    # would put it -- exactly the clash two discs of one album produce.
    blocker: Path = folder / "1.01 - Track.flac"
    silence(blocker)
    metadata.write(blocker, {Tag.DISC: "2"})

    report = discs.apply(discs.plan([folder], sorted(folder.glob("*.flac"))))

    assert (folder / "01 - Track.flac").is_file()
    assert len(report.failures) == 1
    assert "already there" in report.failures[0][1]
