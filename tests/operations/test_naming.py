# tests/operations/test_naming.py
"""Tests for rebuilding an album folder's name.

Run against real folders, since the operation's whole job is the name it
leaves on disk. Albums are built from silent FLACs where a tier matters,
and from empty folders where "missing" is the point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.core import names
from discography_toolkit.operations import naming

import pytest
from tests.helpers import fill

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.helpers import Tier


# ==================================================================================== #
#                                       FIXTURES                                       #
# ==================================================================================== #
@pytest.fixture()
def make_album(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory building an album folder with a chosen tier.

    Args:
        tmp_path: Pytest's per-test temporary directory.

    Returns:
        A callable taking a folder name and a tier keyword, returning the
        folder. `"none"` leaves it empty.
    """

    def build(name: str, tier: Tier = "lossless") -> Path:
        return fill(tmp_path / name, tier)

    return build


def named(album: Path) -> naming.AlbumName:
    """Plan one album and return its outcome.

    Args:
        album: The album folder to plan.

    Returns:
        Its outcome.
    """
    return naming.plan([album]).outcomes[0]


# ==================================================================================== #
#                                      ASSEMBLY                                        #
# ==================================================================================== #
def test_a_name_is_title_cased_and_tagged(make_album: Callable[..., Path]) -> None:
    """A lowercase, untagged lossless album comes out cased and marked FLAC.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - kind of blue")

    assert named(album).new_name == "01. (1959) - Kind of Blue [FLAC]"


def test_the_pin_mark_moves_to_the_front(make_album: Callable[..., Path]) -> None:
    """A "©" typed anywhere is relocated to the very front.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. © (1959) - Kind of Blue")

    assert named(album).new_name == "©01. (1959) - Kind of Blue [FLAC]"


def test_an_existing_index_is_kept(make_album: Callable[..., Path]) -> None:
    """A numbering index already present survives the rebuild verbatim.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("27. (1977) - October")

    assert named(album).new_name.startswith("27. (1977) - ")


def test_an_opus_album_is_tagged_opus(make_album: Callable[..., Path]) -> None:
    """A transcode to Opus earns its own tag, not FLAC's.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1970) - Bitches Brew", tier="opus")

    assert named(album).new_name == "01. (1970) - Bitches Brew [OPUS]"


def test_a_lossy_album_earns_no_tag(make_album: Callable[..., Path]) -> None:
    """MP3s are held but not worth announcing, so no tag is added.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1951) - Modern Jazz", tier="lossy")

    assert named(album).new_name == "01. (1951) - Modern Jazz"


def test_a_stale_tag_is_replaced_by_the_real_tier(make_album: Callable[..., Path]) -> None:
    """A folder that kept "[FLAC]" after a transcode has it corrected.

    The tag is never trusted from the name -- the files decide it -- so a
    lossy album carrying a stale "[FLAC]" loses it.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1951) - Modern Jazz [FLAC]", tier="lossy")

    assert named(album).new_name == "01. (1951) - Modern Jazz"


def test_an_album_without_a_year_is_skipped(make_album: Callable[..., Path]) -> None:
    """Without a year there is nothing to anchor a name around.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("A Folder With No Year")

    outcome = named(album)

    assert outcome.skipped
    assert not outcome.needs_rename
    assert outcome.new_name == ""


# ==================================================================================== #
#                                   MISSING & CONFLICT                                 #
# ==================================================================================== #
def test_an_empty_folder_is_marked_missing(make_album: Callable[..., Path]) -> None:
    """A folder with no audio is missing whatever the name says.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1980) - Lost Album", tier="none")

    outcome = named(album)

    assert outcome.missing
    assert outcome.new_name == "01. (1980) - M - Lost Album"


def test_a_first_time_missing_mark_is_flagged(make_album: Callable[..., Path]) -> None:
    """The tool declaring an album lost is worth a person's eye.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1980) - Lost Album", tier="none")

    assert named(album).newly_missing


def test_an_already_missing_folder_is_not_newly_flagged(
    make_album: Callable[..., Path],
) -> None:
    """A folder already carrying "M", still empty, is settled, not news.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1980) - M - Truly Gone", tier="none")

    outcome = named(album)

    assert outcome.missing
    assert not outcome.newly_missing
    assert outcome.new_name == "01. (1980) - M - Truly Gone"


