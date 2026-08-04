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
FLAC album arrives here as Opus. That is what makes a second run a sync:
rename or renumber an album in the discography, run again, and the
playlist follows -- and unpin one there and the pin comes off here.

Three passes per artist, in an order that cannot change: fold, then
write the Album tag off the folder the fold has just named, then write
a cover.jpg from the art the tracks carry. That order is why there is no
dry run -- a preview of the tag pass would describe folders that do not
exist yet -- so there is one confirmation for the whole run instead, as
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
    discover_albums,
    find_artist_folders,
    find_audio_files,
    owning_folder,
)
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import album_title, strip_artist_label
from discography_toolkit.operations import playlist as folding, tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Artist:
    """One artist as both sides see them.

    Attributes:
        name: The discography folder's name without its count label.
        albums: The albums the discography holds for them.
        homes: The playlist folders named for them, empty when the
            playlist holds none of their work yet.
        candidates: `(folder, destination)` for every album folder this
            run should try to place.
    """

    name: str
    albums: tuple[Path, ...]
    homes: tuple[Path, ...]
    candidates: tuple[tuple[Path, Path], ...]


@dataclass(frozen=True, slots=True)
class Synced:
    """What a run did for one artist.

    Attributes:
        name: The artist's name.
        folded: How many album folders were moved or renamed.
        tagged: How many tracks had their Album tag written.
        covered: How many loose covers were written.
        notices: What the run saw but would not act on.
        failures: `(path, reason)` for each operation that failed.
    """

    name: str
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

    artists: list[Artist] = _gather(roster, target)
    _echo_found(artists)

    workable: list[Artist] = [artist for artist in artists if artist.candidates]
    wanted: int = sum(len(artist.candidates) for artist in workable)
    if not wanted:
        typer.secho(f"\nNothing of these artists found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    warning: str = (
        f"This syncs {wanted} album folder(s) across {len(workable)} artist(s) against the "
        "discography, writing their Album tag and a cover.jpg beside their tracks. "
        "There is no dry run."
    )
    typer.secho(f"\n{warning}", fg=typer.colors.YELLOW)
    if not typer.confirm("Proceed?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()
    results: list[Synced] = []
    for artist in workable:
        result: Synced = _sync(artist)
        results.append(result)
        _echo_artist(result)

    _echo_summary(results)


# ==================================================================================== #
#                                      GATHERING                                       #
# ==================================================================================== #
def _gather(roster: Sequence[Path], target: Path) -> list[Artist]:
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
        target: The playlist path to search.

    Returns:
        One entry per artist, in roster order.
    """
    names: dict[Path, str] = {}
    for folder in roster:
        label: str | None = strip_artist_label(folder.name)
        if label is not None:
            names[folder] = label

    homes: dict[str, list[Path]] = folding.find_homes(target, set(names.values()))
    settled: list[Path] = [home for found in homes.values() for home in found]
    loose: list[Path] = folding.loose_albums(target, settled)

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
                albums=albums[name],
                homes=tuple(found),
                candidates=tuple(candidates),
            )
        )

    return artists


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
def _sync(artist: Artist) -> Synced:
    """Fold, tag and cover one artist's albums.

    In that order and only that order: the Album tag is read off the
    folder name the fold has just written, and the cover off the tracks
    once they are where they belong.

    Args:
        artist: The artist to sync.

    Returns:
        What the three passes did, and what they could not.
    """
    with make_progress(noun="albums") as progress:
        fold_plan = folding.plan(
            artist.candidates,
            artist.albums,
            on_progress=make_bar(progress, f"Playlist: {artist.name}", len(artist.candidates)),
        )

    report = folding.apply(fold_plan)

    # Re-read rather than trust the plan: the fold has just renamed these
    # and one of the renames may have failed, so what is on disk is what
    # the tags and covers are written from.
    settled: list[Path] = sorted(
        {match.target for match in fold_plan.matches if match.target.is_dir()}
    )

    tagged, tag_failures = _write_tags(settled)
    covered, art_notices, art_failures = _write_covers(settled)

    return Synced(
        name=artist.name,
        folded=report.moved,
        tagged=tagged,
        covered=covered,
        notices=(*_fold_notices(fold_plan), *art_notices),
        failures=(*report.failures, *tag_failures, *art_failures),
    )


def _write_tags(settled: Sequence[Path]) -> tuple[int, tuple[tuple[Path, str], ...]]:
    """Write the Album tag of every track, read off its settled folder.

    Args:
        settled: The artist's album folders, as they now stand.

    Returns:
        How many tracks were written, and what failed.
    """
    tracks: list[Path] = [track for album in settled for track in find_audio_files(album)]
    if not tracks:
        return 0, ()

    plan = tagging.plan(tracks, [Tag.ALBUM], _wants(settled))
    report = tagging.apply(plan)
    return report.written, report.failures


def _write_covers(
    settled: Sequence[Path],
) -> tuple[int, tuple[Notice, ...], tuple[tuple[Path, str], ...]]:
    """Write one cover.jpg per album, from the art its tracks carry.

    Args:
        settled: The artist's album folders, as they now stand.

    Returns:
        How many covers were written, what had no art, and what failed.
    """
    if not settled:
        return 0, (), ()

    plan = folding.plan_covers(settled)
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


def _wants(albums: Sequence[Path]) -> tagging.Desired:
    """Build the value function: each track named for its settled folder.

    Args:
        albums: The playlist's album folders.

    Returns:
        A `Desired` callable returning nothing for a track under none of
        them, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        folder: Path | None = owning_folder(track, albums)
        return {} if folder is None else {Tag.ALBUM: folding.album_tag(folder.name)}

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


def _echo_found(artists: Sequence[Artist]) -> None:
    """Print what the roster turned up, before anything is agreed to.

    Every artist the discography path covers, and where each stands in
    the playlist. An artist with nothing there is named too, dimmed: with
    eight of them it is the difference between "you have not converted
    these yet" and "you pointed at the wrong playlist", and a run that
    listed only the ones it found would look identical either way.

    Args:
        artists: Every artist in the roster, with what was found for
            them.
    """
    typer.echo()
    # Padded by cell width, not character count: a CJK name is half as
    # many characters as it is columns wide, and "{:<40}" would step the
    # column left by one for every one of them. The width comes from the
    # longest name rather than a fixed number, so nothing ever overruns.
    width: int = max(cell_len(artist.name) for artist in artists)

    for artist in artists:
        name: str = set_cell_size(artist.name, width)
        if not artist.candidates:
            typer.secho(
                f"  {name}  nothing found in the playlist",
                fg=typer.colors.BRIGHT_BLACK,
            )
            continue

        where: str = (
            f"in {len(artist.homes)} folder(s)"
            if len(artist.homes) > 1
            else ("to file" if not artist.homes else "to sync")
        )
        typer.secho(
            f"  {name}  {len(artist.candidates)} album(s) {where}",
            fg=typer.colors.CYAN,
        )


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
    else:
        typer.secho("      already in step with the discography", fg=typer.colors.BRIGHT_BLACK)

    if result.failures:
        typer.secho(f"      {len(result.failures)} operation(s) failed", fg=typer.colors.RED)
        echo_failures(result.failures)
    echo_notices(result.notices)


def _echo_summary(results: Sequence[Synced]) -> None:
    """Print the closing line for the whole run.

    Args:
        results: What the run did for each artist.
    """
    changed: int = sum(1 for result in results if result.changed)
    failures: int = sum(len(result.failures) for result in results)

    typer.secho(
        f"\nDone. {changed} of {len(results)} artist(s) synced.",
        fg=typer.colors.GREEN,
        bold=True,
    )
    if failures:
        typer.secho(f"{failures} operation(s) failed.", fg=typer.colors.RED)
