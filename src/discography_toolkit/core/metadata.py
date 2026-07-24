# src/discography_toolkit/core/metadata.py
"""Reading and writing audio metadata, across every supported format.

The only module that imports mutagen. Everything above it works in plain
strings, which keeps a library with no type information from leaking
`Unknown` through the rest of the package -- and means adding a format
touches one file.

Twelve extensions fall into five families, each with its own key for the
same tag. `_KEYS` is that table; `read` and `write` are the single
dispatch over it.

Formats are exercised by the tests where a file can be generated: FLAC,
OGG, Opus, MP3, WAV and AIFF. MP4, APEv2 and ASF are written from their
specifications and mutagen's API but are not covered by tests.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.dsdiff import DSDIFF
from mutagen.dsf import DSF
from mutagen.flac import FLAC
from mutagen.id3 import Frames
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.trueaudio import TrueAudio
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
class Tag(StrEnum):
    """A metadata field, named independently of how a format stores it."""

    ALBUM = "album"
    ALBUM_ARTIST = "album_artist"
    DATE = "date"
    GENRE = "genre"
    TITLE = "title"


class Family(StrEnum):
    """A group of formats sharing one tagging mechanism."""

    VORBIS = "vorbis"
    ID3 = "id3"
    MP4 = "mp4"
    APEV2 = "apev2"
    ASF = "asf"


_FAMILIES: Final[dict[Family, frozenset[str]]] = {
    Family.VORBIS: frozenset({".flac", ".ogg", ".opus"}),
    Family.ID3: frozenset({".mp3", ".wav", ".aiff", ".aif", ".dsf", ".dff", ".tta"}),
    Family.MP4: frozenset({".m4a"}),
    Family.APEV2: frozenset({".ape", ".wv"}),
    Family.ASF: frozenset({".wma"}),
}

_EXTENSION_FAMILY: Final[dict[str, Family]] = {
    extension: family for family, extensions in _FAMILIES.items() for extension in extensions
}

SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(_EXTENSION_FAMILY)

# The same field under five different names. ALBUM_ARTIST is TPE2 rather
# than TPE1: TPE1 is the per-track performer, which a collaboration album
# needs to keep.
_KEYS: Final[dict[Tag, dict[Family, str]]] = {
    Tag.ALBUM: {
        Family.VORBIS: "album",
        Family.ID3: "TALB",
        Family.MP4: "\xa9alb",
        Family.APEV2: "Album",
        Family.ASF: "WM/AlbumTitle",
    },
    Tag.ALBUM_ARTIST: {
        Family.VORBIS: "albumartist",
        Family.ID3: "TPE2",
        Family.MP4: "aART",
        Family.APEV2: "Album Artist",
        Family.ASF: "WM/AlbumArtist",
    },
    Tag.DATE: {
        Family.VORBIS: "date",
        Family.ID3: "TDRC",
        Family.MP4: "\xa9day",
        Family.APEV2: "Year",
        Family.ASF: "WM/Year",
    },
    Tag.GENRE: {
        Family.VORBIS: "genre",
        Family.ID3: "TCON",
        Family.MP4: "\xa9gen",
        Family.APEV2: "Genre",
        Family.ASF: "WM/Genre",
    },
    Tag.TITLE: {
        Family.VORBIS: "title",
        Family.ID3: "TIT2",
        Family.MP4: "\xa9nam",
        Family.APEV2: "Title",
        Family.ASF: "Title",
    },
}


class UnsupportedFormatError(ValueError):
    """Raised for a file this module has no tagging mechanism for."""


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def family_of(path: Path) -> Family:
    """Identify which tagging mechanism a file uses.

    Args:
        path: The audio file.

    Returns:
        The family its extension belongs to.

    Raises:
        UnsupportedFormatError: If the extension is not supported.
    """
    family: Family | None = _EXTENSION_FAMILY.get(path.suffix.lower())
    if family is None:
        msg = f"Unsupported audio format: {path.suffix}"
        raise UnsupportedFormatError(msg)
    return family


def read(path: Path, tags: Iterable[Tag]) -> dict[Tag, str]:
    """Read tags from a file.

    An absent tag comes back as an empty string rather than `None`. The
    two mean the same thing to every caller -- no value -- and
    collapsing them keeps comparisons from having to handle both.

    Args:
        path: The audio file to read.
        tags: Which fields to read.

    Returns:
        Each requested tag mapped to its value, empty where absent.

    Raises:
        UnsupportedFormatError: If the file's format is not supported.
    """
    family: Family = family_of(path)
    audio = _open(path, family)
    return {tag: _read_one(audio, family, tag) for tag in tags}


def write(path: Path, values: Mapping[Tag, str]) -> None:
    """Write tags to a file and save it.

    All values are written in one pass, so a step setting several fields
    opens and saves the file once.

    An empty value clears the field. ID3 cannot store an empty frame, so
    the frame is removed instead -- the same outcome, since a missing
    frame and an empty one both read back as empty.

    Args:
        path: The audio file to write.
        values: Each field mapped to its new value.

    Raises:
        UnsupportedFormatError: If the file's format is not supported.
    """
    if not values:
        return

    family: Family = family_of(path)
    audio = _open(path, family)
    for tag, value in values.items():
        _write_one(audio, family, tag, value)
    audio.save()


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _open(path: Path, family: Family):
    """Open a file with the right mutagen class, creating tags if absent.

    Args:
        path: The audio file to open.
        family: Its tagging mechanism.

    Returns:
        A mutagen file object with usable tags.
    """
    suffix: str = path.suffix.lower()

    match family:
        case Family.VORBIS:
            if suffix == ".flac":
                return FLAC(path)
            return OggOpus(path) if suffix == ".opus" else OggVorbis(path)
        case Family.MP4:
            audio = MP4(path)
        case Family.APEV2:
            audio = MonkeysAudio(path) if suffix == ".ape" else WavPack(path)
        case Family.ASF:
            return ASF(path)
        case Family.ID3:
            audio = _open_id3(path, suffix)

    if audio.tags is None:
        audio.add_tags()
    return audio


def _open_id3(path: Path, suffix: str):
    """Open an ID3-tagged file.

    MP3 goes through the raw frame interface like the rest, not mutagen's
    Easy wrapper, so every ID3 format takes one code path.

    Note that ID3 still normalizes a numeric genre on load: written as
    "17" it reads back as "Rock", whatever interface is used. A caller
    comparing the two would rewrite the file on every run, so genres are
    best given as text.

    Args:
        path: The audio file to open.
        suffix: Its lowercase extension.

    Returns:
        A mutagen file object carrying ID3 tags.
    """
    match suffix:
        case ".mp3":
            return MP3(path)
        case ".wav":
            return WAVE(path)
        case ".aiff" | ".aif":
            return AIFF(path)
        case ".tta":
            return TrueAudio(path)
        case ".dff":
            return DSDIFF(path)
        case _:
            return DSF(path)


def _read_one(audio, family: Family, tag: Tag) -> str:
    """Read a single tag from an already-open file.

    Args:
        audio: An open mutagen file object.
        family: Its tagging mechanism.
        tag: The field to read.

    Returns:
        The stored value, or an empty string if absent.
    """
    key: str = _KEYS[tag][family]

    if family is Family.ID3:
        frame = audio.tags.get(key) if audio.tags is not None else None
        text = getattr(frame, "text", None)
        return str(text[0]) if text else ""

    values = audio.get(key)
    if not values:
        return ""
    return str(values[0] if isinstance(values, list) else values)


def _write_one(audio, family: Family, tag: Tag, value: str) -> None:
    """Write a single tag into an already-open file, without saving.

    Args:
        audio: An open mutagen file object.
        family: Its tagging mechanism.
        tag: The field to write.
        value: The new value; empty clears the field.
    """
    key: str = _KEYS[tag][family]

    match family:
        case Family.ID3:
            if audio.tags is None:
                audio.add_tags()
            frames = audio.tags
            if frames is None:
                return
            if value:
                frames.setall(key, [Frames[key](encoding=3, text=[value])])
            else:
                frames.delall(key)
        case Family.APEV2:
            if value:
                audio[key] = value
            elif key in audio:
                del audio[key]
        case _:
            if value:
                audio[key] = [value]
            elif key in audio:
                del audio[key]
