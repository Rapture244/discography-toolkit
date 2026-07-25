# tests/cli/commands/test_layout.py
"""Tests for the `rapt layout` command.

The command chains five operations, each with its own unit tests, so
these check the wiring: that the steps run in the right order against
each other's output, that a whole shelf is walked, and that the command's
own guards -- no artists, two containers, the confirmation -- behave.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

from discography_toolkit.cli.main import app

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
def flac(album: Path) -> None:
    """Put a silent FLAC track in a folder, making its parents.

    Args:
        album: The album folder to fill.
    """
    album.mkdir(parents=True, exist_ok=True)
    sf.write(album / "a track.flac", np.zeros(441, dtype="float32"), 44100, format="FLAC")


def lossy(album: Path) -> None:
    """Put a stub MP3 track in a folder, making its parents.

    Args:
        album: The album folder to fill.
    """
    album.mkdir(parents=True, exist_ok=True)
    _ = (album / "a track.mp3").write_bytes(b"x")


def subfolders(root: Path) -> set[str]:
    """The relative paths of every folder beneath a root.

    Args:
        root: The folder to walk.

    Returns:
        Relative folder paths, forward-slashed for portability.
    """
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()}


@pytest.fixture()
def messy_artist(tmp_path: Path) -> Callable[[], Path]:
    """Return a factory building one artist in need of every step.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable returning the artist folder.
    """

    def build() -> Path:
        artist: Path = tmp_path / "Miles Davis"
        flac(artist / "(1959) - kind of blue [flac]")  # lossless in root: move in, case, tag
        lossy(artist / "FLAC - (56 on 65)" / "(1972) - on the corner")  # lossy in container: out
        flac(artist / "FLAC - (56 on 65)" / "(1970) - bitches brew")  # lossless: stays
        (artist / "(1980) - missing").mkdir()  # empty placeholder: marked M
        return artist

    return build


# ==================================================================================== #
#                                     END TO END                                       #
# ==================================================================================== #
def test_a_messy_artist_is_fully_laid_out(messy_artist: Callable[[], Path]) -> None:
    """Every step runs, in order, against what the last one wrote.

    Args:
        messy_artist: Factory building the artist.
    """
    artist: Path = messy_artist()
    parent: Path = artist.parent

    result = runner.invoke(app, ["layout", "--path", str(artist), "--yes"])

    assert result.exit_code == 0
    laid_out: Path = parent / "Miles Davis - [4 \u2022 2F \u2022 1L \u2022 1M]"
    assert laid_out.is_dir()
    assert subfolders(laid_out) == {
        "FLAC",
        "FLAC/01. (1959) - Kind of Blue [FLAC]",
        "FLAC/02. (1970) - Bitches Brew [FLAC]",
        "03. (1972) - On the Corner",
        "04. (1980) - M - Missing",
    }


def test_track_filenames_are_cased(messy_artist: Callable[[], Path]) -> None:
    """The casing step reaches the files inside the albums it moved.

    Args:
        messy_artist: Factory building the artist.
    """
    artist: Path = messy_artist()
    parent: Path = artist.parent

    _ = runner.invoke(app, ["layout", "--path", str(artist), "--yes"])

    laid_out: Path = parent / "Miles Davis - [4 \u2022 2F \u2022 1L \u2022 1M]"
    tracks: set[str] = {p.name for p in laid_out.rglob("*.flac")}
    assert "A Track.flac" in tracks


def test_running_twice_settles(messy_artist: Callable[[], Path]) -> None:
    """A second run finds nothing to change: the layout is a fixed point.

    Args:
        messy_artist: Factory building the artist.
    """
    artist: Path = messy_artist()
    parent: Path = artist.parent

    _ = runner.invoke(app, ["layout", "--path", str(artist), "--yes"])
    laid_out: Path = parent / "Miles Davis - [4 \u2022 2F \u2022 1L \u2022 1M]"

    second = runner.invoke(app, ["layout", "--path", str(laid_out), "--yes"])

    assert second.exit_code == 0
    assert "clean" in second.stdout
    # The name is unchanged, so the label did not accumulate a second bracket.
    assert (parent / "Miles Davis - [4 \u2022 2F \u2022 1L \u2022 1M]").is_dir()


def test_a_whole_shelf_is_walked(tmp_path: Path) -> None:
    """Two artists under a shelf are both laid out in one run.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    flac(tmp_path / "Shelf" / "Miles Davis" / "(1959) - kind of blue")
    lossy(tmp_path / "Shelf" / "Fela Kuti" / "(1972) - zombie")

    result = runner.invoke(app, ["layout", "--path", str(tmp_path / "Shelf"), "--yes"])

    assert result.exit_code == 0
    assert (tmp_path / "Shelf" / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]").is_dir()
    assert (tmp_path / "Shelf" / "Fela Kuti - [1 \u2022 0F \u2022 1L \u2022 0M]").is_dir()


def test_naming_runs_before_numbering(tmp_path: Path) -> None:
    """Numbering sorts on the year naming puts at the front, so order matters.

    One album carries its year at the end. Only once naming has moved it
    to the front does numbering sort the two by year; run the other way
    round, the later album would take "01".

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    lossy(artist / "kind of blue (1959)")  # year at the end
    lossy(artist / "(1970) - live")  # year already at the front

    _ = runner.invoke(app, ["layout", "--path", str(artist), "--yes"])

    laid_out: Path = tmp_path / "Miles Davis - [2 \u2022 0F \u2022 2L \u2022 0M]"
    assert (laid_out / "01. (1959) - Kind of Blue").is_dir()
    assert (laid_out / "02. (1970) - Live").is_dir()


# ==================================================================================== #
#                                        GUARDS                                        #
# ==================================================================================== #
def test_no_artists_is_an_error(tmp_path: Path) -> None:
    """A folder with no audio to anchor on cannot be laid out.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "empty shelf").mkdir()

    result = runner.invoke(app, ["layout", "--path", str(tmp_path / "empty shelf"), "--yes"])

    assert result.exit_code == 1


def test_two_containers_skip_the_artist(tmp_path: Path) -> None:
    """An artist with two containers is skipped whole, not half laid out.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    flac(artist / "FLAC" / "(1959) - kind of blue")
    flac(artist / "FLAC (65 on 65)" / "(1970) - bitches brew")

    result = runner.invoke(app, ["layout", "--path", str(artist), "--yes"])

    assert result.exit_code == 0
    assert "skipped" in result.stdout
    # Untouched: still lowercase, still two containers, no label.
    assert (artist / "FLAC" / "(1959) - kind of blue").is_dir()


def test_declining_the_prompt_changes_nothing(messy_artist: Callable[[], Path]) -> None:
    """Answering no at the confirmation leaves the artist exactly as found.

    Args:
        messy_artist: Factory building the artist.
    """
    artist: Path = messy_artist()

    result = runner.invoke(app, ["layout", "--path", str(artist)], input="n\n")

    assert result.exit_code == 0
    assert artist.is_dir()
    assert (artist / "(1959) - kind of blue [flac]").is_dir()


def test_confirming_the_prompt_proceeds(messy_artist: Callable[[], Path]) -> None:
    """Answering yes runs the layout without needing the flag.

    Args:
        messy_artist: Factory building the artist.
    """
    artist: Path = messy_artist()
    parent: Path = artist.parent

    result = runner.invoke(app, ["layout", "--path", str(artist)], input="y\n")

    assert result.exit_code == 0
    assert (parent / "Miles Davis - [4 \u2022 2F \u2022 1L \u2022 1M]").is_dir()
