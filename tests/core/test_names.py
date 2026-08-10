# tests/core/test_names.py
"""Tests for name parsing and casing.

Each case records a judgement that took evidence to reach -- a name that
looked like it would work and did not, or one that had to survive
untouched.
"""

from __future__ import annotations

from discography_toolkit.core.names import (
    album_title,
    clean_name,
    conforms_body,
    conforms_unnumbered,
    drop_unpaired_wrappers,
    extract_year,
    format_artist_label,
    has_lowercase_ep,
    is_approximate_year,
    is_singles,
    sort_key,
    split_ep_marker,
    split_front_mark,
    split_index,
    split_missing_marker,
    split_year,
    strip_artist_label,
    strip_quality_tag,
    title_case,
    title_case_filename,
    track_number,
    with_artist_label,
    wrappers_balanced,
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


def test_strip_artist_label_closes_a_doubled_space() -> None:
    """A typo in the folder must not travel into the tag or the playlist.

    "Young  Thug" reaches the Album Artist tag and names the playlist
    folder the artist is filed under, where an exact-match search then
    misses the "Young Thug" already there and files everything a level
    deeper.
    """
    assert strip_artist_label("Young  Thug - [29 \u2022 28F \u2022 1L \u2022 0M]") == "Young Thug"


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


def test_with_artist_label_closes_a_doubled_space() -> None:
    """The artist folder is the one name nothing else tidies.

    An album's is rebuilt through `clean_name` and a track's through
    `title_case_filename`, so a doubled space in either is closed by the
    pass that touches it. Nothing rebuilt the artist folder, so a typo
    there survived every run.
    """
    label: str = format_artist_label(29, 28, 1, 0)

    assert with_artist_label("Young  Thug", label) == f"Young Thug - {label}"
    assert with_artist_label(f"Young  Thug - {label}", label) == f"Young Thug - {label}"


def test_with_artist_label_keeps_edge_punctuation() -> None:
    """Only whitespace is collapsed, not the stops an artist is entitled to.

    `clean_name` would take the trailing stop as a leftover separator and
    hand back "R.E.M", which is a different band's name.
    """
    label: str = format_artist_label(1, 1, 0, 0)

    assert with_artist_label("R.E.M.", label) == f"R.E.M. - {label}"


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
#                                   FRONT MARK & INDEX                                 #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "remainder", "expected"),
    [
        ("\u00a9 Kind of Blue", "Kind of Blue", "\u00a9"),
        # Not anchored: found wherever it was typed, front, middle or end.
        ("01. \u00a9 Kind of Blue", "01. Kind of Blue", "\u00a9"),
        ("Kind of Blue \u00a9", "Kind of Blue", "\u00a9"),
        ("01. Kind \u00a9 of Blue", "01. Kind of Blue", "\u00a9"),
        # The dud mark rides the same mechanism -- one split, two verdicts.
        ("\u2717 Kind of Blue", "Kind of Blue", "\u2717"),
        ("01. \u2717 Kind of Blue", "01. Kind of Blue", "\u2717"),
        ("Kind of Blue \u2717", "Kind of Blue", "\u2717"),
        ("01. Kind \u2717 of Blue", "01. Kind of Blue", "\u2717"),
    ],
)
def test_split_front_mark_lifts_the_mark(name: str, remainder: str, expected: str) -> None:
    """Either mark comes off wherever it sat, and the gap is tidied.

    Args:
        name: A name carrying a verdict mark.
        remainder: What should be left, cleaned.
        expected: The mark that should come back.
    """
    mark, rest = split_front_mark(name)

    assert mark == expected
    assert rest == remainder


@pytest.mark.parametrize("name", ["Kind of Blue", "01. So What", ""])
def test_split_front_mark_leaves_an_unmarked_name(name: str) -> None:
    """No mark means the name comes back untouched.

    Args:
        name: A name with no verdict mark.
    """
    mark, rest = split_front_mark(name)

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


