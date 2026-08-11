# tests/operations/test_playlist.py
"""Tests for folding converted albums into a playlist.

Every case here is a mistake this module made against a real library.
The command moves folders and writes tags in one pass with no dry run,
so a wrong answer reaches the files: one folder matched wrongly once
cost an entire artist their Album tags. What the tests pin, above all,
is that nothing which is not an album is ever treated as one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.core import artwork, metadata
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations.playlist import (
    COVER_NAME,
    album_tag,
    apply,
    apply_covers,
    find_homes,
    identity,
    loose_albums,
    plan,
    plan_covers,
    settled_name,
)

import pytest
from tests.helpers import encode, silence

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def album(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an album folder of tagged tracks.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking a path relative to the temporary directory, the
        Album tag its tracks should carry -- `None` for none at all --
        and how many tracks to write.
    """

    def build(relative: str, tag: str | None = "Kind of Blue", tracks: int = 1) -> Path:
        folder: Path = tmp_path / relative
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(tracks):
            track: Path = folder / f"{index + 1:02d}.flac"
            silence(track)
            if tag is not None:
                metadata.write(track, {Tag.ALBUM: tag})
        return folder

    return build


# ==================================================================================== #
#                                      IDENTITY                                        #
# ==================================================================================== #
def test_identity_reads_the_bare_title(album: Callable[..., Path]) -> None:
    """A fresh conversion carries the title alone, which needs no peeling.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Miles Davis - Kind of Blue", tag="Kind of Blue")

    assert identity(folder) == "kind of blue"


def test_identity_peels_a_folder_this_pass_settled(album: Callable[..., Path]) -> None:
    """A second run must read its own writing, or it would fold twice.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("settled", tag="\u00a9(1993) - 60 Horses in My Herd [OPUS]")

    assert identity(folder) == "60 horses in my herd"


def test_identity_keeps_a_title_starting_with_a_number(album: Callable[..., Path]) -> None:
    """A leading number is part of the title, not a numbering index.

    `album_title` peels a folder name, so its first act is to read "60"
    as an index and hand back "Horses in My Herd" -- which matched no
    album and left the record unconverted with no explanation.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("bare", tag="60 Horses in My Herd")

    assert identity(folder) == "60 horses in my herd"


def test_identity_ignores_audio_deeper_than_a_disc(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """A folder holding albums has no identity of its own.

    The bug that cost an artist their tags: a converter left the albums
    nested one folder deeper than usual, a recursive read found a track
    several levels down, and the whole tree was reported as that one
    album -- then matched, moved, and stamped with its name.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    _ = album("drop/(Australia) -/Hudson - 1991 - Sound of the Earth", tag="Sound of the Earth")
    _ = album("drop/(Australia) -/Hudson - 1993 - Woolunda", tag="Woolunda")

    assert identity(tmp_path / "drop" / "(Australia) -") is None


def test_identity_reads_a_disc_subfolder(album: Callable[..., Path], tmp_path: Path) -> None:
    """One disc down is still the album's own, unlike anything deeper.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    _ = album("Bitches Brew/CD 1", tag="Bitches Brew")

    assert identity(tmp_path / "Bitches Brew") == "bitches brew"


def test_identity_keeps_an_ep_marker(album: Callable[..., Path]) -> None:
    """The marker survives the peel, or an EP matches nothing.

    This key is looked up in a dictionary built from `album_title`, and
    that one keeps the marker. The two peel the same name from different
    ends -- one a folder, one a tag -- and have to land on the same
    string.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("settled", tag="\u00a9(2013) - Summer Knights (EP) [OPUS]")

    assert identity(folder) == "summer knights (ep)"


def test_identity_is_none_without_a_readable_tag(album: Callable[..., Path]) -> None:
    """Nothing to match on is a refusal, not a guess from the folder name.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Miles Davis - Kind of Blue", tag=None)

    assert identity(folder) is None


# ==================================================================================== #
#                                      DISCOVERY                                       #
# ==================================================================================== #
def test_loose_albums_keeps_a_folder_of_albums_out(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """Only a folder holding audio of its own is a candidate to move.

    Anything else holds albums, and moving it would take everything
    beneath it along.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    root: Path = tmp_path / "playlist"
    _ = album("playlist/An Album", tag="An Album")
    _ = album("playlist/Region/Artist/Their Album", tag="Their Album")

    albums, unreadable = loose_albums(root, [])

    assert albums == [root / "An Album"]
    assert unreadable == [root / "Region"]


