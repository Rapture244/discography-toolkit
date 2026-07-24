# tests/cli/commands/tags/test_cover.py
"""Tests for the `rapt tags cover` command."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf
from typer.testing import CliRunner

from discography_toolkit.cli.main import app
from discography_toolkit.core import artwork, metadata
from discography_toolkit.core.metadata import Tag

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
def image(size: int, seed: int = 0) -> bytes:
    """Build image bytes with enough detail that JPEG cannot cheat.

    Args:
        size: Width and height in pixels.
        seed: Varies the image, so two calls differ.

    Returns:
        The encoded bytes.
    """
    picture = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(picture)
    for row in range(size):
        draw.line(
            [(0, row), (size, row)],
            fill=((seed * 37 + row) % 256, (row * 3) % 256, (255 - row) % 256),
        )
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture()
def artist(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an artist folder holding given albums.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking album names and a track count, returning the
        artist folder.
    """

    def build(*albums: str, tracks: int = 1) -> Path:
        root: Path = tmp_path / "Miles Davis - [1 • 1F • 0L • 0M]"
        for name in albums:
            folder: Path = root / "FLAC" / name
            folder.mkdir(parents=True)
            for index in range(tracks):
                sf.write(
                    folder / f"{index + 1:02d}.flac",
                    np.zeros(4410, dtype="float32"),
                    44100,
                    format="FLAC",
                )
        return root

    return build


def album_of(root: Path, name: str) -> Path:
    """Return one album folder inside an artist folder.

    Args:
        root: The artist folder.
        name: The album folder's name.

    Returns:
        The album folder.
    """
    return root / "FLAC" / name


def counted(output: str, label: str) -> int:
    """Read one summary-box row's figure out of the rendered output.

    Args:
        output: Everything the command printed.
        label: The row's label, e.g. `"No cover"`.

    Returns:
        The count that row shows.

    Raises:
        AssertionError: If no such row was printed.
    """
    line: str | None = next(
        (line for line in output.splitlines() if label in line and ":" in line), None
    )
    assert line is not None, f"no {label!r} row in the summary"
    return int(line.split(":")[1].split()[0])


def embed(track: Path, data: bytes) -> None:
    """Give a track a front cover.

    Args:
        track: The audio file to write into.
        data: The image bytes to embed.
    """
    cover = artwork.read(data)
    assert cover is not None
    metadata.write_cover(track, cover)


# ==================================================================================== #
#                                    WHAT IT WRITES                                    #
# ==================================================================================== #
def test_art_in_the_tags_is_spilled_onto_disk(artist: Callable[..., Path]) -> None:
    """An album carrying art in its tags gains the file a file manager reads.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    art: bytes = image(900)
    embed(album / "01.flac", art)

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    assert result.exit_code == 0
    assert (album / "cover.jpg").read_bytes() == art


def test_an_existing_name_is_renamed_rather_than_duplicated(
    artist: Callable[..., Path],
) -> None:
    """An album already using "folder.jpg" gains no twin, it just moves.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("02. (1970) - Bitches Brew [FLAC]")
    album: Path = album_of(root, "02. (1970) - Bitches Brew [FLAC]")
    art: bytes = image(900)
    _ = (album / "folder.jpg").write_bytes(art)

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    assert result.exit_code == 0
    assert not (album / "folder.jpg").exists()
    assert (album / "cover.jpg").read_bytes() == art


def test_duplicates_are_removed(artist: Callable[..., Path]) -> None:
    """One artwork under three names leaves the one a player reads.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("03. (1972) - On the Corner [FLAC]")
    album: Path = album_of(root, "03. (1972) - On the Corner [FLAC]")
    art: bytes = image(700)
    for stem in ("cover", "folder", "albumart"):
        _ = (album / f"{stem}.jpg").write_bytes(art)

    _ = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    assert (album / "cover.jpg").exists()
    assert not (album / "folder.jpg").exists()
    assert not (album / "albumart.jpg").exists()


def test_the_cover_is_embedded_into_every_track(artist: Callable[..., Path]) -> None:
    """A loose file reaches the tracks, capped on the way in.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("04. (1986) - Tutu [FLAC]", tracks=3)
    album: Path = album_of(root, "04. (1986) - Tutu [FLAC]")
    _ = (album / "cover.jpg").write_bytes(image(2400))

    _ = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    stored = metadata.read_cover(album / "03.flac")
    assert stored is not None
    assert artwork.longest_edge(stored) == artwork.EMBED_MAX_PIXELS


