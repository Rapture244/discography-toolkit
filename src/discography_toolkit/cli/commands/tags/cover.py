# src/discography_toolkit/cli/commands/tags/cover.py
"""The `rapt tags cover` command.

Settles one front cover per album: a loose file beside the tracks, and a
copy inside each of them. The two drift apart on their own -- a rip
arrives with art in the tags and none on disk, or a file manager leaves
a "folder.jpg" nothing else reads -- and this is what brings them back
together.

The listing reports actions rather than states. Which albums already
have artwork is not something anyone can act on; what is worth reading
is which folders gain a cover file and which tracks get written into.
The two kinds of write are grouped rather than interleaved per album,
because a cover file is the same write every time -- naming it once
above the list of folders it lands in says as much as repeating it
beside each of forty-seven album names, in half the lines.

Empty placeholder folders appear in no listing. They hold no tracks, so
there is nothing to cover and nothing to fix, and where a third of a
discography is placeholders, listing them buries the albums that
genuinely lack art.
"""

from __future__ import annotations

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from discography_toolkit.cli.console import (
    SummaryRow,
    artist_names,
    echo_banner,
    echo_summary,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core.layout import find_albums, find_artist_folders
from discography_toolkit.operations import covers

if TYPE_CHECKING:
    from collections.abc import Iterable


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def cover(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to work beneath. An artist, or a shelf holding several.",
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
    """Settle one front cover per album, on disk and inside every track.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no album found, no audio found, a
            user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to work beneath")
    artists: list[Path] = find_artist_folders(target)
    echo_banner("Metadata: Album Cover", target.name, children=artist_names(target, artists))

    albums: list[Path] = find_albums(target)
    if not albums:
        typer.secho(
            f"\nNo album folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run the layout pass first -- albums are recognized through their artist.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    with make_progress(noun="albums") as progress:
        advance = make_advancer(progress, target.name, albums, artists)
        plan = covers.plan(albums, on_progress=advance)

    # Asked of the plan rather than the tree: the albums have just been
    # read, and walking the whole target again to learn the same thing
    # would be the third pass over it.
    if not any(album.tracks for album in plan.albums):
        typer.secho(f"\nNo audio files found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    _echo_plan(plan)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not plan.changes:
        typer.secho("\nEvery album already has its cover. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm(f"\n{_intent(plan)}?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    with make_progress(noun="operations") as progress:
        advance = make_advancer(progress, target.name, plan.touched, artists)
        report = covers.apply(plan, on_progress=advance)

    for failed, detail in report.failures:
        typer.secho(f"  Failed: {str(failed)!r} - {detail}", fg=typer.colors.RED)

    typer.secho(f"\nDone. {_outcome(report)}.", fg=typer.colors.GREEN, bold=True)
    if report.failures:
        typer.secho(f"{len(report.failures)} operation(s) failed.", fg=typer.colors.RED)


# ==================================================================================== #
#                                       PHRASING                                       #
# ==================================================================================== #
def _intent(plan: covers.CoverPlan) -> str:
    """Phrase what the run is about to do, for the confirmation prompt.

    Args:
        plan: The plan awaiting confirmation.

    Returns:
        A sentence naming only the kinds of work there are.
    """
    parts: list[str] = []
    if plan.writes:
        parts.append(f"write {plan.writes} cover file(s)")
    if plan.renames:
        parts.append(f"rename {plan.renames}")
    if plan.deletions:
        parts.append(f"delete {plan.deletions} duplicate(s)")
    if plan.embeds:
        parts.append(f"embed into {plan.embeds} track(s)")

    return ", ".join(parts).capitalize()


def _outcome(report: covers.CoverReport) -> str:
    """Phrase what the run did, for the closing line.

    Args:
        report: What applying the plan achieved.

    Returns:
        A sentence naming the counts worth reading.
    """
    parts: list[str] = [f"{report.written + report.renamed} cover file(s) in place"]
    if report.deleted:
        parts.append(f"{report.deleted} duplicate(s) removed")
    parts.append(f"{report.embedded} track(s) embedded")

    return ", ".join(parts)


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_plan(plan: covers.CoverPlan) -> None:
    """Render a plan as a summary box, the work, and what is missing.

    Args:
        plan: The plan to summarize.
    """
    _echo_counts(plan)
    _echo_work(plan)
    _echo_missing(plan)


def _echo_counts(plan: covers.CoverPlan) -> None:
    """Print the summary box.

    Two partitions over the same albums, then the work itself. Where
    each cover came from and what the run will do about it are separate
    tallies, so they are separate groups rather than one list adding to
    well over the album count.

    "Empty" is its own row rather than folded into "No cover": a
    placeholder holds no tracks, so it has no artwork by definition and
    is not a fault. Counting it as a missing cover would put a third of
    a discography in the failure column.

    Args:
        plan: The plan to summarize.
    """
    counts: list[list[SummaryRow]] = [
        [SummaryRow(label="Total", count=plan.total, indent="", percent=False)],
        [
            SummaryRow(
                label="From tags",
                count=_sourced(plan, "tags"),
                marker="(<-)",
                color=typer.colors.GREEN,
            ),
            SummaryRow(
                label="From disk",
                count=_sourced(plan, "disk"),
                marker="(<-)",
                color=typer.colors.CYAN,
            ),
            SummaryRow(
                label="No cover",
                count=len(plan.without_artwork),
                marker="(--)",
                color=typer.colors.YELLOW,
            ),
            SummaryRow(
                label="Empty",
                count=len(plan.empty),
                marker="(  )",
                color=typer.colors.BRIGHT_BLACK,
            ),
        ],
        [
            SummaryRow(
                label="Cover files",
                count=plan.writes + plan.renames,
                marker="(->)",
                color=typer.colors.GREEN,
                percent=False,
            ),
            SummaryRow(
                label="Duplicates",
                count=plan.deletions,
                marker="(xx)",
                color=typer.colors.YELLOW,
                percent=False,
            ),
            SummaryRow(
                label="Tracks to embed",
                count=plan.embeds,
                marker="(->)",
                color=typer.colors.GREEN,
                percent=False,
            ),
            SummaryRow(
                label="Tracks settled",
                count=sum(a.settlement.correct for a in plan.albums if a.settlement),
                marker="(==)",
                color=typer.colors.BLUE,
                percent=False,
            ),
        ],
    ]

    echo_summary(counts, total=plan.total)


def _echo_work(plan: covers.CoverPlan) -> None:
    """List what the run will change, grouped by kind of write.

    Args:
        plan: The plan to render.
    """
    writing: list[covers.AlbumPlan] = [
        album for album in plan.albums if album.settlement and album.settlement.write
    ]
    if writing:
        names: str = " / ".join(
            sorted({repr(album.settlement.target.name) for album in writing if album.settlement})
        )
        header: str = f"\nfile  {names}  ({len(writing)} album(s))"
        typer.secho(header, fg=typer.colors.CYAN, bold=True)
        _echo_lines(f"{album.album.name!r}" for album in writing)

    renaming: list[covers.AlbumPlan] = [
        album for album in plan.albums if album.settlement and album.settlement.rename_from
    ]
    if renaming:
        typer.secho(
            f"\nrename  -> 'cover.*'  ({len(renaming)} album(s))",
            fg=typer.colors.CYAN,
            bold=True,
        )
        _echo_lines(
            f"{album.album.name!r}  {album.settlement.rename_from.name!r}"
            for album in renaming
            if album.settlement and album.settlement.rename_from
        )

    deleting: list[covers.AlbumPlan] = [
        album for album in plan.albums if album.settlement and album.settlement.delete
    ]
    if deleting:
        typer.secho(
            f"\ndelete  duplicate image(s)  ({plan.deletions} file(s) in {len(deleting)} album(s))",
            fg=typer.colors.RED,
            bold=True,
        )
        _echo_lines(
            f"{album.album.name!r}  {stale.name!r}"
            for album in deleting
            if album.settlement
            for stale in album.settlement.delete
        )

    embedding: list[covers.AlbumPlan] = [
        album for album in plan.albums if album.settlement and album.settlement.embed
    ]
    if embedding:
        typer.secho(
            f"\ntag   ({plan.embeds} track(s) in {len(embedding)} album(s))",
            fg=typer.colors.GREEN,
            bold=True,
        )
        # Grouped per album, unlike the writes above: the cover file is
        # the same name everywhere, but these are different tracks.
        for album in embedding:
            if album.settlement is None:
                continue
            typer.echo(f"\n  {album.album.name!r}")
            for track in album.settlement.embed:
                typer.secho(
                    f"    {str(track.relative_to(album.album))!r}", fg=typer.colors.BRIGHT_BLACK
                )


def _echo_missing(plan: covers.CoverPlan) -> None:
    """Name the albums holding no artwork, and the files nothing can hold it.

    Args:
        plan: The plan to render.
    """
    if plan.without_artwork:
        typer.secho(
            f"\n{len(plan.without_artwork)} album(s) with no cover:",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        _echo_lines(f"{album.album.name!r}" for album in plan.without_artwork)

    unsupported: int = sum(album.unsupported for album in plan.albums)
    if unsupported:
        note: str = "in formats covers are not written into (APE, WV, WMA)"
        typer.secho(
            f"\n{unsupported} file(s) {note} -- left untouched.",
            fg=typer.colors.YELLOW,
        )


def _echo_lines(lines: Iterable[str]) -> None:
    """Print an indented, dimmed list beneath a heading.

    Args:
        lines: The lines to print, already phrased and quoted.
    """
    for line in lines:
        typer.secho(f"  {line}", fg=typer.colors.BRIGHT_BLACK)


def _sourced(plan: covers.CoverPlan, source: covers.Source) -> int:
    """Count the albums whose cover came from one place.

    Args:
        plan: The plan to count over.
        source: Where the cover was found.

    Returns:
        How many albums settled on a cover from there.
    """
    return sum(1 for album in plan.albums if album.settlement and album.settlement.source == source)
