# src/discography_toolkit/operations/placement.py
"""Filing every album on the correct side of the FLAC container.

The layout keeps lossy albums directly under an artist folder and every
lossless album inside one sibling container. This walks both sides,
decides each album's side from its actual files -- never from the
"[FLAC]" text in a name, so it stays right when run on its own -- and
moves the ones that are on the wrong side.

A singles collection is the exception on both counts. It sits in the
root whatever it holds, since it is one pile of loose tracks rather than
a release of a format, and it is left out of the question of whether the
container is wanted at all -- an artist whose albums are every one
lossless, bar its singles, has nothing to separate and stays flat.

The container follows the albums. It is created the moment a lossless
album exists, normalised to a bare "FLAC" from any older "FLAC -
(56 on 65)" spelling, and removed once nothing lossless is left, so the
root goes flat rather than keeping an empty container.

Planning never writes. Applying moves in a careful order: the container
is created before the moves but renamed or removed after them, since
renaming it first would break the path of every album being lifted out,
and it is only empty once they have all left. The artist folder's own
name is left untouched -- describing what it holds is the label's job,
not this one's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import shutil
from typing import TYPE_CHECKING

from discography_toolkit.core import names
from discography_toolkit.core.layout import (
    CONTAINER_NAME,
    AudioTier,
    detect_tier,
    discover_albums,
    find_containers,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class TooManyContainersError(Exception):
    """Raised when an artist folder holds more than one FLAC container.

    Two containers cannot be merged without guessing which copy of a
    same-named album to keep, so placement refuses rather than picks.
    Consolidating them by hand is a one-time job.

    Attributes:
        containers: The container folders found.
    """

    def __init__(self, containers: tuple[Path, ...]) -> None:
        """Record the containers that were found.

        Args:
            containers: The clashing container folders.
        """
        self.containers: tuple[Path, ...] = containers
        names: str = ", ".join(repr(container.name) for container in containers)
        super().__init__(f"expected at most one FLAC container, found {len(containers)}: {names}")


class Side(StrEnum):
    """Which way an album needs to move, if at all."""

    IN = "move_in"
    OUT = "move_out"
    KEEP = "keep"


class ContainerChange(StrEnum):
    """What needs to happen to the container itself."""

    CREATE = "create"
    RENAME = "rename"
    REMOVE = "remove"


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Placement:
    """Where one album sits and where it belongs.

    Attributes:
        album: The album folder as it stands.
        side: The move it needs -- in, out, or keep.
        destination: Where it would end up, or `None` when it keeps its
            place.
        collision: Whether a folder already sat where this one would move
            to, taken once at plan time so the plan stays a stable
            snapshot after moves have happened.
    """

    album: Path
    side: Side
    destination: Path | None = None
    collision: bool = False

    @property
    def needs_move(self) -> bool:
        """Whether the album changes side."""
        return self.side is not Side.KEEP


@dataclass(frozen=True, slots=True)
class PlacementPlan:
    """What a run would move, and what becomes of the container.

    Attributes:
        placements: One entry per album, on both sides, in name order.
        container: The existing container, or `None`.
        working_container: The container path the moves target -- the
            existing one, or where a new one will be created.
        container_change: What happens to the container, or `None`.
    """

    placements: tuple[Placement, ...]
    container: Path | None
    working_container: Path
    container_change: ContainerChange | None

    @property
    def moving_in(self) -> tuple[Placement, ...]:
        """Lossless albums to lift into the container, clear of collisions."""
        return tuple(p for p in self.placements if p.side is Side.IN and not p.collision)

    @property
    def moving_out(self) -> tuple[Placement, ...]:
        """Non-lossless albums to lift out to the root, clear of collisions."""
        return tuple(p for p in self.placements if p.side is Side.OUT and not p.collision)

    @property
    def collisions(self) -> tuple[Placement, ...]:
        """Albums that cannot move -- a folder of that name is in the way."""
        return tuple(p for p in self.placements if p.needs_move and p.collision)


@dataclass(frozen=True, slots=True)
class PlacementReport:
    """What happened when a plan was applied.

    Attributes:
        moved: How many albums were successfully moved.
        container_change: What was done to the container, or `None`.
        failures: `(album, reason)` for each move that failed.
    """

    moved: int = 0
    container_change: ContainerChange | None = None
    failures: tuple[tuple[Path, str], ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def plan(
    artist: Path,
    on_progress: Callable[[Path], None] | None = None,
) -> PlacementPlan:
    """Work out which albums are on the wrong side, without writing.

    Args:
        artist: The artist folder to lay out.
        on_progress: Called with each album as it is examined, so a
            caller can drive a display without this module knowing one
            exists.

    Returns:
        The placement each album needs, and what becomes of the
        container.

    Raises:
        TooManyContainersError: If the artist holds more than one
            container, which cannot be resolved without guessing.
    """
    containers: list[Path] = find_containers(artist)
    if len(containers) > 1:
        raise TooManyContainersError(tuple(containers))
    container: Path | None = containers[0] if containers else None

    # Moves target the container's current path, not the name it ends up
    # with: renaming it first would break the source path of every album
    # lifted out of it, so the rename waits until after the moves.
    working_container: Path = container if container is not None else artist / CONTAINER_NAME

    # Tiers are read first, before any album is placed, because where an
    # album belongs depends on the whole: the container is only wanted
    # when it has something to separate the lossless albums from.
    albums: list[Path] = discover_albums(artist)
    tiers: dict[Path, AudioTier] = {}
    for album in albums:
        tiers[album] = detect_tier(album)
        if on_progress is not None:
            on_progress(album)

    flac_count: int = sum(1 for tier in tiers.values() if tier is AudioTier.LOSSLESS)
    wanted: bool = _container_wanted(flac_count, len(albums))

    placements: tuple[Placement, ...] = tuple(
        _place(album, tiers[album], artist, working_container, wanted) for album in albums
    )
    return PlacementPlan(
        placements=placements,
        container=container,
        working_container=working_container,
        container_change=_container_change(container, wanted),
    )


def apply(
    placement_plan: PlacementPlan,
    on_progress: Callable[[Path], None] | None = None,
) -> PlacementReport:
    """Carry out the moves and container changes, in a safe order.

    The container is created before the moves and renamed or removed
    after them. Only collision-free moves are attempted; a move that
    fails is recorded and the run goes on.

    Args:
        placement_plan: A plan produced by `plan`.
        on_progress: Called with each album as it is moved.

    Returns:
        A count of moves, what became of the container, and any
        failures.
    """
    if placement_plan.container_change is ContainerChange.CREATE:
        placement_plan.working_container.mkdir()

    moved: int = 0
    failures: list[tuple[Path, str]] = []
    for placement in (*placement_plan.moving_in, *placement_plan.moving_out):
        detail: str | None = _move(placement)
        if detail is None:
            moved += 1
        else:
            failures.append((placement.album, detail))
        if on_progress is not None:
            on_progress(placement.album)

    _settle_container(placement_plan)

    return PlacementReport(
        moved=moved,
        container_change=placement_plan.container_change,
        failures=tuple(failures),
    )


# ==================================================================================== #
#                                       PLANNING                                       #
# ==================================================================================== #
def _container_wanted(flac_count: int, total: int) -> bool:
    """Report whether the container earns its place: a genuine mix.

    The container exists to separate lossless albums from the rest, so it
    is wanted only when both are present -- at least one lossless album
    and at least one that is not. An all-lossless artist keeps its albums
    flat, with nothing to separate them from; an all-lossy or all-missing
    one never had a container to begin with.

    Args:
        flac_count: How many releases are lossless.
        total: How many releases there are in all, singles excluded.

    Returns:
        `True` when the artist holds both lossless and non-lossless
        releases.
    """
    return 0 < flac_count < total


def _place(
    album: Path,
    tier: AudioTier,
    artist: Path,
    working_container: Path,
    wanted: bool,
) -> Placement:
    """Decide the side one album belongs on.

    With a container wanted, lossless albums belong inside it and the
    rest in the root. With none wanted -- an all-lossless artist, or one
    with no lossless at all -- every album belongs in the root, and any
    still sitting in an old container is lifted out so the container can
    go.

    Args:
        album: The album folder to inspect.
        tier: The album's tier, already detected.
        artist: The artist folder, where the root-side albums belong.
        working_container: The container path a lossless album moves to.
        wanted: Whether the container is wanted for this artist.

    Returns:
        The album's tier and the move it needs, if any.
    """
    in_container: bool = album.parent != artist

    # A singles pile is not a release of a format, so the tier that would
    # otherwise file it says nothing. It belongs in the root, and is
    # lifted out of a container it was put in before this was settled.
    if names.is_singles(album.name):
        if in_container:
            singles_home: Path = artist / album.name
            return Placement(album, Side.OUT, singles_home, collision=singles_home.exists())
        return Placement(album, Side.KEEP)

    if not wanted:
        if in_container:
            destination: Path = artist / album.name
            return Placement(album, Side.OUT, destination, collision=destination.exists())
        return Placement(album, Side.KEEP)

    if tier is AudioTier.LOSSLESS and not in_container:
        destination = working_container / album.name
        return Placement(album, Side.IN, destination, collision=destination.exists())
    if tier is not AudioTier.LOSSLESS and in_container:
        destination = artist / album.name
        return Placement(album, Side.OUT, destination, collision=destination.exists())
    return Placement(album, Side.KEEP)


def _container_change(container: Path | None, wanted: bool) -> ContainerChange | None:
    """Decide what the container needs, from whether it is wanted.

    Args:
        container: The existing container, or `None`.
        wanted: Whether the artist wants a container at all.

    Returns:
        The change the container needs, or `None` when it is already
        right.
    """
    if not wanted:
        return ContainerChange.REMOVE if container is not None else None
    if container is None:
        return ContainerChange.CREATE
    return ContainerChange.RENAME if container.name != CONTAINER_NAME else None


# ==================================================================================== #
#                                       APPLYING                                       #
# ==================================================================================== #
def _move(placement: Placement) -> str | None:
    """Move one album to its destination, reporting failure rather than raising.

    Args:
        placement: The album and where it goes. Its destination is not
            `None`, since only moving placements reach here.

    Returns:
        The failure's detail, or `None` on success.
    """
    if placement.destination is None:  # unreachable: only moves are applied
        return None
    try:
        _ = shutil.move(str(placement.album), str(placement.destination))
    except OSError as exc:
        return str(exc)
    return None


def _settle_container(placement_plan: PlacementPlan) -> None:
    """Rename or remove the container once the albums have moved.

    Removal waits until the albums are out, since the container is only
    empty then, and refuses a non-empty directory -- anything still
    inside is something placement did not account for, and is safer kept
    than discarded.

    Args:
        placement_plan: The plan being applied.
    """
    change: ContainerChange | None = placement_plan.container_change
    if change is ContainerChange.REMOVE:
        container: Path | None = placement_plan.container
        if container is not None and container.exists():
            container.rmdir()
    elif change is ContainerChange.RENAME:
        working: Path = placement_plan.working_container
        _ = working.rename(working.with_name(CONTAINER_NAME))
