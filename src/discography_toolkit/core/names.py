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

# The separator inside the label. U+2022 BULLET reads clearly at a file
# browser's font size and is about as widely present in fonts as anything
# outside Latin-1 -- chosen over the smaller U+00B7 and the rarer maths
# dots.
_LABEL_DOT: Final[str] = "\u2022"

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

# Strips the "M"/"⚠" marker for ordering, keeping the "(year) - " ahead of
# it. Unlike `_MISSING_MARKER_RE`, which reads the marker once the year is
# gone, this runs on the whole "(year) - M - Title" so a sort key can drop
# the marker without losing the year it orders by. The leading group is
# any parenthesised head, in practice the year.
_SORT_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"^(\([^)]*\)\s*-\s*)(?:⚠|M)\s*-\s*")

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

# The bare title of a singles collection -- the yearless album that
# gathers loose tracks belonging to no release, pinned ahead of the dated
# albums at "00". Matched case-insensitively, so "singles" is recognised
# and rewritten to this.
SINGLES_TITLE: Final[str] = "Singles"

# One trailing "(...)" or "[...]" tag, stripped when reading a title so a
# collection kept in several formats -- "Singles [FLAC]", "Singles
# [OPUS]", "Singles [OGG]" -- is recognised whatever it is stored as. Only
# the last such group goes, and only at the very end.
_TRAILING_TAG_RE: Final[re.Pattern[str]] = re.compile(r"\s*[\[(][^\[\]()]*[\])]\s*$")

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

# The extended-play marker, in any of the shapes it is typed in: bare
# "EP", "(EP)" or "[EP]", anywhere in the name. Word-bounded, so it can
# never eat into a title -- "Epitaph", "Epic", "EPMD" all carry an
# adjoining letter and so cannot match -- and case-sensitive, so only
# the all-capital marker counts. The convention writes it in capitals,
# standing apart with spaces around it, and a lower-case "ep" is as
# likely to be a word as a marker.
EP_MARKER: Final[str] = "EP"
_EP_MARKER_RE: Final[re.Pattern[str]] = re.compile(rf"\b{EP_MARKER}\b")

# A lower- or mixed-case "ep" standing as its own word, which is not
# taken as a marker but is worth pointing out: it is usually one typed
# in the wrong case, and only a person can say which. The lookahead
# excludes the all-capital form, which `_EP_MARKER_RE` has already
# claimed.
_EP_LOWERCASE_RE: Final[re.Pattern[str]] = re.compile(rf"\b(?!{EP_MARKER}\b)[Ee][Pp]\b")

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

