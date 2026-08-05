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
from typing import TYPE_CHECKING, Final

from discography_toolkit.core import artwork, metadata, names
from discography_toolkit.core.layout import (
    AUDIO_EXTENSIONS,
    QUALITY_TAG,
    album_tracks,
    detect_tier,
    holds_audio,
    rename,
)
from discography_toolkit.core.metadata import Tag

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence, Set
    from pathlib import Path

    from discography_toolkit.core.artwork import Cover

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# The name a folder wears mid-move, for a change that only alters case.
# Hidden, and distinct enough that no album is it.
_STAGING_PREFIX: str = ".__playlist__"

# The one name a loose cover is written under. A phone that will not read
# the embedded art looks for this beside the tracks, and reads it by
# extension rather than by content -- which is why the bytes are forced
# to JPEG rather than saved as whatever the album happened to carry.
COVER_NAME: Final[str] = "cover.jpg"


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
        matches: One entry per converted album that was placed.
        unmatched: Folders whose album is in no discography album.
        untagged: Folders carrying no readable Album tag to match on.
        contested: Groups of folders all claiming one discography album.
        ambiguous: Folders whose album names several discography albums.
    """

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


@dataclass(frozen=True, slots=True)
class CoverWrite:
    """One album's loose cover, and the bytes it should hold.

    Attributes:
        target: The file to write, always `cover.jpg` beside the tracks.
        data: The image, capped and forced to JPEG.
    """

    target: Path
    data: bytes


@dataclass(frozen=True, slots=True)
class CoverPlan:
    """What a run would write beside the tracks, before anything is written.

    Attributes:
        writes: One entry per album whose loose cover is missing or
            stale. An album already holding the right bytes is absent.
        without_artwork: Albums whose tracks carry no readable cover, so
            there is nothing to write out.
    """

    writes: tuple[CoverWrite, ...] = ()
    without_artwork: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverReport:
    """What happened when a cover plan was applied.

    Attributes:
        written: How many loose covers were written.
        failures: `(path, reason)` for each write that failed.
    """

    written: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def find_homes(root: Path, artist_names: Set[str]) -> tuple[dict[str, list[Path]], list[Path]]:
    """Find where each artist already lives in the playlist, and who else does.

    One artist may have several homes, or none. The playlist is a
    curation rather than a mirror -- an album filed under "Classical"
    because that is what it is to you sits beside the same artist's other
    shelf elsewhere -- so every folder named for them is theirs, and the
    run syncs all of them against the one discography.

    Folders that hold albums but answer to no name in the roster come
    back too. They are nobody's to sync, the discography saying nothing
    about them, but the walk passes them and staying quiet would mean a
    whole artist sitting in the playlist that the run never mentioned.

    One walk for every artist rather than a walk apiece, and it stops
    descending on three conditions: a folder that matches, since an
    artist holds albums and never another artist; a folder holding audio
    of its own, which is an album or a disc; and a folder whose children
    hold audio, which is an artist by shape whatever it is called.

    Names are matched exactly. Nothing types these -- the fold writes
    them from the discography and a converter writes them from the tag --
    so a difference in case is a folder that has drifted, and reporting
    it as missing is more use than quietly syncing it under a name the
    discography does not agree with.

    Args:
        root: The playlist path to search.
        artist_names: The names to look for, from the discography.

    Returns:
        Each name mapped to the folders found for it, in walk order, and
        the artist-shaped folders belonging to no name in the roster.
        A name with no folder is absent.
    """
    found: dict[str, list[Path]] = defaultdict(list)
    strangers: list[Path] = []
    _collect_homes(root, artist_names, found, strangers)

    # The root is not a stranger to itself: pointed at a converter's drop
    # folder it holds albums directly, which is the ordinary case.
    return dict(found), [folder for folder in strangers if folder != root]


def loose_albums(root: Path, homes: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """Sort the playlist path's own children into albums and the unreadable.

    What a converter leaves behind: album folders dropped directly into
    one place, belonging to no artist folder yet. A playlist that has
    been tidied has none of these, and a fresh drop folder is nothing
    but.

    An album is a folder holding audio of its own, directly or one disc
    down. A folder whose audio lies deeper is not one, and must never be
    treated as one: moving it would take everything beneath it along.

    Such a folder is usually a region -- "Africa" holding the artists who
    hold the albums -- and there is nothing to report about it, since the
    artists inside were already found by walking through it. Only a
    folder holding neither audio of its own nor any artist is genuinely
    passed over, and only those come back.

    Direct children only. An album deeper down is inside somebody's
    folder already, and moving it would be deciding where it lives.

    Nothing at all is loose when the playlist path is itself an artist's
    folder, which is what a settled playlist looks like once it has been
    moved somewhere permanent and pointed at directly. Its children are
    that artist's albums, gathered already from the home they sit in, and
    counting them a second time here would have every album contest
    itself and settle nothing.

    Args:
        root: The playlist path.
        homes: Every artist folder found beneath it, which are not
            albums.

    Returns:
        The loose albums, and the folders that are neither an album nor
        anything holding a known artist, each sorted by path.
    """
    settled: set[Path] = set(homes)
    if root in settled:
        return [], []

    albums: list[Path] = []
    unreadable: list[Path] = []

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry in settled:
            continue
        if holds_audio(entry):
            albums.append(entry)
        elif not any(home.is_relative_to(entry) for home in settled):
            unreadable.append(entry)

    return albums, unreadable


def plan(
    folders: Sequence[tuple[Path, Path]],
    disco_albums: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> PlaylistPlan:
    """Match every converted album to its discography album, without moving.

    Args:
        folders: `(folder, destination)` pairs -- the album as it stands,
            and the artist folder it belongs in. An album already inside
            one of its artist's folders is paired with that folder, so
            it is renamed where it sits rather than moved; a loose one is
            paired with where it should be filed. Deciding which is the
            caller's, since only it knows the artists.
        disco_albums: The discography's album folders, read-only.
        on_progress: Called with each folder as it is examined.

    Returns:
        What would move, and everything that could not be settled.
    """
    disco: dict[str, list[Path]] = defaultdict(list)
    for album in disco_albums:
        disco[names.album_title(album.name).casefold()].append(album)

    claims: defaultdict[str, list[tuple[Path, Path]]] = defaultdict(list)
    untagged: list[Path] = []
    unmatched: list[Path] = []
    ambiguous: list[Path] = []

    for candidate, destination in folders:
        title: str | None = identity(candidate)
        if title is None:
            untagged.append(candidate)
        elif title not in disco:
            unmatched.append(candidate)
        elif len(disco[title]) > 1:
            ambiguous.append(candidate)
        else:
            claims[title].append((candidate, destination))
        if on_progress is not None:
            on_progress(candidate)

    matches: list[Match] = []
    contested: list[tuple[Path, ...]] = []
    for title, claimants in claims.items():
        if len(claimants) > 1:
            contested.append(tuple(folder for folder, _ in claimants))
            continue
        claimant, destination = claimants[0]
        matches.append(
            Match(album=claimant, target=destination / settled_name(disco[title][0], claimant))
        )

    return PlaylistPlan(
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
    moved: int = 0
    failures: list[tuple[Path, str]] = []

    for match in playlist_plan.pending:
        match.target.parent.mkdir(parents=True, exist_ok=True)
        detail: str | None = _place(match)
        if detail is None:
            moved += 1
        else:
            failures.append((match.album, detail))
        if on_progress is not None:
            on_progress(match.album)

    return PlaylistReport(moved=moved, failures=tuple(failures))


def plan_covers(
    albums: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> CoverPlan:
    """Work out which albums need a loose cover written, without writing.

    The source is the art already inside the tracks, which the converter
    carried across from the discography. Nothing is read from the
    discography and nothing is embedded: the tracks are the source here,
    and the loose file is the copy.

    An album already holding the right bytes is left out, so a second run
    writes nothing. One holding different bytes is overwritten, which is
    what makes re-arting an album in the discography reach the playlist.

    Args:
        albums: The playlist's album folders.
        on_progress: Called with each album as it is examined.

    Returns:
        What would be written, and the albums with no art to write.
    """
    writes: list[CoverWrite] = []
    without_artwork: list[Path] = []

    for album in albums:
        cover: Cover | None = _embedded_cover(album)
        if cover is None:
            without_artwork.append(album)
        else:
            target: Path = album / COVER_NAME
            if _on_disk(target) != cover.data:
                writes.append(CoverWrite(target=target, data=cover.data))
        if on_progress is not None:
            on_progress(album)

    return CoverPlan(writes=tuple(writes), without_artwork=tuple(without_artwork))


def apply_covers(
    cover_plan: CoverPlan,
    on_progress: Callable[[Path], None] | None = None,
) -> CoverReport:
    """Write every loose cover the plan found.

    A write that fails is recorded and the run continues: one unwritable
    folder should not cost the rest their artwork.

    Args:
        cover_plan: A plan produced by `plan_covers`.
        on_progress: Called with each file as it is dealt with.

    Returns:
        A count of writes and the failures collected along the way.
    """
    written: int = 0
    failures: list[tuple[Path, str]] = []

    for write in cover_plan.writes:
        try:
            _ = write.target.write_bytes(write.data)
        except OSError as exc:
            failures.append((write.target, str(exc)))
        else:
            written += 1
        if on_progress is not None:
            on_progress(write.target)

    return CoverReport(written=written, failures=tuple(failures))


def identity(album: Path) -> str | None:
    """Read which album a folder holds, from the tags rather than the name.

    The first readable Album tag settles it -- the tracks of one folder
    are one album, so a second opinion would only cost a file open.

    Reads the folder's own tracks, and those one disc down, rather than
    everything beneath it. Handed something that merely holds albums, a
    recursive read would find a track several levels down and report one
    confident identity for the whole tree, which is how a container comes
    to be matched and moved as though it were an album.

    Args:
        album: The converted folder to read.

    Returns:
        The album's title, casefolded for matching, or `None` when no
        track carries a readable Album tag.
    """
    for track in album_tracks(album):
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
    pin, rest = names.split_front_mark(name)
    _, rest = names.split_index(rest)
    return f"{pin}{rest}"


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _collect_homes(
    folder: Path,
    artist_names: Set[str],
    found: dict[str, list[Path]],
    strangers: list[Path],
) -> None:
    """Descend a folder, adding the artists found and not stepping past them.

    Args:
        folder: The folder to examine.
        artist_names: The names to look for.
        found: The mapping to append matches to, in place.
        strangers: The list to append unrecognised artists to, in place.
    """
    if folder.name in artist_names:
        found[folder.name].append(folder)
        return

    children: list[Path] = []
    for entry in sorted(folder.iterdir(), key=lambda path: path.name):
        if entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
            return  # an album or a disc: it holds tracks, never an artist
        if entry.is_dir() and not entry.name.startswith("."):
            children.append(entry)

    if any(holds_audio(child) for child in children):
        strangers.append(folder)  # its children are albums, so it is an artist
        return

    for child in children:
        _collect_homes(child, artist_names, found, strangers)


def _embedded_cover(album: Path) -> Cover | None:
    """Read the art an album's tracks carry, capped and forced to JPEG.

    The first readable front cover settles it. The tracks of one folder
    came from one album, so they agree -- the vote `tags cover` holds
    exists to stop a stray scan on one file of a hand-assembled album
    winning, and nothing here is hand-assembled.

    Capped because the copy is for a phone, and to the same size the
    tracks already hold, so in practice this is the embedded image
    written out rather than anything re-encoded.

    Reads the album's own tracks, and those one disc down, rather than
    everything beneath it -- the same limit `identity` keeps, and for the
    same reason: handed a folder that merely holds albums, a recursive
    read would take art from some track buried inside it and write it out
    as though it belonged to the whole tree.

    Args:
        album: The album folder to read.

    Returns:
        The cover to write, or `None` when no track carries readable art
        or the bytes will not convert.
    """
    for track in album_tracks(album):
        cover: Cover | None = _cover_of(track)
        if cover is not None:
            return artwork.as_jpeg(artwork.for_embedding(cover))
    return None


def _cover_of(track: Path) -> Cover | None:
    """Read one track's front cover, or nothing when it cannot be read.

    Args:
        track: The audio file to read.

    Returns:
        Its front cover, or `None` when it carries none or will not read.
    """
    try:
        return metadata.read_cover(track)
    except Exception:  # noqa: BLE001 - any file can fail in ways not worth enumerating
        return None


def _on_disk(target: Path) -> bytes | None:
    """Read what a loose cover file already holds.

    Args:
        target: The file to read.

    Returns:
        Its bytes, or `None` when it is absent or will not read -- both
        of which mean it does not hold what it should.
    """
    try:
        return target.read_bytes()
    except OSError:
        return None


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
    _, rest = names.split_front_mark(tag)
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
