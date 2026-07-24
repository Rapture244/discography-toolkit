# tests/cli/commands/test_title.py
"""Tests for the `rapt title` command."""

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
def album(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an album of tracks with given titles.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking titles and returning the album folder.
    """

    def build(*titles: str | None) -> Path:
        folder: Path = tmp_path / "Miles Davis - [1 • 1F • 0L • 0M]" / "FLAC" / "01. (1959) - X"
        folder.mkdir(parents=True)
        for index, value in enumerate(titles, start=1):
            track: Path = folder / f"{index:02d}.flac"
            sf.write(track, np.zeros(4410, dtype="float32"), 44100, format="FLAC")
            if value is not None:
                metadata.write(track, {Tag.TITLE: value})
        return folder

    return build


def titles_in(folder: Path) -> list[str]:
    """Read every Title beneath a folder, in filename order.

    Args:
        folder: The folder to scan.

    Returns:
        The titles found.
    """
    return [
        metadata.read(track, [Tag.TITLE])[Tag.TITLE] for track in sorted(folder.rglob("*.flac"))
    ]


# ==================================================================================== #
#                                        CASING                                        #
# ==================================================================================== #
def test_recases_lowercase_titles(album: Callable[..., Path]) -> None:
    """A lowercase title comes out properly cased.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("so what", "freddie freeloader")

    result = runner.invoke(app, ["title", "-p", str(folder)], input="y\n")

    assert result.exit_code == 0
    assert titles_in(folder) == ["So What", "Freddie Freeloader"]


def test_leaves_a_correct_title_alone(album: Callable[..., Path]) -> None:
    """A title already cased is not rewritten.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("Kind of Blue")

    result = runner.invoke(app, ["title", "-p", str(folder)])

    assert "Nothing to do" in result.output


def test_leaves_an_untitled_track_alone(album: Callable[..., Path]) -> None:
    """No title means nothing to case, not a title to invent.

    Args:
        album: Factory building an album.
    """
    folder: Path = album(None)

    result = runner.invoke(app, ["title", "-p", str(folder)])

    assert titles_in(folder) == [""]
    assert "carry no Title tag" in result.output


def test_untitled_is_counted_apart_from_clean(album: Callable[..., Path]) -> None:
    """A track with no title is not a track already correct.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("Kind of Blue", None)

    result = runner.invoke(app, ["title", "-p", str(folder)])

    assert "Untitled" in result.output
    assert "Clean" in result.output


def test_a_second_run_finds_nothing(album: Callable[..., Path]) -> None:
    """Casing twice is casing once.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("so what")
    _ = runner.invoke(app, ["title", "-p", str(folder)], input="y\n")

    result = runner.invoke(app, ["title", "-p", str(folder)])

    assert "Nothing to do" in result.output


# ==================================================================================== #
#                                       REPORTING                                      #
# ==================================================================================== #
def test_a_dry_run_lists_old_beside_new(album: Callable[..., Path]) -> None:
    """The listing shows what a title was, not only what it becomes.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("so what")

    result = runner.invoke(app, ["title", "-p", str(folder), "--dry-run"])

    assert "'so what'" in result.output
    assert "'So What'" in result.output


def test_a_real_run_omits_the_listing(album: Callable[..., Path]) -> None:
    """Once the decision is made, the listing is confirmation nobody reads.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("so what")

    result = runner.invoke(app, ["title", "-p", str(folder)], input="y\n")

    assert "'so what'" not in result.output


def test_a_dry_run_changes_nothing(album: Callable[..., Path]) -> None:
    """A dry run reports and writes nothing.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("so what")

    result = runner.invoke(app, ["title", "-p", str(folder), "--dry-run"])

    assert "Dry run" in result.output
    assert titles_in(folder) == ["so what"]


def test_declining_the_prompt_changes_nothing(album: Callable[..., Path]) -> None:
    """Answering no stops before any write.

    Args:
        album: Factory building an album.
    """
    folder: Path = album("so what")

    result = runner.invoke(app, ["title", "-p", str(folder)], input="n\n")

    assert "Aborted" in result.output
    assert titles_in(folder) == ["so what"]


def test_a_folder_without_audio_exits_cleanly(tmp_path: Path) -> None:
    """Nothing to do is not an error.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    result = runner.invoke(app, ["title", "-p", str(tmp_path)])

    assert result.exit_code == 0
    assert "No audio files" in result.output