def test_extract_year_reads_a_month_beside_the_year() -> None:
    """A month is the only thing that can order two albums from one year.

    Read from all three shapes a year is written in, so a name typed
    without brackets settles the same way as one with them.
    """
    assert extract_year("01. (2017-04) - Perfect Timing [FLAC]") == "2017-04"
    assert extract_year("03. [2017-10] - Too Hard [FLAC]") == "2017-10"
    assert extract_year("Perfect Timing 2017-04") == "2017-04"


def test_a_range_is_not_a_month() -> None:
    """A span of years is two years, and the second is not December.

    The month pattern admits 01 through 12 and nothing else, so
    "1999-2001" resolves to its first year and the rest stays in the
    title where it can be seen.
    """
    assert extract_year("Anthology 1999-2001") == "1999"


def test_a_month_orders_two_albums_from_one_year() -> None:
    """Sorting needs no rule for it: the string already carries the order.

    ")" sorts below "-", so an undated album leads the dated ones of its
    year rather than landing among them alphabetically.
    """
    names: list[str] = [
        "(2017-10) - Too Hard",
        "(2017-03) - Perfect Timing",
        "(2017) - An Undated One",
    ]

    assert sorted(names, key=sort_key) == [
        "(2017) - An Undated One",
        "(2017-03) - Perfect Timing",
        "(2017-10) - Too Hard",
    ]


@pytest.mark.parametrize(
    "name",
    [
        # Not a month: the range only runs to twelve.
        "01. (2017-13) - Album",
        "01. (2017-00) - Album",
        # Two digits are required, so a lone "4" is not April.
        "01. (2017-4) - Album",
    ],
)
def test_a_bad_month_leaves_the_year_alone(name: str) -> None:
    """An impossible month is not silently taken as one.

    The month group simply does not match, so the year alone is found and
    what follows stays in the title -- visible in the rename preview
    rather than written as a date nothing can read.

    Args:
        name: An album folder name carrying an impossible month.
    """
    assert extract_year(name) == "2017"


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
        ("\u00a901. (1997) - M - Kind of Blue [FLAC]", "(1997) - kind of blue"),
        ("\u271701. (1997) - M - Kind of Blue [FLAC]", "(1997) - kind of blue"),
        ("05. (1959) - So What [FLAC]", "(1959) - so what"),
        # The conflict glyph is stripped the same as the plain marker.
        ("12. (1980) - \u26a0 - Decoy", "(1980) - decoy"),
        # A name with none of the strippable parts casefolds as it is.
        ("No Year Album", "no year album"),
    ],
)
def test_sort_key_ignores_what_should_not_move_an_album(name: str, expected: str) -> None:
    """The key is year and title, with everything else taken out.

    Args:
        name: The raw album folder name.
        expected: The key it should produce.
    """
    assert sort_key(name) == expected


def test_sort_key_is_blind_to_which_verdict_a_name_carries() -> None:
    """A verdict says nothing about where an album belongs in sequence.

    Pinning a favourite or writing off a dud must not renumber the shelf,
    so both marks fall out of the key and a pinned, a dud and a bare name
    order identically.
    """
    bare: str = "03. (1959) - Kind of Blue [FLAC]"

    assert sort_key(f"\u00a9{bare}") == sort_key(bare)
    assert sort_key(f"\u2717{bare}") == sort_key(bare)


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


def test_sort_key_puts_a_sequel_after_the_album_it_follows() -> None:
    """The quality word must not decide a tie between two titles.

    "Slime Season" and "Slime Season 2" agree as far as the sequel's
    number, and there the first compares its "[" against the second's
    "2" -- which sorts "[" the higher and puts the sequel first.
    Numbering then hands the sequel the lower index, and the shelf reads
    with the second tape ahead of the first.

    Any album whose title is another's with something appended has the
    same fault, which is why the tag comes out of the key rather than
    this one pair being special-cased.
    """
    sequel: str = "12. (2015) - Slime Season 2 [FLAC]"
    first: str = "13. (2015) - Slime Season [FLAC]"

    assert sorted([sequel, first], key=sort_key) == [first, sequel]


