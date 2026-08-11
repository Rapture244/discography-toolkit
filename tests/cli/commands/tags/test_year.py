# tests/cli/commands/tags/test_year.py
"""Tests for the `rapt tags year` command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from discography_toolkit.cli.main import app
from discography_toolkit.core import metadata
from discography_toolkit.core.metadata import Tag

from tests.helpers import only_track, silence

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       HELPERS                                        #
# ==================================================================================== #
def date_of(track: Path) -> str:
    """Read one track's Date tag.

    Args:
        track: The file to read.

    Returns:
        The stored value, empty when absent.
    """
    return metadata.read(track, [Tag.DATE])[Tag.DATE]


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def test_writes_the_year_from_the_album_folder(artist: Callable[..., Path]) -> None:
    """The year in the folder name becomes the Date tag.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")

    result = runner.invoke(app, ["tags", "year", "-p", str(root)], input="y\n")

    assert result.exit_code == 0
    assert date_of(only_track(root)) == "1959"


def test_a_reissue_year_in_the_title_does_not_win(artist: Callable[..., Path]) -> None:
    """The leftmost wrapped year is the release; the other is what it reissues.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("90. (2013) - In India [1973] [FLAC]")

    _ = runner.invoke(app, ["tags", "year", "-p", str(root)], input="y\n")

    assert date_of(only_track(root)) == "2013"


def test_every_disc_of_an_album_shares_its_year(artist: Callable[..., Path]) -> None:
    """A track in a disc subfolder belongs to the album above it.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1970) - Bitches Brew [FLAC]")
    album: Path = root / "FLAC" / "01. (1970) - Bitches Brew [FLAC]"
    (album / "CD 2").mkdir()
    silence(album / "CD 2" / "01.flac")

    _ = runner.invoke(app, ["tags", "year", "-p", str(root)], input="y\n")

    assert date_of(album / "CD 2" / "01.flac") == "1970"


def test_an_approximate_year_clears_the_tag(artist: Callable[..., Path]) -> None:
    """An approximation is cleared rather than written.

    A date field holding something that is not a date is worse than an
    empty one: players sort on it, and ID3 stores it in a timestamp frame
    that refuses the value anyway.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("13. (199x) - Unknown Decade [FLAC]")
    track: Path = only_track(root)
    metadata.write(track, {Tag.DATE: "1995"})

    result = runner.invoke(app, ["tags", "year", "-p", str(root)], input="y\n")

    assert result.exit_code == 0
    assert date_of(track) == ""


def test_an_album_without_a_year_is_left_alone(artist: Callable[..., Path]) -> None:
    """Nothing to read means nothing to write.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("03. - No Year Here [FLAC]")
    track: Path = only_track(root)
    # Seeded, so "left alone" is distinguishable from "cleared".
    metadata.write(track, {Tag.DATE: "1994"})

    result = runner.invoke(app, ["tags", "year", "-p", str(root)])

    assert date_of(track) == "1994"
    assert "no year to take" in result.output


def test_undated_is_counted_apart_from_clean(artist: Callable[..., Path]) -> None:
    """An album with no year is not an album already dated.

    A bare count cannot tell "nothing to do because everything is right"
    from "nothing to do because nothing could be derived", so the second
    is said out loud.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Dated [FLAC]", "02. - Undated [FLAC]")
    _ = runner.invoke(app, ["tags", "year", "-p", str(root)], input="y\n")

    result = runner.invoke(app, ["tags", "year", "-p", str(root)])

    assert "0 to date" in result.output
    assert "1 file(s) have no year to take" in result.output


# ==================================================================================== #
#                                    WHAT IT REFUSES                                   #
# ==================================================================================== #
def test_refuses_a_path_with_no_album(tmp_path: Path) -> None:
    """Albums are recognized through their artist, and there is none here.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    loose: Path = tmp_path / "Unsorted"
    loose.mkdir()
    silence(loose / "01.flac")

    result = runner.invoke(app, ["tags", "year", "-p", str(tmp_path)])

    assert result.exit_code == 1
    assert "No album folder found" in result.output


def test_a_folder_without_audio_exits_cleanly(tmp_path: Path) -> None:
    """Nothing to do is not an error.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Miles Davis - [1 on 1]" / "01. (1959) - X").mkdir(parents=True)

    result = runner.invoke(app, ["tags", "year", "-p", str(tmp_path)])

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

    result = runner.invoke(app, ["tags", "year", "-p", str(root), "--dry-run"])

    assert "Dry run" in result.output
    assert date_of(only_track(root)) == ""


def test_declining_the_prompt_changes_nothing(artist: Callable[..., Path]) -> None:
    """Answering no stops before any write.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")

    result = runner.invoke(app, ["tags", "year", "-p", str(root)], input="n\n")

    assert "No changes made" in result.output
    assert date_of(only_track(root)) == ""


def test_a_second_run_finds_nothing(artist: Callable[..., Path]) -> None:
    """Dating twice is dating once.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    _ = runner.invoke(app, ["tags", "year", "-p", str(root)], input="y\n")

    result = runner.invoke(app, ["tags", "year", "-p", str(root)])

    assert "Nothing to do" in result.output


def test_the_album_tag_is_untouched(artist: Callable[..., Path]) -> None:
    """This command writes Date and nothing else.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    track: Path = only_track(root)
    metadata.write(track, {Tag.ALBUM: "Untouched"})

    _ = runner.invoke(app, ["tags", "year", "-p", str(root)], input="y\n")

    assert metadata.read(track, [Tag.ALBUM])[Tag.ALBUM] == "Untouched"
