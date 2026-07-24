# src/discography_toolkit/core/names.py
"""What things are called: the patterns in a folder or track name.

`layout` knows where a file sits; this knows what its name means. An
artist folder carries a count label, an album folder carries an index and
a year, and a title follows English casing -- all of it string work, none
of it touching disk.
"""

from __future__ import annotations

import re
from typing import Final

from titlecase import titlecase

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# The count label an artist folder carries: "[90 • 60F • 0L • 30M]", or an
# older "[M31 on 90]". Anchored to the end, so a bracketed number earlier
# in the name is part of the title.
ARTIST_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"\s*(?:-\s*)?\[\s*M?\d[^\[\]]*\]\s*$")

# Guards ARTIST_LABEL_RE: "90. (2013) - In India [1973]" matches the label
# by accident, and only the index tells the two apart.
ALBUM_INDEX_RE: Final[re.Pattern[str]] = re.compile(r"^©?\d+\.")

# A year, with "x" standing in for a digit nobody knows: "1994", "199x",
# "19xx". Searched wrapped-first so a title carrying a bare number --
# "17.01.2002", "Helen 12 Trees" -- cannot outrank the real year in
# brackets beside it.
_YEAR_CORE: Final[str] = r"\d{2}[\dx]{2}"
_YEAR_WRAPPED_RE: Final[re.Pattern[str]] = re.compile(rf"\((?:{_YEAR_CORE})\)|\[(?:{_YEAR_CORE})\]")
_YEAR_BARE_RE: Final[re.Pattern[str]] = re.compile(rf"(?<!\d){_YEAR_CORE}(?!\d)")


# ==================================================================================== #
#                                     ARTIST NAMES                                     #
# ==================================================================================== #
def strip_artist_label(folder_name: str) -> str | None:
    """Take the count label off an artist folder's name.

    Anchored to the end, so everything left of it survives verbatim --
    including a prefix the name genuinely carries. Splitting on " - "
    would cut "(Ivory Coast) - Christy B - [90 • 60F • 0L • 30M]" at its
    first separator; matching the end removes only the label.

    Args:
        folder_name: The artist folder's name.

    Returns:
        The name without its label, or `None` when it carries none --
        meaning the folder has not been through the placement step and
        there is no agreed value to derive.
    """
    stripped: str = ARTIST_LABEL_RE.sub("", folder_name).strip()
    if stripped == folder_name.strip() or not stripped:
        return None
    return stripped


# ==================================================================================== #
#                                        YEARS                                         #
# ==================================================================================== #
def extract_year(name: str) -> str | None:
    """Find the year token in an album folder name, leaving the name alone.

    A wrapped year wins over a bare one wherever it sits, so an album
    titled "17.01.2002" or "In India [1973]" resolves to the year in its
    own brackets rather than the number in its title. Among equals the
    leftmost wins, which is where the convention puts it.

    Args:
        name: The album folder's name.

    Returns:
        The four-character token -- "1994", "199x" -- or `None` when the
        name carries no year at all.
    """
    match: re.Match[str] | None = _YEAR_WRAPPED_RE.search(name)
    if match is None:
        match = _YEAR_BARE_RE.search(name)
    if match is None:
        return None
    return match.group(0).strip("()[]")


def is_approximate_year(year: str) -> bool:
    """Report whether a year token stands for one nobody knows exactly.

    Args:
        year: A token as returned by `extract_year`.

    Returns:
        `True` if it carries an "x" placeholder.
    """
    return "x" in year


# ==================================================================================== #
#                                     TITLE CASING                                     #
# ==================================================================================== #
def title_case(text: str) -> str:
    """Apply classic English title case.

    A thin pass to `titlecase`, which implements John Gruber's
    algorithm. It already handles the cases that need handling: minor
    words stay lowercase unless they lead, apostrophes survive where
    `str.title()` gives "It'S", an acronym among ordinary words is kept,
    a wholly-capitalized title is read as shouting and normalized, and a
    script without letter case is left alone.

    The convention is English, so a German or French title comes out
    capitalized rather than correct. No library can settle that without
    knowing the language.

    Args:
        text: The text to case.

    Returns:
        The text in title case.
    """
    return titlecase(text)
