# tests/operations/test_covers.py
"""Tests for settling one front cover per album.

Run against real files: the step's whole job is what it leaves on disk
and in the tags, so a mocked filesystem would only test the mock. Albums
are built from silent FLACs, the one format the test dependencies can
generate.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

from discography_toolkit.core import artwork, metadata
from discography_toolkit.operations import covers

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from discography_toolkit.core.artwork import Cover


# ==================================================================================== #
#                                       HELPERS                                        #
# ==================================================================================== #
def encode(size: int, seed: int = 0, fmt: str = "JPEG") -> bytes:
    """Build image bytes with enough detail that JPEG cannot cheat.

    A flat colour compresses to almost nothing at any size, which would
    hide what the embedding cap does.

    Args:
        size: Width and height in pixels.
        seed: Varies the image, so two calls differ.
        fmt: Pillow format name.

    Returns:
        The encoded bytes.
    """
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    for row in range(size):
        draw.line(
            [(0, row), (size, row)],
            fill=((seed * 37 + row) % 256, (row * 3) % 256, (255 - row) % 256),
        )
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def cover_of(data: bytes) -> Cover:
    """Wrap image bytes, asserting they are readable as artwork.

    Args:
        data: Encoded image bytes.

    Returns:
        The cover they hold.
    """
    cover = artwork.read(data)
    assert cover is not None
    return cover


def settlement_of(album: Path) -> covers.Settlement:
    """Plan one album and return the settlement it must have.

    Args:
        album: The album folder to plan.

    Returns:
        Its settlement.
    """
    settlement = covers.plan([album]).albums[0].settlement
    assert settlement is not None
    return settlement


@pytest.fixture()
def make_album(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an album folder of silent FLACs.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking a folder name, a track count and the cover
        each track starts with, returning the album folder.
    """

    def build(name: str, tracks: int = 2, embedded: list[bytes | None] | None = None) -> Path:
        album: Path = tmp_path / name
        album.mkdir(parents=True, exist_ok=True)
        for index in range(tracks):
            path: Path = album / f"{index + 1:02d} - Track.flac"
            sf.write(path, np.zeros(4410, dtype="float32"), 44100, format="FLAC")
            data: bytes | None = embedded[index] if embedded else None
            if data is not None:
                metadata.write_cover(path, cover_of(data))
        return album

    return build


# ==================================================================================== #
#                                       CHOOSING                                       #
# ==================================================================================== #
def test_the_cover_the_tracks_agree_on_wins(make_album: Callable[..., Path]) -> None:
    """Two tracks carrying the same art settle on it.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Kind Of Blue", embedded=[art, art])

    settlement = settlement_of(album)

    assert settlement.source == "tags"
    assert settlement.cover.data == art


def test_a_larger_loose_file_beats_the_tags(make_album: Callable[..., Path]) -> None:
    """A full-resolution scan on disk outranks the capped copy in the tags.

    This is the second-run case: embedding caps the image, so trusting
    the tags would overwrite the master with its own thumbnail.

    Args:
        make_album: Factory building an album folder.
    """
    small: bytes = encode(400, seed=1)
    album: Path = make_album("Nefertiti", embedded=[small, small])
    large: bytes = encode(2000, seed=2)
    _ = (album / "cover.jpg").write_bytes(large)

    settlement = settlement_of(album)

    assert settlement.source == "disk"
    assert settlement.cover.data == large


def test_a_smaller_loose_file_does_not_beat_the_tags(make_album: Callable[..., Path]) -> None:
    """A thumbnail beside the album does not displace what the tracks hold.

    Args:
        make_album: Factory building an album folder.
    """
    large: bytes = encode(1000, seed=1)
    album: Path = make_album("Milestones", embedded=[large, large])
    _ = (album / "folder.jpg").write_bytes(encode(200, seed=2))

    settlement = settlement_of(album)

    assert settlement.source == "tags"
    assert settlement.cover.data == large


def test_an_album_with_no_artwork_anywhere_has_no_settlement(
    make_album: Callable[..., Path],
) -> None:
    """Nothing to settle, but the tracks are still counted.

    The count is what tells an album with music and no art apart from an
    empty placeholder folder.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("Bitches Brew", tracks=3)

    result = covers.plan([album])

    assert result.albums[0].settlement is None
    assert result.albums[0].tracks == 3
    assert result.without_artwork == result.albums
    assert result.empty == ()


