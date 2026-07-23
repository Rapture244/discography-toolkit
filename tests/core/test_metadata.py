# tests/core/test_metadata.py
"""Tests for reading and writing audio metadata.

Run against real files rather than mocks: the point of this module is
that mutagen behaves differently per format, which a mock would hide.

Only the formats a file can be generated for are covered -- FLAC, OGG,
Opus, MP3, WAV and AIFF, spanning the Vorbis and ID3 families. MP4,
APEv2 and ASF share the same dispatch but are untested here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from mutagen.id3 import ID3, TPE1
import numpy as np
import soundfile as sf

from discography_toolkit.core.metadata import (
    Family,
    Tag,
    UnsupportedFormatError,
    family_of,
    read,
    write,
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
    """Writing all five at once keeps them distinct.

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
        Tag.GENRE: "Jazz",
        Tag.TITLE: "Fall",
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


def test_reading_an_unsupported_format_raises(tmp_path: Path) -> None:
    """A cover image is not a track.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    cover: Path = tmp_path / "cover.jpg"
    _ = cover.write_bytes(b"not audio")

    with pytest.raises(UnsupportedFormatError):
        _ = read(cover, [Tag.ALBUM])
