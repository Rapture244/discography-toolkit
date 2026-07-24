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

# The "pin to top" mark: a bare "©" typed anywhere in a name to float a
# favourite above the default sort. Nothing to do with copyright, and
# carries no year or quality meaning -- it is relocated to the very front
# on rebuild. Found anywhere, since it is typed wherever the eye lands,
# not anchored like the index.
PIN_MARK: Final[str] = "©"
_PIN_MARK_RE: Final[re.Pattern[str]] = re.compile(re.escape(PIN_MARK))

# A leading numbering index to preserve across a rename: "01. ", "(01) ",
# "[27] ", "5. ", trailing separators included. Broader than
# ALBUM_INDEX_RE, which only answers whether a folder looks numbered at
# all; this captures the exact prefix so a rebuild can keep it verbatim.
# "^" only -- an index is meaningful solely as a prefix -- and
# `\d{1,3}(?!\d)` so a four-digit title like "1999" is not read as one.
_INDEX_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:\(\d{1,3}\)|\[\d{1,3}\]|\d{1,3}(?!\d))[._\s-]*"
)

# A year, with "x" standing in for a digit nobody knows: "1994", "199x",
# "19xx". Searched wrapped-first so a title carrying a bare number --
# "17.01.2002", "Helen 12 Trees" -- cannot outrank the real year in
# brackets beside it.
_YEAR_CORE: Final[str] = r"\d{2}[\dx]{2}"
_YEAR_WRAPPED_RE: Final[re.Pattern[str]] = re.compile(rf"\((?:{_YEAR_CORE})\)|\[(?:{_YEAR_CORE})\]")
_YEAR_BARE_RE: Final[re.Pattern[str]] = re.compile(rf"(?<!\d){_YEAR_CORE}(?!\d)")

# The "missing album" marker: an album known to exist but not held keeps
# an empty placeholder folder, marked between the year and the title. A
# plain "M" is the settled state -- declared missing, and the folder is
# indeed empty. "⚠" is a conflict: the name says missing while the folder
# holds audio. Neither resolution is the tool's to make -- the conflict
# clears by deleting stray files (back to "M") or dropping the marker by
# hand -- so a step states the problem in the name and stops there.
MISSING_MARKER: Final[str] = "M"
MISSING_CONFLICT_MARKER: Final[str] = "⚠"

# Reads either marker at the front of what is left once the year is gone.
# The older "(1967) M - Title" and the current "(1967) - M - Title" both
# arrive as "M - Title", so one pattern covers both. Anchored, and the
# hyphen is required: a title merely starting with M -- "Mirror", "Miles
# Smiles" -- cannot match, and an album titled "M" carries no hyphen, so
# it stays a title.
_MISSING_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"^(?:⚠|M)\s*-\s*")

# A single "(...)" or "[...]" wrapper, captured whole so its inner text
# can be read. Some albums mixed the quality word into a bracket with
# another tag worth keeping -- "[FLAC, 40th Anniversary]", "(FLAC,
# Live)", "[FLAC m4a]" -- so the word is cut from the bracket while the
# rest of it survives.
_ANY_WRAPPED_RE: Final[re.Pattern[str]] = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")

# A "FLAC" or "OPUS" word, case-insensitive, bounded so it cannot match
# inside an unrelated word. Only applied to text already inside a
# bracket, where a quality word is unambiguous.
_QUALITY_BRACKETED_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:FLAC|OPUS)\b", re.IGNORECASE)

# An unbracketed quality word left over from before the convention, e.g.
# "Some Album FLAC". Loose matching here is dangerous: "Opus" is an
# ordinary title word ("Magnum Opus", "Opus One"). Two guards make the
# mistake impossible -- anchored to the end, the only place a stale
# marker sits, and case-sensitive, since the convention writes the codec
# in caps while a title writes the word normally. The trade is
# deliberate: a lowercase "flac" is left for the eye to catch, where the
# opposite error would eat a word of the title.
_QUALITY_TRAILING_RE: Final[re.Pattern[str]] = re.compile(r"\s*\b(?:FLAC|OPUS)\s*$")