def test_a_folder_holding_a_known_artist_is_not_reported(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """A region folder is the shelf's shape, not something passed over.

    Its artists were found by walking through it, so there is nothing to
    say about it.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    root: Path = tmp_path / "playlist"
    _ = album("playlist/Region/Artist/Their Album", tag="Their Album")

    albums, unreadable = loose_albums(root, [root / "Region" / "Artist"])

    assert albums == []
    assert unreadable == []


def test_find_homes_finds_an_artist_at_any_depth(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """The playlist is not assumed to mirror the discography's shelf.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    root: Path = tmp_path / "playlist"
    _ = album("playlist/Region/Miles Davis/An Album", tag="An Album")

    homes, strangers = find_homes(root, {"Miles Davis"})

    assert homes == {"Miles Davis": [root / "Region" / "Miles Davis"]}
    assert strangers == []


def test_find_homes_finds_one_artist_in_several_places(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """A playlist is a curation: one artist may sit wherever you filed them.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    root: Path = tmp_path / "playlist"
    _ = album("playlist/Japan/Rodrigo/One", tag="One")
    _ = album("playlist/Classical/Rodrigo/Two", tag="Two")

    found, _strangers = find_homes(root, {"Rodrigo"})

    assert sorted(found["Rodrigo"]) == sorted(
        [root / "Japan" / "Rodrigo", root / "Classical" / "Rodrigo"]
    )


def test_find_homes_does_not_look_inside_an_album(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """An album holds tracks, never an artist, so the walk stops there.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    root: Path = tmp_path / "playlist"
    folder: Path = album("playlist/An Album", tag="An Album")
    (folder / "Miles Davis").mkdir()

    homes, strangers = find_homes(root, {"Miles Davis"})

    assert homes == {}
    assert strangers == []


def test_find_homes_names_an_artist_the_roster_does_not_know(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """An artist in the playlist the discography knows nothing about.

    Nobody's to sync, but the walk goes right past them, and saying
    nothing means a whole artist sitting in the playlist that the run
    never mentions -- as though they were not there at all.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    root: Path = tmp_path / "playlist"
    _ = album("playlist/Africa/(Mali) - Toumani Diabat\u00e9/02. (1988) - Kaira", tag="Kaira")

    homes, strangers = find_homes(root, {"Miles Davis"})

    assert homes == {}
    assert strangers == [root / "Africa" / "(Mali) - Toumani Diabat\u00e9"]


def test_the_playlist_path_may_be_the_artist_folder_itself(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """A settled playlist moved somewhere permanent is pointed at directly.

    The artist folder is then the root, and its children are that
    artist's albums -- gathered from the home they sit in. Counted a
    second time as loose, every album would claim itself, contest
    itself, and settle nothing.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    root: Path = tmp_path / "Mamady Keita"
    _ = album("Mamady Keita/Keita - 1989 - Wassolon", tag="Wassolon")
    _ = album("Mamady Keita/Keita - 1992 - Nankama", tag="Nankama")

    homes, strangers = find_homes(root, {"Mamady Keita"})
    albums, unreadable = loose_albums(root, [home for found in homes.values() for home in found])

    assert homes == {"Mamady Keita": [root]}
    assert strangers == []
    assert albums == []
    assert unreadable == []


# ==================================================================================== #
#                                       NAMING                                         #
# ==================================================================================== #
def test_settled_name_takes_its_quality_from_the_converted_files(tmp_path: Path) -> None:
    """The discography's name, but never the discography's quality word.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "\u00a901. (1993) - 60 Horses in My Herd [FLAC]"
    disco.mkdir()
    converted: Path = tmp_path / "converted"
    converted.mkdir()
    _ = (converted / "01.opus").write_bytes(b"x")

    assert settled_name(disco, converted) == "\u00a901. (1993) - 60 Horses in My Herd [OPUS]"


def test_a_lossy_conversion_earns_no_quality_word(tmp_path: Path) -> None:
    """As in the discography, lossy is held but not worth announcing.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "01. (1993) - Kind of Blue [FLAC]"
    disco.mkdir()
    converted: Path = tmp_path / "converted"
    converted.mkdir()
    _ = (converted / "01.mp3").write_bytes(b"x")

    assert settled_name(disco, converted) == "01. (1993) - Kind of Blue"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "\u00a901. (1993) - 60 Horses in My Herd [OPUS]",
            "\u00a9(1993) - 60 Horses in My Herd [OPUS]",
        ),
        ("03. (1996) - Fly Fly My Sadness [OPUS]", "(1996) - Fly Fly My Sadness [OPUS]"),
        # A written-off album carries its mark like a pinned one does.
        ("\u271703. (2006) - Inner Thoughts [OPUS]", "\u2717(2006) - Inner Thoughts [OPUS]"),
        ("07. (2001) - Best Live [Live] [OPUS]", "(2001) - Best Live [Live] [OPUS]"),
    ],
)
def test_album_tag_drops_the_index_and_keeps_the_mark(name: str, expected: str) -> None:
    """A browser sorts on the index; a player sorts on the tag.

    The index would go stale in the tag the moment the discography
    renumbered, where the mark and the year are what make a player list
    favourites first and chronological after.

    Args:
        name: A settled playlist folder's name.
        expected: The Album tag it should carry.
    """
    assert album_tag(name) == expected


