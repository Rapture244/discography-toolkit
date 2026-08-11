# tests/helpers.py
"""What the suite shares: silent audio, albums of a tier, folder and track lookups.

Imported rather than injected as fixtures. Most callers reach for these
inside their own fixtures, or several times in one test body, where a
fixture parameter would cost a signature and a docstring line at every
call site to save the same few lines here.

`pytest.toml` puts the repository root on `sys.path`, which is what makes
`from tests.helpers import ...` resolve: `--import-mode=importlib`
deliberately leaves `sys.path` alone, so nothing else would.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Final, Literal

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

if TYPE_CHECKING:
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Ten milliseconds of silence: long enough that every decoder accepts the
# file as real audio, short enough that a suite writing hundreds of them
# stays quick. Nothing asserts on duration.
_FRAMES: Final[int] = 441
_SAMPLE_RATE: Final[int] = 44100

type Tier = Literal["lossless", "opus", "lossy", "none"]
"""What an album should read as. `"none"` leaves the folder empty."""


# ==================================================================================== #
#                                       BUILDERS                                       #
# ==================================================================================== #
def silence(path: Path) -> None:
    """Write a short silent FLAC, making its parents.

    Args:
        path: Where to write it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(_FRAMES, dtype="float32"), _SAMPLE_RATE, format="FLAC")


def fill(album: Path, tier: Tier = "lossless") -> Path:
    """Build an album folder holding one file of the given tier.

    Only the lossless file is real audio. Every step that reads a tier
    decides it from the extension, so an empty ".opus" or ".mp3" stands
    for its tier without a decoder ever opening it -- and `"none"` leaves
    an empty folder, which is what a missing album is.

    Args:
        album: Where to make the folder.
        tier: What the album should read as.

    Returns:
        The album folder.
    """
    album.mkdir(parents=True, exist_ok=True)
    if tier == "lossless":
        silence(album / "01.flac")
    elif tier == "opus":
        _ = (album / "01.opus").write_bytes(b"x")
    elif tier == "lossy":
        _ = (album / "01.mp3").write_bytes(b"x")
    return album


def subfolders(root: Path) -> set[str]:
    """List every folder beneath a root, relative to it.

    Args:
        root: The folder to walk.

    Returns:
        Relative folder paths, forward-slashed so a test reads the same
        on every platform.
    """
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()}


def only_track(root: Path) -> Path:
    """Return the first track beneath a folder.

    For the tests building an album of exactly one, where "the track" is
    unambiguous and naming its path again would only repeat the fixture.

    Args:
        root: The folder to search.

    Returns:
        Its track.
    """
    return next(root.rglob("*.flac"))


def encode(size: int, seed: int = 0, fmt: str = "JPEG", quality: int = 90) -> bytes:
    """Build image bytes with enough detail that JPEG cannot cheat.

    A flat colour compresses to almost nothing at any size, which would
    hide what the embedding cap does: a downscaled copy has to come out
    measurably smaller, or `for_embedding` reads it as no saving and
    hands the original back.

    The quality is above Pillow's own default for the same reason. It is
    passed only for JPEG, PNG having no such setting.

    Args:
        size: Width and height in pixels.
        seed: Varies the image, so two calls differ.
        fmt: Pillow format name.
        quality: JPEG quality.

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
    if fmt == "JPEG":
        image.save(buffer, fmt, quality=quality)
    else:
        image.save(buffer, fmt)
    return buffer.getvalue()
