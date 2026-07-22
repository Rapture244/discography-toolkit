#!/usr/bin/env -S uv run
"""Write one Genre tag across every audio file beneath a given path.

Takes a path and a genre string, and writes that string -- exactly as
given -- to the Genre tag of every audio file underneath. Nothing is
derived, parsed or inferred: unlike the other metadata steps, this one
has no opinion about folder names, album structure or the discography
layout. The path is simply a scope, and the genre is simply a value.

That makes the scope whatever is useful at the time. Point it at one
album folder to fix a single release, at an artist folder to set a
whole discography, or at a shelf like "DISCOGRAPHY/Jazz" to cover
everything below it in one pass.

The value is written verbatim, so a compound genre stays one string:
"Jazz/Jazz Fusion" is a single Genre value containing a slash, not two
genres. Nothing is split, reordered or title-cased.

MP3 is handled through its raw ID3 frame rather than mutagen's Easy
interface. ID3's genre frame carries legacy baggage -- a bare "17"
means "Rock", and "(17)Rock" means the same -- so a numeric genre read
back through the Easy layer never matches what was written, and the
file would be rewritten on every run forever. Reading the frame text
directly compares what is actually stored.

Supports FLAC, MP3, M4A, OGG Vorbis, Opus, WAV, AIFF, DSF, APE, WV,
TTA, and WMA. Idempotent per file: a file whose tag already matches is
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
from mutagen.id3 import TCON
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
# Every audio extension this script will tag.
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

# Formats that store tags as ID3 frames. MP3 is in here deliberately:
# mutagen's Easy interface routes genre through ID3's legacy parsing,
# where a bare "17" resolves to "Rock", so the value read back would
# never equal the value written and every run would rewrite the file.
_ID3_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".aiff", ".aif", ".dsf", ".tta"})


@dataclass
class TagResult:
    """Outcome of attempting to tag a single audio file.

    Attributes:
        status: `"updated"` if the tag was written (or would be, in a
            dry run), `"already_correct"` if it already matched and
            nothing needed to change, or `"error"` if the file couldn't
            be read or tagged at all.
        detail: A human-readable reason, populated only for `"error"`.
    """

    status: Literal["updated", "already_correct", "error"]
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
def tag(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Absolute path to tag beneath. Any folder: album, artist, or a whole shelf.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    genre: Annotated[
        str | None,
        typer.Option(
            "--genre",
            "-g",
            help='Genre to write, verbatim. Quote it: "Jazz/Jazz Fusion".',
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be tagged without touching anything."),
    ] = False,
) -> None:
    """Write one Genre value to every audio file beneath a path.

    Args:
        path: Absolute path to tag beneath. Passed via `--path`/`-p`;
            if omitted, the user is prompted for it interactively.
        genre: The genre string to write, used exactly as given.
            Passed via `--genre`/`-g`; if omitted, the user is
            prompted for it interactively.
        dry_run: If `True`, probe every file and report what would
            change without saving anything.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            empty genre, no audio found, nothing to do, user abort, or
            a completed run), with an appropriate exit code attached.
    """
    if path is None:
        raw_input_path: str = cast(str, typer.prompt("Enter the absolute path to tag beneath"))
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    _echo_banner("Metadata: Genre", path.name)

    if genre is None:
        genre = cast(str, typer.prompt('\nEnter the genre (e.g. "Jazz" or "Jazz/Jazz Fusion")'))

    genre = genre.strip()
    if not genre:
        typer.secho("\nGenre cannot be empty.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    tracks: list[Path] = _find_audio_files(path)
    if not tracks:
        typer.secho(f"\nNo audio files found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    label: str = typer.style("Genre ->", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"\n{label} {genre!r}")

    progress_columns = (
        TextColumn("[bold]{task.description}"),
        DashBarColumn(),
        TaskProgressColumn(),
        FileCountColumn(),
    )
    run_label: str = path.name or str(path)

    outcomes: list[tuple[Path, TagResult]] = []
    with Progress(*progress_columns) as progress:
        probe_task = progress.add_task(run_label, total=len(tracks))
        for track in tracks:
            outcomes.append((track, _apply_tag(track, genre, dry_run=True)))
            progress.update(probe_task, advance=1)

    total: int = len(outcomes)
    tagged_count: int = sum(1 for _, r in outcomes if r.status == "updated")
    clean_count: int = sum(1 for _, r in outcomes if r.status == "already_correct")
    error_count: int = sum(1 for _, r in outcomes if r.status == "error")
    _echo_summary(total=total, tagged=tagged_count, clean=clean_count, errors=error_count)

    if error_count:
        typer.secho(f"\n{error_count} file(s) could not be read:", fg=typer.colors.YELLOW)
        for track, result in outcomes:
            if result.status == "error":
                typer.echo(f"  {str(track)!r} - {result.detail}")

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not tagged_count:
        typer.secho(
            "\nEvery file already carries this Genre. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nWrite Genre to {tagged_count} file(s)?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Failures are collected rather than printed as they happen: writing
    # to the terminal inside a live progress region tears the bar apart,
    # and grouping them at the end reads better anyway.
    written: int = 0
    failures: list[tuple[Path, str]] = []
    with Progress(*progress_columns) as progress:
        write_task = progress.add_task(run_label, total=tagged_count)
        for track, probe in outcomes:
            if probe.status != "updated":
                continue
            result: TagResult = _apply_tag(track, genre, dry_run=False)
            if result.status == "error":
                failures.append((track, result.detail))
            else:
                written += 1
            progress.update(write_task, advance=1)

    for track, detail in failures:
        typer.secho(f"  Failed: {str(track)!r} - {detail}", fg=typer.colors.RED)

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


def _find_audio_files(root: Path) -> list[Path]:
    """Collect every audio file anywhere beneath the given path.

    A flat recursive walk with no knowledge of the discography layout,
    which is what lets the path be any scope at all: one album, one
    artist, or a whole genre shelf holding many artists.

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


