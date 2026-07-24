# src/discography_toolkit/cli/commands/tags/album_artist.py
"""The `rapt tags album-artist` command.

Writes the Album Artist tag -- the discography a track belongs to, taken
from the name of the artist folder above it. Not the Artist tag: that
names who played, which on a collaboration album differs and is left
alone.

The value is per artist, not per run. Pointed at one artist every track
gets that name; pointed at a shelf each track gets the name of the folder
it sits under, so one pass covers a whole region.

A track under no labelled artist folder is left alone and reported. There
is no name to derive, and guessing one from a parent folder that has not
been through the layout pass would invent a discography.
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
    find_artist_folders,
    find_audio_files,
    owning_folder,
)
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import strip_artist_label
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def album_artist(
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
    """Write each track's Album Artist from the artist folder above it.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no audio found, no artist folder
            recognized, a user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to work beneath")
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Metadata: Album Artist", target.name, children=artist_names(target, artists))

    if not artists:
        typer.secho(
            f"\nNo artist folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run the layout pass first -- it writes the count label this reads from.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    tracks: list[Path] = find_audio_files(target)
    if not tracks:
        typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    _echo_artists(artists)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.ALBUM_ARTIST], _wants(artists), on_progress=advance)

    _echo_plan(plan, artists)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    if not pending:
        typer.secho(
            "\nEvery file already carries its Album Artist. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nWrite Album Artist to {pending} file(s)?"):
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
def _wants(artists: Sequence[Path]) -> tagging.Desired:
    """Build the value function: each track named for the folder above it.

    Args:
        artists: The artist folders in scope.

    Returns:
        A `Desired` callable returning nothing for a track under none of
        them, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        name: str | None = _artist_of(track, artists)
        return {} if name is None else {Tag.ALBUM_ARTIST: name}

    return desired


def _artist_of(track: Path, artists: Sequence[Path]) -> str | None:
    """Find the Album Artist a track should carry.

    Args:
        track: The track to place.
        artists: The artist folders in scope.

    Returns:
        The owning folder's name without its count label, or `None` when
        the track sits under no artist folder.
    """
    folder: Path | None = owning_folder(track, artists)
    if folder is None:
        return None
    return strip_artist_label(folder.name)


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_artists(artists: Sequence[Path]) -> None:
    """Show the value each artist folder resolves to, before any writing.

    The derived name is the one decision this command makes, and it is
    about to go into hundreds of files, so it is shown rather than
    inferred from the folder name.

    Args:
        artists: The artist folders in scope.
    """
    label: str = typer.style("Album Artist ->", fg=typer.colors.GREEN, bold=True)
    typer.echo()
    for folder in artists:
        name: str | None = strip_artist_label(folder.name)
        typer.echo(f"{label} {name!r}")


def _unresolved(plan: tagging.TagPlan, artists: Sequence[Path]) -> list[tagging.TrackOutcome]:
    """Find tracks sitting under no artist folder.

    They count as clean, since nothing was written, but they are a
    different thing from a track already correct and worth naming.

    Args:
        plan: The plan to inspect.
        artists: The artist folders in scope.

    Returns:
        Outcomes for tracks with no derivable Album Artist.
    """
    return [
        outcome
        for outcome in plan.outcomes
        if outcome.status == "already_correct" and _artist_of(outcome.path, artists) is None
    ]


def _echo_plan(plan: tagging.TagPlan, artists: Sequence[Path]) -> None:
    """Render a plan as a summary box, plus anything that needs naming.

    Args:
        plan: The plan to summarize.
        artists: The artist folders in scope.
    """
    unresolved: list[tagging.TrackOutcome] = _unresolved(plan, artists)
    counts: list[list[SummaryRow]] = [
        [SummaryRow(label="Total", count=plan.total, indent="", percent=False)],
        [
            SummaryRow(
                label="Tagged", count=len(plan.pending), marker="(->)", color=typer.colors.GREEN
            ),
            SummaryRow(
                label="Clean",
                count=plan.clean - len(unresolved),
                marker="(==)",
                color=typer.colors.BLUE,
            ),
        ],
    ]
    if unresolved:
        counts[1].append(
            SummaryRow(
                label="No artist", count=len(unresolved), marker="(--)", color=typer.colors.YELLOW
            )
        )
    if plan.errors:
        counts[1].append(
            SummaryRow(
                label="Errors", count=len(plan.errors), marker="(!!)", color=typer.colors.RED
            )
        )

    echo_summary(counts, total=plan.total)

    if unresolved:
        typer.secho(
            f"\n{len(unresolved)} file(s) sit under no artist folder:", fg=typer.colors.YELLOW
        )
        for outcome in unresolved:
            typer.secho(f"  {str(outcome.path)!r}", fg=typer.colors.BRIGHT_BLACK)

    if plan.errors:
        typer.secho(f"\n{len(plan.errors)} file(s) could not be read:", fg=typer.colors.YELLOW)
        for outcome in plan.errors:
            typer.echo(f"  {str(outcome.path)!r} - {outcome.detail}")
