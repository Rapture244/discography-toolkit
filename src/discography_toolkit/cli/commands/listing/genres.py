# src/discography_toolkit/cli/commands/listing/genres.py
"""The `rapt list genres` command.

A survey, not a change: what genre every track beneath a path is going
to carry, counted, so a convention can be seen whole instead of one
album at a time.

Resolved exactly as `tags genre` resolves it -- nearest `.genre` wins,
searched no higher than the path given, and the file's own tag where no
declaration reaches it. Sharing `core.declarations` is what keeps the two
honest: a survey answering differently from the command that writes would
be worse than no survey.

Sorted by value rather than by count, which is the whole point. Near
misses land next to each other -- "(JP) Shakuhachi" directly above
"(JPN) Shakuhachi" -- so a convention that drifted announces itself
without anything having to guess at what looks similar.

One bar, no per-artist breakdown. A survey is asked about a path, not
about the artists under it, and a whole discography would otherwise
print three hundred names above a dozen result lines.
"""

from __future__ import annotations

from collections import Counter

# Runtime import, not a type-checking one: Typer resolves annotations with
# get_type_hints() when it builds the command, so every name used in a
# signature has to exist in module globals.
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
from discography_toolkit.cli.scope import require_tracks, resolve_path
from discography_toolkit.core import declarations, metadata
from discography_toolkit.core.metadata import Tag

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# What settled a track's genre. Shown per line because the two mean
# different things: a declared value is what the next `tags genre` run
# would write, which is not necessarily what the files hold yet.
_DECLARED: str = "declared"
_TAGGED: str = "tagged"

# Stands in for the empty string, which would otherwise print as a pair of
# quotes and read as a genre someone had set to nothing.
_UNTAGGED: str = "(none)"


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def genres(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to survey beneath. Any level: album, artist, or a whole shelf.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """List every Genre in use beneath a path, with a count for each.

    A `.genre` file answers for the folder holding it, nearest winning,
    searched no higher than the path given. Where none reaches a track,
    the tag inside the file is read instead.

    Args:
        path: Folder to survey beneath; prompted for if omitted.

    Raises:
        typer.Exit: On an invalid path, an unusable `.genre`, no audio
            found, or a completed survey.
    """
    target: Path = resolve_path(path, "Enter the absolute path to survey beneath")
    # No artist breakdown, and so no walk to find one. A survey answers
    # about the path as a whole -- listing three hundred artists above a
    # dozen result lines buries the answer under the question.
    echo_banner("Genres", target.name)

    tracks: list[Path] = require_tracks(target)

    try:
        declared: Mapping[Path, declarations.Declaration] = declarations.resolve(tracks, target)
    except declarations.UnusableDeclarationError as exc:
        typer.secho(f"\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    counts: Counter[tuple[str, str]] = Counter()
    unreadable: list[Path] = []

    with make_progress() as progress:
        advance = make_bar(progress, target.name, len(tracks))
        for track in tracks:
            _tally(track, declared, counts, unreadable)
            advance(track)

    _echo_genres(counts)
    echo_result("Genres", len(counts), "in use", notices=_notices(unreadable))


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _tally(
    track: Path,
    declared: Mapping[Path, declarations.Declaration],
    counts: Counter[tuple[str, str]],
    unreadable: list[Path],
) -> None:
    """Count one track under the genre it carries, and where that came from.

    A declaration answers without the file being opened at all, which is
    most of why a declared shelf surveys quickly: the tags are only read
    where nothing declared anything.

    Args:
        track: The file to count.
        declared: The declaration reaching each folder holding tracks.
        counts: Running tally, keyed by `(genre, origin)`.
        unreadable: Collects files that would not open.
    """
    declaration: declarations.Declaration | None = declared.get(track.parent)
    if declaration is not None:
        counts[declaration.genre, _DECLARED] += 1
        return

    try:
        current: dict[Tag, str] = metadata.read(track, [Tag.GENRE])
    except Exception:  # noqa: BLE001 - a corrupt file must not stop the survey
        unreadable.append(track)
        return

    counts[current.get(Tag.GENRE, "").strip(), _TAGGED] += 1


def _echo_genres(counts: Counter[tuple[str, str]]) -> None:
    """Print one line per genre, sorted so near misses sit together.

    By value, never by count: sorting is the feature. A shelf carrying
    both "(JP) Koto" and "(JPN) Koto" shows them on adjacent lines, which
    is the cheapest possible way to catch a convention that drifted --
    no guessing at what looks similar, no threshold to tune.

    Untagged files sort to the top, where they read as the worklist they
    are.

    Args:
        counts: The tally, keyed by `(genre, origin)`.
    """
    labelled: list[tuple[str, str, int]] = sorted(
        (value or _UNTAGGED, origin, count) for (value, origin), count in counts.items()
    )

    width: int = max((len(value) for value, _, _ in labelled), default=0)
    digits: int = max((len(str(count)) for _, _, count in labelled), default=0)

    typer.echo()
    for value, origin, count in labelled:
        source: str = typer.style(origin, fg=typer.colors.BRIGHT_BLACK)
        typer.echo(f"  {value:<{width}}   {count:>{digits}} file(s)   {source}")
    typer.echo()


def _notices(unreadable: Sequence[Path]) -> list[Notice]:
    """Report the files that would not open, if any did.

    Args:
        unreadable: The files that could not be read.

    Returns:
        One notice naming them, or nothing when every file opened.
    """
    if not unreadable:
        return []
    return [
        Notice(
            summary=f"{len(unreadable)} file(s) could not be read",
            details=tuple(str(track) for track in unreadable),
        )
    ]
