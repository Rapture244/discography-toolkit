# docs/genres.md

# Genre conventions

What the `Genre` tag holds in this discography, and why. `tags genre` enforces none of it — it writes what it is given, verbatim. These are conventions for a person; `list genres` is what catches them slipping.

---

## 🗂️ Two layers

An audio file sits in exactly one folder. The tree is the **physical layer** — `DISCOGRAPHY/<shelf>/<region>/<artist>/<container>/<album>/` — and membership in it is exclusive: filing an artist under one shelf is a decision that picks a winner and loses every alternative. Duplicating the files to be in two places is not a solution, it is two copies to keep in step.

Genre is the **second layer**, and it is many-to-many. A file carries as many genres as it needs, separated by `;`, so the same records regroup along axes the tree cannot express — and nothing is copied, nothing moves, nothing has to be kept in step. The shelf says where an artist lives; the tag says what you would want to browse them beside.

Which is why **a genre that restates the shelf buys nothing.** Tagging every Traditional Sounds artist `Traditional` would be the path spelled a second way. The tag earns its place by saying what the path cannot:

- David Hudson is one folder and two groups: `Indigenous;(AUS) Didgeridoo` puts him under both, which no filesystem can do.
- Rodrigo Rodriguez sits under Japan for the instrument he plays, and one of his albums reads `(JPN) Shakuhachi;Classical` — Bach and Albinoni on shakuhachi, which belongs beside both and can be filed beside only one.

The physical layer is documented per shelf below, as each is settled.

---

## 📐 The rules

**The separator is `;`.** A compound genre is one string with punctuation in it, not several values: `(JPN) Shakuhachi;Classical`. Formats do support real multi-value fields, but a single string survives the FLAC → Opus conversion unexamined, where multi-value depends on every tool in the chain preserving it. Tell the player that `;` separates genres and it groups on the parts. `list genres` splits when counting; writing never splits.

**Countries use ISO 3166-1 alpha-3.** `(JPN)`, `(CHN)`, `(AUS)`, `(GIN)`. Alpha-2 was tried and abandoned: too many codes are English words or audio jargon — `(CD)` is DR Congo, `(ML)` is Mali, `(IN)` is India. These are read by a person at a glance, which is what scoreboards use three letters for.

**Where ISO has no answer, write the place's name.** `(Tibet)`, `(Sapmi)`, `(Kurdistan)`. The rule is "name the origin"; ISO is the shorthand where one exists. Mixed widths are the price of never being stuck. ISO 3166-3 codes for former countries are not used — nobody reads `SUHH` and thinks USSR.

**Genre values are plain ASCII.** `Mande`, not `Mandé`. `(Sapmi)`, not `(Sápmi)`. A tag has to survive the FLAC → Opus conversion and whatever encoding the phone assumes, and accented letters sort unpredictably — some collations file `é` after `z`, which would strand a genre at the bottom of the list away from its family. Prose about the music can spell things properly; the tag cannot afford to.

**General before specific.** Where one genre contains another, the broader leads: `Mande;(GIN) Djembe`, `Indigenous;(AUS) Didgeridoo`. The umbrella gathers everything of its kind in one place, and the country-plus-instrument sits inside it rather than scattering the family down the alphabet. The umbrella is named once and not repeated — `Mande;(MLI) Kora`, never `(Mande) Kora;(MLI) Kora`.

**Parentheses are for places, bare words for traditions.** `(MLI)`, `(JPN)`, `(Tibet)`, `(Sapmi)` say where something is from. `Mande`, `Indigenous`, `Throat-Singing`, `Classical` say what it is.

**Where neither contains the other, the browse bucket leads.** A crossover names two independent facts, not a general and a specific one — `(JPN) Shakuhachi;Classical` is Bach played on shakuhachi, and neither term is a subset of the other. First position then goes to the axis you would actually reach for it under, which for that record is the instrument: it sits with the other shakuhachi, and `Classical` records where the tunes came from.

Either way the list is flat and sorts alphabetically, so whatever leads is the only grouping there is. Decide once per tradition, not per record.

**Instrument over category.** `Djembe`, not `Drums`. `Didgeridoo`, not `Percussion`. Everything here is percussion or wind or voice, so those words do no work.

**`Indigenous` means indigenous** — minority or first peoples, not "folk" and not "traditional". Not the majority ethnic group of a country: Malinké are a plurality in Guinea, so Mamady Keita is a djembefola and not `Indigenous`. `Indigenous` rather than `Aboriginal` because only the first travels beyond Australia.

