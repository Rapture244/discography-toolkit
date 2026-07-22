#!/usr/bin/env -S uv run
"""Write Album and Date/Year tags across every audio file in a discography.

For each album folder, the folder's full name (e.g. "01. (1994) - Kind of
Blue [FLAC]") is written verbatim as the Album tag on every audio file
inside it -- recursively, so multi-disc "CD1"/"CD2" subfolders are
covered. The year is separately extracted from that same folder name
(same "1994" / "199x" / "19xx" convention as albums_naming.py) and
written to the Date/Year tag.

A fully-known year is written as-is. An approximate year ("199x"/"19xx")
means the year is genuinely unknown, and each tagging system is asked to
represent that as honestly as it's able to:

- FLAC, OGG Vorbis, Opus, M4A, APE, WV, WMA -- these formats store tags
  as free-form text, so an empty Date/Year value is written and persists
  exactly as an empty string.
- MP3, WAV, AIFF, DSF, TTA -- these are ID3-based, and ID3's date frame
  enforces a strict numeric timestamp: it silently drops any value that
  isn't one, empty string included. There's no way to make it hold
  "unknown" as data, so no Date tag is written at all for these formats
  when the year is unknown -- and if a stale one already exists (e.g.
  from before this convention), it's removed. "Tag absent" is treated as
  the correct, idempotent end state, so re-runs stay stable rather than
  perpetually trying to write a value that can never actually persist.

Calin's discography layout keeps lossy albums as direct children of an
artist folder, alongside one sibling container named "FLAC" holding
every lossless album underneath it. That container is
bookkeeping, not an album: it is never tagged or treated as one itself.
Its children are pulled into the very same pool as the direct lossy
albums and tagged exactly like any other album, in place.

Supports FLAC, MP3, M4A, OGG Vorbis, Opus, WAV, AIFF, DSF, APE, WV, TTA,
and WMA. Idempotent per file: a file whose tags already match is left
untouched and reported separately, not re-saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, Literal, override

from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.dsf import DSF
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC
from mutagen.id3 import TALB, TDRC, ID3NoHeaderError
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.trueaudio import TrueAudio
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack
from rich.console import Console
from rich.progress import Progress, ProgressColumn, TaskProgressColumn, TextColumn
from rich.rule import Rule
from rich.text import Text
import typer

if TYPE_CHECKING:
    from mutagen import FileType
    from rich.progress import Task

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(add_completion=False, help=__doc__)


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Same year-token convention as albums_naming.py: 2 known century digits +
# 2 more chars, each a digit or "x" for unknown -- "1994", "199x", "19xx".
_YEAR_CORE: str = r"\d{2}[\dx]{2}"
_YEAR_WRAPPED_RE: re.Pattern[str] = re.compile(rf"\((?:{_YEAR_CORE})\)|\[(?:{_YEAR_CORE})\]")
_YEAR_BARE_RE: re.Pattern[str] = re.compile(rf"(?<!\d){_YEAR_CORE}(?!\d)")

# Every audio extension this script will tag -- lossless and lossy alike,
# unlike albums_naming.py's lossless-only detection set.
_LOSSLESS_EXTENSIONS: frozenset[str] = frozenset(
    {".flac", ".wav", ".ape", ".wv", ".tta", ".aiff", ".aif", ".dsf", ".dff"}
)
_LOSSY_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".m4a", ".ogg", ".opus", ".wma"})
_AUDIO_EXTENSIONS: frozenset[str] = _LOSSLESS_EXTENSIONS | _LOSSY_EXTENSIONS

# Calin's discography layout splits an artist folder into lossy albums
# directly inside it, plus one sibling container -- e.g.
# "FLAC" -- holding every lossless album underneath. Older names
# carried a count ("FLAC - (56 on 65)") and are still recognized, so
# this works whether or not 02.1 has normalized the folder yet. That
# container is bookkeeping, not an album: it starts with the word
# "FLAC" and, after that, contains nothing but digits, whitespace,
# hyphens, parens/brackets, and the literal word "on" (as in "56 on
# 65") -- never any other letter. The container itself is never
# touched; only its children are discovered as ordinary albums.
_FLAC_CONTAINER_RE: re.Pattern[str] = re.compile(r"^FLAC(?:[\s\-()\[\]0-9]|on)*$", re.IGNORECASE)

# ID3-based formats -- these are the ones where an empty Date value
# can't actually persist (see module docstring), so an approximate
# year means "no Date tag at all" instead of a literal empty string.
_ID3_BASED_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".aiff", ".aif", ".dsf", ".tta"})

# Files the operating system drops into folders unasked. A placeholder
# folder is still "empty" in every sense that matters if these are all
# it holds -- Windows in particular writes desktop.ini into any folder
# whose view settings are touched, so without this tolerance a
# placeholder would migrate into the "has files but no audio" bucket
# for reasons that have nothing to do with the collection.
_JUNK_FILENAMES: frozenset[str] = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})


@dataclass
class TagResult:
    """Outcome of attempting to tag a single audio file.

    Attributes:
        status: `"updated"` if either tag was written (or would be, in
            a dry run), `"already_correct"` if both tags already
            matched and nothing needed to change, or `"error"` if the
            file couldn't be read or tagged at all.
        album_changed: `True` if the Album tag was written (or would
            be). Always `False` when `status` is `"error"`, since the
            file was never actually read.
        year_changed: `True` if the Year/Date tag was written (or
            would be). Always `False` when `status` is `"error"`.
        detail: A human-readable reason, populated only for `"error"`.
    """

    status: Literal["updated", "already_correct", "error"]
    album_changed: bool = False
    year_changed: bool = False
    detail: str = ""


# ==================================================================================== #
#                                   PROGRESS DISPLAY                                   #
# ==================================================================================== #
class DashBarColumn(ProgressColumn):
    """A progress bar drawn as a run of dashes, in the style of `uv sync`.

    uv renders its bars with indicatif's `{bar:30.green/black.dim}` and
    `progress_chars("--")`: a fixed-width row of `-` characters that
    starts entirely dim and is repainted green from the left as the task
    advances. Rich's stock `BarColumn` draws solid block glyphs instead,
    so the bar is assembled by hand here.

    Because the character set is a single glyph rather than a ramp of
    partial blocks, the filled length is floored -- a dash only turns
    green once its share of the work is genuinely complete, which is the
    same rounding indicatif applies.

    Attributes:
        bar_width: Number of dash characters the bar occupies.
        complete_style: Rich style for the completed portion.
        remaining_style: Rich style for the not-yet-completed portion.
    """

    def __init__(
        self,
        bar_width: int = 30,
        complete_style: str = "green",
        remaining_style: str = "bright_black",
    ) -> None:
        """Initialize the column.

        Args:
            bar_width: Number of dash characters the bar occupies.
            complete_style: Rich style for the completed portion.
            remaining_style: Rich style for the remaining portion.
        """
        self.bar_width: int = bar_width
        self.complete_style: str = complete_style
        self.remaining_style: str = remaining_style
        super().__init__()

    @override
    def render(self, task: Task) -> Text:
        """Render the bar for one task at its current completion.

        Args:
            task: The task being rendered, supplying `completed` and
                `total`.

        Returns:
            A `Text` of `bar_width` dashes, split into a completed run
            and a remaining run styled independently.
        """
        total: float = task.total or 0
        fraction: float = 0.0 if total <= 0 else min(max(task.completed / total, 0.0), 1.0)
        filled: int = int(fraction * self.bar_width)

        return Text.assemble(
            ("-" * filled, self.complete_style),
            ("-" * (self.bar_width - filled), self.remaining_style),
        )


class FileCountColumn(ProgressColumn):
    """A `(completed/total files)` counter to sit beside the percentage.

    Rich keeps `Task.completed` as a `float`, so a plain
    `TextColumn("{task.completed}")` would render `58.0`; the count is
    coerced to `int` here. The running count is also right-aligned to
    the width of the total, so the column keeps a constant width as the
    counter gains digits instead of shifting the line beside it on every
    refresh.

    Attributes:
        style: Rich style for the counter, kept dim by default so the
            percentage stays the more prominent figure.
    """

    def __init__(self, style: str = "bright_black") -> None:
        """Initialize the column.

        Args:
            style: Rich style applied to the whole counter.
        """
        self.style: str = style
        super().__init__()

    @override
    def render(self, task: Task) -> Text:
        """Render the counter for one task at its current completion.

        Args:
            task: The task being rendered, supplying `completed` and
                `total`.

        Returns:
            A `Text` of the form `(  58/103 files)`, the running count
            padded to the width of the total.
        """
        total: int = int(task.total) if task.total else 0
        completed: int = int(task.completed)
        width: int = len(str(total))

        return Text(f"({completed:>{width}}/{total} files)", style=self.style)


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
@app.command()
def metadata(
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
        typer.Option("--dry-run", help="Show what would be tagged without touching anything."),
    ] = False,
) -> None:
    """Write Album (full folder name) and Date (extracted year) tags per album.

    Args:
        path: Absolute path to the folder containing album subfolders.
            Passed via `--path`/`-p`; if omitted, the user is
            prompted for it interactively instead.
        dry_run: If `True`, probe every file and report what would
            change without saving anything.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            no albums found, nothing to do, user abort, or a completed
            run), with an appropriate exit code attached.
    """
    if path is None:
        raw_input_path: str = typer.prompt("Enter the absolute path to your discography folder")
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    _echo_banner("Metadata: Album & Year", path.name)

    albums: list[Path] = _discover_albums(path)
    if not albums:
        typer.secho(f"No album folders found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    plan: list[tuple[Path, str, list[Path]]] = []
    skipped_no_year: list[Path] = []
    missing_albums: list[Path] = []
    skipped_no_audio: list[tuple[Path, list[str]]] = []

    for album in albums:
        year: str | None = _extract_year(album.name)
        if year is None:
            skipped_no_year.append(album)
            continue

        audio_files: list[Path] = _find_audio_files(album)
        if not audio_files:
            # No audio is two different situations wearing the same
            # face: a deliberate placeholder for an album that isn't
            # owned, or a folder that holds something unusable. Only
            # the second is a problem worth a warning.
            #
            # Neither is compared against the "M"/"⚠" marker in the
            # folder name. That reconciliation belongs to 01, which
            # derives the marker from the same files and rewrites the
            # name to match; re-deriving it here only creates a second
            # answer that can disagree with the first.
            if _is_effectively_empty(album):
                missing_albums.append(album)
            else:
                skipped_no_audio.append((album, _foreign_extensions(album)))
            continue

        date_value: str = "" if _is_approximate_year(year) else year
        plan.append((album, date_value, audio_files))

    if not plan:
        typer.secho("Nothing to tag.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()

    progress_columns = (
        TextColumn("[bold]{task.description}"),
        DashBarColumn(),
        TaskProgressColumn(),
        FileCountColumn(),
    )

    # One bar for the whole run, labelled with the folder that was
    # pointed at, rather than one bar per album -- the per-album detail
    # is already reported in the Album/Year table further down, so a bar
    # each would just list every album twice. Progress is counted in
    # files rather than albums so the bar reflects actual work done: a
    # 30-track live set doesn't advance at the same rate as a 4-track EP.
    total_files: int = sum(len(audio_files) for _, _, audio_files in plan)
    run_label: str = path.name or str(path)

    results: list[tuple[Path, str, list[Path], list[TagResult]]] = []
    with Progress(*progress_columns) as progress:
        task_id = progress.add_task(run_label, total=total_files)
        for album, date_value, audio_files in plan:
            outcomes: list[TagResult] = []
            for audio_file in audio_files:
                outcomes.append(_apply_tags(audio_file, album.name, date_value, dry_run=True))
                progress.update(task_id, advance=1)
            results.append((album, date_value, audio_files, outcomes))

    total: int = len(results)
    if total:
        tagged_count: int = sum(
            1 for _, _, _, outcomes in results if any(o.status == "updated" for o in outcomes)
        )
        clean_count: int = total - tagged_count
        tagged_pct: float = tagged_count / total * 100
        clean_pct: float = clean_count / total * 100
        count_width: int = len(str(total))

        # (indent, bold word, rest of line, color) -- the label word itself
        # is bold, the marker/colon/count/percentage stay regular weight,
        # both segments sharing the same color so the row still reads as one.
        summary_rows: list[tuple[str, str, str, str]] = [
            ("", "Total", f"{'':<9}: {total}", typer.colors.BRIGHT_MAGENTA),
            (
                "  ",
                "Tagged",
                f" (->) : {tagged_count:>{count_width}}  ({tagged_pct:.2f}%)",
                typer.colors.GREEN,
            ),
            (
                "  ",
                "Clean ",
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

    name_width: int = max((len(repr(album.name)) for album, _, _, _ in results), default=0)
    for album, date_value, _, outcomes in results:
        album_changed: bool = any(o.album_changed for o in outcomes)
        year_changed: bool = any(o.year_changed for o in outcomes)

        album_color: str = typer.colors.GREEN if album_changed else typer.colors.BLUE
        year_color: str = typer.colors.GREEN if year_changed else typer.colors.BLUE
        album_marker: str = "->" if album_changed else "=="
        year_marker: str = "->" if year_changed else "=="

        album_label: str = typer.style(f"Album {album_marker}", fg=album_color, bold=True)
        year_label: str = typer.style(f"Year {year_marker}", fg=year_color, bold=True)

        typer.echo(f"{album_label} {album.name!r:{name_width}}   {year_label} {date_value!r}")

    albums_with_errors: list[tuple[Path, list[tuple[Path, str]]]] = [
        (
            album,
            [
                (audio_file, outcome.detail)
                for audio_file, outcome in zip(audio_files, outcomes, strict=True)
                if outcome.status == "error"
            ],
        )
        for album, _, audio_files, outcomes in results
    ]
    albums_with_errors = [(album, errors) for album, errors in albums_with_errors if errors]
    if albums_with_errors:
        typer.secho(
            f"\n{len(albums_with_errors)} album(s) had file(s) that could not be read:\n",
            fg=typer.colors.RED,
        )
        for album, errors in albums_with_errors:
            typer.echo(f"  {album.name!r}")
            for audio_file, detail in errors:
                relative_path: Path = audio_file.relative_to(album)
                typer.echo(f"    {str(relative_path)!r} - {detail}")
            typer.echo()

    if missing_albums:
        # Yellow heading like the two anomaly sections below, but a
        # dimmed body where those keep theirs plain. An empty
        # placeholder is an expected, correct state -- the heading
        # earns attention because the count is worth knowing, while the
        # thirty names under it are reference, not a to-do list.
        typer.secho(
            f"\n{len(missing_albums)} album(s) not in the collection (empty placeholder):",
            fg=typer.colors.YELLOW,
        )
        for album in missing_albums:
            typer.secho(f"  {album.name!r}", fg=typer.colors.BRIGHT_BLACK)

    if skipped_no_audio:
        typer.secho(
            f"\n{len(skipped_no_audio)} album(s) with files but no usable audio (skipped):",
            fg=typer.colors.YELLOW,
        )
        for album, extensions in skipped_no_audio:
            typer.echo(f"  {album.name!r} - found: {', '.join(extensions)}")

    if skipped_no_year:
        typer.secho(
            f"\n{len(skipped_no_year)} album(s) with no detectable year (skipped entirely):",
            fg=typer.colors.YELLOW,
        )
        for album in skipped_no_year:
            typer.echo(f"  {album.name!r}")

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    total_to_update: int = sum(
        1 for _, _, _, outcomes in results for o in outcomes if o.status == "updated"
    )
    if total_to_update == 0:
        typer.secho(
            "\nEverything is already tagged correctly. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nWrite tags to {total_to_update} file(s)?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Same bar as the probe pass, over the files actually being written
    # rather than every file examined -- so it counts down the number
    # the prompt just quoted. Failures are collected instead of printed
    # as they happen: writing to the terminal inside a live progress
    # region tears the bar apart, and the reader wants them grouped at
    # the end anyway.
    written: int = 0
    failures: list[tuple[Path, str]] = []
    with Progress(*progress_columns) as progress:
        write_task = progress.add_task(run_label, total=total_to_update)
        for album, date_value, audio_files, outcomes in results:
            for audio_file, outcome in zip(audio_files, outcomes, strict=True):
                if outcome.status != "updated":
                    continue
                result = _apply_tags(audio_file, album.name, date_value, dry_run=False)
                if result.status == "error":
                    failures.append((audio_file, result.detail))
                else:
                    written += 1
                progress.update(write_task, advance=1)

    for audio_file, detail in failures:
        typer.secho(f"  Failed: {str(audio_file)!r} - {detail}", fg=typer.colors.RED)

    typer.secho(f"\nDone. {written} file(s) tagged.", fg=typer.colors.GREEN, bold=True)
    if failures:
        typer.secho(f"{len(failures)} file(s) failed during writing.", fg=typer.colors.RED)


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


def _is_effectively_empty(album: Path) -> bool:
    """Report whether an album folder holds nothing but OS bookkeeping.

    Searched recursively, so a placeholder containing only empty
    subfolders still counts as empty. Files listed in
    `_JUNK_FILENAMES` are ignored: they're written by the operating
    system rather than by the user, and their presence says nothing
    about whether the album is actually held.

    Args:
        album: The album folder to inspect.

    Returns:
        `True` if the folder contains no files at all beyond OS junk.
    """
    return not any(
        entry.is_file() and entry.name.lower() not in _JUNK_FILENAMES for entry in album.rglob("*")
    )


def _foreign_extensions(album: Path) -> list[str]:
    """List the distinct non-audio file extensions inside an album folder.

    Used to explain *why* a non-empty folder yielded no audio -- a
    stray `.cue`/`.log` pair reads very differently from a folder full
    of an unsupported format.

    Args:
        album: The album folder to inspect.

    Returns:
        Sorted, lowercased extensions of every non-junk file found.
        Files with no extension are reported as `"(no extension)"`.
    """
    extensions: set[str] = set()
    for entry in album.rglob("*"):
        if not entry.is_file() or entry.name.lower() in _JUNK_FILENAMES:
            continue
        extensions.add(entry.suffix.lower() or "(no extension)")

    return sorted(extensions)


def _discover_albums(root: Path) -> list[Path]:
    """Find album subdirectories inside a discography folder.

    A direct child that matches `_FLAC_CONTAINER_RE` -- "FLAC", or an
    older "FLAC - (56 on 65)" -- is bookkeeping, not an album: it is never
    included itself, and never tagged. Instead, one level inside it is
    discovered and its children are added exactly as if they sat
    directly under `root`, tagged in place just like any other album.

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


