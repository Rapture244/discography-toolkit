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
FLAC album arrives here as Opus. The genre is the one thing copied that
no folder name holds, so it comes from the discography's own tracks --
retag it there and the next run brings it across.

That is what makes a second run a sync: rename or renumber an album in
the discography, run again, and the playlist follows -- and unpin one
there and the pin comes off here.

Three passes per artist, in an order that cannot change: fold, then
write the tags off the folder the fold has just named, then write
a cover.jpg from the art the tracks carry. That order is why there is no
dry run -- a preview of the tag pass would describe folders that do not
exist yet -- so the fold alone is shown before a single confirmation, as
`layout` and `organize` have.
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
from discography_toolkit.cli.scope import resolve_path
from discography_toolkit.core.layout import (
    album_tracks,
    discover_albums,
    find_artist_folders,
    holding_album,
)
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import album_title, strip_artist_label
from discography_toolkit.operations import playlist as folding, tagging

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
        tagged: How many tracks had their Album tag written.
        covered: How many loose covers were written.
        notices: What the run saw but would not act on.
        failures: `(path, reason)` for each operation that failed.
    """

    name: str
    matched: int = 0
    folded: int = 0
    tagged: int = 0
    covered: int = 0
    notices: tuple[Notice, ...] = ()
    failures: tuple[tuple[Path, str], ...] = field(default=())

    @property
    def changed(self) -> bool:
        """Whether anything about this artist actually moved."""
        return bool(self.folded or self.tagged or self.covered)


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
    disco: Path = resolve_path(path, "Enter the absolute path to the discography")
    target: Path = resolve_path(converted, "Enter the absolute path to the playlist")

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

    artists, skipped = _gather(roster, disco, target)
    # One column for the whole run: the roster below, and the progress
    # bar labels after it, so every bar starts where the names ended.
    width: int = max(cell_len(artist.name) for artist in artists)
    _echo_found(artists, skipped, width)

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

    warning: str = (
        f"This moves {moves} album folder(s), then writes their Album tag, their Genre "
        "from the discography, and a cover.jpg beside their tracks. There is no dry run."
    )
    typer.secho(f"\n{warning}", fg=typer.colors.YELLOW)
    if not typer.confirm("Proceed?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

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
def _gather(roster: Sequence[Path], disco: Path, target: Path) -> tuple[list[Artist], list[Path]]:
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
        One entry per artist, in roster order, and the folders in the
        playlist the run has nothing to say about -- an artist the
        discography does not know, or something that is neither.
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
    owners: dict[Path, str] = _assign(loose, titles)

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

    return artists, [*skipped, *strangers]


def _assign(loose: Sequence[Path], titles: Mapping[str, set[str]]) -> dict[Path, str]:
    """Decide which artist each loose album folder belongs to.

    Read from the folder's own Album tag rather than its name, as every
    other match here is. A folder no artist claims, or one that two do,
    is left unassigned and reported by the pass that would have placed
    it.

    Args:
        loose: Album folders sitting in the playlist path itself.
        titles: Each artist's album titles, casefolded.

    Returns:
        Each assignable folder mapped to its artist's name.
    """
    owners: dict[Path, str] = {}
    for album in loose:
        title: str | None = folding.identity(album)
        if title is None:
            continue
        claimants: list[str] = [name for name, held in titles.items() if title in held]
        if len(claimants) == 1:
            owners[album] = claimants[0]
    return owners


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

    tagged, tag_notices, tag_failures = _write_tags(placed, advance)
    covered, art_notices, art_failures = _write_covers(
        sorted({match.target for match in placed}), advance
    )

    return Synced(
        name=artist.name,
        matched=len(fold_plan.matches),
        folded=report.moved,
        tagged=tagged,
        covered=covered,
        notices=(*_fold_notices(fold_plan), *tag_notices, *art_notices),
        failures=(*report.failures, *tag_failures, *art_failures),
    )


def _write_tags(
    placed: Sequence[folding.Match],
    advance: Callable[[Path], None],
) -> tuple[int, tuple[Notice, ...], tuple[tuple[Path, str], ...]]:
    """Write the Album tag and the Genre of every track in one pass.

    Two values from two sources, written together because they are one
    write. The Album comes off the folder the fold has just named; the
    Genre has no folder to come from and is read out of the discography's
    own tracks, which is what lets a genre changed there reach the
    playlist on the next run.

    Only folders that are actually on disk are read, since one of the
    fold's renames may have failed and what is there is what the tags are
    written from.

    A discography album carrying no genre leaves the playlist's alone
    rather than clearing it: nothing to copy is not the same as an
    instruction to empty the field.

    The genre is read from the discography's tracks rather than from the
    `.genre` files that decide it there. A declaration is what the
    discography was told; a tag is what it holds, and the playlist
    mirrors what it holds. Reading declarations would put the playlist
    ahead of its own source -- a genre on the phone that the discography
    has not been given yet -- so the order stays: declare, run `tags
    genre`, then sync.

    Args:
        placed: The matches whose folders are in place.
        advance: The run's progress callback, called per track read.

    Returns:
        How many tracks were written, what had no genre to copy, and
        what failed.
    """
    wanted: dict[Path, dict[Tag, str]] = {}
    ungenred: list[Path] = []

    for match in placed:
        values: dict[Tag, str] = {Tag.ALBUM: folding.album_tag(match.target.name)}
        genre: str = folding.genre_of(match.source)
        if genre:
            values[Tag.GENRE] = genre
        else:
            ungenred.append(match.target)
        wanted[match.target] = values

    tracks: list[Path] = [track for album in wanted for track in album_tracks(album)]
    if not tracks:
        return 0, (), ()

    # Barred on the read rather than the write: opening every track to
    # see what it already holds is where the time goes, and it is the
    # pause a run would otherwise sit through in silence.
    plan = tagging.plan(tracks, [Tag.ALBUM, Tag.GENRE], _wants(wanted), on_progress=advance)
    report = tagging.apply(plan)

    notices: tuple[Notice, ...] = ()
    if ungenred:
        notices = (
            Notice(
                summary=(
                    f"{len(ungenred)} album(s) carry no Genre in the discography "
                    "-- run `rapt tags genre` there first"
                ),
                details=tuple(f"{album.name!r}" for album in ungenred),
            ),
        )
    return report.written, notices, report.failures


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


def _wants(wanted: Mapping[Path, Mapping[Tag, str]]) -> tagging.Desired:
    """Build the value function: each track given what its album should hold.

    A track must be in the album, or one disc down. `owning_folder` would
    accept any ancestor however distant, which is how one wrongly matched
    folder came to stamp its name on every track beneath it. The limit
    belongs here as well as at discovery, because this is where the value
    is decided: a wrong match should cost a folder move, which is undone
    by moving it back, and never the tags, which are not.

    Args:
        wanted: Each album folder mapped to the tags it should write.

    Returns:
        A `Desired` callable returning nothing for a track no album
        directly holds, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        album: Path | None = holding_album(track, wanted)
        return {} if album is None else wanted[album]

    return desired


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


def _echo_found(artists: Sequence[Artist], skipped: Sequence[Path], width: int) -> None:
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
        counts: str = (
            f"{result.folded} folded, {result.tagged} tagged, {result.covered} cover(s) written"
        )
        typer.secho(f"      {counts}", fg=typer.colors.GREEN)
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
