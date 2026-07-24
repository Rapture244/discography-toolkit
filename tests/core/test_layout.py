# tests/core/test_layout.py
"""Tests for folder identification and filesystem walking.

Covers the decisions the module makes, not its every line: each case
here records a judgement that took evidence to reach.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from discography_toolkit.core.layout import (
    AUDIO_EXTENSIONS,
    LOSSLESS_EXTENSIONS,
    LOSSY_EXTENSIONS,
    OPUS_EXTENSIONS,
    discover_albums,
    find_albums,
    find_artist_folders,
    find_audio_files,
    is_artist_folder,
    is_effectively_empty,
    is_flac_container,
    owning_folder,
)
from discography_toolkit.core.metadata import SUPPORTED_EXTENSIONS

import pytest


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def artist(tmp_path: Path) -> Path:
    """Build an artist folder in the shape the pipeline produces.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        The artist folder's path.
    """
    root: Path = tmp_path / "Miles Davis - [4 • 2F • 1L • 1M]"

    kind_of_blue: Path = root / "FLAC" / "02. (1959) - Kind of Blue [FLAC]"
    kind_of_blue.mkdir(parents=True)
    (kind_of_blue / "01 - So What.flac").touch()
    (kind_of_blue / "._So What.flac").touch()

    bitches_brew: Path = root / "FLAC" / "03. (1970) - Bitches Brew [FLAC]"
    (bitches_brew / "CD 1").mkdir(parents=True)
    (bitches_brew / "CD 2").mkdir(parents=True)
    (bitches_brew / "CD 1" / "01 - Pharaoh's Dance.flac").touch()
    (bitches_brew / "CD 2" / "01 - Spanish Key.flac").touch()

    lossy: Path = root / "01. (1951) - Modern Jazz Trumpets"
    lossy.mkdir(parents=True)
    (lossy / "01 - Trumpet.mp3").touch()

    (root / "04. (1980) - M - Missing").mkdir(parents=True)

    return root


# ==================================================================================== #
#                                      EXTENSIONS                                      #
# ==================================================================================== #
def test_audio_is_exactly_what_can_be_tagged() -> None:
    """Finding a file nothing downstream can tag helps no one.

    Two hand-kept lists drift: ".dff" was once found here and rejected by
    metadata, so every run over a DSDIFF file reported an error for
    nothing.
    """
    assert AUDIO_EXTENSIONS == SUPPORTED_EXTENSIONS


def test_the_quality_tiers_partition_the_audio_set() -> None:
    """Every audio file has exactly one tier, and no tier invents one.

    The placement operation sorts by tier, so an extension missing from
    all three would be found and then never placed.
    """
    tiers: frozenset[str] = LOSSLESS_EXTENSIONS | OPUS_EXTENSIONS | LOSSY_EXTENSIONS

    assert tiers == AUDIO_EXTENSIONS
    assert not LOSSLESS_EXTENSIONS & LOSSY_EXTENSIONS
    assert not LOSSLESS_EXTENSIONS & OPUS_EXTENSIONS
    assert not LOSSY_EXTENSIONS & OPUS_EXTENSIONS


# ==================================================================================== #
#                                 FOLDER IDENTIFICATION                                #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Miles Davis - [65 • 65F • 0L • 0M]", True),
        ("Charlie Mariano - [M31 on 90]", True),
        ("Miles Davis - [65 on 65]", True),
        # Ends in a bracketed number, so the label pattern matches: only
        # the album index tells this from an artist.
        ("90. (2013) - In India [1973]", False),
        ("©43. (1970) - Live at Montreux [1970]", False),
        # No trailing bracketed number at all -- the label never matches.
        ("90. (2013) - In India [1973] [FLAC]", False),
        # The label must end the name: a bracketed number mid-name is part
        # of the title, not a count.
        ("Miles Davis [1970s] Bootlegs", False),
        ("©43. (1970) - Bitches Brew [FLAC, 40th Anniversary]", False),
        ("01. (1959) - Kind of Blue [FLAC]", False),
        # Unlabelled: unknowable, which is why detection reports rather
        # than decides.
        ("Miles Davis", False),
        ("Portishead [Live]", False),
        ("FLAC", False),
    ],
)
def test_is_artist_folder(tmp_path: Path, name: str, *, expected: bool) -> None:
    """An artist carries a count label and no album index.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        name: The folder name under test.
        expected: Whether it should be read as an artist folder.
    """
    folder: Path = tmp_path / name
    folder.mkdir()

    assert is_artist_folder(folder) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("FLAC", True),
        ("FLAC - (56 on 65)", True),
        ("flac", True),
        ("FLAC Collection", False),
        ("01. (1959) - Kind of Blue [FLAC]", False),
    ],
)
def test_is_flac_container(tmp_path: Path, name: str, *, expected: bool) -> None:
    """The container is the word FLAC plus counting punctuation, nothing else.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        name: The folder name under test.
        expected: Whether it should be read as the container.
    """
    folder: Path = tmp_path / name
    folder.mkdir()

    assert is_flac_container(folder) is expected


def test_find_artist_folders_ignores_unlabelled(tmp_path: Path) -> None:
    """Only labelled folders come back.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    for name in (
        "Miles Davis - [65 • 65F • 0L • 0M]",
        "Charlie Mariano - [M31 on 90]",
        "Unsorted Downloads",
    ):
        (tmp_path / name).mkdir()

    found: list[str] = [folder.name for folder in find_artist_folders(tmp_path)]

    assert found == [
        "Charlie Mariano - [M31 on 90]",
        "Miles Davis - [65 • 65F • 0L • 0M]",
    ]