def test_a_folder_holding_no_tracks_is_a_placeholder(tmp_path: Path) -> None:
    """An empty folder is reported apart from albums that lost their art.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    placeholder: Path = tmp_path / "Placeholder"
    placeholder.mkdir()

    result = covers.plan([placeholder])

    assert result.albums[0].tracks == 0
    assert result.empty == result.albums
    assert result.without_artwork == ()


def test_a_folder_holding_art_but_no_tracks_is_left_alone(tmp_path: Path) -> None:
    """No music, no album: a stray image is not tidied on the way past.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    placeholder: Path = tmp_path / "Placeholder"
    placeholder.mkdir()
    _ = (placeholder / "folder.jpg").write_bytes(encode(400))

    result = covers.plan([placeholder])

    assert result.albums[0].settlement is None
    assert result.changes == 0


def test_a_track_whose_format_carries_no_cover_is_counted(
    make_album: Callable[..., Path],
) -> None:
    """APEv2 is reported as unsupported rather than written blind.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Unsupported", embedded=[art, art])
    _ = (album / "03 - Track.wv").write_bytes(b"not really wavpack")

    result = covers.plan([album])

    assert result.albums[0].tracks == 3
    assert result.albums[0].unsupported == 1
    assert settlement_of(album).embed == ()


def test_an_unreadable_image_is_not_a_candidate(make_album: Callable[..., Path]) -> None:
    """A file named like a cover that is not one is ignored, not chosen.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(300)
    album: Path = make_album("Sketches Of Spain", embedded=[art, art])
    _ = (album / "front.jpg").write_bytes(b"not an image")

    settlement = settlement_of(album)

    assert settlement.cover.data == art
    assert album / "front.jpg" not in settlement.delete


def test_an_unreadable_track_casts_no_vote(make_album: Callable[..., Path]) -> None:
    """A truncated rip does not abandon the album it sits in.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Truncated", embedded=[art, art])
    _ = (album / "03 - Track.flac").write_bytes(b"not audio")

    settlement = settlement_of(album)

    assert settlement.cover.data == art
    assert settlement.embed == (album / "03 - Track.flac",)


# ==================================================================================== #
#                                   SETTLING THE FILE                                  #
# ==================================================================================== #
def test_the_loose_file_is_written_when_the_album_has_none(
    make_album: Callable[..., Path],
) -> None:
    """Art living only in the tags is spilled onto disk.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Agharta", embedded=[art, art])

    settlement = settlement_of(album)

    assert settlement.write
    assert settlement.target == album / "cover.jpg"
    assert settlement.rename_from is None


def test_the_extension_follows_the_bytes(make_album: Callable[..., Path]) -> None:
    """A PNG is settled as "cover.png", so the name does not lie.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600, fmt="PNG")
    album: Path = make_album("Pangaea", embedded=[art, art])

    assert settlement_of(album).target == album / "cover.png"


def test_an_existing_name_is_renamed_rather_than_copied(
    make_album: Callable[..., Path],
) -> None:
    """An album already using "folder.jpg" gains no twin, it just moves.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("On The Corner")
    art: bytes = encode(800)
    _ = (album / "folder.jpg").write_bytes(art)

    settlement = settlement_of(album)

    assert settlement.rename_from == album / "folder.jpg"
    assert not settlement.write
    assert settlement.delete == ()


def test_a_correct_loose_file_is_left_alone(make_album: Callable[..., Path]) -> None:
    """Nothing is rewritten when the canonical file already holds the art.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("In A Silent Way", embedded=[art, art])
    _ = (album / "cover.jpg").write_bytes(art)

    settlement = settlement_of(album)

    assert not settlement.write
    assert settlement.rename_from is None
    assert settlement.delete == ()


def test_the_canonical_name_is_overwritten_when_it_holds_the_wrong_art(
    make_album: Callable[..., Path],
) -> None:
    """A "cover.jpg" that lost the vote is replaced, not moved onto.

    Args:
        make_album: Factory building an album folder.
    """
    winner: bytes = encode(2000, seed=1)
    album: Path = make_album("Jack Johnson")
    _ = (album / "cover.jpg").write_bytes(encode(300, seed=2))
    _ = (album / "front.jpg").write_bytes(winner)

    settlement = settlement_of(album)

    assert settlement.write
    assert settlement.rename_from is None
    assert settlement.delete == (album / "front.jpg",)


def test_duplicates_under_other_names_are_deleted(make_album: Callable[..., Path]) -> None:
    """One artwork under three names leaves only the one a player reads.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(700)
    album: Path = make_album("Filles De Kilimanjaro", embedded=[art, art])
    for stem in ("cover", "folder", "albumart"):
        _ = (album / f"{stem}.jpg").write_bytes(art)

    settlement = settlement_of(album)

    assert set(settlement.delete) == {album / "folder.jpg", album / "albumart.jpg"}


