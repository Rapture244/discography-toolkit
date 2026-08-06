# src/discography_toolkit/cli/commands/align_tags.py
"""The `rapt align-tags` command.

Where `rapt layout` settles the folders and filenames, this settles the
tags to match them. Once the layout has run, the structure is the
canonical form of the collection, and every tag but one can be read
straight off it: the Album from the album folder, the Album Artist from
the artist folder, the Date from the year in the album's name, the cover
from the image beside the tracks. The Title is the exception it recases
in place rather than inventing, the same claim the title command makes.

Genre is the one tag left out. Nothing in the folders says what a record
sounds like, so genre stays a separate, deliberate `rapt tags genre`
with a value given by hand.

Covers and tags are different work, so they run as two visible passes.
The covers settle first, on disk and into the files. Then the text tags
are read and written together -- one open and one save per file for all
of them, since rewriting a large lossless file six times over to set six
fields would be six times the work for none of the gain -- and the run
reports how many files each tag touched.

Two of those tags are settled in place rather than derived. A track
number is padded to the collection's width and stripped of the "of
total" some rippers append. A disc number is cleared, since one disc is
the ordinary case and saying so tells nobody anything -- but that is the
one tag a track cannot answer for alone, whether its "1" means anything
depending on whether a "2" exists elsewhere in the album. So the albums
are read whole first, and any holding more than one disc is left exactly
as it is and reported.
"""

from __future__ import annotations

from collections import defaultdict

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Final

import typer

from discography_toolkit.cli.console import (
    Notice,
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_bar,
    make_progress,
)
from discography_toolkit.cli.scope import require_albums, require_tracks, resolve_path
from discography_toolkit.core import derivation, metadata
from discography_toolkit.core.layout import find_artist_folders, holding_album
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import title_case, track_number
from discography_toolkit.operations import covers, tagging

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Mapping, Sequence

