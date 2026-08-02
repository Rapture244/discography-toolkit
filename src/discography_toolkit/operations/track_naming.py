# src/discography_toolkit/operations/track_naming.py
"""Title-casing the filename of every audio track.

A track's name is cased by the same rule as the album folder holding it,
so "kind of blue.flac" becomes "Kind of Blue.flac". Only the stem is
touched; the extension is left exactly as found, since every later step
matches on it and a file that stops matching stops being tagged.

Two tracks in one folder can only clash when their names differ by
nothing but case or spacing -- which a case-insensitive filesystem would
never have allowed, but a discography copied off a Linux server can carry
all the same. Renaming into a clash would have one file quietly overwrite
the other, so a clash is reported and refused rather than applied.

Planning never writes. Applying renames each track, routing a change
that only alters case through a staging name -- on a case-insensitive
filesystem the source and target are one file, and a direct rename is
rejected or silently does nothing depending on the platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from discography_toolkit.core import files, names

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# The prefix a file wears mid-rename, for the case-only two-step. Hidden,
# and distinct enough that no track is named it, so an interrupted run
# leaves something obvious behind rather than a plausible filename.
_STAGING_PREFIX: str = ".__casing__"


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class TrackName:
    """What one track's filename should become.

    Attributes:
        track: The file as it stands.
        new_name: The cased filename, extension included.
        collision: Whether the new name is already taken in the folder,
            by a file not itself moving out of the way -- in which case
            it cannot be renamed without loss.
    """

    track: Path
    new_name: str
    collision: bool = False

    @property
    def needs_rename(self) -> bool:
        """Whether the cased name differs from the one on disk."""
        return self.new_name != self.track.name

    @property
    def target(self) -> Path:
        """Where the file would be renamed to."""
        return self.track.with_name(self.new_name)


@dataclass(frozen=True, slots=True)
class CasePlan:
    """What a run would rename, before anything is written.

    Attributes:
        outcomes: One entry per track examined, in the order given.
    """

    outcomes: tuple[TrackName, ...]

    @property
    def pending(self) -> tuple[TrackName, ...]:
        """Tracks that can be safely renamed -- changed, and not clashing."""
        return tuple(o for o in self.outcomes if o.needs_rename and not o.collision)

    @property
    def collisions(self) -> tuple[TrackName, ...]:
        """Tracks whose cased name is taken, which cannot be renamed."""
        return tuple(o for o in self.outcomes if o.collision)


@dataclass(frozen=True, slots=True)
class CaseReport:
    """What happened when a plan was applied.

    Attributes:
        renamed: How many files were successfully renamed.
        failures: `(track, reason)` for each rename that failed.
    """

    renamed: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    tracks: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> CasePlan:
    """Work out each track's cased name, and which names clash.

    Args:
        tracks: The audio files to examine. Discovery belongs to the
            caller.
        on_progress: Called with each track as it is examined, so a
            caller can drive a display without this module knowing one
            exists.

    Returns:
        One outcome per track, in the order given.
    """
    drafts: list[tuple[Path, str]] = []
    for track in tracks:
        drafts.append((track, names.title_case_filename(track.name)))
        if on_progress is not None:
            on_progress(track)

    clashing: set[Path] = _clashing(
        [(track, new_name) for track, new_name in drafts if track.name != new_name]
    )
    outcomes: tuple[TrackName, ...] = tuple(
        TrackName(track=track, new_name=new_name, collision=track in clashing)
        for track, new_name in drafts
    )
    return CasePlan(outcomes=outcomes)


def apply(
    case_plan: CasePlan,
    on_progress: Callable[[Path], None] | None = None,
) -> CaseReport:
    """Rename every track the plan cleared, leaving clashes untouched.

    Only `pending` is renamed -- the tracks that changed and did not
    clash -- so a clash is never applied. A rename that fails is recorded
    and the run continues.

    Args:
        case_plan: A plan produced by `plan`.
        on_progress: Called with each track as it is dealt with.

    Returns:
        A count of successes and the failures collected along the way.
    """
    renamed: int = 0
    failures: list[tuple[Path, str]] = []

    for outcome in case_plan.pending:
        detail: str | None = files.rename(
            outcome.track, outcome.target, staging_prefix=_STAGING_PREFIX
        )
        if detail is None:
            renamed += 1
        else:
            failures.append((outcome.track, detail))
        if on_progress is not None:
            on_progress(outcome.track)

    return CaseReport(renamed=renamed, failures=tuple(failures))


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _clashing(changing: Sequence[tuple[Path, str]]) -> set[Path]:
    """Find the tracks whose target name is already taken in their folder.

    A target is taken when a file of that name exists that is not the
    track itself. "Itself" is judged by file identity, not by path text,
    so a change that only alters case -- where the file's own name reads
    as the target on a case-insensitive filesystem -- is a rename, not a
    clash, while a differently-cased *other* file still collides.

    Checked per folder, since `with_name` keeps the parent: two albums
    may each hold a track of the same name without clashing.

    Unlike a general rename, casing needs no "moving out of the way"
    escape: a file that is changing never currently holds a cased name --
    casing is idempotent, so if it already had one it would not be
    changing -- and so can never be the name another is renaming onto.

    Args:
        changing: `(track, new_name)` for every track whose name changes.

    Returns:
        The tracks that cannot be renamed safely.
    """
    claimed: set[Path] = set()
    clashing: set[Path] = set()

    for track, new_name in changing:
        target: Path = track.with_name(new_name)
        taken_on_disk: bool = target.exists() and not target.samefile(track)
        if taken_on_disk or target in claimed:
            clashing.add(track)
        claimed.add(target)

    return clashing
