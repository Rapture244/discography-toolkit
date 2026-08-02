# src/discography_toolkit/cli/commands/layout.py
"""The `rapt layout` command.

Lays out a discography's folders: it renames each album from its parts,
renumbers them into one sequence, title-cases every track filename, files
each album on the correct side of the FLAC container, and labels the
artist with a breakdown of what it holds.

The five run as a protocol, not five passes that could be reordered
freely. Each reads what the last one wrote -- numbering sorts on the year
that naming puts at the front, the label counts what placement has
settled -- so a step has to be applied before the next can plan against
it. That is also why there is no dry run: a preview of step two would be
a preview of a tree that does not exist yet, since step one has not run.
The single confirmation stands in for it.

An artist is checked before any of it runs, and skipped whole where the
check fails. Two FLAC containers cannot be merged without guessing. An
album held twice cannot be numbered into one sequence -- one copy would
take a place that is not its own, and which to keep is not the tool's
call. And a name the rebuild would not settle into the convention is
worth a person's eye rather than a shelf that quietly reads wrong:
naming plans without writing, so what it would produce can be held to
the pattern before a single folder moves.

Skipping before the first write is what keeps a refused artist untouched
rather than half laid out, and the run carries on to the rest.

Artists are found by their audio rather than a label, since the label is
what this pass creates -- an artist has none until it has been here. An
artist whose albums are every one an empty placeholder holds no audio to
find, and is left for a path pointed straight at it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Runtime import, not a type-checking one: Typer reads a command's
# annotations at registration, so every name in the signature must exist
# in module globals.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from discography_toolkit.cli.console import Notice, echo_banner, echo_notices
from discography_toolkit.cli.parameters import resolve_path
from discography_toolkit.core import names
from discography_toolkit.core.layout import (
    discover_albums,
    find_artists,
    find_audio_files,
    find_containers,
)
from discography_toolkit.operations import (
    artist_label,
    naming,
    numbering,
    placement,
    pruning,
    track_naming,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


# ==================================================================================== #
#                                     RESULT TYPES                                     #
# ==================================================================================== #
@dataclass(frozen=True, slots=True)
class Skipped:
    """An artist the pass refused, and why.

    Attributes:
        artist: The artist folder, untouched.
        reason: The short phrase naming the problem, for the line and the
            closing summary.
        details: The specifics a person needs to go and fix it -- the
            colliding folder names, the album that would not settle.
            Empty where the reason says all there is.
    """

    artist: Path
    reason: str
    details: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class ArtistResult:
    """What laying out one artist changed.

    Attributes:
        artist: The artist folder after the run, renamed by the label.
        pruned: Opus albums deleted for duplicating a lossless one.
        named: Album folders renamed.
        numbered: Album folders renumbered.
        cased: Track filenames recased.
        moved: Albums moved across the container.
        labelled: Whether the artist folder was renamed.
        failures: How many individual operations failed.
        notices: What the pass saw but would not act on, each phrased,
            counted, and naming the folders it means. Not changes --
            things a person has to look at, gathered into one list so a
            new kind of notice costs a line here rather than a field, a
            render and a tally.
    """

    artist: Path
    pruned: int
    named: int
    numbered: int
    cased: int
    moved: int
    labelled: bool
    failures: int
    notices: tuple[Notice, ...] = ()

    @property
    def changed(self) -> bool:
        """Whether anything about the artist changed."""
        return bool(
            self.pruned or self.named or self.numbered or self.cased or self.moved or self.labelled
        )


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
def layout(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Folder to lay out. A shelf of artists, or a single artist.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation and lay out straight away."),
    ] = False,
) -> None:
    """Rename, renumber, recase, file, and label a discography's folders.

    Args:
        path: Folder to lay out; prompted for if omitted.
        assume_yes: Proceed without asking to confirm.

    Raises:
        typer.Exit: On an invalid path, no artist found, a user abort, or
            a completed run.
    """
    target: Path = resolve_path(path, "Enter the absolute path to lay out")
    artists: list[Path] = find_artists(target)
    echo_banner("Layout", target.name, children=[artist.name for artist in artists])

    if not artists:
        typer.secho(
            f"\nNo artist folder found at or beneath {target.name!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Artists are recognised by their audio -- point at one holding tracks.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo()
    warning: str = f"This will delete Opus albums that duplicate a FLAC one, then rename, renumber, recase, file, and label {len(artists)} artist(s) -- there is no dry run."
    typer.secho(warning, fg=typer.colors.YELLOW)
    if not assume_yes and not typer.confirm("Proceed?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo()
    results, skipped = run(artists)
    _echo_summary(results, skipped)


def run(artists: Sequence[Path]) -> tuple[list[ArtistResult], list[Skipped]]:
    """Lay out each artist in turn, printing its line as it finishes.

    The loop the command runs after confirming, lifted out so the
    organize command can drive it too without a second confirmation.

    Args:
        artists: The artist folders to lay out.

    Returns:
        The result for each artist laid out, and one entry per artist
        refused before anything was written.
    """
    results: list[ArtistResult] = []
    skipped: list[Skipped] = []
    for artist in artists:
        refusal: Skipped | None = _check(artist)
        if refusal is not None:
            skipped.append(refusal)
            _echo_skip(refusal)
            continue
        result: ArtistResult = _lay_out(artist)
        results.append(result)
        _echo_artist(result)

    return results, skipped


# ==================================================================================== #
#                                     THE GUARDS                                       #
# ==================================================================================== #
def _check(artist: Path) -> Skipped | None:
    """Decide whether an artist can be laid out at all.

    Runs before the first write, so an artist that fails is left exactly
    as it was rather than part-way through the protocol.

    Args:
        artist: The artist folder to check.

    Returns:
        The refusal, or `None` when the artist is fit to lay out.
    """
    if len(find_containers(artist)) > 1:
        # Two containers cannot be merged without guessing which is the
        # real one, so the artist is skipped whole.
        return Skipped(artist=artist, reason="more than one FLAC container")

    repeats: tuple[tuple[Path, ...], ...] = _unresolved_duplicates(artist)
    if repeats:
        return Skipped(
            artist=artist,
            reason=f"{len(repeats)} album(s) held more than once",
            details=tuple(_describe(group) for group in repeats),
        )

    unsettled: tuple[str, ...] = _unsettled_names(artist)
    if unsettled:
        return Skipped(
            artist=artist,
            reason=f"{len(unsettled)} album(s) would not settle into the pattern",
            details=unsettled,
        )

    return None


def _unresolved_duplicates(artist: Path) -> tuple[tuple[Path, ...], ...]:
    """Find albums held twice that pruning will not settle by itself.

    Pruning's own duplicates are excluded, since an Opus copy of a
    lossless album has an answer and is about to get it. What is left is
    two copies neither of which can remake the other, and choosing
    between them means looking at tags, artwork or a mis-ripped track --
    a judgement, not a rule.

    Args:
        artist: The artist folder to inspect.

    Returns:
        One tuple per repeated identity, each holding the folders that
        share it.
    """
    albums: list[Path] = discover_albums(artist)
    doomed: set[Path] = {prune.album for prune in pruning.plan(albums).prunes}
    return pruning.duplicates([album for album in albums if album not in doomed])


def _unsettled_names(artist: Path) -> tuple[str, ...]:
    """Find albums the rebuild would not bring into the pattern.

    Naming plans without writing, and numbering only replaces the index
    ahead of what naming produces, so what a folder would end up called
    is known before anything moves. Anything that would still read wrong
    -- an orphan bracket the peel left behind, a folder with no year to
    build a name around -- is reported here rather than written to disk
    and found later by eye.

    Args:
        artist: The artist folder to inspect.

    Returns:
        One line per album that would not settle, naming it and saying
        what it would have become.
    """
    unsettled: list[str] = []
    for outcome in naming.plan(discover_albums(artist)).outcomes:
        if outcome.skipped:
            unsettled.append(f"{outcome.album.name!r} -- no year to build a name around")
        elif not names.conforms_unnumbered(outcome.new_name):
            unsettled.append(f"{outcome.album.name!r} -- would become {outcome.new_name!r}")

    return tuple(unsettled)


def _describe(group: Sequence[Path]) -> str:
    """Name one set of duplicate folders, for the refusal's detail line.

    Args:
        group: Folders sharing one identity.

    Returns:
        Their names, comma-joined.
    """
    return ", ".join(f"{album.name!r}" for album in group)


# ==================================================================================== #
#                                     THE PROTOCOL                                     #
# ==================================================================================== #
def _lay_out(artist: Path) -> ArtistResult:
    """Run the five steps on one artist, each against the last one's output.

    Albums and tracks are re-read before every step that needs them,
    because the step before renamed or moved what this one reads. Pruning
    runs first, so the count and the numbering settle over what remains
    once the Opus duplicates are gone. The label runs last: it renames
    the artist folder, which invalidates every path beneath it.

    Args:
        artist: The artist folder to lay out.

    Returns:
        A tally of what changed.
    """
    pruned = pruning.apply(pruning.plan(discover_albums(artist)))
    # The plans are held rather than passed straight through: each
    # carries what its pass noticed but would not act on, which the
    # report reads once the writing is done.
    name_plan = naming.plan(discover_albums(artist))
    named = naming.apply(name_plan)
    numbered = numbering.apply(numbering.plan(discover_albums(artist)))
    case_plan = track_naming.plan(find_audio_files(artist))
    cased = track_naming.apply(case_plan)
    place_plan = placement.plan(artist)
    placed = placement.apply(place_plan)
    labelled = artist_label.apply(artist_label.plan(artist))

    failures: int = (
        len(pruned.failures)
        + len(named.failures)
        + len(numbered.failures)
        + len(cased.failures)
        + len(placed.failures)
    )
    if labelled.detail is not None:
        failures += 1

    return ArtistResult(
        artist=labelled.artist,
        pruned=pruned.deleted,
        named=named.renamed,
        numbered=numbered.renamed,
        cased=cased.renamed,
        moved=placed.moved,
        labelled=labelled.renamed,
        failures=failures,
        notices=_notices(name_plan, case_plan, place_plan),
    )


def _notices(
    name_plan: naming.NamePlan,
    case_plan: track_naming.CasePlan,
    place_plan: placement.PlacementPlan,
) -> tuple[Notice, ...]:
    """Phrase everything the pass saw but would not act on.

    Five things resolve to a person rather than a rule: a name claiming
    an album missing over a folder that holds audio, an album this run
    was the first to call missing, a miscased "ep", a track whose cased
    name is already taken, and an album blocked from moving by a folder
    of its name. None is an error -- nothing failed -- but each leaves
    the shelf in a state only a person can settle, and saying nothing
    would let it pass silently.

    Each names what it means. "3 album(s) marked ⚠" across sixty artists
    is not something anyone can act on; the three folder names are.
    Names rather than paths, since the artist is printed on the line
    above and the rest of the path would be noise.

    Args:
        name_plan: What naming worked out.
        case_plan: What track casing worked out.
        place_plan: What placement worked out.

    Returns:
        One notice per kind there was, empty when there was none.
    """
    found: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            tuple(outcome.album.name for outcome in name_plan.conflicts),
            'album(s) marked "⚠" -- named missing while holding audio',
        ),
        (
            tuple(outcome.album.name for outcome in name_plan.newly_missing),
            'album(s) newly marked "M" -- no audio found in them',
        ),
        (
            tuple(outcome.album.name for outcome in name_plan.lowercase_eps),
            'album(s) carry a lower-case "ep" -- left as written',
        ),
        (
            tuple(outcome.track.name for outcome in case_plan.collisions),
            "track(s) not recased -- the cased name is already taken",
        ),
        (
            tuple(outcome.album.name for outcome in place_plan.collisions),
            "album(s) not filed -- a folder of that name is in the way",
        ),
    )

    return tuple(
        Notice(
            summary=f"{len(names)} {phrase}",
            details=tuple(f"{name!r}" for name in names),
        )
        for names, phrase in found
        if names
    )


# ==================================================================================== #
#                                      RENDERING                                       #
# ==================================================================================== #
def _echo_skip(refusal: Skipped) -> None:
    """Print the line for an artist the pass refused.

    Args:
        refusal: Why it was refused.
    """
    typer.secho(f"  skipped  {refusal.artist.name!r} -- {refusal.reason}", fg=typer.colors.RED)
    for detail in refusal.details:
        typer.secho(f"      {detail}", fg=typer.colors.BRIGHT_BLACK)


def _echo_artist(result: ArtistResult) -> None:
    """Print one line for an artist as it finishes, plus its changes.

    The name carries its own outcome -- a fresh label means it was laid
    out -- so it stands alone, green when something changed and dim when
    nothing did. What changed is listed beneath it, and beneath that
    anything the pass noticed but would not act on.

    Args:
        result: What laying the artist out changed.
    """
    if result.changed:
        typer.secho(f"  {result.artist.name!r}", fg=typer.colors.GREEN)
        typer.secho(f"      {_phrase(result)}", fg=typer.colors.BRIGHT_BLACK)
    else:
        typer.secho(f"  {result.artist.name!r}", fg=typer.colors.BRIGHT_BLACK)

    if result.failures:
        typer.secho(f"      {result.failures} operation(s) failed", fg=typer.colors.RED)
    echo_notices(result.notices)


def _phrase(result: ArtistResult) -> str:
    """Name only the kinds of change there were, for one artist's line.

    Args:
        result: The artist's tally.

    Returns:
        A comma-joined phrase, e.g. "3 named, 40 recased".
    """
    parts: list[str] = []
    if result.pruned:
        parts.append(f"{result.pruned} Opus pruned")
    if result.named:
        parts.append(f"{result.named} named")
    if result.numbered:
        parts.append(f"{result.numbered} renumbered")
    if result.cased:
        parts.append(f"{result.cased} recased")
    if result.moved:
        parts.append(f"{result.moved} moved")
    if result.labelled:
        parts.append("labelled")
    return ", ".join(parts)


def echo_skipped(skipped: Sequence[Skipped]) -> None:
    """List every refused artist at the end of a run.

    Named rather than left inline because both this command and organize
    close on the same list, and a person reading a run of sixty artists
    needs the refusals gathered where the totals are, not scrolled past.

    Args:
        skipped: The artists refused, in the order they were met.
    """
    if not skipped:
        return

    typer.secho(f"{len(skipped)} artist(s) skipped -- resolve and rerun:", fg=typer.colors.RED)
    for refusal in skipped:
        typer.secho(f"  {refusal.artist.name!r} -- {refusal.reason}", fg=typer.colors.RED)
        for detail in refusal.details:
            typer.secho(f"      {detail}", fg=typer.colors.BRIGHT_BLACK)


def _echo_summary(results: list[ArtistResult], skipped: list[Skipped]) -> None:
    """Print the closing totals across every artist.

    Args:
        results: What each processed artist changed.
        skipped: The artists refused before anything was written.
    """
    changed: int = sum(1 for result in results if result.changed)
    failures: int = sum(result.failures for result in results)
    noticed: int = sum(1 for result in results if result.notices)

    typer.secho(
        f"\nDone. {changed} of {len(results)} artist(s) changed.",
        fg=typer.colors.GREEN,
        bold=True,
    )
    echo_skipped(skipped)
    if noticed:
        typer.secho(
            f"{noticed} artist(s) need an eye -- see the notices above.",
            fg=typer.colors.YELLOW,
        )
    if failures:
        typer.secho(f"{failures} operation(s) failed across the run.", fg=typer.colors.RED)
