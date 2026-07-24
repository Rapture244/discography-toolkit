# tests/cli/test_console.py
"""Tests for the rendering primitives.

Covers the parts that compute something -- bar fill, counter padding,
column widths, which artist owns a track -- and not the parts that only
emit escape codes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from discography_toolkit.cli.console import (
    DashBarColumn,
    FileCountColumn,
    SummaryRow,
    artist_names,
    echo_summary,
    make_advancer,
    make_progress,
)

import pytest

if TYPE_CHECKING:
    from pathlib import Path


# ==================================================================================== #
#                                       HELPERS                                        #
# ==================================================================================== #
class FakeTask:
    """A stand-in for Rich's `Task`, carrying only what the columns read.

    Attributes:
        completed: Units of work done.
        total: Units of work expected, or `None` when unknown.
    """

    def __init__(self, completed: float, total: float | None) -> None:
        """Initialize the stand-in.

        Args:
            completed: Units of work done.
            total: Units of work expected, or `None`.
        """
        self.completed: float = completed
        self.total: float | None = total


def strip_ansi(text: str) -> str:
    """Remove colour escape codes so output can be compared as text.

    Args:
        text: Captured terminal output.

    Returns:
        The same text with SGR sequences removed.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ==================================================================================== #
#                                     PROGRESS BARS                                    #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("completed", "total", "filled"),
    [
        (0, 100, 0),
        (50, 100, 25),
        (100, 100, 50),
        # Floored: a dash turns green only once its share is finished.
        (1, 100, 0),
        (99, 100, 49),
    ],
)
def test_dash_bar_fill(completed: int, total: int, filled: int) -> None:
    """The green run is the floored fraction of the bar's width.

    Args:
        completed: Units of work done.
        total: Units of work expected.
        filled: Dashes expected to be green.
    """
    rendered = DashBarColumn(bar_width=50).render(FakeTask(completed, total))  # pyright: ignore[reportArgumentType]

    # Rich drops zero-length spans, so count the styled run rather than
    # indexing into spans.
    green: int = sum(span.end - span.start for span in rendered.spans if span.style == "bold green")
    assert len(rendered.plain) == 50
    assert green == filled


@pytest.mark.parametrize(("completed", "total"), [(0, 0), (0, None), (5, None), (5, -1)])
def test_dash_bar_survives_an_unusable_total(completed: int, total: int | None) -> None:
    """A zero, missing or negative total renders an empty bar, not a crash.

    Args:
        completed: Units of work done.
        total: An unusable total.
    """
    rendered = DashBarColumn(bar_width=50).render(FakeTask(completed, total))  # pyright: ignore[reportArgumentType]

    green: int = sum(span.end - span.start for span in rendered.spans if span.style == "bold green")
    assert rendered.plain == "-" * 50
    assert green == 0


def test_dash_bar_clamps_an_overshoot() -> None:
    """A step advancing past its total must not draw past the bar's width."""
    rendered = DashBarColumn(bar_width=50).render(FakeTask(150, 100))  # pyright: ignore[reportArgumentType]

    assert len(rendered.plain) == 50


def test_file_count_shows_integers() -> None:
    """Rich keeps `completed` as a float; the counter must not show "58.0"."""
    rendered = FileCountColumn().render(FakeTask(58.0, 103.0))  # pyright: ignore[reportArgumentType]

    assert rendered.plain == "( 58/103 files)"


@pytest.mark.parametrize("completed", [0, 7, 96, 1103])
def test_file_count_keeps_a_constant_width(completed: int) -> None:
    """The counter must not shift the line beside it as digits accumulate.

    Args:
        completed: Units of work done.
    """
    rendered = FileCountColumn().render(FakeTask(completed, 1103))  # pyright: ignore[reportArgumentType]

    assert len(rendered.plain) == len("(1103/1103 files)")


def test_file_count_names_what_it_counts() -> None:
    """One step walks albums, and a bar calling them files is simply wrong."""
    rendered = FileCountColumn(noun="albums").render(FakeTask(4, 4))  # pyright: ignore[reportArgumentType]

    assert rendered.plain == "(4/4 albums)"


def test_the_noun_reaches_the_counter() -> None:
    """Choosing it on the display is the only way a command can set it."""
    progress = make_progress(noun="operations")

    columns = [column for column in progress.columns if isinstance(column, FileCountColumn)]
    assert [column.noun for column in columns] == ["operations"]