def _apply_tag(path: Path, genre: str, *, dry_run: bool) -> TagResult:
    """Read, compare and optionally write one file's Genre tag.

    Every format is probed the same way: read the current value, return
    `"already_correct"` when it already matches, otherwise set it and
    save.

    Args:
        path: The audio file to tag.
        genre: The value to write, used exactly as given.
        dry_run: If `True`, decide what would change but never save.

    Returns:
        A `TagResult` describing the outcome.
    """
    suffix: str = path.suffix.lower()

    try:
        if suffix in {".flac", ".ogg", ".opus"}:
            audio = _open_vorbis(path, suffix)
            if _first(audio.get("genre")) == genre:
                return TagResult("already_correct")
            audio["genre"] = [genre]

        elif suffix == ".m4a":
            audio = EasyMP4(path)
            if _first(audio.get("genre")) == genre:
                return TagResult("already_correct")
            audio["genre"] = [genre]

        elif suffix in _ID3_EXTENSIONS:
            audio = _open_id3_container(path, suffix)
            if _id3_text(audio) == genre:
                return TagResult("already_correct")
            _set_id3_genre(audio, genre)

        elif suffix in {".ape", ".wv"}:
            audio = _open_apev2(path, suffix)
            if _first(audio.get("Genre")) == genre:
                return TagResult("already_correct")
            audio["Genre"] = genre

        elif suffix == ".wma":
            audio = ASF(path)
            if _first_asf(audio, "WM/Genre") == genre:
                return TagResult("already_correct")
            audio["WM/Genre"] = [genre]

        else:
            return TagResult("error", detail=f"Unsupported audio format: {suffix}")

        if not dry_run:
            audio.save()

    except Exception as exc:  # noqa: BLE001 - any file can be unreadable/corrupt in ways not worth enumerating
        return TagResult("error", detail=str(exc))

    return TagResult("updated")


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
    go through raw frames rather than an Easy wrapper -- which is what
    keeps a numeric genre comparing equal to itself.

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
    """Read the raw text of an ID3 genre frame, if present.

    Deliberately reads `frame.text` rather than `frame.genres`. The
    latter resolves ID3v1 numeric codes, so a genre stored as "17"
    would come back as "Rock" and never compare equal to what was
    written.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.

    Returns:
        The frame's first text value, or `None` if the frame is absent.
    """
    frame = audio.tags.get("TCON")
    if frame is None or not frame.text:
        return None
    return str(frame.text[0])


def _set_id3_genre(audio, value: str) -> None:
    """Set the ID3 genre frame's text, replacing any existing frame.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.
        value: The genre text to write.
    """
    audio.tags.setall("TCON", [TCON(encoding=3, text=[value])])


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
        key: The ASF attribute name, e.g. `"WM/Genre"`.

    Returns:
        The attribute's value as a string, or `None` if absent.
    """
    values = audio.get(key)
    if not values:
        return None
    return str(values[0])


def _echo_summary(*, total: int, tagged: int, clean: int, errors: int) -> None:
    """Print the summary box.

    Args:
        total: Audio files examined.
        tagged: Files whose tag changes.
        clean: Files already carrying the right value.
        errors: Files that could not be read.
    """
    if not total:
        return

    count_width: int = len(str(total))
    rows: list[tuple[str, str, str, int, str]] = [
        ("", "Total", "", total, typer.colors.BRIGHT_MAGENTA),
        ("  ", "Tagged", "(->)", tagged, typer.colors.GREEN),
        ("  ", "Clean", "(==)", clean, typer.colors.BLUE),
    ]
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
