# tests/core/test_names.py
"""Tests for name parsing and casing.

Each case records a judgement that took evidence to reach -- a name that
looked like it would work and did not, or one that had to survive
untouched.
"""

from __future__ import annotations

from discography_toolkit.core.names import (
    clean_name,
    extract_year,
    is_approximate_year,
    split_missing_marker,
    strip_artist_label,
    strip_quality_tag,
    title_case,
)

import pytest


# ==================================================================================== #
#                                     ARTIST NAMES                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("folder_name", "expected"),
    [
        ("Charlie Mariano - [90 • 60F • 0L • 30M]", "Charlie Mariano"),
        # A prefix the name genuinely carries: splitting on " - " would
        # cut this down to "(Ivory Coast)".
        ("(Ivory Coast) - Christy B - [90 • 60F • 0L • 30M]", "(Ivory Coast) - Christy B"),
        ("Sun Ra & His Arkestra - [12 • 12F • 0L • 0M]", "Sun Ra & His Arkestra"),
        # Older label forms are still recognized.
        ("Charlie Mariano - [M31 on 90]", "Charlie Mariano"),
        ("Miles Davis - [65 on 65]", "Miles Davis"),
        # A bracket that is part of the name, not a label.
        ("Portishead [Live] - [3 • 3F • 0L • 0M]", "Portishead [Live]"),
        # The separator is written by the placement step, but a name
        # typed by hand may not have it.
        ("Miles Davis [65 on 65]", "Miles Davis"),
        # Surrounding whitespace is not part of the name.
        ("  Miles Davis - [65 on 65]  ", "Miles Davis"),
    ],
)
def test_strip_artist_label(folder_name: str, expected: str) -> None:
    """Only the trailing label goes; everything left of it survives.

    Args:
        folder_name: The artist folder's name.
        expected: What should remain.
    """
    assert strip_artist_label(folder_name) == expected


@pytest.mark.parametrize(
    "folder_name",
    [
        "Charlie Mariano",  # never been through the placement step
        "Portishead [Live]",  # a bracket, but not a count
        "[90 • 60F • 0L • 30M]",  # nothing but a label
        "",
    ],
)
def test_strip_artist_label_returns_none_without_one(folder_name: str) -> None:
    """No label means no agreed value, which is not the same as an empty one.

    Args:
        folder_name: The artist folder's name.
    """
    assert strip_artist_label(folder_name) is None


# ==================================================================================== #
#                                        YEARS                                         #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("01. (1959) - Kind of Blue [FLAC]", "1959"),
        ("©27. (1977) - October [FLAC]", "1977"),
        # A wrapped year outranks a bare number wherever it sits: the
        # title carries a date, and the real year is in its own brackets.
        ("64. (2002) - 17.01.2002 [FLAC]", "2002"),
        ("26. (1976) - Helen 12 Trees [FLAC]", "1976"),
        # Two wrapped years: the leftmost is the release, the other is
        # what the album is a reissue of.
        ("90. (2013) - In India [1973] [FLAC]", "2013"),
        ("09. (1978) - Loveland [Re. 2016, FLAC]", "1978"),
        # Square brackets count as wrapping too.
        ("Live at Birdland [1963]", "1963"),
        # A title that is itself a number, wherever it sits: Prince
        # released "1999" in 1982, so the wrapped year has to win even
        # though the bare one comes first.
        ("1999 (1982)", "1982"),
        ("Woodstock 1969 (1994)", "1994"),
        # Bare, when nothing is wrapped.
        ("Sketches of Spain 1960", "1960"),
    ],
)
def test_extract_year(name: str, expected: str) -> None:
    """The year is the one in brackets, or the only one there is.

    Args:
        name: The album folder's name.
        expected: The token that should be found.
    """
    assert extract_year(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("13. (199x) - Unknown Decade", "199x"),
        ("01. (19xx) - Untranslated Album Title", "19xx"),
    ],
)
def test_extract_year_accepts_an_approximation(name: str, expected: str) -> None:
    """An "x" stands for a digit nobody knows, and is still a year token.

    Args:
        name: The album folder's name.
        expected: The token that should be found.
    """
    assert extract_year(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Kind of Blue",
        "01. - No Year At All [FLAC]",
        # Five digits is not a year, and the guards stop it matching four
        # of them.
        "Catalogue 123456",
        "",
    ],
)
def test_extract_year_returns_none_without_one(name: str) -> None:
    """No year is not the same as an unknown one.

    Args:
        name: The album folder's name.
    """
    assert extract_year(name) is None