def test_the_loose_file_keeps_its_resolution(artist: Callable[..., Path]) -> None:
    """Only the embedded copy is capped; the master on disk is left alone.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("05. (1957) - Birth of the Cool [FLAC]")
    album: Path = album_of(root, "05. (1957) - Birth of the Cool [FLAC]")
    art: bytes = image(2400)
    _ = (album / "cover.jpg").write_bytes(art)

    _ = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    assert (album / "cover.jpg").read_bytes() == art


# ==================================================================================== #
#                                    WHAT IT REPORTS                                   #
# ==================================================================================== #
def test_each_kind_of_write_names_the_albums_it_touches(
    artist: Callable[..., Path],
) -> None:
    """The listing is grouped by write, so a reader can act on it.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Written [FLAC]", "02. (1970) - Renamed [FLAC]")
    art: bytes = image(900)
    embed(album_of(root, "01. (1959) - Written [FLAC]") / "01.flac", art)
    renamed: Path = album_of(root, "02. (1970) - Renamed [FLAC]")
    _ = (renamed / "folder.jpg").write_bytes(art)
    _ = (renamed / "front.jpg").write_bytes(art)

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert "file  'cover.jpg'" in result.output
    assert "'01. (1959) - Written [FLAC]'" in result.output
    assert "rename" in result.output
    assert "'folder.jpg'" in result.output
    assert "delete" in result.output
    assert "'front.jpg'" in result.output


def test_tracks_to_embed_are_listed_under_their_album(
    artist: Callable[..., Path],
) -> None:
    """Grouped per album, unlike the cover file: these are different files.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]", tracks=2)
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    _ = (album / "cover.jpg").write_bytes(image(600))

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert "tag   (2 track(s) in 1 album(s))" in result.output
    assert "'01.flac'" in result.output
    assert "'02.flac'" in result.output


def test_an_album_with_no_artwork_anywhere_is_listed_apart(
    artist: Callable[..., Path],
) -> None:
    """Nothing to settle is worth naming: it is the one thing to go and fix.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Has Art [FLAC]", "02. (1970) - Bare [FLAC]")
    embed(album_of(root, "01. (1959) - Has Art [FLAC]") / "01.flac", image(600))

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert "1 album(s) with no cover:" in result.output
    assert "'02. (1970) - Bare [FLAC]'" in result.output
    assert "'01. (1959) - Has Art [FLAC]'" not in result.output.split("with no cover:")[1]


def test_an_empty_placeholder_is_counted_but_not_listed(
    artist: Callable[..., Path],
) -> None:
    """A folder with no tracks has nothing to cover, so it is not a fault.

    Where a third of a discography is placeholders, listing them buries
    the albums that genuinely lack art.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Real [FLAC]")
    embed(album_of(root, "01. (1959) - Real [FLAC]") / "01.flac", image(600))
    (root / "FLAC" / "02. (1970) - Placeholder [FLAC]").mkdir()

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert "Empty" in result.output
    assert "with no cover" not in result.output
    assert "'02. (1970) - Placeholder [FLAC]'" not in result.output


def test_a_bare_album_is_counted_apart_from_a_placeholder(
    artist: Callable[..., Path],
) -> None:
    """Folding the two together would put a placeholder in the failure column.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Real [FLAC]", "02. (1970) - Bare [FLAC]")
    embed(album_of(root, "01. (1959) - Real [FLAC]") / "01.flac", image(600))
    # Deliberately not one of each: equal counts would hide the two rows
    # being swapped for one another.
    for name in ("03. (1972) - Empty [FLAC]", "04. (1986) - Also Empty [FLAC]"):
        (root / "FLAC" / name).mkdir()

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert counted(result.output, "Total") == 4
    assert counted(result.output, "No cover") == 1
    assert counted(result.output, "Empty") == 2
    # The scan walks albums; a bar saying "files" would be counting
    # something it never looked at.
    assert "(4/4 albums)" in result.output


def test_the_box_counts_the_work_it_is_about_to_do(artist: Callable[..., Path]) -> None:
    """The figures shown before confirming are the run itself.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]", tracks=2)
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    art: bytes = image(600)
    _ = (album / "folder.jpg").write_bytes(art)
    _ = (album / "front.jpg").write_bytes(art)
    embed(album / "01.flac", art)

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert counted(result.output, "Cover files") == 1
    assert counted(result.output, "Duplicates") == 1
    assert counted(result.output, "Tracks to embed") == 1
    assert counted(result.output, "Tracks settled") == 1


def test_where_each_cover_came_from_is_counted(artist: Callable[..., Path]) -> None:
    """Art recovered from the tags is not art that was already on disk.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Tagged [FLAC]", "02. (1970) - On Disk [FLAC]")
    embed(album_of(root, "01. (1959) - Tagged [FLAC]") / "01.flac", image(600, seed=1))
    _ = (album_of(root, "02. (1970) - On Disk [FLAC]") / "cover.jpg").write_bytes(
        image(600, seed=2)
    )

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert counted(result.output, "From tags") == 1
    assert counted(result.output, "From disk") == 1


