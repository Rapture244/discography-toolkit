# src/discography_toolkit/core/artwork.py
"""Album covers as images: what one is, which one wins, how big to store it.

Knows nothing about audio files or folders -- only bytes that are or are
not a picture. Reading a cover out of a track and writing it back is
`metadata`; deciding which cover an album should have is here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import io
from typing import TYPE_CHECKING, Final

from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Sequence

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
JPEG: Final[str] = "image/jpeg"
PNG: Final[str] = "image/png"

_MAGIC: Final[dict[bytes, str]] = {
    b"\xff\xd8\xff": JPEG,
    b"\x89PNG\r\n\x1a\n": PNG,
}

_EXTENSIONS: Final[dict[str, str]] = {JPEG: ".jpg", PNG: ".png"}

# Longest edge for the copy embedded in each track. Embedded art is
# stored once per file, so a 3000px scan costs roughly 100 MB across a
# twenty-track album against 20 MB at this size -- and no player shows
# more than about a thousand pixels.
EMBED_MAX_PIXELS: Final[int] = 1200
EMBED_QUALITY: Final[int] = 90


@dataclass(frozen=True, slots=True)
class Cover:
    """An album cover held in memory.

    Attributes:
        data: The raw image bytes.
        mime: Its type, sniffed from the bytes rather than trusted from a
            filename or a tag.
    """

    data: bytes
    mime: str

    @property
    def extension(self) -> str:
        """The file extension matching what the bytes actually are."""
        return _EXTENSIONS[self.mime]

    @property
    def digest(self) -> str:
        """A content hash, for deciding which cover an album agrees on."""
        return hashlib.sha256(self.data).hexdigest()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def read(data: bytes) -> Cover | None:
    """Build a cover from raw bytes, if they are a usable image.

    The type comes from the leading bytes, not from a filename or a
    declared MIME: trusting either would mean trusting whatever wrote it.

    Args:
        data: Candidate image bytes.

    Returns:
        A `Cover`, or `None` when the bytes are neither JPEG nor PNG.
        Empty bytes match no magic, so they need no separate guard.
    """
    mime: str | None = next(
        (value for magic, value in _MAGIC.items() if data.startswith(magic)), None
    )
    return None if mime is None else Cover(data=data, mime=mime)


def longest_edge(cover: Cover) -> int:
    """Measure a cover's longest side.

    Args:
        cover: The image to measure.

    Returns:
        The longer of width and height, or `0` when the bytes cannot be
        decoded.
    """
    try:
        with Image.open(io.BytesIO(cover.data)) as image:
            return max(image.size)
    except OSError:
        return 0


def choose(covers: Sequence[Cover]) -> Cover | None:
    """Pick the cover an album agrees on.

    Grouped by content hash and decided by count, so one stray back-cover
    scan among a dozen matching fronts cannot win. Ties go to the largest
    image, which is the better one to keep when two are equally popular.

    Args:
        covers: Every cover found across the album.

    Returns:
        The winner, or `None` when there are none.
    """
    if not covers:
        return None

    by_digest: dict[str, list[Cover]] = {}
    for cover in covers:
        by_digest.setdefault(cover.digest, []).append(cover)

    counts: Counter[str] = Counter({digest: len(group) for digest, group in by_digest.items()})
    best: str = max(counts, key=lambda digest: (counts[digest], len(by_digest[digest][0].data)))
    return by_digest[best][0]


def as_jpeg(cover: Cover) -> Cover | None:
    """Re-encode a cover as JPEG, unless it is one already.

    For a copy that must be written under a name promising JPEG. A PNG
    small enough to pass the embedding cap untouched would otherwise be
    saved as "cover.jpg" while holding PNG bytes -- a name that lies, and
    one some players read by extension rather than by content.

    Args:
        cover: The image to convert.

    Returns:
        A JPEG cover, the same object when it already was one, or `None`
        when the bytes will not decode -- in which case the album has no
        artwork worth writing rather than artwork to write badly.
    """
    if cover.mime == JPEG:
        return cover

    try:
        with Image.open(io.BytesIO(cover.data)) as image:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=EMBED_QUALITY)
    except OSError:
        return None

    return Cover(data=buffer.getvalue(), mime=JPEG)


def for_embedding(cover: Cover) -> Cover:
    """Produce the copy that goes inside each track.

    Embedded art is stored once per track, so an oversized scan is
    multiplied by the track count. Anything longer than
    `EMBED_MAX_PIXELS` is resampled and re-encoded as JPEG; anything at
    or below it comes back untouched, so nothing is re-encoded that need
    not be and no generation loss is introduced for free.

    Args:
        cover: The album's chosen cover, at full resolution.

    Returns:
        The cover to embed -- the same object when it is already small
        enough.
    """
    try:
        with Image.open(io.BytesIO(cover.data)) as image:
            if max(image.size) <= EMBED_MAX_PIXELS:
                return cover
            image.thumbnail((EMBED_MAX_PIXELS, EMBED_MAX_PIXELS), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=EMBED_QUALITY)
    except OSError:
        # Undecodable: embed the original rather than dropping a cover
        # the tracks may well accept.
        return cover

    # Downscaling usually shrinks the file, but not always: a large image
    # of flat colour can compress better than its resampled version,
    # whose detail is now per-pixel. The cap exists to save bytes, so it
    # declines to spend any.
    resized: bytes = buffer.getvalue()
    if len(resized) >= len(cover.data):
        return cover

    return Cover(data=resized, mime=JPEG)
