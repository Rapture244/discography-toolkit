# src/discography_toolkit/cli/scope.py
"""What a run covers, and what it refuses when it covers nothing.

Every command resolves the same three things out of the path it is
given -- which artists it spans, which albums, which tracks -- and
refuses in the same words when the answer is empty. Written per command
those refusals drift: one says "run the layout pass first" and the next
forgets to say it at all.

Nothing here decides what to do with what it finds. That is the
command's, and so is the banner it prints above.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from discography_toolkit.core.layout import (
    find_albums,
    find_artist_folders,
    find_audio_files,
    is_artist_folder,
)

if TYPE_CHECKING:
    from pathlib import Path


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def artists_in(target: Path) -> list[Path]:
    """Find the artists a run should show separately, for display only.

    A target that is itself an artist gets an empty list rather than
    itself: the banner names it on the line above, and a progress bar
    for the one artist would be the total bar drawn twice.

    Only for display. A command that *derives* a value from the artist
    folder -- `tags album-artist`, `align-tags` -- wants
    `find_artist_folders` instead, which returns a lone artist as
    itself. Handed this, such a command would find no folder to read a
    name from and quietly tag nothing.

    Args:
        target: The folder the run is scoped to.

    Returns:
        The artist folders to show, empty when the target is one.
    """
    return [] if is_artist_folder(target) else find_artist_folders(target)


def require_albums(target: Path) -> list[Path]:
    """Find the album folders beneath a target, refusing when there are none.

    Albums are recognized through their artist's count label, so a shelf
    the layout pass has not settled yields nothing -- which is worth
    saying plainly, since the fix is to run that pass rather than to
    point somewhere else.

    Args:
        target: The folder the run is scoped to.

    Returns:
        The album folders found.

    Raises:
        typer.Exit: If no album folder is found. An error: the run cannot
            do the thing it was asked to.
    """
    albums: list[Path] = find_albums(target)
    if albums:
        return albums

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


def require_tracks(target: Path) -> list[Path]:
    """Find the audio files beneath a target, stopping when there are none.

    Not an error, unlike an unsettled shelf: a folder holding no audio is
    a perfectly good folder that this run has nothing to do in.

    Args:
        target: The folder the run is scoped to.

    Returns:
        The audio files found.

    Raises:
        typer.Exit: If no audio file is found. Clean, since nothing to do
            is not a failure.
    """
    tracks: list[Path] = find_audio_files(target)
    if tracks:
        return tracks

    typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
    raise typer.Exit(code=0)
