# src/discography_toolkit/operations/naming.py
"""Rebuilding an album folder's name from its parts.

A folder name carries an order that the eye reads and a player never
sees: a "©" pin, a numbering index, the year, a title, an "(EP)" marker
where the release is one, and -- for the albums held losslessly -- a
quality tag. Names drift, get typed in different shapes, keep a stale tag
after a transcode. This reads a name back into its parts and writes them
out in one settled form.

The quality tag is never trusted from the name; it is decided from the
files, which is where the "M"/"⚠" markers come in. "M" is a claim that
an album is not held, so the files judge it: an empty folder is missing
whatever the name says, a folder with audio is not. The one case the
tool will not settle is a name that claims missing over a folder that
holds audio -- that could be an album finally acquired, or files that
strayed in, and those resolve in opposite directions. It gets the "⚠"
marker and a person makes the call.

The EP marker is trusted from the name, since nothing on disk says
whether a release is one. It is found wherever it was typed -- a bare
"EP" at the front, "(EP)" or "[EP]" anywhere -- and re-emitted in one
place: after the title, ahead of the quality tag, so the eye finds it
where it expects to. An "ep" in the wrong case is not taken as a marker
but is reported, since only a person can say whether it was one.

A singles collection is the one album named without a year, because it
has none: it gathers loose tracks belonging to no release. It comes out
as the bare word, carrying no quality tag either -- one artist keeps one
such pile, whatever mix of formats it is made of, so there is nothing
for a tag to tell apart and a stale one is simply dropped.

What the peel leaves behind is repaired rather than raised over. A
parenthesis or bracket whose partner went with the token that wrapped it
says nothing on its own, so there is nothing for a person to decide and
it is dropped; one that pairs is part of the title and stays. The point
of settling every album into one shape is to correct that drift, not to
collect it.

Planning never writes. It reads each name, works out what it should be,
and records the difference, so the whole rename can be shown before any
of it happens and a folder already right is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from discography_toolkit.core import names
from discography_toolkit.core.layout import AudioTier, detect_tier

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# The tag each held tier earns in the name. Lossy earns none -- it is
# held, but there is nothing about the format worth announcing -- and
# NONE never reaches here, since a folder with no audio is marked
# missing instead.
_QUALITY_TAG: Final[Mapping[AudioTier, str]] = {
    AudioTier.LOSSLESS: " [FLAC]",
    AudioTier.OPUS: " [OPUS]",
}

# The one shape an extended-play marker is written in, wherever it was
# found. Parenthesised rather than bracketed, so it reads as part of what
# the release is while the square brackets stay the format's.
_EP_TAG: Final[str] = f" ({names.EP_MARKER})"

# The name a folder wears mid-rename, for a change that only alters case.
# On a case-insensitive filesystem the source and target are one folder
# and a direct rename is ambiguous, so the change goes via a distinct
# staging name first. Hidden, and distinct enough that no album is it.
_STAGING_PREFIX: str = ".__naming__"


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class AlbumName:
    """What one album's name should become, and what that says about it.

    The verdict is carried as facts rather than a single status because
    they are orthogonal: an album can be renamed and missing at once, or
    already correct and missing. A caller reads whichever it is
    reporting.

    Attributes:
        album: The album folder as it stands.
        new_name: The name it should carry, empty when there is nothing
            to build one around.
        year: The year token, or `None` when the name carries none.
        tier: The quality decided from the files.
        singles: The album is the artist's singles collection -- named
            without a year, since it has none to carry.
        missing: Marked "M": the folder holds no audio.
        conflict: Marked "⚠": the name claimed missing over a folder that
            holds audio.
        newly_missing: An "M" this run added, where the name had none --
            worth surfacing, since it is the tool declaring an album lost.
        is_ep: The name carried an extended-play marker, in some shape.
        lowercase_ep: The name carried an "ep" in the wrong case, which
            was left alone -- worth surfacing, since it is as likely a
            miscased marker as an ordinary word and only a person can
            say which.
    """

    album: Path
    new_name: str
    year: str | None
    tier: AudioTier
    singles: bool = False
    missing: bool = False
    conflict: bool = False
    newly_missing: bool = False
    is_ep: bool = False
    lowercase_ep: bool = False

    @property
    def skipped(self) -> bool:
        """Whether the album was passed over for having nothing to name it by."""
        return self.year is None and not self.singles

    @property
    def needs_rename(self) -> bool:
        """Whether applying this would change the folder on disk."""
        return not self.skipped and self.new_name != self.album.name

    @property
    def held(self) -> bool:
        """Whether the album is held in some real format, unconflicted."""
        return not self.skipped and not self.missing and not self.conflict

    @property
    def target(self) -> Path:
        """Where the folder would be renamed to."""
        return self.album.parent / self.new_name


@dataclass(frozen=True, slots=True)
class NamePlan:
    """What a run would rename, before anything is written.

    Attributes:
        outcomes: One entry per album examined, in the order given.
    """

    outcomes: tuple[AlbumName, ...]

    @property
    def total(self) -> int:
        """How many albums could be named at all."""
        return sum(1 for outcome in self.outcomes if not outcome.skipped)

    @property
    def pending(self) -> tuple[AlbumName, ...]:
        """Albums whose folder name would change."""
        return tuple(outcome for outcome in self.outcomes if outcome.needs_rename)

    @property
    def clean(self) -> int:
        """Albums that can be named and whose name is already right."""
        return sum(
            1 for outcome in self.outcomes if not outcome.skipped and not outcome.needs_rename
        )

    @property
    def skipped(self) -> tuple[AlbumName, ...]:
        """Albums passed over for having nothing to build a name around."""
        return tuple(outcome for outcome in self.outcomes if outcome.skipped)

    @property
    def held(self) -> int:
        """Albums held in a real format, unconflicted."""
        return sum(1 for outcome in self.outcomes if outcome.held)

    @property
    def missing(self) -> tuple[AlbumName, ...]:
        """Albums marked "M" -- their folders hold no audio."""
        return tuple(outcome for outcome in self.outcomes if outcome.missing)

    @property
    def conflicts(self) -> tuple[AlbumName, ...]:
        """Albums marked "⚠" -- a missing claim over a folder with audio."""
        return tuple(outcome for outcome in self.outcomes if outcome.conflict)

    @property
    def newly_missing(self) -> tuple[AlbumName, ...]:
        """Albums this run newly declared missing, worth a person's eye."""
        return tuple(outcome for outcome in self.outcomes if outcome.newly_missing)

    @property
    def lowercase_eps(self) -> tuple[AlbumName, ...]:
        """Albums carrying a miscased "ep", left alone and worth an eye."""
        return tuple(outcome for outcome in self.outcomes if outcome.lowercase_ep)


