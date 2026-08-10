# tests/cli/commands/test_playlist.py
"""Tests for the `rapt playlist` command.

The command moves folders and writes tags in one pass, with no dry run,
so a wrong answer reaches the files before anyone sees it. What matters
most here is the rule that nothing which fails to match is written to at
all -- and that a track is only ever given the name of the album it is
actually in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from discography_toolkit.cli.main import app
from discography_toolkit.core import metadata
from discography_toolkit.core.metadata import Tag

import pytest
from tests.helpers import silence

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       HELPERS                                        #
# ==================================================================================== #
@pytest.fixture()
def album(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an album folder of tagged tracks.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking a path relative to the temporary directory and
        the Album tag its tracks should carry.
    """

    def build(relative: str, tag: str | None = "Kind of Blue", tracks: int = 1) -> Path:
        folder: Path = tmp_path / relative
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(tracks):
            track: Path = folder / f"{index + 1:02d}.flac"
            silence(track)
            # A track number as well as the album: the tags are mirrored
            # from the discography track this one pairs with, and the
            # pairing is by disc and track within the album.
            values: dict[Tag, str] = {Tag.TRACK: f"{index + 1:02d}"}
            if tag is not None:
                values[Tag.ALBUM] = tag
            metadata.write(track, values)
        return folder

    return build


def album_of(track: Path) -> str:
    """Read one track's Album tag.

    Args:
        track: The audio file to read.

    Returns:
        Its Album tag.
    """
    return metadata.read(track, [Tag.ALBUM])[Tag.ALBUM]


# ==================================================================================== #
#                                     THE SAFETY RULE                                  #
# ==================================================================================== #
def test_a_folder_holding_albums_is_left_entirely_alone(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """The failure this command was taught by, end to end.

    A converter left an artist's albums nested one folder deeper than
    usual. That folder was read recursively, reported as the one album it
    found first, matched, moved, and every track beneath it stamped with
    that album's name -- two hundred files, and the tags are not undone
    by moving a folder back.

    Nothing about it should now change: not its place, not its name, and
    above all not one tag.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "Miles Davis - [2 \u2022 2F \u2022 0L \u2022 0M]"
    (disco / "01. (1959) - Kind of Blue [FLAC]").mkdir(parents=True)
    (disco / "02. (1970) - Bitches Brew [FLAC]").mkdir(parents=True)

    playlist: Path = tmp_path / "playlist"
    playlist.mkdir()
    first: Path = album("playlist/nested/Davis - 1959 - Kind of Blue", tag="Kind of Blue")
    second: Path = album("playlist/nested/Davis - 1970 - Bitches Brew", tag="Bitches Brew")

    result = runner.invoke(
        app,
        ["playlist", "--path", str(disco), "--converted", str(playlist)],
        input="y\n",
    )

    assert first.is_dir()
    assert second.is_dir()
    assert album_of(first / "01.flac") == "Kind of Blue"
    assert album_of(second / "01.flac") == "Bitches Brew"
    assert "nested" in result.output


# ==================================================================================== #
#                                       FOLDING                                        #
# ==================================================================================== #
def test_a_loose_album_is_filed_and_tagged(album: Callable[..., Path], tmp_path: Path) -> None:
    """The ordinary case: a conversion takes the discography's name and tags.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: str = "disco/Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    disco: Path = tmp_path / artist
    _ = album(f"{artist}/\u00a901. (1959) - Kind of Blue [FLAC]", tag="Kind of Blue")

    playlist: Path = tmp_path / "playlist"
    playlist.mkdir()
    _ = album("playlist/Miles Davis - Kind of Blue", tag="Kind of Blue")

    _ = runner.invoke(
        app,
        ["playlist", "--path", str(disco), "--converted", str(playlist)],
        input="y\n",
    )

    settled: Path = playlist / "Miles Davis" / "\u00a901. (1959) - Kind of Blue [FLAC]"
    assert settled.is_dir()
    assert album_of(settled / "01.flac") == "\u00a9(1959) - Kind of Blue [FLAC]"


def test_a_second_run_changes_nothing(album: Callable[..., Path], tmp_path: Path) -> None:
    """Syncing twice is syncing once.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    (disco / "01. (1959) - Kind of Blue [FLAC]").mkdir(parents=True)

    playlist: Path = tmp_path / "playlist"
    playlist.mkdir()
    _ = album("playlist/Miles Davis - Kind of Blue", tag="Kind of Blue")

    command: list[str] = ["playlist", "--path", str(disco), "--converted", str(playlist)]
    _ = runner.invoke(app, command, input="y\n")

    result = runner.invoke(app, command, input="y\n")

    assert "already in step with the discography" in result.output


def test_an_artist_whose_folders_all_fail_says_so(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """Nothing to do and nothing that could be done are opposite facts.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    (disco / "01. (1959) - Kind of Blue [FLAC]").mkdir(parents=True)

    playlist: Path = tmp_path / "playlist"
    playlist.mkdir()
    _ = album("playlist/Miles Davis/one", tag="Kind of Blue")
    _ = album("playlist/Miles Davis/two", tag="Kind of Blue")

    result = runner.invoke(
        app,
        ["playlist", "--path", str(disco), "--converted", str(playlist)],
        input="y\n",
    )

    assert "nothing here could be matched" in result.output
    assert "claimed by more than one folder" in result.output


def test_the_roster_is_shown_before_the_confirmation(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """An artist with nothing found is named, not silently omitted.

    "Not converted yet" and "wrong playlist" look identical when only the
    matches are listed.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    shelf: Path = tmp_path / "disco"
    (
        shelf
        / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
        / "01. (1959) - Kind of Blue [FLAC]"
    ).mkdir(parents=True)
    (
        shelf
        / "John Coltrane - [1 \u2022 1F \u2022 0L \u2022 0M]"
        / "01. (1965) - Ascension [FLAC]"
    ).mkdir(parents=True)

    playlist: Path = tmp_path / "playlist"
    playlist.mkdir()
    _ = album("playlist/Miles Davis - Kind of Blue", tag="Kind of Blue")

    result = runner.invoke(
        app,
        ["playlist", "--path", str(shelf), "--converted", str(playlist)],
        input="n\n",
    )

    assert "John Coltrane" in result.output
    assert "nothing found in the playlist" in result.output


def test_the_confirmation_names_the_work_and_the_target(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """The question carries the run, so a scrolled roster still answers it.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    (disco / "01. (1959) - Kind of Blue [FLAC]").mkdir(parents=True)

    playlist: Path = tmp_path / "playlist"
    playlist.mkdir()
    _ = album("playlist/Miles Davis - Kind of Blue", tag="Kind of Blue")

    result = runner.invoke(
        app,
        ["playlist", "--path", str(disco), "--converted", str(playlist)],
        input="n\n",
    )

    assert "Sync 1 album(s) beneath 'playlist'?" in result.output


def test_refusing_the_prompt_changes_nothing(album: Callable[..., Path], tmp_path: Path) -> None:
    """Saying no leaves the playlist exactly as it was.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    (disco / "01. (1959) - Kind of Blue [FLAC]").mkdir(parents=True)

    playlist: Path = tmp_path / "playlist"
    playlist.mkdir()
    folder: Path = album("playlist/Miles Davis - Kind of Blue", tag="Kind of Blue")

    result = runner.invoke(
        app,
        ["playlist", "--path", str(disco), "--converted", str(playlist)],
        input="n\n",
    )

    assert "No changes made" in result.output
    assert folder.is_dir()
    assert not (playlist / "Miles Davis").exists()
