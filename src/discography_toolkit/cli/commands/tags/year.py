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

from discography_toolkit.cli.commands.tags.notices import underivable, unreadable
from discography_toolkit.cli.console import (
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.cli.scope import artists_in, require_albums, require_tracks
from discography_toolkit.core import derivation
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from discography_toolkit.cli.console import Notice


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
    artists: list[Path] = artists_in(target)
    echo_banner("Metadata: Year", target.name, children=artist_names(target, artists))

    albums: list[Path] = require_albums(target)
    tracks: list[Path] = require_tracks(target)

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

    echo_result("Year", report.written, "dated", failures=report.failures)


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
        value: str | None = derivation.date_of(track, albums)
        return {} if value is None else {Tag.DATE: value}

    return desired


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_plan(plan: tagging.TagPlan, albums: Sequence[Path]) -> None:
    """Render the plan as one line, with anything needing an eye beneath it.

    Args:
        plan: The plan to summarize.
        albums: The album folders in scope.
    """
    notices: list[Notice] = [
        notice
        for notice in (
            underivable(
                plan,
                lambda outcome: derivation.date_of(outcome.path, albums) is None,
                "have no year to take",
            ),
            unreadable(plan),
        )
        if notice is not None
    ]

    echo_result("Year", len(plan.pending), "to date", notices)