def test_a_format_carrying_no_cover_is_reported_not_written(
    artist: Callable[..., Path],
) -> None:
    """APEv2 stores pictures too loosely to write blind, so it is left alone.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Mixed [FLAC]")
    album: Path = album_of(root, "01. (1959) - Mixed [FLAC]")
    _ = (album / "cover.jpg").write_bytes(image(600))
    _ = (album / "02.wv").write_bytes(b"not really wavpack")

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert "1 file(s) in formats covers are not written into" in result.output


def test_the_prompt_names_the_work_before_asking(artist: Callable[..., Path]) -> None:
    """Nobody should have to guess what they are agreeing to.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Tagged [FLAC]", tracks=1)
    embed(album_of(root, "01. (1959) - Tagged [FLAC]") / "01.flac", image(600, seed=1))
    loose: Path = root / "FLAC" / "02. (1970) - Loose [FLAC]"
    loose.mkdir()
    for index in (1, 2):
        sf.write(loose / f"{index:02d}.flac", np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    art: bytes = image(600, seed=2)
    _ = (loose / "folder.jpg").write_bytes(art)
    _ = (loose / "front.jpg").write_bytes(art)

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="n\n")

    assert "Write 1 cover file(s)" in result.output
    assert "rename 1" in result.output
    assert "delete 1 duplicate(s)" in result.output
    assert "embed into 2 track(s)" in result.output


def test_the_closing_line_counts_what_was_done(artist: Callable[..., Path]) -> None:
    """A run says what it achieved, not merely that it finished.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    art: bytes = image(600)
    _ = (album / "folder.jpg").write_bytes(art)
    _ = (album / "front.jpg").write_bytes(art)

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    assert "1 cover file(s) in place" in result.output
    assert "1 duplicate(s) removed" in result.output
    assert "1 track(s) embedded" in result.output
    # A rename, a delete and an embed: the bar is sized from the plan,
    # so it has to end full rather than overrun.
    assert "(3/3 operations)" in result.output


# ==================================================================================== #
#                                    WHAT IT REFUSES                                   #
# ==================================================================================== #
def test_refuses_a_path_with_no_album(tmp_path: Path) -> None:
    """Albums are recognized through their artist, and there is none here.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    loose: Path = tmp_path / "Unsorted"
    loose.mkdir()
    sf.write(loose / "01.flac", np.zeros(4410, dtype="float32"), 44100, format="FLAC")

    result = runner.invoke(app, ["tags", "cover", "-p", str(tmp_path)])

    assert result.exit_code == 1
    assert "No album folder found" in result.output


def test_a_folder_without_audio_exits_cleanly(tmp_path: Path) -> None:
    """Nothing to do is not an error.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    (tmp_path / "Miles Davis - [1 on 1]" / "01. (1959) - X").mkdir(parents=True)

    result = runner.invoke(app, ["tags", "cover", "-p", str(tmp_path)])

    assert result.exit_code == 0
    assert "No audio files" in result.output


# ==================================================================================== #
#                                     NOT WRITING                                      #
# ==================================================================================== #
def test_a_dry_run_changes_nothing(artist: Callable[..., Path]) -> None:
    """A dry run reports and writes nothing.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    embed(album / "01.flac", image(600))

    result = runner.invoke(app, ["tags", "cover", "-p", str(root), "--dry-run"])

    assert "Dry run" in result.output
    assert not (album / "cover.jpg").exists()


def test_declining_the_prompt_changes_nothing(artist: Callable[..., Path]) -> None:
    """Answering no stops before any write.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    embed(album / "01.flac", image(600))

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="n\n")

    assert "Aborted" in result.output
    assert not (album / "cover.jpg").exists()


def test_a_second_run_finds_nothing(artist: Callable[..., Path]) -> None:
    """Settling twice is settling once.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]", tracks=2)
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    _ = (album / "cover.jpg").write_bytes(image(2400))
    _ = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    result = runner.invoke(app, ["tags", "cover", "-p", str(root)])

    assert "Nothing to do" in result.output


def test_the_album_tag_is_untouched(artist: Callable[..., Path]) -> None:
    """This command writes artwork and nothing else.

    Args:
        artist: Factory building an artist folder.
    """
    root: Path = artist("01. (1959) - Kind of Blue [FLAC]")
    album: Path = album_of(root, "01. (1959) - Kind of Blue [FLAC]")
    _ = (album / "cover.jpg").write_bytes(image(600))
    metadata.write(album / "01.flac", {Tag.ALBUM: "Untouched"})

    _ = runner.invoke(app, ["tags", "cover", "-p", str(root)], input="y\n")

    assert metadata.read(album / "01.flac", [Tag.ALBUM])[Tag.ALBUM] == "Untouched"