# ==================================================================================== #
#                                     BANNER SCOPE                                     #
# ==================================================================================== #
def test_artist_names_lists_the_artists_beneath_a_shelf(tmp_path: Path) -> None:
    """A shelf's banner says which artists the run covers.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    shelf: Path = tmp_path / "Jazz"
    artists: list[Path] = [shelf / "Miles Davis - [1 on 1]", shelf / "Sun Ra - [2 on 2]"]
    for artist in artists:
        artist.mkdir(parents=True)

    assert artist_names(shelf, artists) == ["Miles Davis - [1 on 1]", "Sun Ra - [2 on 2]"]


def test_artist_names_says_nothing_under_an_artist(tmp_path: Path) -> None:
    """The banner already names it; repeating it underneath says nothing.

    Passed itself, as `find_artist_folders` returns for an artist target,
    so the list has to be judged against the target rather than trusted.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis - [65 • 65F • 0L • 0M]"
    artist.mkdir()

    assert artist_names(artist, [artist]) == []


# ==================================================================================== #
#                                    ARTIST TRACKING                                   #
# ==================================================================================== #
def test_advancer_removes_an_artist_bar_when_it_completes(tmp_path: Path) -> None:
    """Each artist's bar disappears as its last file is handled.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    one: Path = tmp_path / "One - [1 on 1]"
    two: Path = tmp_path / "Two - [1 on 1]"
    tracks: list[Path] = [one / "a.flac", one / "b.flac", two / "c.flac"]

    with make_progress() as progress:
        advance = make_advancer(progress, "shelf", tracks, [one, two])
        assert len(progress.tasks) == 3  # overall plus one per artist

        advance(tracks[0])
        assert len(progress.tasks) == 3

        advance(tracks[1])  # One is done
        assert len(progress.tasks) == 2

        advance(tracks[2])  # Two is done
        assert len(progress.tasks) == 1


def test_advancer_counts_loose_tracks_in_the_total_only(tmp_path: Path) -> None:
    """A track under no artist still advances the overall bar.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    one: Path = tmp_path / "One - [1 on 1]"
    tracks: list[Path] = [one / "a.flac", tmp_path / "loose.flac"]

    with make_progress() as progress:
        advance = make_advancer(progress, "shelf", tracks, [one])
        overall = progress.tasks[0]

        advance(tracks[1])

        assert overall.completed == 1
        assert len(progress.tasks) == 2  # the artist bar is untouched


# ==================================================================================== #
#                                     SUMMARY BOX                                      #
# ==================================================================================== #
def test_summary_box_aligns_every_row(capsys: pytest.CaptureFixture[str]) -> None:
    """Rows of differing label and count width still line up.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_summary(
        [
            [SummaryRow(label="Total", count=538, indent="", percent=False)],
            [
                SummaryRow(label="Tagged", count=7, marker="(->)"),
                SummaryRow(label="Clean", count=531, marker="(==)"),
            ],
        ],
        total=538,
    )

    box: list[str] = strip_ansi(capsys.readouterr().out).splitlines()

    assert len({len(line) for line in box}) == 1
    assert box[2].startswith("├")
    assert box[2].endswith("┤")


def test_summary_box_aligns_the_colons(capsys: pytest.CaptureFixture[str]) -> None:
    """Every row's colon sits in the same column, whatever the label lengths.

    Uniform line length is not enough: the frame stays square while the
    columns wander if either is sized from one row. Counts are given
    narrowest-first so a column sized from the first row is visible.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_summary(
        [
            [
                SummaryRow(label="Tagged", count=7, marker="(->)"),
                SummaryRow(label="A much longer label", count=531, marker="(==)"),
                SummaryRow(label="Clean", count=1103, marker="(!!)"),
            ]
        ],
        total=1103,
    )

    rows: list[str] = strip_ansi(capsys.readouterr().out).splitlines()[1:-1]

    assert len({row.index(":") for row in rows}) == 1
    assert len({row.index("(", row.index(":")) for row in rows}) == 1


def test_summary_box_is_silent_without_rows(capsys: pytest.CaptureFixture[str]) -> None:
    """Nothing to summarize prints nothing, not an empty frame.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_summary([], total=0)

    assert capsys.readouterr().out == ""


def test_summary_box_omits_a_percentage_when_asked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The total is not a share of itself.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_summary(
        [
            [
                SummaryRow(label="Total", count=10, indent="", percent=False),
                SummaryRow(label="Tagged", count=5, marker="(->)"),
            ]
        ],
        total=10,
    )

    lines: list[str] = strip_ansi(capsys.readouterr().out).splitlines()

    assert "%" not in lines[1]
    assert "50.00%" in lines[2]
