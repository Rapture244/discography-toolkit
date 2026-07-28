# tests/operations/test_tagging.py
"""Tests for planning and applying a metadata change.

Run against real files, since the point of the step is what it does to
them. The value function is exercised in all three shapes the pipeline
uses: a constant, a value derived from the path, and a value derived
from the tag already there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf

from discography_toolkit.core import metadata
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations import tagging

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def make_track(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building a silent FLAC, optionally pre-tagged.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking a name and optional tags, returning the path.
    """

    def build(name: str, **tags: str) -> Path:
        path: Path = tmp_path / f"{name}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, np.zeros(4410, dtype="float32"), 44100, format="FLAC")
        if tags:
            metadata.write(path, {Tag[key.upper()]: value for key, value in tags.items()})
        return path

    return build


def constant(values: Mapping[Tag, str]) -> tagging.Desired:
    """Build a value function returning the same values for every track.

    Args:
        values: The tags every track should hold.

    Returns:
        A `Desired` callable ignoring both its arguments.
    """

    def desired(_track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        return values

    return desired


# ==================================================================================== #
#                                       PLANNING                                       #
# ==================================================================================== #
def test_plan_marks_a_track_needing_a_change(make_track: Callable[..., Path]) -> None:
    """A track whose tag differs is queued for writing.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a")

    result = tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert result.total == 1
    assert len(result.pending) == 1
    assert result.pending[0].values == {Tag.GENRE: "Jazz"}


def test_plan_leaves_a_correct_track_alone(make_track: Callable[..., Path]) -> None:
    """A track already holding the value is not queued.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a", genre="Jazz")

    result = tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert result.pending == ()
    assert result.clean == 1


def test_plan_queues_only_the_tags_that_differ(make_track: Callable[..., Path]) -> None:
    """A track needing one of two changes writes only that one.

    Rewriting a field that already matches is work for nothing, and on a
    format that rewrites the whole tag block it is avoidable churn.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a", album="Nefertiti", genre="Rock")

    result = tagging.plan(
        [track],
        [Tag.ALBUM, Tag.GENRE],
        constant({Tag.ALBUM: "Nefertiti", Tag.GENRE: "Jazz"}),
    )

    assert result.pending[0].values == {Tag.GENRE: "Jazz"}


def test_plan_reports_the_values_it_found(make_track: Callable[..., Path]) -> None:
    """The outcome carries what was there, not only what should be.

    A caller showing "old -> new", or telling a track already correct
    from one with no value at all, needs both.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a", genre="Rock")

    result = tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert result.pending[0].current == {Tag.GENRE: "Rock"}
    assert result.pending[0].values == {Tag.GENRE: "Jazz"}


def test_plan_reports_an_absent_value_as_empty(make_track: Callable[..., Path]) -> None:
    """A track with no value at all is distinguishable from a wrong one.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a")

    result = tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert result.pending[0].current == {Tag.GENRE: ""}


def test_plan_records_an_unreadable_file(tmp_path: Path) -> None:
    """A file that is not audio is reported, not raised.

    Args:
        tmp_path: Pytest's per-test temporary directory.
    """
    cover: Path = tmp_path / "cover.jpg"
    _ = cover.write_bytes(b"not audio")

    result = tagging.plan([cover], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert len(result.errors) == 1
    assert result.errors[0].detail


def test_plan_keeps_going_after_a_bad_file(tmp_path: Path, make_track: Callable[..., Path]) -> None:
    """One unreadable file must not abandon the rest of the run.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        make_track: Factory building an audio file.
    """
    cover: Path = tmp_path / "cover.jpg"
    _ = cover.write_bytes(b"not audio")
    track: Path = make_track("a")

    result = tagging.plan([cover, track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert result.total == 2
    assert len(result.errors) == 1
    assert len(result.pending) == 1


def test_an_error_is_not_counted_as_clean(tmp_path: Path, make_track: Callable[..., Path]) -> None:
    """A file that could not be read is not a file already correct.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        make_track: Factory building an audio file.
    """
    cover: Path = tmp_path / "cover.jpg"
    _ = cover.write_bytes(b"not audio")
    correct: Path = make_track("a", genre="Jazz")

    result = tagging.plan([cover, correct], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert result.clean == 1
    assert len(result.errors) == 1


def test_plan_writes_nothing(make_track: Callable[..., Path]) -> None:
    """Planning is read-only, whatever it decides.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a")
    before: bytes = track.read_bytes()

    _ = tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert track.read_bytes() == before


def test_plan_reports_progress_per_track(make_track: Callable[..., Path]) -> None:
    """The callback fires once per track, with the track.

    Args:
        make_track: Factory building an audio file.
    """
    tracks: list[Path] = [make_track("a"), make_track("b")]
    seen: list[Path] = []

    _ = tagging.plan(tracks, [Tag.GENRE], constant({Tag.GENRE: "Jazz"}), on_progress=seen.append)

    assert seen == tracks


# ==================================================================================== #
#                                    VALUE FUNCTIONS                                   #
# ==================================================================================== #
def test_desired_can_derive_from_the_path(make_track: Callable[..., Path]) -> None:
    """The album-artist shape: a value taken from where the file sits.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("Miles Davis - 01")

    def desired(path: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        return {Tag.ALBUM_ARTIST: path.stem.split(" - ")[0]}

    result = tagging.plan([track], [Tag.ALBUM_ARTIST], desired)

    assert result.pending[0].values == {Tag.ALBUM_ARTIST: "Miles Davis"}


def test_desired_can_derive_from_the_current_value(make_track: Callable[..., Path]) -> None:
    """The title-casing shape: a value computed from the tag already there.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a", title="so what")

    def desired(_path: Path, current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        return {Tag.TITLE: current[Tag.TITLE].title()}

    result = tagging.plan([track], [Tag.TITLE], desired)

    assert result.pending[0].values == {Tag.TITLE: "So What"}


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def test_apply_writes_the_planned_values(make_track: Callable[..., Path]) -> None:
    """What the plan queued is what lands on disk.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a")
    queued = tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    report = tagging.apply(queued)

    assert report.written == 1
    assert metadata.read(track, [Tag.GENRE])[Tag.GENRE] == "Jazz"


def test_apply_skips_tracks_already_correct(make_track: Callable[..., Path]) -> None:
    """Only queued tracks are touched.

    Args:
        make_track: Factory building an audio file.
    """
    correct: Path = make_track("a", genre="Jazz")
    stale: Path = make_track("b", genre="Rock")
    queued = tagging.plan([correct, stale], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))
    before: bytes = correct.read_bytes()

    report = tagging.apply(queued)

    assert report.written == 1
    assert correct.read_bytes() == before


def test_apply_collects_failures_and_continues(make_track: Callable[..., Path]) -> None:
    """A track deleted between planning and writing fails alone.

    Args:
        make_track: Factory building an audio file.
    """
    doomed: Path = make_track("doomed")
    survivor: Path = make_track("survivor")
    queued = tagging.plan([doomed, survivor], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))
    doomed.unlink()

    report = tagging.apply(queued)

    assert report.written == 1
    assert len(report.failures) == 1
    assert report.failures[0][0] == doomed


def test_apply_reports_progress_per_written_track(make_track: Callable[..., Path]) -> None:
    """The callback fires for written tracks only, not skipped ones.

    Args:
        make_track: Factory building an audio file.
    """
    correct: Path = make_track("a", genre="Jazz")
    stale: Path = make_track("b", genre="Rock")
    queued = tagging.plan([correct, stale], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))
    seen: list[Path] = []

    _ = tagging.apply(queued, on_progress=seen.append)

    assert seen == [stale]


def test_a_second_run_finds_nothing_to_do(make_track: Callable[..., Path]) -> None:
    """Applying twice is applying once: the step is idempotent.

    Args:
        make_track: Factory building an audio file.
    """
    track: Path = make_track("a")
    _ = tagging.apply(tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"})))

    again = tagging.plan([track], [Tag.GENRE], constant({Tag.GENRE: "Jazz"}))

    assert again.pending == ()
    assert again.clean == 1
