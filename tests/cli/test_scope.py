# tests/cli/test_scope.py
"""Tests for what a run covers, and what it refuses when it covers nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from typer.testing import CliRunner

from discography_toolkit.cli.main import app
from discography_toolkit.cli.scope import clean_input, resolve_path, to_folder

import pytest

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


# ==================================================================================== #
#                                     PROMPT INPUT                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"D:\\Music\\Miles Davis"', "D:\\Music\\Miles Davis"),
        ("'D:\\Music\\Miles Davis'", "D:\\Music\\Miles Davis"),
        ("  D:\\Music  ", "D:\\Music"),
        ('  "D:\\Music"  ', "D:\\Music"),
        ("D:\\Music", "D:\\Music"),
        # Stripped greedily, not one layer: Windows forbids a quote in a
        # filename, so there is no path this could damage.
        ('""D:\\Music""', "D:\\Music"),
    ],
)
def test_clean_input(raw: str, expected: str) -> None:
    """A shell strips quotes before argv; a prompt does not.

    Args:
        raw: The text as typed at the prompt.
        expected: What should survive.
    """
    assert clean_input(raw) == expected


# ==================================================================================== #
#                                    PATH RESOLUTION                                   #
# ==================================================================================== #
def test_resolve_path_accepts_a_given_directory(tmp_path: Path) -> None:
    """A path supplied on the command line is used as given.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    assert resolve_path(tmp_path, "unused") == tmp_path


def test_to_folder_strips_quotes(tmp_path: Path) -> None:
    """Quotes typed at the prompt reach the converter, not the filesystem.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    assert to_folder(f'"{tmp_path}"') == tmp_path


def test_to_folder_expands_a_home_shortcut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path typed as "~/Music" must reach the real folder.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Used to point home at the temporary directory.
    """
    music: Path = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows

    assert to_folder("~/Music") == music


def test_to_folder_makes_a_relative_path_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder is renamed mid-run, so a relative path would go stale.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Used to change the working directory.
    """
    music: Path = tmp_path / "Music"
    music.mkdir()
    monkeypatch.chdir(tmp_path)

    resolved: Path = to_folder("Music")

    assert resolved.is_absolute()
    assert resolved == music


@pytest.mark.parametrize("raw", ["", "   ", '""'])
def test_to_folder_refuses_an_empty_answer(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is not the working directory, which is what it used to mean.

    `Path("").resolve()` is the current directory and passes every check
    below it, so a space at the prompt answered "work wherever the shell
    happens to be" -- on commands that delete folders.

    Args:
        raw: The text as typed.
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Used to put the working directory somewhere real, so
            a pass would be the bug rather than an accident of location.
    """
    monkeypatch.chdir(tmp_path)

    with pytest.raises(typer.BadParameter):
        _ = to_folder(raw)


def test_to_folder_refuses_a_file(tmp_path: Path) -> None:
    """A track is not a folder to work beneath.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    track: Path = tmp_path / "01.flac"
    track.touch()

    with pytest.raises(typer.BadParameter):
        _ = to_folder(str(track))


def test_a_mistyped_path_is_asked_for_again(tmp_path: Path) -> None:
    """A typo costs a retype, not the command.

    The whole point of refusing through the converter: `click.prompt`
    catches it and loops, where an exit would send you back to the shell
    to type the command out once more.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    result = runner.invoke(app, ["tags", "title"], input=f"{tmp_path / 'nowhere'}\n{tmp_path}\n")

    assert "Not a directory" in result.output
    # Reached on the second answer, so the prompt was asked twice.
    assert "No audio files" in result.output
    assert result.exit_code == 0


def test_resolve_path_rejects_a_file(tmp_path: Path) -> None:
    """A track is not a folder to work beneath.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    track: Path = tmp_path / "01.flac"
    track.touch()

    with pytest.raises(typer.Exit) as exit_info:
        _ = resolve_path(track, "unused")

    assert exit_info.value.exit_code == 1


def test_resolve_path_rejects_a_missing_directory(tmp_path: Path) -> None:
    """A path that is not there fails rather than being created.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    with pytest.raises(typer.Exit) as exit_info:
        _ = resolve_path(tmp_path / "nowhere", "unused")

    assert exit_info.value.exit_code == 1
