# src/discography_toolkit/cli/commands/tags/title.py
"""The `rapt title` command.

Recases the Title tag each track already carries, rather than deriving
one from anywhere. That makes it the one command whose wanted value
depends on what is there: `titlecase` reads the existing title and hands
back the same words differently cased.

A track with no Title is left alone and reported. There is nothing to
case, and inventing one from the filename would be a different command
making a much larger claim.
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
from discography_toolkit.core.layout import find_artist_folders, find_audio_files, is_artist_folder
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import title_case
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def title(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to work beneath. Any level: album, artist, or a whole shelf.",
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
    """Title-case the Title tag of every audio file beneath a path.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no audio found, a user abort, or
            a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to work beneath")
    artists: list[Path] = [] if is_artist_folder(target) else find_artist_folders(target)
    echo_banner("Metadata: Title", target.name, children=artist_names(target, artists))

    tracks: list[Path] = find_audio_files(target)
    if not tracks:
        typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.TITLE], _wants, on_progress=advance)

    _echo_plan(plan)

    if dry_run:
        _echo_changes(plan)
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    if not pending:
        typer.secho(
            "\nEvery Title is already cased correctly. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nRewrite {pending} Title tag(s)?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    with make_progress() as progress:
        advance = make_advancer(
            progress, target.name, [outcome.path for outcome in plan.pending], artists
        )
        report = tagging.apply(plan, on_progress=advance)

    echo_result("Title", report.written, "recased", failures=report.failures)


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _wants(_track: Path, current: Mapping[Tag, str]) -> Mapping[Tag, str]:
    """Return the cased form of the title already there.

    An absent title is returned unchanged rather than cased, so it
    compares equal and the track is left alone.

    Args:
        _track: The track, unused: the value comes from the tag.
        current: The track's tags as found.

    Returns:
        The Title tag, recased.
    """
    return {Tag.TITLE: title_case(current[Tag.TITLE])}


def _untitled(plan: tagging.TagPlan) -> list[tagging.TrackOutcome]:
    """Find tracks carrying no Title at all.

    They count as clean, since there is nothing to case, but they are a
    different thing from a title already correct and worth naming.

    Args:
        plan: The plan to inspect.

    Returns:
        Outcomes whose Title was empty.
    """
    return [
        outcome
        for outcome in plan.outcomes
        if outcome.status == "already_correct" and not outcome.current.get(Tag.TITLE)
    ]


def _echo_plan(plan: tagging.TagPlan) -> None:
    """Render the plan as one line, with anything needing an eye beneath it.

    Args:
        plan: The plan to summarize.
    """
    untitled: list[tagging.TrackOutcome] = _untitled(plan)
    notices: list[Notice] = []

    if untitled:
        notices.append(
            Notice(
                summary=f"{len(untitled)} file(s) carry no Title tag",
                details=tuple(f"{str(outcome.path)!r}" for outcome in untitled),
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

    echo_result("Title", len(plan.pending), "to recase", notices)


def _echo_changes(plan: tagging.TagPlan) -> None:
    """List every title that would change, old beside new.

    Shown on a dry run only. Each change is a distinct value worth
    reading before it is written, and several hundred lines of
    confirmation afterwards is not.

    Args:
        plan: The plan to list.
    """
    if not plan.pending:
        return

    width: int = max(len(repr(outcome.current[Tag.TITLE])) for outcome in plan.pending)
    arrow: str = typer.style("->", fg=typer.colors.GREEN)

    typer.echo()
    for outcome in plan.pending:
        was: str = repr(outcome.current[Tag.TITLE])
        now: str = repr(outcome.values[Tag.TITLE])
        typer.echo(f"  {was:{width}} {arrow} {now}")
