# src/discography_toolkit/cli/commands/organize.py
"""The `rapt organize` command.

The whole job in one pass: lay out the folders, then write the tags off
them. It runs `rapt layout` and then `rapt align-tags`, in that order and
only that order -- layout is the pass that labels the artists, and
align-tags finds them by their labels, so the tags can only be read once
the folders have been settled.

That ordering is why this exists as its own command rather than a note in
the README: on fresh material align-tags has nothing to find until layout
has run, and running them by hand means pointing at the same folder
twice. Here the folder is resolved once, the artists are re-found after
they are labelled, and a single confirmation covers both halves. There is
no dry run, since the layout half cannot preview a chained protocol.
"""

from __future__ import annotations

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import Annotated

import typer

from discography_toolkit.cli.commands import align_tags, layout
from discography_toolkit.cli.console import echo_banner
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core.layout import (
    find_albums,
    find_artist_folders,
    find_artists,
    find_audio_files,
)


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def organize(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to organize. A shelf of artists, or a single artist.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    credit: Annotated[
        str | None,
        typer.Option(
            "--album-artist",
            help="Force this Album Artist on every track, in place of the folder's name.",
        ),
    ] = None,
) -> None:
    """Lay out the folders and then write every folder-derived tag.

    Args:
        path: Folder to organize; prompted for if omitted.
        credit: An Album Artist to force on every track; derived per
            folder when omitted. Meant for a discography split across
            collection folders that should read as one artist.

    Raises:
        typer.Exit: On an invalid path, no artist found, a user abort, or
            a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to organize")
    artists: list[Path] = find_artists(target)
    echo_banner("Organize", target.name, children=[artist.name for artist in artists])

    if not artists:
        typer.secho(
            f"\nNo artist folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Artists are recognised by their audio -- point at one holding tracks.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo()
    if credit is not None:
        scopes: int = len(artists)
        typer.secho(
            f"Forcing Album Artist = {credit!r} on every track under {target.name!r} ({scopes} scope(s)).",
            fg=typer.colors.YELLOW,
        )
    warning: str = f"This deletes Opus albums that duplicate a FLAC one, lays out {len(artists)} artist(s), and then writes every tag but genre. There is no dry run."
    typer.secho(warning, fg=typer.colors.YELLOW)
    if not typer.confirm("Proceed?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.secho("\nLayout", fg=typer.colors.CYAN, bold=True)
    results, skipped = layout.run(artists)

    # Layout has just labelled the artists, so they are now findable the
    # ordinary way. If the target was itself a lone artist, it was renamed
    # under it -- follow it to its new path rather than the stale one.
    root: Path = target
    if not target.exists() and results:
        root = results[0].artist
    labelled: list[Path] = find_artist_folders(root)
    albums: list[Path] = find_albums(root)
    tracks: list[Path] = find_audio_files(root)

    if not albums:
        _echo_summary(results, skipped, tagged=0, settled=0, failures=0)
        return

    typer.secho("\nTags", fg=typer.colors.CYAN, bold=True)
    cover_report, tag_report = align_tags.run(albums, tracks, labelled, credit)

    settled: int = cover_report.written + cover_report.renamed + cover_report.embedded
    failures: int = len(cover_report.failures) + len(tag_report.failures)
    _echo_summary(results, skipped, tagged=tag_report.written, settled=settled, failures=failures)


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_summary(
    results: list[layout.ArtistResult],
    skipped: list[layout.Skipped],
    *,
    tagged: int,
    settled: int,
    failures: int,
) -> None:
    """Print the closing totals across both halves.

    Args:
        results: What each laid-out artist changed.
        skipped: Artists the layout half refused, each carrying its own
            reason -- two containers, or an album held more than once.
        tagged: How many files the tag pass wrote.
        settled: How many cover changes were settled.
        failures: How many write operations failed across the run.
    """
    changed: int = sum(1 for result in results if result.changed)

    summary: str = f"\nDone. {changed} of {len(results)} artist(s) laid out; {settled} cover change(s) settled, {tagged} file(s) tagged."
    typer.secho(summary, fg=typer.colors.GREEN, bold=True)
    layout.echo_skipped(skipped)
    if failures:
        typer.secho(f"{failures} operation(s) failed during writing.", fg=typer.colors.RED)