# ==================================================================================== #
#                                      PLANNING                                        #
# ==================================================================================== #
def test_a_matched_album_takes_the_discography_name(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """The discography decides the name; the playlist decides the place.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "\u00a901. (1993) - Kind of Blue [FLAC]"
    disco.mkdir(parents=True)
    home: Path = tmp_path / "home"
    folder: Path = album("loose/Miles Davis - Kind of Blue", tag="Kind of Blue")

    result = plan([(folder, home)], [disco])

    assert len(result.matches) == 1
    assert result.matches[0].target == home / "\u00a901. (1993) - Kind of Blue [FLAC]"


def test_folders_claiming_one_album_are_all_refused(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """Two folders for one album is not something a name can settle.

    A converter that writes one album's tags across a whole batch makes
    every folder claim the same record. Guessing which is which would
    file sixteen albums under the wrong names.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "01. (1991) - Sound of the Earth [FLAC]"
    disco.mkdir(parents=True)
    home: Path = tmp_path / "home"
    first: Path = album("loose/one", tag="Sound of the Earth")
    second: Path = album("loose/two", tag="Sound of the Earth")

    result = plan([(first, home), (second, home)], [disco])

    assert result.matches == ()
    assert result.contested == ((first, second),)


def test_a_refused_folder_is_never_moved(album: Callable[..., Path], tmp_path: Path) -> None:
    """Nothing matched means nothing written, which is the whole safety rule.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "01. (1991) - Sound of the Earth [FLAC]"
    disco.mkdir(parents=True)
    home: Path = tmp_path / "home"
    first: Path = album("loose/one", tag="Sound of the Earth")
    second: Path = album("loose/two", tag="Sound of the Earth")

    report = apply(plan([(first, home), (second, home)], [disco]))

    assert report.moved == 0
    assert first.is_dir()
    assert second.is_dir()
    assert not home.exists()


def test_an_unmatched_folder_is_reported_not_placed(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """A folder the discography does not know is left exactly where it is.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "01. (1993) - Kind of Blue [FLAC]"
    disco.mkdir(parents=True)
    folder: Path = album("loose/Something Else", tag="Something Else")

    result = plan([(folder, tmp_path / "home")], [disco])

    assert result.matches == ()
    assert result.unmatched == (folder,)


def test_an_ep_matches_the_album_it_was_converted_from(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """The two halves of the EP marker's journey have to meet.

    The discography side is keyed by `album_title` and the converted side
    by its Album tag, which is that same title as the discography wrote
    it. Strip the marker from one and not the other and every EP in the
    playlist reports as matching nothing.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "03. (2013) - Summer Knights (EP) [FLAC]"
    disco.mkdir(parents=True)
    home: Path = tmp_path / "home"
    folder: Path = album("loose/Joey - Summer Knights", tag="Summer Knights (EP)")

    result = plan([(folder, home)], [disco])

    assert len(result.matches) == 1
    assert result.matches[0].target == home / "03. (2013) - Summer Knights (EP) [FLAC]"


def test_an_ep_does_not_match_the_album_that_shares_its_name(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """Two records of one name are told apart by the marker, not confused by it.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "04. (2013) - Summer Knights [FLAC]"
    disco.mkdir(parents=True)
    folder: Path = album("loose/the ep", tag="Summer Knights (EP)")

    result = plan([(folder, tmp_path / "home")], [disco])

    assert result.matches == ()
    assert result.unmatched == (folder,)


def test_an_album_already_in_place_is_not_pending(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """A second run finds nothing to do, which is what makes this a sync.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    disco: Path = tmp_path / "disco" / "01. (1993) - Kind of Blue [FLAC]"
    disco.mkdir(parents=True)
    home: Path = tmp_path / "home"
    folder: Path = album("home/01. (1993) - Kind of Blue [FLAC]", tag="Kind of Blue")

    result = plan([(folder, home)], [disco])

    assert len(result.matches) == 1
    assert result.pending == ()


# ==================================================================================== #
#                                        COVERS                                        #
# ==================================================================================== #
def embed(track: Path, data: bytes) -> None:
    """Give a track a front cover.

    Args:
        track: The audio file to write into.
        data: The image bytes to embed.
    """
    cover = artwork.read(data)
    assert cover is not None
    metadata.write_cover(track, cover)


def test_the_art_the_tracks_carry_is_written_beside_them(album: Callable[..., Path]) -> None:
    """The phone that will not read embedded art looks for a file instead.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Kind of Blue")
    embed(folder / "01.flac", encode(600))

    result = plan_covers([folder])

    assert len(result.writes) == 1
    assert result.writes[0].target == folder / COVER_NAME
    assert result.without_artwork == ()


def test_an_album_holding_the_right_bytes_is_left_out(album: Callable[..., Path]) -> None:
    """A second run writes nothing, which is what makes this a sync.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Kind of Blue")
    embed(folder / "01.flac", encode(600))
    _ = apply_covers(plan_covers([folder]))

    assert plan_covers([folder]).writes == ()


def test_stale_bytes_are_replaced(album: Callable[..., Path]) -> None:
    """Re-arting an album upstream has to reach the playlist.

    The counterpart to leaving a settled album alone: the test above
    would pass just as well if nothing were ever written twice.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Kind of Blue")
    embed(folder / "01.flac", encode(600, seed=1))
    _ = (folder / COVER_NAME).write_bytes(encode(600, seed=2))

    assert len(plan_covers([folder]).writes) == 1


def test_an_album_whose_tracks_carry_no_art_is_named(album: Callable[..., Path]) -> None:
    """Nothing to write is worth saying: it is the one thing to go and fix.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Bare")

    result = plan_covers([folder])

    assert result.writes == ()
    assert result.without_artwork == (folder,)


def test_art_deeper_than_a_disc_is_not_the_folder_s_own(
    album: Callable[..., Path], tmp_path: Path
) -> None:
    """A folder holding albums has no artwork of its own.

    The same limit `identity` keeps, and for the same reason: a recursive
    read would take art from some track buried inside and write it out as
    though it belonged to the whole tree.

    Args:
        album: Factory building an album folder.
        tmp_path: Pytest's per-test temporary directory.
    """
    inner: Path = album("drop/Miles Davis - Kind of Blue")
    embed(inner / "01.flac", encode(600))
    drop: Path = tmp_path / "drop"

    result = plan_covers([drop])

    assert result.writes == ()
    assert result.without_artwork == (drop,)


def test_a_png_cover_is_written_as_jpeg(album: Callable[..., Path]) -> None:
    """The file is always "cover.jpg", so the bytes had better be one.

    Players read this one by extension rather than by content, so a PNG
    saved under it is a name that lies.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Kind of Blue")
    embed(folder / "01.flac", encode(600, fmt="PNG"))

    written = plan_covers([folder]).writes[0]

    assert written.target.name == COVER_NAME
    assert written.data.startswith(b"\xff\xd8\xff")


def test_applying_writes_the_file(album: Callable[..., Path]) -> None:
    """The bytes on disk are the ones the tracks were carrying.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Kind of Blue")
    art: bytes = encode(600)
    embed(folder / "01.flac", art)

    report = apply_covers(plan_covers([folder]))

    assert report.written == 1
    assert (folder / COVER_NAME).read_bytes() == art


def test_a_write_that_fails_is_reported(album: Callable[..., Path]) -> None:
    """One unwritable folder must not cost the rest their artwork.

    Args:
        album: Factory building an album folder.
    """
    folder: Path = album("Kind of Blue")
    embed(folder / "01.flac", encode(600))
    # A directory of that name refuses the write on every platform.
    (folder / COVER_NAME).mkdir()

    report = apply_covers(plan_covers([folder]))

    assert report.written == 0
    assert [path for path, _ in report.failures] == [folder / COVER_NAME]
