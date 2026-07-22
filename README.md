# Discography Toolkit

A personal toolkit for organizing my music discography — cleaning up, retagging, and keeping a large collection sane and easy to live with.

> *"Without music, life would be a mistake."*   
> — Friedrich Nietzsche

---

## 💿 What it does

Nine scripts that take an artist folder and make it consistent — folder names, file names, layout, and the tags inside every track.

They run in order, each one safe to re-run: a second pass reports nothing to do. Every script shows what it intends to change and asks before writing.

```
D:\MUSIC\DISCOGRAPHY\Jazz\USA\
└── Miles Davis - [65 • 65F • 0L • 0M]
    ├── 01. (1951) - Modern Jazz Trumpets
    ├── 09. (1954) - Miles Davis With Sonny Rollins
    ├── 13. (1955) - M - Miles Davis All Stars Vol. 2
    └── FLAC
        ├── 27. (1959) - Kind of Blue [FLAC]
        │   ├── cover.jpg
        │   ├── 01 - So What.flac
        │   └── 02 - Freddie Freeloader.flac
        └── 43. (1970) - Bitches Brew [FLAC]
            ├── CD 1
            └── CD 2
```

## 📐 Conventions

The toolkit doesn't invent a scheme so much as enforce one.

| Element            | Shape                                | Meaning                                 |
|--------------------|--------------------------------------|-----------------------------------------|
| Artist folder      | `Miles Davis - [65 • 65F • 0L • 0M]` | total albums, lossless, lossy, missing  |
| Album folder       | `27. (1959) - Kind of Blue [FLAC]`   | index, year, title, quality             |
| Lossless container | `FLAC`                               | holds every lossless album              |
| Missing album      | `13. (1955) - M - Title`             | known to exist, not held — empty folder |
| Conflict           | `13. (1955) - ⚠ - Title`            | marked missing but holding audio        |
| Favourite          | `©27. (1959) - Kind of Blue [FLAC]`  | personal mark, sorts ahead of the index |

Two rules run through all of it.

**The filesystem is the truth.** Quality tags, missing markers and album counts are derived from the files, never trusted from a name. A folder claiming to be missing while holding audio gets flagged rather than believed.

**Counts live in exactly one place.** The artist folder carries the breakdown; the container is a bare `FLAC` rather than repeating it. A number stored twice is a number that can disagree with itself.

## 📦 Installation

```bash
git clone https://github.com/Rapture244/discography-toolkit.git
cd discography-toolkit
uv sync
```

Requires Python 3.13.

## 🚀 Usage

Every script takes `--path`/`-p` and supports `--dry-run`.

```bash
# Folder and file naming
uv run .\scripts\01--albums_naming.py -p "D:\...\Miles Davis - [65 on 65]"
uv run .\scripts\02.0--albums_numbering.py -p "D:\...\Miles Davis - [65 on 65]"
uv run .\scripts\02.1--albums_track_naming.py -p "D:\...\Miles Davis - [65 on 65]"
uv run .\scripts\02.2--albums_placement.py -p "D:\...\Miles Davis - [65 on 65]"

# Metadata
uv run .\scripts\03--metadata_album_year.py -p "D:\...\Miles Davis - [65 • 65F • 0L • 0M]"
uv run .\scripts\04--metadata_album_artist.py -p "D:\...\Miles Davis - [65 • 65F • 0L • 0M]"
uv run .\scripts\06--metadata_title.py -p "D:\...\Miles Davis - [65 • 65F • 0L • 0M]"
uv run .\scripts\07--metadata_album_cover.py -p "D:\...\Miles Davis - [65 • 65F • 0L • 0M]"

# Genre — takes any path, from one album to a whole shelf
uv run .\scripts\05--metadata_genre.py -p "D:\...\DISCOGRAPHY\Jazz" -g "Jazz"
```

Run `02.2` last of the naming steps: it renames the artist folder, so its `New Path` output is what the metadata steps should be pointed at.

`05--metadata_genre.py` stands apart from the others. Its natural scope is a genre shelf rather than one artist, and its value is supplied rather than derived.

### 🪟 On Windows

Album and artist folder names contain `[` and `]`, which PowerShell treats as wildcards. Paths pass fine as script arguments, but `cd` needs `-LiteralPath`:

```powershell
cd -LiteralPath "D:\...\Miles Davis - [65 • 65F • 0L • 0M]"
```

## 🧰 What each script does

| Script                      | Does                                          |
|-----------------------------|-----------------------------------------------|
| `01--albums_naming`         | Year, quality tag, missing marker, title case |
| `02.0--albums_numbering`    | Sequential index by year and title            |
| `02.1--albums_track_naming` | Title-case audio filenames                    |
| `02.2--albums_placement`    | Sort albums by tier, label the artist folder  |
| `03--metadata_album_year`   | Album and Date tags                           |
| `04--metadata_album_artist` | Album Artist across a discography             |
| `05--metadata_genre`        | Genre, at any scope                           |
| `06--metadata_title`        | Title-case the Title tag                      |
| `07--metadata_album_cover`  | One cover per album, on disk and embedded     |

Twelve formats are supported for tagging — FLAC, MP3, M4A, OGG Vorbis, Opus, WAV, AIFF, DSF, APE, WV, TTA, WMA. Cover embedding covers all of those except APE, WV and WMA, whose picture mechanisms are too loosely specified to write blind.

## 🚧 Status

The scripts work and are used against a real library. `src/` is currently empty: the next step is extracting the shared code into a `discography_toolkit` package behind a single `rapt` CLI, at which point the scripts become deprecated.

## 🛠️ Development

```bash
prek run --all-files # ruff check, ruff format, basedpyright
```

Linting is `ruff` against `ruff.toml`, type checking is `basedpyright` in `recommended` mode against `pyrightconfig.json`.

---

Personal project, but feel free to use it if it's helpful to you!
