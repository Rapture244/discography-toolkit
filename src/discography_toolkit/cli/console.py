# src/discography_toolkit/cli/console.py
"""Rendering primitives shared by every command.

Nothing here knows what a step does. Steps compute and return results;
this turns results into output. Keeping the two apart is what lets one
result render as a full summary box for a single artist and as one line
each for twenty.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from rich.console import Console
from rich.progress import Progress, ProgressColumn, TaskProgressColumn, TextColumn
from rich.rule import Rule
from rich.text import Text
import typer

from discography_toolkit.core.layout import owning_folder

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

    Args:
        noun: Plural name for what the bar counts.

    Returns:
        A `Progress` with a bold description, dashed bar, percentage and
        counter.
    """
    return Progress(
        TextColumn("[bold]{task.description}"),
        DashBarColumn(),
        TaskProgressColumn(),
        FileCountColumn(noun=noun),
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


# ==================================================================================== #
#                                       BANNER                                         #
# ==================================================================================== #
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
#                                     SUMMARY BOX                                      #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class SummaryRow:
    """One line of a summary box.

    Attributes:
        label: The row's name, rendered bold.
        count: The figure to show.
        marker: A short symbol identifying the row, e.g. `"(->)"`.
        color: Typer colour applied to the whole row.
        indent: Leading spaces, used to nest rows under a total.
        percent: Whether to show this count as a share of the box total.
    """

    label: str
    count: int
    marker: str = ""
    color: str = typer.colors.WHITE
    indent: str = "  "
    percent: bool = True


def echo_summary(groups: Sequence[Sequence[SummaryRow]], total: int) -> None:
    """Print a summary box, one horizontal rule between each group.

    Groups rather than a divider sentinel: a rule separates partitions,
    so it belongs between them structurally. A box can then hold two
    independent tallies -- what a run did, and what the collection is --
    each summing to the total on its own, where listing them flat would
    read as one tally adding to well over 100%.

    Columns are sized from the data, so adding a row can never knock the
    colons out of line.

    Args:
        groups: Rows to draw, one rule drawn between each group.
        total: The figure percentages are taken against.
    """
    entries: list[SummaryRow] = [row for group in groups for row in group]
    if not entries or total <= 0:
        return

    label_width: int = max(len(row.indent + row.label) for row in entries)
    marker_width: int = max(len(row.marker) for row in entries)
    count_width: int = max(len(str(row.count)) for row in entries)

    def _rest(row: SummaryRow) -> str:
        pad: str = " " * (label_width - len(row.indent + row.label))
        body: str = f"{pad} {row.marker:<{marker_width}} : {row.count:>{count_width}}"
        if not row.percent:
            return body
        return f"{body}  ({row.count / total * 100:>6.2f}%)"

    rendered: list[list[tuple[SummaryRow, str]]] = [
        [(row, _rest(row)) for row in group] for group in groups if group
    ]
    box_width: int = max(
        len(row.indent + row.label + rest) for group in rendered for row, rest in group
    )

    typer.echo("┌" + "─" * (box_width + 2) + "┐")
    for index, group in enumerate(rendered):
        if index:
            typer.echo("├" + "─" * (box_width + 2) + "┤")
        for row, rest in group:
            pad: str = " " * (box_width - len(row.indent + row.label + rest))
            styled: str = (
                row.indent
                + typer.style(row.label, fg=row.color, bold=True)
                + typer.style(rest, fg=row.color)
                + pad
            )
            typer.echo("│ " + styled + " │")
    typer.echo("└" + "─" * (box_width + 2) + "┘")
