# src/discography_toolkit/cli/commands/align_tags.py
"""The `rapt align-tags` command.

Where `rapt layout` settles the folders and filenames, this settles the
tags to match them. Once the layout has run, the structure is the
canonical form of the collection, and every tag but one can be read
straight off it: the Album from the album folder, the Album Artist from
the artist folder, the Date from the year in the album's name, the cover
from the image beside the tracks. The Title is the exception it recases
in place rather than inventing, the same claim the title command makes.

Genre is the one tag left out. Nothing in the folders says what a record
sounds like, so genre stays a separate, deliberate `rapt tags genre`
with a value given by hand.

The passes run one after another and in sight of each other, the way the
individual tag commands would if run by hand: the covers settle first, on
disk and into the files, and then each text tag is written in turn, each
with its own progress. Settling covers and writing tags are different
work, and a long run should show which it is doing.
"""

from __future__ import annotations

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from discography_toolkit.cli.console import (
    artist_names,
    echo_banner,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core.layout import (
    find_albums,
    find_artist_folders,
    find_audio_files,
    owning_folder,
)
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import (
    extract_year,
    is_approximate_year,
    strip_artist_label,
    title_case,
)
from discography_toolkit.operations import covers, tagging

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rich.progress import Progress, TaskID


# ==================================================================================== #
#                                     RESULT TYPE                                      #
# ==================================================================================== #
class _TagStep:
    """One text-tag pass: its name, the tag it writes, and how it derives it.

    Attributes:
        name: The pass's display name, e.g. "Album".
        verb: How its result reads, e.g. "tagged" or "recased".
        tags: The tags it writes, almost always one.
        wants: The value function for `tagging.plan`.
    """

    def __init__(self, name: str, verb: str, tags: list[Tag], wants: tagging.Desired) -> None:
        """Record the pass.

        Args:
            name: The pass's display name.
            verb: How its result reads.
            tags: The tags it writes.
            wants: The value function.
        """
        self.name: str = name
        self.verb: str = verb
        self.tags: list[Tag] = tags
        self.wants: tagging.Desired = wants


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def align_tags(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to align beneath. An artist, or a shelf holding several.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without writing."),
    ] = False,
) -> None:
    """Write every folder-derived tag -- covers, album, artist, year, title.

    Args:
        path: Folder to align beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no album found, no audio found, a
            user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to align beneath")
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Align Tags", target.name, children=artist_names(target, artists))

    albums: list[Path] = find_albums(target)
    if not albums:
        typer.secho(
            f"\nNo album folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run the layout pass first -- align-tags reads the tags off the folders it settles.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    tracks: list[Path] = find_audio_files(target)
    if not tracks:
        typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    steps: list[_TagStep] = _tag_steps(albums, artists)

    if dry_run:
        _preview(albums, tracks, artists, steps)
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not typer.confirm(
        f"\nAlign tags beneath {target.name!r}? Settles covers, then album, artist, year, title."
    ):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()
    cover_report: covers.CoverReport = _run_covers(albums)
    tag_reports: list[tuple[_TagStep, tagging.WriteReport]] = [
        (step, _run_tag(step, tracks, artists)) for step in steps
    ]

    _echo_summary(cover_report, tag_reports)


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _tag_steps(albums: Sequence[Path], artists: Sequence[Path]) -> list[_TagStep]:
    """Build the ordered text-tag passes, each deriving its own value.

    Each derivation matches the command that owns it, so a pass here can
    never disagree with `rapt tags album` and its siblings run alone.

    Args:
        albums: The album folders in scope.
        artists: The artist folders in scope.

    Returns:
        The passes, in the order they run.
    """
    return [
        _TagStep("Album", "tagged", [Tag.ALBUM], _album_wants(albums)),
        _TagStep("Album Artist", "tagged", [Tag.ALBUM_ARTIST], _artist_wants(artists)),
        _TagStep("Year", "dated", [Tag.DATE], _year_wants(albums)),
        _TagStep("Title", "recased", [Tag.TITLE], _title_wants),
    ]


def _album_wants(albums: Sequence[Path]) -> tagging.Desired:
    """Build the Album value function: each track named for its album folder.

    Args:
        albums: The album folders in scope.

    Returns:
        A `Desired` callable, empty for a track under no album.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        folder: Path | None = owning_folder(track, albums)
        return {} if folder is None else {Tag.ALBUM: folder.name}

    return desired


def _artist_wants(artists: Sequence[Path]) -> tagging.Desired:
    """Build the Album Artist value function, from the folder above a track.

    Args:
        artists: The artist folders in scope.

    Returns:
        A `Desired` callable, empty for a track under no artist.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        folder: Path | None = owning_folder(track, artists)
        if folder is None:
            return {}
        name: str | None = strip_artist_label(folder.name)
        return {} if name is None else {Tag.ALBUM_ARTIST: name}

    return desired


def _year_wants(albums: Sequence[Path]) -> tagging.Desired:
    """Build the Date value function, from the year in the album's name.

    An approximate year clears the tag: "199x" is not a date.

    Args:
        albums: The album folders in scope.

    Returns:
        A `Desired` callable, empty for a track whose album has no year.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        album: Path | None = owning_folder(track, albums)
        if album is None:
            return {}
        token: str | None = extract_year(album.name)
        if token is None:
            return {}
        return {Tag.DATE: "" if is_approximate_year(token) else token}

    return desired


