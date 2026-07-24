# src/discography_toolkit/operations/artist_label.py
"""Labelling an artist folder with a breakdown of what it holds.

    Charlie Mariano - [90 • 60F • 0L • 30M]

read as ninety albums: sixty lossless, none lossy, thirty missing. The
three counts partition the total -- every album is counted once -- so
they always sum to it and the label checks itself.

The counts come from the albums' files, not their names, so the label is
right whatever the folders are called and wherever they sit. Opus is
counted as lossy: it is held, just not losslessly, and the label draws
the same line placement does -- lossless on one side, everything else
held on the other, missing apart.

This is the describing half of what the old placement step did, kept
separate from the moving half. It renames the artist folder, so it runs
last of anything working beneath that folder -- the rename invalidates
every path under it.

Planning never writes. Applying renames the folder, or leaves it be when
the label is already right.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from discography_toolkit.core import names
from discography_toolkit.core.layout import AudioTier, detect_tier, discover_albums

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class ArtistLabel:
    """The label an artist folder should carry, and the counts behind it.

    Attributes:
        artist: The artist folder as it stands.
        new_name: The name it should carry, label included.
        total: How many albums it holds, both sides together.
        flac: How many are lossless.
        lossy: How many are held lossily, opus among them.
        missing: How many are empty placeholders.
    """

    artist: Path
    new_name: str
    total: int
    flac: int
    lossy: int
    missing: int

    @property
    def needs_rename(self) -> bool:
        """Whether applying this would change the folder on disk."""
        return self.new_name != self.artist.name

    @property
    def target(self) -> Path:
        """Where the folder would be renamed to."""
        return self.artist.with_name(self.new_name)


@dataclass(frozen=True, slots=True)
class LabelReport:
    """What happened when a label was applied.

    Attributes:
        artist: The artist folder after the run, renamed or not.
        renamed: Whether the folder's name changed.
        detail: The failure's reason, or `None` on success.
    """

    artist: Path
    renamed: bool = False
    detail: str | None = None


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    artist: Path,
    on_progress: Callable[[Path], None] | None = None,
) -> ArtistLabel:
    """Count an artist's albums by tier and work out its label.

    Args:
        artist: The artist folder to label.
        on_progress: Called with each album as it is counted, so a caller
            can drive a display without this module knowing one exists.

    Returns:
        The label the folder should carry, and the counts behind it.
    """
    flac: int = 0
    lossy: int = 0
    missing: int = 0
    for album in discover_albums(artist):
        match detect_tier(album):
            case AudioTier.LOSSLESS:
                flac += 1
            case AudioTier.OPUS | AudioTier.LOSSY:
                lossy += 1
            case AudioTier.NONE:
                missing += 1
        if on_progress is not None:
            on_progress(album)

    total: int = flac + lossy + missing
    label: str = names.format_artist_label(total, flac, lossy, missing)
    return ArtistLabel(
        artist=artist,
        new_name=names.with_artist_label(artist.name, label),
        total=total,
        flac=flac,
        lossy=lossy,
        missing=missing,
    )


def apply(artist_label: ArtistLabel) -> LabelReport:
    """Rename the artist folder to carry its label, if it needs it.

    Args:
        artist_label: A plan produced by `plan`.

    Returns:
        The folder's path after the run, whether it changed, and any
        failure.
    """
    if not artist_label.needs_rename:
        return LabelReport(artist=artist_label.artist, renamed=False)

    target: Path = artist_label.target
    try:
        _ = artist_label.artist.rename(target)
    except OSError as exc:
        return LabelReport(artist=artist_label.artist, renamed=False, detail=str(exc))
    return LabelReport(artist=target, renamed=True)
