# src/discography_toolkit/cli/commands/tags/__init__.py
"""The `rapt tags` group: everything written inside the audio files.

This package assembles the group; each command lives in its own module.
Adding one is a new file and a line here, which is why the root app never
grows.
"""

from __future__ import annotations

import typer

from discography_toolkit.cli.commands.tags import album_artist, genre, title

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(
    no_args_is_help=True,
    help="Read and write the tags inside audio files.",
)


@app.callback()
def main() -> None:
    """Anchor the group so a lone command does not become the group."""


# ==================================================================================== #
#                                  COMMAND REGISTRY                                    #
# ==================================================================================== #
_ = app.command(
    name="album-artist",
    help="Write each track's 'Album Artist' from the artist folder above it.",
)(album_artist.album_artist)
_ = app.command(name="genre", help="Set the 'Genre' tag on every audio file beneath a path.")(
    genre.genre
)
_ = app.command(
    name="title", help="Title-case the 'Title' tag of every audio file beneath a path."
)(title.title)