def _find_audio_files(album: Path) -> list[Path]:
    """Find every recognized audio file inside an album folder, recursively.

    Args:
        album: Path to the album folder to scan.

    Returns:
        Audio files anywhere under `album` (multi-disc subfolders
        included, hidden files excluded), sorted by path for stable,
        repeatable output.
    """
    files: list[Path] = [
        entry
        for entry in album.rglob("*")
        if entry.is_file()
        and entry.suffix.lower() in _AUDIO_EXTENSIONS
        and not entry.name.startswith(".")
    ]
    return sorted(files)


def _extract_year(name: str) -> str | None:
    """Find a year token in an album folder name, without removing it.

    Unlike albums_naming.py's version, this never modifies the name --
    the folder name is written to the Album tag verbatim, index, year,
    and FLAC marker included by design.

    Args:
        name: The album folder name.

    Returns:
        The 4-character year token (e.g. "1994", "199x"), or `None`
        if no year token could be found at all.
    """
    match: re.Match[str] | None = _YEAR_WRAPPED_RE.search(name)
    if match is None:
        match = _YEAR_BARE_RE.search(name)
    if match is None:
        return None
    return match.group(0).strip("()[]")


def _is_approximate_year(year: str) -> bool:
    """Check whether a year token is an approximation rather than a known year.

    Args:
        year: A year token as returned by :func:`_extract_year`, e.g.
            `"1994"`, `"199x"`, or `"19xx"`.

    Returns:
        `True` if the token contains an `x` placeholder digit,
        `False` for a fully-known year.
    """
    return "x" in year


