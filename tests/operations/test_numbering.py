# tests/operations/test_numbering.py
"""Tests for numbering an artist's albums into one sequence.

Run against real folders. Albums need no contents -- numbering reads only
their names -- so they are made as bare directories, which keeps the
ordering and the two-phase rename in plain view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.operations import numbering

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def shelf(tmp_path: Path) -> Callable[..., list[Path]]:
    """Return a factory making album folders and handing back their paths.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking folder names and returning their paths, in the
        order given.
    """

    def build(*names: str) -> list[Path]:
        made: list[Path] = []
        for name in names:
            album: Path = tmp_path / name
            album.mkdir()
            made.append(album)
        return made

    return build


def names_on_disk(album: Path) -> list[str]:
    """List the folder names sitting beside an album, sorted.

    Args:
        album: Any album, used to find its parent.

    Returns:
        The visible sibling folder names.
    """
    return sorted(
        child.name for child in album.parent.iterdir() if child.is_dir() and child.name[0] != "."
    )


# ==================================================================================== #
#                                       ORDERING                                       #
# ==================================================================================== #
def test_albums_number_by_year_then_title(shelf: Callable[..., list[Path]]) -> None:
    """The index follows year and then title, whatever order they arrive in.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf(
        "01. (1970) - Bitches Brew",
        "02. (1959) - So What",
        "03. (1959) - Ascension",
    )

    result = numbering.plan(albums)

    assert [outcome.new_name for outcome in result.outcomes] == [
        "01. (1959) - Ascension",
        "02. (1959) - So What",
        "03. (1970) - Bitches Brew",
    ]


def test_the_input_order_does_not_decide_the_numbering(
    shelf: Callable[..., list[Path]],
) -> None:
    """Sorting is the operation's own; the caller's order is not trusted.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("(1980) - Zeta", "(1960) - Alpha")

    result = numbering.plan(list(reversed(albums)))

    assert result.outcomes[0].new_name == "01. (1960) - Alpha"
    assert result.outcomes[1].new_name == "02. (1980) - Zeta"


def test_availability_does_not_move_an_album(shelf: Callable[..., list[Path]]) -> None:
    """A missing album holds the place its title earns, not the marker's.

    Were the "M" to count, it would sort mid-alphabet and the album would
    jump, renumbering everything after it the next time it was held.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf(
        "(1959) - Ascension",
        "(1959) - M - Blues",
        "(1959) - Cookin",
    )

    result = numbering.plan(albums)

    assert [outcome.new_name for outcome in result.outcomes] == [
        "01. (1959) - Ascension",
        "02. (1959) - M - Blues",
        "03. (1959) - Cookin",
    ]


# ==================================================================================== #
#                                      ASSEMBLY                                        #
# ==================================================================================== #
def test_an_old_index_is_replaced(shelf: Callable[..., list[Path]]) -> None:
    """Whatever prefix a folder wore, it gets the freshly assigned one.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("007_ (1959) - Kind of Blue", "(1959) - Ascension")

    result = numbering.plan(albums)

    assert result.outcomes[1].new_name == "02. (1959) - Kind of Blue"


def test_the_pin_stays_ahead_of_the_index(shelf: Callable[..., list[Path]]) -> None:
    """A "©" keeps its place at the very front, before the number.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("©(2001) - Later")

    assert numbering.plan(albums).outcomes[0].new_name == "©01. (2001) - Later"


def test_the_index_is_two_digits_at_least(shelf: Callable[..., list[Path]]) -> None:
    """A short run still pads to two, so a browser sorts "02" under "10".

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("(1959) - A", "(1970) - B")

    assert numbering.plan(albums).outcomes[0].new_name.startswith("01. ")


def test_the_index_widens_past_ninety_nine(shelf: Callable[..., list[Path]]) -> None:
    """A hundred-album run needs three digits, and "001" keeps them aligned.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf(*(f"({1900 + n}) - Album {n:03d}" for n in range(100)))

    result = numbering.plan(albums)

    assert result.outcomes[0].new_name.startswith("001. ")
    assert result.outcomes[99].new_name.startswith("100. ")


def test_an_album_without_a_year_is_still_numbered(
    shelf: Callable[..., list[Path]],
) -> None:
    """Numbering names the sequence, not the album; a yearless one joins it.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("(1959) - Dated", "No Year Here")

    result = numbering.plan(albums)

    assert any(outcome.new_name.endswith("No Year Here") for outcome in result.outcomes)


# ==================================================================================== #
#                                     THE WHOLE PLAN                                   #
# ==================================================================================== #
def test_a_correctly_numbered_album_is_clean(shelf: Callable[..., list[Path]]) -> None:
    """An album already carrying its index is not work.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("01. (1959) - Ascension", "02. (1970) - Brew")

    result = numbering.plan(albums)

    assert result.pending == ()
    assert result.clean == 2


