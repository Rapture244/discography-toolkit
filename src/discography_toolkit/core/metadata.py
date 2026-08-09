# src/discography_toolkit/core/metadata.py
"""Reading and writing audio metadata, across every supported format.

The only module that imports mutagen. Everything above it works in plain
strings, which keeps a library with no type information from leaking
`Unknown` through the rest of the package -- and means adding a format
touches one file.

Twelve extensions fall into five families, each with its own key for the
same tag. `_KEYS` is that table; `read` and `write` are the single
dispatch over it.

Cover art rides along in the same boundary, though not every family
carries it: APEv2 and ASF store pictures in ways too loosely specified to
write blind, so they report as unsupported rather than being written
badly.

Formats are exercised by the tests where a file can be generated: FLAC,
OGG, Opus, MP3, WAV and AIFF. MP4, APEv2 and ASF are written from their
specifications and mutagen's API but are not covered by tests.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from enum import StrEnum
import signal
from typing import TYPE_CHECKING, Final

from mutagen import MutagenError
from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.dsdiff import DSDIFF
from mutagen.dsf import DSF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, Frames
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.trueaudio import TrueAudio
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack

from discography_toolkit.core import artwork
from discography_toolkit.core.artwork import PNG

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Mapping
    from pathlib import Path
    from types import FrameType

    from discography_toolkit.core.artwork import Cover


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
class Tag(StrEnum):
    """A metadata field, named independently of how a format stores it."""

    ALBUM = "album"
    ALBUM_ARTIST = "album_artist"
    ARTIST = "artist"
    DATE = "date"
    DISC = "disc"
    GENRE = "genre"
    TITLE = "title"
    TRACK = "track"


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
    Tag.ARTIST: {
        Family.VORBIS: "artist",
        Family.ID3: "TPE1",
        Family.MP4: "\xa9ART",
        Family.APEV2: "Artist",
        Family.ASF: "Author",
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
    Tag.TRACK: {
        Family.VORBIS: "tracknumber",
        Family.ID3: "TRCK",
        Family.MP4: "trkn",
        Family.APEV2: "Track",
        Family.ASF: "WM/TrackNumber",
    },
    Tag.DISC: {
        Family.VORBIS: "discnumber",
        Family.ID3: "TPOS",
        Family.MP4: "disk",
        Family.APEV2: "Disc",
        Family.ASF: "WM/PartOfSet",
    },
}

# The two tags MP4 stores as numbers rather than strings -- a
# (number, total) pair in an atom, with nowhere to put a leading zero.
# Mapped to the width each is read back at, so a padded track number
# written here compares equal on the next run instead of being rewritten
# every time. A disc number is never padded, and so is read back plain.
_MP4_NUMERIC: Final[dict[Tag, int]] = {Tag.TRACK: 2, Tag.DISC: 1}


# Families whose picture storage is well enough specified to write.
# APEv2 and ASF are deliberately absent.
COVER_FAMILIES: Final[frozenset[Family]] = frozenset({Family.VORBIS, Family.ID3, Family.MP4})

# ID3 picture type 3 is "cover (front)". Only that one is read or
# replaced: a back cover or an artist photo beside it is left alone.
_FRONT_COVER: Final[int] = 3


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


@contextmanager
def uninterrupted() -> Generator[None]:
    """Hold off Ctrl-C until the block finishes.

    Saving a tag is not atomic. A comment header that changes size means
    every page after it moves, so mutagen rewrites the file in place --
    and a Ctrl-C landing in the middle leaves new data running into old,
    unreadable by anything. It has happened: an Opus track cut off
    mid-rewrite, its picture comment sitting where an audio page should
    be.

    One file's write takes milliseconds, so waiting that long costs a
    person nothing and saves the file. The interrupt is not swallowed --
    it is raised the moment the write is done, so a run still stops when
    asked, just between files rather than inside one.

    Only the main thread can install a handler, and only there does
    Python deliver the signal, so anywhere else this does nothing and
    says so by simply running the block.

    The window is one write. A save that hangs cannot be interrupted, and
    nothing here bounds it -- but a hang is mutagen's or the disk's, and
    a corrupt file is worse than a wait.

    Yields:
        Nothing; the block runs with SIGINT held.

    Raises:
        KeyboardInterrupt: After the block, if one arrived during it.
    """
    arrived: list[tuple[int, FrameType | None]] = []

    def defer(number: int, frame: FrameType | None) -> None:
        arrived.append((number, frame))

    try:
        previous = signal.signal(signal.SIGINT, defer)
    except ValueError:
        yield  # not the main thread: nothing to defer
        return

    try:
        yield
    finally:
        _ = signal.signal(signal.SIGINT, previous)
        if arrived:
            if callable(previous):
                previous(*arrived[-1])
            raise KeyboardInterrupt


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
    with uninterrupted():
        audio.save()


# ==================================================================================== #
#                                        CODEC                                         #
# ==================================================================================== #
def is_lossless_m4a(path: Path) -> bool:
    """Report whether a `.m4a` file holds ALAC rather than AAC.

    The extension cannot say: an MP4 container carries either the lossy
    AAC or the lossless ALAC, and only the codec inside tells them apart.
    mutagen reports `"alac"` for Apple Lossless and an `"mp4a"`-prefixed
    value for AAC.

    Args:
        path: A `.m4a` file.

    Returns:
        `True` when the codec is ALAC. `False` for AAC, or when the file
        cannot be read -- an unreadable file is not confirmed lossless,
        and one bad file must not stop a scan.
    """
    try:
        info = MP4(path).info
    except (MutagenError, OSError):
        # ponytail: an unreadable file is reported as AAC, so an album
        # whose `.m4a` tracks cannot be read comes out lossy -- it loses
        # the "[FLAC]" tag it has earned and is filed out of the
        # container. Non-destructive: nothing is deleted, and repairing
        # the file and rerunning settles it. Upgrading means giving
        # `detect_tier` a channel for warnings as well as a tier, and
        # threading it up through naming, placement and the artist label
        # into layout's notices -- four signatures widened for a case
        # that is a corrupt file you would notice by other means.
        return False
    codec = getattr(info, "codec", None)
    return isinstance(codec, str) and codec.lower().startswith("alac")


# ==================================================================================== #
#                                        COVERS                                        #
# ==================================================================================== #
def supports_cover(path: Path) -> bool:
    """Report whether a file's format can carry cover art here.

    Args:
        path: The audio file.

    Returns:
        `True` when its family has a picture mechanism this writes.

    Raises:
        UnsupportedFormatError: If the extension is not supported at all.
    """
    return family_of(path) in COVER_FAMILIES


def read_cover(path: Path) -> Cover | None:
    """Read a file's front cover.

    Args:
        path: The audio file to read.

    Returns:
        The front cover, or `None` when the file carries none or its
        format has no picture mechanism here.

    Raises:
        UnsupportedFormatError: If the extension is not supported at all.
    """
    family: Family = family_of(path)
    if family not in COVER_FAMILIES:
        return None

    for picture in _pictures(path, family):
        if picture.type == _FRONT_COVER:
            return artwork.read(bytes(picture.data))
    return None


def write_cover(path: Path, cover: Cover) -> None:
    """Write a file's front cover and save it.

    Any other picture the file carries -- a back cover, an artist photo
    -- is kept. MP4 is the exception: it has no picture-type concept, so
    its cover list is replaced outright.

    Args:
        path: The audio file to write.
        cover: The image to embed.

    Raises:
        UnsupportedFormatError: If the format has no picture mechanism
            here, or is not supported at all.
    """
    family: Family = family_of(path)
    if family not in COVER_FAMILIES:
        msg = f"Cover art is not supported for {path.suffix}"
        raise UnsupportedFormatError(msg)

    # Each writer saves the file itself, so the guard wraps the dispatch
    # rather than being repeated five times inside it.
    with uninterrupted():
        if family is Family.MP4:
            _write_mp4_cover(path, cover)
        elif family is Family.ID3:
            _write_id3_cover(path, cover)
        elif path.suffix.lower() == ".flac":
            _write_flac_cover(path, cover)
        else:
            _write_ogg_cover(path, cover)


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

    if family is Family.MP4 and tag in _MP4_NUMERIC:
        return _read_mp4_number(audio, key, _MP4_NUMERIC[tag])

    if family is Family.ID3:
        frame = audio.tags.get(key) if audio.tags is not None else None
        text = getattr(frame, "text", None)
        return str(text[0]) if text else ""

    values = audio.get(key)
    if not values:
        return ""
    return str(values[0] if isinstance(values, list) else values)


def _read_mp4_number(audio, key: str, width: int) -> str:
    """Read an MP4 numeric atom, which holds a `(number, total)` pair.

    Args:
        audio: An open MP4 file object.
        key: The atom name.
        width: How many digits to pad the number to.

    Returns:
        The number alone, empty when absent or malformed. The total is
        dropped: a track's own number is what any of this sorts on, and
        the count of its siblings is derivable by looking.
    """
    pairs = audio.get(key)
    if not pairs:
        return ""
    first = pairs[0]
    number = first[0] if isinstance(first, tuple) and first else first
    return f"{int(number):0{width}d}" if isinstance(number, int) else ""


def _write_one(audio, family: Family, tag: Tag, value: str) -> None:
    """Write a single tag into an already-open file, without saving.

    Args:
        audio: An open mutagen file object.
        family: Its tagging mechanism.
        tag: The field to write.
        value: The new value; empty clears the field.
    """
    key: str = _KEYS[tag][family]

    if family is Family.MP4 and tag in _MP4_NUMERIC:
        if value.isdigit():
            audio[key] = [(int(value), 0)]
        elif key in audio:
            del audio[key]
        return

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


def _pictures(path: Path, family: Family) -> list[Picture]:
    """Collect every picture a file carries, whatever the mechanism.

    Opened per family rather than through `_open`: that returns a union,
    and FLAC's picture API is not on it.

    Args:
        path: The audio file to read.
        family: Its tagging mechanism.

    Returns:
        Each picture as a FLAC-style block, so callers compare one shape.
    """
    suffix: str = path.suffix.lower()

    if family is Family.MP4:
        mp4 = MP4(path)
        covers = mp4.tags.get("covr") if mp4.tags is not None else None
        return [_as_picture(bytes(covers[0]))] if covers else []

    if family is Family.ID3:
        id3 = _open_id3(path, suffix)
        if id3.tags is None:
            return []
        return [_as_picture(bytes(frame.data), frame.type) for frame in id3.tags.getall("APIC")]

    if suffix == ".flac":
        return list(FLAC(path).pictures)

    return [Picture(base64.b64decode(encoded)) for encoded in _ogg_encoded(path)]


def _as_picture(data: bytes, kind: int = _FRONT_COVER) -> Picture:
    """Wrap raw picture bytes in a FLAC-style block.

    MP4 has no picture-type concept, so its single cover is taken as the
    front one.

    Args:
        data: The image bytes.
        kind: The picture type, front cover by default.

    Returns:
        A `Picture` carrying the bytes and type.
    """
    picture = Picture()
    picture.data = data
    picture.type = kind
    return picture


def _ogg_encoded(path: Path) -> list[str]:
    """Return an Ogg file's base64 picture comments.

    Args:
        path: The Ogg Vorbis or Opus file.

    Returns:
        The raw comment values, empty when absent.
    """
    audio = OggOpus(path) if path.suffix.lower() == ".opus" else OggVorbis(path)
    return list(audio.get("metadata_block_picture") or [])


def _write_flac_cover(path: Path, cover: Cover) -> None:
    """Replace a FLAC's front cover, keeping any other picture.

    Args:
        path: The FLAC file.
        cover: The image to embed.
    """
    audio = FLAC(path)
    kept = [picture for picture in audio.pictures if picture.type != _FRONT_COVER]
    audio.clear_pictures()
    for picture in [*kept, _build_picture(cover)]:
        audio.add_picture(picture)
    audio.save()


def _write_ogg_cover(path: Path, cover: Cover) -> None:
    """Replace an Ogg file's front cover, keeping any other picture.

    Args:
        path: The Ogg Vorbis or Opus file.
        cover: The image to embed.
    """
    audio = OggOpus(path) if path.suffix.lower() == ".opus" else OggVorbis(path)
    kept: list[str] = [
        encoded
        for encoded in list(audio.get("metadata_block_picture") or [])
        if Picture(base64.b64decode(encoded)).type != _FRONT_COVER
    ]
    kept.append(base64.b64encode(_build_picture(cover).write()).decode("ascii"))
    audio["metadata_block_picture"] = kept
    audio.save()


def _write_id3_cover(path: Path, cover: Cover) -> None:
    """Replace an ID3 front-cover frame, keeping any other picture.

    Args:
        path: The audio file.
        cover: The image to embed.
    """
    audio = _open_id3(path, path.suffix.lower())
    if audio.tags is None:
        audio.add_tags()
    frames = audio.tags
    if frames is None:
        return
    kept = [frame for frame in frames.getall("APIC") if frame.type != _FRONT_COVER]
    kept.append(APIC(encoding=3, mime=cover.mime, type=_FRONT_COVER, desc="Cover", data=cover.data))
    frames.setall("APIC", kept)
    audio.save()


def _write_mp4_cover(path: Path, cover: Cover) -> None:
    """Set an MP4 file's cover atom.

    MP4 has no picture-type concept, so the cover list is replaced
    outright rather than merged.

    Args:
        path: The MP4 file.
        cover: The image to embed.
    """
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    if audio.tags is None:
        return
    image_format = MP4Cover.FORMAT_PNG if cover.mime == PNG else MP4Cover.FORMAT_JPEG
    audio.tags["covr"] = [MP4Cover(cover.data, imageformat=image_format)]
    audio.save()


def _build_picture(cover: Cover) -> Picture:
    """Build a FLAC-style picture block.

    Used directly by FLAC and, base64-encoded, by Ogg Vorbis and Opus.

    Args:
        cover: The image to wrap.

    Returns:
        A `Picture` marked as the front cover.
    """
    picture = Picture()
    picture.data = cover.data
    picture.type = _FRONT_COVER
    picture.mime = cover.mime
    picture.desc = "Cover"
    return picture
