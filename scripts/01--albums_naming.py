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
from titlecase import titlecase
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
# Only ever applied to text *inside* a bracket, where a quality word is
# unambiguous -- see _QUALITY_TRAILING_RE for the unbracketed case.
_QUALITY_BARE_RE: re.Pattern[str] = re.compile(r"\b(?:FLAC|OPUS)\b", re.IGNORECASE)

# An unbracketed quality word left over from before the convention
# settled, e.g. "Some Album FLAC". Matching this loosely is dangerous:
# "Opus" is an ordinary word in album titles ("Opus de Jazz", "Opus
# One", "Magnum Opus"), and a case-insensitive search anywhere in the
# name silently deletes it from the title. Two guards make that
# impossible:
#
# - anchored to the end of the name, since a trailing position is the
#   only place a stale quality marker actually sits, while a title word
#   is almost always followed by more title, and
# - case-sensitive, since the convention writes the codec in caps
#   ("FLAC"/"OPUS") whereas a title writes the word normally ("Opus").
#
# The trade is deliberate: a marker written as lowercase "flac" is left
# alone, which shows up harmlessly in the dry run and is fixed by hand.
# The opposite error destroys a word of the title and can't be undone.
_QUALITY_TRAILING_RE: re.Pattern[str] = re.compile(r"\s*\b(?:FLAC|OPUS)\s*$")

# Calin's "missing album" convention: an album known to exist but not
# held keeps an empty placeholder folder, marked between the year and
# the title. A plain "M" is the settled state -- the album is declared
# missing and the folder is indeed empty. "⚠" is a conflict: the name
# declares the album missing while the folder holds audio.
#
# The conflict goes into the folder name rather than only into a
# report because the folder name is where Calin actually reads the
# collection; a terminal report scrolls away.
#
# Neither resolution is a decision the tool can make for him: the
# conflict clears either by deleting stray files (back to "M") or by
# dropping the marker by hand (the album is held, and picks up its
# quality tag on the next run). So the marker states the problem and
# stops there.
_MISSING_MARKER: str = "M"
_MISSING_CONFLICT_MARKER: str = "⚠"

# Reads either marker where it lands after year extraction. The older
# "(1967) M - Title" and the current "(1967) - M - Title" both arrive
# here as "M - Title", since _cleanup_name has already trimmed the
# leading separator, so one pattern covers both and the rebuild emits
# only the current spelling.
#
# Anchored, and requires the hyphen: a title that merely starts with M
# ("Mirror", "Miles Smiles") can't match, and an album actually titled
# "M" has no hyphen after it, so it stays a title.
_MISSING_MARKER_RE: re.Pattern[str] = re.compile(r"^(?:⚠|M)\s*-\s*")

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

# Every remaining extension that still counts as audio. These never
# affect the quality tag -- a lossy album carries no marker at all --
# but they matter for deciding whether a folder holds *any* audio,
# which `_detect_audio_tier` can't answer: it reports "lossy" both for
# an album of MP3s and for a folder with nothing in it whatsoever.
_LOSSY_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".m4a", ".ogg", ".wma"})

# The three recognized audio quality tiers, in priority order when
# deciding a suffix: lossless beats opus beats the plain lossy default.
_AudioTier = Literal["lossless", "opus", "lossy", "none"]

# One rendered line of the summary box: indent, bold label, marker,
# count, color. `None` stands for a horizontal rule between the two
# partitions rather than a row of figures.
_SummaryRow = tuple[str, str, str, int, str] | None


# Leftover separator characters (whitespace, hyphen, underscore, dot) at
# the very start or end of a name, after a token has been cut out.
_EDGE_SEPARATORS_RE: re.Pattern[str] = re.compile(r"^[\s._-]+|[\s._-]+$")

# Two or more consecutive whitespace characters, left behind when a token
# is removed from the *middle* of a name rather than an edge.
_MULTI_SPACE_RE: re.Pattern[str] = re.compile(r"\s{2,}")


# Terminal columns occupied by the conflict glyph when drawn. U+26A0 is
# classified East Asian Ambiguous, which means the Unicode standard
# declines to fix its width -- terminals are free to draw it one column
# or two, and len() in Python always reports one character regardless.
# The summary box pads every row to a common width, so a mismatch here
# puts the conflict row's right border out of line by the difference.
#
# Set to 1, confirmed against Windows Terminal by comparing "|\u26a0|"
# with "|X|": the bars line up, so the glyph is drawn narrow. A terminal
# that gives it emoji presentation draws it two columns wide and wants
# 2 here -- worth re-checking if this ever runs from a Linux console.
_CONFLICT_GLYPH_COLUMNS: int = 1


