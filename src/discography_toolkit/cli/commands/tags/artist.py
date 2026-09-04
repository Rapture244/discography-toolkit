# src/discography_toolkit/cli/commands/tags/artist.py
"""The `rapt tags artist` command.

Writes the Artist tag -- who performed the track -- and the Title, both
from what MusicBrainz holds for that track. The one command here whose
answer comes from outside the shelf, and the one that reads a tag rather
than a folder to decide what to ask.

The two travel together because they are one fact split in half.
MusicBrainz keeps a featured artist in the credit and out of the title,
so a file tagged "Punch Drunk Love (Feat. Kanye West)" by "Common" comes
back as "Punch Drunk Love" by "Common feat. Kanye West". Writing one
without the other would say the guest twice or not at all.

Not the Album Artist: that names the discography the track belongs to
and is read off the artist folder, unchanged by whoever guested on one
song. This is the other half of that distinction, and the reason the
toolkit has kept them apart -- a rap album's tracks share one album
artist and rarely share an artist.

An album is settled by the first rung that holds, and printed when none
do. Each rung names an edition rather than an album, because the
thirteen-track version and the fifteen-track one are different records
and crediting one from the other is the failure this exists to avoid:

1. The MusicBrainz release id, and each file's release-track id. Exact.
2. The barcode, which names the physical product, and then each file's
   ISRC, which names the recording. Exact, and refused outright when the
   barcode turns up more than one release.
3. The barcode, and then the track's position -- and only when the
   folder holds exactly as many tracks as the release does, each number
   present once. The one inferred step here, and it infers from the
   folder being complete rather than from a name.
4. Nothing. The album is named and left alone.
"""

from __future__ import annotations

from collections import Counter

