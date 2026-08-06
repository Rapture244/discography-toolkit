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
    rename: Annotated[
        str | None,
        typer.Option(
            "--rename",
            help="Replace this genre with --genre everywhere beneath the path, tags and .genre alike.",
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
        rename: A genre to replace with `value` wherever it appears
            beneath the path -- in the tags and in the `.genre` files
            both, so a convention that changed is typed once.
        force: Delete every `.genre` beneath the path and declare the
            supplied value instead, ignoring what they said. What they
            currently hold is printed before the genre is asked for.
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

    if rename is not None:
        if force:
            _refuse("--rename edits the declarations; --force deletes them. Pick one.")
        _rename(target, tracks, artists, rename, value, dry_run=dry_run)
        raise typer.Exit(code=0)

    # ponytail: rglob does not prune dotted folders the way
    # `layout._visible_files` does, so a repository or a virtualenv
    # beneath the target would be walked. A discography holds neither.
    # Upgrade by pruning during the walk if this ever runs somewhere it
    # might.
    #
    # Gathered before the prompt rather than after: forcing is the one
    # step that destroys something hand-written, and being asked to
    # replace declarations without being shown them is how a considered
    # answer gets overwritten by a hasty one.
    doomed: list[Path] = sorted(target.rglob(SIDECAR_NAME)) if force else []
    _echo_doomed(doomed, target)

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

    # Nothing was asked for and the path declares its own genre, so every
    # track beneath it is already answered. The only useful question left
    # is whether that answer is still the right one -- which is the whole
    # of a convention changing, and the values are on screen to judge it
    # by.
    if value is None and not force and not fallback and (target / SIDECAR_NAME).is_file():
        own: str = declarations.value(target / SIDECAR_NAME)
        if typer.confirm(f"\nRename {_styled(own)} here and everywhere below?"):
            _rename(target, tracks, artists, own, None, dry_run=dry_run)
            raise typer.Exit(code=0)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.GENRE], _wants(declared, fallback), on_progress=advance)

    _echo_plan(plan)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    # A run writing no tag can still owe a declaration: the tags may
    # already be right while the file that keeps them right is missing,
    # and without it the next run asks all over again.
    if not pending and not fallback:
        # "its", not "this": with declarations in play there may be
        # several genres in one run, and no one of them is the run's.
        typer.secho("\nEvery file already carries its Genre. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm(f"\n{_summarize(pending, fallback, doomed, target, declared)}"):
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
#                                       RENAMING                                       #
# ==================================================================================== #
def _rename(
    target: Path,
    tracks: Sequence[Path],
    artists: Sequence[Path],
    old: str,
    new: str | None,
    *,
    dry_run: bool,
) -> None:
    """Replace one genre with another everywhere beneath a path.

    Both stores, in one pass. A convention changes once and the shelf
    holds it in two places -- the tags and the `.genre` files -- and
    typing the correction twice is how the two drift apart.

    Find-and-replace rather than a tagging run: neither store consults
    the other, each simply has the part swapped wherever it appears. That
    is what makes it predictable on a shelf where most values were never
    declared at all, which is most of them.

    Args:
        target: The folder the run is scoped to.
        tracks: The audio files in scope.
        artists: Artist folders beneath the target, for the progress bar.
        old: The genre to replace.
        new: What to replace it with; prompted for if omitted.
        dry_run: Report what would change without writing.

    Raises:
        typer.Exit: On an empty replacement, or a user abort.
    """
    if new is None:
        new = cast("str", typer.prompt(f"\nReplace {_styled(old)} with"))
    new = new.strip()
    if not new:
        _refuse("Genre cannot be empty.")

    edits: list[tuple[Path, str]] = _declarations_renamed(target, old, new)

    with make_progress() as progress:
        advance = make_advancer(progress, target.name, tracks, artists)
        plan = tagging.plan(tracks, [Tag.GENRE], _renaming(old, new), on_progress=advance)

    _echo_plan(plan)

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    pending: int = len(plan.pending)
    if not pending and not edits:
        typer.secho(f"\nNothing beneath this path carries {old!r}.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    summary: str = f"rename {_styled(old)} to {_styled(new)} in {pending} file(s)"
    if edits:
        summary = f"{summary} and {len(edits)} {SIDECAR_NAME} file(s)"
    if not typer.confirm(f"\nProceed to {summary}?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    failures: list[tuple[Path, str]] = []
    for sidecar, renamed in edits:
        try:
            _ = sidecar.write_text(f"{renamed}\n", encoding="utf-8", newline="\n")
        except OSError as exc:
            failures.append((sidecar, str(exc)))

    with make_progress() as progress:
        written = [outcome.path for outcome in plan.pending]
        advance = make_advancer(progress, target.name, written, artists)
        report = tagging.apply(plan, on_progress=advance)

    echo_result("Genre", report.written, "renamed", failures=[*failures, *report.failures])


def _declarations_renamed(target: Path, old: str, new: str) -> list[tuple[Path, str]]:
    """Work out which declarations carry the old genre, and what they become.

    Read now and written later, after the confirm, so a dry run says what
    would change without changing it.

    An unusable file is skipped rather than refused: a rename has nothing
    to match in a file it cannot read, and stopping the run over one
    would block a correction the rest of the shelf needs.

    Args:
        target: The folder the run is scoped to.
        old: The genre to replace.
        new: What to replace it with.

    Returns:
        `(path, contents)` for each declaration that would change.
    """
    edits: list[tuple[Path, str]] = []
    # ponytail: rglob does not prune dotted folders, as elsewhere here. A
    # discography holds no repository or virtualenv to walk into.
    for sidecar in sorted(target.rglob(SIDECAR_NAME)):
        try:
            current: str = declarations.value(sidecar)
        except declarations.UnusableDeclarationError:
            continue
        renamed: str = _swap(current, old, new)
        if renamed != current:
            edits.append((sidecar, renamed))
    return edits


def _renaming(old: str, new: str) -> tagging.Desired:
    """Build the value function: each track's own genre, with the part swapped.

    Reads `current` rather than a declaration, which is what separates a
    rename from a tagging run. Most of what needs correcting was never
    declared -- it came in with the rip -- so the file's own value is the
    only thing there is to work from.

    Args:
        old: The genre to replace.
        new: What to replace it with.

    Returns:
        A `Desired` callable reading the track's current Genre.
    """

    def desired(_track: Path, current: Mapping[Tag, str]) -> Mapping[Tag, str]:
        return {Tag.GENRE: _swap(current.get(Tag.GENRE, ""), old, new)}

    return desired


def _swap(current: str, old: str, new: str) -> str:
    """Replace one genre inside a value, leaving the others as they were.

    Part-wise, never a substring: "(JPN) Shakuhachi;Classical" renames
    its first half and leaves "Classical" alone, where a substring
    replacement would reach inside neighbouring values it was never meant
    to touch.

    A value with nothing to match comes back byte for byte, so a rename
    never quietly tidies the spacing of a file it had no business
    editing. One that does match is rebuilt tidied, and duplicates that
    the rename created are dropped -- renaming "(JP) Koto" onto a file
    already carrying "(JPN) Koto" leaves one of it, not two.

    Args:
        current: The value as stored.
        old: The genre to replace.
        new: What to replace it with.

    Returns:
        The value with `old` swapped for `new`, or `current` untouched.
    """
    parts: list[str] = [part.strip() for part in current.split(";")]
    if old not in parts:
        return current
    renamed: list[str] = [new if part == old else part for part in parts if part]
    return ";".join(dict.fromkeys(renamed))


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


def _echo_doomed(doomed: Sequence[Path], target: Path) -> None:
    """Say what the declarations beneath the path currently hold.

    Printed before the genre is asked for, because the answer often
    starts from what is already there -- a value to extend rather than
    replace outright. Being asked to overwrite something unseen is how a
    considered declaration gets lost to a hastier one.

    An unusable file is named rather than refused. Forcing deletes it
    either way, so stopping the run over a file that is on its way out
    would be a refusal with nothing behind it.

    Args:
        doomed: The declarations that would be deleted.
        target: The folder the run is scoped to, which they are named
            relative to.
    """
    if not doomed:
        return

    holdings: list[tuple[str, str]] = []
    for sidecar in doomed:
        try:
            current: str = declarations.value(sidecar)
        except declarations.UnusableDeclarationError:
            current = "(unusable)"
        holdings.append((current, str(sidecar.relative_to(target))))

    label: str = typer.style("Replacing ->", fg=typer.colors.YELLOW, bold=True)
    width: int = max(len(repr(current)) for current, _ in holdings)

    typer.echo()
    for current, where in holdings:
        source: str = typer.style(where, fg=typer.colors.BRIGHT_BLACK)
        typer.echo(f"{label} {_styled(current, width)}   {source}")


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
def _styled(genre: str, width: int = 0) -> str:
    """Render a genre so it reads apart from the words around it.

    The value is the only part of these lines anyone is actually reading
    -- the prose is scaffolding. Left plain it disappears into the
    sentence, which matters most in the prompts, where it is the thing
    being agreed to.

    Padded before it is styled, never after: the escape codes count
    towards a format width and would pull a column out of line.

    Bright magenta because the palette is spoken for -- green is changed,
    cyan is a dry run, yellow a warning -- and a genre value is none of
    those, it is the subject the line is about. Plain magenta means a
    total on a result line; the bright variant carries no meaning yet.

    Args:
        genre: The value to render.
        width: Column width to pad to, or none.

    Returns:
        The quoted value, styled.
    """
    return typer.style(f"{genre!r:<{width}}", fg=typer.colors.BRIGHT_MAGENTA, bold=True)


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
        typer.echo(f"{label} {_styled(value, width)}   {count:>{digits}} file(s)   {source}")


def _summarize(
    pending: int,
    fallback: str,
    doomed: Sequence[Path],
    target: Path,
    declared: Mapping[Path, Declaration],
) -> str:
    """Phrase the confirmation, naming everything the run would do.

    The deletions especially: they are the one thing here that destroys
    something hand-written, and a person cannot weigh a prompt that does
    not mention them.

    Args:
        pending: How many tracks need their tag written.
        fallback: The value that would be declared, empty when none.
        doomed: Declarations that would be deleted.
        target: The folder the declaration would be left in.
        declared: The declaration reaching each folder holding tracks.

    Returns:
        The question to put, ending in a question mark.
    """
    parts: list[str] = []
    if pending:
        parts.append(_writing(pending, fallback, declared))
    if doomed:
        parts.append(f"delete {len(doomed)} existing {SIDECAR_NAME} file(s)")
    if fallback:
        parts.append(f"declare {_styled(fallback)} in {target.name!r}")

    return f"Proceed to {', '.join(parts)}?"


def _writing(pending: int, fallback: str, declared: Mapping[Path, Declaration]) -> str:
    """Phrase the tag-writing half, saying what is being written where it can.

    "Write Genre to 47 file(s)" reads as a fresh instruction, arriving
    three lines under a declaration it never mentions. What is actually
    happening is narrower: those 47 do not yet carry what their folder
    already says, and the other 151 do.

    So the genre is named when a run has only one, which is the common
    case and the one where the question is otherwise most disconnected
    from the answer above it. Where a shelf declares several, no single
    value is the run's, and the reconciliation is described instead.

    Args:
        pending: How many tracks need their tag written.
        fallback: The supplied value, empty when everything was declared.
        declared: The declaration reaching each folder holding tracks.

    Returns:
        The clause, without a leading capital or a question mark.
    """
    # A supplied value is already spelled out by the "declare X in Y"
    # clause beside this one, so naming it twice in one sentence would
    # be noise rather than clarity.
    if fallback:
        return f"write Genre to {pending} file(s)"

    genres: set[str] = {declaration.genre for declaration in declared.values()}
    if len(genres) == 1:
        return f"write {_styled(next(iter(genres)))} to {pending} file(s)"
    return f"bring {pending} file(s) in line with their declarations"


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