# Leftover separators (whitespace, hyphen, underscore, dot) at either end
# of a name once a token has been cut out.
_EDGE_SEPARATORS_RE: Final[re.Pattern[str]] = re.compile(r"^[\s._-]+|[\s._-]+$")

# Two or more spaces, left behind when a token is removed from the middle
# of a name rather than an edge.
_MULTI_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s{2,}")


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
#                                     PIN MARK & INDEX                                 #
# ==================================================================================== #
def split_pin_mark(name: str) -> tuple[str, str]:
    """Lift a "pin to top" mark off a name, wherever it was typed.

    The mark floats a favourite above the default sort. It is not
    anchored -- it lands wherever the eye was at the time -- so it is
    found anywhere, removed, and the gap it leaves tidied. Re-prefixing
    it at the very front is the caller's job, which is what gives it one
    canonical place. Only the first is handled; the convention assumes at
    most one per name.

    Args:
        name: The raw album folder name.

    Returns:
        A `(mark, remainder)` pair. `mark` is `PIN_MARK` when found, else
        an empty string; `remainder` has that one mark removed and its
        surroundings tidied.
    """
    match: re.Match[str] | None = _PIN_MARK_RE.search(name)
    if match is None:
        return "", name
    return match.group(0), clean_name(name[: match.start()] + name[match.end() :])


def split_index(name: str) -> tuple[str, str]:
    """Set aside a leading numbering index, keeping it verbatim.

    Makes the caller agnostic to whether numbering has run: an
    already-numbered album keeps its index untouched, a never-numbered
    one simply has nothing to set aside. The separator that follows the
    index is part of what is captured, so a rebuild can splice the rest
    of the name back on without guessing at spacing.

    Args:
        name: The raw album folder name, possibly index-prefixed.

    Returns:
        An `(index, remainder)` pair. `index` is the exact leading text
        matched, its trailing separator included, or an empty string when
        there is none; `remainder` is the rest, unchanged.
    """
    match: re.Match[str] | None = _INDEX_PREFIX_RE.match(name)
    if match is None:
        return "", name
    return match.group(0), name[match.end() :]


# ==================================================================================== #
#                                        YEARS                                         #
# ==================================================================================== #
def split_year(name: str) -> tuple[str | None, str]:
    """Find a year token and hand back the name without it.

    A wrapped year -- "(1994)", "[1994]" -- wins over a bare one wherever
    it sits, so "17.01.2002" or "In India [1973]" resolves to the year in
    its own brackets. Among equals the leftmost wins, which is where the
    convention puts the release over a reissue. The remainder is tidied,
    ready for the next thing to be read off its front.

    Args:
        name: The album folder's name.

    Returns:
        A `(year, remainder)` pair. `year` is the four-character token --
        "1994", "199x" -- or `None` when the name carries none, in which
        case `remainder` is the name unchanged.
    """
    match: re.Match[str] | None = _YEAR_WRAPPED_RE.search(name)
    if match is None:
        match = _YEAR_BARE_RE.search(name)
    if match is None:
        return None, name
    token: str = match.group(0).strip("()[]")
    return token, clean_name(name[: match.start()] + name[match.end() :])


def extract_year(name: str) -> str | None:
    """Find the year token in an album folder name, leaving the name alone.

    The non-destructive read, for a caller that wants only the token --
    the year tag is derived from it, and the folder is not being
    rebuilt. Naming, which strips the year to keep parsing, wants
    `split_year` instead.

    Args:
        name: The album folder's name.

    Returns:
        The four-character token -- "1994", "199x" -- or `None` when the
        name carries no year at all.
    """
    return split_year(name)[0]


