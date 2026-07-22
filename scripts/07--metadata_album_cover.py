#!/usr/bin/env -S uv run
"""Give every track in an album the same front cover, and save it beside them.

For each album under an artist folder, this settles on one front cover
and applies it everywhere: written to disk as a loose `cover.jpg` (or
`cover.png`) beside the tracks, and embedded into every audio file in
the album so a player shows artwork whichever way it looks for it.

Where the cover comes from, in order:

- The tracks themselves. Every embedded front cover in the album is
  read and the most common one wins, tie-broken by size. Sloppy rips
  routinely have art on one track and nothing on the rest, or one
  stray back-cover scan among a dozen matching fronts; a majority
  keeps the odd one out from deciding for the album.
- Failing that, a loose image already in the album folder --
  "cover", "folder" or "front" with a .jpg/.jpeg/.png extension. That
  covers albums that shipped with artwork on disk but none in the
  tags, and means this works in both directions rather than only
  redistributing what is already embedded.
- Failing both, the album is reported and skipped.

A multi-disc album is one album. Its "CD1"/"CD2" subfolders share a
single cover at the album root, so a disc whose tracks carry no art
still gets covered by its sibling's.

The loose file keeps the original bytes at full resolution. The
*embedded* copy is capped at 1200 pixels and re-encoded as JPEG when it
exceeds that, because embedded art is stored once per track: a 3000px
scan costs about 100 MB across a twenty-track album, against 20 MB at
1200px, and no player displays more than about a thousand pixels
anyway. Art already at or below the cap is embedded byte-for-byte, so
nothing is re-encoded that doesn't have to be.

Only the front cover is touched. A file holding a back cover or an
artist photo alongside it keeps them.

Covers are handled for FLAC, OGG Vorbis, Opus, MP3, WAV, AIFF, DSF,
TTA and M4A. APE, WV and WMA are left alone and reported: their
picture mechanisms are non-standard enough that writing them blind
would be worse than not writing them.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import io
from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, Literal, cast, override

from mutagen.aiff import AIFF
from mutagen.dsf import DSF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.trueaudio import TrueAudio
from mutagen.wave import WAVE
from PIL import Image
from rich.console import Console
from rich.progress import Progress, ProgressColumn, TaskProgressColumn, TextColumn
from rich.rule import Rule
from rich.text import Text
import typer

if TYPE_CHECKING:
    from mutagen import FileType
    from rich.progress import Task

# ==================================================================================== #
#                                      TYPER APP                                       #
# ==================================================================================== #
app = typer.Typer(add_completion=False, help=__doc__)


# ==================================================================================== #
#                                      CONSTANTS                                       #
# ==================================================================================== #
# Formats whose picture storage is well defined and tested here.
_COVER_EXTENSIONS: frozenset[str] = frozenset(
    {".flac", ".ogg", ".opus", ".mp3", ".wav", ".aiff", ".aif", ".dsf", ".tta", ".m4a"}
)

# Audio this script recognizes but will not write covers into. APE, WV
# and WMA store pictures in ways too loosely specified to write blind,
# so they are counted and reported rather than silently skipped.
_UNSUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".ape", ".wv", ".wma", ".dff"})

_AUDIO_EXTENSIONS: frozenset[str] = _COVER_EXTENSIONS | _UNSUPPORTED_EXTENSIONS

# ID3-based formats, which all carry pictures in an APIC frame.
_ID3_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".aiff", ".aif", ".dsf", ".tta"})

# Vorbis-comment formats, which carry pictures as a base64 FLAC picture
# block in a "metadata_block_picture" comment.
_VORBIS_EXTENSIONS: frozenset[str] = frozenset({".ogg", ".opus"})

# ID3 picture type 3 is "cover (front)". Only this one is replaced;
# a back cover or artist photo sitting beside it is left alone.
_FRONT_COVER: int = 3

# Stems accepted for a loose cover file already on disk, checked in
# this order. Matched case-insensitively against the whole stem.
_COVER_STEMS: tuple[str, ...] = ("cover", "folder", "front", "albumart", "album")
_IMAGE_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png")

# Longest edge, in pixels, for the copy embedded into each track. Art
# at or below this is embedded exactly as found; above it, the image is
# resampled and re-encoded as JPEG at this quality. The loose file on
# disk always keeps the original bytes.
_EMBED_MAX_PIXELS: int = 1200
_EMBED_JPEG_QUALITY: int = 90

# Calin's discography layout splits an artist folder into lossy albums
# directly inside it, plus one sibling container -- "FLAC", or an older
# "FLAC - (56 on 65)" -- holding every lossless album underneath. The
# container is bookkeeping, not an album: its children are discovered
# as ordinary albums and it is never treated as one itself.
_FLAC_CONTAINER_RE: re.Pattern[str] = re.compile(r"^FLAC(?:[\s\-()\[\]0-9]|on)*$", re.IGNORECASE)


@dataclass(frozen=True)
class Cover:
    """A front cover image, in memory.

    Attributes:
        data: The raw image bytes.
        mime: The image's MIME type, sniffed from its magic bytes.
    """

    data: bytes
    mime: str

    @property
    def extension(self) -> str:
        """File extension matching this image's actual type."""
        return ".png" if self.mime == "image/png" else ".jpg"

    @property
    def digest(self) -> str:
        """Content hash, used to decide which cover an album agrees on."""
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class AlbumPlan:
    """What one album needs done.

    Attributes:
        album: The album folder.
        cover: The cover the album settled on, or `None` if it has no
            artwork anywhere.
        source: Where that cover came from, for reporting.
        cover_file: Path the loose cover file should be written to, or
            `None` when one already exists with the right contents.
        rename_from: A loose image to rename to the canonical name,
            when it already holds exactly the right bytes.
        to_delete: Loose images left over once the canonical file is in
            place -- duplicates of the same artwork under another name.
        to_embed: Audio files whose front cover needs writing.
        already_correct: Files already carrying this exact cover.
        unsupported: Files whose format this script won't write into.
        track_count: How many audio files the album holds, recorded up
            front so an album with tracks but no artwork can be told
            apart from an empty placeholder folder.
    """

    album: Path
    cover: Cover | None = None
    source: Literal["tags", "disk", "none"] = "none"
    cover_file: Path | None = None
    rename_from: Path | None = None
    to_delete: list[Path] = field(default_factory=list)
    to_embed: list[Path] = field(default_factory=list)
    already_correct: int = 0
    unsupported: int = 0
    track_count: int = 0


