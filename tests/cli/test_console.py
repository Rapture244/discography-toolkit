# tests/cli/test_console.py
"""Tests for the rendering primitives.

Covers the parts that compute something -- bar fill, counter padding,
column widths, which artist owns a track -- and not the parts that only
emit escape codes.
"""

from __future__ import annotations

from pathlib import Path
import re

from discography_toolkit.cli.console import (
    BannerLine,
    DashBarColumn,
    FileCountColumn,
    Notice,
    artist_names,
    echo_result,
    make_advancer,
    make_progress,
)

import pytest


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

    assert artist_names(shelf, artists) == [
        BannerLine("Miles Davis - [1 on 1]"),
        BannerLine("Sun Ra - [2 on 2]"),
    ]


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


def test_artist_names_groups_the_nested_under_their_folder(tmp_path: Path) -> None:
    """Nine names off five regions say nothing about which came from where.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    shelf: Path = tmp_path / "Traditional Sounds"
    artists: list[Path] = [
        shelf / "Africa" / "(Mali) - Toumani - [1 on 1]",
        shelf / "Africa" / "(Guinea) - Mamady - [2 on 2]",
        shelf / "Tuva" / "Huun Huur Tu - [3 on 3]",
    ]

    assert artist_names(shelf, artists) == [
        BannerLine("Africa", heading=True),
        BannerLine("  (Mali) - Toumani - [1 on 1]"),
        BannerLine("  (Guinea) - Mamady - [2 on 2]"),
        BannerLine("Tuva", heading=True),
        BannerLine("  Huun Huur Tu - [3 on 3]"),
    ]


def test_artist_names_heads_a_group_with_its_whole_path(tmp_path: Path) -> None:
    """Two folders each holding a "Rap - Experimental" are not one group.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    shelf: Path = tmp_path / "Rap"
    artists: list[Path] = [
        shelf / "Rap FR" / "Rap - Experimental" / "Fauve - [1 on 1]",
        shelf / "Rap US" / "Rap - Experimental" / "JPEGMAFIA - [2 on 2]",
    ]

    headings: list[str] = [line.text for line in artist_names(shelf, artists) if line.heading]

    assert headings == [
        str(Path("Rap FR") / "Rap - Experimental"),
        str(Path("Rap US") / "Rap - Experimental"),
    ]


def test_artist_names_lists_the_direct_ones_first(tmp_path: Path) -> None:
    """An artist sitting in the target itself has no folder to be headed by.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    shelf: Path = tmp_path / "Jazz"
    artists: list[Path] = [
        shelf / "Africa" / "Fela Kuti - [1 on 1]",
        shelf / "Sun Ra - [2 on 2]",
    ]

    assert artist_names(shelf, artists) == [
        BannerLine("Sun Ra - [2 on 2]"),
        BannerLine("Africa", heading=True),
        BannerLine("  Fela Kuti - [1 on 1]"),
    ]


def test_artist_names_says_nothing_under_an_unlabelled_artist(tmp_path: Path) -> None:
    """Fresh material has no label yet, and `find_artists` still returns it.

    The labelled target is caught by `is_artist_folder`; this one is not,
    and its own parent sits outside the target -- which `relative_to`
    would refuse.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
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
#                                    RESULT LINES                                      #
# ==================================================================================== #
def test_result_line_names_the_pass_and_its_count(capsys: pytest.CaptureFixture[str]) -> None:
    """The line is the outcome: which pass, how many, and how that reads.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_result("Album Artist", 12, "tagged")

    assert strip_ansi(capsys.readouterr().out).rstrip() == "  Album Artist  12 tagged"


def test_result_lines_align_their_counts(capsys: pytest.CaptureFixture[str]) -> None:
    """Counts line up down a run whichever passes it happens to include.

    A run through align-tags prints five of these in sequence, so a label
    column sized per line would stagger them.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_result("Album Artist", 12, "tagged")
    echo_result("Year", 3, "dated")
    echo_result("Title", 1103, "recased")

    lines: list[str] = strip_ansi(capsys.readouterr().out).splitlines()

    counts: tuple[int, ...] = (12, 3, 1103)
    starts: set[int] = {line.index(str(count)) for line, count in zip(lines, counts, strict=True)}

    assert len(starts) == 1


def test_a_notice_nests_under_its_line(capsys: pytest.CaptureFixture[str]) -> None:
    """Three levels: the result, what it noticed, and the files it names.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_result(
        "Year",
        3,
        "to date",
        [Notice(summary="2 file(s) have no year to take", details=("'a.flac'", "'b.flac'"))],
    )

    lines: list[str] = strip_ansi(capsys.readouterr().out).splitlines()

    assert lines[0].startswith("  Year")
    assert lines[1] == "      2 file(s) have no year to take"
    assert lines[2] == "          'a.flac'"
    assert lines[3] == "          'b.flac'"


def test_a_notice_needs_no_details(capsys: pytest.CaptureFixture[str]) -> None:
    """Some notices say all there is: naming forty placeholders helps no one.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_result("Cover files", 0, "to settle", [Notice(summary="40 placeholder(s) hold no tracks")])

    lines: list[str] = strip_ansi(capsys.readouterr().out).splitlines()

    assert len(lines) == 2
    assert lines[1] == "      40 placeholder(s) hold no tracks"


def test_a_result_line_reports_its_failures(capsys: pytest.CaptureFixture[str]) -> None:
    """A failure is not a notice: something was attempted and did not work.

    Args:
        capsys: Pytest's captured stdout.
    """
    echo_result("Title", 1, "recased", failures=[(Path("broken.flac"), "not audio")])

    lines: list[str] = strip_ansi(capsys.readouterr().out).splitlines()

    assert "broken.flac" in lines[1]
    assert "not audio" in lines[1]
