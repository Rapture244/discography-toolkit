# src/discography_toolkit/operations/numbering.py
"""Numbering an artist's albums into one continuous sequence.

Albums order by year and title, and each gets a "01.", "02." index in
that order -- the number a file browser sorts on, so the shelf reads
chronologically without the year having to lead every name. Numbering
runs across a whole artist at once, the lossy albums beside the artist
folder and the lossless ones inside its container pooled into a single
run, because they are one discography however they are stored.

What an album is called past its index -- the "©" pin, the year, the
title, the availability marker -- plays no part in where it sorts, only
the year and title do. That is what keeps the sequence stable: an album
found missing and later held keeps its number instead of shuffling
everything after it.

Planning never writes. Applying renames in two phases -- every folder to
a temporary name, then each to its final one -- because renumbering
swaps names around, and a folder's new name is often another folder's
current one until that other has moved out of the way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from discography_toolkit.core import names

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Two digits at least, so "01." reads as a sequence rather than a lone
# "1." that a browser sorts after "10.". A run past ninety-nine widens to
# match its own length.
_MIN_INDEX_WIDTH: int = 2

# The prefix a folder wears while renaming, distinct enough that no album
# is called it. Chosen so the intermediate state is obvious if a run is
# interrupted between the two phases.
_STAGING_PREFIX: str = ".__renumbering__"


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Numbering:
    """The index one album should carry.

    Attributes:
        album: The album folder as it stands.
        new_name: The name it should have once numbered.
    """

    album: Path
    new_name: str

    @property
    def needs_rename(self) -> bool:
        """Whether applying this would change the folder on disk."""
        return self.new_name != self.album.name

    @property
    def target(self) -> Path:
        """Where the folder would be renamed to."""
        return self.album.parent / self.new_name


@dataclass(frozen=True, slots=True)
class NumberPlan:
    """What a run would renumber, before anything is written.

    Attributes:
        outcomes: One entry per album, in the order they were numbered.
    """

    outcomes: tuple[Numbering, ...]

    @property
    def total(self) -> int:
        """How many albums were numbered."""
        return len(self.outcomes)

    @property
    def pending(self) -> tuple[Numbering, ...]:
        """Albums whose folder name would change."""
        return tuple(outcome for outcome in self.outcomes if outcome.needs_rename)

    @property
    def clean(self) -> int:
        """Albums already carrying the right index."""
        return sum(1 for outcome in self.outcomes if not outcome.needs_rename)


@dataclass(frozen=True, slots=True)
class NumberReport:
    """What happened when a plan was applied.

    Attributes:
        renamed: How many folders were successfully renamed.
        failures: `(album, reason)` for each rename that failed.
    """

    renamed: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    albums: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> NumberPlan:
    """Order the albums and work out each one's index, without writing.

    The albums are sorted here rather than trusted in the order given:
    the index follows year and title, and only this module knows to look
    past the pin, the old index and the availability marker to find them.

    Args:
        albums: One artist's album folders, the container already
            unwrapped. Discovery belongs to the caller.
        on_progress: Called with each album as it is numbered, so a
            caller can drive a display without this module knowing one
            exists.

    Returns:
        One outcome per album, in numbered order.
    """
    ordered: list[Path] = sorted(albums, key=lambda album: names.sort_key(album.name))
    width: int = max(_MIN_INDEX_WIDTH, len(str(len(ordered))))

    outcomes: list[Numbering] = []
    for index, album in enumerate(ordered, start=1):
        outcomes.append(Numbering(album=album, new_name=_renumbered(album.name, index, width)))
        if on_progress is not None:
            on_progress(album)

    return NumberPlan(outcomes=tuple(outcomes))


def apply(
    number_plan: NumberPlan,
    on_progress: Callable[[Path], None] | None = None,
) -> NumberReport:
    """Renumber every folder the plan found, in two phases.

    Each folder moves to a temporary name first, then to its final one,
    so a target still held by another folder mid-run is never a
    collision. A folder that fails to stage is left where it is and
    reported; the run continues.

    Args:
        number_plan: A plan produced by `plan`.
        on_progress: Called with each album as it is dealt with.

    Returns:
        A count of successes and the failures collected along the way.
    """
    staged: list[tuple[Path, Path]] = []
    failures: list[tuple[Path, str]] = []

    for outcome in number_plan.pending:
        staging: Path = outcome.album.with_name(f"{_STAGING_PREFIX}{outcome.album.name}")
        detail: str | None = _move(outcome.album, staging)
        if detail is None:
            staged.append((staging, outcome.target))
        else:
            failures.append((outcome.album, detail))
            if on_progress is not None:
                on_progress(outcome.album)

    renamed: int = 0
    for staging, target in staged:
        detail = _move(staging, target)
        if detail is None:
            renamed += 1
        else:
            # Left under its staging name: visible, and recoverable by a
            # rerun, rather than lost.
            failures.append((target, detail))
        if on_progress is not None:
            on_progress(target)

    return NumberReport(renamed=renamed, failures=tuple(failures))


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _renumbered(name: str, index: int, width: int) -> str:
    """Rebuild a folder name with a fresh index at its front.

    The pin is kept ahead of the number and the old index is dropped;
    everything past it -- year, marker, title, quality tag -- is left as
    it was. Numbering settles the sequence, not the rest of the name.

    Args:
        name: The album folder's current name.
        index: Its position in the sequence, from one.
        width: How many digits to pad the index to.

    Returns:
        The renumbered name.
    """
    pin, rest = names.split_pin_mark(name)
    _, rest = names.split_index(rest)
    return f"{pin}{index:0{width}d}. {rest}"


def _move(source: Path, target: Path) -> str | None:
    """Rename one folder, reporting failure rather than raising.

    Args:
        source: The folder to move.
        target: Where it should go.

    Returns:
        The failure's detail, or `None` on success.
    """
    try:
        _ = source.rename(target)
    except OSError as exc:
        return str(exc)
    return None
