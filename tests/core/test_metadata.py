# tests/core/test_metadata.py
"""Tests for reading and writing audio metadata.

Run against real files rather than mocks: the point of this module is
that mutagen behaves differently per format, which a mock would hide.

Only the formats a file can be generated for are covered -- FLAC, OGG,
Opus, MP3, WAV and AIFF, spanning the Vorbis and ID3 families. MP4,
APEv2 and ASF share the same dispatch but are untested here.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
import io
import shutil
import subprocess
from typing import TYPE_CHECKING, cast

from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TPE1
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

from discography_toolkit.core import artwork
from discography_toolkit.core.metadata import (
    Family,
    Tag,
    UnsupportedFormatError,
    family_of,
    is_lossless_m4a,
    read,
    read_cover,
    supports_cover,
    write,
    write_cover,
)

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# Formats a silent file can be generated for, with the soundfile format
# name needed to write one.
TESTABLE: dict[str, str] = {
    ".flac": "FLAC",
    ".ogg": "OGG",
    ".opus": "OGG",
    ".mp3": "MP3",
    ".wav": "WAV",
    ".aiff": "AIFF",
}


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def make_track(tmp_path: Path) -> Callable[[str], Path]:
    """Return a factory building a silent audio file of a given extension.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking an extension and returning the file's path.
    """

    def build(extension: str) -> Path:
        path: Path = tmp_path / f"track{extension}"
        # Opus only supports 8/12/16/24/48 kHz.
        rate: int = 48000 if extension == ".opus" else 44100
        subtype: str | None = "OPUS" if extension == ".opus" else None
        sf.write(
            path,
            np.zeros(rate // 10, dtype="float32"),
            rate,
            format=TESTABLE[extension],
            subtype=subtype,
        )
        return path

    return build


def id3_text(path: Path, frame_id: str) -> str:
    """Read a raw ID3 frame's text, for asserting on storage keys.

    mutagen ships no type information, so the cast is where that stops
    rather than spreading through every assertion.

    Args:
        path: The audio file to read.
        frame_id: The frame to read, e.g. `"TPE2"`.

    Returns:
        The frame's first text value, or an empty string if absent.
    """
    frame = cast("object", ID3(path).get(frame_id))
    text = cast("list[str] | None", getattr(frame, "text", None))
    return str(text[0]) if text else ""


# ==================================================================================== #
#                                  FORMAT IDENTIFICATION                               #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("extension", "family"),
    [
        (".flac", Family.VORBIS),
        (".opus", Family.VORBIS),
        (".mp3", Family.ID3),
        (".wav", Family.ID3),
        (".dsf", Family.ID3),
        (".m4a", Family.MP4),
        (".ape", Family.APEV2),
        (".wma", Family.ASF),
    ],
)
def test_family_of(tmp_path: Path, extension: str, family: Family) -> None:
    """Each extension maps to the mechanism that stores its tags.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        extension: The file extension under test.
        family: The family it should resolve to.
    """
    assert family_of(tmp_path / f"track{extension}") is family


def test_family_of_is_case_insensitive(tmp_path: Path) -> None:
    """A file written as ".FLAC" is still a FLAC file.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    assert family_of(tmp_path / "track.FLAC") is Family.VORBIS


def test_family_of_rejects_an_unknown_format(tmp_path: Path) -> None:
    """An unsupported extension raises rather than guessing.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    with pytest.raises(UnsupportedFormatError):
        _ = family_of(tmp_path / "cover.jpg")


# ==================================================================================== #
#                                      ROUND TRIP                                      #
# ==================================================================================== #
@pytest.mark.parametrize("extension", list(TESTABLE))
@pytest.mark.parametrize("tag", list(Tag))
def test_every_tag_round_trips(make_track: Callable[[str], Path], extension: str, tag: Tag) -> None:
    """What goes in comes back, for every field in every format.

    DATE gets a year rather than prose: ID3 stores it in a timestamp
    frame, which refuses anything that is not a date.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
        tag: The field under test.
    """
    track: Path = make_track(extension)
    value: str = "1959" if tag is Tag.DATE else "Kind of Blue"

    write(track, {tag: value})

    assert read(track, [tag])[tag] == value


@pytest.mark.parametrize("extension", list(TESTABLE))
def test_absent_tags_read_as_empty(make_track: Callable[[str], Path], extension: str) -> None:
    """A file with no tags reports empty strings, not `None`.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)

    assert read(track, list(Tag)) == dict.fromkeys(Tag, "")