# The text tags read off the folders, each with its display name and how
# its count reads. Genre is not here -- it cannot be derived -- and the
# cover is settled by its own pass, not written as a text tag.
_TAG_LABELS: Final[tuple[tuple[Tag, str, str], ...]] = (
    (Tag.ALBUM, "Album", "tagged"),
    (Tag.ALBUM_ARTIST, "Album Artist", "tagged"),
    (Tag.DATE, "Year", "dated"),
    (Tag.TITLE, "Title", "recased"),
    (Tag.TRACK, "Track", "numbered"),
    (Tag.DISC, "Disc", "cleared"),
)
_TEXT_TAGS: Final[tuple[Tag, ...]] = tuple(tag for tag, _, _ in _TAG_LABELS)


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def align_tags(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to align beneath. An artist, or a shelf holding several.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without writing."),
    ] = False,
    credit: Annotated[
        str | None,
        typer.Option(
            "--album-artist",
            help="Force this Album Artist on every track, in place of the folder's name.",
        ),
    ] = None,
) -> None:
    """Write every folder-derived tag -- covers, album, artist, year, title.

    Args:
        path: Folder to align beneath; prompted for if omitted.
        dry_run: Report what would change without writing.
        credit: An Album Artist to force on every track; derived per
            folder when omitted.

    Raises:
        typer.Exit: On an invalid path, no album found, no audio found, a
            user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to align beneath")
    # Not `scope.artists_in`: the Album Artist is read off these folders
    # rather than only shown, and that one returns nothing for a target
    # that is itself an artist.
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Align Tags", target.name, children=artist_names(target, artists))

    albums: list[Path] = require_albums(target)
    tracks: list[Path] = require_tracks(target)

    if dry_run:
        _preview(albums, tracks, artists, credit)
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if credit is not None:
        typer.secho(
            f"\nForcing Album Artist = {credit!r} on every track under {target.name!r}.",
            fg=typer.colors.YELLOW,
        )
    if not typer.confirm(
        f"\nAlign tags beneath {target.name!r}? Settles covers, then album, artist, year, title."
    ):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()
    cover_report, tag_report = run(albums, tracks, artists, credit)
    _echo_summary(cover_report, tag_report)


def run(
    albums: Sequence[Path],
    tracks: Sequence[Path],
    artists: Sequence[Path],
    credit: str | None = None,
) -> tuple[covers.CoverReport, tagging.WriteReport]:
    """Settle the covers, then write the tags, each pass in sight.

    The work the command runs after confirming, lifted out so the
    organize command can drive it too without a second confirmation.

    Args:
        albums: The album folders in scope.
        tracks: Every audio file in scope.
        artists: The artist folders in scope.
        credit: An Album Artist to force on every track, in place of the
            one read from the folder above it; `None` derives it as usual.

    Returns:
        What the cover pass and the tag pass each did.
    """
    cover_report: covers.CoverReport = _run_covers(albums, artists)
    tag_report: tagging.WriteReport = _run_tags(tracks, albums, artists, credit)
    return cover_report, tag_report


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _wants(
    albums: Sequence[Path],
    artists: Sequence[Path],
    credit: str | None,
    clear_disc: Container[Path],
) -> tagging.Desired:
    """Build the value function for every text tag written in one pass.

    The three read off the folders come from `core.derivation`, which is
    the single reading of each -- the same one the matching `tags`
    sub-command uses, so the two doors onto the same work cannot
    disagree.

    Three are settled in place instead, from what the file already holds:
    the Title recased, the track number padded, the disc number cleared
    where the album has only one. A tag with nothing to derive or nothing
    to settle is left out, which leaves that field untouched.

    A `credit` overrides the Album Artist for every track, whatever folder
    it sits under -- the one way a track's Album Artist is not read from
    its own folder, so a discography split across collection folders can
    still be credited to the one artist above them.

    Args:
        albums: The album folders in scope.
        artists: The artist folders in scope.
        credit: An Album Artist to force, or `None` to derive it.
        clear_disc: Tracks whose disc number should be emptied, worked
            out per album beforehand -- it is the one tag a track cannot
            answer for alone.

    Returns:
        A `Desired` callable for the combined tagging pass.
    """

    def desired(track: Path, current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        wanted: dict[Tag, str] = {}

        album: str | None = derivation.album_of(track, albums)
        if album is not None:
            wanted[Tag.ALBUM] = album

        date: str | None = derivation.date_of(track, albums)
        if date is not None:
            wanted[Tag.DATE] = date

        if credit is not None:
            wanted[Tag.ALBUM_ARTIST] = credit
        else:
            artist: str | None = derivation.album_artist_of(track, artists)
            if artist is not None:
                wanted[Tag.ALBUM_ARTIST] = artist

        # Recased in place, exactly as the title command does it: an
        # absent title cases to nothing, compares equal, and is left be.
        wanted[Tag.TITLE] = title_case(current.get(Tag.TITLE, ""))

        # Settled in place too, and left alone where it cannot be: a tag
        # holding something that is not a number is a person's to look
        # at, and guessing one would be inventing a running order.
        number: str | None = track_number(current.get(Tag.TRACK, ""))
        if number is not None:
            wanted[Tag.TRACK] = number

        if track in clear_disc:
            wanted[Tag.DISC] = ""

        return wanted

    return desired


# ==================================================================================== #
#                                       PASSES                                         #
# ==================================================================================== #
def _run_covers(albums: Sequence[Path], artists: Sequence[Path]) -> covers.CoverReport:
    """Settle the covers, showing a bar for the scan and one for the work.

    Args:
        albums: The album folders in scope.
        artists: The artist folders, for the per-artist sub-bars.

    Returns:
        What the cover pass did.
    """
    with make_progress(noun="albums") as progress:
        plan = covers.plan(albums, on_progress=make_bar(progress, "Covers: scanning", len(albums)))
    with make_progress() as progress:
        # Settling embeds the cover into every track -- the heaviest pass,
        # a full rewrite per file -- so it settles behind a per-artist
        # breakdown like the tag passes, not one flat bar that barely
        # moves across hundreds of files.
        advance = make_advancer(progress, "Covers: settling", plan.touched, artists)
        report = covers.apply(plan, on_progress=advance)

    settled: int = report.written + report.renamed + report.embedded
    echo_result("Covers", settled, "settled", failures=report.failures)
    return report


def _run_tags(
    tracks: Sequence[Path],
    albums: Sequence[Path],
    artists: Sequence[Path],
    credit: str | None = None,
) -> tagging.WriteReport:
    """Read all four tags in one scan and write them in one save per file.

    Rewriting a lossless file once for four fields rather than four times
    for one each is the whole reason the tags share a pass. The per-tag
    counts come out of the plan afterward, so the run still says how many
    files each field touched.

    Args:
        tracks: Every audio file in scope.
        albums: The album folders in scope.
        artists: The artist folders in scope.
        credit: An Album Artist to force on every track, or `None`.

    Returns:
        What the pass did, counted in files written.
    """
    with make_progress() as progress:
        advance = make_advancer(progress, "Discs: reading", tracks, artists)
        clear_disc, split = _disc_decisions(albums, tracks, advance)

    with make_progress() as progress:
        advance = make_advancer(progress, "Tags: scanning", tracks, artists)
        plan = tagging.plan(
            tracks, _TEXT_TAGS, _wants(albums, artists, credit, clear_disc), on_progress=advance
        )

    breakdown: dict[Tag, int] = _breakdown(plan)

    with make_progress() as progress:
        pending: list[Path] = [outcome.path for outcome in plan.pending]
        advance = make_advancer(progress, "Tags: writing", pending, artists)
        report = tagging.apply(plan, on_progress=advance)

    _echo_breakdown(
        breakdown,
        report.failures,
        {Tag.TRACK: _unnumbered(plan), Tag.DISC: split},
    )
    return report


def _disc_decisions(
    albums: Sequence[Path],
    tracks: Sequence[Path],
    on_progress: Callable[[Path], None] | None = None,
) -> tuple[set[Path], tuple[Notice, ...]]:
    """Decide, per album, which tracks should lose their disc number.

    The one tag a track cannot answer for alone. Whether its "1" means
    anything depends on whether a "2" exists elsewhere in the album, so
    the album is read whole before a single value is settled -- which is
    why this runs as its own pass ahead of the others rather than inside
    the value function.

    An album carrying at most one distinct number has it cleared from
    every track: one disc is the ordinary case, and saying so in a tag
    tells nobody anything. "1 everywhere" and "1 on some, blank on the
    rest" are the same album and are treated alike, which is what keeps
    the second from being left half-tagged forever.

    An album carrying two or more is left entirely alone and reported.
    Those numbers are the only thing keeping its discs apart once the
    folders that used to are gone.

    Args:
        albums: The album folders in scope.
        tracks: Every audio file in scope.
        on_progress: Called with each track as it is read.

    Returns:
        The tracks to clear, and a notice naming the albums left alone.
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
    split: list[str] = []

    for album, entries in by_album.items():
        found: set[str] = {value for _, value in entries if value}
        if len(found) > 1:
            split.append(f"{album.name!r} -- discs {', '.join(sorted(found))}")
            continue
        clear.update(track for track, value in entries if value)

    if not split:
        return clear, ()

    return clear, (
        Notice(
            summary=f"{len(split)} album(s) hold more than one disc -- left as they are",
            details=tuple(split),
        ),
    )


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


