# src/discography_toolkit/cli/commands/playlist.py
"""The `rapt playlist` command.

Syncs a playlist against the discography that is its source of truth.

Two paths, and neither is assumed to mirror the other. The discography
path is the roster: every artist at or beneath it, and for each of them
the albums they hold. The playlist path is the search area: wherever
those artists already live under it, at whatever depth.

The playlist is a curation, not a copy. One artist may sit in several
places -- a shakuhachi record filed under "Classical" because that is
what it is to you, beside the same artist's shelf under "Japan" -- and
all of those folders are theirs. So nothing here decides where an album
lives. An album already inside one of its artist's folders is renamed
where it sits; only one dropped loose by a converter, belonging to no
artist folder yet, is filed, and then into a folder named for its artist
beside the others.

What the discography does decide is everything about the name: the
index, the year, the front mark, the availability marker, an "(EP)".
Only the quality word is read from the playlist's own files, since a
FLAC album arrives here as Opus.

The tags are read straight off the discography's own tracks, paired
within the album by disc and track number. A converter copies them once,
at the moment it runs; anything retagged upstream afterwards only
reaches here by being read again. The Album tag is the one exception,
built from the folder name instead -- the discography writes the bare
title, being shared, while a playlist wants the pinned and dated form a
player sorts on.

That is what makes a second run a sync: rename or renumber an album in
the discography, run again, and the playlist follows -- and unpin one
there and the pin comes off here.

Four passes per artist, in an order that cannot change: fold, then write
the tags, then put a disc number on the filenames of a multi-disc album,
then write a cover.jpg from the art the tracks carry. That order is why
there is no dry run -- a preview of the tag pass would describe folders
that do not exist yet -- so the fold alone is shown before a single
confirmation, as `layout` and `organize` have.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

from rich.cells import cell_len, set_cell_size
import typer

from discography_toolkit.cli.console import (
    Notice,
    echo_banner,
    echo_failures,
    echo_notices,
    make_bar,
    make_progress,
)
from discography_toolkit.cli.scope import confirm_or_exit, resolve_path
from discography_toolkit.core.layout import (
    album_tracks,
    discover_albums,
    find_artist_folders,
)
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import album_title, strip_artist_label
from discography_toolkit.operations import discs, playlist as folding, tagging

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Artist:
    """One artist as both sides see them.

    Attributes:
        name: The discography folder's name without its count label.
        region: The discography folder holding them -- "Japan", "Tuva".
            Empty when the run was pointed at the artist itself, there
            being nothing above them in scope.
        albums: The albums the discography holds for them.
        homes: The playlist folders named for them, empty when the
            playlist holds none of their work yet.
        candidates: `(folder, destination)` for every album folder this
            run should try to place.
    """

    name: str
    region: str
    albums: tuple[Path, ...]
    homes: tuple[Path, ...]
    candidates: tuple[tuple[Path, Path], ...]


@dataclass(frozen=True, slots=True)
class Synced:
    """What a run did for one artist.

    Attributes:
        name: The artist's name.
        matched: How many folders were placed against a discography
            album. Zero with candidates present means nothing could be
            settled, which is a different thing from nothing to do.
        folded: How many album folders were moved or renamed.
        tagged: How many tracks had their tags written.
        prefixed: How many filenames took a disc number.
        covered: How many loose covers were written.
        notices: What the run saw but would not act on.
        failures: `(path, reason)` for each operation that failed.
    """

    name: str
    matched: int = 0
    folded: int = 0
    tagged: int = 0
    prefixed: int = 0
    covered: int = 0
    notices: tuple[Notice, ...] = ()
    failures: tuple[tuple[Path, str], ...] = field(default=())

    @property
    def changed(self) -> bool:
        """Whether anything about this artist actually moved."""
        return bool(self.folded or self.tagged or self.prefixed or self.covered)


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def playlist(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="A discography path: one artist, or a shelf of them. Read, never written.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    converted: Annotated[
        Path | None,
        typer.Option(
            "--converted",
            "-c",
            help="The playlist path to sync, or the folder a converter wrote into.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Sync a playlist against the discography it was converted from.

    Args:
        path: A discography path; prompted for if omitted.
        converted: The playlist path; prompted for if omitted.

    Raises:
        typer.Exit: When no artist is found, nothing matches, the user
            aborts, or the run completes.
    """
    disco: Path = resolve_path(path, "Path to the discography")
    target: Path = resolve_path(converted, "Path to the playlist")

    roster: list[Path] = find_artist_folders(disco)
    if not roster:
        typer.secho(
            f"\nNo labelled artist found at or beneath {disco.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run the layout pass first -- it writes the label this reads from.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    echo_banner("Playlist", target.name, children=[str(disco), str(target)])

    artists, skipped, unclaimed = _gather(roster, disco, target)
    # A folder can carry a label and no name -- "[1 * 1F * 0L * 0M]" and
    # nothing else passes `is_artist_folder` while `strip_artist_label`
    # reads no name out of it. Refused here rather than left to the width
    # below, where an empty roster reaches `max()` and ends the run in a
    # traceback instead of a sentence.
    if not artists:
        typer.secho(
            f"\nNo artist name could be read beneath {disco.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "A folder wearing only its count label has no name to sync under.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    # One column for the whole run: the roster below, and the progress
    # bar labels after it, so every bar starts where the names ended.
    width: int = max(cell_len(artist.name) for artist in artists)
    _echo_found(artists, skipped, unclaimed, width)

    workable: list[Artist] = [artist for artist in artists if artist.candidates]
    wanted: int = sum(len(artist.candidates) for artist in workable)
    if not wanted:
        typer.secho(f"\nNothing of these artists found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()
    with make_progress(noun="albums") as progress:
        matching = make_bar(
            progress, "Playlist: matching", sum(len(a.candidates) for a in workable)
        )
        planned: list[tuple[Artist, folding.PlaylistPlan]] = [
            (artist, folding.plan(artist.candidates, artist.albums, on_progress=matching))
            for artist in workable
        ]

    moves: int = sum(len(plan.pending) for _, plan in planned)
    _echo_moves(planned)

    typer.echo()
    typer.secho(f"{moves} album folder(s) will be moved or renamed.", fg=typer.colors.YELLOW)
    typer.secho(
        "Each matched album is then tagged from the discography, prefixed, and covered.",
        fg=typer.colors.YELLOW,
    )
    typer.secho("There is no dry run.", fg=typer.colors.YELLOW)
    confirm_or_exit(f"Sync {wanted} album(s) beneath {target.name!r}?")

    # One bar for the writing, sized before anything moves: every track
    # to be read for its tags, and one step per album for its cover.
    # Counted from the folders as they stand, a move changing where they
    # are and not how many tracks they hold.
    total: int = sum(
        len(album_tracks(match.album)) + 1
        for _, fold_plan in planned
        for match in fold_plan.matches
    )

    typer.echo()
    results: list[Synced] = []
    with make_progress() as progress:
        writing = make_bar(progress, "Playlist: writing", total)
        for artist, fold_plan in planned:
            results.append(_sync(artist, fold_plan, writing))

    # Printed after the bar closes, not between artists: a live display
    # and a stream of lines to the same terminal fight over the cursor.
    for result in results:
        _echo_artist(result)

    _echo_summary(results)


# ==================================================================================== #
#                                      GATHERING                                       #
# ==================================================================================== #
def _gather(
    roster: Sequence[Path], disco: Path, target: Path
) -> tuple[list[Artist], list[Path], list[tuple[Path, str]]]:
    """Work out, for every artist, which folders this run should place.

    Two kinds of candidate, told apart by where they sit. One already
    inside a folder of its artist's is paired with that folder, so it is
    renamed where it is and the playlist's own arrangement survives. A
    loose one -- what a converter drops -- has no owner, so it is offered
    to whichever artist's discography claims its title, and filed beside
    them.

    A loose folder claimed by more than one artist is left out of every
    one of them: two artists holding an album of the same title is not
    something a name can settle, and guessing would file it under the
    wrong one.

    Args:
        roster: The discography's artist folders.
        disco: The discography path the run was given, so an artist
            pointed at directly is known to have no region above it.
        target: The playlist path to search.

    Returns:
        One entry per artist, in roster order; the folders in the
        playlist the run has nothing to say about -- an artist the
        discography does not know, or something that is neither; and
        `(folder, reason)` for each loose album that could not be given
        to an artist.
    """
    names: dict[Path, str] = {}
    for folder in roster:
        label: str | None = strip_artist_label(folder.name)
        if label is not None:
            names[folder] = label

    regions: dict[str, str] = {
        name: (folder.parent.name if folder != disco else "") for folder, name in names.items()
    }

    homes, strangers = folding.find_homes(target, set(names.values()))
    settled: list[Path] = [home for found in homes.values() for home in found]
    # Strangers count as settled for this: a region folder holding one is
    # not a folder the run passed over, it is a folder it walked through.
    loose, skipped = folding.loose_albums(target, [*settled, *strangers])

    albums: dict[str, tuple[Path, ...]] = {
        name: tuple(discover_albums(folder)) for folder, name in names.items()
    }
    titles: dict[str, set[str]] = {
        name: {album_title(album.name).casefold() for album in found}
        for name, found in albums.items()
    }
    owners, unclaimed = _assign(loose, titles)

    artists: list[Artist] = []
    for name in names.values():
        found = homes.get(name, [])
        candidates: list[tuple[Path, Path]] = [
            (album, home)
            for home in found
            for album in sorted(home.iterdir())
            if album.is_dir() and not album.name.startswith(".")
        ]
        candidates.extend(
            (album, found[0] if found else target / name)
            for album, owner in owners.items()
            if owner == name
        )
        artists.append(
            Artist(
                name=name,
                region=regions[name],
                albums=albums[name],
                homes=tuple(found),
                candidates=tuple(candidates),
            )
        )

    return artists, [*skipped, *strangers], unclaimed


def _assign(
    loose: Sequence[Path], titles: Mapping[str, set[str]]
) -> tuple[dict[Path, str], list[tuple[Path, str]]]:
    """Decide which artist each loose album folder belongs to.

    Read from the folder's own Album tag rather than its name, as every
    other match here is.

    A folder that cannot be assigned comes back with the reason, because
    nothing downstream will mention it otherwise: only an assigned folder
    becomes a candidate, and only a candidate reaches the fold that
    reports what it could not settle. Left out of both, an album a
    converter dropped would vanish from a run that claimed to have synced
    everything.

    The three reasons are kept apart because they send you to different
    places. No tag is a fault in the file; no claimant is a fault in the
    discography, or an album since removed from it; two claimants is two
    artists holding a record of one name, which is not something a title
    can settle.

    Args:
        loose: Album folders sitting in the playlist path itself.
        titles: Each artist's album titles, casefolded.

    Returns:
        Each assignable folder mapped to its artist's name, and
        `(folder, reason)` for each one that could not be.
    """
    owners: dict[Path, str] = {}
    unclaimed: list[tuple[Path, str]] = []

    for album in loose:
        title: str | None = folding.identity(album)
        if title is None:
            unclaimed.append((album, "carries no Album tag to match on"))
            continue

        claimants: list[str] = [name for name, held in titles.items() if title in held]
        if len(claimants) == 1:
            owners[album] = claimants[0]
        elif claimants:
            unclaimed.append((album, f"claimed by {len(claimants)} artists at once"))
        else:
            unclaimed.append((album, "matches no album in the discography"))

    return owners, unclaimed


# ==================================================================================== #
#                                      SYNCING                                         #
# ==================================================================================== #
def _sync(
    artist: Artist,
    fold_plan: folding.PlaylistPlan,
    advance: Callable[[Path], None],
) -> Synced:
    """Fold, tag and cover one artist's albums.

    In that order and only that order: the Album tag is read off the
    folder name the fold has just written, and the cover off the tracks
    once they are where they belong. Which is also why the plan is made
    before the confirmation and handed in here -- the fold can be shown
    before it happens, and the two passes that follow it cannot.

    Args:
        artist: The artist to sync.
        fold_plan: What the fold worked out, already shown and agreed to.
        advance: The run's single progress callback, shared by every
            artist so the whole write reads as one bar.

    Returns:
        What the three passes did, and what they could not.
    """
    report = folding.apply(fold_plan)

    placed: list[folding.Match] = [match for match in fold_plan.matches if match.target.is_dir()]
    settled: list[Path] = sorted({match.target for match in placed})

    tagged, tag_notices, tag_failures = _write_tags(placed, advance)
    prefixed, disc_failures = _prefix_discs(settled)
    covered, art_notices, art_failures = _write_covers(settled, advance)

    return Synced(
        name=artist.name,
        matched=len(fold_plan.matches),
        folded=report.moved,
        tagged=tagged,
        prefixed=prefixed,
        covered=covered,
        notices=(*_fold_notices(fold_plan), *tag_notices, *art_notices),
        failures=(*report.failures, *tag_failures, *disc_failures, *art_failures),
    )


def _prefix_discs(settled: Sequence[Path]) -> tuple[int, tuple[tuple[Path, str], ...]]:
    """Put each multi-disc track's number at the front of its filename.

    The same pass the discography gets, for the same reason: a converter
    writes one flat folder whatever the album was, and a flat folder
    sorted by name is the only view a phone offers. Run after the tags,
    since the number it puts on the filename is the settled one just
    written.

    Args:
        settled: The playlist's album folders, as they now stand.

    Returns:
        How many files took a prefix, and what failed.
    """
    if not settled:
        return 0, ()

    tracks: list[Path] = [track for album in settled for track in album_tracks(album)]
    report = discs.apply(discs.plan(settled, tracks))
    return report.renamed, report.failures


def _write_tags(
    placed: Sequence[folding.Match],
    advance: Callable[[Path], None],
) -> tuple[int, tuple[Notice, ...], tuple[tuple[Path, str], ...]]:
    """Write each converted track's tags from the track it came from.

    The playlist is a subset of the discography, so it says what the
    discography says. A converter copies the tags once and they drift the
    moment anything is retagged upstream; this reads them again and puts
    them back.

    The Album tag is the one exception, built from the folder the fold has
    just named rather than copied. The discography's is the bare title,
    being shared; the playlist's carries the pin, the year and the format
    a player sorts on. It follows the discography all the same -- through
    the folder name, which the fold takes from it.

    Args:
        placed: The matches whose folders are in place.
        advance: The run's progress callback, called per track read.

    Returns:
        How many tracks were written, what could not be paired, and what
        failed.
    """
    wanted: dict[Path, dict[Tag, str]] = {}
    spoiled: list[tuple[Path, int, int]] = []

    for match in placed:
        mirrored, missing = folding.mirror(match)
        album: str = folding.album_tag(match.target.name)
        for track, values in mirrored.items():
            wanted[track] = {**values, Tag.ALBUM: album}
        if missing:
            spoiled.append((match.target, len(missing), len(mirrored) + len(missing)))

    if not wanted:
        return 0, _unpaired_notice(spoiled), ()

    plan = tagging.plan(
        list(wanted),
        [Tag.ALBUM, *folding.MIRRORED],
        lambda track, _current: wanted.get(track, {}),
        on_progress=advance,
    )
    report = tagging.apply(plan)

    return report.written, _unpaired_notice(spoiled), report.failures


def _unpaired_notice(spoiled: Sequence[tuple[Path, int, int]]) -> tuple[Notice, ...]:
    """Name the albums holding tracks no discography track answers for.

    Grouped by album and counted against its size, because that is what
    tells the two cases apart. A few tracks of one album is a conversion
    that skipped some, or leftovers from an older run. Every track of an
    album is the pairing itself failing -- most often because one side
    carries no track numbers at all -- and a flat list of forty-seven
    filenames hides which of the two it is.

    Args:
        spoiled: Each album, how many of its tracks went unpaired, and
            how many it holds.

    Returns:
        One notice when there were any, empty otherwise.
    """
    if not spoiled:
        return ()

    total: int = sum(count for _, count, _ in spoiled)
    return (
        Notice(
            summary=f"{total} track(s) match no track in the discography",
            details=tuple(
                f"{album.name!r} -- {count} of {held} track(s)" for album, count, held in spoiled
            ),
        ),
    )


def _write_covers(
    settled: Sequence[Path],
    advance: Callable[[Path], None],
) -> tuple[int, tuple[Notice, ...], tuple[tuple[Path, str], ...]]:
    """Write one cover.jpg per album, from the art its tracks carry.

    Args:
        settled: The artist's album folders, as they now stand.
        advance: The run's progress callback, called per album read.

    Returns:
        How many covers were written, what had no art, and what failed.
    """
    if not settled:
        return 0, (), ()

    plan = folding.plan_covers(settled, on_progress=advance)
    report = folding.apply_covers(plan)

    notices: tuple[Notice, ...] = ()
    if plan.without_artwork:
        notices = (
            Notice(
                summary=f"{len(plan.without_artwork)} album(s) carry no art to write out",
                details=tuple(f"{album.name!r}" for album in plan.without_artwork),
            ),
        )
    return report.written, notices, report.failures


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _fold_notices(fold_plan: folding.PlaylistPlan) -> tuple[Notice, ...]:
    """Phrase what the fold saw but would not act on.

    Args:
        fold_plan: The plan the fold worked from.

    Returns:
        One notice per kind there was, empty when there was none.
    """
    found: tuple[tuple[Sequence[Path], str], ...] = (
        (fold_plan.unmatched, "folder(s) match no album in the discography"),
        (fold_plan.untagged, "folder(s) carry no Album tag to match on"),
        (fold_plan.ambiguous, "folder(s) name several albums at once"),
    )
    notices: list[Notice] = [
        Notice(
            summary=f"{len(folders)} {phrase}",
            details=tuple(f"{album.name!r}" for album in folders),
        )
        for folders, phrase in found
        if folders
    ]

    if fold_plan.contested:
        notices.append(
            Notice(
                summary=f"{len(fold_plan.contested)} album(s) claimed by more than one folder",
                details=tuple(
                    ", ".join(f"{album.name!r}" for album in group) for group in fold_plan.contested
                ),
            )
        )
    return tuple(notices)


def _echo_found(
    artists: Sequence[Artist],
    skipped: Sequence[Path],
    unclaimed: Sequence[tuple[Path, str]],
    width: int,
) -> None:
    """Print what the roster turned up, before anything is agreed to.

    Every artist the discography path covers, and where each stands in
    the playlist. An artist with nothing there is named too, dimmed: with
    eight of them it is the difference between "you have not converted
    these yet" and "you pointed at the wrong playlist", and a run that
    listed only the ones it found would look identical either way.

    Grouped under the discography folder that holds them, which is a fact
    about the artist rather than about where you filed their music -- one
    artist may sit in several places in the playlist, or none. The
    heading only appears when there is more than one region to tell
    apart, so pointing at a single one reads as a plain list.

    Args:
        artists: Every artist in the roster, with what was found for
            them.
        skipped: Folders in the playlist the run has nothing to say
            about -- an artist the discography does not know, or
            something that is neither an album nor an artist.
        unclaimed: `(folder, reason)` for each loose album that could not
            be given to an artist. Named here rather than left to the
            fold, which never sees them: an album that reaches neither is
            an album the run simply stopped mentioning.
        width: The column every artist's name is padded to. By cell
            width, not character count: a CJK name is half as many
            characters as it is columns wide, and "{:<40}" would step
            the column left by one for every one of them.
    """
    typer.echo()
    grouped: bool = len({artist.region for artist in artists}) > 1
    indent: str = "    " if grouped else "  "
    region: str = ""

    for artist in artists:
        if grouped and artist.region != region:
            region = artist.region
            typer.secho(f"  {region}", fg=typer.colors.MAGENTA, bold=True)

        name: str = set_cell_size(artist.name, width)
        if not artist.candidates:
            typer.secho(
                f"{indent}{name}  nothing found in the playlist",
                fg=typer.colors.BRIGHT_BLACK,
            )
            continue

        where: str = (
            f"in {len(artist.homes)} folder(s)"
            if len(artist.homes) > 1
            else ("to file" if not artist.homes else "to sync")
        )
        typer.secho(
            f"{indent}{name}  {len(artist.candidates)} album(s) {where}",
            fg=typer.colors.CYAN,
        )

    if skipped:
        typer.secho(
            f"\n  {len(skipped)} folder(s) in the playlist match no artist in the discography:",
            fg=typer.colors.YELLOW,
        )
        for folder in skipped:
            typer.secho(f"      {folder.name!r}", fg=typer.colors.BRIGHT_BLACK)

    if unclaimed:
        typer.secho(
            f"\n  {len(unclaimed)} loose album(s) could not be given to an artist:",
            fg=typer.colors.YELLOW,
        )
        for folder, reason in unclaimed:
            typer.secho(f"      {folder.name!r} -- {reason}", fg=typer.colors.BRIGHT_BLACK)


def _echo_moves(planned: Sequence[tuple[Artist, folding.PlaylistPlan]]) -> None:
    """Show every rename the fold would make, before it is agreed to.

    The one part of the run that can be previewed, and the one that
    moves things. The two passes after it cannot be: the Album tag is
    read off a folder name the fold has not written yet.

    Every move is listed rather than counted or truncated. A count told
    nobody that seventeen albums were about to be folded into one folder
    under the name of a single record; the line saying so would have.
    Truncating would hide exactly the odd one out, which is the only line
    worth reading.

    An artist with nothing to move is left out. On a sync -- the ordinary
    case, everything already in place -- that means this prints nothing
    at all.

    Args:
        planned: Each artist and what the fold worked out for them.
    """
    for artist, fold_plan in planned:
        if not fold_plan.pending:
            continue

        typer.secho(f"\n  {artist.name}", fg=typer.colors.CYAN, bold=True)
        for match in fold_plan.pending:
            typer.secho(f"      {match.album.name!r}", fg=typer.colors.BRIGHT_BLACK)
            typer.secho(f"        \u2192 {match.target.name!r}", fg=typer.colors.GREEN)


def _echo_artist(result: Synced) -> None:
    """Print one artist's line, with anything needing an eye beneath it.

    Args:
        result: What the run did for them.
    """
    typer.secho(f"  {result.name!r}", fg=typer.colors.CYAN, bold=True)

    if result.changed:
        done: str = f"{result.folded} folded, {result.tagged} tagged"
        typer.secho(
            f"      {done}, {result.prefixed} prefixed, {result.covered} cover(s) written",
            fg=typer.colors.GREEN,
        )
    elif result.matched:
        typer.secho("      already in step with the discography", fg=typer.colors.BRIGHT_BLACK)
    else:
        typer.secho(
            "      nothing here could be matched to the discography", fg=typer.colors.YELLOW
        )

    if result.failures:
        typer.secho(f"      {len(result.failures)} operation(s) failed", fg=typer.colors.RED)
        echo_failures(result.failures)
    echo_notices(result.notices)


def _echo_summary(results: Sequence[Synced]) -> None:
    """Print the closing line for the whole run.

    Three outcomes, not two. An artist whose albums were all found and
    all already right has been synced as surely as one that changed --
    counting only the changes said "0 of 1 synced" for a run that did
    exactly what it was asked and found nothing left to do. An artist
    nothing could be matched for is the one that did not.

    Only the outcomes that happened are named, so the ordinary run reads
    as one clause rather than two zeroes.

    Args:
        results: What the run did for each artist.
    """
    changed: int = sum(1 for result in results if result.changed)
    in_step: int = sum(1 for result in results if not result.changed and result.matched)
    unmatched: int = sum(1 for result in results if not result.matched)
    failures: int = sum(len(result.failures) for result in results)

    parts: list[str] = []
    if changed:
        parts.append(f"{changed} artist(s) updated")
    if in_step:
        parts.append(f"{in_step} already in step")
    if unmatched:
        parts.append(f"{unmatched} with nothing matched")

    colour: str = typer.colors.YELLOW if unmatched and not changed else typer.colors.GREEN
    typer.secho(f"\nDone. {', '.join(parts)}.", fg=colour, bold=True)

    if failures:
        typer.secho(f"{failures} operation(s) failed.", fg=typer.colors.RED)