def _display_width(text: str) -> int:
    """Measure how many terminal columns a string occupies when drawn.

    Differs from `len` only for the conflict glyph, which counts as one
    character but is usually drawn two columns wide. Everything else in
    the summary box is ASCII, where the two agree.

    Args:
        text: The string to measure.

    Returns:
        The number of terminal columns the string is expected to fill.
    """
    extra: int = text.count(_MISSING_CONFLICT_MARKER) * (_CONFLICT_GLYPH_COLUMNS - 1)
    return len(text) + extra


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
    marker_conflicts: list[Path] = []
    newly_marked_missing: list[Path] = []
    missing_albums: list[Path] = []
    for album in albums:
        mark, after_mark = _split_caline_mark(album.name)
        index_prefix, content = _split_existing_index(after_mark)
        year, after_year = _extract_year(content)
        if year is None:
            skipped.append(album)
            continue

        is_missing, after_marker = _split_missing_marker(after_year)

        after_quality_strip: str = _strip_quality_marker(after_marker)
        title: str = _title_case(after_quality_strip)

        # "M" is a claim that the album isn't held, so the files decide
        # it, not the name. An empty folder is missing whatever the name
        # says; a folder with audio isn't. The only case the tool won't
        # settle by itself is a name claiming missing over a folder that
        # holds audio -- that could mean the album was acquired and
        # never renamed, or that stray files landed somewhere they
        # shouldn't, and those resolve in opposite directions. It gets
        # the conflict marker and a human gets the decision.
        #
        # A marked album carries no quality tag either way: on an empty
        # folder there's no file to describe, and on a conflicted one
        # the name's claim is exactly what's in doubt.
        tier: _AudioTier = _detect_audio_tier(album)

        if tier == "none":
            name_marker: str = _MISSING_MARKER
            quality_suffix: str = ""
            missing_albums.append(album)
            if not is_missing:
                newly_marked_missing.append(album)
        elif is_missing:
            name_marker = _MISSING_CONFLICT_MARKER
            quality_suffix = ""
            marker_conflicts.append(album)
        else:
            name_marker = ""
            quality_suffix = {"lossless": " [FLAC]", "opus": " [OPUS]", "lossy": ""}[tier]

        marker_prefix: str = f"{name_marker} - " if name_marker else ""
        new_name: str = f"{mark}{index_prefix}({year}) - {marker_prefix}{title}{quality_suffix}"
        plan.append((album, new_name))

    typer.secho(f"\n{len(plan)} album(s) to process\n", bold=True)

    total: int = len(plan)
    if total:
        renamed_count: int = sum(1 for album, new_name in plan if album.name != new_name)
        clean_count: int = total - renamed_count
        missing_count: int = len(missing_albums)
        conflict_count: int = len(marker_conflicts)
        held_count: int = total - missing_count - conflict_count
        count_width: int = len(str(total))

        # Two independent partitions over the same albums, separated by
        # a rule: what this run *did* (renamed/clean), then what the
        # collection *is* (held/missing/conflicted). Each block sums to
        # Total on its own, which is the whole reason for the divider --
        # listed as five flat siblings they'd read as one tally adding
        # up to well over 100%.
        #
        # Conflict is shown even at zero, green rather than magenta. A
        # row that only appears on failure is one that's never been
        # seen, and so gets misread in exactly the moment it matters; a
        # row always in the same place makes a clean run say so.
        #
        # The marker column shows the glyph as it actually appears in
        # the folder name. Padding it goes through _display_width rather
        # than len(), since the conflict glyph occupies more columns than
        # it has characters.
        conflict_color: str = (
            typer.colors.GREEN if conflict_count == 0 else typer.colors.BRIGHT_MAGENTA
        )

        # (indent, bold label, marker, count, color) -- the label is bold
        # while the marker/count/percentage stay regular weight, all in
        # one color so each row still reads as a single line. A `None`
        # entry is a horizontal rule.
        summary_rows: list[_SummaryRow] = [
            ("", "Total", "", total, typer.colors.BRIGHT_MAGENTA),
            None,
            ("  ", "Renamed", "(->)", renamed_count, typer.colors.GREEN),
            ("  ", "Clean", "(==)", clean_count, typer.colors.BLUE),
            None,
            ("  ", "Held", "", held_count, typer.colors.GREEN),
            ("  ", "Missing", "(M)", missing_count, typer.colors.BLUE),
            ("  ", "Conflict", f"({_MISSING_CONFLICT_MARKER})", conflict_count, conflict_color),
        ]

        entries: list[tuple[str, str, str, int, str]] = [
            row for row in summary_rows if row is not None
        ]
        label_width: int = max(_display_width(indent + label) for indent, label, _, _, _ in entries)
        marker_width: int = max(_display_width(marker) for _, _, marker, _, _ in entries)

        def _rest(indent: str, label: str, marker: str, count: int) -> str:
            """Build the non-bold remainder of a summary row."""
            pad: str = " " * (label_width - _display_width(indent + label))
            marker_cell: str = marker + " " * (marker_width - _display_width(marker))
            body: str = f"{pad} {marker_cell} : {count:>{count_width}}"
            if label == "Total":
                return body
            return f"{body}  ({count / total * 100:>6.2f}%)"

        rendered: list[tuple[str, str, str, str] | None] = [
            None if row is None else (row[0], row[1], _rest(row[0], row[1], row[2], row[3]), row[4])
            for row in summary_rows
        ]
        box_width: int = max(
            _display_width(indent + label + rest)
            for row in rendered
            if row is not None
            for indent, label, rest, _ in [row]
        )

        typer.echo("┌" + "─" * (box_width + 2) + "┐")
        for row in rendered:
            if row is None:
                typer.echo("├" + "─" * (box_width + 2) + "┤")
                continue
            indent, label, rest, color = row
            pad = " " * (box_width - _display_width(indent + label + rest))
            styled: str = (
                indent + typer.style(label, fg=color, bold=True) + typer.style(rest, fg=color) + pad
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

    if newly_marked_missing:
        typer.secho(
            f"\n{len(newly_marked_missing)} empty album(s) newly marked missing:",
            fg=typer.colors.BLUE,
        )
        for album in newly_marked_missing:
            typer.secho(f"  {album.name!r}", fg=typer.colors.BLUE)

    if marker_conflicts:
        conflict_note: str = (
            "marked missing but holding audio (flagged with the warning sign -- resolve by hand)"
        )
        typer.secho(
            f"\n{len(marker_conflicts)} album(s) {conflict_note}:",
            fg=typer.colors.BRIGHT_MAGENTA,
            bold=True,
        )
        for album in marker_conflicts:
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


def _preserve_acronym(word: str, **_kwargs: object) -> str | None:
    """Keep an all-caps word as written, for `titlecase`'s callback hook.

    The library lowercases runs of capitals -- "OST" becomes "Ost",
    "NASA" becomes "Nasa" -- which is wrong for the tags Calin's names
    actually carry ("[FLAC, OST]", "[LIVE]") and for any acronym in a
    title. A word already written in full caps is treated as
    deliberate and left exactly as it is.

    The guard is `len > 1` so a lone capital still goes through the
    library's own logic, which is what correctly keeps "Vol. I" and
    "Vol. II" as roman numerals rather than folding them to "Vol. Ii".

    Args:
        word: A single word being considered by `titlecase`.
        **_kwargs: Positional context passed by the library (`all_caps`
            and so on), unused here.

    Returns:
        The word unchanged if it's an all-caps run of more than one
        character, otherwise `None` to defer to the library's default
        handling.
    """
    if len(word) > 1 and word.isupper():
        return word
    return None


def _title_case(name: str) -> str:
    """Apply classic English title case to an album title.

    Delegates to the `titlecase` library, which implements John
    Gruber's algorithm: minor words ("of", "the", "a") stay lowercase
    unless they lead the title, while apostrophes and embedded
    punctuation survive intact -- `str.title()` would produce "It'S"
    and "Vol. Ii" here.

    The convention is English, and applying it to a German or French
    title produces something merely capitalized rather than correct
    ("Warum bist du traurig" becomes "Warum Bist Du Traurig"). No
    library can settle that without knowing the language, so the dry
    run is the place to catch it. Scripts without letter case at all,
    such as Japanese and Chinese, pass through untouched.

    The acronym guard applies only when the title contains some
    lowercase. A name written entirely in capitals is far likelier to
    be shouted than to be one long acronym, so "THE MAN WITH THE HORN"
    normalizes, while the "OST" in "Miles Ahead [FLAC, OST]" is read as
    an acronym precisely because the words around it are not capitals.

    Args:
        name: The album title, with year, markers and quality tag
            already split off.

    Returns:
        The title in title case, acronyms preserved.
    """
    is_shouted: bool = name == name.upper() and name != name.lower()
    if is_shouted:
        return titlecase(name)

    return titlecase(name, callback=_preserve_acronym)


def _split_missing_marker(name: str) -> tuple[bool, str]:
    """Split a leading "missing" marker off an album name.

    Accepts the marker in either spelling -- the older "M - Title" and
    the current "- M - Title" both arrive here as "M - Title", since
    year extraction has already trimmed the leading separator -- and
    reports it as a flag so the rebuild can re-emit it in one canonical
    position.

    Args:
        name: An album name with the year already removed, e.g.
            `"M - Folk Soul"` or `"Mirror"`.

    Returns:
        A `(is_missing, remainder)` pair. `remainder` is the name with
        the marker and its separator removed; it is the name unchanged
        when no marker is present.
    """
    match: re.Match[str] | None = _MISSING_MARKER_RE.match(name)
    if match is None:
        return False, name

    return True, name[match.end() :]


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

    bare_match: re.Match[str] | None = _QUALITY_TRAILING_RE.search(name)
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
        otherwise `"lossy"` if any other recognized audio file is
        present; otherwise `"none"`, meaning the folder holds no audio
        at all. That last case is kept distinct from `"lossy"` because
        the two are opposite facts about the collection -- an album
        held in a lossy format, versus an album not held -- even though
        neither earns a quality tag in the name.
    """
    has_opus: bool = False
    has_lossy: bool = False
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
        elif suffix in _LOSSY_EXTENSIONS:
            has_lossy = True

    if has_opus:
        return "opus"
    return "lossy" if has_lossy else "none"


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