def test_a_missing_claim_over_audio_is_a_conflict(make_album: Callable[..., Path]) -> None:
    """A name calling an album missing while the folder holds audio gets "⚠".

    The two ways it clears -- delete the strays, or drop the marker --
    resolve in opposite directions, so a person decides, not the tool.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1985) - M - Decoy", tier="lossless")

    outcome = named(album)

    assert outcome.conflict
    assert not outcome.missing
    assert outcome.new_name == "01. (1985) - \u26a0 - Decoy"


def test_a_conflict_carries_no_quality_tag(make_album: Callable[..., Path]) -> None:
    """The format is exactly what is in doubt, so it is not announced.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1985) - M - Decoy", tier="lossless")

    assert "[FLAC]" not in named(album).new_name


# ==================================================================================== #
#                                      EP MARKER                                       #
# ==================================================================================== #
@pytest.mark.parametrize(
    "typed",
    [
        "01. (1994) - Zomba EP",
        "01. (1994) - EP Zomba",
        "01. (1994) - Zomba (EP)",
        "01. (1994) - Zomba [EP]",
    ],
)
def test_every_ep_shape_settles_in_one_place(make_album: Callable[..., Path], typed: str) -> None:
    """However it was typed, the marker ends up after the title.

    Args:
        make_album: Factory building an album folder.
        typed: The folder name as found.
    """
    album: Path = make_album(typed)

    outcome: naming.AlbumName = named(album)

    assert outcome.new_name == "01. (1994) - Zomba (EP) [FLAC]"
    assert outcome.is_ep


def test_the_ep_marker_sits_before_the_quality_tag(make_album: Callable[..., Path]) -> None:
    """The release is what it is; the tag says how this copy was made.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1994) - Zomba [EP]")

    new_name: str = named(album).new_name

    assert new_name.index("(EP)") < new_name.index("[FLAC]")


def test_an_ep_name_is_already_settled(make_album: Callable[..., Path]) -> None:
    """Naming twice is naming once.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1994) - Zomba (EP) [FLAC]")

    assert not named(album).needs_rename


def test_a_shared_bracket_keeps_what_is_not_the_marker(
    make_album: Callable[..., Path],
) -> None:
    """Only the marker is cut; the rest of the bracket survives.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1994) - Zomba [EP, Remastered]")

    assert named(album).new_name == "01. (1994) - Zomba [Remastered] (EP) [FLAC]"


def test_a_missing_ep_keeps_both_markers(make_album: Callable[..., Path]) -> None:
    """The two markers are unrelated and both survive, each in its place.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1994) - M - Zomba EP", tier="none")

    outcome: naming.AlbumName = named(album)

    assert outcome.new_name == "01. (1994) - M - Zomba (EP)"
    assert outcome.missing
    assert outcome.is_ep


def test_an_opus_ep_keeps_the_order(make_album: Callable[..., Path]) -> None:
    """The slot is the same whatever tier the copy is.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1994) - EP Zomba", tier="opus")

    assert named(album).new_name == "01. (1994) - Zomba (EP) [OPUS]"


def test_a_lowercase_ep_is_reported_not_acted_on(make_album: Callable[..., Path]) -> None:
    """It is as likely an ordinary word as a marker, so a person decides.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1994) - Zomba ep")

    plan: naming.NamePlan = naming.plan([album])
    outcome: naming.AlbumName = plan.outcomes[0]

    assert not outcome.is_ep
    assert outcome.lowercase_ep
    assert len(plan.lowercase_eps) == 1
    assert "(EP)" not in outcome.new_name


