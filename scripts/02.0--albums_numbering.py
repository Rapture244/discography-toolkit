#!/usr/bin/env -S uv run
"""Renumber album folders inside a discography directory.

Strips any existing numeric prefix from album folder names (e.g. "01. ",
"3 - ", "007_"), then re-numbers every album sequentially in alphabetical
order: "01. ", "02. ", ... "xx. ". Safe to re-run at any time -- a folder
set that mixes numbered and unnumbered albums gets fully normalized.

An optional leading "©" favorite marker is set aside before sorting and
before the old prefix is stripped, so it plays no role in ordering --
albums are ordered purely by whatever's left (in practice, the year
`01--albums_naming.py` prefixes each name with). It's then glued back
onto the very front of the freshly numbered name, ahead of the index.
Unlike `01`, this only looks for "©" anchored at the very front, not
anywhere in the name -- by the time this script runs, `01` is expected
to have already relocated it there; re-validating its placement isn't
this script's job.

Calin's discography layout keeps lossy albums as direct children of an
artist folder, alongside one sibling container -- e.g. "FLAC -
(56 on 65)" -- holding every FLAC album underneath it. That container
is bookkeeping, not an album: it is never numbered or renamed itself.
Its children are pulled into the very same pool as the direct lossy
albums and numbered as one continuous sequence -- each album is then
renumbered in place, inside whichever folder (artist root or
container) it already lives in.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated, cast

from rich.console import Console
from rich.rule import Rule
from rich.text import Text
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

# Calin's personal "pin to the top" favorite marker, anchored to the
# very front only -- unlike 01--albums_naming.py, which searches the
# whole name for a stray "©" and relocates it. By the time this script
# runs, 01 is expected to have already put it there; re-validating its
# placement isn't this script's job. Set aside before sorting/prefix
# stripping so it never affects ordering, then reattached ahead of the
# freshly assigned index.
_CALINE_MARK_RE: re.Pattern[str] = re.compile(r"^©")

# The "missing"/"conflict" markers that 01--albums_naming.py writes
# between the year and the title -- "(1997) - M - Title", or "⚠" in
# place of the "M" when the name claims missing but the folder holds
# audio. Set aside before sorting for the same reason "©" is: the
# marker describes the album's availability, not its title, so an
# album must not change position when its availability changes.
#
# Left uncaught, the two scatter in opposite directions -- "M" sorts
# mid-alphabet while "⚠" (U+26A0) sorts above every ASCII letter, so
# resolving a conflict would move an album from the end of its year to
# wherever its title actually belongs, renumbering everything after it.
#
# Matches only the canonical shape, since 01 always runs first in the
# pipeline and is what puts the marker in that shape to begin with.
#
# This duplicates a convention owned by 01. The scripts are standalone
# by design, with no shared module to import from, so the glyph is
# deliberately hardcoded in both places.
_MISSING_MARKER_RE: re.Pattern[str] = re.compile(r"^(\([^)]*\)\s*-\s*)(?:⚠|M)\s*-\s*")

# Calin's discography layout splits an artist folder into lossy albums
# directly inside it, plus one sibling container -- e.g.
# "FLAC - (56 on 65)" -- holding every FLAC album underneath. That
# container is bookkeeping, not an album: it starts with the word
# "FLAC" and, after that, contains nothing but digits, whitespace,
# hyphens, parens/brackets, and the literal word "on" (as in "56 on
# 65") -- never any other letter. The container itself is never
# touched; only its children are discovered as ordinary albums.
_FLAC_CONTAINER_RE: re.Pattern[str] = re.compile(r"^FLAC(?:[\s\-()\[\]0-9]|on)*$", re.IGNORECASE)


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
        raw_input_path: str = cast(
            str, typer.prompt("Enter the absolute path to your discography folder")
        )
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    _echo_banner("Album Numbering", path.name)

    albums: list[Path] = _discover_albums(path)
    if not albums:
        typer.secho(f"No album folders found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    width: int = max(2, len(str(len(albums))))
    plan: list[tuple[Path, str]] = []
    for index, album in enumerate(albums, start=1):
        mark, after_mark = _split_caline_mark(album.name)
        stripped_name: str = _strip_prefix(after_mark)
        new_name: str = f"{mark}{index:0{width}d}. {stripped_name}"
        plan.append((album, new_name))

    typer.echo()

    total: int = len(plan)
    if total:
        renumbered_count: int = sum(1 for album, new_name in plan if album.name != new_name)
        clean_count: int = total - renumbered_count
        renumbered_pct: float = renumbered_count / total * 100
        clean_pct: float = clean_count / total * 100
        count_width: int = len(str(total))

        # (indent, bold word, rest of line, color) -- the label word itself
        # is bold, the marker/colon/count/percentage stay regular weight,
        # both segments sharing the same color so the row still reads as one.
        summary_rows: list[tuple[str, str, str, str]] = [
            ("", "Total", f"{'':<13}: {total}", typer.colors.BRIGHT_MAGENTA),
            (
                "  ",
                "Renumbered",
                f" (->) : {renumbered_count:>{count_width}}  ({renumbered_pct:.2f}%)",
                typer.colors.GREEN,
            ),
            (
                "  ",
                "Clean     ",
                f" (==) : {clean_count:>{count_width}}  ({clean_pct:.2f}%)",
                typer.colors.BLUE,
            ),
        ]
        box_width: int = max(len(indent + word + rest) for indent, word, rest, _ in summary_rows)

        typer.echo("┌" + "─" * (box_width + 2) + "┐")
        for indent, word, rest, color in summary_rows:
            pad: str = " " * (box_width - len(indent + word + rest))
            styled: str = (
                indent + typer.style(word, fg=color, bold=True) + typer.style(rest, fg=color) + pad
            )
            typer.echo("│ " + styled + " │")
        typer.echo("└" + "─" * (box_width + 2) + "┘")
        typer.echo()

    name_width: int = max((len(repr(album.name)) for album, _ in plan), default=0)
    for album, new_name in plan:
        changed: bool = album.name != new_name
        marker: str = (
            typer.style("->", fg=typer.colors.GREEN)
            if changed
            else typer.style("==", fg=typer.colors.BLUE)
        )
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
        _ = album.rename(temp_path)
        staged.append((temp_path, new_name))

    for temp_path, new_name in staged:
        final_path: Path = temp_path.parent / new_name
        _ = temp_path.rename(final_path)

    typer.secho(f"\nDone. {len(plan)} album(s) renumbered.", fg=typer.colors.GREEN, bold=True)


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
# Colour of the banner rule. Deliberately a colour used nowhere else in
# the output: green, blue, yellow, red, cyan, bright magenta and bright
# black all carry meaning here (changed, unchanged, warning, error, dry
# run, total, dimmed), so reusing any of them would read as a status
# rather than as a heading.
#
# Orange is only reachable through Rich. Typer styles with the 16-colour
# ANSI set, which has no orange in it at all; this is 256-colour index
# 208. Nearby alternatives are "orange1" (214, more amber), "orange3"
# (172, muted) and "orange_red1" (202, redder).
_BANNER_COLOR: str = "dark_orange"

# One console for the banner. Rich is used here only for its Rule, which
# fits itself to the terminal width -- a hand-rolled rule has to guess.
_console: Console = Console()


def _echo_banner(title: str, target: str) -> None:
    """Announce which step of the pipeline is running, and on what.

    Printed first, so a terminal holding the output of several scripts
    in sequence can be read back and each block attributed to the step
    that produced it.

    The title sits flush left rather than centred, so it lands at the
    same column as every other line of output and can be found by
    running an eye straight down the left margin. It carries no padding
    spaces: Rich inserts its own separator before the rule, and a
    centred title's padding would show as an indent here.

    The target is written with `typer.echo` rather than through the
    console on purpose. Rich parses square brackets as markup, so an
    artist folder named "Charlie Mariano - [90 • 60F • 0L • 30M]" comes
    out with the brackets restyled and the number syntax-highlighted.
    Only the rule, which is plain text under this script's control, goes
    through Rich.

    Deliberately carries no step number. The scripts are numbered by
    filename and that numbering is still settling, so a banner
    repeating it would be one more thing to keep in sync.

    Args:
        title: The step's name, e.g. `"Album Naming"`.
        target: The folder being worked on, printed on its own line
            beneath the rule.
    """
    _console.print()
    _console.print(
        Rule(
            Text(title, style=f"bold {_BANNER_COLOR}"),
            style=_BANNER_COLOR,
            characters="─",
            align="left",
        )
    )
    typer.echo(target)


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


def _split_caline_mark(name: str) -> tuple[str, str]:
    """Set aside a leading "©" marker, if present at the very front.

    Anchored to the front only -- by the time this script runs, `01`
    is expected to have already relocated any "©" there; this doesn't
    search the rest of the name the way `01` does.

    Args:
        name: The raw album folder name, possibly starting with "©".

    Returns:
        A tuple of `(mark, remainder)`. `mark` is `"©"` if the name
        starts with it, or an empty string otherwise. `remainder` is
        the rest of the name, unchanged.
    """
    match: re.Match[str] | None = _CALINE_MARK_RE.match(name)
    if match is None:
        return "", name
    return match.group(0), name[match.end() :]


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


def _sort_key(name: str) -> str:
    """Build the case-insensitive sort key used to order albums.

    Strips a leading "©", any existing numeric prefix, and the
    "M"/"⚠" availability marker first, so none of them affect ordering
    at all -- albums are ordered purely by year and title (in
    practice, the year `01--albums_naming.py` already prefixed each
    name with, then the title itself).

    Dropping the marker is what keeps numbering stable: an album that
    gains or loses audio keeps its place in the sequence instead of
    jumping to wherever its marker happens to sort.

    Args:
        name: The raw album folder name.

    Returns:
        The casefolded remainder, with "©", any numeric prefix, and
        any availability marker removed.
    """
    _, after_mark = _split_caline_mark(name)
    without_prefix: str = _strip_prefix(after_mark)
    without_marker: str = _MISSING_MARKER_RE.sub(r"\1", without_prefix, count=1)
    return without_marker.casefold()


def _discover_albums(root: Path) -> list[Path]:
    """Find album subdirectories inside a discography folder.

    A direct child that matches `_FLAC_CONTAINER_RE` (e.g. "FLAC -
    (56 on 65)") is treated as bookkeeping, not an album: it is never
    included itself, and never renamed. Instead, one level inside it
    is discovered and its children are added exactly as if they sat
    directly under `root` -- so lossy albums (found directly under
    `root`) and FLAC albums (found one level inside the container) end
    up combined into a single flat list, numbered as one continuous
    sequence. Only one level of container is unwrapped.

    Args:
        root: The directory to scan for album subfolders.

    Returns:
        Visible album subdirectories (hidden folders excluded, and the
        FLAC container itself excluded in favor of its own children),
        sorted case-insensitively by `_sort_key`.
    """
    albums: list[Path] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if _FLAC_CONTAINER_RE.match(entry.name):
            albums.extend(
                child
                for child in entry.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            )
            continue
        albums.append(entry)

    sorted_albums: list[Path] = sorted(albums, key=lambda p: _sort_key(p.name))
    return sorted_albums


# ==================================================================================== #
#                                     ENTRY POINT                                      #
# ==================================================================================== #
if __name__ == "__main__":
    app()
