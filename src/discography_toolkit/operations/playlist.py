# src/discography_toolkit/operations/playlist.py
"""Folding converted albums into a playlist that mirrors the discography.

An album leaves the discography as FLAC and comes back from a converter
as Opus, under a folder the converter named from the tags it found:
"(Tuva) - Huun Huur Tu - 60 Horses in My Herd". The Album tag is the
bare title, deliberately, because a discography is shared and what
leaves it should say what a record is and nothing about the shelf it sat
on. That leaves the conversion with no index, no year, no pin and no
quality word -- everything a playlist wants in order to read the way the
discography does.

This puts them back, from the discography rather than from a name. Each
converted folder is matched to the album it came from by its Album tag,
takes that album's name with the quality word decided from its own
files, and moves under one folder named for the artist.

The discography is read and never written. Everything here happens on
the converted side, so a wrong match cannot damage the source.

Matching reads the tag rather than the folder, which is what makes a
second run a sync rather than a refusal. A folder this pass already
settled carries "©(1993) - 60 Horses in My Herd [OPUS]", a fresh
conversion carries the bare title, and an album copied straight out of
the discography carries the bare title too -- all three peel to the same
identity. Rename an album in the discography, run again, and the
playlist follows. Unpin one and the pin comes off, since the
discography is the source and a sync removes as readily as it adds.

Planning never writes. Applying creates the artist folder and moves what
matched, leaving anything it could not settle exactly where it is.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from discography_toolkit.core import metadata, names
from discography_toolkit.core.layout import (
    QUALITY_TAG,
    detect_tier,
    find_audio_files,
    rename,
)
from discography_toolkit.core.metadata import Tag

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# The name a folder wears mid-move, for a change that only alters case.
# Hidden, and distinct enough that no album is it.
_STAGING_PREFIX: str = ".__playlist__"


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Match:
    """One converted album and the discography album it came from.

    Attributes:
        album: The converted folder as it stands.
        target: Where it belongs -- inside the artist folder, under the
            discography's name for it with its own quality word.
    """

    album: Path
    target: Path

    @property
    def needs_move(self) -> bool:
        """Whether applying this would change the folder on disk."""
        return self.album != self.target


@dataclass(frozen=True, slots=True)
class PlaylistPlan:
    """What a run would fold, before anything is moved.

    The four refusals are kept apart because they are different
    problems. A folder matching nothing may be another artist's, or an
    album since removed from the discography. One with no readable tag
    cannot be matched at all. The two contests are the discography and
    the playlist disagreeing about how many albums there are, in one
    direction or the other, and neither is the tool's to settle.

    Attributes:
        artist: The folder everything matched moves into.
        matches: One entry per converted album that was placed.
        unmatched: Folders whose album is in no discography album.
        untagged: Folders carrying no readable Album tag to match on.
        contested: Groups of folders all claiming one discography album.
        ambiguous: Folders whose album names several discography albums.
    """

    artist: Path
    matches: tuple[Match, ...] = ()
    unmatched: tuple[Path, ...] = ()
    untagged: tuple[Path, ...] = ()
    contested: tuple[tuple[Path, ...], ...] = ()
    ambiguous: tuple[Path, ...] = ()

    @property
    def pending(self) -> tuple[Match, ...]:
        """Albums whose folder would move or be renamed."""
        return tuple(match for match in self.matches if match.needs_move)

    @property
    def settled(self) -> int:
        """Albums already in place under the right name."""
        return sum(1 for match in self.matches if not match.needs_move)


@dataclass(frozen=True, slots=True)
class PlaylistReport:
    """What happened when a plan was applied.

    Attributes:
        moved: How many folders were successfully placed.
        failures: `(album, reason)` for each move that failed.
    """

    moved: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def candidates(converted: Path, artist: Path) -> list[Path]:
    """Find the folders a run should try to match.

    Both levels: what the converter dropped loose, and what a previous
    run already filed. The artist folder itself is not a candidate --
    otherwise a second run would see nothing it had settled and report
    the whole playlist as missing.

    Args:
        converted: The folder the converter wrote into.
        artist: The playlist's artist folder, which may not exist yet.

    Returns:
        Candidate folders, sorted by path.
    """
    found: list[Path] = [
        entry
        for entry in converted.iterdir()
        if entry.is_dir() and not entry.name.startswith(".") and entry != artist
    ]
    if artist.is_dir():
        found.extend(
            entry for entry in artist.iterdir() if entry.is_dir() and not entry.name.startswith(".")
        )
    return sorted(found)


def plan(
    folders: Sequence[Path],
    artist: Path,
    disco_albums: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> PlaylistPlan:
    """Match every converted album to its discography album, without moving.

    Args:
        folders: The converted folders to examine. Discovery belongs to
            the caller, which `candidates` does.
        artist: The playlist's artist folder, where matches belong.
        disco_albums: The discography's album folders, read-only.
        on_progress: Called with each folder as it is examined.

    Returns:
        What would move, and everything that could not be settled.
    """
    disco: dict[str, list[Path]] = defaultdict(list)
    for album in disco_albums:
        disco[names.album_title(album.name).casefold()].append(album)

    claims: defaultdict[str, list[Path]] = defaultdict(list)
    untagged: list[Path] = []
    unmatched: list[Path] = []
    ambiguous: list[Path] = []

    for candidate in folders:
        title: str | None = identity(candidate)
        if title is None:
            untagged.append(candidate)
        elif title not in disco:
            unmatched.append(candidate)
        elif len(disco[title]) > 1:
            ambiguous.append(candidate)
        else:
            claims[title].append(candidate)
        if on_progress is not None:
            on_progress(candidate)

    matches: list[Match] = []
    contested: list[tuple[Path, ...]] = []
    for title, claimants in claims.items():
        if len(claimants) > 1:
            contested.append(tuple(claimants))
            continue
        claimant: Path = claimants[0]
        matches.append(
            Match(album=claimant, target=artist / settled_name(disco[title][0], claimant))
        )

    return PlaylistPlan(
        artist=artist,
        matches=tuple(matches),
        unmatched=tuple(unmatched),
        untagged=tuple(untagged),
        contested=tuple(contested),
        ambiguous=tuple(ambiguous),
    )


def apply(
    playlist_plan: PlaylistPlan,
    on_progress: Callable[[Path], None] | None = None,
) -> PlaylistReport:
    """Create the artist folder and move everything the plan matched.

    A move that fails is recorded and the run continues: one folder
    blocked by a name collision should not abandon the others. A target
    already in place and not the folder's own is refused rather than
    written over.

    Args:
        playlist_plan: A plan produced by `plan`.
        on_progress: Called with each album as it is dealt with.

    Returns:
        A count of moves and the failures collected along the way.
    """
    if playlist_plan.pending:
        playlist_plan.artist.mkdir(parents=True, exist_ok=True)

    moved: int = 0
    failures: list[tuple[Path, str]] = []

    for match in playlist_plan.pending:
        detail: str | None = _place(match)
        if detail is None:
            moved += 1
        else:
            failures.append((match.album, detail))
        if on_progress is not None:
            on_progress(match.album)

    return PlaylistReport(moved=moved, failures=tuple(failures))


def identity(album: Path) -> str | None:
    """Read which album a folder holds, from the tags rather than the name.

    The first readable Album tag settles it -- the tracks of one folder
    are one album, so a second opinion would only cost a file open.

    Args:
        album: The converted folder to read.

    Returns:
        The album's title, casefolded for matching, or `None` when no
        track carries a readable Album tag.
    """
    for track in find_audio_files(album):
        value: str = _album_of(track)
        if value:
            return _bare_title(value).casefold()
    return None


def settled_name(disco_album: Path, converted: Path) -> str:
    """Build the name a converted album should carry in the playlist.

    The discography's name, with its quality word replaced by one
    decided from the converted files. Everything else it carries -- the
    pin, the index, the year, the availability marker, an "(EP)" -- is
    the discography's to say and comes across as written, which is what
    makes a rename there reach the playlist on the next run.

    The quality is never copied. A FLAC album arrives as Opus and reads
    "[OPUS]"; one copied across as MP3 earns no word at all, exactly as
    a lossy album does in the discography.

    Args:
        disco_album: The album folder in the discography.
        converted: The converted folder, read for its tier.

    Returns:
        The folder name the converted album should take.
    """
    bare: str = names.strip_quality_tag(disco_album.name)
    return f"{bare}{QUALITY_TAG.get(detect_tier(converted), '')}"


def album_tag(name: str) -> str:
    """Build the Album tag a settled playlist folder should carry.

    The folder's own name, less the numbering index. The index is what a
    file browser sorts on and it belongs in the name; a player sorts on
    the tag instead, where a pin and then a year give favourites first
    and chronological after -- without an index that goes stale the
    moment the discography renumbers.

    Args:
        name: A settled playlist folder's name.

    Returns:
        The Album tag, e.g. "©(1993) - 60 Horses in My Herd [OPUS]".
    """
    pin, rest = names.split_pin_mark(name)
    _, rest = names.split_index(rest)
    return f"{pin}{rest}"


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _album_of(track: Path) -> str:
    """Read one track's Album tag, or nothing when it cannot be read.

    An unreadable track is not the album's answer -- the next one may
    well be -- so it is passed over rather than raised on, the way an
    unreadable track casts no vote when a cover is chosen.

    Args:
        track: The audio file to read.

    Returns:
        The Album tag, empty when absent or when the file will not read.
    """
    try:
        return metadata.read(track, [Tag.ALBUM])[Tag.ALBUM]
    except Exception:  # noqa: BLE001 - any file can fail in ways not worth enumerating
        return ""


def _bare_title(tag: str) -> str:
    """Read an album's title out of whatever an Album tag happens to hold.

    Two shapes arrive here and only one wants peeling. A fresh conversion
    and a copy taken straight out of the discography both carry the bare
    title already, since that is all the discography writes. A folder
    this pass settled on an earlier run carries the playlist's own form,
    "©(1993) - 60 Horses in My Herd [OPUS]", which has to come apart.

    They are told apart by shape rather than peeled alike, because
    peeling a bare title damages it. `names.album_title` reads a folder
    name, so its first act is to take a leading number for a numbering
    index -- and "60 Horses in My Herd" loses its first word. Stripping
    a year unconditionally is no safer: an album titled "1999" would
    lose everything.

    The index is not peeled even from the playlist form, since a tag
    never carries one -- `album_tag` drops it deliberately.

    Args:
        tag: An Album tag as found on a track.

    Returns:
        The album's title.
    """
    _, rest = names.split_pin_mark(tag)
    if not names.conforms_body(rest):
        return tag

    _, rest = names.split_year(rest)
    _, rest = names.split_missing_marker(rest)
    _, rest = names.split_ep_marker(rest)
    return names.drop_unpaired_wrappers(names.strip_quality_tag(rest))


def _place(match: Match) -> str | None:
    """Move one album to its place, refusing to overwrite.

    A target already in place holds an album of its own, so the move is
    refused rather than destroying it. "In place" is judged by file
    identity, not path text, so a change that only alters case -- which
    reads as "already there" on a case-insensitive filesystem -- is
    recognised as the move it is.

    Args:
        match: The album and where it belongs.

    Returns:
        The failure's detail, or `None` on success.
    """
    if match.target.exists() and not match.target.samefile(match.album):
        return f"a folder named {match.target.name!r} is already there"
    return rename(match.album, match.target, staging_prefix=_STAGING_PREFIX)
