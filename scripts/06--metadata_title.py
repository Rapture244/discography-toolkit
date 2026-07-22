#!/usr/bin/env -S uv run
"""Title-case the Title tag of every audio file beneath a given path.

Reads each file's existing Title tag, rewrites it in classic English
title case, and saves it back only when the result differs. Nothing is
derived from folder or file names: the value's source is the file's own
tag, and this only changes its capitalization.

The casing rule is the same one `01--albums_naming.py` applies to album
folders and `02.1--albums_track_naming.py` applies to filenames, so a
track's Title tag and the file holding it end up cased identically. A
word written in full capitals is treated as deliberate and kept -- an
"OST" or "ESP" survives -- while a title written entirely in capitals
is read as shouting and normalized.

The convention is English, so a German or French title comes out
capitalized rather than correct ("warum bist du traurig" becomes "Warum
Bist Du Traurig"). No library can settle that without knowing the
language; the dry run is where to catch it. Scripts without letter case
at all, such as Japanese and Chinese, pass through untouched.

Files carrying no Title tag are reported rather than silently skipped:
there is nothing to case, but an untitled track is worth knowing about.

The path is scope and nothing more -- point it at one album, one
artist, or a whole shelf.

Supports FLAC, MP3, M4A, OGG Vorbis, Opus, WAV, AIFF, DSF, APE, WV,
TTA, and WMA. Idempotent per file: a title already correctly cased is
left untouched and reported separately, not re-saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, cast, override

from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.dsf import DSF
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC
from mutagen.id3 import TIT2
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
from titlecase import titlecase
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
# Every audio extension this script will touch.
_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".flac",
        ".wav",
        ".ape",
        ".wv",
        ".tta",
        ".aiff",
        ".aif",
        ".dsf",
        ".dff",
        ".mp3",
        ".m4a",
        ".ogg",
        ".opus",
        ".wma",
    }
)

# Formats storing tags as ID3 frames. MP3 is handled here with the rest
# rather than through mutagen's Easy interface, so every ID3 format goes
# down one code path and reads the frame text exactly as stored.
_ID3_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".aiff", ".aif", ".dsf", ".tta"})


@dataclass
class TitleResult:
    """Outcome of examining one audio file's Title tag.

    Attributes:
        status: `"cased"` if the title changes (or would, in a dry
            run), `"already_correct"` if it was already right,
            `"no_title"` if the file carries no Title tag at all, or
            `"error"` if the file couldn't be read or written.
        before: The title as found, for reporting.
        after: The title as it would be written.
        detail: A human-readable reason, populated only for `"error"`.
    """

    status: Literal["cased", "already_correct", "no_title", "error"]
    before: str = ""
    after: str = ""
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
    counter gains digits instead of shifting the line beside it.

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
def retitle(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Absolute path to work beneath. Any folder: album, artist, or a whole shelf.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without touching anything."),
    ] = False,
) -> None:
    """Title-case the Title tag of every audio file beneath a path.

    Args:
        path: Absolute path to work beneath. Passed via `--path`/`-p`;
            if omitted, the user is prompted for it interactively.
        dry_run: If `True`, probe every file and report what would
            change without saving anything.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            no audio found, nothing to do, user abort, or a completed
            run), with an appropriate exit code attached.
    """
    if path is None:
        raw_input_path: str = cast(str, typer.prompt("Enter the absolute path to work beneath"))
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    _echo_banner("Metadata: Title", path.name)

    tracks: list[Path] = _find_audio_files(path)
    if not tracks:
        typer.secho(f"\nNo audio files found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    progress_columns = (
        TextColumn("[bold]{task.description}"),
        DashBarColumn(),
        TaskProgressColumn(),
        FileCountColumn(),
    )
    run_label: str = path.name or str(path)

    outcomes: list[tuple[Path, TitleResult]] = []
    with Progress(*progress_columns) as progress:
        probe_task = progress.add_task(run_label, total=len(tracks))
        for track in tracks:
            outcomes.append((track, _apply_title(track, dry_run=True)))
            progress.update(probe_task, advance=1)

    total: int = len(outcomes)
    cased_count: int = sum(1 for _, r in outcomes if r.status == "cased")
    clean_count: int = sum(1 for _, r in outcomes if r.status == "already_correct")
    untitled_count: int = sum(1 for _, r in outcomes if r.status == "no_title")
    error_count: int = sum(1 for _, r in outcomes if r.status == "error")
    _echo_summary(
        total=total,
        cased=cased_count,
        clean=clean_count,
        untitled=untitled_count,
        errors=error_count,
    )

    # The per-file listing is shown only on a dry run. Every change here
    # is a distinct value worth reading before it is written, but once
    # the decision is made the same list is several hundred lines of
    # confirmation nobody reads.
    if dry_run:
        _echo_planned_titles(outcomes)

    if untitled_count:
        typer.secho(f"\n{untitled_count} file(s) carry no Title tag:", fg=typer.colors.YELLOW)
        for track, result in outcomes:
            if result.status == "no_title":
                typer.secho(f"  {str(track)!r}", fg=typer.colors.BRIGHT_BLACK)

    if error_count:
        typer.secho(f"\n{error_count} file(s) could not be read:", fg=typer.colors.YELLOW)
        for track, result in outcomes:
            if result.status == "error":
                typer.echo(f"  {str(track)!r} - {result.detail}")

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not cased_count:
        typer.secho(
            "\nEvery Title is already cased correctly. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nRewrite {cased_count} Title tag(s)?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Failures are collected rather than printed as they happen: writing
    # to the terminal inside a live progress region tears the bar apart,
    # and grouping them at the end reads better anyway.
    written: int = 0
    failures: list[tuple[Path, str]] = []
    with Progress(*progress_columns) as progress:
        write_task = progress.add_task(run_label, total=cased_count)
        for track, probe in outcomes:
            if probe.status != "cased":
                continue
            result: TitleResult = _apply_title(track, dry_run=False)
            if result.status == "error":
                failures.append((track, result.detail))
            else:
                written += 1
            progress.update(write_task, advance=1)

    for track, detail in failures:
        typer.secho(f"  Failed: {str(track)!r} - {detail}", fg=typer.colors.RED)

    typer.secho(f"\nDone. {written} Title tag(s) rewritten.", fg=typer.colors.GREEN, bold=True)
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


def _find_audio_files(root: Path) -> list[Path]:
    """Collect every audio file anywhere beneath the given path.

    A flat recursive walk with no knowledge of the discography layout,
    which is what lets the path be any scope: one album, one artist, or
    a whole shelf holding many artists.

    Hidden files and anything inside a hidden folder are skipped, the
    same way the rest of the pipeline skips them. That covers macOS
    "._track.flac" companion stubs, which carry an audio extension
    without being audio.

    Args:
        root: The folder to scan.

    Returns:
        Audio files beneath `root`, sorted by path.
    """
    return sorted(
        entry
        for entry in root.rglob("*")
        if entry.is_file()
        and entry.suffix.lower() in _AUDIO_EXTENSIONS
        and not any(part.startswith(".") for part in entry.relative_to(root).parts)
    )


def _preserve_acronym(word: str, **_kwargs: object) -> str | None:
    """Keep an all-caps word as written, for `titlecase`'s callback hook.

    The library lowercases runs of capitals -- "OST" becomes "Ost" --
    which is wrong for an acronym in a track title. A word already
    written in full caps is treated as deliberate and left as it is.

    The guard is `len > 1` so a lone capital still goes through the
    library's own logic, which is what correctly keeps "Pt. I" and
    "Pt. II" as roman numerals rather than folding them to "Pt. Ii".

    Args:
        word: A single word being considered by `titlecase`.
        **_kwargs: Positional context passed by the library, unused.

    Returns:
        The word unchanged if it's an all-caps run of more than one
        character, otherwise `None` to defer to the library's default.
    """
    if len(word) > 1 and word.isupper():
        return word
    return None


def _title_case(title: str) -> str:
    """Apply classic English title case to a track title.

    Delegates to the `titlecase` library, which implements John
    Gruber's algorithm: minor words ("of", "the", "a") stay lowercase
    unless they lead the title, while apostrophes and embedded
    punctuation survive intact -- `str.title()` would produce "It'S"
    and "Pt. Ii" here.

    The acronym guard applies only when the title contains some
    lowercase. A title written entirely in capitals is far likelier to
    be shouted than to be one long acronym, so "THE MAN WITH THE HORN"
    normalizes, while the "OST" in "Theme From OST" is recognized as an
    acronym precisely because the words around it are not capitals.

    Args:
        title: The Title tag's current value.

    Returns:
        The title in title case, acronyms preserved.
    """
    is_shouted: bool = title == title.upper() and title != title.lower()
    if is_shouted:
        return titlecase(title)

    return titlecase(title, callback=_preserve_acronym)


def _apply_title(path: Path, *, dry_run: bool) -> TitleResult:
    """Read, case and optionally write one file's Title tag.

    Args:
        path: The audio file to examine.
        dry_run: If `True`, decide what would change but never save.

    Returns:
        A `TitleResult` describing the outcome, carrying the before and
        after values so the caller can report them.
    """
    suffix: str = path.suffix.lower()

    try:
        if suffix in {".flac", ".ogg", ".opus"}:
            audio = _open_vorbis(path, suffix)
            current = _first(audio.get("title"))
            cased = _cased_or_none(current)
            if cased is None:
                return _unchanged(current)
            audio["title"] = [cased]

        elif suffix == ".m4a":
            audio = EasyMP4(path)
            current = _first(audio.get("title"))
            cased = _cased_or_none(current)
            if cased is None:
                return _unchanged(current)
            audio["title"] = [cased]

        elif suffix in _ID3_EXTENSIONS:
            audio = _open_id3_container(path, suffix)
            current = _id3_text(audio)
            cased = _cased_or_none(current)
            if cased is None:
                return _unchanged(current)
            _set_id3_title(audio, cased)

        elif suffix in {".ape", ".wv"}:
            audio = _open_apev2(path, suffix)
            current = _first(audio.get("Title"))
            cased = _cased_or_none(current)
            if cased is None:
                return _unchanged(current)
            audio["Title"] = cased

        elif suffix == ".wma":
            audio = ASF(path)
            current = _first_asf(audio, "Title")
            cased = _cased_or_none(current)
            if cased is None:
                return _unchanged(current)
            audio["Title"] = [cased]

        else:
            return TitleResult("error", detail=f"Unsupported audio format: {suffix}")

        if not dry_run:
            audio.save()

    except Exception as exc:  # noqa: BLE001 - any file can be unreadable/corrupt in ways not worth enumerating
        return TitleResult("error", detail=str(exc))

    return TitleResult("cased", before=current or "", after=cased)


def _cased_or_none(current: str | None) -> str | None:
    """Return the cased title, or `None` when nothing needs writing.

    Args:
        current: The Title tag as found, or `None` if absent.

    Returns:
        The title-cased value when it differs from `current`, otherwise
        `None` -- which the caller reads as "no write needed", covering
        both an absent title and one already correct.
    """
    if not current:
        return None

    cased: str = _title_case(current)
    return None if cased == current else cased


def _unchanged(current: str | None) -> TitleResult:
    """Build the result for a file that needs no write.

    Args:
        current: The Title tag as found, or `None` if absent.

    Returns:
        `"no_title"` when the tag is missing or empty, otherwise
        `"already_correct"`.
    """
    if not current:
        return TitleResult("no_title")
    return TitleResult("already_correct", before=current, after=current)


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
    return str(values[0])


def _open_vorbis(path: Path, suffix: str) -> FileType:
    """Open a file whose tags are Vorbis comments.

    Args:
        path: The audio file to open.
        suffix: The file's lowercase extension, used to pick the right
            mutagen class.

    Returns:
        A mutagen file object with dict-like, free-form string tags.
    """
    if suffix == ".flac":
        return FLAC(path)
    if suffix == ".opus":
        return OggOpus(path)
    return OggVorbis(path)


def _open_id3_container(path: Path, suffix: str) -> FileType:
    """Open an ID3-tagged file, creating a tag block if absent.

    Covers MP3 alongside the containers that borrow ID3, so all of them
    go through raw frames rather than an Easy wrapper and read exactly
    what is stored.

    Args:
        path: The audio file to open.
        suffix: The file's lowercase extension, used to pick the right
            mutagen class.

    Returns:
        A mutagen file object with a populated `.tags` (ID3) attribute.
    """
    if suffix == ".mp3":
        audio_cls = MP3
    elif suffix == ".wav":
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


def _id3_text(audio) -> str | None:
    """Read the text of an ID3 title frame, if present.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.

    Returns:
        The frame's first text value, or `None` if the frame is absent.
    """
    frame = audio.tags.get("TIT2")
    if frame is None or not frame.text:
        return None
    return str(frame.text[0])


def _set_id3_title(audio, value: str) -> None:
    """Set the ID3 title frame's text, replacing any existing frame.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.
        value: The title text to write.
    """
    audio.tags.setall("TIT2", [TIT2(encoding=3, text=[value])])


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
        key: The ASF attribute name, e.g. `"Title"`.

    Returns:
        The attribute's value as a string, or `None` if absent.
    """
    values = audio.get(key)
    if not values:
        return None
    return str(values[0])


