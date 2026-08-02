# src/discography_toolkit/cli/commands/tags/album.py
"""The `rapt tags album` command.

Writes the Album tag from the album folder's name, keeping only the
album's title.

Everything else the folder carries is left out, because the discography
is shared: an album leaving here for someone else's library should say
what it is and nothing more. The pin mark floats a favourite on this
shelf, the index is a position in this numbering, the year is already in
Date, the availability marker describes an empty placeholder, and the
quality word describes how this copy was made. None of that is the
album, and none of it means anything to whoever receives it.

The folder name stays the source of truth, so nothing is lost by leaving
those out: the tag is derived afresh on every run, and a folder renamed
today is picked up by the next one.

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
    Notice,
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core import derivation
from discography_toolkit.core.layout import (
    find_albums,
    find_artist_folders,
    find_audio_files,
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
    """Write each track's Album tag from its album folder's title.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no album found, no audio found, a
            user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to work beneath")
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Metadata: Album", target.name, children=artist_names(target, artists))

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

    echo_result("Album", report.written, "tagged", failures=report.failures)


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _wants(albums: Sequence[Path]) -> tagging.Desired:
    """Build the value function: each track named for its album's title.

    Args:
        albums: The album folders in scope.

    Returns:
        A `Desired` callable returning nothing for a track under none of
        them, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        title: str | None = derivation.album_of(track, albums)
        return {} if title is None else {Tag.ALBUM: title}

    return desired


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
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
        if outcome.status == "already_correct" and derivation.album_of(outcome.path, albums) is None
    ]


def _echo_plan(plan: tagging.TagPlan, albums: Sequence[Path]) -> None:
    """Render the plan as one line, with anything needing an eye beneath it.

    Args:
        plan: The plan to summarize.
        albums: The album folders in scope.
    """
    orphans: list[tagging.TrackOutcome] = _orphans(plan, albums)
    notices: list[Notice] = []

    if orphans:
        notices.append(
            Notice(
                summary=f"{len(orphans)} file(s) sit under no album folder",
                details=tuple(f"{str(outcome.path)!r}" for outcome in orphans),
            )
        )
    if plan.errors:
        notices.append(
            Notice(
                summary=f"{len(plan.errors)} file(s) could not be read",
                details=tuple(
                    f"{str(outcome.path)!r} - {outcome.detail}" for outcome in plan.errors
                ),
            )
        )

    echo_result("Album", len(plan.pending), "to tag", notices)
