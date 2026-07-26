# tests/core/test_layout.py
"""Tests for folder identification and filesystem walking.

Covers the decisions the module makes, not its every line: each case
here records a judgement that took evidence to reach.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import shutil
import subprocess

from discography_toolkit.core.layout import (
    AUDIO_EXTENSIONS,
    LOSSLESS_EXTENSIONS,
    LOSSY_EXTENSIONS,
    OPUS_EXTENSIONS,
    AudioTier,
    detect_tier,
    discover_albums,
    find_albums,
    find_artist_folders,
    find_artists,
    find_audio_files,
    find_containers,
    find_cover_images,
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


def test_find_cover_images_returns_the_canonical_name_first(tmp_path: Path) -> None:
    """A caller settling on one filename needs to know which it should be.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    for name in ("folder.jpg", "cover.jpg", "front.png"):
        (tmp_path / name).touch()

    found: list[str] = [image.name for image in find_cover_images(tmp_path)]

    assert found == ["cover.jpg", "folder.jpg", "front.png"]


def test_find_cover_images_is_case_insensitive(tmp_path: Path) -> None:
    """A file written as "Folder.JPG" is still the album's cover.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Folder.JPG").touch()

    assert [image.name for image in find_cover_images(tmp_path)] == ["Folder.JPG"]


@pytest.mark.parametrize(
    "name",
    [
        "back.jpg",  # a real cover, but not the front
        "booklet.png",
        "scan01.jpg",
        "cover.gif",  # a conventional name, but not a format we store
        "cover.txt",
        "01. (1959) - Kind of Blue.flac",
    ],
)
def test_find_cover_images_ignores_everything_else(tmp_path: Path, name: str) -> None:
    """An album folder holds plenty that is not its cover.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        name: The file to place.
    """
    (tmp_path / name).touch()

    assert find_cover_images(tmp_path) == []


def test_find_cover_images_looks_no_deeper(tmp_path: Path) -> None:
    """A scan inside a subfolder is not the album's cover.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Scans").mkdir()
    (tmp_path / "Scans" / "cover.jpg").touch()

    assert find_cover_images(tmp_path) == []


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


# ==================================================================================== #
#                                     AUDIO TIER                                       #
# ==================================================================================== #
def _album_with(tmp_path: Path, *files: str) -> Path:
    """Build an album folder holding empty files of the given names.

    Extension classification reads only the suffix, so the files need no
    real content -- the one exception, `.m4a`, is exercised on its own.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        files: Filenames to place, e.g. "01.flac".

    Returns:
        The album folder.
    """
    album: Path = tmp_path / "album"
    album.mkdir(exist_ok=True)
    for name in files:
        (album / name).touch()
    return album


def test_one_lossless_file_settles_the_tier(tmp_path: Path) -> None:
    """A single lossless file is enough, whatever else sits beside it.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = _album_with(tmp_path, "01.mp3", "02.flac", "03.opus")

    assert detect_tier(album) is AudioTier.LOSSLESS


def test_opus_beats_lossy(tmp_path: Path) -> None:
    """Opus is a middle tier, above plain lossy.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = _album_with(tmp_path, "01.mp3", "02.opus")

    assert detect_tier(album) is AudioTier.OPUS


def test_a_lossy_only_album_reads_as_lossy(tmp_path: Path) -> None:
    """MP3s alone are lossy, which is held -- not missing.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = _album_with(tmp_path, "01.mp3", "02.mp3")

    assert detect_tier(album) is AudioTier.LOSSY


def test_a_folder_with_no_audio_is_none(tmp_path: Path) -> None:
    """No audio is a distinct fact from lossy: the album is not held.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = _album_with(tmp_path, "cover.jpg", "notes.txt")

    assert detect_tier(album) is AudioTier.NONE


def test_the_tier_is_read_across_disc_subfolders(tmp_path: Path) -> None:
    """A multi-disc album keeps its tier when the lossless file is deeper.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "album"
    (album / "CD 1").mkdir(parents=True)
    (album / "CD 2").mkdir(parents=True)
    (album / "CD 1" / "01.mp3").touch()
    (album / "CD 2" / "01.flac").touch()

    assert detect_tier(album) is AudioTier.LOSSLESS


def test_an_alac_m4a_counts_as_lossless(tmp_path: Path) -> None:
    """The one extension the suffix cannot decide is probed and believed.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    ffmpeg: str | None = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    album: Path = tmp_path / "album"
    album.mkdir()
    _ = subprocess.run(  # noqa: S603 - fully static command, no user input
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            "alac",
            str(album / "01.m4a"),
            "-loglevel",
            "error",
        ],
        check=True,
    )

    assert detect_tier(album) is AudioTier.LOSSLESS