def _echo_planned_titles(outcomes: list[tuple[Path, TitleResult]]) -> None:
    """List the titles that would change, old beside new.

    Only changed titles are shown. A library of five hundred correctly
    cased tracks should print nothing here rather than five hundred
    lines confirming it.

    Args:
        outcomes: Every file examined, paired with its result.
    """
    changes: list[TitleResult] = [r for _, r in outcomes if r.status == "cased"]
    if not changes:
        return

    before_width: int = max(len(repr(r.before)) for r in changes)
    arrow: str = typer.style("->", fg=typer.colors.GREEN)

    typer.echo()
    for result in changes:
        typer.echo(f"  {result.before!r:{before_width}} {arrow} {result.after!r}")


def _echo_summary(*, total: int, cased: int, clean: int, untitled: int, errors: int) -> None:
    """Print the summary box.

    Args:
        total: Audio files examined.
        cased: Files whose Title changes.
        clean: Files already correctly cased.
        untitled: Files carrying no Title tag.
        errors: Files that could not be read.
    """
    if not total:
        return

    count_width: int = len(str(total))
    rows: list[tuple[str, str, str, int, str]] = [
        ("", "Total", "", total, typer.colors.BRIGHT_MAGENTA),
        ("  ", "Cased", "(->)", cased, typer.colors.GREEN),
        ("  ", "Clean", "(==)", clean, typer.colors.BLUE),
    ]
    if untitled:
        rows.append(("  ", "Untitled", "(--)", untitled, typer.colors.YELLOW))
    if errors:
        rows.append(("  ", "Errors", "(!!)", errors, typer.colors.RED))

    label_width: int = max(len(indent + label) for indent, label, _, _, _ in rows)
    marker_width: int = max(len(marker) for _, _, marker, _, _ in rows)

    def _rest(indent: str, label: str, marker: str, count: int) -> str:
        """Build the non-bold remainder of a summary row."""
        pad: str = " " * (label_width - len(indent + label))
        body: str = f"{pad} {marker:<{marker_width}} : {count:>{count_width}}"
        if label == "Total":
            return body
        return f"{body}  ({count / total * 100:>6.2f}%)"

    rendered: list[tuple[str, str, str, str]] = [
        (indent, label, _rest(indent, label, marker, count), color)
        for indent, label, marker, count, color in rows
    ]
    box_width: int = max(len(indent + label + rest) for indent, label, rest, _ in rendered)

    typer.echo("┌" + "─" * (box_width + 2) + "┐")
    for indent, label, rest, color in rendered:
        pad: str = " " * (box_width - len(indent + label + rest))
        styled: str = (
            indent + typer.style(label, fg=color, bold=True) + typer.style(rest, fg=color) + pad
        )
        typer.echo("│ " + styled + " │")
    typer.echo("└" + "─" * (box_width + 2) + "┘")


# ==================================================================================== #
#                                     ENTRY POINT                                      #
# ==================================================================================== #
if __name__ == "__main__":
    app()
