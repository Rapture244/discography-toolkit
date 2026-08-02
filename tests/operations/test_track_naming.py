# tests/operations/test_track_naming.py
"""Tests for title-casing audio filenames.

Run against real files. They need no audio content -- the operation reads
only names -- so they are made as empty files, which keeps the casing and
the collision handling in plain view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.operations import track_naming

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def album(tmp_path: Path) -> Callable[..., list[Path]]:
    """Return a factory making files in a folder and handing back their paths.

    Skips the test outright if the requested names can't coexist on the
    host filesystem -- e.g. two names differing only by case on NTFS or
    a case-insensitive APFS volume, where the second `touch()` just
    re-touches the first file instead of creating a second one. That is
    a property of the disk under the test runner, not of the code under
    test, so letting the test fail would blame the wrong thing.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking filenames and returning their paths, in order.
    """

    def build(*filenames: str, folder: str = "album") -> list[Path]:
        base: Path = tmp_path / folder
        base.mkdir(exist_ok=True)
        made: list[Path] = []
        for name in filenames:
            track: Path = base / name
            track.touch()
            made.append(track)

        on_disk: set[str] = {child.name for child in base.iterdir()}
        if any(name not in on_disk for name in filenames):
            pytest.skip("host filesystem can't hold names differing only by case")

        return made

    return build


def names_on_disk(track: Path) -> list[str]:
    """List the filenames sitting beside a track, sorted.

    Args:
        track: Any file, used to find its folder.

    Returns:
        The visible sibling filenames.
    """
    return sorted(child.name for child in track.parent.iterdir() if child.name[0] != ".")


# ==================================================================================== #
#                                       CASING                                         #
# ==================================================================================== #
def test_a_stem_is_cased_and_the_extension_kept(album: Callable[..., list[Path]]) -> None:
    """The name is title-cased; the extension comes back exactly as found.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("01 - kind of blue.flac")

    outcome = track_naming.plan(tracks).outcomes[0]

    assert outcome.new_name == "01 - Kind of Blue.flac"
    assert outcome.needs_rename


def test_an_already_cased_track_is_not_pending(album: Callable[..., list[Path]]) -> None:
    """A correctly cased name is not work.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("01 - Kind of Blue.flac")

    result = track_naming.plan(tracks)

    assert result.pending == ()
    assert not result.outcomes[0].needs_rename


def test_nothing_is_written_by_planning(album: Callable[..., list[Path]]) -> None:
    """Planning reads names and touches nothing.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("01 - so what.flac")

    _ = track_naming.plan(tracks)

    assert tracks[0].name == "01 - so what.flac"


def test_progress_is_reported_for_every_track(album: Callable[..., list[Path]]) -> None:
    """The caller drives a display without this module knowing one exists.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("a.flac", "b.flac")
    seen: list[Path] = []

    _ = track_naming.plan(tracks, on_progress=seen.append)

    assert seen == tracks


# ==================================================================================== #
#                                      COLLISIONS                                      #
# ==================================================================================== #
def test_two_names_casing_to_one_collide(album: Callable[..., list[Path]]) -> None:
    """Two files that case to the same name cannot both take it.

    One is renamed; the other is flagged, since renaming it too would
    overwrite the first.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("MILES.flac", "miles.flac")

    result = track_naming.plan(tracks)

    assert len(result.pending) == 1
    assert len(result.collisions) == 1


def test_a_target_held_by_a_settled_file_collides(album: Callable[..., list[Path]]) -> None:
    """A cased name already on disk, not itself moving, blocks the rename.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("Song.flac", "song.flac")

    result = track_naming.plan(tracks)

    # "Song.flac" is already correct and stays put; "song.flac" would
    # need its name and cannot have it.
    collided = result.collisions
    assert len(collided) == 1
    assert collided[0].track.name == "song.flac"


def test_same_name_in_different_folders_does_not_collide(
    album: Callable[..., list[Path]],
) -> None:
    """A name is unique only within its folder; two albums may share one.

    Args:
        album: Factory making files.
    """
    first: list[Path] = album("kind of blue.flac", folder="one")
    second: list[Path] = album("kind of blue.flac", folder="two")

    result = track_naming.plan([*first, *second])

    assert result.collisions == ()
    assert len(result.pending) == 2


def test_a_collision_is_left_out_of_pending(album: Callable[..., list[Path]]) -> None:
    """A clashing track is never queued, so applying can never lose it.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("MILES.flac", "miles.flac")

    pending_tracks = {o.track for o in track_naming.plan(tracks).pending}

    assert len(pending_tracks) == 1


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def test_applying_renames_the_files(album: Callable[..., list[Path]]) -> None:
    """The files on disk take their cased names.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("01 - kind of blue.flac", "02 - so what.mp3")

    report = track_naming.apply(track_naming.plan(tracks))

    assert report.renamed == 2
    assert names_on_disk(tracks[0]) == ["01 - Kind of Blue.flac", "02 - So What.mp3"]


def test_a_case_only_change_is_applied(album: Callable[..., list[Path]]) -> None:
    """A change of case alone still lands, routed through a staging name.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("MILES DAVIS.flac")

    report = track_naming.apply(track_naming.plan(tracks))

    assert report.renamed == 1
    assert names_on_disk(tracks[0]) == ["Miles Davis.flac"]


