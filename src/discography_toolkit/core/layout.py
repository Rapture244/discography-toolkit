# src/discography_toolkit/core/layout.py
"""How a discography is arranged on disk, and how to walk it.

An artist folder holds album folders plus one lossless container; an
album may hold disc subfolders. This is the only module that knows that
convention, so everything above it asks questions instead of walking.
"""

from __future__ import annotations

from enum import StrEnum
import re
from typing import TYPE_CHECKING, Final

from discography_toolkit.core import metadata
from discography_toolkit.core.metadata import SUPPORTED_EXTENSIONS
from discography_toolkit.core.names import ALBUM_INDEX_RE, ARTIST_LABEL_RE

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# ".m4a" is absent: an MP4 container holds either AAC or ALAC, and only the codec inside says which.
# The quality tiers partition AUDIO_EXTENSIONS; a test asserts it.
LOSSLESS_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".flac", ".wav", ".ape", ".wv", ".tta", ".aiff", ".aif", ".dsf", ".dff"}
)

OPUS_EXTENSIONS: Final[frozenset[str]] = frozenset({".opus"})

# Not lossless, but still proof an album is held rather than missing.
LOSSY_EXTENSIONS: Final[frozenset[str]] = frozenset({".mp3", ".m4a", ".ogg", ".wma"})

# Derived, not listed: an extension counts as audio exactly when metadata
# knows how to tag it. Two hand-kept lists drift, and did -- ".dff" was
# found here and rejected there.
AUDIO_EXTENSIONS: Final[frozenset[str]] = SUPPORTED_EXTENSIONS

# Names a loose cover image is found under, canonical first. Everything else in
# an album folder is a booklet scan, a back cover, or a log -- none of which a
# player looks for.
COVER_STEMS: Final[tuple[str, ...]] = ("cover", "folder", "front", "albumart", "album")

IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".png"})

# Windows writes desktop.ini unasked, so a placeholder holding only these is still empty.
JUNK_FILENAMES: Final[frozenset[str]] = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})

# "FLAC", or an older "FLAC - (56 on 65)". Any other letter means it is an album, not the container.
FLAC_CONTAINER_RE: Final[re.Pattern[str]] = re.compile(
    r"^FLAC(?:[\s\-()\[\]0-9]|on)*$", re.IGNORECASE
)

CONTAINER_NAME: Final[str] = "FLAC"


class AudioTier(StrEnum):
    """What quality an album is held in, decided from its files.

    `NONE` is kept apart from `LOSSY`: they are opposite facts -- an
    album held in a lossy format, versus an album not held at all -- even
    though neither earns a quality tag in a folder name.
    """

    LOSSLESS = "lossless"
    OPUS = "opus"
    LOSSY = "lossy"
    NONE = "none"


# ==================================================================================== #
#                                    IDENTIFICATION                                    #
# ==================================================================================== #
def detect_tier(album: Path) -> AudioTier:
    """Decide an album's audio tier from the files it actually holds.

    Never trusts a text marker in the folder name -- only content decides
    it. Walks recursively, so a multi-disc album split across subfolders
    is still read whole. One lossless file settles it outright: the
    tiers rank lossless over opus over lossy, and the best present wins.

    Most extensions are unambiguous. `.m4a` is the exception -- an MP4
    container holds either AAC or ALAC -- so it is probed per file, and
    counts toward lossless only when confirmed ALAC.

    Args:
        album: The album folder to scan.

    Returns:
        The best tier any file reaches, or `AudioTier.NONE` when the
        folder holds no audio at all.
    """
    has_opus: bool = False
    has_lossy: bool = False

    for entry in album.rglob("*"):
        if not entry.is_file():
            continue
        suffix: str = entry.suffix.lower()
        if suffix in LOSSLESS_EXTENSIONS or (suffix == ".m4a" and metadata.is_lossless_m4a(entry)):
            return AudioTier.LOSSLESS
        if suffix in OPUS_EXTENSIONS:
            has_opus = True
        elif suffix in LOSSY_EXTENSIONS:
            has_lossy = True

    if has_opus:
        return AudioTier.OPUS
    return AudioTier.LOSSY if has_lossy else AudioTier.NONE


def is_flac_container(folder: Path) -> bool:
    """Report whether a folder is the lossless container, not an album.

    Args:
        folder: The folder to test.

    Returns:
        `True` if the folder is bookkeeping and its children are albums.
    """
    return FLAC_CONTAINER_RE.match(folder.name) is not None


def is_artist_folder(folder: Path) -> bool:
    """Report whether a folder looks like an artist rather than an album.

    Only recognizes folders the pipeline has already labelled, so it says
    nothing about fresh material -- use it to report what was found, not
    to decide what gets processed.

    Args:
        folder: The folder to test.

    Returns:
        `True` if the name carries a count label and no album index.
    """
    return (
        ARTIST_LABEL_RE.search(folder.name) is not None
        and ALBUM_INDEX_RE.match(folder.name) is None
    )