@pytest.mark.parametrize("extension", list(TESTABLE))
def test_several_tags_written_in_one_pass(
    make_track: Callable[[str], Path], extension: str
) -> None:
    """Writing a whole set opens and saves the file once.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)

    write(track, {Tag.ALBUM: "Nefertiti", Tag.DATE: "1968", Tag.GENRE: "Jazz"})

    assert read(track, [Tag.ALBUM, Tag.DATE, Tag.GENRE]) == {
        Tag.ALBUM: "Nefertiti",
        Tag.DATE: "1968",
        Tag.GENRE: "Jazz",
    }


@pytest.mark.parametrize("extension", list(TESTABLE))
def test_an_empty_value_clears_a_tag(make_track: Callable[[str], Path], extension: str) -> None:
    """Writing empty removes the field, however the format stores absence.

    ID3 cannot hold an empty frame, so it is deleted instead -- the two
    read back the same.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    write(track, {Tag.DATE: "1968"})

    write(track, {Tag.DATE: ""})

    assert read(track, [Tag.DATE])[Tag.DATE] == ""


@pytest.mark.parametrize("extension", list(TESTABLE))
def test_writing_one_tag_leaves_the_others(
    make_track: Callable[[str], Path], extension: str
) -> None:
    """A later write must not clear fields it says nothing about.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    write(track, {Tag.ALBUM: "Nefertiti", Tag.GENRE: "Jazz"})

    write(track, {Tag.GENRE: "Hard Bop"})

    assert read(track, [Tag.ALBUM])[Tag.ALBUM] == "Nefertiti"


@pytest.mark.parametrize("extension", list(TESTABLE))
def test_every_tag_has_its_own_slot(make_track: Callable[[str], Path], extension: str) -> None:
    """Writing every tag at once keeps them distinct.

    A round-trip proves nothing about which key a tag uses: a wrong key
    still reads back what it wrote. Writing every field together is what
    catches two of them sharing one slot.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    values: dict[Tag, str] = {
        Tag.ALBUM: "Nefertiti",
        Tag.ALBUM_ARTIST: "Miles Davis",
        Tag.DATE: "1968",
        Tag.DISC: "2",
        Tag.GENRE: "Jazz",
        Tag.TITLE: "Fall",
        Tag.TRACK: "05",
    }

    write(track, values)

    assert read(track, list(Tag)) == values


def test_album_artist_is_tpe2_not_tpe1(make_track: Callable[[str], Path]) -> None:
    """Album Artist must not overwrite the per-track performer.

    TPE1 is who played on the track; TPE2 is whose discography it belongs
    to. A collaboration album needs both, and they differ.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track(".mp3")
    performer = ID3()  # the generated file carries no ID3 header yet
    performer.add(TPE1(encoding=3, text=["Miles Davis & John Coltrane"]))
    performer.save(track)

    write(track, {Tag.ALBUM_ARTIST: "Miles Davis"})

    assert id3_text(track, "TPE2") == "Miles Davis"
    assert id3_text(track, "TPE1") == "Miles Davis & John Coltrane"


# ==================================================================================== #
#                                      TAG VALUES                                      #
# ==================================================================================== #
@pytest.mark.parametrize("extension", [".flac", ".ogg", ".opus"])
def test_a_numeric_genre_survives_outside_id3(
    make_track: Callable[[str], Path], extension: str
) -> None:
    """Vorbis stores a genre as given, numbers included.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)

    write(track, {Tag.GENRE: "17"})

    assert read(track, [Tag.GENRE])[Tag.GENRE] == "17"