@dataclass(frozen=True, slots=True)
class NameReport:
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
) -> NamePlan:
    """Work out each album's settled name, without writing.

    Args:
        albums: The album folders to examine. Discovery belongs to the
            caller.
        on_progress: Called with each album as it is examined, so a
            caller can drive a display without this module knowing one
            exists.

    Returns:
        One outcome per album, in the order given.
    """
    outcomes: list[AlbumName] = []

    for album in albums:
        outcomes.append(_examine(album))
        if on_progress is not None:
            on_progress(album)

    return NamePlan(outcomes=tuple(outcomes))


def apply(
    name_plan: NamePlan,
    on_progress: Callable[[Path], None] | None = None,
) -> NameReport:
    """Rename every folder the plan found, leaving the rest alone.

    A rename that fails is recorded and the run continues: one folder
    blocked by a name collision should not abandon the others. A target
    that already exists is refused rather than written over, since the
    folder in the way holds an album of its own.

    Args:
        name_plan: A plan produced by `plan`.
        on_progress: Called with each album as it is dealt with.

    Returns:
        A count of successes and the failures collected along the way.
    """
    renamed: int = 0
    failures: list[tuple[Path, str]] = []

    for outcome in name_plan.pending:
        detail: str | None = _rename(outcome.album, outcome.target)
        if detail is None:
            renamed += 1
        else:
            failures.append((outcome.album, detail))
        if on_progress is not None:
            on_progress(outcome.album)

    return NameReport(renamed=renamed, failures=tuple(failures))