def test_the_file_renamed_into_place_is_not_also_deleted(
    make_album: Callable[..., Path],
) -> None:
    """The move is the cleanup: deleting it too would undo the settlement.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(500)
    album: Path = make_album("Miles Smiles")
    _ = (album / "folder.jpg").write_bytes(art)
    _ = (album / "front.jpg").write_bytes(art)

    settlement = settlement_of(album)

    assert settlement.rename_from == album / "folder.jpg"
    assert settlement.delete == (album / "front.jpg",)


# ==================================================================================== #
#                                      EMBEDDING                                       #
# ==================================================================================== #
def test_tracks_without_the_cover_are_queued(make_album: Callable[..., Path]) -> None:
    """A track carrying nothing needs the album's art.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Tutu", tracks=3, embedded=[art, art, None])

    settlement = settlement_of(album)

    assert settlement.embed == (album / "03 - Track.flac",)
    assert settlement.correct == 2


def test_a_track_carrying_the_capped_copy_is_left_alone(
    make_album: Callable[..., Path],
) -> None:
    """Comparison is against what would be written, not the full-size master.

    Otherwise every run would rewrite every track: the file on disk is
    full resolution and the tags hold the capped copy, so the two never
    match.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(2400)
    payload: Cover = artwork.for_embedding(cover_of(art))
    assert payload.data != art

    album: Path = make_album("Doo-Bop", embedded=[payload.data, payload.data])
    _ = (album / "cover.jpg").write_bytes(art)

    settlement = settlement_of(album)

    assert settlement.embed == ()
    assert settlement.correct == 2


def test_a_track_carrying_different_art_is_queued(make_album: Callable[..., Path]) -> None:
    """The odd one out is brought into line with the rest.

    Args:
        make_album: Factory building an album folder.
    """
    front: bytes = encode(600, seed=1)
    stray: bytes = encode(600, seed=2)
    album: Path = make_album("Nefertiti", tracks=3, embedded=[front, front, stray])

    assert settlement_of(album).embed == (album / "03 - Track.flac",)


# ==================================================================================== #
#                                    THE WHOLE PLAN                                    #
# ==================================================================================== #
def test_albums_are_planned_in_the_order_given(make_album: Callable[..., Path]) -> None:
    """A report reads in the order the caller discovered things.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(400)
    first: Path = make_album("A", embedded=[art, art])
    second: Path = make_album("B", embedded=[art, art])

    result = covers.plan([second, first])

    assert [album.album for album in result.albums] == [second, first]
    assert result.total == 2


def test_progress_is_reported_for_every_album(make_album: Callable[..., Path]) -> None:
    """The caller drives a display without this module knowing one exists.

    Args:
        make_album: Factory building an album folder.
    """
    seen: list[Path] = []
    albums: list[Path] = [make_album("A"), make_album("B")]

    _ = covers.plan(albums, on_progress=seen.append)

    assert seen == albums


def test_the_counts_add_up(make_album: Callable[..., Path]) -> None:
    """The summary shown before confirming is the work itself.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    written: Path = make_album("Written", embedded=[art, None])
    renamed: Path = make_album("Renamed", embedded=[art, art])
    _ = (renamed / "folder.jpg").write_bytes(art)
    _ = (renamed / "front.jpg").write_bytes(art)

    result = covers.plan([written, renamed])

    assert result.writes == 1
    assert result.renames == 1
    assert result.deletions == 1
    assert result.embeds == 1
    assert result.changes == 4
    assert result.pending == result.albums


def test_an_album_needing_nothing_is_not_pending(make_album: Callable[..., Path]) -> None:
    """A settled album is not work.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    payload: Cover = artwork.for_embedding(cover_of(art))
    album: Path = make_album("Settled", embedded=[payload.data, payload.data])
    _ = (album / "cover.jpg").write_bytes(art)

    result = covers.plan([album])

    assert result.pending == ()
    assert result.changes == 0


def test_nothing_is_written_by_planning(make_album: Callable[..., Path]) -> None:
    """Planning is safe to run against a discography just to read the answer.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Untouched", embedded=[art, None])

    _ = covers.plan([album])

    assert not (album / "cover.jpg").exists()
    assert metadata.read_cover(album / "02 - Track.flac") is None


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def test_applying_writes_the_loose_file(make_album: Callable[..., Path]) -> None:
    """The file on disk keeps the full-resolution bytes, uncapped.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(2400)
    album: Path = make_album("Agharta", embedded=[art, art])

    report = covers.apply(covers.plan([album]))

    assert report.written == 1
    assert (album / "cover.jpg").read_bytes() == art


