# Discography Toolkit

> *"Without music, life would be a mistake."*  
> — Friedrich Nietzsche

`rapt` is short for **Rapture**, the sunken city from *BioShock* — built on the idea of a place where people could create without limits, before it all fell apart. I loved the game, but what stuck with me was the atmosphere: old songs drifting through flooded halls, beauty half-drowned and still playing.

And there's the word itself: Rapture — *a feeling of emotional ecstasy so magical it's almost as if you've been transported to some other world.* That's what music has always done to me.

And loving it that much, you start to gather it. Collectors once dug crate by crate through vinyl to build something of their own; mine are digital, but the hunt is the same. At some point my library grew big enough that enjoying it wasn't the whole of it anymore — I needed to *know* it: what I had, what I was missing, the genres, all the quiet bookkeeping that comes with owning music at scale. This toolkit is how I keep that library sane.



## 💿 What it does

`rapt` is a single command-line tool with two halves.

- **`layout`** settles the folders — names, numbers, filenames, where each album sits, and the count stamped on the artist folder.
- **`align-tags`** writes the tags inside every track, read straight off those settled folders.
- **`organize`** runs both, in that order, which is the whole job in one pass.

Every writing command shows what it intends and asks before it touches anything, and each is safe to re-run — a second pass finds nothing to do.

```
D:\MUSIC\DISCOGRAPHY\Jazz\
└── Miles Davis - [4 • 2F • 1L • 1M]
    ├── 01. (1955) - M - Blue Moods
    ├── 02. (1957) - Birth of the Cool
    └── FLAC
        ├── 03. (1959) - Kind of Blue [FLAC]
        │   ├── cover.jpg
        │   ├── 01 - So What.flac
        │   └── 02 - Freddie Freeloader.flac
        └── 04. (1970) - Bitches Brew [FLAC]
            ├── CD 1
            └── CD 2
```


## 🧠 The idea

Two ideas hold the whole thing together.

**The filesystem is the truth.** Quality tags, missing markers and album counts are derived from the files, never trusted from a name. A folder claiming to be missing while holding audio gets flagged rather than believed — and the tags inside the files are written *from* the folders, not the other way round.

**Counts live in exactly one place.** The artist folder carries the breakdown; the container is a bare `FLAC` rather than repeating it. A number stored twice is a number that can disagree with itself.

That first idea is also why `layout` runs before `align-tags` and not the reverse: laying out the folders is what makes them the canonical form of the collection, and only then can the tags be read back off them. The one tag that *can't* be derived is genre — nothing in a folder says what a record sounds like — so that stays a separate command you give a value by hand.

## 📐 Conventions

The toolkit doesn't invent a scheme so much as enforce one.

| Element            | Shape                               | Meaning                                     |
|--------------------|-------------------------------------|---------------------------------------------|
| Artist folder      | `Miles Davis - [4 • 2F • 1L • 1M]`  | total albums, lossless, lossy, missing      |
| Album folder       | `03. (1959) - Kind of Blue [FLAC]`  | index, year, title, quality                 |
| Lossless container | `FLAC`                              | holds the lossless albums — only when mixed |
| Missing album      | `01. (1955) - M - Title`            | known to exist, not held — empty folder     |
| Conflict           | `01. (1955) - ⚠ - Title`           | marked missing but holding audio            |
| Favourite          | `©03. (1959) - Kind of Blue [FLAC]` | personal mark, sorts ahead of the index     |

The container earns its place only when there is something to separate. An artist with both lossless and other albums keeps the lossless ones in `FLAC` and the rest in the root. An **all-lossless** artist skips the container entirely and sits flat —

```
└── Fela Kuti - [3 • 3F • 0L • 0M]
    ├── 01. (1970) - Zombie [FLAC]
    ├── 02. (1972) - Shakara [FLAC]
    └── 03. (1977) - Sorrow Tears and Blood [FLAC]
```

— since there is nothing to separate the FLAC albums *from*.

## 📦 Installation

```bash
git clone https://github.com/Rapture244/discography-toolkit.git
cd discography-toolkit
uv sync
```

