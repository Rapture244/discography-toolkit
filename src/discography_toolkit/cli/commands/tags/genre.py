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

# Runtime import, not a type-checking one: Typer resolves annotations with
# get_type_hints() when it builds the command, so every name used in a
# signature has to exist in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, NoReturn, cast

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
from discography_toolkit.core import declarations
from discography_toolkit.core.declarations import SIDECAR_NAME
from discography_toolkit.core.metadata import Tag
from discography_toolkit.operations import tagging

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from discography_toolkit.core.declarations import Declaration


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# The declaration itself lives in `core.declarations`, shared with
# `list genres`: a survey that resolved a genre differently from the
# command that writes it would leave two answers to one question.


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
        typer.Option(
            "--force",
            help="Delete every .genre beneath the path and declare one genre for it all.",
        ),
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
    declared: Mapping[Path, Declaration] = {} if force else _resolved(tracks, target)

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

    _echo_values(tracks, declared, fallback, target)

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
        # "its", not "this": with declarations in play there may be
        # several genres in one run, and no one of them is the run's.
        typer.secho("\nEvery file already carries its Genre. Nothing to do.", fg=typer.colors.GREEN)
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
def _resolved(tracks: Sequence[Path], ceiling: Path) -> Mapping[Path, Declaration]:
    """Resolve what each folder declares, turning a bad file into an exit.

    The resolution is `core.declarations`', shared with `list genres`.
    Only the refusal is this command's: core raises, a command stops.

    Args:
        tracks: The audio files in scope.
        ceiling: The folder the search stops at.

    Returns:
        The declaration reaching each folder that holds tracks.

    Raises:
        typer.Exit: If a `.genre` in the way cannot be read as one genre.
    """
    try:
        return declarations.resolve(tracks, ceiling)
    except declarations.UnusableDeclarationError as exc:
        _refuse(str(exc))


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
            # Written LF, not the platform's line ending: `.editorconfig`
            # asks for a final newline and for `end_of_line = lf`, and
            # text mode would translate this to CRLF on Windows.
            _ = declaration.write_text(f"{fallback}\n", encoding="utf-8", newline="\n")
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
def _wants(declared: Mapping[Path, Declaration], fallback: str) -> tagging.Desired:
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
        declaration: Declaration | None = declared.get(track.parent)
        return {Tag.GENRE: fallback if declaration is None else declaration.genre}

    return desired


def _echo_values(
    tracks: Sequence[Path],
    declared: Mapping[Path, Declaration],
    fallback: str,
    target: Path,
) -> None:
    """Say what each group of tracks is getting, and which file decided it.

    One line where a run is one genre, which is the common case. More
    where the shelf declares more -- and each names its source, because a
    hidden file is otherwise unanswerable when an album keeps coming out
    wrong.

    The source is named relative to the target, which the banner has
    already printed in full: an absolute path here is a hundred
    characters of which the last seven carry the information, and a line
    that long is skipped rather than read. Dimmed for the same reason
    every other supporting detail is -- the palette is spoken for, and
    another colour would read as a status.

    Args:
        tracks: The audio files in scope.
        declared: The declaration reaching each folder holding tracks.
        fallback: The value for folders none reaches.
        target: The folder the run is scoped to, which sources are named
            relative to.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for track in tracks:
        declaration: Declaration | None = declared.get(track.parent)
        if declaration is None:
            counts[fallback, "supplied"] += 1
        else:
            counts[declaration.genre, str(declaration.source.relative_to(target))] += 1

    label: str = typer.style("Genre ->", fg=typer.colors.GREEN, bold=True)
    # Both columns padded to their longest, so a run declaring several
    # genres reads down rather than zig-zagging. Counts right-aligned and
    # values left, the way `console.FileCountColumn` pads its own counter.
    width: int = max((len(repr(value)) for value, _ in counts), default=0)
    digits: int = max((len(str(count)) for count in counts.values()), default=0)

    typer.echo()
    for (value, origin), count in sorted(counts.items()):
        source: str = typer.style(origin, fg=typer.colors.BRIGHT_BLACK)
        typer.echo(f"{label} {value!r:<{width}}   {count:>{digits}} file(s)   {source}")


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
