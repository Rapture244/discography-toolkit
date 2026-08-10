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
total" some rippers append. A disc number is the one tag a track cannot
answer for alone -- whether its "1" means anything depends on whether a
"2" exists elsewhere in the album -- so the albums are read whole first.
One disc means the tag is cleared, saying so telling nobody anything;
several means the numbers stay, settled to their bare form, and go on
the front of each filename afterwards. That last pass runs at the end,
once the tags it reads from have been written.
"""

from __future__ import annotations

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
from discography_toolkit.cli.scope import (
    confirm_or_exit,
    require_albums,
    require_tracks,
    resolve_path,
)
from discography_toolkit.core import derivation
from discography_toolkit.core.layout import find_artist_folders, owning_folder
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import strip_artist_label, title_case, track_number
from discography_toolkit.operations import covers, discs, tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The text tags read off the folders, each with its display name and how
# its count reads. Genre is not here -- it cannot be derived -- and the
# cover is settled by its own pass, not written as a text tag.
_TAG_LABELS: Final[tuple[tuple[Tag, str, str], ...]] = (
    (Tag.ALBUM, "Album", "tagged"),
    (Tag.ALBUM_ARTIST, "Album Artist", "tagged"),
    (Tag.DATE, "Year", "dated"),
    (Tag.TITLE, "Title", "recased"),
    (Tag.TRACK, "Track", "numbered"),
    (Tag.DISC, "Disc", "settled"),
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
    target: Path = resolve_path(path, "Path to align tags beneath")
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
    confirm_or_exit(
        f"\nAlign tags beneath {target.name!r}? Settles covers, then album, artist, year, title."
    )

    typer.echo()
    cover_report, tag_report, disc_report = run(albums, tracks, artists, credit)
    _echo_summary(cover_report, tag_report, disc_report)


def run(
    albums: Sequence[Path],
    tracks: Sequence[Path],
    artists: Sequence[Path],
    credit: str | None = None,
) -> tuple[covers.CoverReport, tagging.WriteReport, discs.DiscReport]:
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
        What the cover pass, the tag pass and the disc prefixing each did.
    """
    cover_report: covers.CoverReport = _run_covers(albums, artists)
    tag_report, disc_report = _run_tags(tracks, albums, artists, credit)
    return cover_report, tag_report, disc_report


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _wants(
    albums: Sequence[Path],
    artists: Sequence[Path],
    credit: str | None,
    disc_plan: discs.DiscPlan,
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
        disc_plan: What the albums' disc numbers were found to say --
            the one tag a track cannot answer for alone, worked out per
            album beforehand.

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

        # Cleared where the album holds one disc, settled to its bare
        # form where it holds several. Never chosen: which disc a track
        # belongs to is the ripper's answer and the only record of it.
        if track in disc_plan.clear:
            wanted[Tag.DISC] = ""
        elif track in disc_plan.settle:
            wanted[Tag.DISC] = disc_plan.settle[track]

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
) -> tuple[tagging.WriteReport, discs.DiscReport]:
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
        What the tag pass and the disc prefixing each did.
    """
    with make_progress() as progress:
        advance = make_advancer(progress, "Discs: reading", tracks, artists)
        disc_plan = discs.plan(albums, tracks, on_progress=advance)

    with make_progress() as progress:
        advance = make_advancer(progress, "Tags: scanning", tracks, artists)
        plan = tagging.plan(
            tracks, _TEXT_TAGS, _wants(albums, artists, credit, disc_plan), on_progress=advance
        )

    breakdown: dict[Tag, int] = _breakdown(plan)

    with make_progress() as progress:
        pending: list[Path] = [outcome.path for outcome in plan.pending]
        advance = make_advancer(progress, "Tags: writing", pending, artists)
        report = tagging.apply(plan, on_progress=advance)

    _echo_breakdown(
        breakdown,
        report.failures,
        {Tag.TRACK: _unnumbered(plan), Tag.DISC: _disc_notices(disc_plan, artists)},
    )
    return report, _prefix_discs(disc_plan, artists)


def _prefix_discs(disc_plan: discs.DiscPlan, artists: Sequence[Path]) -> discs.DiscReport:
    """Put each multi-disc track's number at the front of its filename.

    Last, and after the tags: a flat folder sorted by name is the only
    view with nothing else to go on, and the number it wears has to be
    the settled one the tag pass has just written.

    Args:
        disc_plan: What the albums' disc numbers were found to say.
        artists: The artist folders, for the per-artist sub-bars.

    Returns:
        What the prefixing did.
    """
    if not disc_plan.pending:
        return discs.DiscReport()

    with make_progress() as progress:
        renaming: list[Path] = [prefix.track for prefix in disc_plan.pending]
        advance = make_advancer(progress, "Discs: prefixing", renaming, artists)
        report = discs.apply(disc_plan, on_progress=advance)

    echo_result("Disc names", report.renamed, "prefixed", failures=report.failures)
    return report


def _disc_notices(disc_plan: discs.DiscPlan, artists: Sequence[Path]) -> tuple[Notice, ...]:
    """Phrase what the disc pass saw but would not settle.

    An album holding several discs keeps its numbers, and is named --
    they are all that keeps its discs apart once the folders that used to
    are gone, so it is worth seeing that they are still there and
    plausible.

    Named with its artist, since a run over a shelf of thirteen reports
    two albums out of a hundred and forty and an album title alone says
    nothing about where to go and look.

    Args:
        disc_plan: What the albums' disc numbers were found to say.
        artists: The artist folders in scope, for naming which one holds
            each album.

    Returns:
        One notice per kind there was, empty when there was none.
    """
    notices: list[Notice] = []
    if disc_plan.split:
        notices.append(
            Notice(
                summary=f"{len(disc_plan.split)} album(s) hold more than one disc",
                details=tuple(
                    f"{_artist_of(album, artists)}: {album.name!r} -- discs {', '.join(found)}"
                    for album, found in disc_plan.split
                ),
            )
        )
    if disc_plan.unreadable:
        notices.append(
            Notice(
                summary=f"{len(disc_plan.unreadable)} file(s) carry no usable disc number",
                details=tuple(f"{str(track)!r}" for track in disc_plan.unreadable),
            )
        )
    return tuple(notices)


def _artist_of(album: Path, artists: Sequence[Path]) -> str:
    """Name the artist holding an album, for a line that reports on it.

    Args:
        album: The album folder.
        artists: The artist folders in scope.

    Returns:
        The artist's name without its count label, or the album's parent
        folder when no artist in scope holds it.
    """
    folder: Path | None = owning_folder(album, artists)
    if folder is None:
        return album.parent.name
    return strip_artist_label(folder.name) or folder.name


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
        disc_plan = discs.plan(albums, tracks, on_progress=advance)

    with make_progress() as progress:
        advance = make_advancer(progress, "Tags: scanning", tracks, artists)
        plan = tagging.plan(
            tracks, _TEXT_TAGS, _wants(albums, artists, credit, disc_plan), on_progress=advance
        )

    breakdown: dict[Tag, int] = _breakdown(plan)
    beneath: dict[Tag, Sequence[Notice]] = {
        Tag.TRACK: _unnumbered(plan),
        Tag.DISC: _disc_notices(disc_plan, artists),
    }
    for tag, name, _verb in _TAG_LABELS:
        echo_result(name, breakdown[tag], "to write", beneath.get(tag, ()))

    echo_result("Disc names", len(disc_plan.pending), "to prefix")


def _echo_summary(
    cover_report: covers.CoverReport,
    tag_report: tagging.WriteReport,
    disc_report: discs.DiscReport,
) -> None:
    """Print the closing totals across every pass.

    Only what happened is named. A run over a settled shelf says so in
    one clause rather than three zeroes, and a run that renamed forty
    files no longer reports itself as having changed nothing.

    Args:
        cover_report: What the cover pass did.
        tag_report: What the tag pass did.
        disc_report: What the disc prefixing did.
    """
    settled: int = cover_report.written + cover_report.renamed + cover_report.embedded
    failures: int = (
        len(cover_report.failures) + len(tag_report.failures) + len(disc_report.failures)
    )

    parts: list[str] = []
    if settled:
        parts.append(f"{settled} cover change(s) settled")
    if tag_report.written:
        parts.append(f"{tag_report.written} file(s) tagged")
    if disc_report.renamed:
        parts.append(f"{disc_report.renamed} file(s) prefixed with their disc")

    closing: str = ", ".join(parts) if parts else "nothing to change"
    typer.secho(f"\nDone. {closing}.", fg=typer.colors.GREEN, bold=True)

    if failures:
        typer.secho(f"{failures} operation(s) failed during writing.", fg=typer.colors.RED)