def test_an_aac_m4a_is_only_lossy(tmp_path: Path) -> None:
    """The same container holding AAC does not lift the album to lossless.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    ffmpeg: str | None = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    album: Path = tmp_path / "album"
    album.mkdir()
    _ = subprocess.run(  # noqa: S603 - fully static command, no user input
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            "aac",
            str(album / "01.m4a"),
            "-loglevel",
            "error",
        ],
        check=True,
    )

    assert detect_tier(album) is AudioTier.LOSSY


# ==================================================================================== #
#                                     CONTAINERS                                       #
# ==================================================================================== #
def test_find_containers_finds_the_container(tmp_path: Path) -> None:
    """The bookkeeping folder is found; ordinary albums are not.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    (artist / "FLAC").mkdir(parents=True)
    (artist / "01. (1951) - Modern Jazz").mkdir()

    found: list[Path] = find_containers(artist)

    assert [c.name for c in found] == ["FLAC"]


def test_find_containers_recognizes_an_older_spelling(tmp_path: Path) -> None:
    """A historical "FLAC - (56 on 65)" is the same container.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    (artist / "FLAC - (56 on 65)").mkdir(parents=True)

    assert [c.name for c in find_containers(artist)] == ["FLAC - (56 on 65)"]


def test_find_containers_returns_all_matches(tmp_path: Path) -> None:
    """Two containers come back both, so a caller can refuse to merge them.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    (artist / "FLAC").mkdir(parents=True)
    (artist / "FLAC (65 on 65)").mkdir()

    assert len(find_containers(artist)) == 2


def test_find_containers_is_empty_without_one(tmp_path: Path) -> None:
    """An artist with only lossy albums has no container at all.

    A file named "FLAC" is not one either -- only a folder can be.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    (artist / "01. (1951) - Modern Jazz").mkdir(parents=True)
    _ = (artist / "FLAC").write_text("a note, not a container")

    assert find_containers(artist) == []


# ==================================================================================== #
#                                  ARTISTS BY AUDIO                                    #
# ==================================================================================== #
def _track(album: Path) -> None:
    """Put one stub audio file in a folder, making its parents.

    Args:
        album: The folder to place a track in.
    """
    album.mkdir(parents=True, exist_ok=True)
    _ = (album / "01.flac").write_bytes(b"x")


def test_find_artists_walks_a_nested_shelf(tmp_path: Path) -> None:
    """An artist is found however deep the shelf buries it.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    _track(tmp_path / "Jazz" / "Africa" / "Fela Kuti" / "(1972) - Zombie")

    found: list[Path] = find_artists(tmp_path)

    assert found == [tmp_path / "Jazz" / "Africa" / "Fela Kuti"]


def test_find_artists_returns_an_artist_once(tmp_path: Path) -> None:
    """Many albums under one artist still name it a single time.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    _track(artist / "(1959) - Kind of Blue")
    _track(artist / "(1970) - Live")

    assert find_artists(tmp_path) == [artist]


def test_find_artists_steps_over_the_container(tmp_path: Path) -> None:
    """A lossless album inside the container still points to the artist, not it.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    _track(artist / "FLAC" / "(1959) - Kind of Blue")

    assert find_artists(tmp_path) == [artist]


def test_find_artists_steps_over_a_disc_folder(tmp_path: Path) -> None:
    """A multi-disc album resolves to its artist, not the album or the disc.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    _track(artist / "(1970) - Bitches Brew" / "CD 1")
    _track(artist / "(1970) - Bitches Brew" / "CD 2")

    assert find_artists(tmp_path) == [artist]


def test_find_artists_steps_over_disc_and_container_together(tmp_path: Path) -> None:
    """A multi-disc lossless album crosses both levels to reach the artist.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    _track(artist / "FLAC" / "(1970) - Bitches Brew" / "CD 1")

    assert find_artists(tmp_path) == [artist]


def test_find_artists_finds_an_artist_pointed_at_directly(tmp_path: Path) -> None:
    """A path that is itself an artist returns just itself.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    _track(artist / "(1959) - Kind of Blue")

    assert find_artists(artist) == [artist]


def test_find_artists_ignores_an_artist_without_audio(tmp_path: Path) -> None:
    """An artist whose albums are all empty placeholders cannot be anchored on.

    A real artist beside it is still found; the empty one is not.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    _track(tmp_path / "Miles Davis" / "(1959) - Kind of Blue")
    (tmp_path / "Ghost Artist" / "(1999) - Nothing").mkdir(parents=True)

    assert find_artists(tmp_path) == [tmp_path / "Miles Davis"]