def test_a_title_starting_with_ep_is_left_alone(make_album: Callable[..., Path]) -> None:
    """Word bounds keep the marker out of the title.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1994) - Epitaph")

    outcome: naming.AlbumName = named(album)

    assert outcome.new_name == "01. (1994) - Epitaph [FLAC]"
    assert not outcome.is_ep


# ==================================================================================== #
#                                     THE WHOLE PLAN                                   #
# ==================================================================================== #
def test_every_album_lands_in_exactly_one_state(make_album: Callable[..., Path]) -> None:
    """Held, missing, conflicted and skipped are exclusive and cover all.

    Args:
        make_album: Factory building an album folder.
    """
    held: Path = make_album("01. (1959) - Held", tier="lossless")
    gone: Path = make_album("02. (1980) - Gone", tier="none")
    clash: Path = make_album("03. (1985) - M - Clash", tier="lossless")
    skip: Path = make_album("No Year", tier="lossless")

    result = naming.plan([held, gone, clash, skip])
    outcomes = {outcome.album: outcome for outcome in result.outcomes}

    assert not any((outcomes[held].skipped, outcomes[held].missing, outcomes[held].conflict))
    assert outcomes[gone].missing
    assert outcomes[clash].conflict
    assert outcomes[skip].skipped
    assert result.conflicts == (outcomes[clash],)


def test_a_correct_name_is_not_pending(make_album: Callable[..., Path]) -> None:
    """An album already carrying its settled name is not work.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - Kind of Blue [FLAC]")

    result = naming.plan([album])

    assert result.pending == ()
    assert not result.outcomes[0].needs_rename


@pytest.mark.parametrize(
    "typed",
    [
        "(1959) kind of blue",  # no separator, uncased
        "01. \u00a9 (1959) - KIND OF BLUE [flac]",  # pin mid-name, shouting, stale tag
        "(1959) - kind of blue [FLAC, 40th Anniversary]",  # a tag sharing the bracket
        "EP zomba (1994)",  # marker at the front, year at the end
        "(1980) - M - lost record",  # a missing claim over a folder holding audio
    ],
)
def test_a_rebuilt_name_conforms(make_album: Callable[..., Path], typed: str) -> None:
    """Whatever the rebuild produces is a name the convention accepts.

    This is the contract the layout pass leans on: it plans without
    writing, holds each proposed name to `conforms_unnumbered`, and skips
    the artist whole if one would not settle. Were the rebuild able to
    emit a name its own pattern rejects, that guard would refuse work it
    had just done correctly.

    Args:
        make_album: Factory building an album folder.
        typed: The folder name as found, in one of the shapes a real
            shelf carries.
    """
    album: Path = make_album(typed)

    outcome: naming.AlbumName = named(album)

    assert not outcome.skipped
    assert names.conforms_unnumbered(outcome.new_name)


def test_albums_are_planned_in_the_order_given(make_album: Callable[..., Path]) -> None:
    """A report reads in the order the caller discovered things.

    Args:
        make_album: Factory building an album folder.
    """
    first: Path = make_album("01. (1959) - A")
    second: Path = make_album("02. (1970) - B")

    result = naming.plan([second, first])

    assert [outcome.album for outcome in result.outcomes] == [second, first]


def test_progress_is_reported_for_every_album(make_album: Callable[..., Path]) -> None:
    """The caller drives a display without this module knowing one exists.

    Args:
        make_album: Factory building an album folder.
    """
    albums: list[Path] = [make_album("01. (1959) - A"), make_album("02. (1970) - B")]
    seen: list[Path] = []

    _ = naming.plan(albums, on_progress=seen.append)

    assert seen == albums


def test_nothing_is_written_by_planning(make_album: Callable[..., Path]) -> None:
    """Planning is safe to run against a discography just to read the answer.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - kind of blue")

    _ = naming.plan([album])

    assert album.exists()
    assert album.name == "01. (1959) - kind of blue"


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def test_applying_renames_the_folder(make_album: Callable[..., Path]) -> None:
    """The folder on disk takes the settled name.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - kind of blue")
    parent: Path = album.parent

    report = naming.apply(naming.plan([album]))

    assert report.renamed == 1
    assert (parent / "01. (1959) - Kind of Blue [FLAC]").is_dir()
    assert not album.exists()


