#!/usr/bin/env -S uv run
"""Extract the release year and audio quality tier from album folder names.

Finds a year token -- "1994", "(1994)", "[1994]", or an approximate form
like "199x" / "19xx" -- anywhere in an album folder's name, removes it,
and prefixes it cleanly. Any existing "FLAC" / "OPUS" text marker --
wrapped in (parens), [brackets], or bare -- is stripped as well; it is
never trusted on its own. Instead, the album folder is scanned for
actual audio files, and the real-content check alone decides which of
three quality tiers gets suffixed:

- Lossless (.flac, .wav, .ape, .wv, .tta, .aiff/.aif, .dsf/.dff present)
  -> " [FLAC]". This is Calin's master library format.
- Opus (.opus present, no lossless files) -> " [OPUS]". Calin transcodes
  FLAC masters down to Opus for day-to-day playlists -- much smaller,
  no perceptible loss on headphones/speakers, but not the byte-perfect
  master, hence its own tier rather than being folded into "lossless".
- Neither -> no suffix at all. The plain lossy (mp3) default.

If a folder somehow contains both lossless and Opus files at once (e.g.
a transcode caught mid-run), lossless wins -- it's the higher tier and
the more trustworthy signal.

Final format: "(yyyy) - Album Name" for plain lossy albums,
"(yyyy) - Album Name [FLAC]" for lossless albums, or
"(yyyy) - Album Name [OPUS]" for Opus-transcoded albums.

Safe to run at any point in the pipeline, whether before or after
albums_numbering.py, and on a mix of already-numbered and brand-new
albums at once: an existing leading index (e.g. "01. ") is detected,
set aside untouched, and reattached at the end -- this script never
interprets or renumbers it, only preserves it.

An optional "©" is also recognized and relocated to the front. It's a
personal marker (nothing to do with copyright) meaning "I've decided
this one's a favorite" -- used purely to pin an album above everything
else in a file browser's default sort. Unlike the numbering index, it
isn't anchored to any position: type it anywhere in the name (front,
middle, end) while listening, and this script finds it, removes it,
and re-prefixes it at the very front (ahead of the numbering index, if
one is present) -- no need to type it in the right spot to begin with.
It carries no year/quality meaning of its own.

Calin's discography layout keeps lossy albums as direct children of an
artist folder, alongside one sibling container -- e.g. "FLAC -
(56 on 65)" -- holding every FLAC album underneath it. That container
is bookkeeping, not an album: this script never renames it, and looks
one level inside it instead, treating its children exactly like any
other top-level album (same year/quality/© handling, same summary and
per-album reporting). Only one level of container is unwrapped.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated, Literal, cast

from mutagen import MutagenError
from mutagen.mp4 import MP4
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

# Calin's personal "pin to the top" marker: a bare "©" anywhere in the
# name -- not anchored to the front like the numbering index, since it
# gets typed wherever he happens to be while listening, not necessarily
# at the start. Found and relocated to the very front regardless of
# where it originally sat. Nothing to do with copyright; purely
# cosmetic and carries no year/quality meaning of its own.
_CALINE_MARK_RE: re.Pattern[str] = re.compile(r"©")

# A single "(...)" or "[...]" wrapper anywhere in the name, captured
# whole so its inner text can be inspected. Some albums were tagged
# inconsistently before Calin settled on a convention, mixing the
# quality word into the same bracket as another descriptive tag --
# e.g. "[FLAC, 40th Anniversary]", "(FLAC, Live)", "[FLAC m4a]". Used
# to find whichever bracket actually contains the stale quality word,
# so just that word can be cut out of it while any other tag sharing
# the bracket is kept.
_ANY_WRAPPED_RE: re.Pattern[str] = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")

# A bare "FLAC" or "OPUS" marker, case-insensitive, bounded on both
# sides so it can't match as a substring inside an unrelated word.
_QUALITY_BARE_RE: re.Pattern[str] = re.compile(r"\b(?:FLAC|OPUS)\b", re.IGNORECASE)

# Calin's discography layout splits an artist folder into lossy albums
# directly inside it, plus one sibling container -- e.g.
# "FLAC - (56 on 65)" -- holding every FLAC album underneath. That
# container is bookkeeping, not an album: it starts with the word
# "FLAC" and, after that, contains nothing but digits, whitespace,
# hyphens, parens/brackets, and the literal word "on" (as in "56 on
# 65") -- never any other letter. A real album name never matches this
# (e.g. "Flac Albums" or "01. (1951) - Modern Jazz Trumpets" both fail,
# since they carry other letters). The container itself is never
# touched; only its children are discovered as ordinary albums.
_FLAC_CONTAINER_RE: re.Pattern[str] = re.compile(r"^FLAC(?:[\s\-()\[\]0-9]|on)*$", re.IGNORECASE)

# File extensions unambiguously treated as lossless audio, just from
# their extension. ".m4a" is deliberately NOT here -- it's an MP4
# container that can hold either lossy AAC or lossless ALAC, and the
# extension alone can never tell those apart. It's handled separately,
# in `_detect_audio_tier`, by inspecting the actual codec inside the
# file via mutagen.
_LOSSLESS_EXTENSIONS: frozenset[str] = frozenset(
    {".flac", ".wav", ".ape", ".wv", ".tta", ".aiff", ".aif", ".dsf", ".dff"}
)

# Opus is a lossy codec, NOT lossless -- Calin transcodes his FLAC
# masters down to Opus for day-to-day playlists (much smaller, no
# perceptible quality loss on headphones/speakers, just not
# byte-for-byte the master). Tracked as its own middle tier, distinct
# from both true lossless and the plain-mp3 default.
_OPUS_EXTENSIONS: frozenset[str] = frozenset({".opus"})

# The three recognized audio quality tiers, in priority order when
# deciding a suffix: lossless beats opus beats the plain lossy default.
_AudioTier = Literal["lossless", "opus", "lossy"]


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
    """Extract each album's year and audio quality tier, then rename it.

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
        mark, after_mark = _split_caline_mark(album.name)
        index_prefix, content = _split_existing_index(after_mark)
        year, after_year = _extract_year(content)
        if year is None:
            skipped.append(album)
            continue

        after_quality_strip: str = _strip_quality_marker(after_year)
        tier: _AudioTier = _detect_audio_tier(album)
        quality_suffix: str = {"lossless": " [FLAC]", "opus": " [OPUS]", "lossy": ""}[tier]

        new_name: str = f"{mark}{index_prefix}({year}) - {after_quality_strip}{quality_suffix}"
        plan.append((album, new_name))

    typer.secho(f"\n{len(plan)} album(s) found in {path}\n", bold=True)

    total: int = len(plan)
    if total:
        renamed_count: int = sum(1 for album, new_name in plan if album.name != new_name)
        clean_count: int = total - renamed_count
        renamed_pct: float = renamed_count / total * 100
        clean_pct: float = clean_count / total * 100
        count_width: int = len(str(total))

        # (indent, bold word, rest of line, color) -- the label word itself
        # is bold, the marker/colon/count/percentage stay regular weight,
        # both segments sharing the same color so the row still reads as one.
        summary_rows: list[tuple[str, str, str, str]] = [
            ("", "Total", f"{'':<10}: {total}", typer.colors.BRIGHT_MAGENTA),
            (
                "  ",
                "Renamed",
                f" (->) : {renamed_count:>{count_width}}  ({renamed_pct:.2f}%)",
                typer.colors.GREEN,
            ),
            (
                "  ",
                "Clean",
                f"   (==) : {clean_count:>{count_width}}  ({clean_pct:.2f}%)",
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

    A direct child that matches `_FLAC_CONTAINER_RE` (e.g. "FLAC -
    (56 on 65)") is treated as bookkeeping, not an album: it is never
    included itself, and never renamed. Instead, one level inside it
    is discovered and its children are added exactly as if they sat
    directly under `root` -- so lossy albums (found directly under
    `root`) and FLAC albums (found one level inside the container) end
    up combined into a single flat list, matching how they're numbered
    as one continuous sequence in practice. Only one level of container
    is unwrapped; a container is never expected to hold another one.

    Args:
        root: The directory to scan for album subfolders.

    Returns:
        Visible album subdirectories (hidden folders excluded, and the
        FLAC container itself excluded in favor of its own children),
        sorted case-insensitively by name.
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

    sorted_albums: list[Path] = sorted(albums, key=lambda p: p.name.casefold())
    return sorted_albums


def _split_caline_mark(name: str) -> tuple[str, str]:
    """Find and remove a "©" marker, if present anywhere in the name.

    Calin's own convention for pinning a hand-picked album to the top
    of a file browser's default sort -- nothing to do with copyright.
    Unlike the numbering index, it isn't anchored to any position: it
    can be typed anywhere (front, middle, end) while listening, and
    this function finds it wherever it is, removes it, and tidies up
    whatever separator was left behind. It's the caller's job to
    re-prefix the returned mark at the very front of the final name.

    Only the first "©" found is handled -- the convention assumes at
    most one per name.

    Args:
        name: The raw album folder name, possibly containing a "©"
            anywhere within it.

    Returns:
        A tuple of `(mark, remainder)`. `mark` is `"©"` if found
        anywhere in `name`, or an empty string if absent entirely.
        `remainder` is the name with that single "©" removed and the
        surrounding whitespace/punctuation tidied up.
    """
    match: re.Match[str] | None = _CALINE_MARK_RE.search(name)
    if match is None:
        return "", name

    remaining: str = name[: match.start()] + name[match.end() :]
    cleaned: str = _cleanup_name(remaining)
    return match.group(0), cleaned


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


def _strip_quality_marker(name: str) -> str:
    """Find and remove an existing "FLAC"/"OPUS" quality word, if present.

    This never decides an album's quality tier -- it only cleans up a
    stale quality word so `_detect_audio_tier`'s real-content check is
    the sole source of truth for that. A folder can easily carry a
    stale word from before its files last changed (e.g. transcoded to
    Opus), so either word is stripped regardless of which is present.

    Some albums were tagged inconsistently before Calin settled on a
    convention, mixing the quality word into the same bracket as
    another descriptive tag worth keeping -- e.g. "[FLAC, 40th
    Anniversary]", "(FLAC, Live)", "[FLAC m4a]". In that case, only the
    quality word itself is removed; the other tag is kept, re-wrapped
    in a bracket of the same style, in the same position. A bracket
    containing *only* the quality word (a clean "[FLAC]"/"(OPUS)") is
    removed entirely, same as before. Falls back to a bare, unwrapped
    word if no bracket contains one at all.

    Args:
        name: An album name, already past year extraction.

    Returns:
        The name with any stale quality word removed -- any other tag
        sharing its bracket preserved -- and surrounding text tidied
        up. Returned unchanged if no quality word is present anywhere.
    """
    for wrapped_match in _ANY_WRAPPED_RE.finditer(name):
        paren_content, bracket_content = wrapped_match.group(1), wrapped_match.group(2)
        is_paren: bool = paren_content is not None
        content: str = paren_content if is_paren else bracket_content
        opening, closing = ("(", ")") if is_paren else ("[", "]")

        quality_match: re.Match[str] | None = _QUALITY_BARE_RE.search(content)
        if quality_match is None:
            continue

        remaining_content: str = content[: quality_match.start()] + content[quality_match.end() :]
        remaining_content = remaining_content.strip(" ,")
        remaining_content = _MULTI_SPACE_RE.sub(" ", remaining_content).strip()

        replacement: str = f"{opening}{remaining_content}{closing}" if remaining_content else ""
        remaining_name: str = (
            name[: wrapped_match.start()] + replacement + name[wrapped_match.end() :]
        )
        return _cleanup_name(remaining_name)

    bare_match: re.Match[str] | None = _QUALITY_BARE_RE.search(name)
    if bare_match is None:
        return name

    remaining: str = name[: bare_match.start()] + name[bare_match.end() :]
    return _cleanup_name(remaining)


def _is_lossless_m4a(path: Path) -> bool:
    """Determine whether a ".m4a" file is ALAC (lossless) or AAC (lossy).

    The ".m4a" extension is inherently ambiguous -- it's just an MP4
    container that can hold either codec -- so the extension alone can
    never answer this. This looks at the actual codec fourcc stored
    inside the file instead: mutagen reports `"alac"` for Apple
    Lossless, or an `"mp4a"`-prefixed value for AAC.

    Args:
        path: Path to a `.m4a` file.

    Returns:
        `True` if the file's codec is ALAC. `False` for AAC, or if the
        file can't be read/parsed at all -- treated as "not confirmed
        lossless" rather than raising, since one unreadable file
        shouldn't crash a whole discography scan.
    """
    try:
        info: object = MP4(path).info
    except (MutagenError, OSError):
        return False

    codec: object = getattr(info, "codec", None)
    if not isinstance(codec, str):
        return False
    return codec.lower().startswith("alac")


def _detect_audio_tier(album: Path) -> _AudioTier:
    """Determine an album's audio quality tier from its actual files.

    Never trusts any existing text marker in the folder name -- only
    real file content decides the tier. Scans recursively, so
    multi-disc albums split across "CD1"/"CD2" style subfolders are
    still detected correctly. Most extensions are unambiguous and
    decided from `_LOSSLESS_EXTENSIONS` alone; ".m4a" is the one
    exception, resolved per-file via `_is_lossless_m4a` since the
    extension by itself doesn't say whether it's ALAC or AAC.

    Args:
        album: Path to the album folder to scan.

    Returns:
        `"lossless"` if any file has an extension in
        `_LOSSLESS_EXTENSIONS`, or is a `.m4a` file confirmed to be
        ALAC; otherwise `"opus"` if any file has a `.opus` extension;
        otherwise `"lossy"`.
    """
    has_opus: bool = False
    for entry in album.rglob("*"):
        if not entry.is_file():
            continue
        suffix: str = entry.suffix.lower()
        if suffix in _LOSSLESS_EXTENSIONS:
            return "lossless"
        if suffix == ".m4a" and _is_lossless_m4a(entry):
            return "lossless"
        if suffix in _OPUS_EXTENSIONS:
            has_opus = True
    return "opus" if has_opus else "lossy"


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
