#!/usr/bin/env -S uv run
"""Extract the release year and lossless status from album folder names.

Finds a year token -- "1994", "(1994)", "[1994]", or an approximate form
like "199x" / "19xx" -- anywhere in an album folder's name, removes it,
and prefixes it cleanly. Any existing "FLAC" / "(FLAC)" / "[FLAC]" text
is stripped as well -- it is never trusted on its own. Instead, the
album folder is scanned for actual lossless audio files (.flac, .wav,
.ape, .wv, .tta, .aiff/.aif, .dsf/.dff); only that real-content check
decides whether " [FLAC]" gets appended.

Final format: "(yyyy) - Album Name" for lossy albums, or
"(yyyy) - Album Name [FLAC]" for albums with at least one lossless file.

Safe to run at any point in the pipeline, whether before or after
albums_numbering.py, and on a mix of already-numbered and brand-new
albums at once: an existing leading index (e.g. "01. ") is detected,
set aside untouched, and reattached at the end -- this script never
interprets or renumbers it, only preserves it.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated, cast

import typer

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(add_completion=False, help=__doc__)


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# A year "core": exactly 2 known century digits followed by 2 more chars,
# each either a digit or a lowercase "x" for an unknown digit -- covers
# "1994" (fully known), "199x" (decade approximated), "19xx" (century only).
_YEAR_CORE: str = r"\d{2}[\dx]{2}"

# The year wrapped in matching (parens) or [brackets], searched anywhere
# in the name. Checked first -- an explicit wrapper is a much stronger
# signal than a bare number could ever be.
_YEAR_WRAPPED_RE: re.Pattern[str] = re.compile(rf"\((?:{_YEAR_CORE})\)|\[(?:{_YEAR_CORE})\]")

# The bare year, with no wrapper. Guarded on both sides by a "not another
# digit" lookaround so it can never latch onto part of a longer digit run
# (e.g. an 8-digit "19940101" date stamp) and misread it as a year.
_YEAR_BARE_RE: re.Pattern[str] = re.compile(rf"(?<!\d){_YEAR_CORE}(?!\d)")

# An existing numbering index at the very start of the name -- "01. ",
# "(01) ", "[01] " -- the exact convention albums_numbering.py itself
# writes and strips. Detected and set aside before year/FLAC processing
# so an already-numbered album is never corrupted by naming re-running
# on it, then reattached unchanged at the very end. Anchored to the
# start (^) only, unlike the year/FLAC patterns which search anywhere --
# an index is only ever meaningful as a prefix.
_EXISTING_INDEX_RE: re.Pattern[str] = re.compile(
    r"^(?:\(\d{1,3}\)|\[\d{1,3}\]|\d{1,3}(?!\d))[._\s-]*"
)

# An existing "FLAC" marker wrapped in (parens) or [brackets], anywhere
# in the name, case-insensitive. Checked first, same reasoning as years.
_FLAC_WRAPPED_RE: re.Pattern[str] = re.compile(r"\(\s*FLAC\s*\)|\[\s*FLAC\s*\]", re.IGNORECASE)

# A bare "FLAC" marker, case-insensitive, bounded on both sides so it
# can't match as a substring inside an unrelated word (e.g. "SuperFLAC").
_FLAC_BARE_RE: re.Pattern[str] = re.compile(r"\bFLAC\b", re.IGNORECASE)

# File extensions treated as lossless audio for detection purposes.
# ".m4a" is deliberately excluded: it can hold either lossy AAC or
# lossless ALAC, and the extension alone can't tell those apart.
_LOSSLESS_EXTENSIONS: frozenset[str] = frozenset(
    {".flac", ".wav", ".ape", ".wv", ".tta", ".aiff", ".aif", ".dsf", ".dff"}
)


# Leftover separator characters (whitespace, hyphen, underscore, dot) at
# the very start or end of a name, after a token has been cut out.
_EDGE_SEPARATORS_RE: re.Pattern[str] = re.compile(r"^[\s._-]+|[\s._-]+$")

# Two or more consecutive whitespace characters, left behind when a token
# is removed from the *middle* of a name rather than an edge.
_MULTI_SPACE_RE: re.Pattern[str] = re.compile(r"\s{2,}")


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
@app.command()
def naming(
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
    """Extract each album's year and lossless status, then rename it.

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

    albums: list[Path] = _discover_albums(path)
    if not albums:
        typer.secho(f"No album folders found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    plan: list[tuple[Path, str]] = []
    skipped: list[Path] = []
    for album in albums:
        index_prefix, content = _split_existing_index(album.name)
        year, after_year = _extract_year(content)
        if year is None:
            skipped.append(album)
            continue

        after_flac_strip: str = _strip_flac_marker(after_year)
        is_lossless: bool = _is_lossless_album(album)
        flac_suffix: str = " [FLAC]" if is_lossless else ""

        new_name: str = f"{index_prefix}({year}) - {after_flac_strip}{flac_suffix}"
        plan.append((album, new_name))

    typer.secho(f"\n{len(plan)} album(s) found in {path}\n", bold=True)
    name_width: int = max((len(repr(album.name)) for album, _ in plan), default=0)
    for album, new_name in plan:
        changed: bool = album.name != new_name
        marker: str = (
            typer.style("->", fg=typer.colors.GREEN)
            if changed
            else typer.style("==", fg=typer.colors.BLUE)
        )
        typer.echo(f"  {album.name!r:{name_width}} {marker} {new_name!r}")

    if skipped:
        typer.secho(
            f"\n{len(skipped)} album(s) with no detectable year (left untouched):",
            fg=typer.colors.YELLOW,
        )
        for album in skipped:
            typer.echo(f"  {album.name!r}")

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    already_correct: bool = all(album.name == new_name for album, new_name in plan)
    if already_correct:
        typer.secho("\nAlready named correctly. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm("\nApply these renames?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    for album, new_name in plan:
        if album.name != new_name:
            _ = album.rename(album.with_name(new_name))

    typer.secho(f"\nDone. {len(plan)} album(s) renamed.", fg=typer.colors.GREEN, bold=True)


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


def _discover_albums(root: Path) -> list[Path]:
    """Find album subdirectories inside a discography folder.

    Args:
        root: The directory to scan for album subfolders.

    Returns:
        Visible subdirectories of `root` (hidden folders excluded),
        sorted case-insensitively by their current name.
    """
    albums: list[Path] = [
        entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith(".")
    ]
    sorted_albums: list[Path] = sorted(albums, key=lambda p: p.name.casefold())
    return sorted_albums


def _split_existing_index(name: str) -> tuple[str, str]:
    """Set aside an existing numbering index, if the name already has one.

    Makes this script agnostic to whether `albums_numbering.py` has
    already run on a given folder or not -- an already-numbered album
    keeps its index untouched, while a brand-new, never-numbered album
    simply has nothing to set aside.

    Args:
        name: The raw album folder name, possibly still carrying a
            leading index written by `albums_numbering.py` (e.g.
            `"01. "`).

    Returns:
        A tuple of `(index_prefix, remainder)`. `index_prefix` is
        the exact leading text matched, including its own separator
        (e.g. `"01. "`), or an empty string if no index is present.
        `remainder` is the rest of the name, unchanged.
    """
    match: re.Match[str] | None = _EXISTING_INDEX_RE.match(name)
    if match is None:
        return "", name
    return match.group(0), name[match.end() :]


def _extract_year(name: str) -> tuple[str | None, str]:
    """Find and remove a year token from an album folder name.

    Searches for a wrapped year -- "(1994)" or "[1994]" -- anywhere in
    the name first, since an explicit wrapper is a stronger signal than
    a bare number. Falls back to a bare, unwrapped year if no wrapped
    form is found.

    Args:
        name: The original album folder name.

    Returns:
        A tuple of `(year, cleaned_name)`. `year` is the 4-character
        token (e.g. "1994", "199x") with any wrapping brackets removed,
        or `None` if no year token could be found at all -- in which
        case `cleaned_name` is simply `name` unchanged.
    """
    match: re.Match[str] | None = _YEAR_WRAPPED_RE.search(name)
    if match is None:
        match = _YEAR_BARE_RE.search(name)
    if match is None:
        return None, name

    year: str = match.group(0).strip("()[]")
    remaining: str = name[: match.start()] + name[match.end() :]
    cleaned_name: str = _cleanup_name(remaining)
    return year, cleaned_name


def _strip_flac_marker(name: str) -> str:
    """Find and remove an existing "FLAC" text marker, if present.

    This never decides whether an album *is* lossless -- it only cleans
    up a stale text label so `_is_lossless_album`'s real-content check
    is the sole source of truth for that. Searches for a wrapped marker
    -- "(FLAC)" or "[FLAC]" -- first, then falls back to a bare "FLAC".

    Args:
        name: An album name, already past year extraction.

    Returns:
        The name with any existing FLAC marker removed and the
        surrounding text tidied up. Returned unchanged if no marker
        is present.
    """
    match: re.Match[str] | None = _FLAC_WRAPPED_RE.search(name)
    if match is None:
        match = _FLAC_BARE_RE.search(name)
    if match is None:
        return name

    remaining: str = name[: match.start()] + name[match.end() :]
    return _cleanup_name(remaining)


def _is_lossless_album(album: Path) -> bool:
    """Check whether an album folder contains at least one lossless file.

    Scans recursively, so multi-disc albums split across "CD1"/"CD2"
    style subfolders are still detected correctly. Stops at the first
    match found rather than walking the entire tree every time.

    Args:
        album: Path to the album folder to scan.

    Returns:
        `True` if any file anywhere under `album` has an extension
        in `_LOSSLESS_EXTENSIONS`, `False` otherwise.
    """
    has_lossless_file: bool = any(
        entry.is_file() and entry.suffix.lower() in _LOSSLESS_EXTENSIONS
        for entry in album.rglob("*")
    )
    return has_lossless_file


def _cleanup_name(name: str) -> str:
    """Tidy up leftover spacing/punctuation after a token is removed.

    Args:
        name: The album name with a year or FLAC token already cut out.

    Returns:
        The name with any run of internal whitespace collapsed to a
        single space, and any separator characters (spaces, hyphens,
        underscores, dots) trimmed from the very start and end.
    """
    collapsed: str = _MULTI_SPACE_RE.sub(" ", name)
    trimmed: str = _EDGE_SEPARATORS_RE.sub("", collapsed)
    return trimmed.strip()


# ==================================================================================== #
#                                     ENTRY POINT                                      #
# ==================================================================================== #
if __name__ == "__main__":
    app()