def test_find_artist_folders_descends_to_any_depth(tmp_path: Path) -> None:
    """Artists sit under region folders, at whatever depth the shelf puts them.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Africa" / "(Nigeria) - Fela Kuti - [49 on 49]").mkdir(parents=True)
    (tmp_path / "Japan" / "Casiopea - [45 on 45]").mkdir(parents=True)
    (tmp_path / "USA" / "Miles Davis - [65 on 65]").mkdir(parents=True)

    found: list[str] = [folder.name for folder in find_artist_folders(tmp_path)]

    assert found == [
        "(Nigeria) - Fela Kuti - [49 on 49]",
        "Casiopea - [45 on 45]",
        "Miles Davis - [65 on 65]",
    ]


def test_find_artist_folders_stops_at_the_artist(tmp_path: Path) -> None:
    """An artist holds albums, never another artist, so the search prunes there.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "USA" / "Miles Davis - [65 on 65]"
    # A decoy: this album's name matches the label pattern, so it would be
    # collected as an artist if the walk went deeper.
    (artist / "FLAC" / "90. (2013) - In India [1973]").mkdir(parents=True)

    found: list[str] = [folder.name for folder in find_artist_folders(tmp_path)]

    assert found == ["Miles Davis - [65 on 65]"]


def test_find_artist_folders_does_not_enter_albums(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk stops at album level even when no artist folder is labelled.

    Verified by counting directory listings, since the result is the same
    either way -- an album is never mistaken for an artist. What differs
    is the work: without the guard the walk reads every disc folder in
    the library to find nothing.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Used to count `Path.iterdir` calls.
    """
    unlabelled: Path = tmp_path / "Miles Davis"
    for index in range(1, 11):
        album: Path = unlabelled / f"{index:02d}. (196{index}) - Album {index}"
        (album / "CD 1").mkdir(parents=True)
        (album / "CD 2").mkdir(parents=True)

    listings: list[Path] = []
    original = Path.iterdir

    def counted(self: Path) -> Iterator[Path]:
        listings.append(self)
        return original(self)

    monkeypatch.setattr(Path, "iterdir", counted)
    assert find_artist_folders(tmp_path) == []

    # tmp_path and the unlabelled folder; never an album, never a disc.
    assert len(listings) == 2


def test_find_artist_folders_on_an_artist_returns_itself(tmp_path: Path) -> None:
    """Pointing at one artist covers that artist, not nothing.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis - [65 on 65]"
    (artist / "FLAC" / "01. (1959) - Kind of Blue [FLAC]").mkdir(parents=True)

    assert find_artist_folders(artist) == [artist]


def test_find_artist_folders_groups_by_region(tmp_path: Path) -> None:
    """Walk order keeps artists under the folder that holds them.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Africa" / "Zed - [1 on 1]").mkdir(parents=True)
    (tmp_path / "Africa" / "Alpha - [1 on 1]").mkdir(parents=True)
    (tmp_path / "USA" / "Beta - [1 on 1]").mkdir(parents=True)

    found: list[str] = [folder.name for folder in find_artist_folders(tmp_path)]

    assert found == ["Alpha - [1 on 1]", "Zed - [1 on 1]", "Beta - [1 on 1]"]


# ==================================================================================== #
#                                      DISCOVERY                                       #
# ==================================================================================== #
def test_discover_albums_unwraps_the_container(artist: Path) -> None:
    """Albums inside the container come back as albums; it does not.

    Args:
        artist: The fixture artist folder.
    """
    found: list[str] = [album.name for album in discover_albums(artist)]

    assert "FLAC" not in found
    assert found == [
        "01. (1951) - Modern Jazz Trumpets",
        "02. (1959) - Kind of Blue [FLAC]",
        "03. (1970) - Bitches Brew [FLAC]",
        "04. (1980) - M - Missing",
    ]


def test_discover_albums_treats_discs_as_part_of_their_album(artist: Path) -> None:
    """A multi-disc album is one album, not one per disc.

    Args:
        artist: The fixture artist folder.
    """
    found: list[str] = [album.name for album in discover_albums(artist)]

    assert "CD 1" not in found
    assert "CD 2" not in found


def test_find_audio_files_spans_the_whole_tree(artist: Path) -> None:
    """From the artist folder, every track is found wherever it sits.

    Args:
        artist: The fixture artist folder.
    """
    found: list[str] = [track.name for track in find_audio_files(artist)]

    assert found == [
        "01 - Trumpet.mp3",
        "01 - So What.flac",
        "01 - Pharaoh's Dance.flac",
        "01 - Spanish Key.flac",
    ]


def test_find_audio_files_skips_hidden_companions(artist: Path) -> None:
    """A "._track.flac" stub has an audio extension but is not audio.

    Args:
        artist: The fixture artist folder.
    """
    found: list[str] = [track.name for track in find_audio_files(artist)]

    assert not any(name.startswith(".") for name in found)


def test_find_audio_files_scoped_to_one_album(artist: Path) -> None:
    """The same walk, given an album, returns only that album's tracks.

    Args:
        artist: The fixture artist folder.
    """
    album: Path = artist / "FLAC" / "03. (1970) - Bitches Brew [FLAC]"

    found: list[str] = [track.name for track in find_audio_files(album)]

    assert found == ["01 - Pharaoh's Dance.flac", "01 - Spanish Key.flac"]


def test_owning_folder_finds_the_containing_folder(tmp_path: Path) -> None:
    """A track belongs to the artist folder above it, however deep.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    miles: Path = tmp_path / "Miles Davis - [1 on 1]"
    track: Path = miles / "FLAC" / "01. (1959) - Kind of Blue [FLAC]" / "CD 1" / "01.flac"

    assert owning_folder(track, [miles]) == miles


def test_owning_folder_returns_none_for_a_loose_track(tmp_path: Path) -> None:
    """A track outside every artist folder belongs to none of them.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    miles: Path = tmp_path / "Miles Davis - [1 on 1]"

    assert owning_folder(tmp_path / "loose.flac", [miles]) is None


def test_find_albums_gathers_every_artists_albums(tmp_path: Path) -> None:
    """A shelf-wide run resolves each track to its own album.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    for artist, albums in (
        ("USA/Miles Davis - [2 on 2]", ("01. (1959) - Kind of Blue", "02. (1970) - Bitches Brew")),
        ("Japan/Casiopea - [1 on 1]", ("01. (1979) - Casiopea",)),
    ):
        for album in albums:
            (tmp_path / artist / "FLAC" / album).mkdir(parents=True)

    found: list[str] = [album.name for album in find_albums(tmp_path)]

    assert found == [
        "01. (1979) - Casiopea",
        "01. (1959) - Kind of Blue",
        "02. (1970) - Bitches Brew",
    ]


def test_find_albums_needs_a_labelled_artist(tmp_path: Path) -> None:
    """Albums are recognized through their artist, and nothing else says so.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Unsorted" / "01. (1959) - Kind of Blue").mkdir(parents=True)

    assert find_albums(tmp_path) == []


def test_find_albums_on_one_artist(tmp_path: Path) -> None:
    """An artist folder is its own scope.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis - [1 on 1]"
    (artist / "FLAC" / "01. (1959) - Kind of Blue").mkdir(parents=True)

    found: list[str] = [album.name for album in find_albums(artist)]

    assert found == ["01. (1959) - Kind of Blue"]


# ==================================================================================== #
#                                      EMPTINESS                                       #
# ==================================================================================== #
def test_is_effectively_empty_on_a_placeholder(artist: Path) -> None:
    """A missing album's folder holds nothing at all.

    Args:
        artist: The fixture artist folder.
    """
    assert is_effectively_empty(artist / "04. (1980) - M - Missing")


def test_is_effectively_empty_tolerates_os_junk(artist: Path) -> None:
    """Windows writes desktop.ini unasked; that is not occupancy.

    Args:
        artist: The fixture artist folder.
    """
    placeholder: Path = artist / "04. (1980) - M - Missing"
    (placeholder / "desktop.ini").touch()
    (placeholder / "Thumbs.db").touch()

    assert is_effectively_empty(placeholder)


def test_is_effectively_empty_counts_real_files(artist: Path) -> None:
    """Anything the user put there makes the folder occupied.

    Args:
        artist: The fixture artist folder.
    """
    placeholder: Path = artist / "04. (1980) - M - Missing"
    (placeholder / "notes.txt").touch()

    assert not is_effectively_empty(placeholder)


def test_is_effectively_empty_looks_below_subfolders(artist: Path) -> None:
    """An empty subfolder does not make a placeholder occupied.

    Args:
        artist: The fixture artist folder.
    """
    placeholder: Path = artist / "04. (1980) - M - Missing"
    (placeholder / "CD 1").mkdir()

    assert is_effectively_empty(placeholder)
