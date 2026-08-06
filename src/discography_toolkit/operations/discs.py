# src/discography_toolkit/operations/discs.py
"""Reading which disc each track belongs to, and what to do about it.

The one tag a track cannot answer for alone. Whether its "1" means
anything depends on whether a "2" exists elsewhere in the same album, so
an album is read whole before a single value is settled.

Two answers come out of that, and both are readings of the same fact.
An album carrying at most one distinct number has it cleared: one disc
is the ordinary case, and saying so in a tag tells nobody anything.
"1 everywhere" and "1 on some, blank on the rest" are the same album and
are treated alike, which is what keeps the second from being left
half-tagged forever.

An album carrying two or more keeps its numbers, settled to their bare
form -- no padding, no "of total" -- and wears them at the front of each
filename, since a flat folder sorted by name is the only view that has
nothing else to go on. The numbers themselves are never decided here:
which disc a track belongs to is the ripper's answer and, once the
folders that used to say so are gone, the only record of it.

Planning reads and decides. Applying only renames; the tag writing is
the caller's, folded into the pass that writes the others.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from discography_toolkit.core import metadata, names
from discography_toolkit.core.layout import holding_album, rename
from discography_toolkit.core.metadata import Tag

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# The name a file wears mid-rename, for a change that only alters case.
# Never reached in practice -- a disc prefix is never a case-only change
# -- but the shared rename takes one and a distinct value says which step
# left a file behind if a run is interrupted.
_STAGING_PREFIX: str = ".__discs__"

# What separates a disc number from the filename it fronts. A full stop
# rather than a space or a dash: those already appear in the names this
# sits in front of, and the prefix has to be told from them to be found
# again on the next run.
_SEPARATOR: str = "."


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Prefix:
    """One track, and the disc number its filename should carry.

    Attributes:
        track: The file as it stands.
        disc: Its disc number, settled to the bare form.
    """

    track: Path
    disc: str

    @property
    def new_name(self) -> str:
        """The filename it should have, prefixed with its disc."""
        return f"{self.disc}{_SEPARATOR}{self.track.name}"

    @property
    def needs_rename(self) -> bool:
        """Whether the file is not already wearing this prefix.

        Asked of this track's own number rather than of any digit and a
        dot, since plenty of these filenames start with a track number
        already. Only an exact match counts, so a run over a settled
        album renames nothing and a second run cannot stack "1.1.".
        """
        return not self.track.name.startswith(f"{self.disc}{_SEPARATOR}")

    @property
    def target(self) -> Path:
        """Where it would end up."""
        return self.track.with_name(self.new_name)


@dataclass(frozen=True, slots=True)
class DiscPlan:
    """What an album's disc numbers say, and what follows from them.

    Attributes:
        clear: Tracks whose disc number should be emptied, their albums
            holding at most one.
        settle: Each track of a multi-disc album mapped to its number in
            settled form, for the caller to write where it differs.
        prefixes: The filename each multi-disc track should carry.
        split: Each album left carrying its numbers, with the numbers it
            holds. Returned as facts rather than a phrase: naming which
            artist an album belongs to is the caller's, being the only
            one that knows them.
        unreadable: Tracks in a multi-disc album whose own number will
            not settle -- neither written nor renamed, since guessing
            which disc they belong to would scatter the album.
    """

    clear: frozenset[Path] = frozenset()
    settle: dict[Path, str] = field(default_factory=dict)
    prefixes: tuple[Prefix, ...] = ()
    split: tuple[tuple[Path, tuple[str, ...]], ...] = ()
    unreadable: tuple[Path, ...] = ()

    @property
    def pending(self) -> tuple[Prefix, ...]:
        """Tracks whose filename would change."""
        return tuple(prefix for prefix in self.prefixes if prefix.needs_rename)


@dataclass(frozen=True, slots=True)
class DiscReport:
    """What happened when the filenames were prefixed.

    Attributes:
        renamed: How many files took their disc prefix.
        failures: `(track, reason)` for each rename that failed.
    """

    renamed: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    albums: Sequence[Path],
    tracks: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> DiscPlan:
    """Read every album's disc numbers and decide what each track wants.

    Args:
        albums: The album folders in scope.
        tracks: Every audio file in scope.
        on_progress: Called with each track as it is read.

    Returns:
        What to clear, what to settle, what to rename, and what could not
        be read.
    """
    held: set[Path] = set(albums)
    by_album: defaultdict[Path, list[tuple[Path, str]]] = defaultdict(list)

    for track in tracks:
        album: Path | None = holding_album(track, held)
        if album is not None:
            by_album[album].append((track, _disc_of(track)))
        if on_progress is not None:
            on_progress(track)

    clear: set[Path] = set()
    settle: dict[Path, str] = {}
    prefixes: list[Prefix] = []
    split: list[tuple[Path, tuple[str, ...]]] = []
    unreadable: list[Path] = []

    for album, entries in sorted(by_album.items()):
        found: set[str] = {value for _, value in entries if value}
        if len(found) < 2:
            clear.update(track for track, value in entries if value)
            continue

        numbers: list[str] = []
        for track, value in entries:
            number: str | None = names.disc_number(value)
            if number is None:
                unreadable.append(track)
                continue
            settle[track] = number
            prefixes.append(Prefix(track=track, disc=number))
            numbers.append(number)

        split.append((album, tuple(sorted(set(numbers)))))

    return DiscPlan(
        clear=frozenset(clear),
        settle=settle,
        prefixes=tuple(prefixes),
        split=tuple(split),
        unreadable=tuple(unreadable),
    )


def apply(
    disc_plan: DiscPlan,
    on_progress: Callable[[Path], None] | None = None,
) -> DiscReport:
    """Prefix each multi-disc track's filename with its disc number.

    Args:
        disc_plan: A plan produced by `plan`.
        on_progress: Called with each track as it is dealt with.

    Returns:
        A count of renames and the failures collected along the way.
    """
    renamed: int = 0
    failures: list[tuple[Path, str]] = []

    for prefix in disc_plan.pending:
        detail: str | None = _rename(prefix)
        if detail is None:
            renamed += 1
        else:
            failures.append((prefix.track, detail))
        if on_progress is not None:
            on_progress(prefix.track)

    return DiscReport(renamed=renamed, failures=tuple(failures))


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _disc_of(track: Path) -> str:
    """Read one track's disc number, or nothing when it cannot be read.

    Args:
        track: The audio file to read.

    Returns:
        Its disc number, empty when absent or when the file will not
        read -- an unreadable track cannot argue that its album is split.
    """
    try:
        return metadata.read(track, [Tag.DISC])[Tag.DISC]
    except Exception:  # noqa: BLE001 - any file can fail in ways not worth enumerating
        return ""


def _rename(prefix: Prefix) -> str | None:
    """Move one track to its prefixed name, refusing to overwrite.

    Two discs of one album commonly hold a track of the same name, which
    is the whole reason for the prefix -- so a target already in place is
    exactly the collision this exists to prevent, and taking it would
    destroy the file that got there first.

    Args:
        prefix: The track and the number it should wear.

    Returns:
        The failure's detail, or `None` on success.
    """
    target: Path = prefix.target
    if target.exists() and not target.samefile(prefix.track):
        return f"a file named {target.name!r} is already there"
    return rename(prefix.track, target, staging_prefix=_STAGING_PREFIX)
