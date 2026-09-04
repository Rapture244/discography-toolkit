# src/discography_toolkit/core/musicbrainz.py
#
# `urllib.request.urlopen` and `json.loads` are both untyped upstream, so
# every value between the socket and the first cast is `Any`. Silenced
# for this file rather than argued with line by line: the boundary is
# four lines of `_once`, and everything past it is typed by the shapes
# below. The same relaxation `metadata.py` gets for mutagen, and for the
# same reason.
# pyright: reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false
"""Reading artist credits from MusicBrainz, by the ids a tagger left behind.

The one part of this toolkit that goes to the network, and the only one
whose answer does not come from the shelf. Everything else reads the
folders and the files; this reads a release the files already name.

That naming is what makes the lookup exact rather than a search. A file
tagged by Picard carries `MUSICBRAINZ_ALBUMID` -- the id of one release,
not of the album in the abstract -- so the thirteen-track edition and
the fifteen-track one are different ids, and holding either means asking
about that one. `MUSICBRAINZ_RELEASETRACKID` then pins each file to a
track within it. Nothing is matched on a title, a duration or a track
count, so there is nothing to match wrongly.

A file without those ids is not looked up at all. Searching by name is
how the wrong edition gets credited, and a run that guessed once would
be a run whose other answers could no longer be trusted either.

Requests are paced: MusicBrainz asks for about one a second and a
User-Agent that says who is calling. The pacing lives on the one
function every request passes through, since an album that needs a
barcode search and then a release fetch makes two of them from two
places.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import TYPE_CHECKING, Final, TypedDict, cast
import urllib.error
import urllib.parse
import urllib.request

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
_ENDPOINT: Final[str] = "https://musicbrainz.org/ws/2/release/"
_QUERY: Final[str] = "?inc=recordings+artist-credits+isrcs&fmt=json"
_SEARCH: Final[str] = "https://musicbrainz.org/ws/2/release?fmt=json&limit=5&query=barcode:"

# MusicBrainz refuses an anonymous caller and asks that the agent name
# the application and a way to reach whoever runs it.
_USER_AGENT: Final[str] = (
    "discography-toolkit/0.1 ( https://github.com/Rapture244/discography-toolkit )"
)

# Their published rate is one request a second, averaged. The extra
# tenth is slack for a clock that rounds the wrong way -- being refused
# for haste costs a retry, and waiting costs a tenth of a second.
_INTERVAL: Final[float] = 1.1

_TIMEOUT: Final[float] = 30.0

# When the last request went out, so the pacing holds across every
# caller rather than per loop. Module state on purpose: an album needing
# a barcode search and then a release fetch makes two requests from two
# functions, and a delay counted inside either one would let the other
# through unpaced -- which is how a throttled connection ends up hanging
# until it times out.
_last_request: float = 0.0


# ==================================================================================== #
#                                     JSON SHAPES                                      #
# ==================================================================================== #
# Only the slice this reads, spelled out so the response is typed rather
# than `Any` all the way down.
class _Credit(TypedDict, total=False):
    """One name in an artist credit, and the words joining it to the next."""

    name: str
    joinphrase: str


class _Recording(TypedDict, total=False):
    """A recording, narrowed to the codes that name it."""

    isrcs: list[str]


# Functional syntax: "artist-credit" is not a Python identifier.
_Track = TypedDict(
    "_Track",
    {
        "id": str,
        "title": str,
        "position": int,
        "recording": "_Recording",
        "artist-credit": "list[_Credit]",
    },
    total=False,
)


class _Medium(TypedDict, total=False):
    """One disc of a release."""

    position: int
    tracks: list[_Track]


class _Release(TypedDict, total=False):
    """A release, narrowed to the discs this reads."""

    media: list[_Medium]


class _Found(TypedDict, total=False):
    """A search result, narrowed to the ids it turned up."""

    releases: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class Credited:
    """What MusicBrainz says one track is.

    Attributes:
        artist: The artist credit, as the release printed it.
        title: The track's title, which by MusicBrainz's own convention
            carries no "(feat. ...)" -- a featured artist belongs in the
            credit, and appears there instead.
    """

    artist: str
    title: str


@dataclass(frozen=True, slots=True)
class ReleaseTrack:
    """One track of a release, and every handle onto it.

    Three handles because a file rarely carries all three. The track id
    is the exact one. The ISRCs name the recording, which finds the track
    once the release is known. The position is the last resort, and only
    trustworthy when the folder and the release agree on how many tracks
    there are.

    Attributes:
        track_id: The MusicBrainz release-track id.
        isrcs: Every ISRC the recording carries, often none.
        disc: Which disc of the release, counting from one.
        position: Which track of that disc, counting from one.
        credited: What MusicBrainz says the track is.
    """

    track_id: str
    isrcs: frozenset[str]
    disc: int
    position: int
    credited: Credited


@dataclass(frozen=True, slots=True)
class Release:
    """One release as MusicBrainz holds it.

    Attributes:
        tracks: Its tracks, in the order the release lists them.
    """

    tracks: tuple[ReleaseTrack, ...] = ()


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def releases_for(
    release_ids: Iterable[str],
    on_progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Release], dict[str, str]]:
    """Read every named release, whole.

    One request per release, paced. A release that cannot be read is
    recorded and the rest go on: a network that drops once should not
    abandon an artist of thirty albums.

    Args:
        release_ids: The MusicBrainz release ids to ask about.
        on_progress: Called with each id as it is fetched.

    Returns:
        Each release id mapped to what it holds, and each that could not
        be read mapped to why.
    """
    found: dict[str, Release] = {}
    failures: dict[str, str] = {}

    for release_id in release_ids:
        try:
            found[release_id] = _tracks_of(_fetch(f"{_ENDPOINT}{_quoted(release_id)}{_QUERY}"))
        except (urllib.error.URLError, TimeoutError, TypeError, ValueError) as exc:
            failures[release_id] = str(exc)
        if on_progress is not None:
            on_progress(release_id)

    return found, failures


def release_for_barcode(barcode: str) -> str | None:
    """Find the one release carrying a barcode, or refuse.

    A barcode names the physical product, so it names an edition rather
    than an album -- which is what makes it usable at all. But it is not
    unique in the database: a pressing entered twice, or two countries
    sharing one, both come back as several. The scores the search returns
    are ignored and the count is read instead, because a best guess among
    editions is the thing this whole command refuses to make.

    Args:
        barcode: The barcode as the file carries it.

    Returns:
        The release id, or `None` when no release carries the barcode or
        more than one does.

    Raises:
        URLError: When the request fails or the service refuses it.
        TypeError: When the response is not a JSON object.
        ValueError: When the response is not JSON at all.
    """
    payload = cast("_Found", _fetch(f"{_SEARCH}{_quoted(barcode)}"))
    releases: list[dict[str, str]] = payload.get("releases", [])
    if len(releases) != 1:
        return None
    return releases[0].get("id") or None


def flatten(credit: Sequence[Mapping[str, object]]) -> str:
    """Render an artist credit the way the release itself printed it.

    MusicBrainz keeps a credit as its parts and the words between them,
    so "Common feat. Lauryn Hill" is two names and one join phrase. The
    phrase is not decoration: "feat." is a guest on someone's record and
    "&" is a duo, and dropping it would say the same thing of both.

    Typed as a plain mapping of `object` rather than `_Credit`: a `list`
    is invariant, so a caller holding `list[dict[str, str]]` could not
    pass one otherwise -- and a `TypedDict` is assignable to a mapping of
    `object` alone, its value type being the union of its own fields.

    Args:
        credit: The credit as MusicBrainz returned it.

    Returns:
        The credit as one string, empty when it held no names.
    """
    return "".join(f"{part.get('name', '')}{part.get('joinphrase', '')}" for part in credit).strip()


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
def _quoted(value: str) -> str:
    """Escape one value for use in a URL.

    Args:
        value: An id or a barcode read off a file.

    Returns:
        The value, safe to paste into a query.
    """
    return urllib.parse.quote(value, safe="")


def _fetch(url: str) -> object:
    """Ask MusicBrainz one question, paced, and retried once.

    Every request in this module goes through here, which is what keeps
    the one-a-second rate honest: an album that needs a barcode search
    and then a release fetch makes two, and pacing them in the loop above
    would leave the search unpaced.

    The retry is for the timeout that a paced run should not see anyway.
    A second attempt costs one interval; losing a whole album to one slow
    response costs a rerun.

    Args:
        url: The endpoint to read, already built and escaped.

    Returns:
        The response, as `object` -- the caller says which shape it
        expects, and a cast from `object` is the one a type checker will
        take.

    Raises:
        URLError: When both attempts fail or the service refuses them.
        TypeError: When the response is not a JSON object.
        ValueError: When the response is not JSON at all.
    """
    try:
        return _once(url)
    except (urllib.error.URLError, TimeoutError):
        return _once(url)


def _once(url: str) -> object:
    """Wait out the rate limit, then make one request.

    Args:
        url: The endpoint to read, already built and escaped.

    Returns:
        The response, checked only far enough to know it is an object.

    Raises:
        URLError: When the request fails or the service refuses it.
        TypeError: When the response is not a JSON object.
        ValueError: When the response is not JSON at all.
    """
    # One rate limit, shared by every caller, so the timestamp is the
    # module's rather than any one loop's.
    global _last_request

    waited: float = _INTERVAL - (time.monotonic() - _last_request)
    if waited > 0:
        time.sleep(waited)
    _last_request = time.monotonic()

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310 - the scheme is a module constant, not caller input

    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310 - same request, built above
        payload: object = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        msg = f"expected a JSON object, got {type(payload).__name__}"
        raise TypeError(msg)
    return payload


def _tracks_of(payload: object) -> Release:
    """Read one release's tracks, with every handle onto each.

    Keyed on nothing here: the caller decides which handle to match on,
    and it needs the whole list to check a track count before trusting a
    position.

    Args:
        payload: A release as MusicBrainz returned it.

    Returns:
        The release, tracks missing an id, a credit or a title left out.
    """
    release = cast("_Release", payload)
    found: list[ReleaseTrack] = []

    for disc, medium in enumerate(release.get("media", []), start=1):
        for position, track in enumerate(medium.get("tracks", []), start=1):
            track_id: str = track.get("id", "")
            artist: str = flatten(track.get("artist-credit", []))
            title: str = track.get("title", "").strip()
            if not (track_id and artist and title):
                continue
            found.append(
                ReleaseTrack(
                    track_id=track_id,
                    isrcs=frozenset(track.get("recording", {}).get("isrcs", [])),
                    disc=medium.get("position", disc),
                    position=track.get("position", position),
                    credited=Credited(artist=artist, title=title),
                )
            )

    return Release(tracks=tuple(found))
