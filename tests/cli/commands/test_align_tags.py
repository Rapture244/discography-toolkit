# tests/cli/commands/test_align_tags.py
"""Tests for the `rapt align-tags` command.

The command wires the cover pass and a four-tag pass over a laid-out
shelf, so these check the wiring: that each tag is read off the right
folder, that a dry run writes nothing, and that the guards behave. The
individual tag derivations have their own unit tests.
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
def shelf(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building a laid-out artist with one album.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking the album folder name and an initial Title,
        returning the track's path.
    """

    def build(album_name: str = "01. (1959) - Kind of Blue [FLAC]", title: str = "") -> Path:
        artist: Path = tmp_path / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
        album: Path = artist / "FLAC" / album_name
        album.mkdir(parents=True)
        track: Path = album / "01 - so what.flac"
        sf.write(track, np.zeros(441, dtype="float32"), 44100, format="FLAC")
        if title:
            metadata.write(track, {Tag.TITLE: title})
        return track

    return build


def tags_of(track: Path) -> dict[Tag, str]:
    """Read the four aligned tags off a track.

    Args:
        track: The file to read.

    Returns:
        Its Album, Album Artist, Date, and Title.
    """
    return dict(metadata.read(track, [Tag.ALBUM, Tag.ALBUM_ARTIST, Tag.DATE, Tag.TITLE]))


# ==================================================================================== #
#                                     END TO END                                       #
# ==================================================================================== #
def test_the_folder_derived_tags_are_written(shelf: Callable[..., Path]) -> None:
    """Album, Album Artist and Date are read off the folders and written.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="So What")

    result = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="y\n")

    assert result.exit_code == 0
    tags = tags_of(track)
    assert tags[Tag.ALBUM] == "01. (1959) - Kind of Blue [FLAC]"
    assert tags[Tag.ALBUM_ARTIST] == "Miles Davis"
    assert tags[Tag.DATE] == "1959"


def test_the_title_is_recased_not_derived(shelf: Callable[..., Path]) -> None:
    """A messy Title is recased in place, not taken from the filename.

    The filename is "01 - so what"; were the title derived from it, the
    index would leak in. It is the existing tag that is cased.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="so what")

    _ = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="y\n")

    assert tags_of(track)[Tag.TITLE] == "So What"


def test_a_track_with_no_title_is_left_untitled(shelf: Callable[..., Path]) -> None:
    """With nothing to case, the Title is not invented from elsewhere.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="")

    _ = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="y\n")

    assert tags_of(track)[Tag.TITLE] == ""


def test_an_approximate_year_clears_the_date(shelf: Callable[..., Path]) -> None:
    """A "(199x)" is not a date, so the Date field is left empty.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(album_name="01. (199x) - Unknown [FLAC]", title="So What")

    _ = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="y\n")

    assert tags_of(track)[Tag.DATE] == ""


def test_running_twice_settles(shelf: Callable[..., Path]) -> None:
    """A second run finds every tag already right and writes nothing.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="So What")
    root: Path = track.parents[3]

    _ = runner.invoke(app, ["align-tags", "--path", str(root)], input="y\n")
    second = runner.invoke(app, ["align-tags", "--path", str(root)], input="y\n")

    assert second.exit_code == 0
    assert "0 file(s) tagged" in second.stdout


def test_each_pass_shows_by_name(shelf: Callable[..., Path]) -> None:
    """Every pass reports itself, covers before the tags, for visibility.

    A long run should say which work it is doing rather than settle
    covers and write four tags behind one silent bar.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="So What")

    result = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="y\n")

    for name in ("Covers", "Album", "Album Artist", "Year", "Title"):
        assert name in result.stdout
    # Covers is settled before the first tag is written.
    assert result.stdout.index("Covers") < result.stdout.index("Album ")