def test_only_the_misnumbered_albums_are_pending(
    shelf: Callable[..., list[Path]],
) -> None:
    """The one already right is left alone; the other is queued.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("01. (1959) - Ascension", "99. (1970) - Brew")

    result = numbering.plan(albums)

    assert result.total == 2
    assert len(result.pending) == 1
    assert result.pending[0].album.name == "99. (1970) - Brew"


def test_progress_is_reported_for_every_album(shelf: Callable[..., list[Path]]) -> None:
    """The caller drives a display without this module knowing one exists.

    Progress follows numbered order, not the order given.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("(1970) - B", "(1959) - A")
    seen: list[Path] = []

    result = numbering.plan(albums, on_progress=seen.append)

    assert seen == [outcome.album for outcome in result.outcomes]


def test_nothing_is_written_by_planning(shelf: Callable[..., list[Path]]) -> None:
    """Planning is safe to run just to read the answer.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("99. (1959) - Ascension")

    _ = numbering.plan(albums)

    assert albums[0].name == "99. (1959) - Ascension"


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def test_applying_renumbers_the_folders(shelf: Callable[..., list[Path]]) -> None:
    """The folders on disk take their settled indices.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("(1970) - Brew", "(1959) - Ascension")

    report = numbering.apply(numbering.plan(albums))

    assert report.renamed == 2
    assert names_on_disk(albums[0]) == ["01. (1959) - Ascension", "02. (1970) - Brew"]


def test_applying_survives_a_name_swap(shelf: Callable[..., list[Path]]) -> None:
    """Two albums trading numbers is the whole reason for the two phases.

    Alpha is currently "02" and should be "01"; Beta is "01" and should
    be "02". Renamed in one pass, Alpha's target would collide with
    Beta's current name -- staging both first is what avoids it.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("02. (1959) - Alpha", "01. (1959) - Beta")

    report = numbering.apply(numbering.plan(albums))

    assert report.renamed == 2
    assert names_on_disk(albums[0]) == ["01. (1959) - Alpha", "02. (1959) - Beta"]


def test_applying_leaves_no_staging_names_behind(
    shelf: Callable[..., list[Path]],
) -> None:
    """A finished run shows only final names, no half-renamed folders.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("03. (1959) - A", "01. (1970) - B", "02. (1980) - C")

    _ = numbering.apply(numbering.plan(albums))

    assert all(not name.startswith(".") for name in names_on_disk(albums[0]))


def test_applying_is_idempotent(shelf: Callable[..., list[Path]]) -> None:
    """A second run finds the sequence settled and changes nothing.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("(1970) - Brew", "(1959) - Ascension")
    parent: Path = albums[0].parent

    _ = numbering.apply(numbering.plan(albums))
    settled: list[Path] = [child for child in parent.iterdir() if child.is_dir()]
    second = numbering.plan(settled)

    assert second.pending == ()


def test_a_folder_that_vanished_is_reported(shelf: Callable[..., list[Path]]) -> None:
    """A folder removed after planning fails to stage, and the run goes on.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("99. (1959) - Ascension", "98. (1970) - Brew")
    plan = numbering.plan(albums)
    # The first pending album is deleted between planning and applying.
    gone: Path = plan.pending[0].album
    gone.rmdir()

    report = numbering.apply(plan)

    assert len(report.failures) == 1
    assert report.failures[0][0] == gone
    # The other album still reaches its number.
    assert any(name[0].isdigit() and " - Brew" in name for name in names_on_disk(albums[0]))


def test_a_clean_album_is_not_touched_when_applying(
    shelf: Callable[..., list[Path]],
) -> None:
    """Only the misnumbered folders move; the settled ones are left alone.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("01. (1959) - Ascension", "99. (1970) - Brew")
    plan = numbering.plan(albums)

    report = numbering.apply(plan)

    assert report.renamed == len(plan.pending) == 1
    assert names_on_disk(albums[0]) == ["01. (1959) - Ascension", "02. (1970) - Brew"]


def test_a_blocked_final_rename_is_reported(shelf: Callable[..., list[Path]]) -> None:
    """A target held by something outside the run is reported, not forced.

    The album stages successfully but cannot reach its final name, so it
    is left under its staging name -- visible and recoverable -- rather
    than overwriting whatever is in the way.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("99. (1959) - Ascension")
    plan = numbering.plan(albums)
    target: Path = plan.pending[0].target
    # A non-empty folder sitting exactly where the album wants to land,
    # not itself part of the run.
    target.mkdir()
    _ = (target / "keep.txt").write_text("do not lose me")

    report = numbering.apply(plan)

    assert report.renamed == 0
    assert len(report.failures) == 1
    assert report.failures[0][0] == target
    assert (target / "keep.txt").read_text() == "do not lose me"


def test_progress_is_reported_for_every_rename(shelf: Callable[..., list[Path]]) -> None:
    """Every folder touched announces itself, so a bar can be sized from the plan.

    Args:
        shelf: Factory making album folders.
    """
    albums: list[Path] = shelf("02. (1959) - A", "01. (1970) - B")
    plan = numbering.plan(albums)
    seen: list[Path] = []

    _ = numbering.apply(plan, on_progress=seen.append)

    assert len(seen) == len(plan.pending)
