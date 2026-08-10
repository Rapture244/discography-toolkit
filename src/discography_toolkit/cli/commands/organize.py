# src/discography_toolkit/cli/commands/organize.py
"""The `rapt organize` command.

The whole job in one pass: lay out the folders, then write the tags off
them. It runs `rapt layout` and then `rapt align-tags`, in that order and
only that order -- layout is the pass that labels the artists, and
align-tags finds them by their labels, so the tags can only be read once
the folders have been settled.

An artist the layout half refuses is dropped from the tag half too. A
refusal says the shelf was not understood well enough to write to it,
and writing tags to it anyway would be worse than not refusing at all.

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
from discography_toolkit.cli.scope import confirm_or_exit, resolve_path
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
    target: Path = resolve_path(path, "Path to organize")
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
    typer.secho("Opus albums that duplicate a FLAC one will be deleted.", fg=typer.colors.YELLOW)
    typer.secho("There is no dry run.", fg=typer.colors.YELLOW)
    question: str = (
        f"Lay out {len(artists)} artist(s) beneath {target.name!r} and write every tag but genre?"
    )
    confirm_or_exit(question)

    typer.secho("\nLayout", fg=typer.colors.CYAN, bold=True)
    results, skipped = layout.run(artists)

    # Layout has just labelled the artists, so they are now findable the
    # ordinary way. If the target was itself a lone artist, it was renamed
    # under it -- follow it to its new path rather than the stale one.
    root: Path = target
    if not target.exists() and results:
        root = results[0].artist

    # A refused artist is left untouched, and that has to mean by both
    # halves: layout skipping one and the tag pass writing to it anyway
    # is worse than not refusing at all, since the refusal says the
    # shelf was not understood. Refusals happen before the first write,
    # so their folders were never renamed and their paths still hold.
    refused: list[Path] = [refusal.artist for refusal in skipped]
    labelled: list[Path] = _outside(find_artist_folders(root), refused)
    albums: list[Path] = _outside(find_albums(root), refused)
    tracks: list[Path] = _outside(find_audio_files(root), refused)

    if not albums:
        _echo_summary(results, skipped, tagged=0, settled=0, prefixed=0, failures=0)
        return

    typer.secho("\nTags", fg=typer.colors.CYAN, bold=True)
    cover_report, tag_report, disc_report = align_tags.run(albums, tracks, labelled, credit)

    settled: int = cover_report.written + cover_report.renamed + cover_report.embedded
    failures: int = (
        len(cover_report.failures) + len(tag_report.failures) + len(disc_report.failures)
    )
    _echo_summary(
        results,
        skipped,
        tagged=tag_report.written,
        settled=settled,
        prefixed=disc_report.renamed,
        failures=failures,
    )


# ==================================================================================== #
#                                      REFUSALS                                        #
# ==================================================================================== #
def _outside(found: list[Path], refused: list[Path]) -> list[Path]:
    """Drop everything sitting under an artist the layout half refused.

    Args:
        found: Paths discovered beneath the target.
        refused: The artist folders that were skipped.

    Returns:
        Those under none of them.
    """
    if not refused:
        return found
    return [path for path in found if not any(path.is_relative_to(folder) for folder in refused)]


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_summary(
    results: list[layout.ArtistResult],
    skipped: list[layout.Skipped],
    *,
    tagged: int,
    settled: int,
    prefixed: int,
    failures: int,
) -> None:
    """Print the closing totals across both halves.

    The scope comes first and the changes after, only what happened being
    named. "0 of 1 artist(s) laid out" read as a run that did nothing,
    for a run that had checked an artist, found its folders settled, and
    renamed forty of its files.

    Args:
        results: What each laid-out artist changed.
        skipped: Artists the layout half refused, each carrying its own
            reason -- two containers, an album split across discs, or an
            album held more than once.
        tagged: How many files the tag pass wrote.
        settled: How many cover changes were settled.
        prefixed: How many files took a disc prefix.
        failures: How many write operations failed across the run.
    """
    changed: int = sum(1 for result in results if result.changed)

    parts: list[str] = []
    if changed:
        parts.append(f"{changed} artist(s) laid out")
    if settled:
        parts.append(f"{settled} cover change(s) settled")
    if tagged:
        parts.append(f"{tagged} file(s) tagged")
    if prefixed:
        parts.append(f"{prefixed} file(s) prefixed with their disc")

    checked: str = f"{len(results)} artist(s) checked"
    closing: str = f"{checked}; {', '.join(parts)}" if parts else f"{checked}, nothing to change"
    typer.secho(f"\nDone. {closing}.", fg=typer.colors.GREEN, bold=True)

    layout.echo_skipped(skipped)
    if failures:
        typer.secho(f"{failures} operation(s) failed during writing.", fg=typer.colors.RED)