# ==================================================================================== #
#                                   PROGRESS DISPLAY                                   #
# ==================================================================================== #
class DashBarColumn(ProgressColumn):
    """A progress bar drawn as a run of dashes, in the style of `uv sync`.

    uv renders its bars with indicatif's `{bar:30.green/black.dim}` and
    `progress_chars("--")`: a fixed-width row of `-` characters that
    starts entirely dim and is repainted green from the left as the task
    advances. Rich's stock `BarColumn` draws solid block glyphs instead,
    so the bar is assembled by hand here.

    Attributes:
        bar_width: Number of dash characters the bar occupies.
        complete_style: Rich style for the completed portion.
        remaining_style: Rich style for the not-yet-completed portion.
    """

    def __init__(
        self,
        bar_width: int = 30,
        complete_style: str = "green",
        remaining_style: str = "bright_black",
    ) -> None:
        """Initialize the column.

        Args:
            bar_width: Number of dash characters the bar occupies.
            complete_style: Rich style for the completed portion.
            remaining_style: Rich style for the remaining portion.
        """
        self.bar_width: int = bar_width
        self.complete_style: str = complete_style
        self.remaining_style: str = remaining_style
        super().__init__()

    @override
    def render(self, task: Task) -> Text:
        """Render the bar for one task at its current completion.

        Args:
            task: The task being rendered, supplying `completed` and
                `total`.

        Returns:
            A `Text` of `bar_width` dashes, split into a completed run
            and a remaining run styled independently.
        """
        total: float = task.total or 0
        fraction: float = 0.0 if total <= 0 else min(max(task.completed / total, 0.0), 1.0)
        filled: int = int(fraction * self.bar_width)

        return Text.assemble(
            ("-" * filled, self.complete_style),
            ("-" * (self.bar_width - filled), self.remaining_style),
        )