def test_sort_key_is_blind_to_the_quality_word() -> None:
    """How a copy was ripped says nothing about where the album belongs."""
    assert sort_key("01. (1959) - Kind of Blue [FLAC]") == sort_key(
        "01. (1959) - Kind of Blue [OPUS]"
    )


@pytest.mark.parametrize(
    "name",
    [
        "Singles",
        "singles",  # case-insensitive
        "SINGLES",
        "00. Singles",
        "00. Singles [FLAC]",
        "©05. Singles [OPUS]",
        "✗05. Singles [OPUS]",
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
#                                      EP MARKER                                       #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "remainder"),
    [
        # Every shape, wherever it sits.
        ("Zomba EP", "Zomba"),
        ("EP Zomba", "Zomba"),
        ("Zomba (EP)", "Zomba"),
        ("Zomba [EP]", "Zomba"),
        ("(EP) Zomba", "Zomba"),
        # Mid-name, the shape the collection actually carries.
        ("EP KILL YOUR$ELF Part V", "KILL YOUR$ELF Part V"),
        ("Kill Your$elf EP Part V", "Kill Your$elf Part V"),
        # A tag sharing the bracket is kept, as the quality word's is.
        ("Zomba [EP, Remastered]", "Zomba [Remastered]"),
        ("Zomba (EP, Live)", "Zomba (Live)"),
        # Alongside a quality tag, which this leaves for its own step.
        ("Zomba [EP] [FLAC]", "Zomba [FLAC]"),
    ],
)
def test_split_ep_marker_finds_it_anywhere(name: str, remainder: str) -> None:
    """The marker is taken wherever and however it was typed.

    Args:
        name: A name past year extraction.
        remainder: What should be left once the marker is gone.
    """
    is_ep, rest = split_ep_marker(name)

    assert is_ep
    assert rest == remainder


@pytest.mark.parametrize(
    "name",
    [
        # Word-bounded, so it cannot eat into a title.
        "Epitaph",
        "Epic",
        "Epilogue",
        "EPMD",
        # Case-sensitive: only the all-capital marker counts.
        "Zomba ep",
        "Zomba Ep",
        "Zomba [ep]",
        # Ignored, as agreed.
        "Zomba E.P.",
        "Kind of Blue",
        "",
    ],
)
def test_split_ep_marker_leaves_the_rest_alone(name: str) -> None:
    """No marker means the name is handed back whole.

    Args:
        name: A name carrying no all-capital marker.
    """
    is_ep, rest = split_ep_marker(name)

    assert not is_ep
    assert rest == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Zomba ep", True),
        ("Zomba Ep", True),
        ("Zomba eP", True),
        ("Zomba [ep]", True),
        # The all-capital form is a marker, not a miscasing.
        ("Zomba EP", False),
        # Word-bounded here too.
        ("Epitaph", False),
        ("Sleep", False),
        ("Kind of Blue", False),
    ],
)
def test_has_lowercase_ep(name: str, expected: bool) -> None:
    """A miscased "ep" is reported without being acted on.

    Args:
        name: The album folder's name.
        expected: Whether one should be found.
    """
    assert has_lowercase_ep(name) is expected


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
        # Every codec goes, not only the two a settled name can carry:
        # what an album is held in is a fact about its files.
        ("Kind of Blue [MP3]", "Kind of Blue"),
        ("Kind of Blue [AAC]", "Kind of Blue"),
        ("Kind of Blue (WavPack)", "Kind of Blue"),
        ("Kind of Blue [ape]", "Kind of Blue"),
        # A second bracket that is part of the title survives.
        ("Bird [Live] [FLAC]", "Bird [Live]"),
        ("Bird [Live] [MP3]", "Bird [Live]"),
        # A bare trailing word, when nothing is bracketed.
        ("Some Album FLAC", "Some Album"),
        ("Some Album OPUS", "Some Album"),
        ("Some Album MP3", "Some Album"),
    ],
)
def test_strip_quality_tag_removes_the_word(name: str, expected: str) -> None:
    """A stale codec word goes, whichever of them is present.

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
        ("Nefertiti [MP3, Remastered]", "Nefertiti [Remastered]"),
    ],
)
def test_strip_quality_tag_keeps_a_shared_bracket(name: str, expected: str) -> None:
    """Only the codec is cut; a tag sharing its bracket is kept, in style.

    Args:
        name: A name whose bracket holds a codec and something else.
        expected: The name with just the codec removed.
    """
    assert strip_quality_tag(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Nefertiti [FLAC m4a]", "Nefertiti"),
        ("Nefertiti [FLAC, ALAC]", "Nefertiti"),
    ],
)
def test_strip_quality_tag_empties_a_bracket_of_only_codecs(name: str, expected: str) -> None:
    """A bracket naming one album twice leaves nothing behind.

    Every codec in the bracket is cut, not just the first, so the bracket
    goes rather than surviving with the leftovers of a rip tag in it.

    Args:
        name: A name whose bracket holds nothing but codecs.
        expected: The name once the whole bracket is gone.
    """
    assert strip_quality_tag(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # "Opus" and "Ape" are ordinary title words, and only an
        # all-caps trailing word is treated as a marker.
        "Magnum Opus",
        "Opus One",
        "Opus de Jazz",
        "Planet of the Ape",
        # Lowercase "flac" outside a bracket is left for the eye, since
        # eating a title word is the worse mistake.
        "Miles Ahead flac",
        # A code gluing the codec to something else is not a rip tag.
        "Music of the Bahnar [24bVFLAC]",
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
#                                     ALBUM TITLES                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("01. (1959) - Kind of Blue [FLAC]", "Kind of Blue"),
        # Either quality word comes off, so an Opus-only album -- one with
        # no lossless twin for pruning to have deleted it against -- reads
        # like any other.
        ("04. (1972) - On the Corner [OPUS]", "On the Corner"),
        # A verdict floats or sinks an album on this shelf and travels
        # nowhere: neither mark reaches the tag.
        ("©27. (1977) - October [FLAC]", "October"),
        ("✗27. (1977) - October [FLAC]", "October"),
        # Both spellings of the availability marker.
        ("05. (1980) - M - Lost Record", "Lost Record"),
        ("12. (1980) - ⚠ - Decoy", "Decoy"),
        # The yearless exception.
        ("00. Singles [FLAC]", "Singles"),
        # A bracket that is part of the title survives the quality cut.
        ("03. (1963) - Live at Birdland [Live] [FLAC]", "Live at Birdland [Live]"),
        # The EP marker is the one thing peeled only to be put back: it
        # says what the release is, which is as true in another library
        # as in this one.
        ("03. (2013) - Summer Knights (EP) [FLAC]", "Summer Knights (EP)"),
        # Sharing a bracket with a tag worth keeping, only the marker moves.
        ("02. (2011) - Old Soul [EP, Remastered] [FLAC]", "Old Soul [Remastered] (EP)"),
        # Nothing to peel.
        ("Kind of Blue", "Kind of Blue"),
        ("", ""),
    ],
)
def test_album_title_peels_the_shelf_off_a_name(name: str, expected: str) -> None:
    """Mark, index, year, marker and quality word all come off; "(EP)" stays.

    What survives is what the album is called -- the part that does not
    change when it is renumbered, re-ripped, or found at last. Being an
    EP does not change either, which is why it is the one marker that
    travels with the title rather than staying behind with the shelf.

    Args:
        name: The album folder's name.
        expected: The title that should remain.
    """
    assert album_title(name) == expected


def test_album_title_is_stable_across_renumbering() -> None:
    """Inserting an album ahead of another must not rename it.

    This is what lets the Album tag stand for identity: the numbering
    pass rewrites the folder, and the title read off it does not move.
    """
    assert album_title("01. (1959) - Kind of Blue [FLAC]") == album_title(
        "04. (1959) - Kind of Blue [FLAC]"
    )


def test_album_title_is_the_same_however_the_ep_was_typed() -> None:
    """Every shape resolves to one title, which is what pruning matches on.

    The marker is written four ways across the shelf and settles into one
    -- so an EP typed differently in two folders is still one record,
    and an EP is still not the album that shares its name.
    """
    shapes: list[str] = [
        "01. (1994) - Zomba (EP) [FLAC]",
        "01. (1994) - Zomba [EP] [FLAC]",
        "01. (1994) - Zomba EP [FLAC]",
        "01. (1994) - EP Zomba [FLAC]",
    ]

    assert {album_title(shape) for shape in shapes} == {"Zomba (EP)"}


# ==================================================================================== #
#                                      WRAPPERS                                        #
# ==================================================================================== #
@pytest.mark.parametrize(
    "text",
    [
        "Kind of Blue",
        "Live at Birdland [Live]",
        "Bitches Brew (Remastered)",
        "Nefertiti [40th Anniversary] (Live)",
        "((nested))",
        "",
    ],
)
def test_wrappers_balanced_accepts_a_paired_name(text: str) -> None:
    """A wrapper that meets its partner is part of the title and stays.

    Args:
        text: A name whose brackets all pair.
    """
    assert wrappers_balanced(text)


@pytest.mark.parametrize(
    "text",
    [
        "] the Mad Writer",  # a closer whose opener went with a peeled token
        "Kind of Blue (Remastered",  # an opener whose closer never arrived
        "[40th Anniversary",
        "(a]",  # crossed: two orphans, not a pair
        ")Kind of Blue(",  # right glyphs, wrong order
    ],
)
def test_wrappers_balanced_rejects_an_orphan(text: str) -> None:
    """Balance is the test, not absence: an unpaired wrapper fails it.

    Args:
        text: A name carrying an orphan.
    """
    assert not wrappers_balanced(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("] the Mad Writer", "the Mad Writer"),
        ("Kind of Blue (Remastered", "Kind of Blue Remastered"),
        ("(a]", "a"),
        # A pair is the title's own and survives, orphan or not beside it.
        ("Live at Birdland [Live]", "Live at Birdland [Live]"),
        ("] Bird [Live]", "Bird [Live]"),
        ("Kind of Blue", "Kind of Blue"),
    ],
)
def test_drop_unpaired_wrappers(text: str, expected: str) -> None:
    """Orphans go and pairs stay, with the spacing left behind tidied.

    An orphan says nothing on its own -- there is no question of what was
    meant by it -- so this repairs rather than reports.

    Args:
        text: A name past the peel.
        expected: How it should read once repaired.
    """
    assert drop_unpaired_wrappers(text) == expected


def test_drop_unpaired_wrappers_leaves_a_balanced_name_alone() -> None:
    """What it repairs, `wrappers_balanced` then agrees is repaired.

    The two answer one question, one by acting and one by asking, so the
    output of the first has to satisfy the second.
    """
    for text in ("] Bird [Live]", "(a]", "Kind of Blue (Remastered"):
        assert wrappers_balanced(drop_unpaired_wrappers(text))


# ==================================================================================== #
#                                     CONFORMANCE                                      #
# ==================================================================================== #
@pytest.mark.parametrize(
    "body",
    [
        "(1959) - Kind of Blue [FLAC]",
        "(1972) - On the Corner [OPUS]",
        "(1951) - Modern Jazz Trumpets",  # lossy earns no tag
        "(1980) - M - Lost Record",  # declared missing
        "(1985) - \u26a0 - Decoy",  # the conflict marker
        "(1994) - Zomba (EP) [FLAC]",
        "(1994) - Zomba (EP)",
        "(199x) - Unknown Decade",  # an approximate year is still a year
        # A month, for two albums a year alone cannot order.
        "(2017-04) - Perfect Timing [FLAC]",
        "(1963) - Live at Birdland [Live] [FLAC]",  # a bracket the title owns
        # A title may hold " - " of its own: the marker group takes only
        # "M" or "\u26a0", so anything else after the year is simply title.
        "(1959) - X - Kind of Blue",
        # The pattern cannot tell a codec bracket from one the title
        # owns -- "[Live]" and "[MP3]" are the same shape. It does not
        # have to: the rebuild strips every codec before a name is judged,
        # so this leniency is never reached by a name the pass produced.
        "(1959) - Kind of Blue [MP3]",
        "Singles",  # the yearless exception, bare
    ],
)
def test_conforms_body_accepts_a_settled_name(body: str) -> None:
    """What a shelf reads as once every step has run.

    Args:
        body: A name past its index.
    """
    assert conforms_body(body)


@pytest.mark.parametrize(
    "body",
    [
        "Kind of Blue [FLAC]",  # no year to anchor it
        "(1959) Kind of Blue",  # the separator is required
        "1959 - Kind of Blue",  # the year must be wrapped
        "(1959) - ",  # a year and no title
        "(1959) - M - ",  # a marker and no title
        "(1959) - Kind  of Blue",  # spacing a rebuild would have closed
        "(1959) - Kind of Blue ",  # an untrimmed end
        "(1959) - Kind of Blue]",  # an orphan the peel left behind
        "(2017-13) - Album",  # not a month
        "Singles [FLAC]",  # one pile per artist, so it carries no tag
        "singles",  # the title is cased by the time it is judged
        "",
    ],
)
def test_conforms_body_rejects_everything_else(body: str) -> None:
    """The net for whatever the rebuild could not settle.

    Matching the skeleton is not enough on its own -- the title pattern
    swallows an orphan bracket as happily as a letter -- so what lands in
    it is judged too.

    Args:
        body: A name past its index.
    """
    assert not conforms_body(body)


@pytest.mark.parametrize(
    "name",
    [
        "01. (1959) - Kind of Blue [FLAC]",
        "\u00a905. (1959) - Kind of Blue [FLAC]",  # pinned
        "\u271705. (1959) - Kind of Blue [FLAC]",  # written off
        "127. (1970) - Bitches Brew",  # a run past ninety-nine
        "5. (1959) - Kind of Blue",  # an index numbering has yet to pad
        "(1959) - Kind of Blue",  # never numbered at all
        "00. Singles",
    ],
)
def test_conforms_unnumbered_ignores_the_index(name: str) -> None:
    """Numbering owns the index and rewrites it wholesale, so it is not judged.

    Whatever shape the index is in now -- padded, unpadded, absent -- it
    comes out as "01. " regardless. What matters before then is
    everything after it, which no later step touches.

    Args:
        name: An album folder's name, or one naming proposes.
    """
    assert conforms_unnumbered(name)


@pytest.mark.parametrize(
    "name",
    [
        "01. kind of blue",  # uncased, and no year
        "01. (1959) - Kind of Blue] [FLAC]",  # an orphan survives numbering
        "01. A Folder With No Year",
        "01. (1959) - Kind  of Blue",  # spacing a rebuild would have closed
    ],
)
def test_conforms_unnumbered_still_judges_the_rest(name: str) -> None:
    """Past the index, the name is held to the pattern in full.

    Args:
        name: An album folder's name, or one naming proposes.
    """
    assert not conforms_unnumbered(name)


def test_conforms_unnumbered_tidies_a_marked_name_as_it_reads_it() -> None:
    """A known quirk: lifting the mark also closes the spacing behind it.

    `split_front_mark` tidies what it leaves, so a doubled space in a
    marked name is repaired before the pattern ever sees it, while the
    same name unmarked is judged as typed. Recorded rather than wished
    away: naming rebuilds the whole name regardless, so the guard reading
    a marked album slightly more leniently costs nothing.
    """
    assert conforms_unnumbered("\u00a9 (1959) - Kind  of Blue")
    assert conforms_unnumbered("\u2717 (1959) - Kind  of Blue")
    assert not conforms_unnumbered("01. (1959) - Kind  of Blue")


# ==================================================================================== #
#                                    TRACK NUMBERS                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "01"),
        ("01", "01"),
        ("9", "09"),
        ("10", "10"),
        # Past ninety-nine the padding stops rather than truncating: a
        # box set's hundredth track is not "00".
        ("100", "100"),
        # ID3 carries "track of total"; the total is nobody's business
        # here, and no other tag in the collection holds one.
        ("5/12", "05"),
        ("12/12", "12"),
        (" 7 ", "07"),
        # Leading zeros beyond the width are not a wider number.
        ("007", "07"),
    ],
)
def test_track_number_settles_to_two_digits(raw: str, expected: str) -> None:
    """One form for every track number, whatever the ripper wrote.

    Args:
        raw: The number as a file carries it.
        expected: What it should settle to.
    """
    assert track_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        # A vinyl side, which is a real thing to find on a rip.
        "A1",
        "one",
        "/12",
        "-3",
    ],
)
def test_track_number_refuses_what_it_cannot_settle(raw: str) -> None:
    """Nothing to settle is a person's to look at, not a rule's to guess.

    Inventing a number would be inventing a running order, and a file
    with none sorts wrong in every player -- which is worth reporting
    rather than papering over.

    Args:
        raw: A track number that is not one.
    """
    assert track_number(raw) is None


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
        # A word in the keep-caps list stays capital, and a lower-cased
        # one is restored to it.
        ("theme from OST", "Theme From OST"),
        ("theme from ost", "Theme From OST"),
        ("live at BBC", "Live at BBC"),
        ("recorded in usa", "Recorded in USA"),
        ("a flac rip", "A FLAC Rip"),
        # An acronym standing alone among lower-case words is the case
        # the taming pass would otherwise lower and re-cap as "Hd".
        ("an hd remaster", "An HD Remaster"),
        ("an HD remaster", "An HD Remaster"),
        # Roman numerals stay capital; a word that merely looks like one
        # does not.
        ("it's standard time, vol. II", "It's Standard Time, Vol. II"),
        ("guitars from agadez vol. iii", "Guitars From Agadez Vol. III"),
        ("the mix", "The Mix"),
        # A code gluing letters to digits is kept exactly as written,
        # standing alone or among words.
        ("SF120", "SF120"),
        ("78RPM", "78RPM"),
        ("[SF034] group inerane", "[SF034] Group Inerane"),
        ("music of the bahnar [24bVFLAC]", "Music of the Bahnar [24bVFLAC]"),
    ],
)
def test_title_case_keeps_acronyms_romans_and_codes(text: str, expected: str) -> None:
    """Known acronyms, roman numerals and letter-digit codes stay in caps.

    Args:
        text: The title as found.
        expected: How it should read.
    """
    assert title_case(text) == expected


def test_title_case_keeps_an_acronym_through_the_taming_pass() -> None:
    """Shouting comes down, but a keep-caps word inside it does not.

    The taming pass lowers every needless all-caps word so the caser will
    lift it back up; a word worth keeping has to survive that, or it
    comes back capitalised like any other -- "HD" as "Hd".
    """
    assert title_case("AN HD REMASTER") == "An HD Remaster"


def test_title_case_normalizes_shouting() -> None:
    """A title in full capitals is shouted, not one long acronym.

    The acronym guard is suspended when nothing around a capitalized word
    is lowercase, since there is then nothing to make it stand out.
    """
    assert title_case("THE MAN WITH THE HORN") == "The Man With the Horn"


def test_title_case_normalizes_shouting_past_a_mixed_case_tag() -> None:
    """Shouting is tamed even when a rip tag leaves a stray lower-case letter.

    A lone lowercase letter -- the "b" of "[24bVFLAC]" -- would stop the
    library reading the rest as shouting, so the taming is done first.
    """
    assert title_case("MUSIC OF THE BAHNAR [24bVFLAC]") == "Music of the Bahnar [24bVFLAC]"


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