def _unnumbered(plan: tagging.TagPlan) -> tuple[Notice, ...]:
    """Name the tracks whose number could not be settled.

    They come out of the plan as already correct, nothing having been
    written to them, which is indistinguishable from a track that was
    already right until it is asked why. A file with no track number, or
    one holding something that is not a number, will sort wrong in every
    player and nothing else here will mention it.

    Args:
        plan: The combined tag plan.

    Returns:
        One notice when there were any, empty otherwise.
    """
    found: list[tagging.TrackOutcome] = [
        outcome
        for outcome in plan.outcomes
        if outcome.status == "already_correct"
        and track_number(outcome.current.get(Tag.TRACK, "")) is None
    ]
    if not found:
        return ()

    return (
        Notice(
            summary=f"{len(found)} file(s) carry no usable track number",
            details=tuple(f"{str(outcome.path)!r}" for outcome in found),
        ),
    )


def _breakdown(plan: tagging.TagPlan) -> dict[Tag, int]:
    """Count how many files each tag will be written on.

    A file's outcome carries only the fields that actually change, so
    summing them per tag gives what each field touched -- their total is
    more than the file count, since one file can change several.

    Args:
        plan: The combined tag plan.

    Returns:
        Each text tag mapped to the number of files it changes.
    """
    counts: dict[Tag, int] = dict.fromkeys(_TEXT_TAGS, 0)
    for outcome in plan.pending:
        for tag in outcome.values:
            counts[tag] += 1
    return counts


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_breakdown(
    breakdown: Mapping[Tag, int],
    failures: Sequence[tuple[Path, str]],
    notices: Mapping[Tag, Sequence[Notice]] | None = None,
) -> None:
    """Print one line per tag, from the combined plan's counts.

    Args:
        breakdown: Each text tag mapped to the files it changes.
        failures: `(path, reason)` for any write that failed.
        notices: What each pass saw but would not act on, shown under
            its own line rather than at the run's foot.
    """
    beneath: Mapping[Tag, Sequence[Notice]] = notices or {}
    for tag, name, verb in _TAG_LABELS:
        echo_result(name, breakdown[tag], verb, beneath.get(tag, ()))
    for failed, detail in failures:
        typer.secho(f"      {str(failed)!r} - {detail}", fg=typer.colors.RED)


