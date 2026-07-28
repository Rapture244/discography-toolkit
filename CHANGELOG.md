# Changelog

All notable changes to this project will be documented in this file.

This format takes inspiration from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## v0.0.2 (2026-07-28)

The nine standalone scripts become one installable tool. `rapt` -- a single `typer` CLI -- replaces the `scripts/` pipeline with the same operations behind three verbs (`organize`, `layout`, `align-tags`) plus a `tags` group, and the whole job now runs in one pass.

[:octocat: GitHub release](https://github.com/Rapture244/discography-toolkit/releases/tag/v0.0.2)

### New Features

- `rapt` command-line entry point, wired through `[project.scripts]` to `discography_toolkit.cli.main:app`, so the toolkit is one command on the `PATH` rather than a folder of scripts run by path.
- `organize` -- the whole job in one pass: `layout` then `align-tags`, in that order, since the folders must be settled before the tags can be read off them.
- `layout` -- the five folder steps (naming, numbering, track-casing, placement, and the artist label) run per artist as a single command; takes `-y`/`--yes` to skip its one confirmation.
- `align-tags` -- writes every folder-derived tag (album, album artist, year, title, cover) straight off the settled folders.
- `tags` group -- each derived tag as its own command for one-off fixes: `tags album`, `tags album-artist`, `tags year`, `tags title`, `tags cover`, and `tags genre` (the one tag given by hand, at any scope from a single album to a whole shelf).
- All-lossless artists now sit flat, skipping the `FLAC` container entirely -- the container earns its place only when there is something to separate.
- Dot-prefixed folders and files are ignored everywhere: never listed, walked, named, moved, or tagged. The decision is made on the name, not the operating system's hidden flag, so it behaves the same on every platform.
- Discovery walks a shelf as deep as it goes -- region, genre, and numbered category folders like `01. Countries` are walked straight through to the artists beneath, so you point at the top of a tree rather than each artist in turn.

### Changes

- The nine numbered scripts were reorganized into a proper package split by concern -- `cli/`, `core/`, and `operations/` -- separating what the toolkit does to folders from what it writes inside the files.
- `layout` and `organize` no longer take `--dry-run`: their steps only make sense applied in sequence, so there is a single confirmation instead. `align-tags` and every `tags` command keep `--dry-run`.

### Removed

- The nine standalone scripts under `scripts/` -- folded into the `discography_toolkit` package and deleted.

### Breaking Changes

- The `uv run scripts/NN--*.py --path ...` invocation is gone. Migrate to the `rapt` commands: the `01`–`02.2` folder scripts are now `rapt layout`, the `03`–`07` metadata scripts are `rapt align-tags` (or an individual `rapt tags <name>`), and the two together are `rapt organize`.

## v0.0.1 (2026-07-22)

[:octocat: GitHub release](https://github.com/Rapture244/discography-toolkit/releases/tag/v0.0.1)

### New Features

Nine standalone scripts under `scripts/`, run with `uv run`, forming a pipeline over a single artist folder. Each takes `--path`/`-p`, supports `--dry-run`, reports what it would change, and asks before writing.

**Folder and file naming**

- `01--albums_naming.py` -- rebuild album folder names as `NN. (YYYY) - Title [FLAC]`. Extracts the leftmost year, detects the audio tier from the files rather than the name, title-cases the title, and relocates a `©` mark to the front.
- `02.0--albums_numbering.py` -- renumber every album sequentially by year and title, pooling the artist root and the FLAC container into one continuous sequence.
- `02.1--albums_track_naming.py` -- title-case audio filenames, leaving extensions and non-audio files untouched.
- `02.2--albums_placement.py` -- move lossless albums into the `FLAC` container and everything else out of it, then label the artist folder with a breakdown of what it holds.

**Metadata**

- `03--metadata_album_year.py` -- write the album folder name as the Album tag and its year as the Date tag.
- `04--metadata_album_artist.py` -- write one Album Artist across a whole discography, derived from the artist folder's name.
- `05--metadata_genre.py` -- write one Genre to every file beneath any path, from an album to a whole genre shelf.
- `06--metadata_title.py` -- title-case the Title tag of every file.
- `07--metadata_album_cover.py` -- settle one front cover per album, save it beside the tracks and embed it into every file.

**Conventions established**

- Missing albums keep an empty placeholder folder marked `M`; a folder whose name claims missing while holding audio is flagged `⚠` for a human to resolve. Both markers are derived from the files, never trusted from the name.
- The artist folder carries `[T • nF • nL • nM]` -- total, lossless, lossy and missing -- as a partition that always sums to the total.
- The lossless container is a bare `FLAC`, with the count living on the artist folder so no number is stored twice.
- Album artwork is saved as a loose `cover.jpg` at full resolution while the copy embedded in each track is capped at 1200px, since that one is stored once per file.

**Shared behaviour**

- Every step is idempotent: a second run reports no changes.
- Twelve audio formats supported for tagging -- FLAC, MP3, M4A, OGG Vorbis, Opus, WAV, AIFF, DSF, APE, WV, TTA and WMA.
- `uv sync` style progress bars, and a summary box partitioning each run into what it did and what the collection is.

