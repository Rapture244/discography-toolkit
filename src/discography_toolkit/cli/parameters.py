# src/discography_toolkit/cli/parameters.py
"""Option handling shared by every command.

Every command takes a path, so every command should validate it the same
way and ask for it the same way when it is omitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import typer


# ==================================================================================== #
#                                      PUBLIC API                                      #
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


def resolve_path(path: Path | None, prompt: str) -> Path:
    """Settle on a folder to work in, asking for one if none was given.

    Args:
        path: The path passed on the command line, or `None`.
        prompt: What to ask when it was not.

    Returns:
        An absolute, resolved directory.

    Raises:
        typer.Exit: If the path is not a directory.
    """
    if path is None:
        raw: str = cast("str", typer.prompt(prompt))
        path = Path(clean_input(raw)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    return path
