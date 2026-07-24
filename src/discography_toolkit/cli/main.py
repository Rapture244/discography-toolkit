# src/discography_toolkit/cli/main.py
"""Assembly of the `rapt` command line.

This module owns the root app and nothing else. Command bodies live in
`commands/`, one module per group, and are mounted here -- so adding a
command means one new file plus one line, rather than growing a single
module until it is unreadable.

`pyproject.toml` points at the `app` object below:

    [project.scripts]
    rapt = "discography_toolkit.cli.main:app"

which is what makes `rapt` exist on the PATH after an install.
"""

from __future__ import annotations

import typer

from discography_toolkit.cli.commands import genre, title

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(
    name="rapt",
    help="Organize a music discography: folder layout and audio tags.",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Anchor the app as a command group rather than a single command.

    Typer promotes a lone command to the root when no callback exists,
    so `rapt --help` would show that command's own options and the
    subcommand name would disappear. Declaring a callback fixes the
    shape now, before the first command arrives, instead of letting the
    interface change under the user the moment a second one lands.
    """


# ==================================================================================== #
#                                  COMMAND REGISTRY                                    #
# ==================================================================================== #
# The decorator returns the function it wrapped; discarding it is the
# point, since the command is registered as a side effect.
_ = app.command(name="genre", help="Set the Genre tag on every audio file beneath a path.")(
    genre.genre
)
_ = app.command(name="title", help="Title-case the Title tag of every audio file beneath a path.")(
    title.title
)


if __name__ == "__main__":
    app()
