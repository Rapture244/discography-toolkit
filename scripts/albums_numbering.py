#!/usr/bin/env -S uv run
"""Renumber album folders inside a discography directory.

Strips any existing numeric prefix from album folder names (e.g. "01. ",
"3 - ", "007_"), then re-numbers every album sequentially in alphabetical
order: "01. ", "02. ", ... "xx. ". Safe to re-run at any time -- a folder
set that mixes numbered and unnumbered albums gets fully normalized.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated

import typer

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(add_completion=False, help=__doc__)


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Leading 1-3 digit prefix, optionally wrapped in matching (parens) or
# [brackets], followed by zero or more separator characters -- e.g. "01. ",
# "1 - ", "007_", "(01) ", "[01] ", or bare "01" with no separator at all.
# The bare-digit form requires a non-digit immediately after (negative
# lookahead) so a 4+ digit run -- e.g. a "1999 - Album" year -- can never
# be partially matched as if the first 3 digits were a numbering prefix.
_PREFIX_RE: re.Pattern[str] = re.compile(r"^(?:\(\d{1,3}\)|\[\d{1,3}\]|\d{1,3}(?!\d))[._\s-]*")


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
@app.command()
def renumber(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Absolute path to the folder containing album subfolders.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show planned renames without touching anything."),
    ] = False,
) -> None:
    """Strip existing numbering (if any) and renumber all albums in a folder.

    Args:
        path: Absolute path to the folder containing album subfolders.
            Passed via `--path`/`-p`; if omitted, the user is
            prompted for it interactively instead.
        dry_run: If `True`, print the planned renames without
            modifying the filesystem.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            no albums found, nothing to rename, user abort, or a
            successful dry run / completed run), with an appropriate
            exit code attached.
    """
    if path is None:
        raw_input_path: str = typer.prompt("Enter the absolute path to your discography folder")
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    albums: list[Path] = _discover_albums(path)
    if not albums:
        typer.secho(f"No album folders found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    width: int = max(2, len(str(len(albums))))
    plan: list[tuple[Path, str]] = [
        (album, f"{index:0{width}d}. {_strip_prefix(album.name)}")
        for index, album in enumerate(albums, start=1)
    ]

    typer.secho(f"\n{len(plan)} album(s) found in {path}\n", bold=True)
    name_width: int = max((len(repr(album.name)) for album, _ in plan), default=0)
    for album, new_name in plan:
        marker: str = "=" if album.name == new_name else "->"
        typer.echo(f"  {album.name!r:{name_width}} {marker} {new_name!r}")

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    already_correct: bool = all(album.name == new_name for album, new_name in plan)
    if already_correct:
        typer.secho("\nAlready numbered correctly. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm("\nApply these renames?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Two-phase rename: stage everything under a temp name first, so a
    # folder's target name being currently held by a *different* folder
    # (common mid-renumbering) never causes a collision.
    staged: list[tuple[Path, str]] = []
    for album, new_name in plan:
        temp_path: Path = album.with_name(f".__tmp__{album.name}")
        album.rename(temp_path)
        staged.append((temp_path, new_name))

    for temp_path, new_name in staged:
        final_path: Path = temp_path.parent / new_name
        temp_path.rename(final_path)

    typer.secho(f"\nDone. {len(plan)} album(s) renumbered.", fg=typer.colors.GREEN, bold=True)


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _normalize_path_input(raw: str) -> str:
    """Clean up a path typed at an interactive prompt.

    Shell arguments have their surrounding quotes stripped by the shell
    itself before this program ever sees them, but a plain interactive
    prompt has no such step -- typing a quoted path here would otherwise
    leave the quote characters embedded literally in the string.

    Args:
        raw: The raw text as typed at the prompt.

    Returns:
        The input with leading/trailing whitespace and a single layer
        of surrounding single or double quotes removed, if present.
    """
    cleaned: str = raw.strip().strip("'\"")
    return cleaned


def _strip_prefix(name: str) -> str:
    """Remove an existing numeric prefix from an album folder name, if present.

    Args:
        name: The original folder name as it currently exists on disk.

    Returns:
        The folder name with any leading numeric prefix removed and
        surrounding whitespace stripped. Returned unchanged if no
        prefix is present.
    """
    stripped_name: str = _PREFIX_RE.sub("", name, count=1).strip()
    return stripped_name


def _discover_albums(root: Path) -> list[Path]:
    """Find album subdirectories inside a discography folder.

    Args:
        root: The directory to scan for album subfolders.

    Returns:
        Visible subdirectories of `root` (hidden folders excluded),
        sorted case-insensitively by their name with any existing
        numeric prefix stripped.
    """
    albums: list[Path] = [
        entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith(".")
    ]
    sorted_albums: list[Path] = sorted(albums, key=lambda p: _strip_prefix(p.name).casefold())
    return sorted_albums


# ==================================================================================== #
#                                     ENTRY POINT                                      #
# ==================================================================================== #
if __name__ == "__main__":
    app()
