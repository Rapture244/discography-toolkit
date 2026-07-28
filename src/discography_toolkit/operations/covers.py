# src/discography_toolkit/operations/covers.py
"""Settling one front cover per album, on disk and inside every track.

An album's artwork lives in two places: a loose file the file manager
shows, and a copy inside each track the player reads. This works out
which image both should hold, what the loose file should be called, and
which tracks are missing it.

The winner is the best copy found anywhere, not whatever the tags happen
to hold. That matters on a second run: the embedded copy is capped, so
trusting the tags would read the capped image back, call it the album's
cover, and overwrite the full-resolution file on disk with it --
destroying the master the first run went out of its way to keep.

Planning never writes. It reads what is there, decides what should be
there, and records the difference, so a caller can show the whole change
before any of it happens, and an album already settled is left alone
rather than rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from discography_toolkit.core import artwork, layout, metadata

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from discography_toolkit.core.artwork import Cover

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
type Source = Literal["tags", "disk"]
"""Where an album's chosen cover was found.

There is no "none": an album with no artwork has no settlement at all.
"""

_HELD: Final[str] = "kept: the album's cover file could not be settled"
"""Why a duplicate outlived a run -- reported rather than silently skipped."""


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Settlement:
    """What an album's artwork needs, once it has some.

    `write` and `rename_from` are alternatives: the loose file is either
    written from the chosen bytes or moved into place, never both, and
    neither when it is already correct.

    Attributes:
        cover: The image the album settled on, at full resolution.
        payload: The capped copy each track gets.
        source: Where the cover was found.
        target: What the loose file ends up called, set even when it
            already is, so a report can name it.
        write: `True` when `target` must be written from `cover`.
        rename_from: An image already holding the right bytes under
            another name, moved into `target` rather than copied.
        delete: Loose images left over once `target` is in place --
            duplicates of the same artwork under names nothing reads.
        embed: Tracks whose front cover needs writing.
        correct: Tracks already carrying `payload`.
    """

    cover: Cover
    payload: Cover
    source: Source
    target: Path
    write: bool = False
    rename_from: Path | None = None
    delete: tuple[Path, ...] = ()
    embed: tuple[Path, ...] = ()
    correct: int = 0


@dataclass(frozen=True, slots=True)
class AlbumPlan:
    """What one album needs done.

    Attributes:
        album: The album folder.
        tracks: How many audio files it holds, so an album with tracks
            and no artwork is not mistaken for an empty placeholder.
        unsupported: Tracks whose format carries no cover here.
        settlement: The artwork work, or `None` when the album has no
            cover anywhere.
    """

    album: Path
    tracks: int = 0
    unsupported: int = 0
    settlement: Settlement | None = None

    @property
    def changes(self) -> int:
        """How many operations applying this album would perform."""
        settlement: Settlement | None = self.settlement
        if settlement is None:
            return 0
        return (
            int(settlement.write)
            + int(settlement.rename_from is not None)
            + len(settlement.delete)
            + len(settlement.embed)
        )


@dataclass(frozen=True, slots=True)
class CoverPlan:
    """What a run would change, before anything is written.

    Attributes:
        albums: One entry per album examined, in the order given.
    """

    albums: tuple[AlbumPlan, ...]

    @property
    def total(self) -> int:
        """How many albums were examined."""
        return len(self.albums)

    @property
    def pending(self) -> tuple[AlbumPlan, ...]:
        """Albums with something to do."""
        return tuple(album for album in self.albums if album.changes)

    @property
    def without_artwork(self) -> tuple[AlbumPlan, ...]:
        """Albums holding tracks but no cover anywhere."""
        return tuple(
            album for album in self.albums if album.settlement is None and album.tracks > 0
        )

    @property
    def empty(self) -> tuple[AlbumPlan, ...]:
        """Placeholder folders holding no tracks at all."""
        return tuple(album for album in self.albums if album.tracks == 0)

    @property
    def writes(self) -> int:
        """Loose cover files to write."""
        return sum(1 for album in self.albums if album.settlement and album.settlement.write)

    @property
    def renames(self) -> int:
        """Loose cover files to move into place."""
        return sum(1 for album in self.albums if album.settlement and album.settlement.rename_from)

    @property
    def deletions(self) -> int:
        """Duplicate images to remove."""
        return sum(len(album.settlement.delete) for album in self.albums if album.settlement)

    @property
    def embeds(self) -> int:
        """Tracks to embed a cover into."""
        return sum(len(album.settlement.embed) for album in self.albums if album.settlement)

    @property
    def touched(self) -> tuple[Path, ...]:
        """Every path applying the plan reports, in the order it will.

        A caller sizing a progress display needs the paths themselves
        rather than a count: bars are grouped by the artist a path sits
        under. Re-deriving this outside would mean a second copy of
        `apply`'s order, free to drift from the first.
        """
        paths: list[Path] = []

        for album in self.albums:
            settlement: Settlement | None = album.settlement
            if settlement is None:
                continue
            if settlement.rename_from is not None:
                paths.append(settlement.rename_from)
            if settlement.write:
                paths.append(settlement.target)
            paths.extend(settlement.delete)
            paths.extend(settlement.embed)

        return tuple(paths)

    @property
    def changes(self) -> int:
        """How many operations applying the whole plan would perform."""
        return sum(album.changes for album in self.albums)


@dataclass(frozen=True, slots=True)
class CoverReport:
    """What happened when a plan was applied.

    Attributes:
        written: Loose cover files written from the chosen bytes.
        renamed: Loose cover files moved into place.
        deleted: Duplicate images removed.
        embedded: Tracks successfully given their cover.
        failures: `(path, reason)` for each operation that failed.
    """

    written: int = 0
    renamed: int = 0
    deleted: int = 0
    embedded: int = 0
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    albums: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> CoverPlan:
    """Work out what every album needs, without writing.

    Args:
        albums: The album folders to examine. Discovery belongs to the
            caller.
        on_progress: Called with each album as it is examined, so a
            caller can drive a display without this module knowing one
            exists.

    Returns:
        One plan per album, in the order given.
    """
    plans: list[AlbumPlan] = []

    for album in albums:
        plans.append(_plan_album(album))
        if on_progress is not None:
            on_progress(album)

    return CoverPlan(albums=tuple(plans))


def apply(
    cover_plan: CoverPlan,
    on_progress: Callable[[Path], None] | None = None,
) -> CoverReport:
    """Carry out every change the plan found.

    An operation that fails is recorded and the run continues: one
    unwritable track should not abandon the other five hundred.

    Args:
        cover_plan: A plan produced by `plan`.
        on_progress: Called with each path as it is dealt with, whether
            or not the operation succeeded, so a bar sized from the
            plan's `changes` still fills.

    Returns:
        Counts of what was done, and the failures collected along the
        way.
    """
    written: int = 0
    renamed: int = 0
    deleted: int = 0
    embedded: int = 0
    failures: list[tuple[Path, str]] = []

    def record(path: Path, detail: str | None) -> bool:
        """Note one operation's outcome and report it to the caller.

        Args:
            path: What was operated on.
            detail: Why it failed, or `None` when it succeeded.

        Returns:
            `True` when the operation succeeded.
        """
        if detail is not None:
            failures.append((path, detail))
        if on_progress is not None:
            on_progress(path)
        return detail is None

    for album in cover_plan.pending:
        settlement: Settlement | None = album.settlement
        if settlement is None:
            continue

        settled: bool = True
        moved: Path | None = settlement.rename_from
        if moved is not None:
            settled = record(moved, _rename(moved, settlement.target))
            renamed += int(settled)

        if settlement.write:
            target: Path = settlement.target
            settled = record(target, _write(target, settlement.cover))
            written += int(settled)

        # Duplicates go only once the canonical file is in place.
        # Deleting them after a failed write would leave the album with
        # no artwork at all, which is worse than leaving it untidy.
        for stale in settlement.delete:
            deleted += int(record(stale, _delete(stale) if settled else _HELD))

        for track in settlement.embed:
            embedded += int(record(track, _embed(track, settlement.payload)))

    return CoverReport(
        written=written,
        renamed=renamed,
        deleted=deleted,
        embedded=embedded,
        failures=tuple(failures),
    )


def operation_paths(cover_plan: CoverPlan) -> list[Path]:
    """List every path `apply` will report, one per operation it performs.

    The same paths `apply` hands to `on_progress`, gathered up front so a
    caller can size a per-artist bar before the work starts. Order does
    not matter -- the caller groups by artist, not by sequence -- but the
    count matches `changes` exactly, so a bar built from it fills to the
    brim and no further. This mirrors the operations `apply` runs, so the
    two are changed together.

    Args:
        cover_plan: A plan produced by `plan`.

    Returns:
        A path for each rename, write, deletion and embed to come.
    """
    paths: list[Path] = []
    for album in cover_plan.pending:
        settlement: Settlement | None = album.settlement
        if settlement is None:
            continue
        if settlement.rename_from is not None:
            paths.append(settlement.rename_from)
        if settlement.write:
            paths.append(settlement.target)
        paths.extend(settlement.delete)
        paths.extend(settlement.embed)
    return paths


# ==================================================================================== #
#                                       PLANNING                                       #
# ==================================================================================== #
def _plan_album(album: Path) -> AlbumPlan:
    """Decide what one album needs, without changing anything.

    Args:
        album: The album folder to examine.

    Returns:
        Its plan -- the chosen cover, where the loose file belongs, and
        which tracks are missing it.
    """
    tracks: list[Path] = layout.find_audio_files(album)
    if not tracks:
        # No music, no album: a stray image in a placeholder folder is
        # not artwork anything will ever read.
        return AlbumPlan(album=album)

    taggable: list[Path] = [track for track in tracks if metadata.supports_cover(track)]
    found: dict[Path, Cover | None] = {track: _front_cover(track) for track in taggable}
    loose: dict[Path, Cover] = _loose_covers(album)

    chosen, source = _best(found, loose)
    if chosen is None:
        return AlbumPlan(album=album, tracks=len(tracks), unsupported=len(tracks) - len(taggable))

    payload: Cover = artwork.for_embedding(chosen)
    embed: tuple[Path, ...] = tuple(
        track for track, current in found.items() if current is None or current.data != payload.data
    )

    return AlbumPlan(
        album=album,
        tracks=len(tracks),
        unsupported=len(tracks) - len(taggable),
        settlement=_settle(album, chosen, payload, source, loose, embed, len(found) - len(embed)),
    )


def _best(
    embedded: dict[Path, Cover | None], loose: dict[Path, Cover]
) -> tuple[Cover | None, Source]:
    """Pick the album's cover from everything it carries.

    The tracks vote first, so a stray scan on one file cannot win. The
    loose file then takes over only by being genuinely bigger, which is
    what keeps a full-resolution master from being replaced by the
    capped copy embedded from it.

    Args:
        embedded: Each taggable track and the cover it carries, if any.
        loose: Images sitting in the album folder, canonical name first.

    Returns:
        The winner and where it came from. `None` when the album has no
        artwork at all, and the source alongside it means nothing.
    """
    chosen: Cover | None = artwork.choose([cover for cover in embedded.values() if cover])

    if loose:
        # Ties keep the first, and `find_cover_images` orders canonical
        # names first, so "cover.jpg" beats an identical "folder.jpg".
        best: Cover = max(loose.values(), key=artwork.longest_edge)
        if chosen is None or artwork.longest_edge(best) > artwork.longest_edge(chosen):
            return best, "disk"

    return chosen, "tags"


def _settle(
    album: Path,
    chosen: Cover,
    payload: Cover,
    source: Source,
    loose: dict[Path, Cover],
    embed: tuple[Path, ...],
    correct: int,
) -> Settlement:
    """Decide how the loose file reaches its canonical name.

    The extension tells the truth about the bytes -- ".png" only when
    the artwork genuinely is one. An album already using "folder.jpg"
    has it moved rather than gaining a second copy, and anything
    recognizable left over afterwards is the same artwork under a name
    no player looks for.

    Args:
        album: The album folder.
        chosen: The cover it settled on.
        payload: The capped copy for its tracks.
        source: Where `chosen` came from.
        loose: Images sitting in the album folder.
        embed: Tracks needing the cover written.
        correct: Tracks already carrying it.

    Returns:
        The settlement for this album.
    """
    target: Path = album / f"cover{chosen.extension}"
    settled: Cover | None = loose.get(target)

    write: bool = False
    rename_from: Path | None = None
    if settled is None:
        rename_from = next(
            (path for path, cover in loose.items() if cover.data == chosen.data), None
        )
        write = rename_from is None
    elif settled.data != chosen.data:
        # The name is taken by different bytes, so it is overwritten
        # rather than moved onto -- a move would have to delete it first.
        write = True

    return Settlement(
        cover=chosen,
        payload=payload,
        source=source,
        target=target,
        write=write,
        rename_from=rename_from,
        delete=tuple(path for path in loose if path not in {target, rename_from}),
        embed=embed,
        correct=correct,
    )


def _front_cover(track: Path) -> Cover | None:
    """Read a track's embedded front cover, if it has a readable one.

    Args:
        track: The audio file to read.

    Returns:
        Its front cover, or `None` when it carries none or cannot be
        read -- an unreadable track casts no vote rather than abandoning
        the album.
    """
    try:
        cover: Cover | None = metadata.read_cover(track)
    except Exception:  # noqa: BLE001 - any file can fail in ways not worth enumerating
        return None
    return cover


def _loose_covers(album: Path) -> dict[Path, Cover]:
    """Read the images sitting in an album folder.

    Args:
        album: The album folder to search.

    Returns:
        Each readable image by path, canonical name first, so a caller
        settling on one filename knows which it should be.
    """
    found: dict[Path, Cover] = {}

    for path in layout.find_cover_images(album):
        try:
            data: bytes = path.read_bytes()
        except OSError:
            continue
        cover: Cover | None = artwork.read(data)
        if cover is not None:
            found[path] = cover

    return found


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def _rename(source: Path, target: Path) -> str | None:
    """Move an image onto the canonical name.

    Args:
        source: The file to move.
        target: Where it belongs.

    Returns:
        The failure's detail, or `None` on success.
    """
    try:
        _ = source.replace(target)
    except OSError as exc:
        return str(exc)
    return None


def _write(target: Path, cover: Cover) -> str | None:
    """Write the loose cover file, at full resolution.

    Args:
        target: Where it belongs.
        cover: The image to write.

    Returns:
        The failure's detail, or `None` on success.
    """
    try:
        _ = target.write_bytes(cover.data)
    except OSError as exc:
        return str(exc)
    return None


def _delete(stale: Path) -> str | None:
    """Remove a duplicate of the settled artwork.

    Args:
        stale: The file to remove.

    Returns:
        The failure's detail, or `None` on success.
    """
    try:
        stale.unlink()
    except OSError as exc:
        return str(exc)
    return None


def _embed(track: Path, payload: Cover) -> str | None:
    """Write one track's front cover.

    Args:
        track: The audio file to write into.
        payload: The capped copy to embed.

    Returns:
        The failure's detail, or `None` on success.
    """
    try:
        metadata.write_cover(track, payload)
    except Exception as exc:  # noqa: BLE001 - a corrupt file must not stop the run
        return str(exc)
    return None
