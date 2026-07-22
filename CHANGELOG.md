# Changelog

All notable changes to this project will be documented in this file.

This format takes inspiration from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## 0.0.1 (2026-07-22)

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