def _apply_tags(path: Path, album: str, year: str, *, dry_run: bool) -> TagResult:
    """Read a file's current Album/Date tags and write new ones if needed.

    Dispatches per format, since the underlying tagging schemes genuinely
    differ (Vorbis comments, ID3, APEv2, ASF) rather than sharing one
    interface. Always reads first for an idempotency check; `dry_run`
    controls only whether the result is actually saved to disk -- the
    exact same read/compare logic runs either way, so a dry run's
    preview is provably what a real run would do.

    An empty `year` means "unknown" and is handled differently per
    format family: Vorbis/APEv2/ASF-based formats write it as a literal
    empty value, since that's a value they can actually hold. ID3-based
    formats (MP3/WAV/AIFF/DSF/TTA) can't -- an empty ID3 date silently
    fails to persist at all -- so for those, no Date tag is written at
    all, and an existing one is removed if present. Either way, "no
    Date tag" is the idempotent target state for those formats, not a
    literal empty string.

    Album and Year are checked and reported independently -- a file can
    have a correct Album but a stale Year (or vice versa), and the
    caller needs to know which.

    Args:
        path: The audio file to tag.
        album: The value to write as the Album tag (the album folder's
            full name, verbatim).
        year: The value to write as the Date/Year tag, or `""` if the
            year is unknown.
        dry_run: If `True`, determine the outcome but never call the
            underlying library's save.

    Returns:
        A :class:`TagResult` describing what happened or would happen.
    """
    suffix: str = path.suffix.lower()
    try:
        if suffix == ".flac":
            audio = FLAC(path)
            album_changed = _first(audio.get("album")) != album
            year_changed = _first(audio.get("date")) != year
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            audio["album"] = [album]
            audio["date"] = [year]

        elif suffix == ".ogg":
            audio = OggVorbis(path)
            album_changed = _first(audio.get("album")) != album
            year_changed = _first(audio.get("date")) != year
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            audio["album"] = [album]
            audio["date"] = [year]

        elif suffix == ".opus":
            audio = OggOpus(path)
            album_changed = _first(audio.get("album")) != album
            year_changed = _first(audio.get("date")) != year
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            audio["album"] = [album]
            audio["date"] = [year]

        elif suffix == ".mp3":
            audio = _open_easy_id3(path)
            mp3_target_date: str | None = year if year else None
            album_changed = _first(audio.get("album")) != album
            year_changed = _first(audio.get("date")) != mp3_target_date
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            audio["album"] = [album]
            if mp3_target_date is None:
                if "date" in audio:
                    del audio["date"]
            else:
                audio["date"] = [mp3_target_date]

        elif suffix == ".m4a":
            audio = EasyMP4(path)
            album_changed = _first(audio.get("album")) != album
            year_changed = _first(audio.get("date")) != year
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            audio["album"] = [album]
            audio["date"] = [year]

        elif suffix in {".wav", ".aiff", ".aif", ".dsf", ".tta"}:
            audio = _open_id3_container(path, suffix)
            id3_target_date: str | None = year if year else None
            album_changed = _id3_text(audio, "TALB") != album
            year_changed = _id3_text(audio, "TDRC") != id3_target_date
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            _set_id3_text(audio, "TALB", album)
            if id3_target_date is None:
                if audio.tags is not None:
                    audio.tags.delall("TDRC")
            else:
                _set_id3_text(audio, "TDRC", id3_target_date)

        elif suffix in {".ape", ".wv"}:
            audio = _open_apev2(path, suffix)
            album_changed = _first(audio.get("Album")) != album
            year_changed = _first(audio.get("Year")) != year
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            audio["Album"] = album
            audio["Year"] = year

        elif suffix == ".wma":
            audio = ASF(path)
            album_changed = _first_asf(audio, "WM/AlbumTitle") != album
            year_changed = _first_asf(audio, "WM/Year") != year
            if not album_changed and not year_changed:
                return TagResult("already_correct")
            audio["WM/AlbumTitle"] = [album]
            audio["WM/Year"] = [year]

        else:
            return TagResult("error", detail=f"Unsupported audio format: {suffix}")

        if not dry_run:
            audio.save()

    except Exception as exc:  # noqa: BLE001 - any file can be unreadable/corrupt in ways not worth enumerating
        return TagResult("error", detail=str(exc))

    return TagResult("updated", album_changed=album_changed, year_changed=year_changed)


