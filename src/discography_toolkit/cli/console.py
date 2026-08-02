# src/discography_toolkit/cli/console.py
"""Rendering primitives shared by every command.

Nothing here knows what a step does. Steps compute and return results;
this turns results into output. Keeping the two apart is what lets one
result render as a line for a single artist and as one line each for
twenty.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from rich.console import Console
from rich.progress import (
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.text import Text
import typer

from discography_toolkit.core.layout import is_artist_folder, owning_folder

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from rich.progress import Task, TaskID

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Every other colour already means something -- green changed, blue
# unchanged, yellow warning, red error, cyan dry run, magenta total, grey
# dimmed -- so the banner would read as a status in any of them. Orange
# needs Rich: typer styles with the 16-colour ANSI set, which has none.
BANNER_COLOR: Final[str] = "dark_orange"

BAR_WIDTH: Final[int] = 50

_console: Final[Console] = Console()


# ==================================================================================== #
#                                    PROGRESS BARS                                     #
# ==================================================================================== #
class DashBarColumn(ProgressColumn):
    """A progress bar drawn as a run of dashes, in the style of `uv sync`.

    Rich's own `BarColumn` draws solid block glyphs, so the bar is
    assembled by hand to match uv's `progress_chars("--")`.

    Bold applies to the completed run only: in most terminals bold both
    thickens and brightens, so bolding the remainder too would lift the
    grey toward the green and blur the boundary the bar exists to show.

    Attributes:
        bar_width: Number of dash characters the bar occupies.
        complete_style: Rich style for the completed portion.
        remaining_style: Rich style for the remaining portion.
    """

    def __init__(
        self,
        bar_width: int = BAR_WIDTH,
        complete_style: str = "bold green",
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
        """Render the bar at its current completion.

        The filled length is floored: with one glyph rather than a ramp
        of partial blocks, a dash only turns green once its share of the
        work is genuinely done.

        Args:
            task: The task being rendered.

        Returns:
            `bar_width` dashes, split into a completed and a remaining
            run styled independently.
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

    Rich keeps `Task.completed` as a float, so a plain `TextColumn`
    would render `58.0`. The running count is right-aligned to the width
    of the total, so the column stays a constant width as the counter
    gains digits instead of shifting the line beside it.

    Attributes:
        style: Rich style for the counter, dim so the percentage stays
            the more prominent figure.
        noun: What is being counted. Most steps walk files; one walks
            albums, and a bar that says otherwise is simply wrong.
    """

    def __init__(self, style: str = "bright_black", noun: str = "files") -> None:
        """Initialize the column.

        Args:
            style: Rich style applied to the whole counter.
            noun: Plural name for what is being counted.
        """
        self.style: str = style
        self.noun: str = noun
        super().__init__()

    @override
    def render(self, task: Task) -> Text:
        """Render the counter at its current completion.

        Args:
            task: The task being rendered.

        Returns:
            Text of the form `(  58/103 files)`.
        """
        total: int = int(task.total) if task.total else 0
        completed: int = int(task.completed)
        width: int = len(str(total))

        return Text(f"({completed:>{width}}/{total} {self.noun})", style=self.style)


def make_progress(noun: str = "files") -> Progress:
    """Build a progress display in the toolkit's house style.

    The bar carries a spinner and an elapsed clock as well as the
    percentage: on a slow disk a single large file can take many seconds
    to rewrite, and without something ticking the whole bar would look
    frozen for the length of that one file. The spinner and clock keep
    moving -- Rich refreshes them from its own thread -- so a long write
    reads as work in progress, not a hang.

    Args:
        noun: Plural name for what the bar counts.

    Returns:
        A `Progress` with a spinner, bold description, dashed bar,
        percentage, counter, and elapsed clock.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        DashBarColumn(),
        TaskProgressColumn(),
        FileCountColumn(noun=noun),
        TimeElapsedColumn(),
    )


def make_advancer(
    progress: Progress,
    label: str,
    tracks: Sequence[Path],
    artists: Sequence[Path],
) -> Callable[[Path], None]:
    """Build a progress callback driving one bar per artist plus a total.

    Each artist's bar is removed the moment its last file is handled, so
    a long run collapses toward the single total the way `uv sync` does.
    Files belonging to no artist advance only the total.

    Args:
        progress: The live display to add tasks to.
        label: Description for the overall bar.
        tracks: Every file about to be processed, used to size the bars.
        artists: Artist folders to give their own bar.

    Returns:
        A callback to hand a step, called once per file.
    """
    overall: TaskID = progress.add_task(label, total=len(tracks))

    expected: Counter[Path] = Counter(
        artist for track in tracks if (artist := owning_folder(track, artists)) is not None
    )
    tasks: dict[Path, TaskID] = {
        artist: progress.add_task(f"  {artist.name}", total=count)
        for artist, count in expected.items()
    }
    seen: Counter[Path] = Counter()

    def advance(track: Path) -> None:
        progress.advance(overall)
        artist: Path | None = owning_folder(track, artists)
        if artist is None or artist not in tasks:
            return
        progress.advance(tasks[artist])
        seen[artist] += 1
        if seen[artist] >= expected[artist]:
            progress.remove_task(tasks.pop(artist))

    return advance


