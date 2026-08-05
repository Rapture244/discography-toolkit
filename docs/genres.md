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

**Where ISO has no answer, write the place's name.** `(Tibet)`, `(Sápmi)`, `(Kurdistan)`. The rule is "name the origin"; ISO is the shorthand where one exists. Mixed widths are the price of never being stuck. ISO 3166-3 codes for former countries are not used — nobody reads `SUHH` and thinks USSR.

**General before specific.** Where one genre contains another, the broader leads: `(Mandé) Djembe;(GIN) Djembe`, `Indigenous;(AUS) Didgeridoo`. The umbrella gathers everything of its kind in one place, and the narrow term sits inside it rather than scattering the family down the alphabet.

**Where neither contains the other, the browse bucket leads.** A crossover names two independent facts, not a general and a specific one — `(JPN) Shakuhachi;Classical` is Bach played on shakuhachi, and neither term is a subset of the other. First position then goes to the axis you would actually reach for it under, which for that record is the instrument: it sits with the other shakuhachi, and `Classical` records where the tunes came from.

Either way the list is flat and sorts alphabetically, so whatever leads is the only grouping there is. Decide once per tradition, not per record.

**Instrument over category.** `Djembe`, not `Drums`. `Didgeridoo`, not `Percussion`. Everything here is percussion or wind or voice, so those words do no work.

**`Indigenous` means indigenous** — minority or first peoples, not "folk" and not "traditional". Not the majority ethnic group of a country: Malinké are a plurality in Guinea, so Mamady Keita is a djembefola and not `Indigenous`. `Indigenous` rather than `Aboriginal` because only the first travels beyond Australia.

**`Classical` is Western art music** — Bach, Mozart, and the rest. Strictly the word means the 1750–1820 period, but every player and database uses it for the whole tradition, and consistency with them beats precision. Split it into `Classical;Baroque` and the like only if that ever becomes worth the trouble.

**Reuse a genre before inventing one.** The table below is the vocabulary. A new genre is worth adding when nothing there fits; a near-miss of something already listed is drift.

---

## 📚 Shelves

### 🪘 Traditional Sounds

Raw traditional and indigenous music: an artist playing a traditional instrument in a living tradition. Not "world music", which is a shipping category, and not any record that happens to be foreign. Organised by region, then artist.

The region folder says where the music comes from, not where the artist happens to live. Lyu Hong Jun has lived in Japan since 1980, but he is Chinese and 天平楽府 reconstructs Tang court repertoire — so he is filed under China and reads `(CHN) Classical`. Biography is not provenance.

| Artist            | Genre                         |
|-------------------|-------------------------------|
| Mamady Keita      | `(Mandé) Djembe;(GIN) Djembe` |
| David Hudson      | `Indigenous;(AUS) Didgeridoo` |
| Lyu Hong Jun      | `(CHN) Classical`             |
| Kohachiro Miyata  | `(JPN) Shakuhachi`            |
| Rodrigo Rodriguez | `(JPN) Shakuhachi`            |
| Koto Vortex       | `(JPN) Koto`                  |
| Huun Huur Tu      | `Throat-Singing`              |
| Imre Peemot       | `Throat-Singing`              |

---

## 🏷️ Confirmed genres

The vocabulary in use. Reach for one of these before writing a new one.

| Genre              | Means                                                                        |
|--------------------|------------------------------------------------------------------------------|
| `(AUS) Didgeridoo` | Aboriginal Australian didgeridoo, solo or with percussion                    |
| `(CHN) Classical`  | Chinese art music — court, literati, and reconstructions of it               |
| `(GIN) Djembe`     | Guinean-style Malinké djembe over a dunun ensemble                           |
| `(Mandé) Djembe`   | The djembe tradition whole, across every country that plays it               |
| `(JPN) Koto`       | Japanese zither, traditional or contemporary repertoire                      |
| `(JPN) Shakuhachi` | Japanese end-blown bamboo flute, Zen and folk repertoire                     |
| `Classical`        | Western art music — Bach, Mozart, the standard repertoire                    |
| `Indigenous`       | Minority or first peoples' music, any continent                              |
| `Throat-Singing`   | Overtone singing — Tuvan khoomei, Mongolian khöömii, Sardinian tenores, kin  |

**On `Throat-Singing` carrying no country.** Settled, not open: the technique crosses far too many for one to lead — Tuva, Mongolia, Altai, Khakassia, Tibet, Sardinia, the Xhosa, the Inuit. It is the umbrella, so it leads, and a country follows only if one ever earns it: `Throat-Singing;(Tuva)`, `Throat-Singing;(MNG)`. Nothing needs restructuring when the second tradition arrives.

**On the djembe carrying both a tradition and a country.** The instrument is Mandé, not Guinean — Mali, Burkina Faso, Ivory Coast and Senegal all play it — but the styles genuinely differ, Guinean playing having come through Sékou Touré's state ensembles into something more arranged than Malian. So both are kept, umbrella first: `(Mandé) Djembe;(GIN) Djembe`, and a Malian djembefola would read `(Mandé) Djembe;(MLI) Djembe`. Every djembe record gathers under the tradition, and the country still says which style. `(Mandé)` takes the same slot as `(Sápmi)` and `(Tibet)`: a cultural region ISO has no code for.

---

## 🔍 Keeping it honest

`rapt list genres -p <path>` sorts by value, so a convention that drifted shows itself: `(JP) Shakuhachi` lands directly above `(JPN) Shakuhachi`. No fuzzy matching, no threshold — sorting is the whole mechanism. Each genre names the artists carrying it, which turns a wrong count into a folder to go and edit.

Declarations live in `.genre` files: one genre, one line, nearest wins, never read from above the path given. `tags genre --force` clears every declaration beneath a path and writes one, which is how a convention gets rolled out when it changes.