# Runtime import, not a type-checking one: Typer resolves annotations
# with get_type_hints() when it builds the command, so every name used in
# a signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from discography_toolkit.cli.commands.tags.notices import unreadable
from discography_toolkit.cli.console import (
    Notice,
    echo_banner,
    echo_result,
    make_advancer,
    make_bar,
    make_progress,
)
from discography_toolkit.cli.scope import confirm_or_exit, require_tracks, resolve_path
from discography_toolkit.core import metadata, musicbrainz
from discography_toolkit.core.metadata import Tag
from discography_toolkit.core.names import disc_number, track_number
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def artist(
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
    """Write each track's Artist and Title from what MusicBrainz holds.

    Args:
        path: Folder to work beneath; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, no audio found, a user abort, or
            a completed run.
    """
    target: Path = resolve_path(path, "Path to write Artist and Title tags beneath")
    echo_banner("Metadata: Artist", target.name)

    tracks: list[Path] = require_tracks(target)
    albums: dict[Path, dict[Path, metadata.Identifiers]] = _by_album(tracks)

    typer.secho(
        f"\nAsking MusicBrainz about {len(albums)} album(s), one request a second.",
        fg=typer.colors.CYAN,
    )
    with make_progress(noun="albums") as progress:
        advance = make_bar(progress, "Artist: looking up", len(albums))
        wanted, refused = _resolve(albums, advance)

    with make_progress() as progress:
        writing = make_advancer(progress, target.name, list(wanted), [])
        plan = tagging.plan(
            list(wanted), [Tag.ARTIST, Tag.TITLE], _wants(wanted), on_progress=writing
        )

    notices: list[Notice] = [
        notice for notice in (_refused(refused), unreadable(plan)) if notice is not None
    ]
    echo_result("Artist + Title", len(plan.pending), "to tag", notices)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    if not pending:
        typer.secho(
            "\nEvery identified file already carries its credit. Nothing to do.",
            fg=typer.colors.GREEN,
        )
        raise typer.Exit(code=0)

    confirm_or_exit(f"\nWrite Artist and Title to {pending} file(s)?")

    with make_progress() as progress:
        writing = make_advancer(
            progress, target.name, [outcome.path for outcome in plan.pending], []
        )
        report = tagging.apply(plan, on_progress=writing)

    echo_result("Artist + Title", report.written, "tagged", failures=report.failures)


# ==================================================================================== #
#                                     THE LADDER                                       #
# ==================================================================================== #
def _resolve(
    albums: Mapping[Path, Mapping[Path, metadata.Identifiers]],
    on_progress: Callable[[Path], None],
) -> tuple[dict[Path, musicbrainz.Credited], dict[Path, str]]:
    """Settle every album by the first rung that holds.

    Args:
        albums: Each album folder mapped to its tracks and their ids.
        on_progress: Called with each album as it is dealt with.

    Returns:
        Each track mapped to what MusicBrainz says it is, and each album
        that could not be settled mapped to why.
    """
    wanted: dict[Path, musicbrainz.Credited] = {}
    refused: dict[Path, str] = {}

    for album, identified in albums.items():
        try:
            credited, reason = _settle(album, identified)
        except (OSError, TypeError, ValueError) as exc:
            refused[album] = f"MusicBrainz could not be read - {exc}"
        else:
            wanted.update(credited)
            if reason:
                refused[album] = reason
        on_progress(album)

    return wanted, refused


def _settle(
    album: Path, identified: Mapping[Path, metadata.Identifiers]
) -> tuple[dict[Path, musicbrainz.Credited], str]:
    """Work out what each track of one album is, or why it cannot be.

    Args:
        album: The album folder.
        identified: Its tracks and the ids each carries.

    Returns:
        Each track mapped to what MusicBrainz says it is, and the reason
        the album was refused -- empty when it was settled.
    """
    release_id: str = _release_of(identified)
    if not release_id:
        return {}, "no MusicBrainz id and no barcode to look one up by"

    found, failures = musicbrainz.releases_for([release_id])
    if release_id in failures:
        return {}, f"MusicBrainz could not be read - {failures[release_id]}"

    release: musicbrainz.Release = found[release_id]
    for match in (_by_track_id, _by_isrc, _by_position):
        credited = match(album, identified, release)
        if credited:
            return credited, ""

    return {}, "no track of it could be matched to the release it names"


def _release_of(identified: Mapping[Path, metadata.Identifiers]) -> str:
    """Name the release an album's files point at.

    The id when the files carry one, the barcode's release when they do
    not. A barcode carried by more than one release settles nothing, and
    comes back empty like no barcode at all.

    Args:
        identified: An album's tracks and the ids each carries.

    Returns:
        The release id, or empty when the album names none.
    """
    ids: set[str] = {found.release for found in identified.values() if found.release}
    if len(ids) == 1:
        return next(iter(ids))

    barcodes: set[str] = {found.barcode for found in identified.values() if found.barcode}
    if len(barcodes) != 1:
        return ""
    return musicbrainz.release_for_barcode(next(iter(barcodes))) or ""


def _by_track_id(
    _album: Path,
    identified: Mapping[Path, metadata.Identifiers],
    release: musicbrainz.Release,
) -> dict[Path, musicbrainz.Credited]:
    """Match each file by the release-track id it carries.

    Args:
        _album: The album folder, unused on this rung.
        identified: Its tracks and the ids each carries.
        release: The release they name.

    Returns:
        Each track mapped to what it is, empty when any file could not
        be matched this way.
    """
    by_id: dict[str, musicbrainz.Credited] = {
        found.track_id: found.credited for found in release.tracks
    }
    matched: dict[Path, musicbrainz.Credited] = {
        track: by_id[found.track] for track, found in identified.items() if found.track in by_id
    }
    return matched if len(matched) == len(identified) else {}


def _by_isrc(
    _album: Path,
    identified: Mapping[Path, metadata.Identifiers],
    release: musicbrainz.Release,
) -> dict[Path, musicbrainz.Credited]:
    """Match each file by the ISRC of the recording it holds.

    An ISRC shared by two tracks of one release -- the same recording
    listed twice -- is dropped rather than picked between.

    Args:
        _album: The album folder, unused on this rung.
        identified: Its tracks and the ids each carries.
        release: The release they name.

    Returns:
        Each track mapped to what it is, empty when any file could not
        be matched this way.
    """
    seen: Counter[str] = Counter(isrc for found in release.tracks for isrc in found.isrcs)
    by_isrc: dict[str, musicbrainz.Credited] = {
        isrc: found.credited for found in release.tracks for isrc in found.isrcs if seen[isrc] == 1
    }
    matched: dict[Path, musicbrainz.Credited] = {
        track: by_isrc[found.isrc] for track, found in identified.items() if found.isrc in by_isrc
    }
    return matched if len(matched) == len(identified) else {}


def _by_position(
    album: Path,
    identified: Mapping[Path, metadata.Identifiers],
    release: musicbrainz.Release,
) -> dict[Path, musicbrainz.Credited]:
    """Match each file by its place in the running order.

    The one inferred rung, and it infers from the folder rather than from
    a name: when the folder holds exactly as many tracks as the release,
    and every position is present once on both sides, there is only one
    way to lay them against each other. Any other shape -- a track
    missing, a number repeated, a bonus track the release does not have
    -- and the mapping stops being forced, so it is refused.

    Args:
        album: The album folder, unused beyond naming the refusal.
        identified: Its tracks and the ids each carries.
        release: The release they name.

    Returns:
        Each track mapped to what it is, empty when the two sides do not
        line up exactly.
    """
    _ = album
    if len(release.tracks) != len(identified):
        return {}

    by_place: dict[tuple[int, int], musicbrainz.Credited] = {
        (found.disc, found.position): found.credited for found in release.tracks
    }
    if len(by_place) != len(release.tracks):
        return {}

    matched: dict[Path, musicbrainz.Credited] = {}
    for track in identified:
        place: tuple[int, int] | None = _place_of(track)
        if place is None or place not in by_place:
            return {}
        matched[track] = by_place[place]

    return matched


def _place_of(track: Path) -> tuple[int, int] | None:
    """Read one file's place in the running order, from its own tags.

    Args:
        track: The audio file to read.

    Returns:
        `(disc, position)`, or `None` when the position is missing or is
        not a number. A file carrying no disc is on disc one, a
        single-disc album being the ordinary case and the tag cleared
        for it.
    """
    try:
        current: dict[Tag, str] = metadata.read(track, [Tag.DISC, Tag.TRACK])
    except Exception:  # noqa: BLE001 - an unreadable file refuses the rung, not the run
        return None

    position: str | None = track_number(current.get(Tag.TRACK, ""))
    if position is None:
        return None
    disc: str | None = disc_number(current.get(Tag.DISC, ""))
    return int(disc or 1), int(position)


# ==================================================================================== #
#                                   VALUE DERIVATION                                   #
# ==================================================================================== #
def _by_album(tracks: Sequence[Path]) -> dict[Path, dict[Path, metadata.Identifiers]]:
    """Group every track under its folder, with the ids it carries.

    Grouped because the ladder is an album's to climb, not a file's: a
    barcode names the record and a position only means anything against
    the whole running order.

    Args:
        tracks: Every audio file in scope.

    Returns:
        Each album folder mapped to its tracks and their identifiers.
    """
    albums: dict[Path, dict[Path, metadata.Identifiers]] = {}

    for track in tracks:
        try:
            found = metadata.read_identifiers(track)
        except Exception:  # noqa: BLE001 - an unreadable file is reported, not raised
            found = metadata.Identifiers()
        albums.setdefault(track.parent, {})[track] = found

    return albums


def _wants(answered: Mapping[Path, musicbrainz.Credited]) -> tagging.Desired:
    """Build the value function: each track's own credit and title.

    Both come from the one response, so asking for the title costs no
    request that the credit did not already make.

    Args:
        answered: Each track mapped to what MusicBrainz says it is.

    Returns:
        A `Desired` callable returning nothing for a track MusicBrainz
        did not answer for, which leaves it untouched.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        credited: musicbrainz.Credited | None = answered.get(track)
        if credited is None:
            return {}
        return {Tag.ARTIST: credited.artist, Tag.TITLE: credited.title}

    return desired


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _refused(refused: Mapping[Path, str]) -> Notice | None:
    """Name the albums no rung could settle, and why.

    Named by folder rather than by file, and with the reason, because
    the reasons send you to different places: no id at all is Picard's
    job, a barcode matching several releases is one to pick by hand, and
    a track count that differs means the folder and the release are not
    the same record.

    Args:
        refused: Each album folder mapped to why it was refused.

    Returns:
        The notice, or `None` when every album was settled.
    """
    if not refused:
        return None

    return Notice(
        summary=f"{len(refused)} album(s) could not be matched to a release",
        details=tuple(f"{album.name!r} -- {reason}" for album, reason in sorted(refused.items())),
    )
