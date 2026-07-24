# tests/cli/commands/tags/test_album.py
"""Tests for the `rapt tags album` command."""

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
def artist(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an artist folder holding given albums.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking album names and returning the artist folder.
    """

    def build(*albums: str) -> Path:
        root: Path = tmp_path / "Miles Davis - [1 • 1F • 0L • 0M]"
        for name in albums:
            folder: Path = root / "FLAC" / name
            folder.mkdir(parents=True)
            _write_silence(folder / "01.flac")
        return root

    return build


def _write_silence(path: Path) -> None:
    """Write a short silent FLAC.

    Args:
        path: Where to write it.
    """
    sf.write(path, np.zeros(4410, dtype="float32"), 44100, format="FLAC")


def album_of(track: Path) -> str:
    """Read one track's Album tag.

    Args:
        track: The file to read.

    Returns:
        The stored value, empty when absent.
    """
    return metadata.read(track, [Tag.ALBUM])[Tag.ALBUM]


def only_track(root: Path) -> Path:
    """Return the first track beneath a folder.

    Args:
        root: The folder to search.

    Returns:
        Its track.
    """
    return next(root.rglob("*.flac"))


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def test_writes_the_folder_name_verbatim(artist: Callable[..., Path]) -> None:
    """Index, year and quality marker are part of the name, by design.

    The folder name is the canonical form of an album here, so a player
    sorting on Album matches the shelf.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")

    result = runner.invoke(app, ["tags", "album", "-p", str(root)], input="y\n")

    assert result.exit_code == 0
    assert album_of(only_track(root)) == "01. (1959) - Kind of Blue [FLAC]"


def test_a_favourite_mark_is_kept(artist: Callable[..., Path]) -> None:
    """The "©" is part of the name and is written with it.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("©27. (1959) - Kind of Blue [FLAC]")

    _ = runner.invoke(app, ["tags", "album", "-p", str(root)], input="y\n")

    assert album_of(only_track(root)) == "©27. (1959) - Kind of Blue [FLAC]"


def test_every_disc_shares_the_album_name(artist: Callable[..., Path]) -> None:
    """A track in a disc subfolder belongs to the album above it.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1970) - Bitches Brew [FLAC]")
    folder: Path = root / "FLAC" / "01. (1970) - Bitches Brew [FLAC]"
    (folder / "CD 2").mkdir()
    _write_silence(folder / "CD 2" / "01.flac")

    _ = runner.invoke(app, ["tags", "album", "-p", str(root)], input="y\n")

    assert album_of(folder / "CD 2" / "01.flac") == "01. (1970) - Bitches Brew [FLAC]"


def test_each_album_gets_its_own_name(artist: Callable[..., Path]) -> None:
    """One pass writes a different value per album.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Alpha [FLAC]", "02. (1970) - Bravo [FLAC]")

    _ = runner.invoke(app, ["tags", "album", "-p", str(root)], input="y\n")

    stored: set[str] = {album_of(track) for track in root.rglob("*.flac")}
    assert stored == {"01. (1959) - Alpha [FLAC]", "02. (1970) - Bravo [FLAC]"}


# ==================================================================================== #
#                                    WHAT IT REFUSES                                   #
# ==================================================================================== #
def test_a_track_under_no_album_is_left_alone(artist: Callable[..., Path]) -> None:
    """Nothing to take a name from means nothing to write.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    loose: Path = root / "loose.flac"
    _write_silence(loose)
    # Seeded, so "left alone" is distinguishable from "cleared".
    metadata.write(loose, {Tag.ALBUM: "Untouched"})

    result = runner.invoke(app, ["tags", "album", "-p", str(root)], input="y\n")

    assert album_of(loose) == "Untouched"
    assert "sit under no album folder" in result.output


def test_orphans_are_counted_apart_from_clean(artist: Callable[..., Path]) -> None:
    """A track under no album is not a track already correct.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    _write_silence(root / "loose.flac")

    result = runner.invoke(app, ["tags", "album", "-p", str(root)], input="n\n")

    assert "No album" in result.output


def test_refuses_a_path_with_no_album(tmp_path: Path) -> None:
    """Albums are recognized through their artist, and there is none here.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    loose: Path = tmp_path / "Unsorted"
    loose.mkdir()
    _write_silence(loose / "01.flac")

    result = runner.invoke(app, ["tags", "album", "-p", str(tmp_path)])

    assert result.exit_code == 1
    assert "No album folder found" in result.output


def test_a_folder_without_audio_exits_cleanly(tmp_path: Path) -> None:
    """Nothing to do is not an error.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Miles Davis - [1 on 1]" / "01. (1959) - X").mkdir(parents=True)

    result = runner.invoke(app, ["tags", "album", "-p", str(tmp_path)])

    assert result.exit_code == 0
    assert "No audio files" in result.output


# ==================================================================================== #
#                                     NOT WRITING                                      #
# ==================================================================================== #
def test_a_dry_run_changes_nothing(artist: Callable[..., Path]) -> None:
    """A dry run reports and writes nothing.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")

    result = runner.invoke(app, ["tags", "album", "-p", str(root), "--dry-run"])

    assert "Dry run" in result.output
    assert album_of(only_track(root)) == ""


def test_declining_the_prompt_changes_nothing(artist: Callable[..., Path]) -> None:
    """Answering no stops before any write.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")

    result = runner.invoke(app, ["tags", "album", "-p", str(root)], input="n\n")

    assert "Aborted" in result.output
    assert album_of(only_track(root)) == ""


def test_a_second_run_finds_nothing(artist: Callable[..., Path]) -> None:
    """Tagging twice is tagging once.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    _ = runner.invoke(app, ["tags", "album", "-p", str(root)], input="y\n")

    result = runner.invoke(app, ["tags", "album", "-p", str(root)])

    assert "Nothing to do" in result.output


def test_the_date_tag_is_untouched(artist: Callable[..., Path]) -> None:
    """This command writes Album and nothing else.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    track: Path = only_track(root)
    metadata.write(track, {Tag.DATE: "1959"})

    _ = runner.invoke(app, ["tags", "album", "-p", str(root)], input="y\n")

    assert metadata.read(track, [Tag.DATE])[Tag.DATE] == "1959"
