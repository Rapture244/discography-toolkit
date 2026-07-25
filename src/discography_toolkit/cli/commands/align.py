# src/discography_toolkit/cli/commands/align.py
"""The `rapt align` command.

Where `rapt layout` settles the folders and filenames, this settles the
tags to match them. Once the layout has run, the structure is the
canonical form of the collection, and every tag but one can be read
straight off it: the Album from the album folder, the Album Artist from
the artist folder, the Date from the year in the album's name, the cover
from the image beside the tracks. The Title is the exception it still
recases in place rather than inventing, the same claim the title command
makes on its own.

Genre is the one tag left out. Nothing in the folders says what a record
sounds like, so genre stays a separate, deliberate `rapt tags genre`
with a value given by hand.

The four text tags are written in a single pass -- one read and one save
per file for all of them -- and the covers are settled alongside. Both
plan before they touch anything, so `--dry-run` shows the whole of what
would change.
"""

from __future__ import annotations

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from discography_toolkit.cli.console import (
    SummaryRow,
    artist_names,
    echo_banner,
    echo_summary,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core.layout import (
    find_albums,
    find_artist_folders,
    find_audio_files,
    owning_folder,
)
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import (
    extract_year,
    is_approximate_year,
    strip_artist_label,
    title_case,
)
from discography_toolkit.operations import covers, tagging

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rich.progress import Progress, TaskID

# The tags read straight off the folders in one pass. Genre is not among
# them: it cannot be derived, and the cover is settled by its own
# operation, not written as a text tag.
_TEXT_TAGS: list[Tag] = [Tag.ALBUM, Tag.ALBUM_ARTIST, Tag.DATE, Tag.TITLE]


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def align(
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
) -> None:
    """Write every folder-derived tag -- album, artist, year, title, cover.

    Args:
        path: Folder to align beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no album found, no audio found, a
            user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to align beneath")
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Align", target.name, children=artist_names(target, artists))

    albums: list[Path] = find_albums(target)
    if not albums:
        typer.secho(
            f"\nNo album folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run the layout pass first -- align reads the tags off the folders it settles.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    tracks: list[Path] = find_audio_files(target)
    if not tracks:
        typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    with make_progress(noun="albums") as progress:
        advance = _album_advancer(progress, "Covers", albums)
        cover_plan = covers.plan(albums, on_progress=advance)
    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        tag_plan = tagging.plan(tracks, _TEXT_TAGS, _wants(albums, artists), on_progress=advance)

    _echo_plan(cover_plan, tag_plan)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not cover_plan.changes and not tag_plan.pending:
        typer.secho(
            "\nEvery file already agrees with its folders. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\n{_intent(cover_plan, tag_plan)}?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    cover_report = covers.apply(cover_plan)
    with make_progress() as progress:
        advance = make_advancer(
            progress, target.name, [outcome.path for outcome in tag_plan.pending], artists
        )
        tag_report = tagging.apply(tag_plan, on_progress=advance)

    _echo_report(cover_report, tag_report)


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _wants(albums: Sequence[Path], artists: Sequence[Path]) -> tagging.Desired:
    """Build the value function reading all four text tags off the folders.

    Each tag is derived exactly as its own command derives it: the Album
    from the album folder's name, the Album Artist from the artist
    folder's name without its label, the Date from the year in the album
    name -- cleared when approximate -- and the Title recased from the one
    already there. A tag with nothing to derive is left out, which leaves
    that field untouched.

    Args:
        albums: The album folders in scope.
        artists: The artist folders in scope.

    Returns:
        A `Desired` callable for the combined tagging pass.
    """

    def desired(track: Path, current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        wanted: dict[Tag, str] = {}

        album: Path | None = owning_folder(track, albums)
        if album is not None:
            wanted[Tag.ALBUM] = album.name
            token: str | None = extract_year(album.name)
            if token is not None:
                wanted[Tag.DATE] = "" if is_approximate_year(token) else token

        artist: Path | None = owning_folder(track, artists)
        if artist is not None:
            name: str | None = strip_artist_label(artist.name)
            if name is not None:
                wanted[Tag.ALBUM_ARTIST] = name

        # Recased in place, exactly as the title command does it: an
        # absent title cases to nothing, compares equal, and is left be.
        wanted[Tag.TITLE] = title_case(current.get(Tag.TITLE, ""))

        return wanted

    return desired


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _album_advancer(
    progress: Progress, label: str, albums: Sequence[Path]
) -> Callable[[Path], None]:
    """Build a simple one-bar callback over albums, for the cover scan.

    Args:
        progress: The live progress display.
        label: The bar's description.
        albums: The albums about to be examined, for sizing.

    Returns:
        A callback advancing the bar once per album.
    """
    task: TaskID = progress.add_task(label, total=len(albums))

    def advance(_album: Path) -> None:
        progress.advance(task)

    return advance


def _intent(cover_plan: covers.CoverPlan, tag_plan: tagging.TagPlan) -> str:
    """Phrase the confirmation from what there is to do.

    Args:
        cover_plan: The planned cover work.
        tag_plan: The planned tag work.

    Returns:
        A question naming the file counts, e.g. "Tag 40 file(s) and
        settle 3 cover(s)".
    """
    parts: list[str] = []
    if tag_plan.pending:
        parts.append(f"tag {len(tag_plan.pending)} file(s)")
    if cover_plan.changes:
        parts.append(f"settle {cover_plan.changes} cover change(s)")
    return "Align: " + " and ".join(parts)


def _echo_plan(cover_plan: covers.CoverPlan, tag_plan: tagging.TagPlan) -> None:
    """Render both plans as one summary box.

    Args:
        cover_plan: The planned cover work.
        tag_plan: The planned tag work.
    """
    counts: list[list[SummaryRow]] = [
        [SummaryRow(label="Files", count=tag_plan.total, indent="", percent=False)],
        [
            SummaryRow(
                label="Tagged", count=len(tag_plan.pending), marker="(->)", color=typer.colors.GREEN
            ),
            SummaryRow(label="Clean", count=tag_plan.clean, marker="(==)", color=typer.colors.BLUE),
        ],
    ]
    if tag_plan.errors:
        counts[1].append(
            SummaryRow(
                label="Errors", count=len(tag_plan.errors), marker="(!!)", color=typer.colors.RED
            )
        )

    cover_rows: list[SummaryRow] = [
        SummaryRow(label="Cover writes", count=cover_plan.writes, color=typer.colors.GREEN),
        SummaryRow(label="Cover embeds", count=cover_plan.embeds, color=typer.colors.GREEN),
    ]
    if cover_plan.without_artwork:
        cover_rows.append(
            SummaryRow(
                label="No cover",
                count=len(cover_plan.without_artwork),
                marker="(--)",
                color=typer.colors.YELLOW,
            )
        )
    counts.append(cover_rows)

    echo_summary(counts, total=tag_plan.total)

    if tag_plan.errors:
        typer.secho(f"\n{len(tag_plan.errors)} file(s) could not be read:", fg=typer.colors.YELLOW)
        for outcome in tag_plan.errors:
            typer.echo(f"  {str(outcome.path)!r} - {outcome.detail}")


def _echo_report(cover_report: covers.CoverReport, tag_report: tagging.WriteReport) -> None:
    """Print the closing counts across both operations.

    Args:
        cover_report: What the cover pass did.
        tag_report: What the tagging pass did.
    """
    for failed, detail in (*tag_report.failures, *cover_report.failures):
        typer.secho(f"  Failed: {str(failed)!r} - {detail}", fg=typer.colors.RED)

    settled: int = cover_report.written + cover_report.renamed + cover_report.embedded
    typer.secho(
        f"\nDone. {tag_report.written} file(s) tagged, {settled} cover change(s) settled.",
        fg=typer.colors.GREEN,
        bold=True,
    )
    failures: int = len(tag_report.failures) + len(cover_report.failures)
    if failures:
        typer.secho(f"{failures} operation(s) failed during writing.", fg=typer.colors.RED)
