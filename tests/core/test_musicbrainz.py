# tests/core/test_musicbrainz.py
"""Tests for reading a release out of a MusicBrainz response.

The fetching is not exercised -- a test that needs the network is a test
that fails on a train. What is exercised is everything after it: how a
credit's parts and join phrases become one string, and the three handles
onto a track that the matching ladder above chooses between.
"""

from __future__ import annotations

from discography_toolkit.core.musicbrainz import (
    Credited,
    _tracks_of,  # pyright: ignore[reportPrivateUsage]
    flatten,
)

import pytest


# ==================================================================================== #
#                                   ARTIST CREDITS                                     #
# ==================================================================================== #
@pytest.mark.parametrize(
    ("credit", "expected"),
    [
        # The join phrase is the whole point: "feat." is a guest on
        # someone's record and "&" is a duo, and a bare list of names
        # would say the same thing of both.
        (
            [
                {"name": "Common", "joinphrase": " feat. "},
                {"name": "Lauryn Hill", "joinphrase": ""},
            ],
            "Common feat. Lauryn Hill",
        ),
        (
            [
                {"name": "Apollo Brown", "joinphrase": " & "},
                {"name": "Ty Farris", "joinphrase": ""},
            ],
            "Apollo Brown & Ty Farris",
        ),
        ([{"name": "Common", "joinphrase": ""}], "Common"),
        # A trailing phrase must not leave whitespace hanging off the end.
        ([{"name": "Common", "joinphrase": " feat. "}], "Common feat."),
        # A part missing its name still contributes its phrase, for the
        # same reason the line above keeps a trailing "feat.": what the
        # release printed is kept, and inventing a tidier reading of a
        # malformed credit would hide that it was malformed.
        ([{"name": "Common"}, {"joinphrase": " & "}], "Common &"),
        ([], ""),
    ],
)
def test_a_credit_flattens_with_its_join_phrases(
    credit: list[dict[str, str]], expected: str
) -> None:
    """A credit is rendered the way the release itself printed it.

    Args:
        credit: The credit as MusicBrainz returned it.
        expected: The one string it should become.
    """
    assert flatten(credit) == expected


# ==================================================================================== #
#                                     RELEASES                                         #
# ==================================================================================== #
def test_a_track_carries_its_credit_and_its_title() -> None:
    """Both halves of one fact, out of the one response.

    MusicBrainz keeps a featured artist in the credit and out of the
    title, so the two have to be written together -- a file taking the
    credit alone would name the guest twice, once there and once in the
    "(Feat. ...)" its own title still carries.
    """
    release = _tracks_of(
        {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {
                            "id": "t1",
                            "title": "Punch Drunk Love",
                            "position": 1,
                            "artist-credit": [
                                {"name": "Common", "joinphrase": " feat. "},
                                {"name": "Kanye West"},
                            ],
                        },
                        # Missing either half is not a reason to fail the
                        # release, but neither is it something to write.
                        {"id": "t2", "position": 2, "artist-credit": [{"name": "Common"}]},
                        {"id": "t3", "position": 3, "title": "Inhale"},
                    ],
                }
            ]
        }
    )

    assert [track.track_id for track in release.tracks] == ["t1"]
    assert release.tracks[0].credited == Credited(
        artist="Common feat. Kanye West", title="Punch Drunk Love"
    )


def test_a_track_carries_the_isrcs_of_its_recording() -> None:
    """The handle the barcode rung matches on, when the file has one.

    An ISRC names the recording, so it finds the track without anyone
    having to trust a position.
    """
    release = _tracks_of(
        {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {
                            "id": "t1",
                            "title": "Resurrection",
                            "position": 1,
                            "recording": {"isrcs": ["USRE49800111"]},
                            "artist-credit": [{"name": "Common"}],
                        },
                        # A release with no ISRCs at all is the ordinary
                        # case, and the reason the position rung exists.
                        {
                            "id": "t2",
                            "title": "Watermelon",
                            "position": 2,
                            "artist-credit": [{"name": "Common"}],
                        },
                    ],
                }
            ]
        }
    )

    assert release.tracks[0].isrcs == frozenset({"USRE49800111"})
    assert release.tracks[1].isrcs == frozenset()


def test_the_discs_of_a_set_keep_their_own_numbering() -> None:
    """Position alone is not a handle: two discs both start at one.

    The position rung matches on `(disc, position)` for exactly this
    reason -- a box set whose second disc restarts the count would
    otherwise credit every track of it from the first.
    """
    release = _tracks_of(
        {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {
                            "id": "a",
                            "title": "One",
                            "position": 1,
                            "artist-credit": [{"name": "Nujabes"}],
                        }
                    ],
                },
                {
                    "position": 2,
                    "tracks": [
                        {
                            "id": "b",
                            "title": "Two",
                            "position": 1,
                            "artist-credit": [{"name": "Nujabes"}],
                        }
                    ],
                },
            ]
        }
    )

    assert [(track.disc, track.position) for track in release.tracks] == [(1, 1), (2, 1)]
