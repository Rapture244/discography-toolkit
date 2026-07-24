# src/discography_toolkit/cli/commands/tags/year.py
"""The `rapt tags year` command.

Writes the Date tag from the year in the album folder's name. The tag is
called Date because that is the field -- it holds a full date perfectly
well -- while the command is called year because a year is what goes in
it.

An approximate year clears the tag rather than writing "199x". A date
field holding something that is not a date is worse than an empty one:
players sort on it, and ID3 stores it in a timestamp frame that refuses
the value anyway.

A track under no album folder is left alone and reported. Albums are
recognized through their artist, so a path the layout pass has not
touched has nothing to read a year from.
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
from discography_toolkit.core.names import extract_year, is_approximate_year
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def year(
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
    """Write each track's Date tag from the year in its album folder's name.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no album found, no audio found, a
            user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to work beneath")
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Metadata: Year", target.name, children=artist_names(target, artists))

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

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.DATE], _wants(albums), on_progress=advance)

    _echo_plan(plan, albums)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    if not pending:
        typer.secho("\nEvery file already carries its year. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nWrite the Date tag to {pending} file(s)?"):
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
    """Build the value function: each track dated by its album folder.

    Args:
        albums: The album folders in scope.

    Returns:
        A `Desired` callable returning nothing for a track under none of
        them, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        value: str | None = _year_of(track, albums)
        return {} if value is None else {Tag.DATE: value}

    return desired


def _year_of(track: Path, albums: Sequence[Path]) -> str | None:
    """Find the Date a track should carry.

    An approximate year resolves to an empty string, which clears the
    tag: "199x" is not a date, and a date field holding a non-date is
    worse than an empty one.

    Args:
        track: The track to place.
        albums: The album folders in scope.

    Returns:
        The year, an empty string for an approximation, or `None` when
        the track sits under no album folder.
    """
    album: Path | None = owning_folder(track, albums)
    if album is None:
        return None

    token: str | None = extract_year(album.name)
    if token is None:
        return None
    return "" if is_approximate_year(token) else token


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _undated(plan: tagging.TagPlan, albums: Sequence[Path]) -> list[tagging.TrackOutcome]:
    """Find tracks whose album offers no year.

    Either they sit under no album folder, or the folder's name carries
    no year token. Both count as clean, since nothing is written, but
    both are worth naming.

    Args:
        plan: The plan to inspect.
        albums: The album folders in scope.

    Returns:
        Outcomes with no derivable year.
    """
    return [
        outcome
        for outcome in plan.outcomes
        if outcome.status == "already_correct" and _year_of(outcome.path, albums) is None
    ]


def _echo_plan(plan: tagging.TagPlan, albums: Sequence[Path]) -> None:
    """Render a plan as a summary box, plus anything that needs naming.

    Args:
        plan: The plan to summarize.
        albums: The album folders in scope.
    """
    undated: list[tagging.TrackOutcome] = _undated(plan, albums)
    counts: list[list[SummaryRow]] = [
        [SummaryRow(label="Total", count=plan.total, indent="", percent=False)],
        [
            SummaryRow(
                label="Dated", count=len(plan.pending), marker="(->)", color=typer.colors.GREEN
            ),
            SummaryRow(
                label="Clean",
                count=plan.clean - len(undated),
                marker="(==)",
                color=typer.colors.BLUE,
            ),
        ],
    ]
    if undated:
        counts[1].append(
            SummaryRow(
                label="No year", count=len(undated), marker="(--)", color=typer.colors.YELLOW
            )
        )
    if plan.errors:
        counts[1].append(
            SummaryRow(
                label="Errors", count=len(plan.errors), marker="(!!)", color=typer.colors.RED
            )
        )

    echo_summary(counts, total=plan.total)

    if undated:
        typer.secho(f"\n{len(undated)} file(s) have no year to take:", fg=typer.colors.YELLOW)
        for outcome in undated:
            typer.secho(f"  {str(outcome.path)!r}", fg=typer.colors.BRIGHT_BLACK)

    if plan.errors:
        typer.secho(f"\n{len(plan.errors)} file(s) could not be read:", fg=typer.colors.YELLOW)
        for outcome in plan.errors:
            typer.echo(f"  {str(outcome.path)!r} - {outcome.detail}")