**`Classical` is Western art music** — Bach, Mozart, and the rest. Strictly the word means the 1750–1820 period, but every player and database uses it for the whole tradition, and consistency with them beats precision. Split it into `Classical;Baroque` and the like only if that ever becomes worth the trouble.

**Reuse a genre before inventing one.** The table below is the vocabulary. A new genre is worth adding when nothing there fits; a near-miss of something already listed is drift.

**A family either shares an umbrella or shares a prefix.** Both make a group findable on a phone, and they trade off differently:

- **Umbrella** — `Mande;(GIN) Djembe`. One entry gathers the whole family however its members are named. The cost is that the umbrella sorts wherever its own name falls, nowhere near its members.
- **Prefix** — `Soul - Southern`, `Soul - Psychedelic`. The family sorts adjacent and reads down as a block. The cost is no single entry holding all of it.

Traditional Sounds uses umbrellas, Soul uses prefixes. That is a genuine inconsistency, kept on purpose: soul has many subgenres of one thing and wants them clustered, while `Mande` gathers instruments too unlike each other to share a prefix. Pick one per shelf and stay with it — mixing them inside a shelf is the drift `list genres` exists to catch.

---

## 📚 Shelves

### 🎤 Soul

American soul, roughly 1960 to the present. Organised by subgenre, then artist — `Soul/<Soul - X>/<Artist>/` — which is a different shape from Traditional Sounds and deliberately so: soul divides by sound rather than by place, so the sound is what the folders name.

The subgenre in the folder is the tag. One part per artist — no crossing terms so far, though the second layer is there if a record ever needs to sit beside another shelf.

Note that `Southern` and `Northern` are styles here, not places. Southern soul is the gospel-derived vocal over Memphis and Muscle Shoals horns; Northern soul is the uptempo 60s sound named after the English clubs that championed it. Both describe music, the way Delta blues or Chicago house do — a record made anywhere in that style still takes the name.

| Artist          | Genre                |
|-----------------|----------------------|
| Ann Peebles     | `Soul - Southern`    |
| Baby Huey       | `Soul - Psychedelic` |
| Carol Woods     | `Soul - Uptown`      |
| Curtis Mayfield | `Soul - Psychedelic` |
| Doris Duke      | `Soul - Southern`    |
| Erykah Badu     | `Soul - Neo`         |
| Gil Scott-Heron | `Soul - Progressive` |
| Laura Lee       | `Soul - Southern`    |
| Lee Moses       | `Soul - Southern`    |
| Little Ann      | `Soul - Northern`    |
| Marvin Gaye     | `Soul - Progressive` |
| Otis Redding    | `Soul - Southern`    |
| Wendy Rene      | `Soul - Southern`    |

**On Carol Woods.** One album, *Out of the Woods* (1972), and the thinnest paper trail on the shelf — but the credits settle it. Cut at Bell Sound Studios in New York with Beau Ray Fleming producing, vocals and horns overdubbed in London by Ember's Mike Berry. A Queens singer, church-choir trained, covering Goffin and King and a Clarence Paul song first recorded by Marvin Gaye. Contemporary write-ups describe a full, rich voice and a post-Supremes feel. Arranged, pop-facing, and nowhere near the South: `Soul - Uptown`.

**Deliberately not split: `Deep`.** Deep soul names the rawest, most gospel-drenched end, and Lee Moses and Doris Duke sit there. But the South is where the gospel is — that voice is not a separate category from Southern soul, it is what Southern soul means. Splitting them would carve one thing in two and leave every future artist to be assigned by feel.

### 🪘 Traditional Sounds

Raw traditional and indigenous music: an artist playing a traditional instrument in a living tradition. Not "world music", which is a shipping category, and not any record that happens to be foreign. Organised by region, then artist.

The region folder says where the music comes from, not where the artist happens to live. Lyu Hong Jun has lived in Japan since 1980, but he is Chinese and 天平楽府 reconstructs Tang court repertoire — so he is filed under China and reads `(CHN) Classical`. Biography is not provenance.

| Artist            | Genre                         |
|-------------------|-------------------------------|
| Mamady Keita      | `Mande;(GIN) Djembe`          |
| Toumani Diabate   | `Mande;(MLI) Kora`            |
| David Hudson      | `Indigenous;(AUS) Didgeridoo` |
| Lyu Hong Jun      | `(CHN) Classical`             |
| Kohachiro Miyata  | `(JPN) Shakuhachi`            |
| Rodrigo Rodriguez | `(JPN) Shakuhachi`            |
| Koto Vortex       | `(JPN) Koto`                  |
| Huun Huur Tu      | `Throat-Singing`              |
| Imre Peemot       | `Throat-Singing`              |