Requires Python 3.13. The `rapt` command is then available inside the environment:

```bash
uv run rapt --help
```

Optional — install shell completion for commands and options:

```bash
rapt --install-completion
```

## 🚀 Usage

The whole job, on a shelf of artists or a single one:

```bash
rapt organize -p "D:\MUSIC\DISCOGRAPHY\Jazz"
```

Or either half on its own:

```bash
rapt layout -p "D:\MUSIC\DISCOGRAPHY\Jazz"     # folders only
rapt align-tags -p "D:\MUSIC\DISCOGRAPHY\Jazz" # tags only, read off the folders
```

Genre is set by hand, at any scope from one album to a whole shelf:

```bash
rapt tags genre -p "D:\MUSIC\DISCOGRAPHY\Jazz" -g "Jazz"
```

And any single tag can be written on its own, handy for a one-off fix:

```bash
rapt tags cover -p "D:\...\Miles Davis - [4 • 2F • 1L • 1M]"
rapt tags year -p "D:\...\Miles Davis - [4 • 2F • 1L • 1M]"
```

`align-tags` and every `tags` command take `--dry-run` to show what would change without writing. `layout` and `organize` do not: their steps only make sense applied in sequence, so there is a single confirmation instead — `layout` also takes `-y`/`--yes` to skip it.

Point `layout` and `organize` at **fresh, unlabelled** material — they find artists by their audio and write the labels. Once labelled, `align-tags` and the `tags` commands find them by that label. Either way the search walks as deep as your shelf goes: region, genre, even numbered category folders like `01. Countries` are walked straight through to the artists beneath, so you point at the top of a tree rather than each artist in turn.

### 🙈 Ignoring a folder

Any folder or file whose name begins with a dot is skipped entirely — the toolkit never lists it, looks inside it, names it, moves it, or tags it. Renaming a stray folder to `.Documentaries` — a box of live DVDs, artwork, anything that isn't part of the music — is enough to keep it out of the way. The decision is made on the name, not the operating system's hidden flag, so it behaves the same everywhere.

### 🪟 On Windows

Album and artist folder names contain `[` and `]`, which PowerShell treats as wildcards. Paths pass fine as command arguments, but `cd` needs `-LiteralPath`:

```powershell
cd -LiteralPath "D:\...\Miles Davis - [4 • 2F • 1L • 1M]"
```

## 🧰 Commands

| Command             | Does                                                         |
|---------------------|--------------------------------------------------------------|
| `organize`          | Lay out the folders, then write every derived tag            |
| `layout`            | Name, number, recase, file, and label the folders            |
| `align-tags`        | Write album, artist, year, title, and cover from the folders |
| `tags album`        | `Album` tag from the album folder's name                     |
| `tags album-artist` | `Album Artist` from the artist folder above                  |
| `tags year`         | `Date` tag from the year in the album's name                 |
| `tags title`        | Title-case the `Title` tag                                   |
| `tags cover`        | One cover per album, on disk and embedded                    |
| `tags genre`        | `Genre`, at any scope — the one tag given by hand            |

`layout` itself is five folder steps in order — naming, numbering, track-casing, placement, and the artist label — run per artist.

## 🎧 Formats

Twelve formats are supported for tagging — FLAC, MP3, M4A, OGG Vorbis, Opus, WAV, AIFF, DSF, APE, WavPack, TTA, WMA. Cover embedding covers all of those except APE, WavPack and WMA, whose picture mechanisms are too loosely specified to write blind.

## 🚧 Status

The `discography_toolkit` package and the `rapt` CLI are complete and used against a real library. The original standalone scripts have been folded into the package and removed.

## 🛠️ Development

```bash
prek run --all-files # ruff check, ruff format, basedpyright
uv run pytest # the test suite
```

Linting is `ruff` against `ruff.toml`, type checking is `basedpyright` in `recommended` mode against `pyrightconfig.json`, and the suite runs under `pytest`.



Personal project, but feel free to use it if it's helpful to you!