# The canonical album folder name, past its index: what every album on a
# settled shelf reads as. Written here as one pattern rather than left
# implicit in the f-string that builds it, so the shape is something the
# code knows and can hold an album to.
#
# The year and the separator are required; everything else is a fact
# about the album that may or may not hold. The title is non-greedy so
# the optional groups behind it win a trailing "(EP)" or "[FLAC]" rather
# than the title swallowing them.
ALBUM_BODY_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    ^
    \((?P<year>\d{2}[\dx]{2})\)[ ]-[ ]        # (1994) -
    (?:(?P<marker>⚠|M)[ ]-[ ])?               # M -
    (?P<title>.+?)                            # Album Name
    (?:[ ]\(EP\))?                            # (EP)
    (?:[ ]\[(?:FLAC|OPUS)\])?                 # [FLAC]
    $
    """,
    re.VERBOSE,
)

# The index a settled album carries, and the pin that may sit ahead of
# it. Two digits at least, matching what numbering pads to, and widening
# with a run past ninety-nine.
ALBUM_INDEXED_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<pin>©?)(?P<index>\d{2,})\.[ ](?P<body>\S.*)$"
)

# Words kept in capitals against the title-caser, which would otherwise
# read them as shouting and lower them. Formats, media and rip types,
# a few country codes, roles and labels -- ambiguous ones a real word
# could shadow ("US", "LA", "AM", "MIX") are deliberately left out.
KEEP_CAPS: Final[frozenset[str]] = frozenset(
    {
        "FLAC",
        "OPUS",
        "MP3",
        "AAC",
        "ALAC",
        "WAV",
        "AIFF",
        "DSF",
        "DSD",
        "OGG",
        "WMA",
        "APE",
        "TTA",
        "MQA",
        "PCM",
        "OST",
        "EP",
        "LP",
        "CD",
        "DVD",
        "SACD",
        "HDCD",
        "VHS",
        "VBR",
        "CBR",
        "DIVX",
        "XVID",
        "USA",
        "UK",
        "USSR",
        "UAE",
        "DJ",
        "MC",
        "EDM",
        "IDM",
        "DNB",
        "BBC",
        "NPR",
        "NTS",
        "KEXP",
    }
)

# A roman numeral of two letters or more, built only from I, V and X.
# The restriction is what keeps the common word "MIX" (an M) from being
# read as one, at the cost of numbers past thirty-eight, which albums do
# not reach. A lone "I" or "V" is left to the caser, being as often a
# word as a number.
_ROMAN_RE: Final[re.Pattern[str]] = re.compile(r"[IVX]{2,}")

# Splits a token into its punctuation edges and alphanumeric core, so
# "[SF034]" and "III." are read past their brackets and stops. The core
# is non-greedy, letting the trailing run take every closing mark.
_WORD_EDGES: Final[re.Pattern[str]] = re.compile(
    r"^(?P<lead>[^0-9A-Za-z]*)(?P<core>.*?)(?P<trail>[^0-9A-Za-z]*)$"
)


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


def format_artist_label(total: int, flac: int, lossy: int, missing: int) -> str:
    """Build the count label appended to an artist folder's name.

    The three counts partition the total -- every album is one of
    lossless, lossy, or missing -- so `flac + lossy + missing` always
    equals `total`, and the label checks itself against a miscount.

    Args:
        total: How many albums the artist holds, both sides together.
        flac: How many are lossless.
        lossy: How many are held in a lossy format, opus among them.
        missing: How many are empty placeholders.

    Returns:
        A label of the form "[90 • 60F • 0L • 30M]".
    """
    dot: str = f" {_LABEL_DOT} "
    return f"[{total}{dot}{flac}F{dot}{lossy}L{dot}{missing}M]"


def with_artist_label(name: str, label: str) -> str:
    """Attach a fresh label to an artist folder name, replacing any old one.

    The whole existing bracket is replaced rather than edited, so an
    older "[M31 on 90]" and a label written with a different separator
    both converge on the current form, and a name with none simply gains
    one. A bracket that is part of the artist's real name -- "[Live]" --
    is left alone, since the label pattern requires a leading digit.

    Args:
        name: The artist folder's current name.
        label: The label to attach, as built by `format_artist_label`.

    Returns:
        The name carrying exactly one label, at the end.
    """
    stripped: str = ARTIST_LABEL_RE.sub("", name).rstrip()
    return f"{stripped} - {label}"


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


def sort_key(name: str) -> str:
    """Build the key an album orders by, ignoring what should not move it.

    Numbering runs down an artist's albums in this order and assigns
    "01.", "02." in turn, so the key has to leave out anything that is
    not the album's identity: the "©" pin, any existing index, and the
    "M"/"⚠" availability marker. Dropping the marker is what keeps the
    sequence stable -- an album that gains or loses audio holds its place
    instead of jumping to wherever the glyph happens to sort. The year is
    kept, so albums order by year and then title.

    Args:
        name: The raw album folder name.

    Returns:
        The casefolded remainder, ready to sort on.
    """
    _, rest = split_pin_mark(name)
    _, rest = split_index(rest)
    return _SORT_MARKER_RE.sub(r"\1", rest, count=1).casefold()


def is_singles(name: str) -> bool:
    """Report whether an album is an artist's singles collection.

    A singles collection is the yearless exception: titled "Singles", it
    gathers loose tracks that belong to no release -- no album, EP or
    mixtape -- and is pinned ahead of the dated albums at "00". A name
    carrying a year, however it is titled, is an ordinary release and not
    this: a year is what a real album has and a singles pile does not.

    The pin, index, year, availability marker and one trailing format tag
    are all set aside before the title is read, so "©05. Singles [FLAC]"
    and a bare "singles" alike are recognised, and a collection kept in
    several formats is caught whatever bracket it wears.

    Args:
        name: The album folder's name.

    Returns:
        `True` when the album reads as a singles collection.
    """
    _, rest = split_pin_mark(name)
    _, rest = split_index(rest)
    year, rest = split_year(rest)
    if year is not None:
        return False
    _, rest = split_missing_marker(rest)
    bare: str = _TRAILING_TAG_RE.sub("", rest).strip()
    return bare.casefold() == SINGLES_TITLE.casefold()


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
#                                      EP MARKER                                       #
# ==================================================================================== #
def split_ep_marker(name: str) -> tuple[bool, str]:
    """Take an extended-play marker off a name, reporting it as a flag.

    The marker is typed in every shape and every place -- a bare "EP" at
    the front, "(EP)" after the title, "[EP]" among other tags -- so it
    is found wherever it sits and reported as a fact rather than a
    string. Emitting it in one canonical place is the caller's job, which
    is what makes the position settled.

    Where the marker shares a bracket with something worth keeping --
    "[EP, Remastered]" -- only the marker is cut and the rest of the
    bracket is rebuilt in the same style and place, as the quality word
    is. A bracket holding only the marker goes entirely.

    Only the all-capital form counts. A lower-case "ep" is as likely to
    be an ordinary word as a marker, and eating a word of the title is
    the worse error of the two; `has_lowercase_ep` points those out
    instead, for a person to settle.

    Args:
        name: A name, past year extraction.

    Returns:
        An `(is_ep, remainder)` pair. `remainder` has the marker removed
        and its surroundings tidied, or is the name unchanged when there
        is none.
    """
    for wrapped in _ANY_WRAPPED_RE.finditer(name):
        parens, brackets = wrapped.group(1), wrapped.group(2)
        is_paren: bool = parens is not None
        content: str = parens if is_paren else brackets
        opening, closing = ("(", ")") if is_paren else ("[", "]")

        marker: re.Match[str] | None = _EP_MARKER_RE.search(content)
        if marker is None:
            continue

        kept: str = content[: marker.start()] + content[marker.end() :]
        kept = _MULTI_SPACE_RE.sub(" ", kept.strip(" ,")).strip()
        replacement: str = f"{opening}{kept}{closing}" if kept else ""
        return True, clean_name(name[: wrapped.start()] + replacement + name[wrapped.end() :])

    bare: re.Match[str] | None = _EP_MARKER_RE.search(name)
    if bare is None:
        return False, name
    # A space bridges the cut, so a marker taken from the middle of a
    # name does not weld the words either side of it together.
    return True, clean_name(f"{name[: bare.start()]} {name[bare.end() :]}")


def has_lowercase_ep(name: str) -> bool:
    """Report whether a name carries an "ep" written in the wrong case.

    Not a marker, since only the all-capital form is taken as one, but
    worth a person's eye: it is usually a marker typed carelessly, and
    only they can say whether it is that or an ordinary word.

    Args:
        name: The album folder's name.

    Returns:
        `True` when a lower- or mixed-case "ep" stands as its own word.
    """
    return _EP_LOWERCASE_RE.search(name) is not None


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


def drop_unpaired_brackets(text: str) -> str:
    """Remove brackets with no partner, keeping every pair intact.

    A name arrives past the peel carrying the odd orphan: a "]" whose "["
    a step ahead took away with the token it wrapped, a "(" whose close
    never made it into the folder name. Balance is the test rather than
    absence, because a bracket that pairs is part of the title and the
    title's to keep -- "[40th Anniversary]" survives, "] the Mad Writer"
    loses its orphan and reads as the album it always was.

    Nothing here needs a person's judgement, which is why it repairs
    rather than reports: an orphan says nothing on its own, so there is
    no question of what was meant by it.

    Openers are matched to closers as they are met, so a nested pair
    counts as balanced and a crossed one -- "(a]" -- counts as two
    orphans, both removed.

    Args:
        text: A name, past the peel and before casing.

    Returns:
        The text with every unpaired bracket gone and its spacing tidied.
    """
    closers: dict[str, str] = {")": "(", "]": "["}
    open_at: list[int] = []
    orphans: set[int] = set()

    for position, char in enumerate(text):
        if char in {"(", "["}:
            open_at.append(position)
        elif char in closers:
            if open_at and text[open_at[-1]] == closers[char]:
                _ = open_at.pop()
            else:
                orphans.add(position)

    orphans.update(open_at)
    kept: str = "".join(char for position, char in enumerate(text) if position not in orphans)
    return clean_name(kept)


# ==================================================================================== #
#                                     ALBUM TITLES                                     #
# ==================================================================================== #
def album_title(name: str) -> str:
    """Read the bare title out of an album folder's name.

    The whole convention comes off -- the pin mark, the numbering index,
    the year, the availability marker, the EP marker, the quality word --
    leaving what the album is actually called. Everything removed
    describes where the album sits on this shelf or how this copy of it
    was ripped, and none of that means anything to anyone else's library.

    A bracket left without its partner comes off too. Dropping it here as
    well as in `naming` is what keeps one album's identity the same
    before and after the shelf is laid out, so a folder awaiting repair
    is not mistaken for a different album than the one it will become.

    The same sequence `naming` walks when it rebuilds a folder name,
    named here for the callers that want only its end. Casing is not
    applied: a folder the layout pass has settled already carries a cased
    title, and re-casing one it has not would invent a name the folder
    does not have.

    Args:
        name: The album folder's name.

    Returns:
        The title alone, its spacing tidied.
    """
    _, rest = split_pin_mark(name)
    _, rest = split_index(rest)
    _, rest = split_year(rest)
    _, rest = split_missing_marker(rest)
    _, rest = split_ep_marker(rest)
    return drop_unpaired_brackets(strip_quality_tag(rest))


# ==================================================================================== #
#                                     CONFORMANCE                                      #
# ==================================================================================== #
def conforms(name: str) -> bool:
    """Report whether a folder name is a settled album's, in full.

    The whole shape, index included: what a shelf reads as once every
    step has run. `conforms_unnumbered` is the one to ask before
    numbering has had its say.

    Args:
        name: The album folder's name.

    Returns:
        `True` when the name is canonical.
    """
    match: re.Match[str] | None = ALBUM_INDEXED_RE.match(name)
    return match is not None and conforms_body(match.group("body"))


def conforms_unnumbered(name: str) -> bool:
    """Report whether a name is canonical from the year onward.

    The index is left out of the judgement because numbering owns it and
    rewrites it wholesale: whatever shape it is in now -- "(01) ", "5. ",
    none at all -- it comes out as "01. " regardless. What matters before
    then is everything after it, which no later step touches.

    Args:
        name: An album folder's name, or one naming proposes.

    Returns:
        `True` when everything past the index is canonical.
    """
    _, rest = split_pin_mark(name)
    _, rest = split_index(rest)
    return conforms_body(rest)


def conforms_body(body: str) -> bool:
    """Report whether a name past its index is canonical.

    Two shapes are canonical. A dated album carries its year, an optional
    availability marker, a title, and then whatever it happens to be --
    an EP, a lossless or Opus copy. A singles collection is the bare
    word: no year to date it, no tag, since one artist keeps one such
    pile whatever it is made of.

    This is the net rather than the mechanism. The rebuild is what brings
    a name into the pattern, and it repairs the drift it can -- an orphan
    bracket, a stale tag, a marker in the wrong place. What this catches
    is whatever the rebuild could not settle, which is the sort of thing
    a person has to look at.

    Matching the skeleton is not enough on its own -- the title is
    `.+?`, which swallows an orphan bracket as happily as a letter -- so
    what lands in it is judged too.

    Args:
        body: A name with its pin and index already removed.

    Returns:
        `True` when the body is canonical.
    """
    if body == SINGLES_TITLE:
        return True

    match: re.Match[str] | None = ALBUM_BODY_RE.match(body)
    return match is not None and _title_is_wellformed(match.group("title"))


def _title_is_wellformed(title: str) -> bool:
    """Report whether a title is something a rebuild could have produced.

    Three things a settled title never has: nothing at all, a bracket
    without its partner, or spacing a rebuild would have closed. What it
    may have is anything else -- a full stop, an ampersand, a bracket
    that pairs -- since those are the title's own and not the
    convention's to judge.

    Args:
        title: The title read out of a canonical body.

    Returns:
        `True` when the title is wellformed.
    """
    if not title:
        return False
    if title != _MULTI_SPACE_RE.sub(" ", title).strip():
        return False
    return brackets_balanced(title)


def brackets_balanced(text: str) -> bool:
    """Report whether every bracket in a name has its partner.

    The question `drop_unpaired_brackets` answers by acting; asked here
    without acting, for a caller checking that it did.

    Args:
        text: Any name or part of one.

    Returns:
        `True` when every opener meets its closer, in order.
    """
    closers: dict[str, str] = {")": "(", "]": "["}
    stack: list[str] = []

    for char in text:
        if char in {"(", "["}:
            stack.append(char)
        elif char in closers and (not stack or stack.pop() != closers[char]):
            return False

    return not stack


# ==================================================================================== #
#                                     TITLE CASING                                     #
# ==================================================================================== #
def title_case(text: str) -> str:
    """Apply classic English title case, keeping known acronyms in caps.

    A pass to `titlecase`, John Gruber's algorithm, which already reads a
    wholly-capitalised title as shouting and normalises it -- "SO WHAT"
    to "So What". What it does not know is which words are meant to stay
    capitalised, so a callback holds those back: the acronyms in
    `KEEP_CAPS`, roman numerals, and any code that glues letters to
    digits, like a catalogue number "SF034" or a rip tag "24bVFLAC".

    The convention is English, so a German or French title comes out
    capitalised rather than correct. No library can settle that without
    knowing the language.

    Args:
        text: The text to case.

    Returns:
        The text in title case, its acronyms and codes left in caps.
    """
    # titlecase only reads a title as shouting, and normalises it, when
    # every word is capital; one lower-case letter anywhere -- the "b" of
    # "[24bVFLAC]", the "s" of "1970's" -- and it leaves the rest of the
    # capitals be. Rips carry such tags routinely, so the shouting is
    # tamed here first: each all-caps word not worth keeping is lowered,
    # and titlecase then capitalises it as it would any ordinary word.
    tamed: str = " ".join(_tame(token) for token in text.split(" "))
    return titlecase(tamed, callback=_keep_caps)


def _tame(token: str) -> str:
    """Lower an all-caps word so the title-caser will recapitalise it.

    A word already worth keeping in caps -- an acronym, a roman numeral,
    a code -- is left alone; anything else that is wholly capital is
    shouting, and comes down to lower case for the caser to lift back up.

    Args:
        token: One whitespace-delimited token, punctuation included.

    Returns:
        The token, its core lowered when it was needless shouting.
    """
    edges: re.Match[str] | None = _WORD_EDGES.match(token)
    if edges is None:
        return token
    core: str = edges.group("core")
    if core.isalpha() and core.isupper() and _core_case(core) is None:
        return f"{edges.group('lead')}{core.lower()}{edges.group('trail')}"
    return token


def _keep_caps(word: str, **_kwargs: object) -> str | None:
    """Decide a word's case for `titlecase`, or defer to it.

    The word arrives with its surrounding punctuation -- "[SF034]",
    "III." -- so the alphanumeric core is read out, cased, and the
    punctuation put back. Returning `None` leaves the word to the
    ordinary algorithm.

    Args:
        word: One whitespace-delimited token, punctuation included.
        **_kwargs: Extra arguments `titlecase` passes, unused.

    Returns:
        The token cased, or `None` to let `titlecase` handle it.
    """
    edges: re.Match[str] | None = _WORD_EDGES.match(word)
    if edges is None:
        return None
    core: str = edges.group("core")
    cased: str | None = _core_case(core)
    if cased is None:
        return None
    return f"{edges.group('lead')}{cased}{edges.group('trail')}"


def _core_case(core: str) -> str | None:
    """Case one word's core if it is an acronym, a roman numeral or a code.

    A known acronym and a roman numeral are forced to caps -- so a
    lower-cased "ost" or "iii" is restored, not just preserved. A code
    gluing letters to digits is kept exactly as written, since its case
    is not ours to normalise: "24bVFLAC" is not "24BVFLAC".

    Args:
        core: A word stripped of its surrounding punctuation.

    Returns:
        The core cased, or `None` when the ordinary algorithm should
        decide.
    """
    if not core:
        return None
    upper: str = core.upper()
    if upper in KEEP_CAPS or _ROMAN_RE.fullmatch(upper) is not None:
        return upper
    if any(char.isalpha() for char in core) and any(char.isdigit() for char in core):
        return core
    return None


def title_case_filename(name: str) -> str:
    """Title-case a filename's stem, leaving its extension untouched.

    Only the stem is cased. The extension is written back exactly as
    found -- ".Flac" is the same file to a case-insensitive OS but a
    different string to every `suffix.lower()` check in the pipeline, and
    a file that stops matching stops being tagged.

    Runs of whitespace left in the stem are collapsed and its ends
    trimmed. A stem that cases to nothing at all -- one that was only
    punctuation -- leaves the whole name as it was, rather than producing
    a file called just its extension.

    Args:
        name: A filename with its extension, e.g. "01 - so what.flac".

    Returns:
        The filename with its stem cased and its extension unchanged.
    """
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""

    cased: str = _MULTI_SPACE_RE.sub(" ", title_case(stem)).strip()
    if not cased:
        return name
    return f"{cased}.{suffix}" if dot else cased
