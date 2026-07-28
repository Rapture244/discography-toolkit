# tests/core/test_artwork.py
"""Tests for album covers as images.

Run against real encoded bytes rather than fixtures on disk: the module's
job is to decide things from the bytes, so the bytes are the input.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from discography_toolkit.core.artwork import (
    EMBED_MAX_PIXELS,
    JPEG,
    PNG,
    Cover,
    choose,
    for_embedding,
    longest_edge,
    read,
)

import pytest


# ==================================================================================== #
#                                       HELPERS                                        #
# ==================================================================================== #
def encode(size: int, seed: int = 0, fmt: str = "JPEG", quality: int = 90) -> bytes:
    """Build image bytes with enough detail that JPEG cannot cheat.

    A flat colour compresses to almost nothing at any size, which would
    hide what the size cap does.

    Args:
        size: Width and height in pixels.
        seed: Varies the image, so two calls differ.
        fmt: Pillow format name.
        quality: JPEG quality. Above the module's own, so a re-encode is
            measurably smaller and cannot be mistaken for a pass-through.

    Returns:
        The encoded bytes.
    """
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    for row in range(size):
        draw.line(
            [(0, row), (size, row)],
            fill=((seed * 37 + row) % 256, (row * 3) % 256, (255 - row) % 256),
        )
    buffer = io.BytesIO()
    image.save(buffer, fmt, quality=quality) if fmt == "JPEG" else image.save(buffer, fmt)
    return buffer.getvalue()


# ==================================================================================== #
#                                     IDENTIFYING                                      #
# ==================================================================================== #
def test_read_identifies_a_jpeg() -> None:
    """The type comes from the leading bytes, not from a filename."""
    cover = read(encode(64))

    assert cover is not None
    assert cover.mime == JPEG
    assert cover.extension == ".jpg"


def test_read_identifies_a_png() -> None:
    """A genuine PNG stays a PNG, so the extension tells the truth."""
    cover = read(encode(64, fmt="PNG"))

    assert cover is not None
    assert cover.mime == PNG
    assert cover.extension == ".png"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        b"GIF89a",  # a real format, but not one we store
        b"\xff\xd8",  # a truncated JPEG magic
    ],
)
def test_read_rejects_what_is_not_a_cover(data: bytes) -> None:
    """Anything that is not a JPEG or PNG is not artwork here.

    Args:
        data: Candidate bytes.
    """
    assert read(data) is None


def test_identical_bytes_share_a_digest() -> None:
    """The hash is what lets an album agree on one cover."""
    data: bytes = encode(64)

    assert read(data).digest == read(data).digest  # pyright: ignore[reportOptionalMemberAccess]


def test_different_images_differ_by_digest() -> None:
    """Two covers that look different must not be treated as one."""
    first = read(encode(64, seed=1))
    second = read(encode(64, seed=2))

    assert first is not None
    assert second is not None
    assert first.digest != second.digest


# ==================================================================================== #
#                                      MEASURING                                       #
# ==================================================================================== #
@pytest.mark.parametrize("size", [64, 600, 1200, 3000])
def test_longest_edge(size: int) -> None:
    """The resolution decides which of two covers is the better one.

    Args:
        size: The square image's side.
    """
    cover = read(encode(size))

    assert cover is not None
    assert longest_edge(cover) == size


def test_longest_edge_of_undecodable_bytes() -> None:
    """Bytes that sniff as an image but will not decode measure zero.

    Zero rather than a raise: a corrupt cover should lose a comparison,
    not abandon the run.
    """
    broken = Cover(data=b"\xff\xd8\xff" + b"garbage", mime=JPEG)

    assert longest_edge(broken) == 0


# ==================================================================================== #
#                                       CHOOSING                                       #
# ==================================================================================== #
def test_choose_returns_none_without_covers() -> None:
    """An album with no artwork anywhere has no winner."""
    assert choose([]) is None


def test_the_majority_wins() -> None:
    """One stray back-cover scan must not outvote the real front cover.

    The odd file out is exactly the case this exists for: a rip where
    most tracks agree and one carries something else.
    """
    front = read(encode(600, seed=1))
    # Deliberately the bigger image: deciding on size alone would pick it.
    back = read(encode(1500, seed=2))
    assert front is not None
    assert back is not None

    winner = choose([front, front, front, back])

    assert winner is not None
    assert winner.digest == front.digest


def test_a_tie_goes_to_the_larger_image() -> None:
    """Equally popular means the better copy should win.

    Args:
        None.
    """
    small = read(encode(300, seed=1))
    large = read(encode(1200, seed=2))
    assert small is not None
    assert large is not None

    winner = choose([small, large])

    assert winner is not None
    assert winner.digest == large.digest


def test_a_single_cover_wins_by_default() -> None:
    """One candidate is the answer."""
    only = read(encode(600))
    assert only is not None

    winner = choose([only])

    assert winner is not None
    assert winner.digest == only.digest


# ==================================================================================== #
#                                      EMBEDDING                                       #
# ==================================================================================== #
def test_an_oversized_cover_is_capped() -> None:
    """A 3000px scan is multiplied by the track count, so it is resampled."""
    original = read(encode(3000))
    assert original is not None

    payload = for_embedding(original)

    assert longest_edge(payload) == EMBED_MAX_PIXELS
    assert len(payload.data) < len(original.data)


def test_a_small_cover_is_embedded_untouched() -> None:
    """Below the cap nothing is re-encoded, so no quality is lost for free.

    Encoded above the module's own quality, so a re-encode would shrink
    it: equal size would otherwise be indistinguishable from a
    pass-through.
    """
    original = read(encode(600, quality=100))
    assert original is not None

    payload = for_embedding(original)

    # Identity, not equality: equal bytes also result from a re-encode
    # that the never-grow guard rejected, which would hide a cap applied
    # to everything.
    assert payload is original


def test_a_cover_exactly_at_the_cap_is_untouched() -> None:
    """The boundary is inclusive: at the cap there is nothing to save."""
    original = read(encode(EMBED_MAX_PIXELS, quality=100))
    assert original is not None

    assert for_embedding(original) is original


def test_capping_never_grows_a_cover() -> None:
    """Downscaling usually shrinks a file, but not always.

    A flat PNG compresses to almost nothing; the same picture as a JPEG
    does not. A 2000px flat PNG is around 16 KB and its 1200px JPEG is
    around 23 KB, so capping it would cost bytes rather than save them.
    """
    flat = Image.new("RGB", (2000, 2000), (12, 34, 56))
    buffer = io.BytesIO()
    flat.save(buffer, "PNG")
    original = read(buffer.getvalue())
    assert original is not None

    payload = for_embedding(original)

    assert payload is original


def test_undecodable_bytes_are_embedded_as_found() -> None:
    """A cover that will not decode is passed through, not dropped.

    The tracks may well accept it, and losing artwork is worse than
    keeping one this module could not measure.
    """
    broken = Cover(data=b"\xff\xd8\xff" + b"garbage" * 100, mime=JPEG)

    assert for_embedding(broken).data == broken.data
