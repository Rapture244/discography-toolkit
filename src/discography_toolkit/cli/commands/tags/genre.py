# src/discography_toolkit/cli/commands/tags/genre.py
"""The `rapt tags genre` command.

Pairs with `operations.tagging`: that plans and writes, this decides what
a genre run looks like. The operation returns a result and prints
nothing, so every rendering decision lives here.

The value is written verbatim and never parsed. "Jazz;Jazz Fusion" is
one string in one tag; whether a player reads that as two genres depends
on the separator it is configured with, which keeps the choice a display
setting rather than something baked into the files. A `.genre` file
holds exactly the same kind of string, for exactly the same reason.

Genre is the one tag the folders do not determine, so it is asked for
rather than derived. Asking every time is what a `.genre` file avoids:
a folder declaring its genre answers for every track beneath it, and the
nearest declaration wins, so an artist can be settled once and one of
its albums differ. What is supplied on the command line is the fallback
for tracks no declaration reaches -- and is left behind as the
declaration for next time, at whatever level the run was scoped to.

`--force` is the way back out: it clears the declarations beneath the
target and writes one, which is the only operation here that destroys
something a person wrote by hand.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# Runtime import, not a type-checking one: Typer resolves annotations with
# get_type_hints() when it builds the command, so every name used in a
# signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Final, NoReturn, cast

import typer

from discography_toolkit.cli.commands.tags.notices import unreadable
from discography_toolkit.cli.console import (
    artist_names,
    echo_banner,
    echo_result,
    make_advancer,
    make_progress,
)
from discography_toolkit.cli.scope import artists_in, require_tracks, resolve_path
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Dotted, like every other declaration this repo keeps beside its
# subject -- ".python-version", ".editorconfig". It also means
# `layout._visible_files` skips it by rule rather than by luck: that walk
# prunes dotted names, so a declaration can never read as a track.
SIDECAR_NAME: Final[str] = ".genre"


@dataclass(frozen=True, slots=True)
class _Declaration:
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
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def genre(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to tag beneath. Any level: album, artist, or a whole shelf.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    value: Annotated[
        str | None,
        typer.Option(
            "--genre",
            "-g",
            help='Fallback for tracks no .genre declares. Quote it: "Jazz;Fusion".',
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Delete every .genre beneath the path and declare this one."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without writing."),
    ] = False,
) -> None:
    """Set the Genre tag on every audio file beneath a path.

    A `.genre` file declares the genre for everything beneath the folder
    holding it, nearest winning, searched no higher than the path given.
    Tracks it does not reach take the supplied value, which is then left
    at the path as a declaration -- so the same run twice asks once.

    Args:
        path: Folder to tag beneath; prompted for if omitted.
        value: Fallback for tracks no `.genre` declares, written exactly
            as given; prompted for when one is needed and none was given.
        force: Delete every `.genre` beneath the path and declare the
            supplied value instead, ignoring what they said.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an invalid path, an empty or unreadable genre, no
            audio found, a user abort, or a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to tag beneath")
    # Reporting only. Every audio file beneath the target is tagged
    # whether or not it sits under a recognized artist -- the path is a
    # scope, and filtering here would mean pointing at one album and
    # tagging nothing.
    artists: list[Path] = artists_in(target)
    echo_banner("Metadata: Genre", target.name, children=artist_names(target, artists))

    # Before the prompt, not after: whether to ask at all depends on what
    # the shelf already declares, which cannot be known without the
    # tracks in hand.
    tracks: list[Path] = require_tracks(target)

    # Forcing replaces every declaration, so none is read -- the supplied
    # value answers for each track rather than losing to a file that is
    # about to be deleted.
    declared: Mapping[Path, _Declaration] = {} if force else _declarations(tracks, target)

    fallback: str = ""
    if force or any(track.parent not in declared for track in tracks):
        if value is None:
            value = cast(
                "str", typer.prompt('\nEnter the genre (e.g. "Jazz" or "Jazz;Jazz Fusion")')
            )
        fallback = value.strip()
        if not fallback:
            typer.secho("\nGenre cannot be empty.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

    _echo_values(tracks, declared, fallback)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.GENRE], _wants(declared, fallback), on_progress=advance)

    _echo_plan(plan)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    # ponytail: rglob does not prune dotted folders the way
    # `layout._visible_files` does, so a repository or a virtualenv
    # beneath the target would be walked. A discography holds neither.
    # Upgrade by pruning during the walk if this ever runs somewhere it
    # might.
    doomed: list[Path] = sorted(target.rglob(SIDECAR_NAME)) if force else []

    pending: int = len(plan.pending)
    # A run writing no tag can still owe a declaration: the tags may
    # already be right while the file that keeps them right is missing,
    # and without it the next run asks all over again.
    if not pending and not fallback:
        typer.secho(
            "\nEvery file already carries this Genre. Nothing to do.", fg=typer.colors.GREEN
        )
        raise typer.Exit(code=0)

    if not typer.confirm(f"\n{_summarize(pending, fallback, doomed, target)}"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Declarations are settled before the tags rather than after, so an
    # interrupted run leaves the shelf saying what it was told rather
    # than tagged with a genre nothing on disk explains.
    failures: list[tuple[Path, str]] = _declare(doomed, target, fallback)

    with make_progress() as progress:
        written = [outcome.path for outcome in plan.pending]
        advance = make_advancer(progress, target.name, written, artists)
        report = tagging.apply(plan, on_progress=advance)

    echo_result("Genre", report.written, "tagged", failures=[*failures, *report.failures])


# ==================================================================================== #
#                                     DECLARATIONS                                     #
# ==================================================================================== #
def _declarations(tracks: Sequence[Path], ceiling: Path) -> dict[Path, _Declaration]:
    """Resolve the genre declared for each folder holding tracks.

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
    """
    found: dict[Path, _Declaration] = {}
    for folder in {track.parent for track in tracks}:
        declaration: _Declaration | None = _nearest(folder, ceiling)
        if declaration is not None:
            found[folder] = declaration
    return found


def _nearest(folder: Path, ceiling: Path) -> _Declaration | None:
    """Find the declaration nearest a folder, climbing no higher than the ceiling.

    Nearest wins, which is the whole of the precedence rule: an album's
    own file beats its artist's, and the artist's beats the shelf's,
    without anything having to rank them.

    Args:
        folder: The folder holding the tracks.
        ceiling: The last folder to look in, inclusive.

    Returns:
        The declaration found, or `None` when none reaches the folder.
    """
    for parent in (folder, *folder.parents):
        sidecar: Path = parent / SIDECAR_NAME
        if sidecar.is_file():
            return _Declaration(genre=_read(sidecar), source=sidecar)
        if parent == ceiling:
            return None
    return None


def _read(sidecar: Path) -> str:
    """Read one declaration, refusing anything that is not a single genre.

    A hand-written file is untrusted input: it can be empty, hold a
    paste of several lines, or not be text at all. None of those has an
    obvious meaning, and guessing one would write it into every track
    beneath the folder -- so each is refused by name instead.

    A trailing newline is not one of them: `.editorconfig` sets
    `insert_final_newline`, so an editor honouring it adds one to every
    file here. Stripping is required, not tidiness.

    Args:
        sidecar: The `.genre` file to read.

    Returns:
        The declared genre, verbatim but for surrounding whitespace.

    Raises:
        typer.Exit: If the file cannot be read, is empty, or holds more
            than one line.
    """
    try:
        raw: str = sidecar.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _refuse(f"{str(sidecar)!r} could not be read - {exc}")

    value: str = raw.strip()
    if not value:
        _refuse(f"{str(sidecar)!r} is empty. Delete it, or give it a genre.")
    if "\n" in value:
        _refuse(f"{str(sidecar)!r} holds more than one line. A declaration is one genre.")
    return value


def _declare(doomed: Sequence[Path], target: Path, fallback: str) -> list[tuple[Path, str]]:
    """Clear the declarations a forced run replaces, and leave the new one.

    Writes nothing when the run needed no fallback: every track was
    already answered by a file that is staying exactly where it is.

    Args:
        doomed: Declarations to delete, empty unless forcing.
        target: The folder the run was scoped to, where the declaration
            is left.
        fallback: The value to declare, empty when none was needed.

    Returns:
        `(path, reason)` for each file that would not be written or
        deleted -- reported rather than raised, so one locked folder does
        not cost the run its tags.
    """
    failures: list[tuple[Path, str]] = []

    for sidecar in doomed:
        try:
            sidecar.unlink()
        except OSError as exc:
            failures.append((sidecar, str(exc)))

    if fallback:
        declaration: Path = target / SIDECAR_NAME
        try:
            # Trailing newline to match `.editorconfig`, which asks for
            # one in every file here.
            _ = declaration.write_text(f"{fallback}\n", encoding="utf-8")
        except OSError as exc:
            failures.append((declaration, str(exc)))

    return failures


def _refuse(message: str) -> NoReturn:
    """Stop the run, saying which file was wrong and why.

    Args:
        message: What to print.

    Raises:
        typer.Exit: Always.
    """
    typer.secho(f"\n{message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _wants(declared: Mapping[Path, _Declaration], fallback: str) -> tagging.Desired:
    """Build the value function: what each track's folder was told to hold.

    One function for both shapes. A forced run passes no declarations, so
    every track falls back to the supplied value; an unforced one falls
    back only where nothing declared anything.

    Args:
        declared: The declaration reaching each folder holding tracks.
        fallback: The value for folders none reaches.

    Returns:
        A `Desired` callable reading the track's folder, not its tags.
    """

    def desired(track: Path, _current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        declaration: _Declaration | None = declared.get(track.parent)
        return {Tag.GENRE: fallback if declaration is None else declaration.genre}

    return desired


def _echo_values(
    tracks: Sequence[Path],
    declared: Mapping[Path, _Declaration],
    fallback: str,
) -> None:
    """Say what each group of tracks is getting, and which file decided it.

    One line where a run is one genre, which is the common case and reads
    as it always did. More where the shelf declares more -- and each
    names its source, because a hidden file is otherwise unanswerable
    when an album keeps coming out wrong.

    Args:
        tracks: The audio files in scope.
        declared: The declaration reaching each folder holding tracks.
        fallback: The value for folders none reaches.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for track in tracks:
        declaration: _Declaration | None = declared.get(track.parent)
        if declaration is None:
            counts[fallback, ""] += 1
        else:
            counts[declaration.genre, str(declaration.source)] += 1

    label: str = typer.style("Genre ->", fg=typer.colors.GREEN, bold=True)
    typer.echo()
    for (value, source), count in sorted(counts.items()):
        origin: str = source or "supplied"
        typer.echo(f"{label} {value!r}  ({count} file(s), {origin})")


def _summarize(pending: int, fallback: str, doomed: Sequence[Path], target: Path) -> str:
    """Phrase the confirmation, naming everything the run would do.

    The deletions especially: they are the one thing here that destroys
    something hand-written, and a person cannot weigh a prompt that does
    not mention them.

    Args:
        pending: How many tracks need their tag written.
        fallback: The value that would be declared, empty when none.
        doomed: Declarations that would be deleted.
        target: The folder the declaration would be left in.

    Returns:
        The question to put, ending in a question mark.
    """
    parts: list[str] = []
    if pending:
        parts.append(f"write Genre to {pending} file(s)")
    if doomed:
        parts.append(f"delete {len(doomed)} existing {SIDECAR_NAME} file(s)")
    if fallback:
        parts.append(f"declare {fallback!r} in {target.name!r}")

    return f"Proceed to {', '.join(parts)}?"


def _echo_plan(plan: tagging.TagPlan) -> None:
    """Render the plan as one line, with any unreadable files beneath it.

    Nothing here can be underivable, still: a track no declaration
    reaches takes the supplied value, and a run where some track needs
    one refuses to start without it. So every track has a value, and the
    only thing left to report is a file that would not open.

    Args:
        plan: The plan to summarize.
    """
    notice = unreadable(plan)

    echo_result("Genre", len(plan.pending), "to tag", [] if notice is None else [notice])