def _title_wants(_track: Path, current: Mapping[Tag, str]) -> Mapping[Tag, str]:
    """Return the recased form of the Title already there.

    An absent title cases to nothing, compares equal, and is left be --
    the title is recased in place, never invented from the filename.

    Args:
        _track: The track, unused: the value comes from the tag.
        current: The track's tags as found.

    Returns:
        The Title tag, recased.
    """
    return {Tag.TITLE: title_case(current.get(Tag.TITLE, ""))}


# ==================================================================================== #
#                                    STEP RUNNERS                                      #
# ==================================================================================== #
def _run_covers(albums: Sequence[Path]) -> covers.CoverReport:
    """Settle the covers, showing a bar for the scan and one for the work.

    Args:
        albums: The album folders in scope.

    Returns:
        What the cover pass did.
    """
    with make_progress(noun="albums") as progress:
        plan = covers.plan(albums, on_progress=_bar(progress, "Covers: scanning", len(albums)))
    with make_progress(noun="changes") as progress:
        report = covers.apply(plan, on_progress=_bar(progress, "Covers: settling", plan.changes))

    settled: int = report.written + report.renamed + report.embedded
    _echo_result("Covers", settled, "settled", report.failures)
    return report


def _run_tag(
    step: _TagStep, tracks: Sequence[Path], artists: Sequence[Path]
) -> tagging.WriteReport:
    """Run one text-tag pass, showing a bar for the scan and one for the write.

    Args:
        step: The pass to run.
        tracks: Every audio file in scope.
        artists: The artist folders, for the per-artist sub-bars.

    Returns:
        What the pass did.
    """
    with make_progress() as progress:
        advance = make_advancer(progress, f"{step.name}: scanning", tracks, artists)
        plan = tagging.plan(tracks, step.tags, step.wants, on_progress=advance)

    with make_progress() as progress:
        pending: list[Path] = [outcome.path for outcome in plan.pending]
        advance = make_advancer(progress, f"{step.name}: writing", pending, artists)
        report = tagging.apply(plan, on_progress=advance)

    _echo_result(step.name, report.written, step.verb, report.failures)
    return report


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _bar(progress: Progress, label: str, total: int) -> Callable[[Path], None]:
    """Build a one-bar callback, for a pass with no per-artist breakdown.

    Args:
        progress: The live progress display.
        label: The bar's description.
        total: How many advances fill it.

    Returns:
        A callback advancing the bar once per item.
    """
    task: TaskID = progress.add_task(label, total=total)

    def advance(_item: Path) -> None:
        progress.advance(task)

    return advance


def _echo_result(name: str, count: int, verb: str, failures: Sequence[tuple[Path, str]]) -> None:
    """Print one pass's result, and any failures beneath it.

    Args:
        name: The pass's name.
        count: How many files it changed.
        verb: How the count reads, e.g. "tagged".
        failures: `(path, reason)` for anything that failed.
    """
    colour: str = typer.colors.GREEN if count else typer.colors.BRIGHT_BLACK
    typer.secho(f"  {name:<13} {count} {verb}", fg=colour)
    for failed, detail in failures:
        typer.secho(f"      {str(failed)!r} - {detail}", fg=typer.colors.RED)


def _preview(
    albums: Sequence[Path],
    tracks: Sequence[Path],
    artists: Sequence[Path],
    steps: Sequence[_TagStep],
) -> None:
    """Plan every pass and report its counts, without writing.

    Args:
        albums: The album folders in scope.
        tracks: Every audio file in scope.
        artists: The artist folders in scope.
        steps: The text-tag passes.
    """
    typer.echo()
    with make_progress(noun="albums") as progress:
        cover_plan = covers.plan(
            albums, on_progress=_bar(progress, "Covers: scanning", len(albums))
        )
    _echo_result("Covers", cover_plan.changes, "to settle", ())

    for step in steps:
        with make_progress() as progress:
            advance = make_advancer(progress, f"{step.name}: scanning", tracks, artists)
            plan = tagging.plan(tracks, step.tags, step.wants, on_progress=advance)
        _echo_result(step.name, len(plan.pending), "to write", ())


def _echo_summary(
    cover_report: covers.CoverReport,
    tag_reports: Sequence[tuple[_TagStep, tagging.WriteReport]],
) -> None:
    """Print the closing totals across every pass.

    Args:
        cover_report: What the cover pass did.
        tag_reports: What each text-tag pass did.
    """
    settled: int = cover_report.written + cover_report.renamed + cover_report.embedded
    written: int = sum(report.written for _, report in tag_reports)
    failures: int = len(cover_report.failures) + sum(
        len(report.failures) for _, report in tag_reports
    )

    typer.secho(
        f"\nDone. {settled} cover change(s) settled, {written} tag write(s) across the files.",
        fg=typer.colors.GREEN,
        bold=True,
    )
    if failures:
        typer.secho(f"{failures} operation(s) failed during writing.", fg=typer.colors.RED)
