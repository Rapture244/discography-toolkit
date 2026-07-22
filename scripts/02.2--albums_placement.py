#!/usr/bin/env -S uv run
"""File every album on the correct side of the FLAC container, and label the artist folder.

Calin's discography layout keeps lossy albums as direct children of an
artist folder, alongside one sibling container holding every lossless
album underneath it. Nothing so far has enforced that split: an album
ripped straight into the artist root, or dropped into the container by
mistake, stays where it landed and quietly makes the counts wrong.

This walks every album, decides from its actual files whether it is
lossless, and moves it to the side it belongs on -- lossless albums
into the container, everything else (lossy, Opus, and empty "missing"
placeholders) out into the artist root. Placement is decided by
content alone; the "[FLAC]" text in a folder name is written by
`01--albums_naming.py` from the same evidence and is never consulted
here, so this stays correct even when run on its own.

The container is normalized to a bare "FLAC". It used to carry a count
-- "FLAC - (56 on 65)" -- but that duplicated a number the artist
folder now states, and a fact stored twice is a fact that can disagree
with itself. It is created when at least one lossless album exists
(including when every album is lossless, so the layout stays uniform
across artists) and removed when none do, leaving the root flat.

The artist folder is then labelled with a breakdown of what it holds:

    Charlie Mariano - [90 • 60F • 0L • 30M]

read as ninety albums: sixty lossless, none lossy, thirty missing.
Those three are a partition -- every album is counted exactly once, so
F + L + M always equals the total and the label checks itself. Any
previous label is replaced wholesale, which means an older
"[M31 on 90]" or a hand-typed variant of the separator is corrected
rather than accumulated.

Album numbering is unaffected. `02--albums_numbering.py` pools the
root and the container into one sequence ordered by year and title, so
an album keeps its number wherever it physically sits, and this can be
run before or after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Annotated, Literal, cast

from mutagen.mp4 import MP4, MP4StreamInfoError
from rich.console import Console
from rich.rule import Rule
from rich.text import Text
import typer

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(add_completion=False, help=__doc__)


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# File extensions unambiguously treated as lossless audio, just from
# their extension. ".m4a" is deliberately NOT here -- it's an MP4
# container that can hold either lossy AAC or lossless ALAC, and the
# extension alone can never tell those apart. It's handled separately,
# in `_detect_audio_tier`, by inspecting the actual codec inside the
# file via mutagen.
_LOSSLESS_EXTENSIONS: frozenset[str] = frozenset(
    {".flac", ".wav", ".ape", ".wv", ".tta", ".aiff", ".aif", ".dsf", ".dff"}
)

# Lossy extensions. These never earn a place in the container, but they
# do prove the album is held rather than missing -- which is why an
# empty folder has to be a tier of its own rather than folding into
# "lossy" the way a naive extension check would leave it.
_LOSSY_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".m4a", ".ogg", ".opus", ".wma"})

_AudioTier = Literal["lossless", "lossy", "none"]

# The container is matched loosely on the way in and written strictly on
# the way out: any historical spelling -- "FLAC", "FLAC - (56 on 65)",
# "FLAC (56 ON 65)" -- is recognized as the same folder, and all of them
# are normalized to _CONTAINER_NAME. The pattern is deliberately
# identical to the one in 01/02/03, since all four scripts have to agree
# on which folder is bookkeeping rather than an album.
_FLAC_CONTAINER_RE: re.Pattern[str] = re.compile(r"^FLAC(?:[\s\-()\[\]0-9]|on)*$", re.IGNORECASE)
_CONTAINER_NAME: str = "FLAC"

# Separator inside the artist label. U+2022 BULLET, chosen over the
# smaller U+00B7 and the rarer math-block dots: it reads clearly at
# Explorer's font size and lives in General Punctuation, which is about
# as universally present in fonts as anything outside Latin-1.
_LABEL_DOT: str = "•"

# Terminal columns the bullet occupies when drawn. U+2022 is classified
# East Asian Ambiguous, so the standard declines to fix its width and
# terminals may draw it one column or two, while len() in Python always
# reports one character. Only matters where output is boxed and every
# row is padded to a common width -- a mismatch puts the border of any
# row containing a bullet out of line by the difference.
#
# Set to 1, matching the same measurement made for U+26A0 in 01. If the
# folder-changes box looks a column off, set this to 2.
_LABEL_DOT_COLUMNS: int = 1

# The label to replace, anchored to the end of the artist folder name.
# Requires the bracket to open with an optional "M" and then a digit,
# so every historical form is caught -- "[M31 on 90]", "[65 on 65]",
# "[90 • 60F • 0L • 30M]" -- while a bracket that is part of the artist's
# actual name ("[Live]", "[Best Of]") is left alone.
#
# The " - " ahead of the bracket is consumed too, so the separator is
# rebuilt exactly once rather than accumulating on every run.
#
# Because the whole bracket is replaced rather than edited, a label
# written with a different separator (a hand-typed "·" or "∙") is
# rewritten with the canonical one automatically. No lookalike list is
# needed; regenerating beats parsing.
_ARTIST_LABEL_RE: re.Pattern[str] = re.compile(r"\s*(?:-\s*)?\[\s*M?\d[^\[\]]*\]\s*$")

# One rendered line of the summary box: indent, bold label, marker,
# count, color. `None` stands for a horizontal rule between partitions
# rather than a row of figures.
_SummaryRow = tuple[str, str, str, int, str] | None


@dataclass
class AlbumPlacement:
    """Where a single album currently sits and where it belongs.

    Attributes:
        album: The album folder's current path.
        tier: What its files say it is.
        action: `"move_in"` to put a lossless album into the container,
            `"move_out"` to lift a non-lossless album back to the artist
            root, or `"keep"` when it's already on the right side.
        destination: The path the album would end up at, or `None` when
            `action` is `"keep"`.
    """

    album: Path
    tier: _AudioTier
    action: Literal["move_in", "move_out", "keep"]
    destination: Path | None = None


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
@app.command()
def place(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            "-p",
            help="Absolute path to the artist folder containing album subfolders.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show planned moves and renames without touching anything."),
    ] = False,
) -> None:
    """Sort albums across the FLAC container and label the artist folder.

    Args:
        path: Absolute path to the artist folder. Passed via
            `--path`/`-p`; if omitted, the user is prompted for it
            interactively instead.
        dry_run: If `True`, report every planned move and rename
            without modifying the filesystem.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            more than one container, no albums found, nothing to do,
            user abort, or a completed run), with an appropriate exit
            code attached.
    """
    if path is None:
        raw_input_path: str = cast(
            str, typer.prompt("Enter the absolute path to your artist folder")
        )
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    _echo_banner("Album Placement", path.name)

    containers: list[Path] = _find_containers(path)
    if len(containers) > 1:
        # Two containers can't be merged safely without guessing which
        # copy of a same-named album to keep, so this stops rather than
        # picks. Consolidating them by hand is a one-time job.
        typer.secho(
            f"\n{len(containers)} FLAC containers found -- expected at most one:",
            fg=typer.colors.RED,
            err=True,
        )
        for container in containers:
            typer.echo(f"  {container.name!r}", err=True)
        raise typer.Exit(code=1)

    container: Path | None = containers[0] if containers else None

    albums: list[Path] = _discover_albums(path, container)
    if not albums:
        typer.secho(f"No album folders found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Moves are planned against the container's *current* path, not the
    # name it will end up with. Renaming it first would invalidate the
    # source path of every album being lifted out of it, so the rename
    # is deferred until after the moves (see _apply).
    working_container: Path = container if container is not None else path / _CONTAINER_NAME
    placements: list[AlbumPlacement] = [
        _plan_placement(album, path, working_container) for album in albums
    ]

    total: int = len(placements)
    flac_count: int = sum(1 for p in placements if p.tier == "lossless")
    lossy_count: int = sum(1 for p in placements if p.tier == "lossy")
    missing_count: int = sum(1 for p in placements if p.tier == "none")

    moved_in: list[AlbumPlacement] = [p for p in placements if p.action == "move_in"]
    moved_out: list[AlbumPlacement] = [p for p in placements if p.action == "move_out"]
    in_place_count: int = total - len(moved_in) - len(moved_out)

    new_artist_name: str = _rewrite_artist_name(
        path.name, _artist_label(total, flac_count, lossy_count, missing_count)
    )
    container_changed: bool = _container_action(container, flac_count) is not None

    typer.echo()
    _echo_summary(
        total=total,
        moved_in=len(moved_in),
        moved_out=len(moved_out),
        in_place=in_place_count,
        flac=flac_count,
        lossy=lossy_count,
        missing=missing_count,
    )

    collisions: list[AlbumPlacement] = [
        p for p in moved_in + moved_out if p.destination is not None and p.destination.exists()
    ]
    if collisions:
        collision_note: str = "cannot move -- a folder of that name already exists at the target"
        typer.secho(
            f"\n{len(collisions)} album(s) {collision_note}:",
            fg=typer.colors.RED,
            err=True,
        )
        for placement in collisions:
            typer.echo(f"  {placement.album.name!r}", err=True)
        raise typer.Exit(code=1)

    _echo_move_group(f"-> into {_CONTAINER_NAME}", moved_in, typer.colors.GREEN)
    _echo_move_group("<- into ROOT", moved_out, typer.colors.YELLOW)

    _echo_folder_changes(path, container, new_artist_name, flac_count)

    nothing_to_do: bool = (
        not moved_in and not moved_out and not container_changed and new_artist_name == path.name
    )
    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        _echo_new_path(path.with_name(new_artist_name))
        raise typer.Exit(code=0)

    if nothing_to_do:
        typer.secho("\nAlready laid out correctly. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    if not typer.confirm("\nApply these changes?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    final_path: Path = _apply(
        artist=path,
        container=container,
        working_container=working_container,
        moves=moved_in + moved_out,
        flac_count=flac_count,
        new_artist_name=new_artist_name,
    )

    typer.secho(
        f"\nDone. {len(moved_in) + len(moved_out)} album(s) moved.",
        fg=typer.colors.GREEN,
        bold=True,
    )
    _echo_new_path(final_path)


# ==================================================================================== #
#                                   HELPER FUNCTIONS                                   #
# ==================================================================================== #
# Colour of the banner rule. Deliberately a colour used nowhere else in
# the output: green, blue, yellow, red, cyan, bright magenta and bright
# black all carry meaning here (changed, unchanged, warning, error, dry
# run, total, dimmed), so reusing any of them would read as a status
# rather than as a heading.
#
# Orange is only reachable through Rich. Typer styles with the 16-colour
# ANSI set, which has no orange in it at all; this is 256-colour index
# 208. Nearby alternatives are "orange1" (214, more amber), "orange3"
# (172, muted) and "orange_red1" (202, redder).
_BANNER_COLOR: str = "dark_orange"

# One console for the banner. Rich is used here only for its Rule, which
# fits itself to the terminal width -- a hand-rolled rule has to guess.
_console: Console = Console()


def _echo_banner(title: str, target: str) -> None:
    """Announce which step of the pipeline is running, and on what.

    Printed first, so a terminal holding the output of several scripts
    in sequence can be read back and each block attributed to the step
    that produced it.

    The title sits flush left rather than centred, so it lands at the
    same column as every other line of output and can be found by
    running an eye straight down the left margin. It carries no padding
    spaces: Rich inserts its own separator before the rule, and a
    centred title's padding would show as an indent here.

    The target is written with `typer.echo` rather than through the
    console on purpose. Rich parses square brackets as markup, so an
    artist folder named "Charlie Mariano - [90 • 60F • 0L • 30M]" comes
    out with the brackets restyled and the number syntax-highlighted.
    Only the rule, which is plain text under this script's control, goes
    through Rich.

    Deliberately carries no step number. The scripts are numbered by
    filename and that numbering is still settling, so a banner
    repeating it would be one more thing to keep in sync.

    Args:
        title: The step's name, e.g. `"Album Naming"`.
        target: The folder being worked on, printed on its own line
            beneath the rule.
    """
    _console.print()
    _console.print(
        Rule(
            Text(title, style=f"bold {_BANNER_COLOR}"),
            style=_BANNER_COLOR,
            characters="─",
            align="left",
        )
    )
    typer.echo(target)


def _normalize_path_input(raw: str) -> str:
    """Clean up a path typed at an interactive prompt.

    Shell arguments have their surrounding quotes stripped by the shell
    itself before this program ever sees them, but a plain interactive
    prompt has no such step -- typing a quoted path here would otherwise
    leave the quote characters embedded literally in the string.

    Args:
        raw: The raw text as typed at the prompt.

    Returns:
        The input with leading/trailing whitespace and a single layer
        of surrounding single or double quotes removed, if present.
    """
    cleaned: str = raw.strip().strip("'\"")
    return cleaned


def _find_containers(root: Path) -> list[Path]:
    """Find every direct child that looks like the FLAC container.

    Returns a list rather than a single path so the caller can refuse
    to guess: more than one match means two containers exist, and
    merging them could silently discard an album.

    Args:
        root: The artist folder to scan.

    Returns:
        Every visible direct subdirectory matching
        `_FLAC_CONTAINER_RE`, sorted by name.
    """
    matches: list[Path] = [
        entry
        for entry in root.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and _FLAC_CONTAINER_RE.match(entry.name)
    ]
    return sorted(matches, key=lambda p: p.name)


def _discover_albums(root: Path, container: Path | None) -> list[Path]:
    """Collect every album folder, on both sides of the container.

    Args:
        root: The artist folder to scan.
        container: The FLAC container, if one exists. It is never
            returned as an album itself -- only its children are.

    Returns:
        Visible album subdirectories from the artist root and from
        inside the container, as one flat list sorted by name.
    """
    albums: list[Path] = [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(".") and entry != container
    ]
    if container is not None:
        albums.extend(
            child
            for child in container.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )

    return sorted(albums, key=lambda p: p.name)


def _plan_placement(album: Path, root: Path, target_container: Path) -> AlbumPlacement:
    """Decide whether an album needs to move, and where to.

    Args:
        album: The album folder to inspect.
        root: The artist folder, where everything non-lossless belongs.
        target_container: The container path albums should end up in,
            named as it will be *after* normalization so the planned
            destination is the final one rather than an intermediate.

    Returns:
        An `AlbumPlacement` describing the album's tier and the move it
        needs, if any.
    """
    tier: _AudioTier = _detect_audio_tier(album)
    in_container: bool = album.parent != root

    if tier == "lossless" and not in_container:
        return AlbumPlacement(album, tier, "move_in", target_container / album.name)
    if tier != "lossless" and in_container:
        return AlbumPlacement(album, tier, "move_out", root / album.name)

    return AlbumPlacement(album, tier, "keep")


def _is_lossless_m4a(path: Path) -> bool:
    """Report whether an ".m4a" file holds ALAC rather than lossy AAC.

    The extension alone can't say: MP4 is a container, and the same
    ".m4a" name covers both. The codec is read from the stream info
    instead.

    Args:
        path: The ".m4a" file to inspect.

    Returns:
        `True` if the file's codec is ALAC. `False` for AAC, and for
        any file that can't be read as MP4 at all.
    """
    try:
        audio = MP4(path)
    except (MP4StreamInfoError, OSError):
        return False

    codec: str = getattr(audio.info, "codec", "")
    return codec.lower().startswith("alac")


def _detect_audio_tier(album: Path) -> _AudioTier:
    """Classify an album folder by the audio it actually contains.

    Searched recursively, so multi-disc "CD1"/"CD2" subfolders count.
    Most extensions are decided from `_LOSSLESS_EXTENSIONS` alone;
    ".m4a" is resolved per-file via `_is_lossless_m4a`.

    An empty folder is reported as `"none"` rather than folding into
    `"lossy"`. The two are opposite facts about the collection -- an
    album held in a lossy format versus an album not held at all --
    and only the first is a reason to leave it in the artist root
    while the second makes it a missing placeholder.

    Args:
        album: Path to the album folder to scan.

    Returns:
        `"lossless"` if any file is lossless or a confirmed-ALAC
        ".m4a"; otherwise `"lossy"` if any other recognized audio file
        is present; otherwise `"none"`.
    """
    has_lossy: bool = False
    for entry in album.rglob("*"):
        if not entry.is_file():
            continue
        suffix: str = entry.suffix.lower()
        if suffix in _LOSSLESS_EXTENSIONS:
            return "lossless"
        if suffix == ".m4a" and _is_lossless_m4a(entry):
            return "lossless"
        if suffix in _LOSSY_EXTENSIONS:
            has_lossy = True

    return "lossy" if has_lossy else "none"


def _artist_label(total: int, flac: int, lossy: int, missing: int) -> str:
    """Build the bracketed breakdown appended to the artist folder name.

    Args:
        total: Album count across the artist folder and container.
        flac: How many of those are lossless.
        lossy: How many are held in a lossy format.
        missing: How many are empty placeholders.

    Returns:
        A label of the form `"[90 • 60F • 0L • 30M]"`. The three counts
        partition the total, so they always sum to it.
    """
    dot: str = f" {_LABEL_DOT} "
    return f"[{total}{dot}{flac}F{dot}{lossy}L{dot}{missing}M]"


def _rewrite_artist_name(name: str, label: str) -> str:
    """Attach a fresh label to an artist folder name.

    Any existing label is replaced whole rather than edited, so an
    older "[M31 on 90]" and a label written with a different separator
    both converge on the current form. A name with no label at all
    simply gains one.

    Args:
        name: The artist folder's current name.
        label: The label to attach, as built by `_artist_label`.

    Returns:
        The name with exactly one label, at the end.
    """
    stripped: str = _ARTIST_LABEL_RE.sub("", name).rstrip()
    return f"{stripped} - {label}"


def _container_action(container: Path | None, flac_count: int) -> str | None:
    """Decide what needs to happen to the FLAC container.

    Args:
        container: The existing container, or `None` if there isn't one.
        flac_count: How many lossless albums the artist has.

    Returns:
        `"create"`, `"rename"`, `"remove"`, or `None` when the container
        is already in the state it should be.
    """
    if flac_count == 0:
        return "remove" if container is not None else None
    if container is None:
        return "create"

    return "rename" if container.name != _CONTAINER_NAME else None


def _echo_move_group(header: str, placements: list[AlbumPlacement], color: str) -> None:
    """Print one direction's moves under a single heading.

    The direction is stated once as a coloured heading rather than
    repeated as a prefix on every line, so a run that relocates thirty
    albums reads as two groups instead of thirty near-identical rows.
    Only the heading is coloured -- the album names stay plain, since
    colouring them too would make the block compete with itself for
    attention. Nothing is printed at all when the direction is empty.

    Args:
        header: The heading for this direction, e.g. `"-> into FLAC"`.
        placements: The albums moving in that direction.
        color: Typer colour for the heading.
    """
    if not placements:
        return

    typer.secho(f"\n{header}", fg=color, bold=True)
    for placement in placements:
        typer.echo(f"  {placement.album.name!r}")


def _display_width(text: str) -> int:
    """Measure how many terminal columns a string occupies when drawn.

    Differs from `len` only for the bullet separator, which counts as
    one character but may be drawn wider. Everything else in the boxed
    output is ASCII, where the two agree.

    Args:
        text: The string to measure.

    Returns:
        The number of terminal columns the string is expected to fill.
    """
    extra: int = text.count(_LABEL_DOT) * (_LABEL_DOT_COLUMNS - 1)
    return len(text) + extra


def _echo_new_path(path: Path) -> None:
    """Print the artist folder's resolved path, on its own.

    Preceded by a blank line so it stands apart from whatever outcome
    line came before it, and double-quoted so it can be pasted
    straight back as a `-p` argument -- the name contains spaces, and
    after labelling it contains bullets too.

    Only the label is coloured. The path itself stays plain, which is
    what makes it read as a value to copy rather than as more status
    output.

    Args:
        path: The artist folder's path, projected on a dry run and
            actual after a real one.
    """
    label: str = typer.style("New Path:", fg=typer.colors.GREEN, bold=True)
    typer.echo(f'\n{label} "{path}"')


def _pad_cell(text: str, width: int) -> str:
    """Pad a table cell to a column width, measured in terminal columns.

    Args:
        text: The cell's contents.
        width: The column's width.

    Returns:
        `text` followed by enough spaces to fill the column.
    """
    return text + " " * (width - _display_width(text))


def _echo_folder_changes(
    artist: Path, container: Path | None, new_artist_name: str, flac_count: int
) -> None:
    """Print the planned container and artist folder changes, as a table.

    Laid out with Artist and Container as columns and Before/After as
    rows, rather than one row per folder. Transposing it stacks the two
    long artist names vertically instead of spreading them across the
    line, which is what keeps the table narrow -- laid out the other
    way, the container's short values sit in columns sized by the
    artist's long ones and the box runs twenty columns wider.

    A grid has nowhere to put an arrow, so the "After" cells carry the
    signal in colour instead: green where the name changes, blue where
    it doesn't. Per-cell rather than per-row, since the artist folder
    frequently changes on a run where the container doesn't.

    Real folder names are quoted; the states that aren't names
    (`none`, `removed`) are left bare, which is what distinguishes them
    at a glance. Widths run through `_display_width` rather than `len`,
    since the artist name carries bullets on every run after the first.

    Args:
        artist: The artist folder's current path.
        container: The existing container, or `None`.
        new_artist_name: The artist folder's name after labelling.
        flac_count: How many lossless albums the artist has.
    """
    action: str | None = _container_action(container, flac_count)
    if action == "create":
        container_before, container_after = "none", repr(_CONTAINER_NAME)
    elif action == "remove" and container is not None:
        container_before, container_after = repr(container.name), "removed"
    elif action == "rename" and container is not None:
        container_before, container_after = repr(container.name), repr(_CONTAINER_NAME)
    elif container is not None:
        container_before = container_after = repr(container.name)
    else:
        container_before = container_after = "none"

    artist_before: str = repr(artist.name)
    artist_after: str = repr(new_artist_name)

    header: tuple[str, str, str] = ("", "Artist", "Container")
    before_row: tuple[str, str, str] = ("Before", artist_before, container_before)
    after_row: tuple[str, str, str] = ("After", artist_after, container_after)
    changed: tuple[bool, bool] = (artist_after != artist_before, action is not None)

    widths: list[int] = [
        max(_display_width(row[column]) for row in (header, before_row, after_row))
        for column in range(3)
    ]
    rule: str = "─" + "─┬─".join("─" * w for w in widths) + "─"

    typer.echo()
    typer.echo("┌" + rule + "┐")
    typer.echo(
        "│ "
        + " │ ".join(
            typer.style(_pad_cell(cell, w), bold=True)
            for cell, w in zip(header, widths, strict=True)
        )
        + " │"
    )
    typer.echo("├" + rule.replace("┬", "┼") + "┤")
    typer.echo(
        "│ " + " │ ".join(_pad_cell(c, w) for c, w in zip(before_row, widths, strict=True)) + " │"
    )

    after_cells: list[str] = [_pad_cell(after_row[0], widths[0])]
    for index, is_changed in enumerate(changed, start=1):
        color: str = typer.colors.GREEN if is_changed else typer.colors.BLUE
        after_cells.append(typer.style(_pad_cell(after_row[index], widths[index]), fg=color))
    typer.echo("│ " + " │ ".join(after_cells) + " │")
    typer.echo("└" + rule.replace("┬", "┴") + "┘")


def _echo_summary(
    *, total: int, moved_in: int, moved_out: int, in_place: int, flac: int, lossy: int, missing: int
) -> None:
    """Print the two-partition summary box.

    Two independent partitions over the same albums, separated by a
    rule: what this run *moves* (in/out/in place), then what the
    collection *is* (lossless/lossy/missing). Each block sums to the
    total on its own, which is the whole reason for the divider --
    listed flat as six siblings they'd read as one tally adding up to
    well over 100%.

    Args:
        total: Album count.
        moved_in: Albums moving into the container.
        moved_out: Albums moving out to the artist root.
        in_place: Albums already on the correct side.
        flac: Lossless album count.
        lossy: Lossy album count.
        missing: Empty placeholder count.
    """
    if not total:
        return

    count_width: int = len(str(total))
    summary_rows: list[_SummaryRow] = [
        ("", "Total", "", total, typer.colors.BRIGHT_MAGENTA),
        None,
        ("  ", "Moved in", "(->)", moved_in, typer.colors.GREEN),
        ("  ", "Moved out", "(<-)", moved_out, typer.colors.YELLOW),
        ("  ", "In place", "(==)", in_place, typer.colors.BLUE),
        None,
        ("  ", "FLAC", "(F)", flac, typer.colors.GREEN),
        ("  ", "Lossy", "(L)", lossy, typer.colors.YELLOW),
        ("  ", "Missing", "(M)", missing, typer.colors.BLUE),
    ]

    entries: list[tuple[str, str, str, int, str]] = [row for row in summary_rows if row is not None]
    label_width: int = max(len(indent + label) for indent, label, _, _, _ in entries)
    marker_width: int = max(len(marker) for _, _, marker, _, _ in entries)

    def _rest(indent: str, label: str, marker: str, count: int) -> str:
        """Build the non-bold remainder of a summary row."""
        pad: str = " " * (label_width - len(indent + label))
        body: str = f"{pad} {marker:<{marker_width}} : {count:>{count_width}}"
        if label == "Total":
            return body
        return f"{body}  ({count / total * 100:>6.2f}%)"

    rendered: list[tuple[str, str, str, str] | None] = [
        None if row is None else (row[0], row[1], _rest(row[0], row[1], row[2], row[3]), row[4])
        for row in summary_rows
    ]
    box_width: int = max(
        len(indent + label + rest)
        for row in rendered
        if row is not None
        for indent, label, rest, _ in [row]
    )

    typer.echo("┌" + "─" * (box_width + 2) + "┐")
    for row in rendered:
        if row is None:
            typer.echo("├" + "─" * (box_width + 2) + "┤")
            continue
        indent, label, rest, color = row
        pad = " " * (box_width - len(indent + label + rest))
        styled: str = (
            indent + typer.style(label, fg=color, bold=True) + typer.style(rest, fg=color) + pad
        )
        typer.echo("│ " + styled + " │")
    typer.echo("└" + "─" * (box_width + 2) + "┘")


def _apply(
    *,
    artist: Path,
    container: Path | None,
    working_container: Path,
    moves: list[AlbumPlacement],
    flac_count: int,
    new_artist_name: str,
) -> Path:
    """Carry out the planned moves and renames, in a safe order.

    Order matters twice over. The container is created before the
    moves but renamed *after* them, because renaming it first would
    invalidate the source path of every album being lifted out of it.
    The artist folder is renamed last of all, since that invalidates
    every path computed beneath it.

    Args:
        artist: The artist folder's current path.
        container: The existing container, or `None`.
        working_container: The container path the moves were planned
            against -- the existing one if there is one, otherwise the
            path a new one will be created at.
        moves: Every album that needs to change side.
        flac_count: How many lossless albums the artist has.
        new_artist_name: The artist folder's name after labelling.

    Returns:
        The artist folder's path after any rename.
    """
    if flac_count > 0 and container is None:
        working_container.mkdir()

    for placement in moves:
        if placement.destination is not None:
            _ = shutil.move(str(placement.album), str(placement.destination))

    # Removal waits until the albums are out, since the container is
    # only empty once they've left. Refuses on a non-empty directory,
    # which is the desired behaviour: anything still in there is
    # something this script didn't account for.
    if flac_count == 0:
        if container is not None and container.exists():
            container.rmdir()
    elif working_container.name != _CONTAINER_NAME:
        _ = working_container.rename(artist / _CONTAINER_NAME)

    if new_artist_name == artist.name:
        return artist

    final_path: Path = artist.with_name(new_artist_name)
    _ = artist.rename(final_path)
    return final_path


# ==================================================================================== #
#                                     ENTRY POINT                                      #
# ==================================================================================== #
if __name__ == "__main__":
    app()