def _preview(
    albums: Sequence[Path],
    tracks: Sequence[Path],
    artists: Sequence[Path],
    credit: str | None = None,
) -> None:
    """Plan both passes and report their counts, without writing.

    Args:
        albums: The album folders in scope.
        tracks: Every audio file in scope.
        artists: The artist folders in scope.
        credit: An Album Artist to force on every track, or `None`.
    """
    typer.echo()
    with make_progress(noun="albums") as progress:
        cover_plan = covers.plan(
            albums, on_progress=make_bar(progress, "Covers: scanning", len(albums))
        )
    echo_result("Covers", cover_plan.changes, "to settle")

    with make_progress() as progress:
        advance = make_advancer(progress, "Discs: reading", tracks, artists)
        clear_disc, split = _disc_decisions(albums, tracks, advance)

    with make_progress() as progress:
        advance = make_advancer(progress, "Tags: scanning", tracks, artists)
        plan = tagging.plan(
            tracks, _TEXT_TAGS, _wants(albums, artists, credit, clear_disc), on_progress=advance
        )

    breakdown: dict[Tag, int] = _breakdown(plan)
    beneath: dict[Tag, Sequence[Notice]] = {Tag.TRACK: _unnumbered(plan), Tag.DISC: split}
    for tag, name, _verb in _TAG_LABELS:
        echo_result(name, breakdown[tag], "to write", beneath.get(tag, ()))


def _echo_summary(cover_report: covers.CoverReport, tag_report: tagging.WriteReport) -> None:
    """Print the closing totals across both passes.

    Args:
        cover_report: What the cover pass did.
        tag_report: What the tag pass did.
    """
    settled: int = cover_report.written + cover_report.renamed + cover_report.embedded
    failures: int = len(cover_report.failures) + len(tag_report.failures)

    typer.secho(
        f"\nDone. {settled} cover change(s) settled, {tag_report.written} file(s) tagged.",
        fg=typer.colors.GREEN,
        bold=True,
    )
    if failures:
        typer.secho(f"{failures} operation(s) failed during writing.", fg=typer.colors.RED)
