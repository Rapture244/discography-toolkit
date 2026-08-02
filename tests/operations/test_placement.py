# tests/operations/test_placement.py
"""Tests for filing albums on the correct side of the FLAC container.

Run against real folders. Tiers are made real where they decide a move --
a silent FLAC for lossless, a stub MP3 for lossy, an empty folder for
missing -- since placement reads the files, not the names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.core.layout import AudioTier
from discography_toolkit.operations import placement
from discography_toolkit.operations.placement import ContainerChange, Side, TooManyContainersError

import pytest
from tests.helpers import fill, subfolders

if TYPE_CHECKING:
    from pathlib import Path


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def artist(tmp_path: Path) -> Path:
    """An artist folder to build albums under.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        The artist folder's path.
    """
    folder: Path = tmp_path / "Miles Davis"
    folder.mkdir()
    return folder


# ==================================================================================== #
#                                       PLACEMENT                                      #
# ==================================================================================== #
def test_a_lossless_album_in_the_root_moves_in(artist: Path) -> None:
    """A FLAC album sitting in the root belongs in the container.

    Args:
        artist: The artist folder.
    """
    album: Path = fill(artist / "01. (1959) - Kind of Blue", "lossless")
    _ = fill(artist / "02. (1990) - Lossy", "lossy")  # makes a mix, so a container is wanted

    result = placement.plan(artist)

    moved = next(p for p in result.placements if p.album == album)
    assert moved.side is Side.IN
    assert moved.destination == result.working_container / album.name


def test_a_lossy_album_in_the_container_moves_out(artist: Path) -> None:
    """An MP3 album inside the container belongs back in the root.

    Args:
        artist: The artist folder.
    """
    album: Path = fill(artist / "FLAC" / "01. (1970) - Live", "lossy")

    result = placement.plan(artist)

    moved = next(p for p in result.placements if p.album == album)
    assert moved.side is Side.OUT
    assert moved.destination == artist / album.name


def test_an_opus_album_is_treated_as_not_lossless(artist: Path) -> None:
    """Opus is held but not lossless, so it lives in the root, not the container.

    Args:
        artist: The artist folder.
    """
    album: Path = fill(artist / "FLAC" / "01. (1972) - On the Corner", "opus")

    moved = next(p for p in placement.plan(artist).placements if p.album == album)

    assert moved.tier is AudioTier.OPUS
    assert moved.side is Side.OUT


def test_a_missing_placeholder_stays_in_the_root(artist: Path) -> None:
    """An empty folder is not held, so it belongs in the root, and is there.

    Args:
        artist: The artist folder.
    """
    album: Path = artist / "01. (1980) - M - Missing"
    album.mkdir()

    moved = next(p for p in placement.plan(artist).placements if p.album == album)

    assert moved.tier is AudioTier.NONE
    assert moved.side is Side.KEEP


def test_albums_already_on_the_right_side_are_kept(artist: Path) -> None:
    """A lossless album in the container and a lossy one in the root: no moves.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - Kept Lossless", "lossless")
    _ = fill(artist / "02. (1990) - Kept Lossy", "lossy")

    result = placement.plan(artist)

    assert result.moving_in == ()
    assert result.moving_out == ()
    assert result.kept == 2


def test_placement_reads_the_files_not_the_name(artist: Path) -> None:
    """A folder named "[FLAC]" holding MP3s is lossy, and moves out.

    Args:
        artist: The artist folder.
    """
    album: Path = fill(artist / "FLAC" / "01. (1990) - Mislabelled [FLAC]", "lossy")

    moved = next(p for p in placement.plan(artist).placements if p.album == album)

    assert moved.side is Side.OUT


