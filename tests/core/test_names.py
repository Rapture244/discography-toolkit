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
    format_artist_label,
    is_approximate_year,
    is_singles,
    sort_key,
    split_index,
    split_missing_marker,
    split_pin_mark,
    split_year,
    strip_artist_label,
    strip_quality_tag,
    title_case,
    title_case_filename,
    with_artist_label,
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


def test_format_artist_label_reads_as_a_breakdown() -> None:
    """The label states the total and its three parts, in that shape."""
    assert format_artist_label(90, 60, 0, 30) == "[90 \u2022 60F \u2022 0L \u2022 30M]"


def test_format_artist_label_counts_partition_the_total() -> None:
    """The three parts always sum to the total, which is the label's own check."""
    label: str = format_artist_label(12, 7, 3, 2)

    assert label == "[12 \u2022 7F \u2022 3L \u2022 2M]"


@pytest.mark.parametrize(
    "name",
    [
        # No label yet: one is simply gained.
        "Charlie Mariano",
        # An older form is replaced whole, not accumulated.
        "Charlie Mariano - [M31 on 90]",
        # The current form is replaced too, so a rerun stays at one label.
        "Charlie Mariano - [90 \u2022 60F \u2022 0L \u2022 30M]",
    ],
)
def test_with_artist_label_settles_on_one_label(name: str) -> None:
    """Whatever a name carried, it comes out with exactly the new label.

    Args:
        name: The artist folder's current name.
    """
    label: str = format_artist_label(5, 3, 1, 1)

    assert with_artist_label(name, label) == f"Charlie Mariano - {label}"


def test_with_artist_label_keeps_a_real_bracket_in_the_name() -> None:
    """A "[Live]" that is part of the artist's name is not mistaken for a label.

    The label pattern requires a leading digit, so a word bracket survives.
    """
    label: str = format_artist_label(3, 3, 0, 0)

    assert with_artist_label("Portishead [Live]", label) == f"Portishead [Live] - {label}"


def test_with_artist_label_survives_a_prefix_in_the_name() -> None:
    """Everything left of the old label is kept, prefix and all.

    Splitting on " - " would cut the name at its first separator; the
    label is matched at the end instead.
    """
    label: str = format_artist_label(2, 1, 1, 0)
    name: str = "(Ivory Coast) - Christy B - [65 on 65]"

    assert with_artist_label(name, label) == f"(Ivory Coast) - Christy B - {label}"


# ==================================================================================== #
#                                     PIN MARK & INDEX                                 #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "remainder"),
    [
        ("\u00a9 Kind of Blue", "Kind of Blue"),
        # Not anchored: found wherever it was typed, front, middle or end.
        ("01. \u00a9 Kind of Blue", "01. Kind of Blue"),
        ("Kind of Blue \u00a9", "Kind of Blue"),
        ("01. Kind \u00a9 of Blue", "01. Kind of Blue"),
    ],
)
def test_split_pin_mark_lifts_the_mark(name: str, remainder: str) -> None:
    """The mark comes off wherever it sat, and the gap is tidied.

    Args:
        name: A name carrying the pin mark.
        remainder: What should be left, cleaned.
    """
    mark, rest = split_pin_mark(name)

    assert mark == "\u00a9"
    assert rest == remainder


@pytest.mark.parametrize("name", ["Kind of Blue", "01. So What", ""])
def test_split_pin_mark_leaves_an_unmarked_name(name: str) -> None:
    """No mark means the name comes back untouched.

    Args:
        name: A name with no pin mark.
    """
    mark, rest = split_pin_mark(name)

    assert mark == ""
    assert rest == name


@pytest.mark.parametrize(
    ("name", "index", "remainder"),
    [
        ("01. Kind of Blue", "01. ", "Kind of Blue"),
        ("(01) Kind of Blue", "(01) ", "Kind of Blue"),
        ("[27] October", "[27] ", "October"),
        ("5. So What", "5. ", "So What"),
        ("127. Album", "127. ", "Album"),
        # The trailing separator is captured, whatever it is.
        ("3 - Live", "3 - ", "Live"),
        ("01.Kind", "01.", "Kind"),
    ],
)
def test_split_index_sets_aside_the_prefix(name: str, index: str, remainder: str) -> None:
    """An existing index is captured verbatim, its separator included.

    Args:
        name: A name carrying a leading index.
        index: The exact prefix that should be set aside.
        remainder: The rest of the name.
    """
    prefix, rest = split_index(name)

    assert prefix == index
    assert rest == remainder


@pytest.mark.parametrize(
    "name",
    [
        "Kind of Blue",
        # A four-digit title is not a three-digit index: Prince's "1999"
        # keeps its name.
        "1999 (1982)",
        "",
    ],
)
def test_split_index_leaves_an_unnumbered_name(name: str) -> None:
    """Nothing to set aside means the name comes back whole.

    Args:
        name: A name with no leading index.
    """
    prefix, rest = split_index(name)

    assert prefix == ""
    assert rest == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("\u00a901. (1997) - M - Kind of Blue [FLAC]", "(1997) - kind of blue [flac]"),
        ("05. (1959) - So What [FLAC]", "(1959) - so what [flac]"),
        # The conflict glyph is stripped the same as the plain marker.
        ("12. (1980) - \u26a0 - Decoy", "(1980) - decoy"),
        # A name with none of the strippable parts casefolds as it is.
        ("No Year Album", "no year album"),
    ],
)
def test_sort_key_ignores_what_should_not_move_an_album(name: str, expected: str) -> None:
    """The key is year and title, with pin, index and marker taken out.

    Args:
        name: The raw album folder name.
        expected: The key it should produce.
    """
    assert sort_key(name) == expected