def make_bar(progress: Progress, label: str, total: int) -> Callable[[Path], None]:
    """Build a one-bar callback, for a pass with no per-artist breakdown.

    The counterpart to `make_advancer`, for work that is not grouped
    under artists -- a playlist folds one artist's albums, so there is
    nothing to break down by.

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


# ==================================================================================== #
#                                       BANNER                                         #
# ==================================================================================== #
def artist_names(target: Path, artists: Sequence[Path]) -> list[str]:
    """Name the artist folders beneath a target, for the banner.

    A target that is itself an artist gets no list: the banner names it
    on the line above, and its children are albums, which say nothing
    about scope.

    The artists are passed in rather than found here. Every command
    already walks for them to size its bars, and walking a shelf twice
    to print one line is a poor trade.

    Args:
        target: The folder the run is scoped to.
        artists: The artist folders found beneath it.

    Returns:
        Their names, empty when the target is itself an artist.
    """
    if is_artist_folder(target):
        return []
    return [artist.name for artist in artists]


def echo_banner(title: str, target: str, children: Sequence[str] = ()) -> None:
    """Announce which step is running, and on what.

    Printed first, so a terminal holding several steps in sequence can be
    read back and each block attributed to the step that produced it.

    The target goes through `typer.echo`, not the console: Rich parses
    square brackets as markup, so "Miles Davis - [65 • 65F • 0L • 0M]"
    would come back with its brackets restyled and its numbers
    highlighted. Only the rule is Rich's to render.

    Args:
        title: The step's name, e.g. `"Metadata: Genre"`.
        target: The folder being worked on, printed beneath the rule.
        children: Artist folders found inside the target, listed under it
            and dimmed.
    """
    _console.print()
    _console.print(
        Rule(
            Text(title, style=f"bold {BANNER_COLOR}"),
            style=BANNER_COLOR,
            characters="─",
            align="left",
        )
    )
    typer.echo(target)
    for child in children:
        typer.secho(f"  {child}", fg=typer.colors.BRIGHT_BLACK)


# ==================================================================================== #
#                                     RESULT LINES                                     #
# ==================================================================================== #
# Wide enough for "Album Artist", the longest label any pass carries, so
# the counts line up down a run whichever passes it happens to include.
RESULT_LABEL_WIDTH: Final[int] = 13


@dataclass(frozen=True, slots=True)
class Notice:
    """One thing a pass saw but would not act on.

    Not an error -- nothing failed -- but something only a person can
    settle: a track under no album folder, a file that would not read.
    Kept apart from the count because the two answer different
    questions, and "nothing to do" means the opposite thing depending on
    which of them is empty.

    Attributes:
        summary: The line naming it, already counted and phrased.
        details: The specifics needed to go and fix it -- the paths, the
            reasons. Empty where the summary says all there is.
    """

    summary: str
    details: tuple[str, ...] = ()


def echo_notices(notices: Sequence[Notice]) -> None:
    """Print what a pass saw but would not act on, nested under its line.

    Three levels: the result above, the notice, and the things it names.
    Shared with `layout`, which reports per artist rather than per pass
    and so has no result line of its own to hang them from.

    Args:
        notices: What the pass saw, already phrased and counted.
    """
    for notice in notices:
        typer.secho(f"      {notice.summary}", fg=typer.colors.YELLOW)
        for detail in notice.details:
            typer.secho(f"          {detail}", fg=typer.colors.BRIGHT_BLACK)


def echo_failures(failures: Sequence[tuple[Path, str]]) -> None:
    """Print what was attempted and would not work.

    A failure is not a notice: something was tried, the filesystem or the
    file refused it, and the reason it gave is the only thing that says
    what to do next. Counting them without naming them leaves a person
    with a number and nowhere to start.

    Args:
        failures: `(path, reason)` for each operation that failed.
    """
    for failed, reason in failures:
        typer.secho(f"      {str(failed)!r} - {reason}", fg=typer.colors.RED)


def echo_result(
    name: str,
    count: int,
    verb: str,
    notices: Sequence[Notice] = (),
    failures: Sequence[tuple[Path, str]] = (),
) -> None:
    """Print one pass's line, with whatever it wants a person's eye on.

    The one shape every pass reports in, so the same work reads the same
    whether it was reached through `align-tags` or through the `tags`
    command that does it alone.

    Args:
        name: The pass's name, e.g. `"Album Artist"`.
        count: How many files or albums it changes.
        verb: How the count reads, e.g. `"tagged"`, `"to date"`.
        notices: What it saw but would not act on.
        failures: `(path, reason)` for anything that failed outright.
    """
    colour: str = typer.colors.GREEN if count else typer.colors.BRIGHT_BLACK
    typer.secho(f"  {name:<{RESULT_LABEL_WIDTH}} {count} {verb}", fg=colour)

    echo_notices(notices)
    echo_failures(failures)