# ==================================================================================== #
#                                      CONTAINER                                       #
# ==================================================================================== #
def test_the_container_is_created_for_a_first_lossless_album(artist: Path) -> None:
    """A lossless album with no container yet calls for one to be made.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Kind of Blue", "lossless")
    _ = fill(
        artist / "02. (1990) - Lossy", "lossy"
    )  # the non-lossless the container separates from

    result = placement.plan(artist)

    assert result.container is None
    assert result.container_change is ContainerChange.CREATE


def test_an_older_container_name_is_normalised(artist: Path) -> None:
    """A "FLAC - (56 on 65)" is recognised and slated for renaming.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC - (56 on 65)" / "01. (1959) - Kept", "lossless")
    _ = fill(artist / "02. (1990) - Lossy", "lossy")  # keeps the container earning its place

    result = placement.plan(artist)

    assert result.container_change is ContainerChange.RENAME


def test_an_empty_container_is_removed_when_nothing_is_lossless(artist: Path) -> None:
    """With no lossless album left, the container should go, leaving the root flat.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1990) - Lossy", "lossy")
    (artist / "FLAC").mkdir()

    result = placement.plan(artist)

    assert result.container_change is ContainerChange.REMOVE


def test_a_correct_container_needs_no_change(artist: Path) -> None:
    """A bare "FLAC" holding the lossless albums is already right.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - Kept", "lossless")
    _ = fill(artist / "02. (1990) - Lossy", "lossy")  # the non-lossless it separates from

    assert placement.plan(artist).container_change is None


def test_an_all_lossless_artist_wants_no_container(artist: Path) -> None:
    """With every album lossless there is nothing to separate, so no container.

    The albums stay flat under the artist rather than gathering in a
    container of their own.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Kind of Blue", "lossless")
    _ = fill(artist / "02. (1970) - Bitches Brew", "lossless")

    result = placement.plan(artist)

    assert result.container_change is None
    assert result.moving_in == ()
    assert result.kept == 2


def test_an_all_lossless_artist_has_its_container_dissolved(artist: Path) -> None:
    """A container left from when a lossy album existed is taken back down.

    Its lossless albums move out to the root and the container is removed.

    Args:
        artist: The artist folder.
    """
    inside: Path = fill(artist / "FLAC" / "01. (1959) - Kind of Blue", "lossless")

    result = placement.plan(artist)

    assert result.container_change is ContainerChange.REMOVE
    moved = next(p for p in result.placements if p.album == inside)
    assert moved.side is Side.OUT
    assert moved.destination == artist / inside.name


def test_two_containers_are_refused(artist: Path) -> None:
    """Two containers cannot be merged without guessing, so planning refuses.

    Args:
        artist: The artist folder.
    """
    (artist / "FLAC").mkdir()
    (artist / "FLAC (65 on 65)").mkdir()

    with pytest.raises(TooManyContainersError):
        _ = placement.plan(artist)


# ==================================================================================== #
#                                     COLLISIONS                                       #
# ==================================================================================== #
def test_a_move_blocked_by_a_name_in_the_way_is_flagged(artist: Path) -> None:
    """A lossless album cannot move in when the container already holds its name.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Kind of Blue", "lossless")
    # A different album of the same name already inside the container.
    _ = fill(artist / "FLAC" / "01. (1959) - Kind of Blue", "lossless")

    result = placement.plan(artist)

    assert len(result.collisions) == 1
    assert result.moving_in == ()


def test_a_move_out_blocked_by_a_name_in_the_way_is_flagged(artist: Path) -> None:
    """A lossy album cannot move out when the root already holds its name.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1990) - Twofer", "lossy")
    # A different album of the same name already in the root.
    _ = fill(artist / "01. (1990) - Twofer", "lossy")

    result = placement.plan(artist)

    assert len(result.collisions) == 1
    assert result.moving_out == ()


# ==================================================================================== #
#                                     THE WHOLE PLAN                                   #
# ==================================================================================== #
def test_nothing_is_written_by_planning(artist: Path) -> None:
    """Planning reads tiers and touches nothing.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Kind of Blue", "lossless")

    _ = placement.plan(artist)

    assert (artist / "01. (1959) - Kind of Blue").exists()
    assert not (artist / "FLAC").exists()