def test_a_case_only_rename_is_applied(make_album: Callable[..., Path]) -> None:
    """A title that differs only in case is still recased on disk.

    A lossy album earns no tag, so casing "kind of blue" to "Kind of
    Blue" is a change of case alone -- which a case-insensitive
    filesystem treats as no change unless it is staged.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - kind of blue", tier="lossy")
    parent: Path = album.parent

    report = naming.apply(naming.plan([album]))

    assert report.renamed == 1
    assert (parent / "01. (1959) - Kind of Blue").is_dir()


def test_a_case_only_rename_routes_through_staging(
    make_album: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A case-only change goes via a staging name, not straight across.

    On a case-sensitive host the two are the same by result, so the path
    itself is checked -- the staging step is what makes the rename land
    on a case-insensitive filesystem, where the folder's own name reads
    as the target.

    Args:
        make_album: Factory building an album folder.
        monkeypatch: Pytest's attribute patcher.
    """
    album: Path = make_album("01. (1959) - kind of blue", tier="lossy")
    moves: list[tuple[str, str]] = []
    real_rename = type(album).rename

    def spy(self: Path, target: Path) -> Path:
        moves.append((self.name, target.name))
        return real_rename(self, target)

    monkeypatch.setattr(type(album), "rename", spy)
    _ = naming.apply(naming.plan([album]))

    assert any(after.startswith(".__naming__") for _, after in moves)
    assert moves[-1][1] == "01. (1959) - Kind of Blue"


def test_applying_is_idempotent(make_album: Callable[..., Path]) -> None:
    """A second run finds the name already settled and changes nothing.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - kind of blue")
    parent: Path = album.parent

    _ = naming.apply(naming.plan([album]))
    settled: Path = next(child for child in parent.iterdir() if child.is_dir())
    second = naming.plan([settled])

    assert second.pending == ()


def test_a_target_already_taken_is_refused(make_album: Callable[..., Path]) -> None:
    """A folder in the way holds its own album, so it is not overwritten.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - kind of blue")
    # The exact name the plan will want, already occupied by something.
    taken: Path = album.parent / "01. (1959) - Kind of Blue [FLAC]"
    taken.mkdir()
    _ = (taken / "keep.flac").write_bytes(b"do not lose me")

    report = naming.apply(naming.plan([album]))

    assert report.renamed == 0
    assert len(report.failures) == 1
    assert report.failures[0][0] == album
    assert (taken / "keep.flac").read_bytes() == b"do not lose me"
    assert album.exists()


def test_a_rename_that_the_os_refuses_is_reported(make_album: Callable[..., Path]) -> None:
    """A rename can fail past the existence check, and that is caught too.

    The parent of the settled name is removed after planning, so the move
    is attempted and the OS refuses it -- the run reports it rather than
    raising mid-discography.

    Args:
        make_album: Factory building an album folder.
    """
    album: Path = make_album("01. (1959) - kind of blue")
    plan = naming.plan([album])
    # Rename the album's own parent out from under it, so the target path
    # points into a directory that no longer exists.
    _ = album.parent.rename(album.parent.parent / "moved-away")

    report = naming.apply(plan)

    assert report.renamed == 0
    assert len(report.failures) == 1
    assert report.failures[0][0] == album


def test_one_failure_does_not_abandon_the_rest(make_album: Callable[..., Path]) -> None:
    """A single blocked rename must not stop the others.

    Args:
        make_album: Factory building an album folder.
    """
    blocked: Path = make_album("01. (1959) - kind of blue")
    (blocked.parent / "01. (1959) - Kind of Blue [FLAC]").mkdir()
    fine: Path = make_album("02. (1970) - so what")

    report = naming.apply(naming.plan([blocked, fine]))

    assert report.renamed == 1
    assert len(report.failures) == 1
    assert (fine.parent / "02. (1970) - So What [FLAC]").is_dir()


def test_progress_is_reported_for_every_rename(make_album: Callable[..., Path]) -> None:
    """Every folder renamed announces itself, so a bar can be sized from the plan.

    Args:
        make_album: Factory building an album folder.
    """
    albums: list[Path] = [make_album("01. (1959) - a"), make_album("02. (1970) - b")]
    plan = naming.plan(albums)
    seen: list[Path] = []

    _ = naming.apply(plan, on_progress=seen.append)

    assert seen == [outcome.album for outcome in plan.pending]