def is_hidden(path: Path, relative_to: Path) -> bool:
    """Report whether a path lies inside a hidden folder, or is hidden itself.

    Checks every component below `relative_to` rather than only the final
    name, since the caller may well have pointed at a path containing a
    dotted directory of their own.

    Args:
        path: The path to test.
        relative_to: The root the check starts from.

    Returns:
        `True` if any component below the root starts with a dot.
    """
    return any(part.startswith(".") for part in path.relative_to(relative_to).parts)


def is_effectively_empty(album: Path) -> bool:
    """Report whether an album folder holds nothing but OS bookkeeping.

    Searched recursively, so a placeholder holding only empty subfolders
    still counts as empty.

    Args:
        album: The album folder to inspect.

    Returns:
        `True` if the folder contains no files beyond OS junk.
    """
    return not any(
        entry.is_file() and entry.name.lower() not in JUNK_FILENAMES for entry in album.rglob("*")
    )


# ==================================================================================== #
#                                      DISCOVERY                                       #
# ==================================================================================== #
def find_audio_files(root: Path) -> list[Path]:
    """Collect every audio file anywhere beneath a folder.

    Blind to the structure around it: given an album it returns that
    album's tracks, given an artist folder it returns the whole
    discography, including audio loose in the root.

    Hidden files are skipped -- a macOS "._track.flac" stub carries an
    audio extension without being audio.

    Args:
        root: The folder to scan.

    Returns:
        Audio files beneath `root`, sorted by path.
    """
    return sorted(
        entry
        for entry in root.rglob("*")
        if entry.is_file()
        and entry.suffix.lower() in AUDIO_EXTENSIONS
        and not is_hidden(entry, root)
    )


def discover_albums(artist: Path) -> list[Path]:
    """Find the album folders inside an artist folder.

    The container is unwrapped rather than returned, so albums either
    side of it come back as one list. Disc subfolders are part of their
    album, not albums themselves.

    Args:
        artist: The artist folder to scan.

    Returns:
        Visible album subdirectories, sorted by name.
    """
    albums: list[Path] = []
    for entry in artist.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if is_flac_container(entry):
            albums.extend(
                child
                for child in entry.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            )
            continue
        albums.append(entry)

    return sorted(albums, key=lambda path: path.name)


def owning_folder(path: Path, candidates: Sequence[Path]) -> Path | None:
    """Find which of some folders a path sits under.

    One question asked of two lists: which artist owns this track, and
    which album does. The bodies were identical, so the name is the
    general one.

    Args:
        path: The file or folder to place.
        candidates: Folders it might sit under.

    Returns:
        The containing folder, or `None` when it is under none of them.
    """
    parents = set(path.parents)
    return next((folder for folder in candidates if folder in parents), None)


def find_cover_images(album: Path) -> list[Path]:
    """Find the loose cover images sitting in an album folder.

    Direct children only, and only the conventional names: a booklet
    scan deeper in the tree is not the album's cover, and neither is a
    photo that happens to be there.

    Args:
        album: The album folder to search.

    Returns:
        Matching images, canonical name first, so a caller settling on
        one filename knows which it should be.
    """
    try:
        entries = [entry for entry in album.iterdir() if entry.is_file()]
    except OSError:
        return []

    return [
        entry
        for stem in COVER_STEMS
        for entry in entries
        if entry.stem.lower() == stem and entry.suffix.lower() in IMAGE_EXTENSIONS
    ]


def find_albums(root: Path) -> list[Path]:
    """Find every album folder at or beneath a path.

    Albums are discovered through their artist, so a path with no
    labelled artist beneath it yields none -- the layout pass has not run
    there, and nothing else says which folders are albums.

    Args:
        root: The folder to search from -- a shelf, a region, or an
            artist.

    Returns:
        Album folders, grouped under the artist that holds them.
    """
    return [album for artist in find_artist_folders(root) for album in discover_albums(artist)]


def find_artist_folders(root: Path) -> list[Path]:
    """Find every artist folder at or beneath a path.

    Artists sit at whatever depth the shelf puts them --
    "Jazz/Africa/(Nigeria) - Fela Kuti - [49 on 49]" is two levels below
    "Jazz" -- so the search descends until it finds one, then stops. An
    artist holds albums, never another artist, and descending into one
    would read every album and disc folder it owns to find nothing.

    A path that is itself an artist returns just itself, so the answer to
    "which artists does this cover?" is complete for any path.

    Args:
        root: The folder to search from -- a shelf, a region, or an
            artist.

    Returns:
        Artist folders, in walk order, so artists stay grouped under the
        folder that holds them.
    """
    if is_artist_folder(root):
        return [root]

    # A folder whose name does not match ARTIST_LABEL_RE -- "Miles Davis"
    # rather than "Miles Davis - [65 • 65F • 0L • 0M]" -- is descended
    # into like any other. Its albums stop the walk here instead of
    # letting it run through every disc folder beneath them.
    if is_flac_container(root) or ALBUM_INDEX_RE.match(root.name):
        return []

    found: list[Path] = []
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        if entry.is_dir() and not entry.name.startswith("."):
            found.extend(find_artist_folders(entry))

    return found