def test_progress_is_reported_for_every_album(artist: Path) -> None:
    """The caller drives a display without this module knowing one exists.

    Args:
        artist: The artist folder.
    """
    a: Path = fill(artist / "01. (1959) - A", "lossless")
    b: Path = fill(artist / "02. (1990) - B", "lossy")
    seen: list[Path] = []

    _ = placement.plan(artist, on_progress=seen.append)

    assert set(seen) == {a, b}


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def test_applying_files_everything_correctly(artist: Path) -> None:
    """Move-in, move-out, and a container rename all land in one pass.

    The moved-in album ends up inside the normalised "FLAC", proving the
    rename happens after the moves rather than before.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Kind of Blue", "lossless")  # root -> in
    _ = fill(artist / "FLAC - (56 on 65)" / "02. (1970) - Live", "lossy")  # container -> out
    _ = fill(artist / "FLAC - (56 on 65)" / "03. (1965) - ESP", "lossless")  # stays

    report = placement.apply(placement.plan(artist))

    assert report.moved == 2
    assert report.container_change is ContainerChange.RENAME
    assert subfolders(artist) == {
        "FLAC",
        "FLAC/01. (1959) - Kind of Blue",
        "FLAC/03. (1965) - ESP",
        "02. (1970) - Live",
    }


def test_applying_creates_the_container_before_moving_in(artist: Path) -> None:
    """A first lossless album lands inside a freshly made container.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Kind of Blue", "lossless")
    _ = fill(artist / "02. (1990) - Lossy", "lossy")  # makes a mix, so a container is wanted

    report = placement.apply(placement.plan(artist))

    assert report.container_change is ContainerChange.CREATE
    assert (artist / "FLAC" / "01. (1959) - Kind of Blue").is_dir()


def test_applying_removes_the_emptied_container(artist: Path) -> None:
    """Once the last non-lossless album is out, the container is removed.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1990) - Lossy", "lossy")
    (artist / "FLAC").mkdir()

    report = placement.apply(placement.plan(artist))

    assert report.container_change is ContainerChange.REMOVE
    assert not (artist / "FLAC").exists()


def test_the_artist_folder_name_is_left_untouched(artist: Path) -> None:
    """Placement moves albums; naming the artist is the label's job, not this.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Kind of Blue", "lossless")
    original: str = artist.name

    _ = placement.apply(placement.plan(artist))

    assert artist.exists()
    assert artist.name == original


def test_applying_is_idempotent(artist: Path) -> None:
    """A second run finds everything on its correct side and changes nothing.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - Lossless", "lossless")
    _ = fill(artist / "02. (1990) - Lossy", "lossy")

    _ = placement.apply(placement.plan(artist))
    second = placement.plan(artist)

    assert second.moving_in == ()
    assert second.moving_out == ()
    assert second.container_change is None


def test_a_move_that_fails_is_reported(artist: Path) -> None:
    """A source removed after planning fails to move, and the run goes on.

    Args:
        artist: The artist folder.
    """
    gone: Path = fill(artist / "01. (1959) - Gone", "lossless")
    _ = fill(artist / "02. (1960) - Here", "lossless")
    _ = fill(artist / "03. (1990) - Lossy", "lossy")  # makes a mix, so the lossless albums move in
    plan = placement.plan(artist)
    # Remove one source between planning and applying.
    (gone / "01.flac").unlink()
    gone.rmdir()

    report = placement.apply(plan)

    assert report.moved == 1
    assert len(report.failures) == 1
    assert report.failures[0][0] == gone


def test_progress_is_reported_for_every_move(artist: Path) -> None:
    """Every album moved announces itself, so a bar can be sized from the plan.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1959) - A", "lossless")
    _ = fill(artist / "02. (1960) - B", "lossless")
    plan = placement.plan(artist)
    seen: list[Path] = []

    _ = placement.apply(plan, on_progress=seen.append)

    assert len(seen) == len(plan.moving_in) + len(plan.moving_out)
