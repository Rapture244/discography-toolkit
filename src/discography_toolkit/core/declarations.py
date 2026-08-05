# src/discography_toolkit/core/declarations.py
"""What a folder declares about the music beneath it.

Genre is the one tag the folders do not determine, so it has to be asked
for -- and asking every run is what a `.genre` file avoids. A folder
holding one declares that genre for everything beneath it, and the
nearest declaration wins, so an artist can be settled once and one of its
albums differ.

Two commands read these now: `tags genre` to decide what to write, and
`list genres` to say what is in use. They must resolve identically. A
survey that disagreed with the command that writes would leave two
answers to one question and no way to tell which to trust -- so the
resolution lives here rather than in either of them.

Nothing here writes or prints. Declaring a genre belongs to the command
that asked for one; this only reads what is already on the shelf.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Dotted, like every other declaration kept beside its subject --
# ".python-version", ".editorconfig". It also means `layout._visible_files`
# skips it by rule rather than by luck: that walk prunes dotted names, so
# a declaration can never be mistaken for a track.
SIDECAR_NAME: Final[str] = ".genre"

# Phrased here rather than at the raise, so the message lives with the
# exception that carries it.
_EMPTY: Final[str] = "is empty. Delete it, or give it a genre."
_MULTILINE: Final[str] = "holds more than one line. A declaration is one genre."
_UNREADABLE: Final[str] = "could not be read"


class UnusableDeclarationError(ValueError):
    """A `.genre` file that cannot be read as a single genre.

    A hand-written file is untrusted input: it can be empty, hold a paste
    of several lines, or not be text at all. None of those has an obvious
    meaning, and guessing one would write it into every track beneath the
    folder -- so each is refused by name instead.

    Attributes:
        sidecar: The file that could not be used.
    """

    def __init__(self, sidecar: Path, problem: str) -> None:
        """Initialize the error.

        Args:
            sidecar: The file that could not be used.
            problem: What is wrong with it, phrased to follow the path.
        """
        self.sidecar: Path = sidecar
        super().__init__(f"{str(sidecar)!r} {problem}")


@dataclass(frozen=True, slots=True)
class Declaration:
    """A genre a folder declares, and the file that declares it.

    The source is carried because the value alone cannot be argued with:
    a hidden file settling an album's genre is only debuggable if the run
    says which one it read.

    Attributes:
        genre: The declared value, written verbatim.
        source: The `.genre` file it came from.
    """

    genre: str
    source: Path


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def resolve(tracks: Sequence[Path], ceiling: Path) -> dict[Path, Declaration]:
    """Resolve the declaration reaching each folder that holds tracks.

    Keyed on the folder rather than the track, which is what keeps the
    walk cheap: an album's twenty tracks share one parent and so one
    lookup, instead of twenty identical climbs up the same shelf.

    Args:
        tracks: The audio files in scope.
        ceiling: The folder the search stops at -- the run's own path,
            since a declaration above it is outside what was asked for.

    Returns:
        The declaration reaching each folder, folders reached by none
        being absent rather than present and empty.

    Raises:
        UnusableDeclarationError: If a `.genre` in the way cannot be read
            as one genre.
    """
    found: dict[Path, Declaration] = {}
    for folder in {track.parent for track in tracks}:
        declaration: Declaration | None = nearest(folder, ceiling)
        if declaration is not None:
            found[folder] = declaration
    return found


def nearest(folder: Path, ceiling: Path) -> Declaration | None:
    """Find the declaration nearest a folder, climbing no higher than the ceiling.

    Nearest wins, which is the whole of the precedence rule: an album's
    own file beats its artist's, and the artist's beats the shelf's,
    without anything having to rank them.

    Args:
        folder: The folder holding the tracks.
        ceiling: The last folder to look in, inclusive.

    Returns:
        The declaration found, or `None` when none reaches the folder.

    Raises:
        UnusableDeclarationError: If the file found cannot be read as one
            genre.
    """
    for parent in (folder, *folder.parents):
        sidecar: Path = parent / SIDECAR_NAME
        if sidecar.is_file():
            return Declaration(genre=value(sidecar), source=sidecar)
        if parent == ceiling:
            return None
    return None


# ==================================================================================== #
#                                 READING ONE FILE                                     #
# ==================================================================================== #
def value(sidecar: Path) -> str:
    """Read one declaration, refusing anything that is not a single genre.

    Public because a known file sometimes has to be read rather than
    found: replacing the declarations beneath a path means saying what
    they hold first, and nobody should be asked to overwrite something
    they cannot see.

    A trailing newline is not a fault: `.editorconfig` sets
    `insert_final_newline`, so an editor honouring it adds one to every
    file here. Stripping is required, not tidiness.

    Args:
        sidecar: The `.genre` file to read.

    Returns:
        The declared genre, verbatim but for surrounding whitespace.

    Raises:
        UnusableDeclarationError: If the file cannot be read, is empty,
            or holds more than one line.
    """
    try:
        raw: str = sidecar.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UnusableDeclarationError(sidecar, f"{_UNREADABLE} - {exc}") from exc

    declared: str = raw.strip()
    if not declared:
        raise UnusableDeclarationError(sidecar, _EMPTY)
    if "\n" in declared:
        raise UnusableDeclarationError(sidecar, _MULTILINE)
    return declared
