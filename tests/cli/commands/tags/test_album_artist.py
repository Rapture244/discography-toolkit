# tests/cli/commands/tags/test_album_artist.py
"""Tests for the `rapt tags album-artist` command."""

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
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def shelf(tmp_path: Path) -> Path:
    """Build a shelf holding two artists and one track under neither.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        The shelf's path.
    """
    root: Path = tmp_path / "Jazz"
    for region, artist in (
        ("USA", "Miles Davis - [1 • 1F • 0L • 0M]"),
        ("Africa", "(Nigeria) - Fela Kuti - [1 • 1F • 0L • 0M]"),
    ):
        album: Path = root / region / artist / "FLAC" / "01. (1970) - X [FLAC]"
        album.mkdir(parents=True)
        _write_silence(album / "01.flac")

    (root / "Unsorted").mkdir(parents=True)
    _write_silence(root / "Unsorted" / "loose.flac")

    return root


def _write_silence(path: Path) -> None:
    """Write a short silent FLAC.

    Args:
        path: Where to write it.
    """
    sf.write(path, np.zeros(4410, dtype="float32"), 44100, format="FLAC")


def album_artist_of(track: Path) -> str:
    """Read one track's Album Artist.

    Args:
        track: The file to read.

    Returns:
        The stored value, empty when absent.
    """
    return metadata.read(track, [Tag.ALBUM_ARTIST])[Tag.ALBUM_ARTIST]


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def test_each_track_gets_the_artist_above_it(shelf: Path) -> None:
    """One pass over a shelf writes a different value per artist.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="y\n")

    assert result.exit_code == 0
    assert album_artist_of(next((shelf / "USA").rglob("*.flac"))) == "Miles Davis"
    assert album_artist_of(next((shelf / "Africa").rglob("*.flac"))) == "(Nigeria) - Fela Kuti"


def test_a_prefix_in_the_folder_name_survives(shelf: Path) -> None:
    """Splitting on " - " would cut "(Nigeria) - Fela Kuti" to "(Nigeria)".

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="y\n")

    assert album_artist_of(next((shelf / "Africa").rglob("*.flac"))) == "(Nigeria) - Fela Kuti"


def test_pointing_at_one_artist_uses_that_artist(shelf: Path) -> None:
    """An artist folder is its own scope.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [1 • 1F • 0L • 0M]"

    result = runner.invoke(app, ["tags", "album-artist", "-p", str(artist)], input="y\n")

    assert result.exit_code == 0
    assert album_artist_of(next(artist.rglob("*.flac"))) == "Miles Davis"


def test_shows_the_value_before_writing(shelf: Path) -> None:
    """The derived name is the one decision, so it is shown, not inferred.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="n\n")

    assert "'Miles Davis'" in result.output
    assert "'(Nigeria) - Fela Kuti'" in result.output


# ==================================================================================== #
#                                    WHAT IT REFUSES                                   #
# ==================================================================================== #
def test_a_track_under_no_artist_is_left_alone(shelf: Path) -> None:
    """There is no name to derive, and guessing one would invent a discography.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="y\n")

    assert album_artist_of(shelf / "Unsorted" / "loose.flac") == ""
    assert "sit under no artist folder" in result.output


def test_unresolved_is_counted_apart_from_clean(shelf: Path) -> None:
    """A track with no artist above it is not a track already correct.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="n\n")

    assert "No artist" in result.output


def test_refuses_a_path_with_no_artist_folder(tmp_path: Path) -> None:
    """Without a labelled folder there is nothing to derive from at all.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "Unsorted" / "01. (1959) - X"
    album.mkdir(parents=True)
    _write_silence(album / "01.flac")

    result = runner.invoke(app, ["tags", "album-artist", "-p", str(tmp_path)])

    assert result.exit_code == 1
    assert "No artist folder found" in result.output


def test_a_folder_without_audio_exits_cleanly(tmp_path: Path) -> None:
    """Nothing to do is not an error.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Miles Davis - [0 • 0F • 0L • 0M]").mkdir()

    result = runner.invoke(app, ["tags", "album-artist", "-p", str(tmp_path)])

    assert result.exit_code == 0
    assert "No audio files" in result.output


# ==================================================================================== #
#                                     NOT WRITING                                      #
# ==================================================================================== #
def test_a_dry_run_changes_nothing(shelf: Path) -> None:
    """A dry run reports and writes nothing.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf), "--dry-run"])

    assert "Dry run" in result.output
    assert album_artist_of(next((shelf / "USA").rglob("*.flac"))) == ""


def test_declining_the_prompt_changes_nothing(shelf: Path) -> None:
    """Answering no stops before any write.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="n\n")

    assert "Aborted" in result.output
    assert album_artist_of(next((shelf / "USA").rglob("*.flac"))) == ""


def test_a_second_run_finds_nothing(shelf: Path) -> None:
    """Tagging twice is tagging once.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="y\n")

    result = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)])

    assert "Nothing to do" in result.output


def test_the_per_track_artist_tag_is_untouched(shelf: Path) -> None:
    """TPE1 names who played; this command writes TPE2 and nothing else.

    Args:
        shelf: The fixture shelf.
    """
    track: Path = next((shelf / "USA").rglob("*.flac"))
    metadata.write(track, {Tag.TITLE: "So What"})

    _ = runner.invoke(app, ["tags", "album-artist", "-p", str(shelf)], input="y\n")

    assert metadata.read(track, [Tag.TITLE])[Tag.TITLE] == "So What"