@pytest.mark.parametrize("extension", [".mp3", ".wav", ".aiff"])
def test_id3_normalizes_a_numeric_genre(make_track: Callable[[str], Path], extension: str) -> None:
    """ID3 resolves "17" to "Rock" on load, and nothing can stop it.

    Recorded rather than wished away: mutagen normalizes the frame when
    reading the file, so a caller comparing what it wrote against what
    comes back would rewrite on every run. Genres belong as text.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)

    write(track, {Tag.GENRE: "17"})

    assert read(track, [Tag.GENRE])[Tag.GENRE] == "Rock"


@pytest.mark.parametrize("extension", list(TESTABLE))
@pytest.mark.parametrize(
    "value",
    [
        "Jazz;Jazz Fusion",  # a compound genre stays one string
        "©43. (1970) - Bitches Brew",
        "四人囃子",
        "»Eine Einzige Stunde Frei Sein«",
        "Charlie Mariano - [90 • 60F • 0L • 30M]",
    ],
)
def test_awkward_values_survive(
    make_track: Callable[[str], Path], extension: str, value: str
) -> None:
    """Separators, marks and non-Latin scripts round-trip unchanged.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
        value: The value under test.
    """
    track: Path = make_track(extension)

    write(track, {Tag.ALBUM: value})

    assert read(track, [Tag.ALBUM])[Tag.ALBUM] == value


@pytest.mark.parametrize("extension", list(TESTABLE))
def test_writing_nothing_is_a_no_op(make_track: Callable[[str], Path], extension: str) -> None:
    """An empty mapping must not touch the file.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    before: bytes = track.read_bytes()

    write(track, {})

    assert track.read_bytes() == before


# ==================================================================================== #
#                                        COVERS                                        #
# ==================================================================================== #
COVERABLE: list[str] = [".flac", ".ogg", ".opus", ".mp3", ".wav", ".aiff"]


def artwork_bytes(size: int = 64, seed: int = 0) -> bytes:
    """Build a small JPEG to embed.

    Args:
        size: Width and height in pixels.
        seed: Varies the image, so two calls differ.

    Returns:
        The encoded bytes.
    """
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    for row in range(size):
        draw.line([(0, row), (size, row)], fill=((seed * 60 + row) % 256, row % 256, 200))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


@pytest.mark.parametrize("extension", COVERABLE)
def test_a_cover_round_trips(make_track: Callable[[str], Path], extension: str) -> None:
    """What goes in comes back, byte for byte.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    cover = artwork.read(artwork_bytes())
    assert cover is not None

    write_cover(track, cover)

    stored = read_cover(track)
    assert stored is not None
    assert stored.data == cover.data


@pytest.mark.parametrize("extension", COVERABLE)
def test_a_file_without_a_cover_reads_as_none(
    make_track: Callable[[str], Path], extension: str
) -> None:
    """No artwork is not an error.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    assert read_cover(make_track(extension)) is None


@pytest.mark.parametrize("extension", COVERABLE)
def test_replacing_a_cover_leaves_one(make_track: Callable[[str], Path], extension: str) -> None:
    """A second write replaces the front cover rather than adding to it.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    first = artwork.read(artwork_bytes(seed=1))
    second = artwork.read(artwork_bytes(seed=2))
    assert first is not None
    assert second is not None

    write_cover(track, first)
    write_cover(track, second)

    stored = read_cover(track)
    assert stored is not None
    assert stored.data == second.data


@pytest.mark.parametrize("extension", COVERABLE)
def test_writing_a_cover_leaves_the_tags(make_track: Callable[[str], Path], extension: str) -> None:
    """Artwork and text are stored side by side, not instead of each other.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    write(track, {Tag.ALBUM: "Nefertiti"})
    cover = artwork.read(artwork_bytes())
    assert cover is not None

    write_cover(track, cover)

    assert read(track, [Tag.ALBUM])[Tag.ALBUM] == "Nefertiti"


