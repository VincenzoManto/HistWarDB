# HistWarDB Codebook

This codebook fixes the coding rules behind HistWarDB, so that two people
reading the same Wikidata snapshot reach the same dataset. It documents what
is already enforced in [`src/histwardb/query.py`](src/histwardb/query.py) and
[`src/histwardb/core.py`](src/histwardb/core.py), and should be updated
whenever those rules change.

## 1. Unit of analysis

A **conflict** is any Wikidata item that is an instance of `war` (`wd:Q198`)
or of one of its subclasses (`P31` then `P279*`: world war, civil war, proxy
war, war of independence, ...). There is no minimum-fatality threshold:
HistWarDB inherits Wikidata's own inclusion criteria rather than imposing a
COW-style battle-death cutoff. This is a deliberate scope choice that trades
precision for coverage across under-documented historical conflicts.

Items that are not typed under `war` are out of scope even when they describe
wars. The best-known case is "series of wars" items such as the Napoleonic
Wars.

### 1.1 Exclusions

A conflict item is dropped if any of its `P31` classes is, or is a subclass
of:

| Class | QID | Rationale |
|---|---|---|
| Peacekeeping mission | `wd:Q20082270` | Not a belligerent conflict |
| Military exercise | `wd:Q357104` | Simulated, not actual combat |

## 2. Belligerents

A participant (`P710`) is eligible as a belligerent only if one of its `P31`
classes is, or is a subclass of:

| Type | QID |
|---|---|
| Sovereign state | `wd:Q3624078` |
| Historical country | `wd:Q3024240` |
| Country | `wd:Q6256` |

This positive filter is what keeps non-state actors (people, armed groups,
alliances, sports teams) out.

### 2.1 No exclusion list on the belligerent side

Earlier versions also excluded participants typed as human, organization or
national sports team. That list was removed:

- In Wikidata, `sovereign state` and `historical country` are themselves
  subclasses of `organization` (`wd:Q43229`). Excluding organizations
  therefore excludes every state, including France and the United Kingdom.
- Humans and sports teams never pass the positive filter above, so excluding
  them changes nothing.

### 2.2 Identity