class FileCountColumn(ProgressColumn):
    """A `(completed/total files)` counter to sit beside the percentage.

    Rich keeps `Task.completed` as a `float`, so a plain
    `TextColumn("{task.completed}")` would render `58.0`; the count is
    coerced to `int` here. The running count is also right-aligned to
    the width of the total, so the column keeps a constant width as the
    counter gains digits instead of shifting the line beside it.

    Attributes:
        style: Rich style for the counter, kept dim by default so the
            percentage stays the more prominent figure.
    """

    def __init__(self, style: str = "bright_black") -> None:
        """Initialize the column.

        Args:
            style: Rich style applied to the whole counter.
        """
        self.style: str = style
        super().__init__()

    @override
    def render(self, task: Task) -> Text:
        """Render the counter for one task at its current completion.

        Args:
            task: The task being rendered, supplying `completed` and
                `total`.

        Returns:
            A `Text` of the form `(  58/103 files)`, the running count
            padded to the width of the total.
        """
        total: int = int(task.total) if task.total else 0
        completed: int = int(task.completed)
        width: int = len(str(total))

        return Text(f"({completed:>{width}}/{total} files)", style=self.style)


# ==================================================================================== #
#                                    PUBLIC COMMAND                                    #
# ==================================================================================== #
@app.command()
def cover(
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
        typer.Option("--dry-run", help="Show what would be written without touching anything."),
    ] = False,
) -> None:
    """Settle one front cover per album, save it to disk and embed it.

    Args:
        path: Absolute path to the artist folder. Passed via
            `--path`/`-p`; if omitted, the user is prompted for it
            interactively instead.
        dry_run: If `True`, work out every change and report it
            without writing anything.

    Raises:
        typer.Exit: Raised for all early-exit scenarios (invalid path,
            no albums found, nothing to do, user abort, or a completed
            run), with an appropriate exit code attached.
    """
    if path is None:
        raw_input_path: str = cast(
            str, typer.prompt("Enter the absolute path to your artist folder")
        )
        path = Path(_normalize_path_input(raw_input_path)).expanduser().resolve()

    if not path.is_dir():
        typer.secho(f"Not a directory: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    _echo_banner("Metadata: Album Cover", path.name)

    albums: list[Path] = _discover_albums(path)
    if not albums:
        typer.secho(f"\nNo album folders found in {path}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    progress_columns = (
        TextColumn("[bold]{task.description}"),
        DashBarColumn(),
        TaskProgressColumn(),
        FileCountColumn(),
    )
    run_label: str = path.name or str(path)

    plans: list[AlbumPlan] = []
    with Progress(*progress_columns) as progress:
        scan_task = progress.add_task(run_label, total=len(albums))
        for album in albums:
            plans.append(_plan_album(album))
            progress.update(scan_task, advance=1)

    _echo_summary(plans)
    _echo_album_reports(plans)

    embed_total: int = sum(len(p.to_embed) for p in plans)
    files_to_write: int = sum(1 for p in plans if p.cover_file is not None)
    files_to_rename: int = sum(1 for p in plans if p.rename_from is not None)
    files_to_delete: int = sum(len(p.to_delete) for p in plans)
    file_ops: int = files_to_write + files_to_rename + files_to_delete

    if dry_run:
        typer.secho("\nDry run: no changes made.", fg=typer.colors.CYAN)
        raise typer.Exit(code=0)

    if not embed_total and not file_ops:
        typer.secho("\nEvery album already has its cover. Nothing to do.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    actions: list[str] = []
    if files_to_write:
        actions.append(f"write {files_to_write} cover file(s)")
    if files_to_rename:
        actions.append(f"rename {files_to_rename}")
    if files_to_delete:
        actions.append(f"delete {files_to_delete} duplicate(s)")
    if embed_total:
        actions.append(f"embed into {embed_total} track(s)")
    prompt: str = "\n" + ", ".join(actions).capitalize() + "?"
    if not typer.confirm(prompt):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    # Failures are collected rather than printed as they happen: writing
    # to the terminal inside a live progress region tears the bar apart,
    # and grouping them at the end reads better anyway.
    written_files: int = 0
    renamed_files: int = 0
    deleted_files: int = 0
    embedded: int = 0
    failures: list[tuple[Path, str]] = []

    with Progress(*progress_columns) as progress:
        write_task = progress.add_task(run_label, total=embed_total + file_ops)
        for plan in plans:
            if plan.cover is None:
                continue

            if plan.rename_from is not None:
                try:
                    _ = plan.rename_from.rename(plan.album / f"cover{plan.cover.extension}")
                except OSError as exc:
                    failures.append((plan.rename_from, str(exc)))
                else:
                    renamed_files += 1
                progress.update(write_task, advance=1)

            if plan.cover_file is not None:
                try:
                    _ = plan.cover_file.write_bytes(plan.cover.data)
                except OSError as exc:
                    failures.append((plan.cover_file, str(exc)))
                else:
                    written_files += 1
                progress.update(write_task, advance=1)

            # Deleted only after the canonical file is in place, so a
            # failure above never leaves the album with no artwork.
            for stale in plan.to_delete:
                try:
                    stale.unlink()
                except OSError as exc:
                    failures.append((stale, str(exc)))
                else:
                    deleted_files += 1
                progress.update(write_task, advance=1)

            payload: Cover = _embed_payload(plan.cover)
            for track in plan.to_embed:
                try:
                    _embed_cover(track, payload)
                except Exception as exc:  # noqa: BLE001 - any file can fail in ways not worth enumerating
                    failures.append((track, str(exc)))
                else:
                    embedded += 1
                progress.update(write_task, advance=1)

    for target, detail in failures:
        typer.secho(f"  Failed: {str(target)!r} - {detail}", fg=typer.colors.RED)

    done: list[str] = [f"{written_files + renamed_files} cover file(s) in place"]
    if deleted_files:
        done.append(f"{deleted_files} duplicate(s) removed")
    done.append(f"{embedded} track(s) embedded")
    typer.secho(f"\nDone. {', '.join(done)}.", fg=typer.colors.GREEN, bold=True)
    if failures:
        typer.secho(f"{len(failures)} operation(s) failed.", fg=typer.colors.RED)


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


def _discover_albums(root: Path) -> list[Path]:
    """Find album subdirectories inside an artist folder.

    A direct child matching `_FLAC_CONTAINER_RE` is bookkeeping rather
    than an album: it is never returned itself, but its children are,
    so lossy albums in the artist root and lossless albums inside the
    container end up in one flat list.

    Album folders are the unit here, not disc folders -- a multi-disc
    album's "CD1"/"CD2" live inside one album and share its cover.

    Args:
        root: The artist folder to scan.

    Returns:
        Visible album subdirectories, sorted by name.
    """
    albums: list[Path] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if _FLAC_CONTAINER_RE.match(entry.name):
            albums.extend(
                child
                for child in entry.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            )
            continue
        albums.append(entry)

    return sorted(albums, key=lambda p: p.name)


def _find_audio_files(album: Path) -> list[Path]:
    """Collect every audio file inside an album folder.

    Searched recursively, so a multi-disc album's "CD1"/"CD2" tracks
    are included and end up sharing the album's single cover.

    Args:
        album: The album folder to scan.

    Returns:
        Audio files beneath `album`, sorted by path.
    """
    return sorted(
        entry
        for entry in album.rglob("*")
        if entry.is_file()
        and entry.suffix.lower() in _AUDIO_EXTENSIONS
        and not any(part.startswith(".") for part in entry.relative_to(album).parts)
    )


def _sniff_mime(data: bytes) -> str | None:
    """Identify an image from its magic bytes.

    Trusting a file extension or a tag's declared MIME type would mean
    trusting whatever wrote it; the first bytes are the actual answer.

    Args:
        data: The image's raw bytes.

    Returns:
        `"image/jpeg"`, `"image/png"`, or `None` if it is neither.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _make_cover(data: bytes) -> Cover | None:
    """Build a `Cover` from raw bytes, if they are a usable image.

    Args:
        data: Candidate image bytes.

    Returns:
        A `Cover`, or `None` when the bytes are empty or not a JPEG or
        PNG.
    """
    if not data:
        return None

    mime: str | None = _sniff_mime(data)
    if mime is None:
        return None
    return Cover(data=data, mime=mime)


def _read_front_cover(path: Path) -> Cover | None:
    """Read a track's embedded front cover, if it has one.

    Args:
        path: The audio file to read.

    Returns:
        The front cover, or `None` when the file has none, is a format
        this script does not write covers into, or cannot be read.
    """
    suffix: str = path.suffix.lower()

    try:
        if suffix == ".flac":
            audio = FLAC(path)
            for picture in audio.pictures:
                if picture.type == _FRONT_COVER:
                    return _make_cover(bytes(picture.data))
            return None

        if suffix in _VORBIS_EXTENSIONS:
            audio = OggOpus(path) if suffix == ".opus" else OggVorbis(path)
            for encoded in _vorbis_pictures(audio):
                picture = Picture(base64.b64decode(encoded))
                if picture.type == _FRONT_COVER:
                    return _make_cover(bytes(picture.data))
            return None

        if suffix in _ID3_EXTENSIONS:
            audio = _open_id3(path, suffix)
            for frame in _id3_pictures(audio):
                if frame.type == _FRONT_COVER:
                    return _make_cover(bytes(frame.data))
            return None

        if suffix == ".m4a":
            audio = MP4(path)
            covers = audio.tags.get("covr") if audio.tags else None
            if covers:
                return _make_cover(bytes(covers[0]))
            return None

    except Exception:  # noqa: BLE001 - an unreadable file simply contributes no cover
        return None

    return None


def _vorbis_pictures(audio) -> list[str]:
    """Return a Vorbis file's base64 picture comments, or an empty list.

    Args:
        audio: A mutagen Ogg Vorbis or Opus file object.

    Returns:
        The raw `metadata_block_picture` values, empty when absent.
    """
    return list(audio.get("metadata_block_picture") or [])


def _id3_pictures(audio):
    """Return an ID3 file's APIC frames, or an empty list.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.

    Returns:
        Every APIC frame present, empty when the file has no tags.
    """
    if audio.tags is None:
        return []
    return list(audio.tags.getall("APIC"))


def _find_loose_images(album: Path) -> list[tuple[Cover, Path]]:
    """Find every loose cover image sitting in the album folder.

    Checked only at the album root, and only for the conventional
    stems, so an unrelated photo deeper in the tree is never mistaken
    for artwork. All matches are returned rather than the first,
    because the extras are exactly what needs cleaning up once the
    canonical file is settled.

    Args:
        album: The album folder to search.

    Returns:
        `(cover, path)` pairs for every readable JPEG or PNG whose stem
        is a recognized cover name, ordered by `_COVER_STEMS` so the
        canonical "cover" comes first.
    """
    found: list[tuple[Cover, Path]] = []
    try:
        entries = [entry for entry in album.iterdir() if entry.is_file()]
    except OSError:
        return found

    for stem in _COVER_STEMS:
        for entry in entries:
            if entry.stem.lower() != stem or entry.suffix.lower() not in _IMAGE_EXTENSIONS:
                continue
            try:
                cover = _make_cover(entry.read_bytes())
            except OSError:
                continue
            if cover is not None:
                found.append((cover, entry))

    return found


def _longest_edge(cover: Cover) -> int:
    """Measure a cover's longest edge in pixels.

    Args:
        cover: The image to measure.

    Returns:
        The longer of width and height, or `0` if the bytes cannot be
        read as an image.
    """
    try:
        with Image.open(io.BytesIO(cover.data)) as image:
            return max(image.size)
    except OSError:
        return 0


def _choose_cover(covers: list[Cover]) -> Cover | None:
    """Pick the cover an album agrees on.

    Grouped by content hash and decided by count, so a single stray
    scan among a dozen matching fronts cannot win. Ties go to the
    largest image, which is the better one to keep when two candidates
    are equally popular.

    Args:
        covers: Every front cover found across the album's tracks.

    Returns:
        The winning cover, or `None` if the list is empty.
    """
    if not covers:
        return None

    grouped: dict[str, list[Cover]] = {}
    for cover in covers:
        grouped.setdefault(cover.digest, []).append(cover)

    best_digest: str = max(grouped, key=lambda d: (len(grouped[d]), len(grouped[d][0].data)))
    return grouped[best_digest][0]


def _plan_album(album: Path) -> AlbumPlan:
    """Work out what one album needs, without changing anything.

    Args:
        album: The album folder to examine.

    Returns:
        An `AlbumPlan` describing the chosen cover, the loose file to
        write, and which tracks need embedding.
    """
    plan = AlbumPlan(album=album)
    tracks: list[Path] = _find_audio_files(album)
    plan.track_count = len(tracks)
    if not tracks:
        return plan

    taggable: list[Path] = [t for t in tracks if t.suffix.lower() in _COVER_EXTENSIONS]
    plan.unsupported = len(tracks) - len(taggable)

    embedded: list[tuple[Path, Cover | None]] = [(t, _read_front_cover(t)) for t in taggable]
    found: list[Cover] = [c for _, c in embedded if c is not None]

    # The album's cover is the best version available anywhere, not
    # merely whatever the tags hold. That matters on a second run: the
    # copy embedded in the tracks is capped, so preferring tags
    # unconditionally would read the capped image back, decide it was
    # the album's cover, and overwrite the full-resolution file on disk
    # with it -- destroying the master this script went out of its way
    # to keep.
    chosen: Cover | None = _choose_cover(found)
    if chosen is not None:
        plan.source = "tags"

    loose: list[tuple[Cover, Path]] = _find_loose_images(album)
    if loose:
        best_loose, _ = max(loose, key=lambda pair: _longest_edge(pair[0]))
        if chosen is None or _longest_edge(best_loose) > _longest_edge(chosen):
            chosen = best_loose
            plan.source = "disk"

    if chosen is None:
        return plan

    plan.cover = chosen

    # The loose file keeps the original bytes; only the embedded copy is
    # capped. Skipped entirely when a file of the right name already
    # holds exactly these bytes.
    # The loose file is normalized to "cover", with the extension
    # telling the truth about the bytes -- ".jpg" for JPEG, ".png" only
    # when the artwork genuinely is a PNG. An album already using
    # "folder.jpg" has it renamed rather than gaining a second copy,
    # and any other recognized image left over afterwards is a
    # duplicate of the same artwork under a name nothing reads.
    target: Path = album / f"cover{chosen.extension}"
    existing: dict[Path, Cover] = {path: cover for cover, path in loose}

    if target in existing and existing[target].data == chosen.data:
        pass  # already correct, nothing to write
    else:
        rename_source: Path | None = next(
            (
                path
                for path, cover in existing.items()
                if path != target and cover.data == chosen.data
            ),
            None,
        )
        if rename_source is not None and target not in existing:
            plan.rename_from = rename_source
        else:
            plan.cover_file = target

    plan.to_delete = [path for path in existing if path != target and path != plan.rename_from]

    payload: Cover = _embed_payload(chosen)
    for track, current in embedded:
        if current is not None and current.data == payload.data:
            plan.already_correct += 1
        else:
            plan.to_embed.append(track)

    return plan


def _embed_payload(cover: Cover) -> Cover:
    """Produce the copy of a cover that gets embedded into each track.

    Embedded art is stored once per track, so an oversized scan is
    multiplied by the track count. Anything longer than
    `_EMBED_MAX_PIXELS` on its longest edge is resampled and re-encoded
    as JPEG; anything at or below it is returned untouched, so no image
    is re-encoded that doesn't need to be and no generation loss is
    introduced for free.

    Args:
        cover: The album's chosen cover, at original resolution.

    Returns:
        The cover to embed -- the same object when it is already small
        enough, otherwise a downscaled JPEG.
    """
    try:
        with Image.open(io.BytesIO(cover.data)) as image:
            if max(image.size) <= _EMBED_MAX_PIXELS:
                return cover
            image.thumbnail((_EMBED_MAX_PIXELS, _EMBED_MAX_PIXELS), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=_EMBED_JPEG_QUALITY)
    except OSError:
        # Unreadable as an image: embed the original bytes rather than
        # dropping a cover the tracks may well accept.
        return cover

    # Downscaling normally shrinks the file, but not always -- a large
    # image of flat colour can compress better than its resampled
    # version, whose detail is now per-pixel. The cap exists to save
    # bytes, so it declines to spend any.
    resized: bytes = buffer.getvalue()
    if len(resized) >= len(cover.data):
        return cover

    return Cover(data=resized, mime="image/jpeg")


def _open_id3(path: Path, suffix: str) -> FileType:
    """Open an ID3-tagged file, creating a tag block if absent.

    Args:
        path: The audio file to open.
        suffix: The file's lowercase extension, used to pick the right
            mutagen class.

    Returns:
        A mutagen file object with a populated `.tags` (ID3) attribute.
    """
    if suffix == ".mp3":
        audio_cls = MP3
    elif suffix == ".wav":
        audio_cls = WAVE
    elif suffix in {".aiff", ".aif"}:
        audio_cls = AIFF
    elif suffix == ".tta":
        audio_cls = TrueAudio
    else:
        audio_cls = DSF

    audio = audio_cls(path)
    if audio.tags is None:
        audio.add_tags()
    return audio


def _embed_cover(path: Path, cover: Cover) -> None:
    """Write a front cover into one track, preserving its other pictures.

    Args:
        path: The audio file to write.
        cover: The cover to embed.

    Raises:
        Exception: Whatever mutagen raises for an unwritable or corrupt
            file, left to the caller to record.
    """
    suffix: str = path.suffix.lower()

    if suffix == ".flac":
        audio = FLAC(path)
        others = [p for p in audio.pictures if p.type != _FRONT_COVER]
        audio.clear_pictures()
        for picture in others:
            audio.add_picture(picture)
        audio.add_picture(_build_picture(cover))
        audio.save()
        return

    if suffix in _VORBIS_EXTENSIONS:
        audio = OggOpus(path) if suffix == ".opus" else OggVorbis(path)
        kept: list[str] = [
            encoded
            for encoded in _vorbis_pictures(audio)
            if Picture(base64.b64decode(encoded)).type != _FRONT_COVER
        ]
        kept.append(base64.b64encode(_build_picture(cover).write()).decode("ascii"))
        audio["metadata_block_picture"] = kept
        audio.save()
        return

    if suffix in _ID3_EXTENSIONS:
        audio = _open_id3(path, suffix)
        _set_id3_cover(audio, cover)
        audio.save()
        return

    if suffix == ".m4a":
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        image_format = MP4Cover.FORMAT_PNG if cover.mime == "image/png" else MP4Cover.FORMAT_JPEG
        _set_mp4_cover(audio, cover.data, image_format)
        audio.save()
        return

    msg = f"Cover embedding not supported for {suffix}"
    raise ValueError(msg)


def _build_picture(cover: Cover) -> Picture:
    """Build a FLAC-style picture block for a cover.

    Used for FLAC directly and, base64-encoded, for Vorbis comments.

    Args:
        cover: The cover to wrap.

    Returns:
        A `Picture` marked as the front cover.
    """
    picture = Picture()
    picture.data = cover.data
    picture.type = _FRONT_COVER
    picture.mime = cover.mime
    picture.desc = "Cover"
    return picture


def _set_id3_cover(audio, cover: Cover) -> None:
    """Replace an ID3 front-cover frame, keeping any other pictures.

    Args:
        audio: A mutagen file object with an ID3-based `.tags`.
        cover: The cover to embed.
    """
    kept = [frame for frame in audio.tags.getall("APIC") if frame.type != _FRONT_COVER]
    kept.append(APIC(encoding=3, mime=cover.mime, type=_FRONT_COVER, desc="Cover", data=cover.data))
    audio.tags.setall("APIC", kept)


def _set_mp4_cover(audio, data: bytes, image_format: int) -> None:
    """Set an MP4 file's cover atom.

    MP4 has no picture-type concept, so the cover list is replaced
    outright rather than merged.

    Args:
        audio: A mutagen `MP4` file object.
        data: The image bytes.
        image_format: `MP4Cover.FORMAT_JPEG` or `MP4Cover.FORMAT_PNG`.
    """
    audio.tags["covr"] = [MP4Cover(data, imageformat=image_format)]


def _echo_album_reports(plans: list[AlbumPlan]) -> None:
    """List what this run will actually change, grouped by kind of write.

    Reports actions rather than states. Which albums already have
    artwork is not something anyone can act on; what is worth reading
    is which folders gain a cover file and which tracks get written
    into.

    The two kinds of write are separated rather than interleaved per
    album, because a cover file is the same write every time -- naming
    it once above the list of folders it lands in says as much as
    repeating it beside each of forty-seven album names, in half the
    lines. Track embedding does need per-album grouping, since the
    files differ.

    Empty placeholder folders appear nowhere. They hold no tracks, so
    there is nothing to cover and nothing to fix -- and where a third
    of a discography is placeholders, listing them buries the albums
    that genuinely lack art.

    Args:
        plans: Every album examined.
    """
    writing: list[AlbumPlan] = [p for p in plans if p.cover_file is not None]
    if writing:
        names: str = " / ".join(
            sorted({repr(p.cover_file.name) for p in writing if p.cover_file is not None})
        )
        typer.secho(f"\nfile  {names}  ({len(writing)} album(s))", fg=typer.colors.CYAN, bold=True)
        for plan in writing:
            typer.secho(f"  {plan.album.name!r}", fg=typer.colors.BRIGHT_BLACK)

    renaming: list[AlbumPlan] = [p for p in plans if p.rename_from is not None]
    if renaming:
        typer.secho(
            f"\nrename  -> 'cover.*'  ({len(renaming)} album(s))",
            fg=typer.colors.CYAN,
            bold=True,
        )
        for plan in renaming:
            if plan.rename_from is not None:
                line: str = f"{plan.album.name!r}  {plan.rename_from.name!r}"
                typer.secho(f"  {line}", fg=typer.colors.BRIGHT_BLACK)

    deleting: list[AlbumPlan] = [p for p in plans if p.to_delete]
    if deleting:
        count: int = sum(len(p.to_delete) for p in deleting)
        typer.secho(
            f"\ndelete  duplicate image(s)  ({count} file(s) in {len(deleting)} album(s))",
            fg=typer.colors.RED,
            bold=True,
        )
        for plan in deleting:
            for stale in plan.to_delete:
                typer.secho(f"  {plan.album.name!r}  {stale.name!r}", fg=typer.colors.BRIGHT_BLACK)

    embedding: list[AlbumPlan] = [p for p in plans if p.to_embed]
    if embedding:
        tracks: int = sum(len(p.to_embed) for p in embedding)
        typer.secho(
            f"\ntag   ({tracks} track(s) in {len(embedding)} album(s))",
            fg=typer.colors.GREEN,
            bold=True,
        )
        for plan in embedding:
            typer.echo(f"\n  {plan.album.name!r}")
            for track in plan.to_embed:
                name: str = repr(str(track.relative_to(plan.album)))
                typer.secho(f"    {name}", fg=typer.colors.BRIGHT_BLACK)

    bare: list[AlbumPlan] = [p for p in plans if p.cover is None and _has_tracks(p)]
    if bare:
        typer.secho(f"\n{len(bare)} album(s) with no cover:", fg=typer.colors.YELLOW, bold=True)
        for plan in bare:
            typer.secho(f"  {plan.album.name!r}", fg=typer.colors.BRIGHT_BLACK)

    unsupported: int = sum(p.unsupported for p in plans)
    if unsupported:
        note: str = "in formats this script won't write covers into (APE, WV, WMA)"
        typer.secho(f"\n{unsupported} file(s) {note} -- left untouched.", fg=typer.colors.YELLOW)


def _has_tracks(plan: AlbumPlan) -> bool:
    """Report whether an album held any audio at all.

    Distinguishes an album genuinely missing artwork from an empty
    placeholder folder, which has nothing to cover and is not a
    problem.

    Args:
        plan: The album's plan.

    Returns:
        `True` if the album contained audio files.
    """
    return plan.track_count > 0


def _echo_summary(plans: list[AlbumPlan]) -> None:
    """Print the summary box.

    Two partitions over the same albums, separated by a rule: where
    each album's cover came from, then what this run will do about it.

    "Empty" is its own row rather than being folded into "No cover".
    A placeholder folder holds no tracks, so it has no artwork by
    definition and is not a fault -- counting it as a missing cover
    would put a third of this discography in the failure column.

    Args:
        plans: Every album examined.
    """
    total: int = len(plans)
    if not total:
        return

    from_tags: int = sum(1 for p in plans if p.source == "tags")
    from_disk: int = sum(1 for p in plans if p.source == "disk")
    empty: int = sum(1 for p in plans if not _has_tracks(p))
    without: int = total - from_tags - from_disk - empty
    to_write: int = sum(1 for p in plans if p.cover_file is not None)
    to_embed: int = sum(len(p.to_embed) for p in plans)
    settled: int = sum(p.already_correct for p in plans)

    rows: list[tuple[str, str, str, int, str] | None] = [
        ("", "Albums", "", total, typer.colors.BRIGHT_MAGENTA),
        None,
        ("  ", "From tags", "(<-)", from_tags, typer.colors.GREEN),
        ("  ", "From disk", "(<-)", from_disk, typer.colors.CYAN),
        ("  ", "No cover", "(--)", without, typer.colors.YELLOW),
        ("  ", "Empty", "(  )", empty, typer.colors.BRIGHT_BLACK),
        None,
        ("  ", "Cover files", "(->)", to_write, typer.colors.GREEN),
        ("  ", "Tracks to embed", "(->)", to_embed, typer.colors.GREEN),
        ("  ", "Tracks settled", "(==)", settled, typer.colors.BLUE),
    ]

    entries = [row for row in rows if row is not None]
    label_width: int = max(len(indent + label) for indent, label, _, _, _ in entries)
    marker_width: int = max(len(marker) for _, _, marker, _, _ in entries)
    count_width: int = max(len(str(count)) for _, _, _, count, _ in entries)

    def _rest(indent: str, label: str, marker: str, count: int) -> str:
        """Build the non-bold remainder of a summary row."""
        pad: str = " " * (label_width - len(indent + label))
        return f"{pad} {marker:<{marker_width}} : {count:>{count_width}}"

    rendered: list[tuple[str, str, str, str] | None] = [
        None if row is None else (row[0], row[1], _rest(row[0], row[1], row[2], row[3]), row[4])
        for row in rows
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


# ==================================================================================== #
#                                     ENTRY POINT                                      #
# ==================================================================================== #
if __name__ == "__main__":
    app()
