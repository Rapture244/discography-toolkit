# tests/cli/commands/test_organize.py
"""Tests for the `rapt organize` command.

Organize composes layout and align-tags, each with its own tests, so
these check the composition: that fresh material is laid out and then
tagged in one pass, under one confirmation, and that the tag half sees
the folders the layout half just settled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

from discography_toolkit.cli.main import app
from discography_toolkit.core import metadata
from discography_toolkit.core.metadata import Tag

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def fresh_shelf(tmp_path: Path) -> Callable[[], Path]:
    """Return a factory building a fresh, unlabelled shelf of two artists.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable returning the shelf folder.
    """

    def build() -> Path:
        def album(artist: str, name: str, title: str) -> None:
            folder: Path = tmp_path / artist / name
            folder.mkdir(parents=True)
            track: Path = folder / "01 - so what.flac"
            sf.write(track, np.zeros(441, dtype="float32"), 44100, format="FLAC")
            metadata.write(track, {Tag.TITLE: title})

        album("Miles Davis", "(1959) - kind of blue", "so what")
        album("Fela Kuti", "(1972) - zombie", "zombie")
        return tmp_path

    return build


# ==================================================================================== #
#                                     END TO END                                       #
# ==================================================================================== #
def test_fresh_material_is_laid_out_and_tagged(fresh_shelf: Callable[[], Path]) -> None:
    """The whole job: unlabelled folders come out organised and tagged.

    This is the case neither command does alone -- align-tags cannot find
    an unlabelled artist, and layout does not touch tags.

    Args:
        fresh_shelf: Factory building the shelf.
    """
    shelf: Path = fresh_shelf()

    result = runner.invoke(app, ["organize", "--path", str(shelf)], input="y\n")

    assert result.exit_code == 0
    # Folders: laid out and labelled.
    album: Path = (
        shelf
        / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
        / "FLAC"
        / "01. (1959) - Kind of Blue [FLAC]"
    )
    assert album.is_dir()
    # Tags: written off those folders.
    track: Path = next(album.glob("*.flac"))
    tags = metadata.read(track, [Tag.ALBUM, Tag.ALBUM_ARTIST, Tag.DATE, Tag.TITLE])
    assert tags[Tag.ALBUM] == "01. (1959) - Kind of Blue [FLAC]"
    assert tags[Tag.ALBUM_ARTIST] == "Miles Davis"
    assert tags[Tag.DATE] == "1959"
    assert tags[Tag.TITLE] == "So What"


def test_both_phases_are_shown(fresh_shelf: Callable[[], Path]) -> None:
    """The run reads as two phases, layout before tags.

    Args:
        fresh_shelf: Factory building the shelf.
    """
    shelf: Path = fresh_shelf()

    result = runner.invoke(app, ["organize", "--path", str(shelf)], input="y\n")

    assert "Layout" in result.stdout
    assert "Tags" in result.stdout
    assert result.stdout.index("Layout") < result.stdout.index("Tags")


def test_a_single_confirmation_covers_both_halves(fresh_shelf: Callable[[], Path]) -> None:
    """One "Proceed?" is asked, not one per half.

    Args:
        fresh_shelf: Factory building the shelf.
    """
    shelf: Path = fresh_shelf()

    result = runner.invoke(app, ["organize", "--path", str(shelf)], input="y\n")

    assert result.stdout.count("Proceed?") == 1


def test_a_single_artist_target_is_followed_when_renamed(tmp_path: Path) -> None:
    """Pointed at one artist, layout renames it and the tags still land.

    The target folder is renamed by the label, so the tag half has to
    follow it to its new path rather than the one first given.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    album: Path = artist / "(1959) - kind of blue"
    album.mkdir(parents=True)
    sf.write(album / "01 - so what.flac", np.zeros(441, dtype="float32"), 44100, format="FLAC")

    result = runner.invoke(app, ["organize", "--path", str(artist)], input="y\n")

    assert result.exit_code == 0
    laid_out: Path = tmp_path / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    track: Path = next(laid_out.rglob("*.flac"))
    assert metadata.read(track, [Tag.ALBUM_ARTIST])[Tag.ALBUM_ARTIST] == "Miles Davis"


# ==================================================================================== #
#                                        GUARDS                                        #
# ==================================================================================== #
def test_no_artists_is_an_error(tmp_path: Path) -> None:
    """A folder with no audio to anchor on cannot be organized.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "empty shelf").mkdir()

    result = runner.invoke(app, ["organize", "--path", str(tmp_path / "empty shelf")], input="y\n")

    assert result.exit_code == 1


def test_declining_changes_nothing(fresh_shelf: Callable[[], Path]) -> None:
    """Answering no leaves the folders and files exactly as found.

    Args:
        fresh_shelf: Factory building the shelf.
    """
    shelf: Path = fresh_shelf()

    result = runner.invoke(app, ["organize", "--path", str(shelf)], input="n\n")

    assert result.exit_code == 0
    assert (shelf / "Miles Davis" / "(1959) - kind of blue").is_dir()