@pytest.mark.parametrize(
    ("year", "expected"),
    [("1959", False), ("199x", True), ("19xx", True), ("2013", False)],
)
def test_is_approximate_year(year: str, *, expected: bool) -> None:
    """A token carrying an "x" is an approximation.

    Args:
        year: A token as returned by `extract_year`.
        expected: Whether it should read as approximate.
    """
    assert is_approximate_year(year) is expected


# ==================================================================================== #
#                                   MISSING MARKER                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "remainder"),
    [
        ("M - Folk Soul", "Folk Soul"),
        # The conflict spelling is accepted the same way.
        ("\u26a0 - Some Album", "Some Album"),
        # Both marker forms arrive here as "X - Title", so the older
        # unseparated spelling is covered too.
        ("M  -  Extra Spaces", "Extra Spaces"),
    ],
)
def test_split_missing_marker_takes_the_marker(name: str, remainder: str) -> None:
    """Either marker is lifted off, leaving the title behind.

    Args:
        name: A name with the year already removed.
        remainder: What should be left once the marker is gone.
    """
    is_missing, rest = split_missing_marker(name)

    assert is_missing is True
    assert rest == remainder


@pytest.mark.parametrize(
    "name",
    [
        "Mirror",  # a title that merely starts with M
        "Miles Smiles",
        "M",  # an album genuinely titled "M" carries no hyphen
        "Kind of Blue",
        "",
    ],
)
def test_split_missing_marker_leaves_a_title_alone(name: str) -> None:
    """The trailing hyphen is what tells a marker from a title starting M.

    Args:
        name: A name carrying no marker.
    """
    is_missing, rest = split_missing_marker(name)

    assert is_missing is False
    assert rest == name


def test_split_missing_marker_swallows_a_hyphenated_title() -> None:
    """A known cost: "M-Base" reads as a marker, since the hyphen is there.

    Recorded rather than wished away. Requiring spaces around the hyphen
    would be the fix, but the older unseparated spelling depends on their
    absence, so the two cannot both be honored by one pattern. The case
    is rare and shows plainly in the rename preview.
    """
    is_missing, rest = split_missing_marker("M-Base")

    assert is_missing is True
    assert rest == "Base"


# ==================================================================================== #
#                                     QUALITY TAG                                      #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Kind of Blue [FLAC]", "Kind of Blue"),
        ("So What (OPUS)", "So What"),
        # Case-insensitive inside a bracket, where the word is unambiguous.
        ("Milestones [flac]", "Milestones"),
        # A second bracket that is part of the title survives.
        ("Bird [Live] [FLAC]", "Bird [Live]"),
        # A bare trailing word, when nothing is bracketed.
        ("Some Album FLAC", "Some Album"),
        ("Some Album OPUS", "Some Album"),
    ],
)
def test_strip_quality_tag_removes_the_word(name: str, expected: str) -> None:
    """A stale quality word goes, whichever of the two it is.

    Args:
        name: A name past year extraction.
        expected: The name once the word is gone.
    """
    assert strip_quality_tag(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Milestones [FLAC, 40th Anniversary]", "Milestones [40th Anniversary]"),
        ("Live (FLAC, Live)", "Live (Live)"),
        ("Nefertiti [FLAC m4a]", "Nefertiti [m4a]"),
    ],
)
def test_strip_quality_tag_keeps_a_shared_bracket(name: str, expected: str) -> None:
    """Only the word is cut; a tag sharing its bracket is kept, in style.

    Args:
        name: A name whose bracket holds the word and something else.
        expected: The name with just the word removed.
    """
    assert strip_quality_tag(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # "Opus" is an ordinary title word, and only an all-caps trailing
        # word is treated as a marker.
        "Magnum Opus",
        "Opus One",
        "Opus de Jazz",
        # Lowercase "flac" outside a bracket is left for the eye, since
        # eating a title word is the worse mistake.
        "Miles Ahead flac",
        # No quality word at all.
        "Sketches of Spain",
        "",
    ],
)
def test_strip_quality_tag_leaves_a_title_alone(name: str) -> None:
    """The guards refuse anything that could be a title word.

    Args:
        name: A name carrying no stale quality word.
    """
    assert strip_quality_tag(name) == name


