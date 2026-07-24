# src/discography_toolkit/cli/commands/tags/album.py
"""The `rapt tags album` command.

Writes the Album tag from the album folder's name, exactly as it stands
-- index, year and quality marker included. The folder name is the
canonical form of an album in this collection, so the tag matching it is
the point rather than a side effect: a player sorting on Album then
matches the shelf.

A track under no album folder is left alone and reported. Albums are
recognized through their artist, so a path the layout pass has not
touched has no name to take.
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
    is_artist_folder,
    owning_folder,
)
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def album(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to work beneath. An artist, or a shelf holding several.",
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
    """Write each track's Album tag from its album folder's name.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no album found, no audio found, a
            user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to work beneath")
    echo_banner("Metadata: Album", target.name, children=_artist_names(target))

    albums: list[Path] = find_albums(target)
    if not albums:
        typer.secho(
            f"\nNo album folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run the layout pass first -- albums are recognized through their artist.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    tracks: list[Path] = find_audio_files(target)
    if not tracks:
        typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    artists: list[Path] = find_artist_folders(target)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.ALBUM], _wants(albums), on_progress=advance)

    _echo_plan(plan, albums)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    if not pending:
        typer.secho("\nEvery file already carries its album. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nWrite the Album tag to {pending} file(s)?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    with make_progress() as progress:
        advance = make_advancer(
            progress, target.name, [outcome.path for outcome in plan.pending], artists
        )
        report = tagging.apply(plan, on_progress=advance)

    for failed, detail in report.failures:
        typer.secho(f"  Failed: {str(failed)!r} - {detail}", fg=typer.colors.RED)

    typer.secho(f"\nDone. {report.written} file(s) tagged.", fg=typer.colors.GREEN, bold=True)
    if report.failures:
        typer.secho(f"{len(report.failures)} file(s) failed during writing.", fg=typer.colors.RED)


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _wants(albums: Sequence[Path]) -> tagging.Desired:
    """Build the value function: each track named for its album folder.

    Args:
        albums: The album folders in scope.

    Returns:
        A `Desired` callable returning nothing for a track under none of
        them, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        folder: Path | None = owning_folder(track, albums)
        return {} if folder is None else {Tag.ALBUM: folder.name}

    return desired


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _artist_names(target: Path) -> list[str]:
    """List the artist folders inside a target, for the banner.

    Args:
        target: The folder the run is scoped to.

    Returns:
        Names of the artist folders found beneath it, empty when the
        target is itself an artist.
    """
    if is_artist_folder(target):
        return []
    return [folder.name for folder in find_artist_folders(target)]


def _orphans(plan: tagging.TagPlan, albums: Sequence[Path]) -> list[tagging.TrackOutcome]:
    """Find tracks sitting under no album folder.

    They count as clean, since nothing was written, but they are a
    different thing from a track already correct and worth naming.

    Args:
        plan: The plan to inspect.
        albums: The album folders in scope.

    Returns:
        Outcomes with no album to take a name from.
    """
    return [
        outcome
        for outcome in plan.outcomes
        if outcome.status == "already_correct" and owning_folder(outcome.path, albums) is None
    ]


def _echo_plan(plan: tagging.TagPlan, albums: Sequence[Path]) -> None:
    """Render a plan as a summary box, plus anything that needs naming.

    Args:
        plan: The plan to summarize.
        albums: The album folders in scope.
    """
    orphans: list[tagging.TrackOutcome] = _orphans(plan, albums)
    counts: list[list[SummaryRow]] = [
        [SummaryRow(label="Total", count=plan.total, indent="", percent=False)],
        [
            SummaryRow(
                label="Tagged", count=len(plan.pending), marker="(->)", color=typer.colors.GREEN
            ),
            SummaryRow(
                label="Clean",
                count=plan.clean - len(orphans),
                marker="(==)",
                color=typer.colors.BLUE,
            ),
        ],
    ]
    if orphans:
        counts[1].append(
            SummaryRow(
                label="No album", count=len(orphans), marker="(--)", color=typer.colors.YELLOW
            )
        )
    if plan.errors:
        counts[1].append(
            SummaryRow(
                label="Errors", count=len(plan.errors), marker="(!!)", color=typer.colors.RED
            )
        )

    echo_summary(counts, total=plan.total)

    if orphans:
        typer.secho(f"\n{len(orphans)} file(s) sit under no album folder:", fg=typer.colors.YELLOW)
        for outcome in orphans:
            typer.secho(f"  {str(outcome.path)!r}", fg=typer.colors.BRIGHT_BLACK)

    if plan.errors:
        typer.secho(f"\n{len(plan.errors)} file(s) could not be read:", fg=typer.colors.YELLOW)
        for outcome in plan.errors:
            typer.echo(f"  {str(outcome.path)!r} - {outcome.detail}")
