#!/usr/bin/env -S uv run
"""Write one Album Artist tag across every audio file in a discography.

The value comes from the artist folder's own name, with the trailing
count label that `02.2--albums_placement.py` maintains stripped off:

    Charlie Mariano - [90 • 60F • 0L • 30M]            -> Charlie Mariano
    (Ivory Coast) - Christy B - [90 • 60F • 0L • 30M]  -> (Ivory Coast) - Christy B

Only that label is removed, and only where it sits at the very end of
the name. Everything to its left survives exactly as written, including
any internal " - " separator and any prefix -- a country, a collective,
a disambiguator -- that Calin has deliberately put in front. Splitting
on " - " instead would cut the second example down to "(Ivory Coast)",
which is why this anchors to the end rather than searching.

A folder carrying no label at all is refused rather than guessed at.
The label is written by 02.2 as part of making the discography
homogeneous, so its absence means that step hasn't run here yet, and
tagging from an unprocessed name would write a value that quietly
disagrees with the one every other artist got.

Unlike the Album tag, which differs per album folder, the Album Artist
is one value for the entire tree. So this walks the artist folder flat:
albums, the FLAC container, and multi-disc subfolders are all just
directories to descend, and audio sitting loose in the artist root gets
the same tag as everything else.

Supports FLAC, MP3, M4A, OGG Vorbis, Opus, WAV, AIFF, DSF, APE, WV,
TTA, and WMA. Idempotent per file: a file whose tag already matches is
left untouched and reported separately, not re-saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, Literal, cast, override

from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.dsf import DSF
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC
from mutagen.id3 import TPE2, ID3NoHeaderError
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

# The count label 02.2 maintains on the artist folder, anchored to the
# end of the name. Deliberately the same pattern 02.2 writes with: the
# bracket must open with an optional "M" and then a digit, so every form
# the label has taken is caught -- "[90 • 60F • 0L • 30M]", an older
# "[M31 on 90]", "[65 on 65]" -- while a bracket that is part of the
# artist's actual name ("[Live]", "[Best Of]") is left alone.
#
# Anchoring is what makes a prefixed name survive. Searching for " - "
# and splitting would cut "(Ivory Coast) - Christy B - [90 • ...]" at
# its first separator; matching the end removes only the label.
_ARTIST_LABEL_RE: re.Pattern[str] = re.compile(r"\s*(?:-\s*)?\[\s*M?\d[^\[\]]*\]\s*$")


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
            help="Absolute path to the artist folder containing album subfolders.",
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
    """Write the artist folder's name as Album Artist on every track beneath it.

    Args:
        path: Absolute path to the artist folder. Passed via
            `--path`/`-p`; if omitted, the user is prompted for it
            interactively instead.
        dry_run: If `True`, probe every file and report what would
            change without saving anything.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            a folder with no count label, no audio found, nothing to
            do, user abort, or a completed run), with an appropriate
            exit code attached.
    """
    if path is None:
        raw_input_path: str = cast(
            str, typer.prompt("Enter the absolute path to your artist folder")
        )
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    _echo_banner("Metadata: Album Artist", path.name)

    album_artist: str | None = _derive_album_artist(path.name)
    if album_artist is None:
        typer.secho(
            f"\nNo count label found on {path.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run 02.2--albums_placement.py first -- it writes the label this reads from.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    tracks: list[Path] = _find_audio_files(path)
    if not tracks:
        typer.secho(f"\nNo audio files found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    label: str = typer.style("Album Artist ->", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"\n{label} {album_artist!r}")

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
            outcomes.append((track, _apply_tag(track, album_artist, dry_run=True)))
            progress.update(probe_task, advance=1)

    total: int = len(outcomes)
    tagged_count: int = sum(1 for _, r in outcomes if r.status == "updated")
    clean_count: int = sum(1 for _, r in outcomes if r.status == "already_correct")
    error_count: int = sum(1 for _, r in outcomes if r.status == "error")
    _echo_summary(total=total, tagged=tagged_count, clean=clean_count, errors=error_count)

    if error_count:
        typer.secho(
            f"\n{error_count} file(s) could not be read:",
            fg=typer.colors.YELLOW,
        )
        for track, result in outcomes:
            if result.status == "error":
                typer.echo(f"  {str(track)!r} - {result.detail}")

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not tagged_count:
        typer.secho(
            "\nEvery file already carries this Album Artist. Nothing to do.",
            fg=typer.colors.GREEN,
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nWrite Album Artist to {tagged_count} file(s)?"):
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
            result: TagResult = _apply_tag(track, album_artist, dry_run=False)
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


def _derive_album_artist(folder_name: str) -> str | None:
    """Strip the trailing count label off an artist folder's name.

    Anchored to the end, so only the label goes and everything to its
    left survives verbatim -- including a prefix the artist name
    genuinely carries, which a split on " - " would truncate.

    Args:
        folder_name: The artist folder's name, e.g.
            `"(Ivory Coast) - Christy B - [90 • 60F • 0L • 30M]"`.

    Returns:
        The name with its label removed, or `None` when the name
        carries no label -- meaning 02.2 hasn't run on this folder and
        there is no agreed value to derive. Also `None` if stripping
        would leave nothing behind.
    """
    stripped: str = _ARTIST_LABEL_RE.sub("", folder_name).strip()
    if stripped == folder_name.strip() or not stripped:
        return None
    return stripped


def _find_audio_files(root: Path) -> list[Path]:
    """Collect every audio file anywhere beneath an artist folder.

    A flat recursive walk. The Album Artist is one value for the whole
    tree, so unlike 03 this has no reason to know which album a file
    belongs to -- which also means audio sitting loose in the artist
    root, or directly inside the FLAC container, is tagged like
    everything else rather than being missed.

    Hidden files and anything inside a hidden folder are skipped, the
    same way the rest of the pipeline skips them. That covers macOS
    "._track.flac" companion stubs, which carry an audio extension
    without being audio.

    Args:
        root: The artist folder to scan.

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


