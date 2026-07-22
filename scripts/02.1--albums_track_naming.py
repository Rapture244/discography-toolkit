#!/usr/bin/env -S uv run
"""Title-case the filename of every audio track in a discography.

Walks every album under an artist folder and rewrites each audio file's
name in classic English title case -- "kind of blue.flac" becomes
"Kind of Blue.flac" -- using the same `titlecase` implementation of
John Gruber's algorithm that `01--albums_naming.py` applies to album
folders, so a track and the album holding it are cased by one rule.

Only the stem is touched. The extension is left exactly as found and
never cased, because it is what every other script in the pipeline
matches on: ".Flac" is the same file to Windows but a different string
to a `suffix.lower() in _AUDIO_EXTENSIONS` check, and a file that stops
matching stops being tagged.

Only audio files are touched, for the same reason one level down.
"cover.jpg" and "folder.jpg" are lookup conventions that players search
for by exact name, and ".cue" sheets reference their audio by filename,
so casing either would break a reference this script has no way to
follow. Anything that isn't recognized audio is left alone.

The convention is English, so a German or French track title comes out
capitalized rather than correct ("warum bist du traurig" becomes "Warum
Bist Du Traurig"). No library can settle that without knowing the
language; the dry run is where to catch it. Scripts without letter case
at all, such as Japanese and Chinese, pass through untouched.

Runs after the album-level scripts and before the metadata ones: it
changes filenames only, so album folder names, numbering, placement,
and the Album/Date tags are all unaffected by it.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, cast, override

from rich.progress import Progress, ProgressColumn, TaskProgressColumn, TextColumn
from rich.text import Text
from titlecase import titlecase
import typer

if TYPE_CHECKING:
    from rich.progress import Task

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(add_completion=False, help=__doc__)


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Every audio extension this script will rename. Matched case-folded, so
# a file arriving as ".FLAC" is recognized -- though its extension is
# still written back exactly as found.
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

# Collapses runs of whitespace left behind in a stem.
_MULTI_SPACE_RE: re.Pattern[str] = re.compile(r"\s{2,}")

# Terminal columns the dashed progress bar occupies.
_BAR_WIDTH: int = 30


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
        bar_width: int = _BAR_WIDTH,
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
def rename(
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
        typer.Option("--dry-run", help="Show planned renames without touching anything."),
    ] = False,
) -> None:
    """Title-case every audio filename beneath an artist folder.

    Args:
        path: Absolute path to the artist folder. Passed via
            `--path`/`-p`; if omitted, the user is prompted for it
            interactively instead.
        dry_run: If `True`, report every planned rename without
            modifying the filesystem.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            no albums or tracks found, a name collision, nothing to do,
            user abort, or a completed run), with an appropriate exit
            code attached.
    """
    if path is None:
        raw_input_path: str = cast(
            str, typer.prompt("Enter the absolute path to your artist folder")
        )
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    tracks: list[Path] = _find_audio_files(path)
    if not tracks:
        typer.secho(f"No audio files found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    total_tracks: int = len(tracks)

    progress_columns = (
        TextColumn("[bold]{task.description}"),
        DashBarColumn(),
        TaskProgressColumn(),
        FileCountColumn(),
    )
    run_label: str = path.name or str(path)

    typer.echo()
    plan: list[tuple[Path, str]] = []
    with Progress(*progress_columns) as progress:
        scan_task = progress.add_task(run_label, total=total_tracks)
        for track in tracks:
            new_name: str = _title_case_filename(track.name)
            if new_name != track.name:
                plan.append((track, new_name))
            progress.update(scan_task, advance=1)

    rename_count: int = len(plan)
    _echo_summary(total=total_tracks, renamed=rename_count, clean=total_tracks - rename_count)

    collisions: list[tuple[Path, str]] = _find_collisions(plan)
    if collisions:
        collision_note: str = "cannot be renamed -- the target name is already taken"
        typer.secho(f"\n{len(collisions)} file(s) {collision_note}:", fg=typer.colors.RED, err=True)
        for track, new_name in collisions:
            typer.echo(f"  {str(track)!r} -> {new_name!r}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not rename_count:
        typer.secho("\nEvery track is already title-cased. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm(f"\nRename {rename_count} file(s)?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Failures are collected rather than printed as they happen:
    # writing to the terminal inside a live progress region tears the
    # bar apart, and grouping them at the end reads better anyway.
    renamed: int = 0
    failures: list[tuple[Path, str]] = []
    with Progress(*progress_columns) as progress:
        write_task = progress.add_task(run_label, total=rename_count)
        for track, new_name in plan:
            try:
                _rename_track(track, new_name)
            except OSError as exc:
                failures.append((track, str(exc)))
            else:
                renamed += 1
            progress.update(write_task, advance=1)

    for track, detail in failures:
        typer.secho(f"  Failed: {str(track)!r} - {detail}", fg=typer.colors.RED)

    typer.secho(f"\nDone. {renamed} file(s) renamed.", fg=typer.colors.GREEN, bold=True)
    if failures:
        typer.secho(f"{len(failures)} file(s) failed during renaming.", fg=typer.colors.RED)


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


def _find_audio_files(root: Path) -> list[Path]:
    """Collect every audio file anywhere beneath an artist folder.

    A flat recursive walk, deliberately blind to the structure around
    it: albums, the FLAC container, and multi-disc "CD1"/"CD2"
    subfolders are all just directories to descend. Filenames are the
    only thing this script touches, and a track's name has nothing to
    do with which folder holds it -- so anything caught by a
    structure-aware search would only be audio it then failed to
    rename.

    Extensions are matched case-folded, so a ".FLAC" file is found;
    its extension is still written back exactly as found.

    Hidden files and anything inside a hidden folder are skipped, the
    same way the rest of the pipeline skips them. That covers macOS
    "._track.flac" companion stubs, which carry an audio extension
    without being audio, and any ".__tmp__" file left behind by an
    interrupted rename.

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


