# src/discography_toolkit/cli/commands/listing/__init__.py
"""The `rapt list` group: reading the shelf without changing it.

Every other group writes. This one only reports -- which is why it is a
group of its own rather than a flag on the commands that write: `tags
genre` and `tags genres` would be one letter apart, and the difference
between them is rewriting every file and merely looking at them.

The package is `listing` rather than `list` because the latter shadows a
builtin. Typer takes the name it is mounted under, so the command line
still reads `rapt list`.
"""

from __future__ import annotations

import typer

from discography_toolkit.cli.commands.listing import genres

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(
    no_args_is_help=True,
    help="Report on what a discography holds, without changing any of it.",
)


@app.callback()
def main() -> None:
    """Anchor the group so a lone command does not become the group."""


# ==================================================================================== #
#                                  COMMAND REGISTRY                                    #
# ==================================================================================== #
_ = app.command(
    name="genres",
    help="List every 'Genre' in use beneath a path, with a count for each.",
)(genres.genres)
