# tests/core/test_declarations.py
"""Tests for what a folder declares about the music beneath it.

The rules themselves, at the level two commands share them. `tags genre`
and `list genres` both resolve through this module and must agree, so
proving the walk here is what keeps their two test files from each
half-proving it through a runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.core.declarations import (
    SIDECAR_NAME,
    Declaration,
    UnusableDeclarationError,
    nearest,
    resolve,
)

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def declare(folder: Path, value: str) -> Path:
    """Write a declaration into a folder, creating it if needed.

    Args:
        folder: The folder to declare for.
        value: What it should declare.

    Returns:
        The file written.
    """
    folder.mkdir(parents=True, exist_ok=True)
    sidecar: Path = folder / SIDECAR_NAME
    _ = sidecar.write_text(f"{value}\n", encoding="utf-8", newline="\n")
    return sidecar


# ==================================================================================== #
#                                       NEAREST                                        #
# ==================================================================================== #
def test_a_folders_own_declaration_is_found(tmp_path: Path) -> None:
    """The search starts where the tracks are, not above it.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    sidecar: Path = declare(tmp_path, "Jazz")

    assert nearest(tmp_path, tmp_path) == Declaration(genre="Jazz", source=sidecar)


def test_a_declaration_above_reaches_down(tmp_path: Path) -> None:
    """One file answers for everything beneath it, however deep.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "Artist" / "FLAC" / "01. (1959) - Album [FLAC]"
    album.mkdir(parents=True)
    sidecar: Path = declare(tmp_path, "Jazz")

    found: Declaration | None = nearest(album, tmp_path)

    assert found is not None
    assert found.genre == "Jazz"
    assert found.source == sidecar


def test_the_nearest_declaration_wins(tmp_path: Path) -> None:
    """Precedence is distance and nothing else -- no ranking, no rules.

    An album's own file beats its artist's, and the artist's beats the
    shelf's, purely because the walk stops at the first one it meets.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Artist"
    album: Path = artist / "01. (1959) - Album"
    _ = declare(tmp_path, "Shelf")
    _ = declare(artist, "Artist")
    closest: Path = declare(album, "Album")

    assert nearest(album, tmp_path) == Declaration(genre="Album", source=closest)


def test_a_container_between_the_two_is_walked_through(tmp_path: Path) -> None:
    """The FLAC container is bookkeeping, so it does not break the reach.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Artist"
    album: Path = artist / "FLAC" / "01. (1959) - Album [FLAC]"
    album.mkdir(parents=True)
    _ = declare(artist, "Jazz")

    found: Declaration | None = nearest(album, tmp_path)

    assert found is not None
    assert found.genre == "Jazz"


def test_the_search_stops_at_the_ceiling(tmp_path: Path) -> None:
    """A declaration above the path given is outside what was asked for.

    Scope is scope: pointed at one artist, a run must not inherit the
    shelf's answer from a folder it was never told about.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    artist: Path = tmp_path / "Artist"
    album: Path = artist / "01. (1959) - Album"
    album.mkdir(parents=True)
    _ = declare(tmp_path, "Shelf")

    assert nearest(album, artist) is None


def test_the_ceiling_itself_is_looked_in(tmp_path: Path) -> None:
    """Inclusive, or a shelf could never declare for its own tracks.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "01. (1959) - Album"
    album.mkdir()
    _ = declare(tmp_path, "Jazz")

    found: Declaration | None = nearest(album, tmp_path)

    assert found is not None
    assert found.genre == "Jazz"


def test_no_declaration_anywhere_is_none(tmp_path: Path) -> None:
    """Absent, not empty: a folder no file reaches has no answer at all.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "Artist" / "01. (1959) - Album"
    album.mkdir(parents=True)

    assert nearest(album, tmp_path) is None


# ==================================================================================== #
#                                       RESOLVE                                        #
# ==================================================================================== #
def test_resolve_keys_on_the_folder_not_the_track(tmp_path: Path) -> None:
    """An album's twenty tracks share one parent, and so one lookup.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    album: Path = tmp_path / "01. (1959) - Album"
    album.mkdir()
    _ = declare(tmp_path, "Jazz")
    tracks: list[Path] = [album / "01.flac", album / "02.flac", album / "03.flac"]

    found: dict[Path, Declaration] = resolve(tracks, tmp_path)

    assert list(found) == [album]


def test_resolve_omits_a_folder_nothing_reaches(tmp_path: Path) -> None:
    """Absent rather than present and empty, so `.get` says which is which.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    declared: Path = tmp_path / "Declared"
    bare: Path = tmp_path / "Bare"
    bare.mkdir()
    _ = declare(declared, "Jazz")

    found: dict[Path, Declaration] = resolve([declared / "01.flac", bare / "01.flac"], tmp_path)

    assert declared in found
    assert bare not in found


