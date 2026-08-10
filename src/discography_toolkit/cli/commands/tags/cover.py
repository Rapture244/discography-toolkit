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
    Notice,
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.scope import artists_in, confirm_or_exit, require_albums, resolve_path
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
    artists: list[Path] = artists_in(target)
    echo_banner("Metadata: Album Cover", target.name, children=artist_names(target, artists))

    albums: list[Path] = require_albums(target)

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

    confirm_or_exit(f"\n{_intent(plan)}?")

    with make_progress(noun="operations") as progress:
        advance = make_advancer(progress, target.name, plan.touched, artists)
        report = covers.apply(plan, on_progress=advance)

    echo_result("Cover files", report.written + report.renamed, "in place")
    if report.deleted:
        echo_result("Duplicates", report.deleted, "removed")
    echo_result("Tracks", report.embedded, "embedded", failures=report.failures)


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


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_plan(plan: covers.CoverPlan) -> None:
    """Render the plan as a line per kind of work, then the work itself.

    Three lines rather than one, because the cover pass does three
    different things and a single count would hide which. What is missing
    hangs off them as notices: an album holding tracks but no artwork is
    a person's problem, while a placeholder folder holding nothing is not
    a fault at all and is only said once, without naming forty of them.

    Args:
        plan: The plan to summarize.
    """
    album_notices: list[Notice] = []
    if plan.without_artwork:
        album_notices.append(
            Notice(
                summary=f"{len(plan.without_artwork)} album(s) hold tracks but no cover",
                details=tuple(f"{album.album.name!r}" for album in plan.without_artwork),
            )
        )
    if plan.empty:
        album_notices.append(
            Notice(summary=f"{len(plan.empty)} placeholder(s) hold no tracks to cover")
        )

    track_notices: list[Notice] = []
    unsupported: int = sum(album.unsupported for album in plan.albums)
    if unsupported:
        note: str = "in formats covers are not written into (APE, WV, WMA)"
        track_notices.append(Notice(summary=f"{unsupported} file(s) {note} -- left untouched"))

    echo_result("Cover files", plan.writes + plan.renames, "to settle", album_notices)
    if plan.deletions:
        echo_result("Duplicates", plan.deletions, "to remove")
    echo_result("Tracks", plan.embeds, "to embed", track_notices)

    _echo_work(plan)


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


def _echo_lines(lines: Iterable[str]) -> None:
    """Print an indented, dimmed list beneath a heading.

    Args:
        lines: The lines to print, already phrased and quoted.
    """
    for line in lines:
        typer.secho(f"  {line}", fg=typer.colors.BRIGHT_BLACK)