Every belligerent and every conflict is identified by its Wikidata QID
(`country_qid`, `conflict_qid`). English labels are for display only. When
two different entities share a label (for example the several "Kingdom of
Hungary" items), the QID is appended to the label so they stay separate.
A country's ISO 3166-1 alpha-3 code (`P298`) is carried as `iso3` where
Wikidata has one; historical states mostly do not.

Successor states are **not** merged: `Russian Empire`, `Soviet Union` and
`Russia` are three belligerents.

## 3. Entry and exit dates (temporal scope)

- **Conflict start**: `P580` (start time) — required. Conflicts without a
  start date are not retrievable by the query.
- **Conflict end**: `P582` (end time) if present; otherwise see Section 5.
- **Several dates on one conflict**: the earliest start and the latest
  recorded end are used.
- **Country lifespan**: `P571` (inception) and `P576` (dissolution), with
  `P582` used for dissolution only when no `P576` exists. When Wikidata lists
  several values, the earliest inception and the latest dissolution are used,
  so that each country has exactly one lifespan.
- **Window inclusion test**: a conflict overlaps a query chunk
  `[chunk_start, chunk_end)` if `start < chunk_end` and `effective_end >=
  chunk_start`. The last chunk's upper bound is `end_year + 1`, so conflicts
  starting in `end_year` itself are included. Chunking exists only to page
  around Wikidata's result limits and must not change which conflicts are
  included versus a single unchunked query.
- **Resolution**: years. Month and day are discarded.

## 4. Typology

HistWarDB currently codes a single, undifferentiated conflict type
("war" and its Wikidata subclasses). It does **not** yet distinguish:

- Interstate wars vs. civil wars vs. extra-state conflicts
- Militarized disputes below the threshold of war (MID-equivalent)

Any of these distinctions, if added, must be implemented as an additional
classification layer on top of the existing `CONFLICT_CLASS` filter (e.g. by
checking subclass membership under `wd:Q198` for civil-war or interstate-war
subtypes), not by changing the base inclusion rule in Section 1.

## 5. Missing end dates

A conflict without a recorded `P582` has an unknown duration. HistWarDB
never hides this: every such participation is flagged `end_imputed = True`,
and every country-year that is "at war" only because of the imputation is
flagged `War_Imputed = 1`. The start year itself is always treated as
observed.

How far an undated conflict is extended is set by the `missing_end`
parameter:

| `missing_end` | Rule |
|---|---|
| `"heuristic"` (default) | Started in 1800 or later: runs to the dataset's `end_year`. Started before 1800: lasts 5 years. |
| `"open"` | Runs to the dataset's `end_year`, whatever the start year. |
| `"start_only"` | Counted for its start year only. |
| `"drop"` | Discarded. |

Whatever the rule, the extension stops at the belligerent's dissolution
(Section 6).

The default is a heuristic, not a verified end date. In the 2026-10-13
snapshot it affects 44 of 1,438 participations and 910 of 12,184 at-war
country-years. Some of those conflicts are really ongoing; others simply
have incomplete Wikidata items (for example a "Chilean War of Independence"
item with a start date of 1827 and no end). For analysis, either filter on
`War_Imputed == 0` or use `missing_end="start_only"` as a robustness check.

If both `P580` (start) and `P582` (end) are present, the end year is clamped
to `max(end, start)` so a data-entry error producing an end date before the
start date cannot invert the conflict's span.

## 6. Post-fetch cleaning (`clean_dataframe`)

Raw records are reduced to one row per (conflict, country) participation:

1. **Unresolved-label exclusion.** Rows where the country label is still a
   raw Wikidata QID (regex `^Q\d+$`) are dropped: the entity has no English
   label, which is treated as insufficient evidence to code a belligerent.
   An unlabelled *conflict* is kept, with its QID as its name.
2. **One lifespan per country** (Section 3), capped by the manual table in
   Section 6.1.
3. **One interval per participation.** Duplicate rows for the same
   (conflict, country) pair are merged: earliest start, latest end. A
   recorded end date always takes precedence over an imputed one.
4. **Lifespan clipping.** The participation interval is clipped to the
   country's lifespan:
   `start_year = max(war_start_year, country_start)` and
   `end_year = min(war_end_year, country_end)`. If nothing is left the
   participation is dropped. A state is therefore never at war in a year in
   which it did not exist. Missing lifespan data imposes no constraint.
   The conflict's own dates are kept in `war_start_year` / `war_end_year`.
5. **Window.** Participations entirely outside the dataset's
   `[start_year, end_year]` are dropped.

### 6.1 Manual lifespan caps (`FORCED_MAX_YEARS`)

| State | Max year | Why |
|---|---|---|
| German Empire | 1918 | Dissolved at WWI armistice |
| Qing dynasty | 1912 | Ended at the Xinhai Revolution |
| Kingdom of Montenegro | 1918 | Absorbed into the Kingdom of Serbs, Croats and Slovenes |
| Principality of Montenegro | 1910 | Became the Kingdom of Montenegro |
| Russian Soviet Federative Socialist Republic | 1991 | Dissolution of the USSR |

The cap is combined with Wikidata's own dissolution date and the earlier of
the two wins. Participations are clipped to it, not dropped. Wikidata's `P576`
already gives the same year for the German Empire, the Qing dynasty, the
Kingdom of Montenegro and the Russian SFSR (checked October 2026), so for
those the table is only a safety net. Prefer fixing the source data on
Wikidata over adding entries.

## 7. Outputs

### 7.1 Participation table (`HistWarDB.participations`)

One row per (conflict, country). This is the primary table and the content
of a snapshot's `participations.csv`.

| Column | Meaning |
|---|---|
| `country`, `country_qid`, `iso3` | Belligerent label, Wikidata QID, ISO 3166-1 alpha-3 code (may be missing) |
| `conflict_name`, `conflict_qid` | Conflict label and Wikidata QID |
| `start_year`, `end_year` | Years this country is counted as taking part (after clipping) |
| `war_start_year`, `war_end_year` | The conflict's own first and last year |
| `end_imputed` | `True` when the conflict has no recorded end date |
| `country_start`, `country_end` | The country's lifespan; missing when Wikidata has no date |

### 7.2 Country × Year grid (`view="long"`)

Derived deterministically from the participation table
(`build_long_grid`): one row for every year in `[start_year, end_year]` for
every country that appears in at least one participation.

| Column | Meaning |
|---|---|
| `Country`, `Country_QID`, `ISO3` | Belligerent |
| `Year` | Calendar year |
| `War_State` | `1` if at least one participation covers the year, else `0` |
| `Active_Conflicts` | Names of those conflicts, sorted, joined with `"; "` |
| `State_Exists` | `0` if the year is outside the country's known lifespan, else `1` |
| `War_Imputed` | `1` if `War_State` is 1 only through a missing-end-date extension |

**Reading `War_State = 0`.** A zero means "no war recorded in Wikidata for
this country-year". Combine it with `State_Exists`:

- `War_State = 0`, `State_Exists = 1`: the state existed and no war is
  recorded. This is the closest the data gets to "peace", and it still
  includes wars missing from Wikidata.
- `State_Exists = 0`: the state did not exist. The cell is not an
  observation and must be excluded from any denominator. In the 2026-10-13
  snapshot this is 97,209 of 141,591 cells.

Pass `only_existing=True` to drop those rows; in the `binary` view they
then read `<NA>` instead of `0`.

### 7.3 Derived views

| View | Derivation |
|---|---|
| `long` | The grid itself |
| `binary` | Pivot of `War_State` by `Country` × `Year` |
| `names` | Pivot of `Active_Conflicts` by `Country` × `Year` |
| `list` | Deduplicated, sorted conflict names across the filtered long grid |
| `countries` | Sorted countries with `War_State == 1` in the filtered window |
| `conflicts` | Sorted, deduplicated conflict names among `War_State == 1` rows |

Query-time filters are applied to the grid before a view is rendered:

- `countries` matches exactly, ignoring case, on the label, the QID or the
  ISO code. `"Russia"` does not match `"Prussia"`. A name that matches
  nothing raises a warning with close matches.
- `keyword` is a literal, case-insensitive substring search in
  `Active_Conflicts`. It is not a regular expression.

## 8. Retrieval and reproducibility

- Data comes from the public Wikidata Query Service. Two kinds of query are
  issued: a participation query per 25-year chunk, then entity queries for
  labels, lifespans and ISO codes. They are separate because the combined
  query times out on the public endpoint.
- Each query is retried with exponential backoff. If a chunk still fails,
  the fetch raises `FetchError` (`strict=True`, the default). With
  `strict=False` the chunk is skipped with a warning and listed in
  `failed_chunks`. A chunk that returns as many rows as the query `LIMIT`
  is treated as truncated and is an error.
- Wikidata changes continuously, so a live fetch is **not** reproducible.
  Published results must cite a frozen snapshot (`save_snapshot` /
  `from_snapshot`). A snapshot holds `participations.csv` and
  `metadata.json` (retrieval time, parameters, query text, row counts and a
  SHA-256 checksum that is verified on load). Snapshots shipped with the
  repository live in [`data/snapshots/`](data/snapshots/).

## 9. Known limitations

- Coverage and accuracy are bounded by Wikidata's own curation; HistWarDB
  does not independently verify fatality counts, dates, or belligerent lists.
  It has not yet been validated against Correlates of War, UCDP/PRIO or
  Brecke's Conflict Catalog.
- `P710` (participant) records participation only. It does not encode
  side/alliance (e.g. Allied vs. Axis), role, or per-participant entry and
  exit dates, and it is incomplete for major wars: in the 2026-10-13
  snapshot World War II has 8 state participants and World War I has 4.
- A country appears in the grid only if it has at least one recorded
  participation. States never recorded at war are absent, so the grid is not
  a complete state-year panel.
- No battle-death threshold means the dataset mixes major wars with minor,
  sparsely documented conflicts, and with items that are not single wars
  (fronts, long-running "conflicts", and occasionally list articles typed
  as wars).
- The missing-end-date rule (Section 5) is a stopgap for incomplete upstream
  data and is the largest source of uncertainty below the SPARQL filter
  layer.
- Chunked fetching (Section 3) is purely a pagination mechanism; any
  difference in results between chunked and unchunked queries over the same
  window is a bug, not an intended behavior.