def test_resolve_gives_each_folder_the_one_that_reaches_it(tmp_path: Path) -> None:
    """Two folders under one shelf can hold two different answers.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    first: Path = tmp_path / "Alpha"
    second: Path = tmp_path / "Bravo"
    _ = declare(tmp_path, "Shelf")
    _ = declare(first, "Bebop")
    second.mkdir()

    found: dict[Path, Declaration] = resolve([first / "01.flac", second / "01.flac"], tmp_path)

    assert found[first].genre == "Bebop"
    assert found[second].genre == "Shelf"


# ==================================================================================== #
#                                        READING                                       #
# ==================================================================================== #
def test_a_compound_genre_survives_verbatim(tmp_path: Path) -> None:
    """The separator is punctuation to be preserved, not parsed.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    _ = declare(tmp_path, "Jazz;Jazz Fusion")

    found: Declaration | None = nearest(tmp_path, tmp_path)

    assert found is not None
    assert found.genre == "Jazz;Jazz Fusion"


@pytest.mark.parametrize(
    "contents",
    [
        "Jazz\n",  # the final newline `.editorconfig` asks for
        "Jazz",  # none at all
        "  Jazz  \n",  # editor-trimmed or hand-typed spacing
        "\n\nJazz\n\n",  # blank lines around it
    ],
)
def test_surrounding_whitespace_is_not_part_of_the_genre(tmp_path: Path, contents: str) -> None:
    """Stripping is required, not tidiness: the final newline is guaranteed.

    `.editorconfig` sets `insert_final_newline`, so an editor honouring
    it appends one to every declaration written by hand.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        contents: What the file holds.
    """
    _ = (tmp_path / SIDECAR_NAME).write_text(contents, encoding="utf-8", newline="\n")

    found: Declaration | None = nearest(tmp_path, tmp_path)

    assert found is not None
    assert found.genre == "Jazz"


# ==================================================================================== #
#                                       REFUSALS                                       #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("contents", "complaint"),
    [
        ("", "is empty"),
        ("   \n", "is empty"),
        ("\n\n", "is empty"),
        ("Jazz\nRock\n", "more than one line"),
        ("Jazz\n\nRock", "more than one line"),
    ],
)
def test_a_file_that_is_not_one_genre_is_refused(
    tmp_path: Path, contents: str, complaint: str
) -> None:
    """Neither nothing nor two things is a genre, and neither is guessed at.

    Guessing would write the guess into every track beneath the folder,
    so each is refused by name instead.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        contents: What the file holds.
        complaint: The phrase the refusal should carry.
    """
    _ = (tmp_path / SIDECAR_NAME).write_text(contents, encoding="utf-8", newline="\n")

    with pytest.raises(UnusableDeclarationError, match=complaint):
        _ = nearest(tmp_path, tmp_path)


def test_a_file_that_is_not_text_is_refused(tmp_path: Path) -> None:
    """The branch a hand-written file reaches when it is not one at all.

    A declaration is read as UTF-8 because that is what an editor writes;
    anything else is refused rather than decoded with a guess at its
    encoding.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    _ = (tmp_path / SIDECAR_NAME).write_bytes(b"\xff\xfe\x00\x4a")

    with pytest.raises(UnusableDeclarationError, match="could not be read"):
        _ = nearest(tmp_path, tmp_path)


def test_a_refusal_names_the_file(tmp_path: Path) -> None:
    """A complaint about an unnamed hidden file is not actionable.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    sidecar: Path = tmp_path / SIDECAR_NAME
    _ = sidecar.write_text("", encoding="utf-8", newline="\n")

    with pytest.raises(UnusableDeclarationError) as caught:
        _ = nearest(tmp_path, tmp_path)

    assert caught.value.sidecar == sidecar
    assert SIDECAR_NAME in str(caught.value)
