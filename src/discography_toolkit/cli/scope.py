# src/discography_toolkit/cli/scope.py
"""What a run covers, and what it refuses when it covers nothing.

One question in two halves. First the path itself: every command takes
one, so every command should validate it the same way and ask for it the
same way when it is omitted. Then what lies beneath it -- which artists
the run spans, which albums, which tracks -- and the refusal, in the same
words each time, when the answer is empty.

Written per command those refusals drift: one says "run the layout pass
first" and the next forgets to say it at all.

The last refusal is the person's rather than the shelf's -- a
confirmation answered no -- and it lives here for that same reason. It
was spelled out at every command that writes, which is what made its
wording impossible to correct in one place.

Nothing here decides what to do with what it finds. That is the
command's, and so is the banner it prints above.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, cast

import typer

from discography_toolkit.core.layout import (
    find_albums,
    find_artist_folders,
    find_audio_files,
    is_artist_folder,
)

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Phrased here rather than at the raise, as `core.declarations` does with
# its own: a refusal read at a prompt is the interface, and one that is
# built inside a call is one nobody finds when the wording is questioned.
_NO_PATH: Final[str] = "Enter a path."
_NOT_A_DIRECTORY: Final[str] = "Not a directory: {folder}"

# Not "Aborted.", which is Click's own word for an interrupt -- it prints
# "Aborted!" on Ctrl-C and exits 1. Two outcomes a single character apart,
# and neither saying the thing anyone wants confirmed. This borrows the
# phrasing the dry runs already use, which reports the same fact.
_NOTHING_CHANGED: Final[str] = "No changes made."


# ==================================================================================== #
#                                        THE PATH                                      #
# ==================================================================================== #
def clean_input(raw: str) -> str:
    """Clean up a path typed at an interactive prompt.

    A shell strips surrounding quotes before the program sees an
    argument; a prompt does not, so a quoted path would otherwise keep
    the quote characters.

    Args:
        raw: The raw text as typed.

    Returns:
        The input with surrounding whitespace and quotes removed. Quotes
        are stripped greedily rather than one layer deep -- Windows
        forbids them in a filename, so there is no path this can damage.
    """
    return raw.strip().strip("'\"")


def to_folder(raw: str) -> Path:
    """Turn text typed at a prompt into the folder it names.

    Refuses by raising rather than exiting, because `click.prompt` catches
    a `BadParameter` from its converter and asks again. A path mistyped
    at a prompt then costs a retype instead of the whole command -- while
    the same mistake given as `--path` is caught by Typer before the body
    runs, which is what a script wants.

    Args:
        raw: The text as typed.

    Returns:
        The absolute, resolved directory it names.

    Raises:
        typer.BadParameter: If the text names nothing at all, or names
            something that is not a directory.
    """
    cleaned: str = clean_input(raw)
    # Nothing is not the current directory. `Path("").resolve()` is the
    # working directory and passes every check below it, so a space typed
    # at the prompt would answer "lay out wherever the shell happens to
    # be" -- which is the one answer nobody means.
    if not cleaned:
        raise typer.BadParameter(_NO_PATH)

    folder: Path = Path(cleaned).expanduser().resolve()
    if not folder.is_dir():
        raise typer.BadParameter(_NOT_A_DIRECTORY.format(folder=folder))

    return folder


def resolve_path(path: Path | None, prompt: str) -> Path:
    """Settle on a folder to work in, asking for one if none was given.

    Args:
        path: The path passed on the command line, or `None`.
        prompt: What to ask when it was not.

    Returns:
        An absolute, resolved directory.

    Raises:
        typer.Exit: If the path given on the command line is not a
            directory. One typed at the prompt is asked for again instead.
    """
    if path is None:
        return cast("Path", typer.prompt(prompt, value_proc=to_folder))

    if not path.is_dir():
        typer.secho(_NOT_A_DIRECTORY.format(folder=path), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    return path


# ==================================================================================== #
#                                     WHAT IS UNDER IT                                 #
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


# ==================================================================================== #
#                                     BEFORE WRITING                                   #
# ==================================================================================== #
def confirm_or_exit(question: str) -> None:
    """Ask before writing, and stop cleanly when the answer is no.

    Args:
        question: What to ask, already naming the action and its scope.
            Phrased by the command, since only it knows what it is about
            to do.

    Raises:
        typer.Exit: If the answer is no. Clean, since declining is a
            decision rather than a failure -- the same reason
            `require_tracks` exits zero on an empty folder.
    """
    if typer.confirm(question):
        return

    typer.secho(_NOTHING_CHANGED, fg=typer.colors.YELLOW)
    raise typer.Exit(code=0)
