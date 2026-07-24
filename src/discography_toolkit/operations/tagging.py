# src/discography_toolkit/operations/tagging.py
"""Planning and applying a metadata change across a set of tracks.

Four of the metadata steps -- album, album artist, genre, title -- do the
same thing and differ only in what value they want. That difference is a
function here rather than four near-identical modules:

    genre         lambda track, current: {Tag.GENRE: "Jazz"}
    album artist  lambda track, current: {Tag.ALBUM_ARTIST: from_folder(track)}
    title         lambda track, current: {Tag.TITLE: cased(current[Tag.TITLE])}

Planning never writes. It reads what is there, asks for what should be
there, and records the difference -- so a caller can show the whole
change before any of it happens, and a file already correct is left
untouched rather than rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from discography_toolkit.core import metadata

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

    from discography_toolkit.core.metadata import Tag

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
type TrackStatus = Literal["updated", "already_correct", "error"]

_NO_VALUES: Final[Mapping[Tag, str]] = MappingProxyType({})
"""Shared empty default: immutable, so one instance is safe for every outcome."""

type Desired = Callable[[Path, Mapping[Tag, str]], Mapping[Tag, str]]
"""Given a track and its current tags, return the values it should hold.

Receives the current values so a step can derive from them -- title
casing reads the existing title -- and returns only the tags it intends
to change.
"""


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class TrackOutcome:
    """What examining one track found.

    Attributes:
        path: The track examined.
        status: Whether it needs writing, is already right, or could not
            be read.
        values: The tags to write, empty unless `status` is `"updated"`.
        detail: Why it failed, populated only for `"error"`.
    """

    path: Path
    status: TrackStatus
    values: Mapping[Tag, str] = _NO_VALUES
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TagPlan:
    """What a run would change, before anything is written.

    Attributes:
        outcomes: One entry per track examined, in the order given.
    """

    outcomes: tuple[TrackOutcome, ...]

    @property
    def total(self) -> int:
        """How many tracks were examined."""
        return len(self.outcomes)

    @property
    def pending(self) -> tuple[TrackOutcome, ...]:
        """Tracks whose tags need changing."""
        return tuple(o for o in self.outcomes if o.status == "updated")

    @property
    def clean(self) -> int:
        """How many tracks already hold the right values."""
        return sum(1 for o in self.outcomes if o.status == "already_correct")

    @property
    def errors(self) -> tuple[TrackOutcome, ...]:
        """Tracks that could not be read."""
        return tuple(o for o in self.outcomes if o.status == "error")


@dataclass(frozen=True, slots=True)
class WriteReport:
    """What happened when a plan was applied.

    Attributes:
        written: How many tracks were successfully tagged.
        failures: `(track, reason)` for each write that failed.
    """

    written: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    tracks: Sequence[Path],
    tags: Iterable[Tag],
    desired: Desired,
    on_progress: Callable[[Path], None] | None = None,
) -> TagPlan:
    """Work out which tracks need changing, without writing.

    Args:
        tracks: The files to examine. Discovery belongs to the caller.
        tags: Which fields to read and compare.
        desired: Given a track and its current values, the values it
            should hold.
        on_progress: Called with each track as it is examined, so a
            caller can drive a display without this module knowing one
            exists.

    Returns:
        One outcome per track, in the order given.
    """
    wanted: list[Tag] = list(tags)
    outcomes: list[TrackOutcome] = []

    for track in tracks:
        outcomes.append(_examine(track, wanted, desired))
        if on_progress is not None:
            on_progress(track)

    return TagPlan(outcomes=tuple(outcomes))


def apply(
    tag_plan: TagPlan,
    on_progress: Callable[[Path], None] | None = None,
) -> WriteReport:
    """Write every change the plan found.

    A file that fails is recorded and the run continues: one unwritable
    track should not abandon the other five hundred.

    Args:
        tag_plan: A plan produced by `plan`.
        on_progress: Called with each track as it is written.

    Returns:
        A count of successes and the failures collected along the way.
    """
    written: int = 0
    failures: list[tuple[Path, str]] = []

    for outcome in tag_plan.pending:
        try:
            metadata.write(outcome.path, outcome.values)
        except Exception as exc:  # noqa: BLE001 - a corrupt file must not stop the run
            failures.append((outcome.path, str(exc)))
        else:
            written += 1
        if on_progress is not None:
            on_progress(outcome.path)

    return WriteReport(written=written, failures=tuple(failures))


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _examine(track: Path, tags: Sequence[Tag], desired: Desired) -> TrackOutcome:
    """Read one track and decide whether it needs writing.

    Args:
        track: The file to examine.
        tags: Which fields to read and compare.
        desired: Given the track and its current values, what it should
            hold.

    Returns:
        The outcome for this track.
    """
    try:
        current: dict[Tag, str] = metadata.read(track, tags)
        wanted: Mapping[Tag, str] = desired(track, current)
    except Exception as exc:  # noqa: BLE001 - an unreadable file is reported, not raised
        return TrackOutcome(path=track, status="error", detail=str(exc))

    changes: dict[Tag, str] = {
        tag: value for tag, value in wanted.items() if current.get(tag, "") != value
    }
    if not changes:
        return TrackOutcome(path=track, status="already_correct")

    return TrackOutcome(path=track, status="updated", values=changes)
