# tests/cli/test_parameters.py
"""Tests for the option handling every command shares."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from discography_toolkit.cli.parameters import clean_input, resolve_path

import pytest

if TYPE_CHECKING:
    from pathlib import Path


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


def test_resolve_path_prompts_when_omitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No path means ask for one, and quotes typed at the prompt are stripped.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Used to answer the prompt.
    """

    def answer(_message: str) -> str:
        return f'"{tmp_path}"'

    monkeypatch.setattr(typer, "prompt", answer)

    assert resolve_path(None, "Enter a path") == tmp_path


def test_resolve_path_expands_a_home_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path typed as "~/Music" must reach the real folder.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Used to point home at the temporary directory.
    """
    music: Path = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows

    def answer(_message: str) -> str:
        return "~/Music"

    monkeypatch.setattr(typer, "prompt", answer)

    assert resolve_path(None, "Enter a path") == music


def test_resolve_path_makes_a_relative_path_absolute(
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

    def answer(_message: str) -> str:
        return "Music"

    monkeypatch.setattr(typer, "prompt", answer)

    resolved: Path = resolve_path(None, "Enter a path")

    assert resolved.is_absolute()
    assert resolved == music


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