def test_find_artists_still_finds_an_artist_with_a_placeholder(tmp_path: Path) -> None:
    """An empty album beside a real one does not stop the artist being found.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    _track(artist / "(1959) - Kind of Blue")
    (artist / "(1980) - M - Missing").mkdir()

    assert find_artists(tmp_path) == [artist]


def test_find_artists_clamps_to_the_given_path(tmp_path: Path) -> None:
    """Pointed at an album, it reaches no artist above and returns nothing.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "Miles Davis" / "(1959) - Kind of Blue"
    _track(album)

    assert find_artists(album) == []


def test_find_artists_does_not_descend_into_a_numbered_album(tmp_path: Path) -> None:
    """A box set is an album by its index, so its inner folders are never albums.

    Its subfolders hold audio but are not discs, which is what fooled a
    walk that started from the audio: here the numbered box stops the
    descent, so the artist is the box's owner, not the box.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Miles Davis"
    box: Path = artist / "FLAC" / "24. (1957) - The Complete Birth of the Cool [FLAC]"
    _track(box / "Sessions 1948")
    _track(box / "Sessions 1950")

    assert find_artists(tmp_path) == [artist]


def test_find_artists_reads_an_organised_shelf(tmp_path: Path) -> None:
    """Two labelled artists, each with numbered albums, come back as two.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    mariano: Path = tmp_path / "Charlie Mariano - [1 \u2022 1F \u2022 0L \u2022 0M]"
    _track(mariano / "FLAC" / "01. (1962) - Mirror")
    davis: Path = tmp_path / "Miles Davis - [1 \u2022 1F \u2022 0L \u2022 0M]"
    _track(davis / "FLAC" / "01. (1959) - Kind of Blue [FLAC]")

    assert find_artists(tmp_path) == sorted([mariano, davis])


def test_find_artists_pointed_at_a_numbered_album_finds_nothing(tmp_path: Path) -> None:
    """A numbered box set is an album, not an artist, even pointed at directly.

    Without the index stopping the descent, its inner audio-bearing
    folders would read as albums and the box as their artist.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    box: Path = tmp_path / "24. (1957) - The Complete Birth of the Cool [FLAC]"
    _track(box / "Sessions 1948")
    _track(box / "Sessions 1950")

    assert find_artists(box) == []


def test_find_artists_pointed_at_a_multi_disc_album_finds_nothing(tmp_path: Path) -> None:
    """A fresh multi-disc album is an album; its discs are not sub-albums.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "(1970) - Bitches Brew"
    _track(album / "CD 1")
    _track(album / "CD 2")

    assert find_artists(album) == []


def test_find_artists_walks_through_a_numbered_category(tmp_path: Path) -> None:
    """A numbered organizational folder is not an album, so it is walked.

    "01. Countries" shares the shape of an album's index but its real
    content is levels down; it must be descended into, not dismissed.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "01. Countries" / "Africa" / "(Mali) - Ali Farka Touré - [M1 on 20]"
    _track(artist / "01. (1976) - Special")

    assert find_artists(tmp_path / "01. Countries") == [artist]


def test_find_artists_does_not_flag_a_category_parent(tmp_path: Path) -> None:
    """The folder above a numbered category is not itself taken for an artist.

    The category would otherwise read as an album child, making its
    parent look like the artist.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = (
        tmp_path / "DISCOGRAPHY" / "01. Countries" / "Africa" / "(Niger) - Mdou Moctar - [13 on 13]"
    )
    _track(artist / "01. (2013) - Afelan [FLAC]")

    assert find_artists(tmp_path / "DISCOGRAPHY") == [artist]


def test_find_artists_keeps_a_missing_placeholder_from_breaking_detection(tmp_path: Path) -> None:
    """An empty numbered placeholder counts as an album, so the artist is found.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "(Mali) - Ali Farka Touré - [M1 on 20]"
    _track(artist / "01. (1976) - Special")
    (artist / "18. MISSING (2011) - Sac A Paroles").mkdir()  # empty placeholder

    assert find_artists(tmp_path) == [artist]


def test_find_artists_finds_an_artist_of_only_placeholders(tmp_path: Path) -> None:
    """An artist whose albums are all numbered placeholders is still found.

    A numbered placeholder is a known album, missing for now; an artist
    made only of them has nothing to anchor on but is not a bare shelf.
    An unnumbered empty folder, by contrast, cannot be told from one.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "(Mali) - Lost Artist - [M2 on 2]"
    (artist / "01. MISSING (1970) - A").mkdir(parents=True)
    (artist / "02. MISSING (1972) - B").mkdir()

    assert find_artists(tmp_path) == [artist]