def _first(values: list[str] | None) -> str | None:
    """Return the first element of a mutagen-style value list, or `None`.

    Args:
        values: A tag value as mutagen returns it -- a list of strings,
            or `None` if the tag isn't present at all.

    Returns:
        The first string in the list, or `None` if the list is empty
        or `None`.
    """
    if not values:
        return None
    return values[0]


def _open_easy_id3(path: Path):
    """Open an MP3's tags via mutagen's EasyID3, creating a tag if absent.

    Args:
        path: The MP3 file to open.

    Returns:
        An `EasyID3` instance ready for dict-style `["album"]` access.
    """
    try:
        return EasyID3(path)
    except ID3NoHeaderError:
        mp3_audio = MP3(path, ID3=EasyID3)
        mp3_audio.add_tags()
        return mp3_audio


def _open_id3_container(path: Path, suffix: str) -> FileType:
    """Open a WAV/AIFF/DSF file's raw ID3 tags, creating a tag if absent.

    These formats carry ID3 like MP3 does, but without an Easy wrapper --
    tags are set via explicit frame objects (`TALB`, `TDRC`) rather
    than plain string keys.

    Args:
        path: The audio file to open.
        suffix: The file's lowercase extension, used to pick the right
            mutagen class.

    Returns:
        A mutagen file object with a populated `.tags` (ID3) attribute.
    """
    if suffix == ".wav":
        audio_cls = WAVE
    elif suffix in {".aiff", ".aif"}:
        audio_cls = AIFF
    elif suffix == ".tta":
        audio_cls = TrueAudio
    else:
        audio_cls = DSF

    audio = audio_cls(path)
    if audio.tags is None:
        audio.add_tags()
    return audio