def test_applying_renames_rather_than_copying(make_album: Callable[..., Path]) -> None:
    """The album ends with one image, under the name a player reads.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("On The Corner")
    art: bytes = encode(800)
    _ = (album / "folder.jpg").write_bytes(art)

    report = covers.apply(covers.plan([album]))

    assert report.renamed == 1
    assert not (album / "folder.jpg").exists()
    assert (album / "cover.jpg").read_bytes() == art


def test_applying_removes_the_duplicates(make_album: Callable[..., Path]) -> None:
    """Names nothing reads are cleared once the canonical file is in place.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(700)
    album: Path = make_album("Filles De Kilimanjaro", embedded=[art, art])
    for stem in ("cover", "folder", "albumart"):
        _ = (album / f"{stem}.jpg").write_bytes(art)

    report = covers.apply(covers.plan([album]))

    assert report.deleted == 2
    assert not (album / "folder.jpg").exists()
    assert (album / "cover.jpg").exists()


def test_applying_embeds_the_capped_copy(make_album: Callable[..., Path]) -> None:
    """Each track gets the small copy, so the art is not paid for per file.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(2400)
    album: Path = make_album("Tutu", tracks=2)
    _ = (album / "cover.jpg").write_bytes(art)

    report = covers.apply(covers.plan([album]))

    assert report.embedded == 2
    stored = metadata.read_cover(album / "01 - Track.flac")
    assert stored is not None
    assert artwork.longest_edge(stored) == artwork.EMBED_MAX_PIXELS


def test_applying_twice_changes_nothing_the_second_time(
    make_album: Callable[..., Path],
) -> None:
    """The run is idempotent, which is what makes it safe to repeat.

    The full-resolution master must survive the round trip: a second run
    reading the capped copy back would overwrite it.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(2400)
    album: Path = make_album("Pangaea", tracks=2)
    _ = (album / "cover.jpg").write_bytes(art)

    _ = covers.apply(covers.plan([album]))
    second = covers.plan([album])

    assert second.changes == 0
    assert (album / "cover.jpg").read_bytes() == art


def test_progress_is_reported_for_every_operation(make_album: Callable[..., Path]) -> None:
    """Every unit of work announces itself, so a bar sized from the plan fills.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Milestones", embedded=[art, None])
    seen: list[Path] = []

    cover_plan = covers.plan([album])
    _ = covers.apply(cover_plan, on_progress=seen.append)

    assert len(seen) == cover_plan.changes


def test_a_failure_is_collected_and_the_run_continues(
    make_album: Callable[..., Path],
) -> None:
    """One unwritable file must not abandon the rest of the discography.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    broken: Path = make_album("Broken", embedded=[art, None])
    intact: Path = make_album("Intact", embedded=[art, None])
    # An extension without the audio behind it: the shape a truncated
    # rip arrives in, and one mutagen refuses to write.
    _ = (broken / "02 - Track.flac").write_bytes(b"not audio")

    report = covers.apply(covers.plan([broken, intact]))

    assert report.embedded == 1
    assert len(report.failures) == 1
    assert report.failures[0][0] == broken / "02 - Track.flac"
    assert (intact / "cover.jpg").exists()


def test_a_cover_file_that_cannot_be_written_is_reported(
    make_album: Callable[..., Path],
) -> None:
    """The album keeps its tags, and the run says what it could not do.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(600)
    album: Path = make_album("Blocked", embedded=[art, art])
    # The canonical name, taken by something bytes cannot be written to.
    (album / "cover.jpg").mkdir()

    report = covers.apply(covers.plan([album]))

    assert report.written == 0
    assert report.failures == ((album / "cover.jpg", report.failures[0][1]),)
    assert (album / "cover.jpg").is_dir()


def test_a_duplicate_that_vanished_between_plan_and_apply_is_reported(
    make_album: Callable[..., Path],
) -> None:
    """The disk can change under a plan, and one album must not stop the run.

    Args:
        make_album: Factory building an album folder.
    """
    art: bytes = encode(900)
    album: Path = make_album("Shifted", embedded=[art, art])
    _ = (album / "cover.jpg").write_bytes(art)
    _ = (album / "folder.jpg").write_bytes(encode(300))

    cover_plan = covers.plan([album])
    (album / "folder.jpg").unlink()
    (album / "folder.jpg").mkdir()
    report = covers.apply(cover_plan)

    assert report.deleted == 0
    assert len(report.failures) == 1
    assert (album / "cover.jpg").read_bytes() == art


def test_a_duplicate_outlives_a_failed_settle(make_album: Callable[..., Path]) -> None:
    """An album that cannot get its cover file keeps the copy it has.

    Deleting the duplicates after a failed write would leave the album
    with no artwork at all, which is worse than leaving it untidy.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("Obstructed")
    _ = (album / "folder.jpg").write_bytes(encode(300, seed=1))
    _ = (album / "front.jpg").write_bytes(encode(900, seed=2))
    # The canonical name, taken by something that cannot be written over.
    (album / "cover.jpg").mkdir()

    report = covers.apply(covers.plan([album]))

    assert report.deleted == 0
    assert (album / "folder.jpg").exists()
    assert len(report.failures) == 2
