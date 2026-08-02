# tests/helpers.py
"""Builders the suite shares: silent audio, albums of a tier, folder listings.

Imported rather than injected as fixtures. Most callers reach for these
inside their own fixtures, or several times in one test body, where a
fixture parameter would cost a signature and a docstring line at every
call site to save the same few lines here.

`pytest.toml` puts the repository root on `sys.path`, which is what makes
`from tests.helpers import ...` resolve: `--import-mode=importlib`
deliberately leaves `sys.path` alone, so nothing else would.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

import numpy as np
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