@pytest.mark.parametrize("extension", COVERABLE)
def test_writing_tags_leaves_the_cover(make_track: Callable[[str], Path], extension: str) -> None:
    """And the other way round.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    cover = artwork.read(artwork_bytes())
    assert cover is not None
    write_cover(track, cover)

    write(track, {Tag.ALBUM: "Nefertiti"})

    stored = read_cover(track)
    assert stored is not None
    assert stored.data == cover.data


BACK_COVER: int = 4
FRONT_COVER: int = 3


def _open_id3_container(track: Path) -> MP3 | WAVE | AIFF:
    """Open an ID3-carrying file with its own class.

    `mutagen.File` returns a union of every format it knows, which cannot
    be narrowed to something with an ID3 `.tags`.

    Args:
        track: The audio file.

    Returns:
        The file, opened as its own type.
    """
    suffix: str = track.suffix.lower()
    if suffix == ".mp3":
        return MP3(track)
    if suffix == ".wav":
        return WAVE(track)
    return AIFF(track)


def _back_picture(data: bytes) -> Picture:
    """Build a back-cover picture block.

    Args:
        data: The image bytes.

    Returns:
        A `Picture` marked as the back cover.
    """
    picture = Picture()
    picture.data = data
    picture.type = BACK_COVER
    picture.mime = "image/jpeg"
    return picture


def seed_back_cover(track: Path, data: bytes) -> None:
    """Attach a back cover, which a front-cover write must not disturb.

    Files carry several pictures -- a back cover, an artist photo -- and
    only one of them is this module's business.

    Args:
        track: The audio file to seed.
        data: The image bytes.
    """
    suffix: str = track.suffix.lower()

    if suffix == ".flac":
        flac = FLAC(track)
        flac.add_picture(_back_picture(data))
        flac.save()
    elif suffix in {".ogg", ".opus"}:
        vorbis = OggOpus(track) if suffix == ".opus" else OggVorbis(track)
        encoded: list[str] = list(vorbis.get("metadata_block_picture") or [])
        encoded.append(base64.b64encode(_back_picture(data).write()).decode("ascii"))
        vorbis["metadata_block_picture"] = encoded
        vorbis.save()
    else:
        audio = _open_id3_container(track)
        if audio.tags is None:
            audio.add_tags()
        frames = audio.tags
        assert frames is not None
        frames.add(APIC(encoding=3, mime="image/jpeg", type=BACK_COVER, desc="Back", data=data))
        audio.save()


def picture_types(track: Path) -> set[int]:
    """Collect the picture types a file carries.

    Asserted on rather than raw bytes: ID3 may escape a byte sequence on
    write, so a JPEG that survived intact is not findable verbatim.

    Args:
        track: The audio file to inspect.

    Returns:
        Every picture type present.
    """
    suffix: str = track.suffix.lower()

    if suffix == ".flac":
        return {int(picture.type) for picture in FLAC(track).pictures}

    if suffix in {".ogg", ".opus"}:
        vorbis = OggOpus(track) if suffix == ".opus" else OggVorbis(track)
        return {
            int(Picture(base64.b64decode(encoded)).type)
            for encoded in list(vorbis.get("metadata_block_picture") or [])
        }

    audio = _open_id3_container(track)
    frames = audio.tags
    assert frames is not None
    return {int(frame.type) for frame in frames.getall("APIC")}


@pytest.mark.parametrize("extension", COVERABLE)
def test_a_back_cover_is_not_read_as_the_front(
    make_track: Callable[[str], Path], extension: str
) -> None:
    """Only the front cover is this module's business.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    # Seeded first, so a read that took any picture would find this one.
    seed_back_cover(track, artwork_bytes(seed=2))
    front = artwork.read(artwork_bytes(seed=1))
    assert front is not None
    write_cover(track, front)

    stored = read_cover(track)

    assert stored is not None
    assert stored.data == front.data


@pytest.mark.parametrize("extension", COVERABLE)
def test_writing_a_cover_keeps_a_back_cover(
    make_track: Callable[[str], Path], extension: str
) -> None:
    """A front-cover write replaces its own kind and nothing else.

    Args:
        make_track: Factory building an audio file.
        extension: The format under test.
    """
    track: Path = make_track(extension)
    seed_back_cover(track, artwork_bytes(seed=2))
    front = artwork.read(artwork_bytes(seed=1))
    assert front is not None

    write_cover(track, front)

    assert picture_types(track) == {FRONT_COVER, BACK_COVER}


