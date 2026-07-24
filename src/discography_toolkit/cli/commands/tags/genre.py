# src/discography_toolkit/cli/commands/tags/genre.py
"""The `rapt genre` command.

Pairs with `operations.tagging`: that plans and writes, this decides what
a genre run looks like. The operation returns a result and prints
nothing, so every rendering decision lives here.

The value is written verbatim and never parsed. "Jazz;Jazz Fusion" is
one string in one tag; whether a player reads that as two genres depends
on the separator it is configured with, which keeps the choice a display
setting rather than something baked into the files.

Genre is a bare command rather than part of a group. Its scope is
whatever path it is given -- one album, one artist, or a whole shelf --
and its value is supplied rather than derived, so it belongs to neither
the folder pass nor the metadata pass.
"""

from __future__ import annotations

# Runtime import, not a type-checking one: Typer resolves annotations with
# get_type_hints() when it builds the command, so every name used in a
# signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, cast

import typer

from discography_toolkit.cli.console import (
    SummaryRow,
    echo_banner,
    echo_summary,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core.layout import find_artist_folders, find_audio_files, is_artist_folder
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def genre(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to tag beneath. Any level: album, artist, or a whole shelf.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    value: Annotated[
        str | None,
        typer.Option("--genre", "-g", help='Genre to write, verbatim. Quote it: "Jazz;Fusion".'),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without writing."),
    ] = False,
) -> None:
    """Set the Genre tag on every audio file beneath a path.

    Args:
        path: Folder to tag beneath; prompted for if omitted.
        value: Written to the Genre tag exactly as given; prompted for
            if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, an empty genre, no audio found,
            a user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to tag beneath")
    echo_banner("Metadata: Genre", target.name, children=_artist_names(target))

    if value is None:
        value = cast("str", typer.prompt('\nEnter the genre (e.g. "Jazz" or "Jazz;Jazz Fusion")'))

    value = value.strip()
    if not value:
        typer.secho("\nGenre cannot be empty.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    tracks: list[Path] = find_audio_files(target)
    if not tracks:
        typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    label: str = typer.style("Genre ->", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"\n{label} {value!r}")

    artists: list[Path] = [] if is_artist_folder(target) else find_artist_folders(target)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.GENRE], _wants(value), on_progress=advance)

    _echo_plan(plan)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    if not pending:
        typer.secho(
            "\nEvery file already carries this Genre. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nWrite Genre to {pending} file(s)?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    with make_progress() as progress:
        written = [outcome.path for outcome in plan.pending]
        advance = make_advancer(progress, target.name, written, artists)
        report = tagging.apply(plan, on_progress=advance)

    for failed, detail in report.failures:
        typer.secho(f"  Failed: {str(failed)!r} - {detail}", fg=typer.colors.RED)

    typer.secho(f"\nDone. {report.written} file(s) tagged.", fg=typer.colors.GREEN, bold=True)
    if report.failures:
        typer.secho(f"{len(report.failures)} file(s) failed during writing.", fg=typer.colors.RED)


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _wants(value: str) -> tagging.Desired:
    """Build the value function: the same value for every track.

    Args:
        value: Written to the Genre tag exactly as given.

    Returns:
        A `Desired` callable ignoring the track and its current tags.
    """

    def desired(_track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        return {Tag.GENRE: value}

    return desired


def _artist_names(target: Path) -> list[str]:
    """List the artist folders inside a target, for the banner.

    Empty when the target is itself an artist: its children are albums,
    and listing them would say nothing about scope.

    Reporting only. Every audio file beneath the target is tagged whether
    or not it sits under a recognized artist -- the path is a scope, and
    filtering here would mean pointing at one album tagged nothing.

    Args:
        target: The folder the run is scoped to.

    Returns:
        Names of the artist folders found beneath it.
    """
    if is_artist_folder(target):
        return []
    return [folder.name for folder in find_artist_folders(target)]


def _echo_plan(plan: tagging.TagPlan) -> None:
    """Render a plan as a summary box, plus any files that failed to read.

    Args:
        plan: The plan to summarize.
    """
    counts: list[list[SummaryRow]] = [
        [SummaryRow(label="Total", count=plan.total, indent="", percent=False)],
        [
            SummaryRow(
                label="Tagged", count=len(plan.pending), marker="(->)", color=typer.colors.GREEN
            ),
            SummaryRow(label="Clean", count=plan.clean, marker="(==)", color=typer.colors.BLUE),
        ],
    ]
    if plan.errors:
        counts[1].append(
            SummaryRow(
                label="Errors", count=len(plan.errors), marker="(!!)", color=typer.colors.RED
            )
        )

    echo_summary(counts, total=plan.total)

    if plan.errors:
        typer.secho(f"\n{len(plan.errors)} file(s) could not be read:", fg=typer.colors.YELLOW)
        for outcome in plan.errors:
            typer.echo(f"  {str(outcome.path)!r} - {outcome.detail}")