def _id3_text(audio, frame_id: str) -> str | None:
    """Read the text value of a raw ID3 frame, if present.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.
        frame_id: The ID3 frame identifier, e.g. `"TALB"`.

    Returns:
        The frame's first text value, or `None` if the frame is absent.
    """
    frame = audio.tags.get(frame_id)
    if frame is None or not frame.text:
        return None
    return str(frame.text[0])


def _set_id3_text(audio, frame_id: str, value: str) -> None:
    """Set a raw ID3 frame's text value, replacing any existing frame.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.
        frame_id: The ID3 frame identifier, e.g. `"TALB"`.
        value: The text to write into the frame.
    """
    frame_cls = TALB if frame_id == "TALB" else TDRC
    audio.tags.setall(frame_id, [frame_cls(encoding=3, text=[value])])


def _open_apev2(path: Path, suffix: str) -> FileType:
    """Open an APEv2-tagged file (APE/WV), creating a tag if absent.

    Args:
        path: The audio file to open.
        suffix: The file's lowercase extension, used to pick the right
            mutagen class.

    Returns:
        A mutagen file object with a dict-like, APEv2-backed `.tags`.
    """
    audio_cls = MonkeysAudio if suffix == ".ape" else WavPack

    audio = audio_cls(path)
    if audio.tags is None:
        audio.add_tags()
    return audio


def _first_asf(audio: ASF, key: str) -> str | None:
    """Read the first value of an ASF/WMA attribute, if present.

    Args:
        audio: A mutagen `ASF` file object.
        key: The ASF attribute name, e.g. `"WM/AlbumTitle"`.

    Returns:
        The attribute's value as a string, or `None` if absent.
    """
    values = audio.get(key)
    if not values:
        return None
    return str(values[0])


# ==================================================================================== #
#                                     ENTRY POINT                                      #
# ==================================================================================== #
if __name__ == "__main__":
    app()
