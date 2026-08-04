# tests/cli/commands/listing/test_genres.py
"""Tests for the `rapt list genres` command.

A survey changes nothing, so these check what it reports and that it
leaves the shelf exactly as it found it. The resolution itself belongs to
`core.declarations` and is tested there.
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
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def shelf(tmp_path: Path) -> Path:
    """Build a shelf holding two artists and one loose track.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        The shelf's path.
    """
    root: Path = tmp_path / "Jazz"
    for artist in (
        "USA/Miles Davis - [1 • 1F • 0L • 0M]",
        "Japan/Casiopea - [1 • 1F • 0L • 0M]",
    ):
        folder: Path = root / artist / "FLAC" / "01. (1959) - Album [FLAC]"
        folder.mkdir(parents=True)
        for index in (1, 2):
            silence(folder / f"0{index}.flac")

    (root / "Unsorted").mkdir(parents=True)
    silence(root / "Unsorted" / "loose.flac")

    return root


def declare(folder: Path, value: str) -> None:
    """Write a `.genre` declaration into a folder.

    Args:
        folder: The folder to declare for.
        value: What it should declare.
    """
    _ = (folder / ".genre").write_text(f"{value}\n", encoding="utf-8", newline="\n")


# ==================================================================================== #
#                                       SURVEYING                                      #
# ==================================================================================== #
def test_an_untagged_shelf_reports_every_file_as_none(shelf: Path) -> None:
    """Nothing declared and nothing tagged still answers, rather than saying nothing.

    Args:
        shelf: The fixture shelf.
    """
    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    assert result.exit_code == 0
    assert "(none)" in result.output
    assert "5 file(s)" in result.output


def test_a_declaration_answers_without_the_tags_being_read(shelf: Path) -> None:
    """A declared folder reports what it declares, not what its files hold.

    The files here are untagged, so the two genuinely differ -- which is
    the whole point of showing where the answer came from.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Jazz")

    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    assert "Jazz" in result.output
    assert "declared" in result.output


def test_tags_are_read_where_nothing_declares(shelf: Path) -> None:
    """With no `.genre` anywhere, the survey falls back to the files themselves.

    Args:
        shelf: The fixture shelf.
    """
    _ = runner.invoke(app, ["tags", "genre", "-p", str(shelf), "-g", "Bebop"], input="y\n")
    (shelf / ".genre").unlink()

    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    assert "Bebop" in result.output
    assert "tagged" in result.output


def test_the_nearest_declaration_is_the_one_counted(shelf: Path) -> None:
    """Each folder reports under the declaration that actually reaches it.

    Args:
        shelf: The fixture shelf.
    """
    artist: Path = shelf / "USA" / "Miles Davis - [1 • 1F • 0L • 0M]"
    declare(shelf, "Jazz")
    declare(artist, "Bebop")

    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    # Two under the artist, three left to the shelf.
    assert "2 file(s)" in result.output
    assert "3 file(s)" in result.output
    assert "Bebop" in result.output


def test_a_near_miss_sorts_beside_what_it_nearly_matches(shelf: Path) -> None:
    """Sorting by value is the drift detector: "(JP)" lands next to "(JPN)".

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf / "USA" / "Miles Davis - [1 • 1F • 0L • 0M]", "(JPN) Shakuhachi")
    declare(shelf / "Japan" / "Casiopea - [1 • 1F • 0L • 0M]", "(JP) Shakuhachi")

    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])
    lines: list[str] = [line for line in result.output.splitlines() if "Shakuhachi" in line]

    assert len(lines) == 2
    assert "(JP) Shakuhachi" in lines[0]
    assert "(JPN) Shakuhachi" in lines[1]


def test_the_count_of_distinct_genres_is_reported(shelf: Path) -> None:
    """The tally line says how many conventions are in play, which is the alarm.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf / "USA" / "Miles Davis - [1 • 1F • 0L • 0M]", "Bebop")
    declare(shelf / "Japan" / "Casiopea - [1 • 1F • 0L • 0M]", "Fusion")

    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    # Bebop, Fusion, and the loose track's absent tag.
    assert "3 in use" in result.output


def test_an_unusable_declaration_is_refused(shelf: Path) -> None:
    """A survey is as fussy as the command that writes: neither guesses.

    Args:
        shelf: The fixture shelf.
    """
    _ = (shelf / ".genre").write_text("   \n", encoding="utf-8", newline="\n")

    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    assert result.exit_code == 1
    assert "is empty" in result.output


def test_surveying_changes_nothing(shelf: Path) -> None:
    """The read-only half stays read-only, tags and declarations alike.

    Args:
        shelf: The fixture shelf.
    """
    declare(shelf, "Jazz")

    _ = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    assert {metadata.read(t, [Tag.GENRE])[Tag.GENRE] for t in shelf.rglob("*.flac")} == {""}
    assert list(shelf.rglob(".genre")) == [shelf / ".genre"]


def test_one_track_answers_for_its_album(shelf: Path) -> None:
    """The sampling, pinned: the folder is counted whole from its first track.

    An album is tagged as a unit, so reading every track would be twenty
    times the disk for the same answer. The cost is recorded here rather
    than left to be discovered: a track that drifted inside an otherwise
    consistent album does not show up, and its siblings' genre is
    reported for it.

    Args:
        shelf: The fixture shelf.
    """
    album: Path = shelf / "USA" / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]" / "FLAC"
    album = album / "01. (1959) - Album [FLAC]"
    metadata.write(album / "01.flac", {Tag.GENRE: "Bebop"})
    metadata.write(album / "02.flac", {Tag.GENRE: "Strays"})

    result = runner.invoke(app, ["list", "genres", "-p", str(shelf)])

    assert "Bebop" in result.output
    assert "Strays" not in result.output
    # Both tracks counted, under the one genre the first reported.
    assert "2 file(s)" in result.output


def test_a_folder_without_audio_exits_cleanly(tmp_path: Path) -> None:
    """Nothing to survey is not a failure.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    result = runner.invoke(app, ["list", "genres", "-p", str(tmp_path)])

    assert result.exit_code == 0
    assert "No audio files" in result.output