@pytest.mark.parametrize(
    ("extension", "expected"),
    [
        (".flac", True),
        (".mp3", True),
        (".m4a", True),
        # Loosely specified picture storage: reported rather than written
        # badly.
        (".ape", False),
        (".wv", False),
        (".wma", False),
    ],
)
def test_supports_cover(tmp_path: Path, extension: str, *, expected: bool) -> None:
    """Not every format carries artwork this module will write.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        extension: The format under test.
        expected: Whether it should be writable.
    """
    assert supports_cover(tmp_path / f"track{extension}") is expected


def test_reading_a_cover_from_an_unsupported_format_is_none(tmp_path: Path) -> None:
    """A format with no picture mechanism has no cover to report.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    assert read_cover(tmp_path / "track.wma") is None


def test_writing_a_cover_to_an_unsupported_format_raises(tmp_path: Path) -> None:
    """Silently skipping would leave the caller thinking it worked.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    cover = artwork.read(artwork_bytes())
    assert cover is not None

    with pytest.raises(UnsupportedFormatError):
        write_cover(tmp_path / "track.wma", cover)


def test_reading_an_unsupported_format_raises(tmp_path: Path) -> None:
    """A cover image is not a track.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    cover: Path = tmp_path / "cover.jpg"
    _ = cover.write_bytes(b"not audio")

    with pytest.raises(UnsupportedFormatError):
        _ = read(cover, [Tag.ALBUM])


# ==================================================================================== #
#                                        CODEC                                         #
# ==================================================================================== #
def _encode_m4a(path: Path, codec: str) -> bool:
    """Write a short `.m4a` in the given codec, if ffmpeg can.

    Args:
        path: Where to write the file.
        codec: The ffmpeg codec name, `"alac"` or `"aac"`.

    Returns:
        `True` when the file was written, `False` when ffmpeg is absent.
    """
    ffmpeg: str | None = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    _ = subprocess.run(  # noqa: S603 - fully static command, no user input
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            codec,
            str(path),
            "-loglevel",
            "error",
        ],
        check=True,
    )
    return True


def test_alac_reads_as_lossless(tmp_path: Path) -> None:
    """Apple Lossless is the whole reason this probe exists.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    track: Path = tmp_path / "alac.m4a"
    if not _encode_m4a(track, "alac"):
        pytest.skip("ffmpeg not available")

    assert is_lossless_m4a(track) is True


def test_aac_does_not_read_as_lossless(tmp_path: Path) -> None:
    """The same container holding AAC is lossy, and must not pass.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    track: Path = tmp_path / "aac.m4a"
    if not _encode_m4a(track, "aac"):
        pytest.skip("ffmpeg not available")

    assert is_lossless_m4a(track) is False


def test_an_m4a_whose_codec_is_unreadable_is_not_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container that parses but reports no codec string is not trusted.

    mutagen can hand back an info object without a usable `codec`; the
    guard treats that as unconfirmed rather than letting `None.lower()`
    raise mid-scan.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Pytest's attribute patcher.
    """

    class _NoCodec:
        info: object = object()  # an info with no `codec` attribute at all

    def _no_codec(_path: Path) -> _NoCodec:
        return _NoCodec()

    monkeypatch.setattr("discography_toolkit.core.metadata.MP4", _no_codec)

    assert is_lossless_m4a(tmp_path / "whatever.m4a") is False


def test_an_unreadable_m4a_is_not_lossless(tmp_path: Path) -> None:
    """A file that will not parse is not confirmed lossless, and is not raised.

    One corrupt file must not stop a scan, so the answer is a plain
    `False`.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    track: Path = tmp_path / "broken.m4a"
    _ = track.write_bytes(b"not really an mp4 container")

    assert is_lossless_m4a(track) is False
