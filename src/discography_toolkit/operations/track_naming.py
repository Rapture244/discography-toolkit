# src/discography_toolkit/operations/track_naming.py
"""Settling the filename of every audio track.

An ordinary track is cased by the same rule as the album folder holding
it, so "kind of blue.flac" becomes "Kind of Blue.flac". Only the stem is
touched; the extension is left exactly as found, since every later step
matches on it and a file that stops matching stops being tagged.

A single is rebuilt rather than cased. It shares one folder with every
other single the artist released, so unlike every other track here it
has no folder of its own saying what it is -- and its name is read out
of its own Date and Title instead, as "(2019-05) - Some Song". That is
the one place in the toolkit where a name comes from the tags rather
than the tags from a name, and it is why this reads them.

A single carrying no year, or no title, is left exactly as it is and
reported. Both are a person's to fill in, and a name invented around a
missing one would be a guess written to disk.

Two tracks in one folder can only clash when their names differ by
nothing but case or spacing -- which a case-insensitive filesystem would
never have allowed, but a discography copied off a Linux server can carry
all the same. Singles clash more easily: two releases of one title in
one month settle on the same name. Renaming into a clash would have one
file quietly overwrite the other, so a clash is reported and refused
rather than applied.

Planning never writes. Applying renames each track, routing a change
that only alters case through a staging name -- on a case-insensitive
filesystem the source and target are one file, and a direct rename is
rejected or silently does nothing depending on the platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from discography_toolkit.core import metadata, names
from discography_toolkit.core.layout import rename
from discography_toolkit.core.metadata import Tag

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
        undated: Singles left alone because they carry no year or no
            title to build a name from.
    """

    outcomes: tuple[TrackName, ...]
    undated: tuple[Path, ...] = ()

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
    """Work out each track's settled name, and which names clash.

    A track in a singles folder is rebuilt from its own tags; every
    other is cased in place. Which it is comes from the folder holding
    it, so a caller hands over a flat list of tracks and needs to know
    nothing about the distinction.

    Args:
        tracks: The audio files to examine. Discovery belongs to the
            caller.
        on_progress: Called with each track as it is examined, so a
            caller can drive a display without this module knowing one
            exists.

    Returns:
        One outcome per track, in the order given, and the singles that
        could not be named.
    """
    drafts: list[tuple[Path, str]] = []
    undated: list[Path] = []

    for track in tracks:
        if names.is_singles(track.parent.name):
            stem: str | None = _single_stem(track)
            if stem is None:
                undated.append(track)
            drafts.append((track, track.name if stem is None else f"{stem}{track.suffix}"))
        else:
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
    return CasePlan(outcomes=outcomes, undated=tuple(undated))


def _single_stem(track: Path) -> str | None:
    """Read one single's name out of its own tags.

    Args:
        track: The single to read.

    Returns:
        Its stem, or `None` when the tags cannot say -- no year, no
        title, or a file that will not open.
    """
    try:
        current: dict[Tag, str] = metadata.read(track, [Tag.DATE, Tag.TITLE])
    except Exception:  # noqa: BLE001 - a corrupt file is left as it is, not raised on
        return None
    return names.single_stem(current.get(Tag.DATE, ""), current.get(Tag.TITLE, ""))


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
        detail: str | None = rename(outcome.track, outcome.target, staging_prefix=_STAGING_PREFIX)
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

    A file whose target is held by another that is itself moving away is
    refused all the same. Casing never produces that case -- it is
    idempotent, so a file that is changing cannot already hold a settled
    name -- but a singles rename can, two tracks swapping names between
    them. Refusing is the conservative answer: the alternative is
    ordering the moves and staging the cycle, for a case that means two
    singles were mislabelled.

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
