# src/discography_toolkit/cli/commands/layout.py
"""The `rapt layout` command.

Lays out a discography's folders: it renames each album from its parts,
renumbers them into one sequence, title-cases every track filename, files
each album on the correct side of the FLAC container, and labels the
artist with a breakdown of what it holds.

The five run as a protocol, not five passes that could be reordered
freely. Each reads what the last one wrote -- numbering sorts on the year
that naming puts at the front, the label counts what placement has
settled -- so a step has to be applied before the next can plan against
it. That is also why there is no dry run: a preview of step two would be
a preview of a tree that does not exist yet, since step one has not run.
The single confirmation stands in for it.

Artists are found by their audio rather than a label, since the label is
what this pass creates -- an artist has none until it has been here. An
artist whose albums are every one an empty placeholder holds no audio to
find, and is left for a path pointed straight at it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Runtime import, not a type-checking one: Typer reads a command's
# annotations at registration, so every name in the signature must exist
# in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from discography_toolkit.cli.console import echo_banner
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core.layout import (
    discover_albums,
    find_artists,
    find_audio_files,
    find_containers,
)
from discography_toolkit.operations import artist_label, naming, numbering, placement, track_naming

if TYPE_CHECKING:
    from collections.abc import Sequence


# ==================================================================================== #
#                                     RESULT TYPE                                      #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class ArtistResult:
    """What laying out one artist changed.

    Attributes:
        artist: The artist folder after the run, renamed by the label.
        named: Album folders renamed.
        numbered: Album folders renumbered.
        cased: Track filenames recased.
        moved: Albums moved across the container.
        labelled: Whether the artist folder was renamed.
        failures: How many individual operations failed.
    """

    artist: Path
    named: int
    numbered: int
    cased: int
    moved: int
    labelled: bool
    failures: int

    @property
    def changed(self) -> bool:
        """Whether anything about the artist changed."""
        return bool(self.named or self.numbered or self.cased or self.moved or self.labelled)


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def layout(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to lay out. A shelf of artists, or a single artist.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation and lay out straight away."),
    ] = False,
) -> None:
    """Rename, renumber, recase, file, and label a discography's folders.

    Args:
        path: Folder to lay out; prompted for if omitted.
        assume_yes: Proceed without asking to confirm.

    Raises:
        typer.Exit: On an invalid path, no artist found, a user abort, or
            a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to lay out")
    artists: list[Path] = find_artists(target)
    echo_banner("Layout", target.name, children=[artist.name for artist in artists])

    if not artists:
        typer.secho(
            f"\nNo artist folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Artists are recognised by their audio -- point at one holding tracks.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo()
    warning: str = f"This will rename, renumber, recase, file, and label {len(artists)} artist(s) -- there is no dry run."
    typer.secho(warning, fg=typer.colors.YELLOW)
    if not assume_yes and not typer.confirm("Proceed?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()
    results, blocked = run(artists)
    _echo_summary(results, blocked)


def run(artists: Sequence[Path]) -> tuple[list[ArtistResult], list[Path]]:
    """Lay out each artist in turn, printing its line as it finishes.

    The loop the command runs after confirming, lifted out so the
    organize command can drive it too without a second confirmation.

    Args:
        artists: The artist folders to lay out.

    Returns:
        The result for each artist laid out, and the artists skipped for
        holding more than one container.
    """
    results: list[ArtistResult] = []
    blocked: list[Path] = []
    for artist in artists:
        if len(find_containers(artist)) > 1:
            # Two containers cannot be merged without guessing, so the
            # artist is skipped whole rather than half-laid-out.
            blocked.append(artist)
            typer.secho(
                f"  skipped  {artist.name!r} -- more than one FLAC container", fg=typer.colors.RED
            )
            continue
        result: ArtistResult = _lay_out(artist)
        results.append(result)
        _echo_artist(result)

    return results, blocked


# ==================================================================================== #
#                                     THE PROTOCOL                                     #
# ==================================================================================== #
def _lay_out(artist: Path) -> ArtistResult:
    """Run the five steps on one artist, each against the last one's output.

    Albums and tracks are re-read before every step that needs them,
    because the step before renamed or moved what this one reads. The
    label runs last: it renames the artist folder, which invalidates
    every path beneath it.

    Args:
        artist: The artist folder to lay out.

    Returns:
        A tally of what changed.
    """
    named = naming.apply(naming.plan(discover_albums(artist)))
    numbered = numbering.apply(numbering.plan(discover_albums(artist)))
    cased = track_naming.apply(track_naming.plan(find_audio_files(artist)))
    placed = placement.apply(placement.plan(artist))
    labelled = artist_label.apply(artist_label.plan(artist))

    failures: int = (
        len(named.failures) + len(numbered.failures) + len(cased.failures) + len(placed.failures)
    )
    if labelled.detail is not None:
        failures += 1

    return ArtistResult(
        artist=labelled.artist,
        named=named.renamed,
        numbered=numbered.renamed,
        cased=cased.renamed,
        moved=placed.moved,
        labelled=labelled.renamed,
        failures=failures,
    )


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_artist(result: ArtistResult) -> None:
    """Print one line for an artist as it finishes, plus its changes.

    The name carries its own outcome -- a fresh label means it was laid
    out -- so it stands alone, green when something changed and dim when
    nothing did. What changed is listed beneath it.

    Args:
        result: What laying the artist out changed.
    """
    if not result.changed:
        typer.secho(f"  {result.artist.name!r}", fg=typer.colors.BRIGHT_BLACK)
        return

    typer.secho(f"  {result.artist.name!r}", fg=typer.colors.GREEN)
    typer.secho(f"      {_phrase(result)}", fg=typer.colors.BRIGHT_BLACK)
    if result.failures:
        typer.secho(f"      {result.failures} operation(s) failed", fg=typer.colors.RED)


def _phrase(result: ArtistResult) -> str:
    """Name only the kinds of change there were, for one artist's line.

    Args:
        result: The artist's tally.

    Returns:
        A comma-joined phrase, e.g. "3 named, 40 recased".
    """
    parts: list[str] = []
    if result.named:
        parts.append(f"{result.named} named")
    if result.numbered:
        parts.append(f"{result.numbered} renumbered")
    if result.cased:
        parts.append(f"{result.cased} recased")
    if result.moved:
        parts.append(f"{result.moved} moved")
    if result.labelled:
        parts.append("labelled")
    return ", ".join(parts)


def _echo_summary(results: list[ArtistResult], blocked: list[Path]) -> None:
    """Print the closing totals across every artist.

    Args:
        results: What each processed artist changed.
        blocked: Artists skipped for holding two containers.
    """
    changed: int = sum(1 for result in results if result.changed)
    failures: int = sum(result.failures for result in results)

    typer.secho(
        f"\nDone. {changed} of {len(results)} artist(s) changed.",
        fg=typer.colors.GREEN,
        bold=True,
    )
    if blocked:
        typer.secho(
            f"{len(blocked)} artist(s) skipped -- resolve their containers and rerun.",
            fg=typer.colors.RED,
        )
    if failures:
        typer.secho(f"{failures} operation(s) failed across the run.", fg=typer.colors.RED)
