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

from discography_toolkit.cli.commands import align_tags, layout, organize, tags

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
#                                   GROUP REGISTRY                                     #
# ==================================================================================== #
# Groups, not bare commands: the toolkit splits into what it does to the
# folders and what it writes inside the files, and `rapt --help` should
# say so before listing nine verbs.
app.add_typer(tags.app, name="tags")

# The one exception is a bare command, not a group: layout is a single
# protocol, not a family of verbs, so it reads as one on `rapt --help`.
_ = app.command(
    name="layout",
    help="Rename, renumber, recase, file, and label a discography's folders.",
)(layout.layout)

# The tag counterpart to layout: it settles the folders, this settles the
# tags to match them -- every one the structure determines, genre aside.
_ = app.command(
    name="align-tags",
    help="Write every folder-derived tag -- album, artist, year, title, cover.",
)(align_tags.align_tags)

# The whole job in one: layout then align-tags, the app's tagline as a
# command. It sits first because it is the one most people want.
_ = app.command(
    name="organize",
    help="Lay out the folders and then write every folder-derived tag.",
)(organize.organize)


if __name__ == "__main__":
    app()