def test_a_file_needing_several_tags_is_written_once(shelf: Callable[..., Path]) -> None:
    """The tags share one save: the breakdown sums past the files tagged.

    One track needs Album, Album Artist, Year, and a recased Title -- four
    fields, but one file, written once. The per-tag lines each count it,
    while the summary counts the file a single time.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="so what")

    result = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="y\n")

    # Every field landed, proving the one save carried all of them.
    tags = tags_of(track)
    assert tags[Tag.ALBUM]
    assert tags[Tag.ALBUM_ARTIST]
    assert tags[Tag.DATE]
    assert tags[Tag.TITLE] == "So What"
    # The file is counted once, not once per field.
    assert "1 file(s) tagged" in result.stdout


def test_the_breakdown_counts_only_the_tags_that_change(tmp_path: Path) -> None:
    """A tag already right is not counted; only what changes is.

    Album, Album Artist and Date are pre-set correctly and only the Title
    needs recasing, so the breakdown must read the Title alone.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    album: Path = artist / "FLAC" / "01. (1959) - Kind of Blue [FLAC]"
    album.mkdir(parents=True)
    track: Path = album / "01 - so what.flac"
    sf.write(track, np.zeros(441, dtype="float32"), 44100, format="FLAC")
    metadata.write(
        track,
        {
            Tag.ALBUM: "01. (1959) - Kind of Blue [FLAC]",
            Tag.ALBUM_ARTIST: "Miles Davis",
            Tag.DATE: "1959",
            Tag.TITLE: "so what",  # the one field that needs a change
        },
    )

    result = runner.invoke(app, ["align-tags", "--path", str(tmp_path)], input="y\n")

    assert f"  {'Album':<13} 0 tagged" in result.stdout
    assert f"  {'Title':<13} 1 recased" in result.stdout


# ==================================================================================== #
#                                     DRY RUN & GUARDS                                 #
# ==================================================================================== #
def test_a_dry_run_writes_nothing(shelf: Callable[..., Path]) -> None:
    """The plan is shown, but no tag is touched.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="So What")

    result = runner.invoke(app, ["align-tags", "--path", str(track.parents[3]), "--dry-run"])

    assert result.exit_code == 0
    assert tags_of(track)[Tag.ALBUM] == ""


def test_declining_the_prompt_writes_nothing(shelf: Callable[..., Path]) -> None:
    """Answering no leaves every tag as it was.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="So What")

    result = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="n\n")

    assert result.exit_code == 0
    assert tags_of(track)[Tag.ALBUM] == ""


def test_unlaid_material_is_an_error(tmp_path: Path) -> None:
    """Without labelled artists, there are no folders to read tags from.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    # An unlabelled artist: no count label, so find_albums sees nothing.
    album: Path = tmp_path / "Miles Davis" / "(1959) - Kind of Blue"
    album.mkdir(parents=True)
    sf.write(album / "track.flac", np.zeros(441, dtype="float32"), 44100, format="FLAC")

    result = runner.invoke(app, ["align-tags", "--path", str(tmp_path)], input="y\n")

    assert result.exit_code == 1


def test_genre_is_never_touched(shelf: Callable[..., Path]) -> None:
    """Align leaves Genre alone: it is the one tag not derived from folders.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="So What")
    metadata.write(track, {Tag.GENRE: "Jazz"})

    _ = runner.invoke(app, ["align-tags", "--path", str(track.parents[3])], input="y\n")

    assert metadata.read(track, [Tag.GENRE])[Tag.GENRE] == "Jazz"


def test_a_forced_album_artist_overrides_the_folder(shelf: Callable[..., Path]) -> None:
    """--album-artist wins over the name derived from the artist folder.

    The folder says "Miles Davis"; the flag says "DJ Screw", and it is
    the flag that lands in the file. The other tags are unaffected.

    Args:
        shelf: Factory building a laid-out artist.
    """
    track: Path = shelf(title="So What")

    result = runner.invoke(
        app,
        ["align-tags", "--path", str(track.parents[3]), "--album-artist", "DJ Screw"],
        input="y\n",
    )

    assert result.exit_code == 0
    tags = tags_of(track)
    assert tags[Tag.ALBUM_ARTIST] == "DJ Screw"
    assert tags[Tag.ALBUM] == "01. (1959) - Kind of Blue [FLAC]"  # still folder-derived
    assert "DJ Screw" in result.stdout  # named in the confirmation
