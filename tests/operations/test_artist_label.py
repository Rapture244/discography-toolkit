# tests/operations/test_artist_label.py
"""Tests for labelling an artist folder with its breakdown.

Run against real folders. Tiers are made real where they are counted -- a
silent FLAC for lossless, a stub for lossy or opus, an empty folder for
missing -- since the label reads the files, not the names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf

from discography_toolkit.operations import artist_label

import pytest

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
    folder: Path = tmp_path / "Charlie Mariano"
    folder.mkdir()
    return folder


def fill(album: Path, tier: str) -> Path:
    """Build an album folder holding files of the given tier.

    Args:
        album: Where to make the folder.
        tier: "lossless", "opus", "lossy", or "none".

    Returns:
        The album folder.
    """
    album.mkdir(parents=True, exist_ok=True)
    if tier == "lossless":
        sf.write(album / "01.flac", np.zeros(441, dtype="float32"), 44100, format="FLAC")
    elif tier == "opus":
        _ = (album / "01.opus").write_bytes(b"x")
    elif tier == "lossy":
        _ = (album / "01.mp3").write_bytes(b"x")
    return album


# ==================================================================================== #
#                                       COUNTING                                       #
# ==================================================================================== #
def test_the_counts_read_the_files(artist: Path) -> None:
    """Each album is counted by what it holds, not what it is called.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - A", "lossless")
    _ = fill(artist / "02. (1980) - B", "lossy")
    (artist / "03. (1990) - M - C").mkdir()

    result = artist_label.plan(artist)

    assert (result.total, result.flac, result.lossy, result.missing) == (3, 1, 1, 1)


def test_opus_counts_as_lossy(artist: Path) -> None:
    """Opus is held but not losslessly, so it falls on the lossy side.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1972) - On the Corner", "opus")

    result = artist_label.plan(artist)

    assert result.lossy == 1
    assert result.flac == 0


def test_the_counts_partition_the_total(artist: Path) -> None:
    """Every album is counted exactly once, so the parts sum to the total.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - A", "lossless")
    _ = fill(artist / "FLAC" / "02. (1960) - B", "lossless")
    _ = fill(artist / "03. (1970) - C", "opus")
    _ = fill(artist / "04. (1980) - D", "lossy")
    (artist / "05. (1990) - M - E").mkdir()

    result = artist_label.plan(artist)

    assert result.total == result.flac + result.lossy + result.missing
    assert (result.flac, result.lossy, result.missing) == (2, 2, 1)


def test_albums_on_both_sides_are_counted(artist: Path) -> None:
    """The container is unwrapped, so albums inside it count too.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - Inside", "lossless")
    _ = fill(artist / "02. (1990) - Outside", "lossy")

    assert artist_label.plan(artist).total == 2


# ==================================================================================== #
#                                       THE LABEL                                      #
# ==================================================================================== #
def test_the_label_is_built_into_the_new_name(artist: Path) -> None:
    """The new name is the folder's, with the breakdown appended.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - A", "lossless")
    _ = fill(artist / "02. (1980) - B", "lossy")

    result = artist_label.plan(artist)

    assert result.new_name == "Charlie Mariano - [2 \u2022 1F \u2022 1L \u2022 0M]"


def test_an_old_label_is_replaced_not_accumulated(tmp_path: Path) -> None:
    """A folder already labelled gets the fresh count, not a second bracket.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Charlie Mariano - [99 \u2022 99F \u2022 0L \u2022 0M]"
    artist.mkdir()
    _ = fill(artist / "01. (1980) - B", "lossy")

    result = artist_label.plan(artist)

    assert result.new_name == "Charlie Mariano - [1 \u2022 0F \u2022 1L \u2022 0M]"


def test_nothing_is_written_by_planning(artist: Path) -> None:
    """Planning counts tiers and touches nothing.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1980) - B", "lossy")
    original: str = artist.name

    _ = artist_label.plan(artist)

    assert artist.name == original


def test_progress_is_reported_for_every_album(artist: Path) -> None:
    """The caller drives a display without this module knowing one exists.

    Args:
        artist: The artist folder.
    """
    a: Path = fill(artist / "FLAC" / "01. (1959) - A", "lossless")
    b: Path = fill(artist / "02. (1990) - B", "lossy")
    seen: list[Path] = []

    _ = artist_label.plan(artist, on_progress=seen.append)

    assert set(seen) == {a, b}


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def test_applying_renames_the_folder(artist: Path) -> None:
    """The folder on disk takes its labelled name.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - A", "lossless")
    parent: Path = artist.parent

    report = artist_label.apply(artist_label.plan(artist))

    assert report.renamed is True
    assert report.artist.name == "Charlie Mariano - [1 \u2022 1F \u2022 0L \u2022 0M]"
    assert (parent / report.artist.name).is_dir()


def test_applying_reports_the_new_path(artist: Path) -> None:
    """The report carries the folder's path after the rename, for the caller.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1980) - B", "lossy")

    report = artist_label.apply(artist_label.plan(artist))

    assert report.artist.exists()
    assert report.artist != artist


def test_an_already_correct_label_is_left_alone(tmp_path: Path) -> None:
    """A folder already carrying its right label is not renamed.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Charlie Mariano - [1 \u2022 0F \u2022 1L \u2022 0M]"
    artist.mkdir()
    _ = fill(artist / "01. (1980) - B", "lossy")

    report = artist_label.apply(artist_label.plan(artist))

    assert report.renamed is False
    assert report.artist == artist


def test_applying_is_idempotent(artist: Path) -> None:
    """A second run finds the label right and changes nothing.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "FLAC" / "01. (1959) - A", "lossless")

    report = artist_label.apply(artist_label.plan(artist))
    second = artist_label.plan(report.artist)

    assert second.needs_rename is False


def test_a_failed_rename_is_reported(artist: Path) -> None:
    """A folder blocked from renaming reports the failure rather than raising.

    Args:
        artist: The artist folder.
    """
    _ = fill(artist / "01. (1980) - B", "lossy")
    plan = artist_label.plan(artist)
    # A folder already sitting at the target name blocks the rename.
    blocker: Path = plan.target
    blocker.mkdir()
    _ = (blocker / "keep.txt").write_text("do not lose me")

    report = artist_label.apply(plan)

    assert report.renamed is False
    assert report.detail is not None
    assert (blocker / "keep.txt").read_text() == "do not lose me"