def _apply_tag(path: Path, album_artist: str, *, dry_run: bool) -> TagResult:
    """Read, compare and optionally write one file's Album Artist tag.

    Every format is probed the same way: read the current value, return
    `"already_correct"` when it already matches, otherwise set it and
    save. The value is always a non-empty string, so none of the
    empty-value handling the Date tag needs in 03 applies here.

    Args:
        path: The audio file to tag.
        album_artist: The value to write.
        dry_run: If `True`, decide what would change but never save.

    Returns:
        A `TagResult` describing the outcome.
    """
    suffix: str = path.suffix.lower()

    try:
        if suffix == ".flac":
            audio = FLAC(path)
            if _first(audio.get("albumartist")) == album_artist:
                return TagResult("already_correct")
            audio["albumartist"] = [album_artist]

        elif suffix == ".ogg":
            audio = OggVorbis(path)
            if _first(audio.get("albumartist")) == album_artist:
                return TagResult("already_correct")
            audio["albumartist"] = [album_artist]

        elif suffix == ".opus":
            audio = OggOpus(path)
            if _first(audio.get("albumartist")) == album_artist:
                return TagResult("already_correct")
            audio["albumartist"] = [album_artist]

        elif suffix == ".mp3":
            audio = _open_easy_id3(path)
            if _first(audio.get("albumartist")) == album_artist:
                return TagResult("already_correct")
            audio["albumartist"] = [album_artist]

        elif suffix == ".m4a":
            audio = EasyMP4(path)
            if _first(audio.get("albumartist")) == album_artist:
                return TagResult("already_correct")
            audio["albumartist"] = [album_artist]

        elif suffix in {".wav", ".aiff", ".aif", ".dsf", ".tta"}:
            audio = _open_id3_container(path, suffix)
            if _id3_text(audio, "TPE2") == album_artist:
                return TagResult("already_correct")
            _set_id3_text(audio, "TPE2", album_artist)

        elif suffix in {".ape", ".wv"}:
            audio = _open_apev2(path, suffix)
            if _first(audio.get("Album Artist")) == album_artist:
                return TagResult("already_correct")
            audio["Album Artist"] = album_artist

        elif suffix == ".wma":
            audio = ASF(path)
            if _first_asf(audio, "WM/AlbumArtist") == album_artist:
                return TagResult("already_correct")
            audio["WM/AlbumArtist"] = [album_artist]

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


def _open_easy_id3(path: Path):
    """Open an MP3's tags via mutagen's EasyID3, creating a tag if absent.

    Args:
        path: The MP3 file to open.

    Returns:
        An `EasyID3` instance ready for dict-style access.
    """
    try:
        return EasyID3(path)
    except ID3NoHeaderError:
        mp3_audio = MP3(path, ID3=EasyID3)
        mp3_audio.add_tags()
        return mp3_audio


def _open_id3_container(path: Path, suffix: str) -> FileType:
    """Open a WAV/AIFF/DSF/TTA file's raw ID3 tags, creating one if absent.

    These formats carry ID3 like MP3 does, but without an Easy wrapper
    -- tags are set via explicit frame objects rather than string keys.

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
        frame_id: The ID3 frame identifier, e.g. `"TPE2"`.

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
        frame_id: The ID3 frame identifier, e.g. `"TPE2"`.
        value: The text to write into the frame.
    """
    del frame_id  # Only TPE2 is written here; kept for call-site clarity.
    audio.tags.setall("TPE2", [TPE2(encoding=3, text=[value])])


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
        key: The ASF attribute name, e.g. `"WM/AlbumArtist"`.

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
