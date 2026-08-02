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

from discography_toolkit.cli.commands.tags.notices import underivable, unreadable
from discography_toolkit.cli.console import (
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.cli.scope import require_tracks
from discography_toolkit.core import derivation
from discography_toolkit.core.layout import find_artist_folders
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import strip_artist_label
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from discography_toolkit.cli.console import Notice


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
    credit: Annotated[
        str | None,
        typer.Option(
            "--album-artist",
            help="Force this Album Artist on every track, in place of the folder's name.",
        ),
    ] = None,
) -> None:
    """Write each track's Album Artist from the artist folder above it.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.
        credit: An Album Artist to force on every track; derived per
            folder when omitted.

    Raises:
        typer.Exit: On an invalid path, no audio found, no artist folder
            recognized, a user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to work beneath")
    # Not `scope.artists_in`: this command reads the Album Artist off
    # these folders rather than only showing them, and that one returns
    # nothing for a target that is itself an artist. Handed that, every
    # track would find no folder to take a name from.
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Metadata: Album Artist", target.name, children=artist_names(target, artists))

    # A forced credit needs no artist folders: it names every track alike,
    # so a discography with none recognised yet is still taggable.
    if not artists and credit is None:
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

    tracks: list[Path] = require_tracks(target)

    _echo_artists(artists, credit)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(
            tracks, [Tag.ALBUM_ARTIST], _wants(artists, credit), on_progress=advance
        )

    _echo_plan(plan, artists, credit)

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

    echo_result("Album Artist", report.written, "tagged", failures=report.failures)


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _wants(artists: Sequence[Path], credit: str | None = None) -> tagging.Desired:
    """Build the value function: each track named for the folder above it.

    A `credit` overrides that, forcing the same Album Artist on every
    track whatever folder it sits under -- for a discography split across
    collection folders that should read as one artist.

    Args:
        artists: The artist folders in scope.
        credit: An Album Artist to force, or `None` to derive per folder.

    Returns:
        A `Desired` callable returning nothing for a track under none of
        them, which leaves it untouched -- unless a credit is forced, when
        every track takes it.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        if credit is not None:
            return {Tag.ALBUM_ARTIST: credit}
        name: str | None = derivation.album_artist_of(track, artists)
        return {} if name is None else {Tag.ALBUM_ARTIST: name}

    return desired


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_artists(artists: Sequence[Path], credit: str | None = None) -> None:
    """Show the value each artist folder resolves to, before any writing.

    The derived name is the one decision this command makes, and it is
    about to go into hundreds of files, so it is shown rather than
    inferred from the folder name. A forced credit is shown once, since it
    is the value for every track.

    Args:
        artists: The artist folders in scope.
        credit: The Album Artist being forced, or `None` to derive it.
    """
    label: str = typer.style("Album Artist ->", fg=typer.colors.GREEN, bold=True)
    typer.echo()
    if credit is not None:
        typer.echo(f"{label} {credit!r} (forced on every track)")
        return
    for folder in artists:
        name: str | None = strip_artist_label(folder.name)
        typer.echo(f"{label} {name!r}")


def _echo_plan(plan: tagging.TagPlan, artists: Sequence[Path], credit: str | None = None) -> None:
    """Render the plan as one line, with anything needing an eye beneath it.

    A forced credit reaches every track, so nothing can be unresolved
    and only unreadable files are worth an eye.

    Args:
        plan: The plan to summarize.
        artists: The artist folders in scope.
        credit: The Album Artist being forced, or `None`.
    """
    unresolved: Notice | None = (
        None
        if credit is not None
        else underivable(
            plan,
            lambda outcome: derivation.album_artist_of(outcome.path, artists) is None,
            "sit under no artist folder",
        )
    )
    notices: list[Notice] = [
        notice for notice in (unresolved, unreadable(plan)) if notice is not None
    ]

    echo_result("Album Artist", len(plan.pending), "to tag", notices)
