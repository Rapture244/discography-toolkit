# src/discography_toolkit/cli/commands/playlist.py
"""The `rapt playlist` command.

Folds converted albums back into a playlist that reads the way the
discography does. An album leaves as FLAC and returns from a converter
as Opus, in a folder the converter named from the tags it found -- and
the Album tag is the bare title, deliberately, because the discography
is shared. So the conversion arrives with no index, no year, no pin and
no quality word, and this puts them back from the discography.

Two paths: the artist's discography folder, which is read and never
written, and the folder the converter dropped its work into. Everything
that changes happens on the converted side.

The command runs two halves, in that order and only that order: the
folders are folded under one artist folder and given the discography's
names, and only then can the Album tag be read off those names. That is
also why there is no dry run -- a preview of the tag half would describe
folders that do not exist yet -- so there is a single confirmation
instead, as `layout` and `organize` have.

Run it again after changing the discography and the playlist follows:
matching reads the Album tag rather than a folder name, so a folder this
pass already settled is recognised as readily as a fresh conversion. The
discography is the source, so a sync removes as readily as it adds -- an
album unpinned there loses its pin here.
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
    echo_banner,
    echo_result,
    make_bar,
    make_progress,
)
from discography_toolkit.cli.scope import resolve_path
from discography_toolkit.core.layout import discover_albums, find_audio_files, owning_folder
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import strip_artist_label
from discography_toolkit.operations import playlist as folding, tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def playlist(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="The artist's folder in the discography. Read, never written.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    converted: Annotated[
        Path | None,
        typer.Option(
            "--converted",
            "-c",
            help="The folder holding the converted albums.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Fold converted albums into a playlist mirroring the discography.

    Args:
        path: The artist's discography folder; prompted for if omitted.
        converted: The folder holding the converted albums; prompted for
            if omitted.

    Raises:
        typer.Exit: On an invalid path, an unlabelled artist, no album
            found on either side, a user abort, or a completed run.
    """
    disco: Path = resolve_path(path, "Enter the absolute path to the artist in the discography")
    target: Path = resolve_path(converted, "Enter the absolute path holding the converted albums")

    artist_name: str | None = strip_artist_label(disco.name)
    if artist_name is None:
        typer.secho(
            f"\n{disco.name!r} carries no count label, so it names no artist.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Run the layout pass first -- it writes the label this reads from.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    echo_banner("Playlist", artist_name, children=[str(disco), str(target)])

    albums: list[Path] = discover_albums(disco)
    if not albums:
        typer.secho(f"\nNo album found in {disco.name!r}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    # Either the folder a converter dropped its work into, which wants an
    # artist folder made inside it, or a settled playlist that has since
    # been moved somewhere permanent -- which is already that folder.
    # Pointing at the second again should sync it in place rather than
    # fold it into a second folder of the same name one level down.
    artist: Path = target if target.name == artist_name else target / artist_name
    folders: list[Path] = folding.candidates(target, artist)
    if not folders:
        typer.secho(f"\nNo converted album found in {target}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    with make_progress(noun="albums") as progress:
        fold_plan = folding.plan(
            folders,
            artist,
            albums,
            on_progress=make_bar(progress, "Playlist: matching", len(folders)),
        )

    _echo_plan(fold_plan)

    if not fold_plan.matches:
        typer.secho(
            "\nNo converted album matches this discography. Nothing to do.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=0)

    warning: str = f"This moves {len(fold_plan.matches)} album(s) under {artist_name!r}, writes their Album tag and a cover.jpg beside their tracks. There is no dry run."
    typer.secho(f"\n{warning}", fg=typer.colors.YELLOW)
    if not typer.confirm("Proceed?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()
    report = folding.apply(fold_plan)
    echo_result("Albums", report.moved, "folded", failures=report.failures)

    settled: list[Path] = _settled(artist)
    _write_tags(artist, settled)
    _write_covers(settled)


# ==================================================================================== #
#                                    WHAT IS SETTLED                                   #
# ==================================================================================== #
def _settled(artist: Path) -> list[Path]:
    """List the playlist's album folders as they now stand on disk.

    Read from the folder rather than taken from the plan, because the
    fold has just renamed them and one of those renames may have failed.
    What is on disk is what the tags and the covers are written from.

    Args:
        artist: The playlist's artist folder.

    Returns:
        Its album folders, empty when the fold created none.
    """
    if not artist.is_dir():
        return []
    return sorted(
        entry for entry in artist.iterdir() if entry.is_dir() and not entry.name.startswith(".")
    )


# ==================================================================================== #
#                                       THE TAGS                                       #
# ==================================================================================== #
def _write_tags(artist: Path, settled: Sequence[Path]) -> None:
    """Write the Album tag of every track, read off its settled folder.

    Args:
        artist: The playlist's artist folder.
        settled: Its album folders.
    """
    tracks: list[Path] = find_audio_files(artist)
    if not tracks:
        return

    with make_progress() as progress:
        plan = tagging.plan(
            tracks,
            [Tag.ALBUM],
            _wants(settled),
            on_progress=make_bar(progress, "Playlist: reading", len(tracks)),
        )

    with make_progress() as progress:
        pending: list[Path] = [outcome.path for outcome in plan.pending]
        report = tagging.apply(
            plan, on_progress=make_bar(progress, "Playlist: tagging", len(pending))
        )

    echo_result("Album", report.written, "tagged", failures=report.failures)


# ==================================================================================== #
#                                      THE COVERS                                       #
# ==================================================================================== #
def _write_covers(settled: Sequence[Path]) -> None:
    """Write one cover.jpg per album, from the art its tracks carry.

    A phone that will not read the embedded art reads this instead, so
    every album in the playlist ends with one beside its tracks.

    Args:
        settled: The playlist's album folders.
    """
    if not settled:
        return

    with make_progress(noun="albums") as progress:
        plan = folding.plan_covers(
            settled, on_progress=make_bar(progress, "Playlist: reading art", len(settled))
        )

    with make_progress(noun="covers") as progress:
        report = folding.apply_covers(
            plan, on_progress=make_bar(progress, "Playlist: covers", len(plan.writes))
        )

    notices: list[Notice] = []
    if plan.without_artwork:
        notices.append(
            Notice(
                summary=f"{len(plan.without_artwork)} album(s) carry no art to write out",
                details=tuple(f"{album.name!r}" for album in plan.without_artwork),
            )
        )

    echo_result("Covers", report.written, "written", notices, report.failures)


def _wants(albums: Sequence[Path]) -> tagging.Desired:
    """Build the value function: each track named for its settled folder.

    Args:
        albums: The playlist's album folders.

    Returns:
        A `Desired` callable returning nothing for a track under none of
        them, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        folder: Path | None = owning_folder(track, albums)
        return {} if folder is None else {Tag.ALBUM: folding.album_tag(folder.name)}

    return desired


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_plan(fold_plan: folding.PlaylistPlan) -> None:
    """Render the fold as one line, with anything it could not settle.

    Args:
        fold_plan: The plan to summarize.
    """
    notices: list[Notice] = []

    if fold_plan.unmatched:
        notices.append(
            Notice(
                summary=f"{len(fold_plan.unmatched)} folder(s) match no album in the discography",
                details=tuple(f"{album.name!r}" for album in fold_plan.unmatched),
            )
        )
    if fold_plan.untagged:
        notices.append(
            Notice(
                summary=f"{len(fold_plan.untagged)} folder(s) carry no Album tag to match on",
                details=tuple(f"{album.name!r}" for album in fold_plan.untagged),
            )
        )
    if fold_plan.contested:
        notices.append(
            Notice(
                summary=f"{len(fold_plan.contested)} album(s) claimed by more than one folder",
                details=tuple(
                    ", ".join(f"{album.name!r}" for album in group) for group in fold_plan.contested
                ),
            )
        )
    if fold_plan.ambiguous:
        notices.append(
            Notice(
                summary=f"{len(fold_plan.ambiguous)} folder(s) name several albums at once",
                details=tuple(f"{album.name!r}" for album in fold_plan.ambiguous),
            )
        )

    echo_result("Albums", len(fold_plan.pending), "to fold", notices)
    if fold_plan.settled:
        typer.secho(
            f"      {fold_plan.settled} album(s) already in place", fg=typer.colors.BRIGHT_BLACK
        )
