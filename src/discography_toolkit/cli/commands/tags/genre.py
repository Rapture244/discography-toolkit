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

from discography_toolkit.cli.commands.tags.notices import unreadable
from discography_toolkit.cli.console import (
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.scope import artists_in, require_tracks, resolve_path
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
    # Reporting only. Every audio file beneath the target is tagged
    # whether or not it sits under a recognized artist -- the path is a
    # scope, and filtering here would mean pointing at one album and
    # tagging nothing.
    artists: list[Path] = artists_in(target)
    echo_banner("Metadata: Genre", target.name, children=artist_names(target, artists))

    if value is None:
        value = cast("str", typer.prompt('\nEnter the genre (e.g. "Jazz" or "Jazz;Jazz Fusion")'))

    value = value.strip()
    if not value:
        typer.secho("\nGenre cannot be empty.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    tracks: list[Path] = require_tracks(target)

    label: str = typer.style("Genre ->", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"\n{label} {value!r}")

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

    echo_result("Genre", report.written, "tagged", failures=report.failures)


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


def _echo_plan(plan: tagging.TagPlan) -> None:
    """Render the plan as one line, with any unreadable files beneath it.

    Nothing here can be underivable: the value is given rather than read
    off a folder, so every track has one.

    Args:
        plan: The plan to summarize.
    """
    notice = unreadable(plan)

    echo_result("Genre", len(plan.pending), "to tag", [] if notice is None else [notice])