# ==================================================================================== #
#                                      CLEANUP                                         #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # A token removed from the middle leaves a double space.
        ("Kind of  Blue", "Kind of Blue"),
        # One removed from an end leaves a dangling separator.
        ("- Kind of Blue", "Kind of Blue"),
        ("Kind of Blue -", "Kind of Blue"),
        ("_Kind of Blue.", "Kind of Blue"),
        ("  Kind of Blue  ", "Kind of Blue"),
        # A hyphen inside the name is not an edge and stays.
        ("Sun Ra - Space Is the Place", "Sun Ra - Space Is the Place"),
        ("", ""),
    ],
)
def test_clean_name(name: str, expected: str) -> None:
    """Spacing collapses and edge separators go; the middle is untouched.

    Args:
        name: A name with a token already cut out.
        expected: How it should read once tidied.
    """
    assert clean_name(name) == expected


# ==================================================================================== #
#                                     TITLE CASING                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("so what", "So What"),
        ("freddie freeloader", "Freddie Freeloader"),
        # Minor words stay down unless they lead.
        ("kind of blue", "Kind of Blue"),
        ("the man with the horn", "The Man With the Horn"),
        # str.title() would give "'Round" the wrong apostrophe handling.
        ("'round midnight", "'Round Midnight"),
        ("it's about that time", "It's About That Time"),
    ],
)
def test_title_case_normalizes_lowercase(text: str, expected: str) -> None:
    """A lowercase title comes out properly cased.

    Args:
        text: The title as found.
        expected: How it should read.
    """
    assert title_case(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # An acronym among ordinary words is deliberate and kept.
        ("theme from OST", "Theme From OST"),
        ("live at BBC", "Live at BBC"),
        # A lone capital goes through the library, which keeps roman
        # numerals: str.title() would give "Vol. Ii".
        ("it's standard time, vol. II", "It's Standard Time, Vol. II"),
    ],
)
def test_title_case_keeps_acronyms(text: str, expected: str) -> None:
    """Words written in full caps are treated as deliberate.

    Args:
        text: The title as found.
        expected: How it should read.
    """
    assert title_case(text) == expected


def test_title_case_normalizes_shouting() -> None:
    """A title in full capitals is shouted, not one long acronym.

    The acronym guard is suspended when nothing around a capitalized word
    is lowercase, since there is then nothing to make it stand out.
    """
    assert title_case("THE MAN WITH THE HORN") == "The Man With the Horn"


@pytest.mark.parametrize(
    "text",
    [
        "四人囃子",  # no letter case at all
        "17.01.2002",
        "E.S.P",
        "Kind of Blue",  # already correct
    ],
)
def test_title_case_leaves_these_alone(text: str) -> None:
    """Text with nothing to case comes back unchanged.

    Args:
        text: The title as found.
    """
    assert title_case(text) == text


def test_title_case_applies_english_rules_to_other_languages() -> None:
    """German capitalizes nouns only, and no library can know that.

    Recorded rather than wished away: the result is capitalized rather
    than correct, which the dry run is there to catch.
    """
    assert title_case("warum bist du traurig") == "Warum Bist Du Traurig"