def test_applying_leaves_no_staging_names(album: Callable[..., list[Path]]) -> None:
    """A finished run shows only final names, no half-renamed files.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("kind of blue.flac")

    _ = track_naming.apply(track_naming.plan(tracks))

    assert all(not name.startswith(".") for name in names_on_disk(tracks[0]))


def test_a_collision_is_not_renamed(album: Callable[..., list[Path]]) -> None:
    """Applying touches only the safe renames; the clash is left in place.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("MILES.flac", "miles.flac")
    plan = track_naming.plan(tracks)

    _ = track_naming.apply(plan)

    # Both files survive: one is now "Miles.flac", the clashing one kept
    # under its own name rather than overwritten.
    surviving: list[str] = names_on_disk(tracks[0])
    assert "Miles.flac" in surviving
    assert len(surviving) == 2


def test_a_case_only_change_routes_through_staging(
    album: Callable[..., list[Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A case-only rename goes via a staging name, not straight across.

    On a case-sensitive host the two are indistinguishable by result, so
    the code path itself is checked: the staging step is what makes the
    rename unambiguous on the case-insensitive filesystems this runs on.

    Args:
        album: Factory making files.
        monkeypatch: Pytest's attribute patcher.
    """
    tracks: list[Path] = album("miles.flac")  # -> "Miles.flac", case only
    moves: list[tuple[str, str]] = []
    real_rename = type(tracks[0]).rename

    def spy(self: Path, target: Path) -> Path:
        moves.append((self.name, target.name))
        return real_rename(self, target)

    monkeypatch.setattr(type(tracks[0]), "rename", spy)
    _ = track_naming.apply(track_naming.plan(tracks))

    assert any(after.startswith(".__casing__") for _, after in moves)
    assert moves[-1][1] == "Miles.flac"


def test_a_plain_change_does_not_stage(
    album: Callable[..., list[Path]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change that alters more than case is a single, direct rename.

    Args:
        album: Factory making files.
        monkeypatch: Pytest's attribute patcher.
    """
    tracks: list[Path] = album("kind  of  blue.flac")  # spacing changes too
    moves: list[tuple[str, str]] = []
    real_rename = type(tracks[0]).rename

    def spy(self: Path, target: Path) -> Path:
        moves.append((self.name, target.name))
        return real_rename(self, target)

    monkeypatch.setattr(type(tracks[0]), "rename", spy)
    _ = track_naming.apply(track_naming.plan(tracks))

    assert len(moves) == 1
    assert not any(after.startswith(".__casing__") for _, after in moves)


def test_applying_is_idempotent(album: Callable[..., list[Path]]) -> None:
    """A second run finds every name settled and changes nothing.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("kind of blue.flac")
    parent: Path = tracks[0].parent

    _ = track_naming.apply(track_naming.plan(tracks))
    settled: list[Path] = list(parent.iterdir())
    second = track_naming.plan(settled)

    assert second.pending == ()


def test_a_failed_rename_is_reported(album: Callable[..., list[Path]]) -> None:
    """A file removed after planning fails to rename, and the run goes on.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("gone.flac", "here.flac")
    plan = track_naming.plan(tracks)
    tracks[0].unlink()

    report = track_naming.apply(plan)

    assert report.renamed == 1
    assert len(report.failures) == 1
    assert report.failures[0][0] == tracks[0]
    assert "Here.flac" in names_on_disk(tracks[1])


def test_progress_is_reported_for_every_rename(album: Callable[..., list[Path]]) -> None:
    """Every file touched announces itself, so a bar can be sized from the plan.

    Args:
        album: Factory making files.
    """
    tracks: list[Path] = album("a.flac", "b.flac")
    plan = track_naming.plan(tracks)
    seen: list[Path] = []

    _ = track_naming.apply(plan, on_progress=seen.append)

    assert seen == [o.track for o in plan.pending]