def is_approximate_year(year: str) -> bool:
    """Report whether a year token stands for one nobody knows exactly.

    Args:
        year: A token as returned by `extract_year`.

    Returns:
        `True` if it carries an "x" placeholder.
    """
    return "x" in year


# ==================================================================================== #
#                                   MISSING MARKER                                     #
# ==================================================================================== #
def split_missing_marker(name: str) -> tuple[bool, str]:
    """Take a leading "missing" marker off a name, reporting it as a flag.

    Accepts either spelling of the marker -- the settled "M - Title" and
    the conflict "⚠ - Title" -- so a step can re-emit it in one canonical
    place. The name is expected to have had its year removed already, the
    point at which the marker lands at the front.

    A title that merely begins with M does not match: the trailing hyphen
    is required, so "Mirror" and "Miles Smiles" stay titles. The cost is
    that a title genuinely written "M - Something", or the hyphenated
    genre "M-Base", reads as a marker; that is rare enough to fix by hand
    and shows up plainly in the rename preview.

    Args:
        name: A name with the year already removed, e.g. "M - Folk Soul"
            or "Mirror".

    Returns:
        A `(is_missing, remainder)` pair. `remainder` has the marker and
        its separator removed, or is the name unchanged when there is no
        marker.
    """
    match: re.Match[str] | None = _MISSING_MARKER_RE.match(name)
    if match is None:
        return False, name
    return True, name[match.end() :]


# ==================================================================================== #
#                                     QUALITY TAG                                      #
# ==================================================================================== #
def strip_quality_tag(name: str) -> str:
    """Remove a stale "FLAC"/"OPUS" quality word from a name.

    Only cleans up; it never decides a tier, which is the job of whatever
    reads the album's actual files. A folder easily carries a word from
    before its contents last changed -- a FLAC master transcoded to Opus
    keeps the old "[FLAC]" until this strips it -- so either word goes
    regardless of which is present.

    Where the word shares a bracket with another tag worth keeping --
    "[FLAC, 40th Anniversary]", "(FLAC, Live)", "[FLAC m4a]" -- only the
    word itself is cut and the rest of the bracket is rebuilt in the same
    style and place. A bracket holding only the word is removed entirely.
    Failing any bracketed match, a bare trailing word is taken instead.

    Args:
        name: A name, past year extraction.

    Returns:
        The name with a stale quality word removed and its surroundings
        tidied, or unchanged when there is no such word.
    """
    for wrapped in _ANY_WRAPPED_RE.finditer(name):
        parens, brackets = wrapped.group(1), wrapped.group(2)
        is_paren: bool = parens is not None
        content: str = parens if is_paren else brackets
        opening, closing = ("(", ")") if is_paren else ("[", "]")

        quality: re.Match[str] | None = _QUALITY_BRACKETED_RE.search(content)
        if quality is None:
            continue

        kept: str = content[: quality.start()] + content[quality.end() :]
        kept = _MULTI_SPACE_RE.sub(" ", kept.strip(" ,")).strip()
        replacement: str = f"{opening}{kept}{closing}" if kept else ""
        return clean_name(name[: wrapped.start()] + replacement + name[wrapped.end() :])

    trailing: re.Match[str] | None = _QUALITY_TRAILING_RE.search(name)
    if trailing is None:
        return name
    return clean_name(name[: trailing.start()] + name[trailing.end() :])


def clean_name(name: str) -> str:
    """Tidy the spacing and edge punctuation left when a token is cut out.

    Removing a year or a quality word from the middle of a name leaves a
    double space; removing one from an end leaves a dangling separator.
    This closes both.

    Args:
        name: A name with a token already removed.

    Returns:
        Internal whitespace collapsed to single spaces, and separator
        characters -- spaces, hyphens, underscores, dots -- trimmed from
        both ends.
    """
    collapsed: str = _MULTI_SPACE_RE.sub(" ", name)
    return _EDGE_SEPARATORS_RE.sub("", collapsed).strip()


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