def test_sort_key_is_stable_when_availability_changes() -> None:
    """Gaining or losing the marker must not change where an album sorts.

    An album found missing and later held keeps its place in the
    sequence, so numbering does not shuffle everything after it.
    """
    missing: str = "07. (1971) - M - Live-Evil [FLAC]"
    held: str = "07. (1971) - Live-Evil [FLAC]"

    assert sort_key(missing) == sort_key(held)


def test_sort_key_orders_by_year_then_title() -> None:
    """An earlier year sorts first, and same-year albums fall back to title."""
    names: list[str] = [
        "01. (1970) - Bitches Brew",
        "02. (1959) - So What",
        "03. (1959) - Ascension",
    ]

    assert sorted(names, key=sort_key) == [
        "03. (1959) - Ascension",
        "02. (1959) - So What",
        "01. (1970) - Bitches Brew",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "Singles",
        "singles",  # case-insensitive
        "SINGLES",
        "00. Singles",
        "00. Singles [FLAC]",
        "©05. Singles [OPUS]",
        "Singles [OGG]",  # a non-standard format tag is still set aside
        "07. Singles",  # wrongly numbered, still a singles collection
    ],
)
def test_is_singles_recognises_a_singles_collection(name: str) -> None:
    """Titled "Singles" and yearless, whatever decorations it wears.

    Args:
        name: An album folder name that is a singles collection.
    """
    assert is_singles(name)


@pytest.mark.parametrize(
    "name",
    [
        "01. (1970) - Bitches Brew [FLAC]",  # a dated album
        "(2015) - Singles",  # a year makes it a real release, not the pile
        "00. Singles Collection",  # a different title
        "The Singles",
        "00. (2015) - Best Of [OGG]",
    ],
)
def test_is_singles_rejects_everything_else(name: str) -> None:
    """A year, or any title but "Singles", is not a singles collection.

    Args:
        name: An album folder name that is not a singles collection.
    """
    assert not is_singles(name)


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
    ("name", "year", "remainder"),
    [
        ("(1959) - Kind of Blue", "1959", "Kind of Blue"),
        # A wrapped year is removed even when a bare number sits before it.
        ("1999 (1982)", "1982", "1999"),
        ("In India [1973]", "1973", "In India"),
        # A bare year, when nothing is wrapped, and the remainder tidied.
        ("Sketches of Spain 1960", "1960", "Sketches of Spain"),
        ("(199x) - Unknown Decade", "199x", "Unknown Decade"),
    ],
)
def test_split_year_removes_the_year(name: str, year: str, remainder: str) -> None:
    """The token comes back with the name it was cut from, tidied.

    Args:
        name: The album folder's name.
        year: The token that should be found.
        remainder: The name once the year is gone.
    """
    found, rest = split_year(name)

    assert found == year
    assert rest == remainder


@pytest.mark.parametrize("name", ["Kind of Blue", "M - Folk Soul", ""])
def test_split_year_leaves_a_yearless_name(name: str) -> None:
    """No year means the name is handed back whole.

    Args:
        name: A name carrying no year.
    """
    found, rest = split_year(name)

    assert found is None
    assert rest == name


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


# ==================================================================================== #
#                                   FILENAME CASING                                    #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("kind of blue.flac", "Kind of Blue.flac"),
        ("01 - so what.flac", "01 - So What.flac"),
        # The extension is written back verbatim, not cased.
        ("track.FLAC", "Track.FLAC"),
        # An embedded acronym survives; full shouting normalizes.
        ("theme from OST.mp3", "Theme From OST.mp3"),
        ("THE MAN WITH THE HORN.wav", "The Man With the Horn.wav"),
        # Runs of whitespace in the stem collapse.
        ("multi   space   name.flac", "Multi Space Name.flac"),
        # Only the last dot splits the extension.
        ("a.b.c.flac", "A.b.c.flac"),
        # A dotted stem that cases to itself proves the split is the last
        # dot, not the first: a first-dot split would cap the lone "e".
        ("e.s.p.flac", "e.s.p.flac"),
    ],
)
def test_title_case_filename_cases_the_stem_only(name: str, expected: str) -> None:
    """The stem is cased and tidied; the extension is left exactly as found.

    Args:
        name: The filename as found.
        expected: How it should read.
    """
    assert title_case_filename(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "四人囃子.flac",  # a script with no letter case
        "Already Cased.flac",
        ".hidden",  # nothing but an extension
        # A stem of only whitespace cases to nothing; the whole name is
        # kept rather than collapsing to a bare ".flac" hidden file.
        "   .flac",
    ],
)
def test_title_case_filename_leaves_these_alone(name: str) -> None:
    """A name with nothing to case comes back unchanged.

    Args:
        name: The filename as found.
    """
    assert title_case_filename(name) == name


def test_title_case_filename_applies_english_rules_to_other_languages() -> None:
    """The convention is English, so a German title is capitalized, not fixed.

    Recorded rather than wished away: the result is capitalized rather
    than correct, which the rename preview is there to catch.
    """
    assert title_case_filename("warum bist du traurig.opus") == "Warum Bist Du Traurig.opus"
