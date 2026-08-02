# src/discography_toolkit/operations/pruning.py
"""Pruning an Opus album that duplicates a lossless one.

An album kept in both FLAC and Opus is the same record twice. The Opus
is a conversion of the FLAC -- made with a tool like fre:ac and remade
from it at any time -- so holding both is a mistake, the copy taking a
place in the discography that is not its own. This finds each Opus album
that shares a lossless album's identity, its year and title whatever
their index, container or quality tag, and deletes the Opus.

The deletion is permanent, and it is meant to be: the lossless twin it
copies stays, so nothing is lost that cannot be regenerated. Only an
Opus album with such a twin is ever touched -- an Opus-only album, with
nothing to remake it from, is left exactly where it is.

Planning never deletes. Applying removes the folders the plan named, one
failure recorded and the run continued rather than the rest abandoned.

That one duplicate is the only one with an answer. Two albums of the
same tier are a different matter -- neither is derivable from the other,
and which to keep is a judgement about tags, artwork or a mis-ripped
track that no rule reaches. `duplicates` finds those and says so; what
happens next belongs to whoever is looking.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import shutil
from typing import TYPE_CHECKING

from discography_toolkit.core import names
from discography_toolkit.core.layout import AudioTier, detect_tier

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# What makes one album distinct from another: the year it came out, its
# title casefolded, and whether it is an EP. Named rather than written
# out at each use, because both the pruning dictionary and the duplicate
# grouping key on it, and a component added to `_identity` should not
# have to be remembered in their annotations too.
type _Identity = tuple[str | None, str, bool]


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Prune:
    """One Opus album marked for deletion, and the twin that condemns it.

    Attributes:
        album: The Opus album to delete.
        twin: The lossless album it duplicates, kept for the report.
    """

    album: Path
    twin: Path


@dataclass(frozen=True, slots=True)
class PrunePlan:
    """What a run would delete, before anything is removed.

    Attributes:
        prunes: One entry per Opus album that duplicates a lossless one.
    """

    prunes: tuple[Prune, ...]


@dataclass(frozen=True, slots=True)
class PruneReport:
    """What happened when a plan was applied.

    Attributes:
        deleted: How many Opus albums were removed.
        failures: `(album, reason)` for each deletion that failed.
    """

    deleted: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    albums: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> PrunePlan:
    """Find each Opus album that duplicates a lossless one, without deleting.

    An album's identity is its year, title and whether it is an EP, read
    past the index, pin, marker and quality tag, so an Opus album and its
    FLAC twin match however differently they are named or wherever they
    are stored. The tier is read from the files, not the name, so a
    mislabelled folder is judged by what it actually holds.

    Args:
        albums: One artist's album folders, the container already
            unwrapped. Discovery belongs to the caller.
        on_progress: Called with each album as it is examined.

    Returns:
        One entry per Opus album with a lossless twin, in the order given.
    """
    tiers: dict[Path, AudioTier] = {album: detect_tier(album) for album in albums}

    # A lossless album is looked up by its identity, so an Opus album can
    # ask whether its own is already held in lossless form. A later
    # lossless album with a shared identity simply wins the slot; that an
    # Opus copy is redundant does not depend on which twin answers.
    lossless: dict[_Identity, Path] = {
        _identity(album.name): album for album in albums if tiers[album] is AudioTier.LOSSLESS
    }

    prunes: list[Prune] = []
    for album in albums:
        if tiers[album] is AudioTier.OPUS:
            twin: Path | None = lossless.get(_identity(album.name))
            if twin is not None:
                prunes.append(Prune(album=album, twin=twin))
        if on_progress is not None:
            on_progress(album)

    return PrunePlan(prunes=tuple(prunes))


def apply(
    prune_plan: PrunePlan,
    on_progress: Callable[[Path], None] | None = None,
) -> PruneReport:
    """Delete every Opus duplicate the plan found.

    A folder that will not delete is recorded and the run continues; one
    locked file should not spare the other duplicates.

    Args:
        prune_plan: A plan produced by `plan`.
        on_progress: Called with each album as it is dealt with.

    Returns:
        A count of deletions and the failures collected along the way.
    """
    deleted: int = 0
    failures: list[tuple[Path, str]] = []

    for prune in prune_plan.prunes:
        detail: str | None = _delete(prune.album)
        if detail is None:
            deleted += 1
        else:
            failures.append((prune.album, detail))
        if on_progress is not None:
            on_progress(prune.album)

    return PruneReport(deleted=deleted, failures=tuple(failures))


def duplicates(albums: Sequence[Path]) -> tuple[tuple[Path, ...], ...]:
    """Group albums that are the same record held more than once.

    The same identity `plan` prunes on, asked of every album rather than
    only the Opus ones: two folders sharing a year, a title and an EP
    marker are one album twice however they are indexed, contained or
    tagged.

    Nothing is proposed. An Opus copy of a lossless album has an obvious
    resolution and `plan` takes it; anything left over does not, so this
    only reports. Call it on what pruning would leave behind, or a pair
    it is about to settle reads as a problem it has already solved.

    Args:
        albums: One artist's album folders, the container already
            unwrapped.

    Returns:
        One tuple per repeated identity, each holding the folders that
        share it, in the order given. Empty when every album is its own.
    """
    grouped: defaultdict[_Identity, list[Path]] = defaultdict(list)
    for album in albums:
        grouped[_identity(album.name)].append(album)

    return tuple(tuple(group) for group in grouped.values() if len(group) > 1)


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _identity(name: str) -> _Identity:
    """Read an album's identity: its year, title, and whether it is an EP.

    Everything that a copy might differ by -- the pin, the index, the
    availability marker, the quality tag -- is what `album_title` peels
    off, so a FLAC album and its Opus conversion resolve to the same
    triple however they are dressed. The title is casefolded so case
    alone never tells them apart.

    The EP marker is peeled with the rest but put back here, because it
    is a fact about the release rather than a dressing on the folder: an
    artist who puts out an EP and then an album of the same name in the
    same year has made two records, and they are not copies of each
    other.

    Args:
        name: The album folder's name.

    Returns:
        A `(year, title, is_ep)` triple, the year `None` when the name
        carries none.
    """
    is_ep, _ = names.split_ep_marker(name)
    return names.extract_year(name), names.album_title(name).casefold(), is_ep


def _delete(album: Path) -> str | None:
    """Remove one folder and everything in it, reporting rather than raising.

    Args:
        album: The album folder to delete.

    Returns:
        The failure's detail, or `None` on success.
    """
    try:
        shutil.rmtree(album)
    except OSError as exc:
        return str(exc)
    return None
