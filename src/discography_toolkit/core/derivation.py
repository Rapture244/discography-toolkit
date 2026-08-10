# src/discography_toolkit/core/derivation.py
"""What each folder-derived tag should hold, read off the settled folders.

Once the layout pass has run, the structure is the canonical form of the
collection and every tag but genre can be read straight off it. Each of
those readings lives here, once, because two commands ask for them:
`align-tags` writes all of them in one pass, and each `tags` sub-command
writes one on its own. Written twice they drift, and the two then
disagree about the same file without anything failing or being noticed.

Two tags are absent. Genre has nothing in a folder to derive it from and
is given by hand. Title is recased from the tag already there rather
than read off a folder, which is `names.title_case` and needs no
discovery to call.

Values only: a caller turns these into whatever shape its step wants,
which is what keeps the domain from having to know about the tagging
engine above it.

`None` means there is nothing to derive -- the track sits under no
folder of that kind, or the folder carries no year. That is a different
answer from an empty string, which is a value: it clears the field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.core.layout import owning_folder
from discography_toolkit.core.names import (
    album_title,
    extract_year,
    is_approximate_year,
    strip_artist_label,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def album_of(track: Path, albums: Sequence[Path]) -> str | None:
    """Read the Album a track should carry, from the folder holding it.

    The title, with everything the folder says about this shelf left
    behind -- the pin, the index, the availability marker, the quality
    tag. The discography is shared, so an album leaving here should say
    what it is and nothing about where it sat.

    An "(EP)" is not one of those things and travels with the title. It
    says what the release is rather than where it sits, which is as true
    in someone else's library as in this one -- and there is no other tag
    written here that could carry it.

    Args:
        track: The track to place.
        albums: The album folders in scope.

    Returns:
        The album's title, or `None` when the track sits under none of
        them.
    """
    folder: Path | None = owning_folder(track, albums)
    return None if folder is None else album_title(folder.name)


def date_of(track: Path, albums: Sequence[Path]) -> str | None:
    """Read the Date a track should carry, from its album folder's year.

    An approximate year resolves to an empty string, which clears the
    tag: "199x" is not a date, players sort on the field, and ID3 stores
    it in a timestamp frame that refuses the value anyway.

    Args:
        track: The track to place.
        albums: The album folders in scope.

    Returns:
        The year, an empty string for an approximation, or `None` when
        the track sits under no album folder or the folder carries no
        year.
    """
    folder: Path | None = owning_folder(track, albums)
    if folder is None:
        return None

    token: str | None = extract_year(folder.name)
    if token is None:
        return None
    return "" if is_approximate_year(token) else token


def album_artist_of(track: Path, artists: Sequence[Path]) -> str | None:
    """Read the Album Artist a track should carry, from the folder above it.

    The artist folder's name without its count label -- the discography
    the track belongs to, not who played on it. That distinction is why
    this writes Album Artist and leaves Artist alone: a collaboration
    album needs the per-track performer kept.

    Args:
        track: The track to place.
        artists: The artist folders in scope.

    Returns:
        The artist's name, or `None` when the track sits under no artist
        folder or that folder carries no label to strip.
    """
    folder: Path | None = owning_folder(track, artists)
    return None if folder is None else strip_artist_label(folder.name)