Two albums append `;Classical` at album level — one of Rodrigo Rodriguez's, one of Toumani Diabate's — which is the whole reason declarations resolve nearest-first. The artist is settled once and the exception sits inside it.

**Settled.** Every album on this shelf is declared. Nothing here is read from a tag any more, which is what makes `list genres` on this path instant.

---

## 🏷️ Confirmed genres

The vocabulary in use. Reach for one of these before writing a new one.

| Genre                | Means                                                                       |
|----------------------|-----------------------------------------------------------------------------|
| `(AUS) Didgeridoo`   | Aboriginal Australian didgeridoo, solo or with percussion                   |
| `(CHN) Classical`    | Chinese art music — court, literati, and reconstructions of it              |
| `(GIN) Djembe`       | Guinean-style Malinké djembe over a dunun ensemble                          |
| `(JPN) Koto`         | Japanese zither, traditional or contemporary repertoire                     |
| `(JPN) Shakuhachi`   | Japanese end-blown bamboo flute, Zen and folk repertoire                    |
| `(MLI) Kora`         | Malian kora, the 21-string Mande harp-lute                                  |
| `Classical`          | Western art music — Bach, Mozart, the standard repertoire                   |
| `Indigenous`         | Minority or first peoples' music, any continent                             |
| `Mande`              | The West African Mande tradition — kora, djembe, ngoni, balafon             |
| `Soul - Neo`         | Live-band soul with hip-hop rhythm and jazz phrasing, 1995 onward           |
| `Soul - Northern`    | Uptempo 60s soul, named for the English clubs that championed it            |
| `Soul - Progressive` | Album-length, socially serious soul — the early-70s turn                    |
| `Soul - Psychedelic` | Wah-wah, fuzz and extended arrangements, late 60s into the 70s              |
| `Soul - Southern`    | Gospel-derived vocal over Memphis and Muscle Shoals horns                   |
| `Soul - Uptown`      | Arranged, pop-facing soul — New York rather than the South                  |
| `Throat-Singing`     | Overtone singing — Tuvan khoomei, Mongolian khöömii, Sardinian tenores, kin |

**On `Throat-Singing` carrying no country.** Settled, not open: the technique crosses far too many for one to lead — Tuva, Mongolia, Altai, Khakassia, Tibet, Sardinia, the Xhosa, the Inuit. It is the umbrella, so it leads, and a country follows only if one ever earns it: `Throat-Singing;(Tuva)`, `Throat-Singing;(MNG)`. Nothing needs restructuring when the second tradition arrives.

**On `Mande` as an umbrella.** The kora and the djembe are Mande instruments, not Malian or Guinean ones — Mali, Guinea, Senegal, Gambia and Guinea-Bissau all play them — but the styles genuinely differ, Guinean djembe having come through Sékou Touré's state ensembles into something more arranged than Malian. So the tradition leads and the country says which style: `Mande;(GIN) Djembe`, `Mande;(MLI) Kora`. Nothing is lost by having no kora-wide entry, since the kora is played nowhere outside Mande cultures anyway, and `Mande` gathers the ngoni and balafon players besides.

**Not yet needed: `Griot`.** A social role rather than an instrument or a place — hereditary praise-singers and historians across the Mande world, playing kora, ngoni or balafon by family. Toumani Diabate was one, of a line he counted at seventy-one generations; Mamady Keita was not, being a djembefola from outside the caste. It would earn its place the day two griots of different instruments are on the shelf, which is one more than there are.

**On the word order.** Nobody outside this library writes `Soul - Psychedelic` — the terms are "psychedelic soul", "Southern soul", "neo-soul". The concepts are all standard; the formatting is not. Reversing them is what makes the family sort as a block, and matching Discogs matters less here than being able to find things.

---

## 🔍 Keeping it honest

`rapt list genres -p <path>` sorts by value, so a convention that drifted shows itself: `(JP) Shakuhachi` lands directly above `(JPN) Shakuhachi`. No fuzzy matching, no threshold — sorting is the whole mechanism. Each genre names the artists carrying it, which turns a wrong count into a folder to go and edit.

Declarations live in `.genre` files: one genre, one line, nearest wins, never read from above the path given. `tags genre --force` clears every declaration beneath a path and writes one, which is how a convention gets rolled out when it changes.