def _title_case_filename(name: str) -> str:
    """Title-case a filename's stem, leaving its extension untouched.

    The stem and suffix are split before casing, because `titlecase`
    applied to a whole filename produces "Kind of Blue.Flac" -- which
    is the same file to Windows but a different string to every
    `suffix.lower()` check in this pipeline.

    The acronym guard is applied only when the stem contains some
    lowercase. A name written entirely in capitals is far likelier to
    be shouted than to be one long acronym, so "THE MAN WITH THE HORN"
    normalizes, while the "OST" in "Theme From OST" is recognized as an
    acronym precisely because the words around it are not capitals.

    Args:
        name: The filename, with extension, e.g. `"01 - so what.flac"`.

    Returns:
        The filename with its stem title-cased and its extension
        exactly as given. Runs of whitespace in the stem are collapsed
        and the edges trimmed.
    """
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""

    is_shouted: bool = stem == stem.upper() and stem != stem.lower()
    cased: str = titlecase(stem) if is_shouted else titlecase(stem, callback=_preserve_acronym)
    cased = _MULTI_SPACE_RE.sub(" ", cased).strip()

    if not cased:
        return name
    return f"{cased}.{suffix}" if dot else cased


def _find_collisions(plan: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """Find planned renames whose target name is already taken.

    Two tracks can only collide when their names differ by nothing but
    case or spacing, which a case-insensitive filesystem wouldn't have
    allowed in the first place -- but a discography copied from a Linux
    server can carry exactly that. Renaming blindly would have one file
    silently overwrite the other.

    Checked per directory, since a name is only unique within its own
    folder: two albums are free to hold a track of the same name.

    Args:
        plan: `(track, new_name)` pairs for every file that changes.

    Returns:
        The pairs that cannot be renamed safely.
    """
    moving: set[Path] = {track for track, _ in plan}
    claimed: set[Path] = set()
    collisions: list[tuple[Path, str]] = []

    for track, new_name in plan:
        target: Path = track.with_name(new_name)
        # An existing file only blocks the rename if it isn't itself
        # moving out of the way, and a case-only change resolves to the
        # same path on a case-insensitive filesystem.
        taken_on_disk: bool = target.exists() and target != track and target not in moving
        if taken_on_disk or target in claimed:
            collisions.append((track, new_name))
        claimed.add(target)

    return collisions


def _rename_track(track: Path, new_name: str) -> None:
    """Rename one track, safely for case-only changes.

    A change that only alters case is routed through a temporary name
    first. On a case-insensitive filesystem the source and target are
    the same file, and a direct rename is either rejected or silently
    does nothing depending on the platform; the two-step is unambiguous
    everywhere.

    Args:
        track: The file to rename.
        new_name: Its new filename, extension included.

    Raises:
        OSError: If either step of the rename fails.
    """
    target: Path = track.with_name(new_name)
    if track.name.lower() != new_name.lower():
        _ = track.rename(target)
        return

    staging: Path = track.with_name(f".__tmp__{new_name}")
    _ = track.rename(staging)
    _ = staging.rename(target)


def _echo_summary(*, total: int, renamed: int, clean: int) -> None:
    """Print the summary box.

    Args:
        total: Audio files examined.
        renamed: Files whose name changes.
        clean: Files already correctly cased.
    """
    if not total:
        return

    count_width: int = len(str(total))
    rows: list[tuple[str, str, str, int, str]] = [
        ("", "Total", "", total, typer.colors.BRIGHT_MAGENTA),
        ("  ", "Renamed", "(->)", renamed, typer.colors.GREEN),
        ("  ", "Clean", "(==)", clean, typer.colors.BLUE),
    ]
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
