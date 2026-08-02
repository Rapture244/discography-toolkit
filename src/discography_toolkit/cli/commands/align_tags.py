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
The covers settle first, on disk and into the files. Then the four text
tags are read and written together -- one open and one save per file for
all of them, since rewriting a large lossless file four times over to
set four fields would be four times the work for none of the gain -- and
the run reports how many files each tag touched.
"""

from __future__ import annotations

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Final

import typer

from discography_toolkit.cli.console import (
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.cli.scope import require_albums, require_tracks
from discography_toolkit.core import derivation
from discography_toolkit.core.layout import find_artist_folders
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import title_case
from discography_toolkit.operations import covers, tagging

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rich.progress import Progress, TaskID

# The text tags read off the folders, each with its display name and how
# its count reads. Genre is not here -- it cannot be derived -- and the
# cover is settled by its own pass, not written as a text tag.
_TAG_LABELS: Final[tuple[tuple[Tag, str, str], ...]] = (
    (Tag.ALBUM, "Album", "tagged"),
    (Tag.ALBUM_ARTIST, "Album Artist", "tagged"),
    (Tag.DATE, "Year", "dated"),
    (Tag.TITLE, "Title", "recased"),
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
    albums: Sequence[Path], artists: Sequence[Path], credit: str | None = None
) -> tagging.Desired:
    """Build the value function reading all four text tags off the folders.

    Each tag comes from `core.derivation`, which is the single reading of
    it -- the same one the matching `tags` sub-command uses, so the two
    doors onto the same work cannot disagree. The Title is the exception,
    recased from the tag already there rather than read off a folder.

    A tag with nothing to derive is left out, which leaves that field
    untouched.

    A `credit` overrides the Album Artist for every track, whatever folder
    it sits under -- the one way a track's Album Artist is not read from
    its own folder, so a discography split across collection folders can
    still be credited to the one artist above them.

    Args:
        albums: The album folders in scope.
        artists: The artist folders in scope.
        credit: An Album Artist to force, or `None` to derive it.

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
        plan = covers.plan(albums, on_progress=_bar(progress, "Covers: scanning", len(albums)))
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
        advance = make_advancer(progress, "Tags: scanning", tracks, artists)
        plan = tagging.plan(
            tracks, _TEXT_TAGS, _wants(albums, artists, credit), on_progress=advance
        )

    breakdown: dict[Tag, int] = _breakdown(plan)

    with make_progress() as progress:
        pending: list[Path] = [outcome.path for outcome in plan.pending]
        advance = make_advancer(progress, "Tags: writing", pending, artists)
        report = tagging.apply(plan, on_progress=advance)

    _echo_breakdown(breakdown, report.failures)
    return report


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
def _bar(progress: Progress, label: str, total: int) -> Callable[[Path], None]:
    """Build a one-bar callback, for a pass with no per-artist breakdown.

    Args:
        progress: The live progress display.
        label: The bar's description.
        total: How many advances fill it.

    Returns:
        A callback advancing the bar once per item.
    """
    task: TaskID = progress.add_task(label, total=total)

    def advance(_item: Path) -> None:
        progress.advance(task)

    return advance


def _echo_breakdown(breakdown: Mapping[Tag, int], failures: Sequence[tuple[Path, str]]) -> None:
    """Print one line per tag, from the combined plan's counts.

    Args:
        breakdown: Each text tag mapped to the files it changes.
        failures: `(path, reason)` for any write that failed.
    """
    for tag, name, verb in _TAG_LABELS:
        echo_result(name, breakdown[tag], verb)
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
            albums, on_progress=_bar(progress, "Covers: scanning", len(albums))
        )
    echo_result("Covers", cover_plan.changes, "to settle")

    with make_progress() as progress:
        advance = make_advancer(progress, "Tags: scanning", tracks, artists)
        plan = tagging.plan(
            tracks, _TEXT_TAGS, _wants(albums, artists, credit), on_progress=advance
        )

    breakdown: dict[Tag, int] = _breakdown(plan)
    for tag, name, _verb in _TAG_LABELS:
        echo_result(name, breakdown[tag], "to write")


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