# ==================================================================================== #
#                                       PLANNING                                       #
# ==================================================================================== #
def _examine(album: Path) -> AlbumName:
    """Read one folder's name into parts and rebuild it.

    A singles collection is settled first, since it is recognised by
    having no year and would otherwise fall to the skip below. Past it,
    the year is the pivot: without one there is nothing to anchor a name
    around. With one, the marker and quality tag are decided from the
    files rather than the name, which is what lets a stale tag be dropped
    and a lost album be flagged. The EP marker is the exception, read
    from the name because nothing on disk carries it.

    The title is repaired before it is cased: the quality word is cut
    while its bracket is still whole, then whatever bracket the peel
    orphaned is dropped, and only then is the result cased, on a title
    that has stopped changing shape.

    Args:
        album: The album folder to examine.

    Returns:
        Its outcome -- the name it should carry, and what that says.
    """
    pin, rest = names.split_pin_mark(album.name)
    index, rest = names.split_index(rest)

    if names.is_singles(album.name):
        return AlbumName(
            album=album,
            new_name=f"{pin}{index}{names.SINGLES_TITLE}",
            year=None,
            tier=detect_tier(album),
            singles=True,
        )

    year, rest = names.split_year(rest)
    if year is None:
        return AlbumName(album=album, new_name="", year=None, tier=AudioTier.NONE)

    claims_missing, rest = names.split_missing_marker(rest)
    is_ep, rest = names.split_ep_marker(rest)
    title: str = names.title_case(names.drop_unpaired_wrappers(names.strip_quality_tag(rest)))
    tier: AudioTier = detect_tier(album)

    marker, tag, missing, conflict, newly_missing = _resolve(tier, claimed=claims_missing)
    marker_prefix: str = f"{marker} - " if marker else ""
    ep_tag: str = _EP_TAG if is_ep else ""
    new_name: str = f"{pin}{index}({year}) - {marker_prefix}{title}{ep_tag}{tag}"

    return AlbumName(
        album=album,
        new_name=new_name,
        year=year,
        tier=tier,
        missing=missing,
        conflict=conflict,
        newly_missing=newly_missing,
        is_ep=is_ep,
        lowercase_ep=names.has_lowercase_ep(album.name),
    )


def _resolve(tier: AudioTier, *, claimed: bool) -> tuple[str, str, bool, bool, bool]:
    """Settle the marker and quality tag against the files.

    Three outcomes, in order of precedence: an empty folder is missing
    whatever the name said; a folder with audio the name called missing
    is a conflict; anything else is held and earns its tier's tag.

    Args:
        tier: The quality decided from the files.
        claimed: Whether the name already carried a missing marker.

    Returns:
        A `(marker, tag, missing, conflict, newly_missing)` tuple: the
        glyph to emit, the quality suffix, and the three facts a report
        reads.
    """
    if tier is AudioTier.NONE:
        return names.MISSING_MARKER, "", True, False, not claimed
    if claimed:
        return names.MISSING_CONFLICT_MARKER, "", False, True, False
    return "", _QUALITY_TAG.get(tier, ""), False, False, False


# ==================================================================================== #
#                                      APPLYING                                        #
# ==================================================================================== #
def _rename(album: Path, target: Path) -> str | None:
    """Move a folder to its settled name, refusing to overwrite.

    A target already in place holds an album of its own, so the rename is
    refused rather than destroying it -- the collision is the person's to
    resolve. "In place" is judged by file identity, not path text: a
    change that only alters case reads as "already there" on a
    case-insensitive filesystem though it is the folder's own name, so
    that is recognised as a rename and routed through a staging name,
    which a direct rename cannot do unambiguously there.

    Args:
        album: The folder to rename.
        target: Where it should go.

    Returns:
        The failure's detail, or `None` on success.
    """
    case_only: bool = album.name.casefold() == target.name.casefold()
    if target.exists() and not target.samefile(album):
        return f"a folder named {target.name!r} is already there"
    try:
        if case_only:
            staging: Path = album.with_name(f"{_STAGING_PREFIX}{target.name}")
            _ = album.rename(staging)
            _ = staging.rename(target)
        else:
            _ = album.rename(target)
    except OSError as exc:
        return str(exc)
    return None
