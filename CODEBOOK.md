# HistWarDB Codebook

This codebook fixes the coding rules behind HistWarDB, so that two people
reading the same Wikidata snapshot reach the same dataset. It documents what
is already enforced in [`src/histwardb/query.py`](src/histwardb/query.py) and
[`src/histwardb/core.py`](src/histwardb/core.py), and should be updated
whenever those rules change.

## 1. Unit of analysis

A **conflict** is any Wikidata item that is an instance of, or subclass of,
`war` (`wd:Q198`). There is no minimum-fatality threshold: HistWarDB inherits
Wikidata's own inclusion criteria rather than imposing a COW-style battle-death
cutoff. This is a deliberate scope choice that trades precision for coverage
across under-documented historical conflicts.

### 1.1 Exclusions

A conflict item is dropped if it (or any superclass via `P279*`) matches:

| Class | QID | Rationale |
|---|---|---|
| Peacekeeping mission | `wd:Q20082270` | Not a belligerent conflict |
| Military exercise | `wd:Q357104` | Simulated, not actual combat |

## 2. Belligerents

A **country** entity is eligible as a belligerent side only if it is an
instance/subclass of one of:

| Type | QID |
|---|---|
| Sovereign state | `wd:Q3624078` |
| Historical country | `wd:Q3024240` |
| Country | `wd:Q6256` |

### 2.1 Exclusions

A country-side entity is dropped if it matches:

| Class | QID | Rationale |
|---|---|---|
| Human | `wd:Q5` | Individuals are not state belligerents |
| Organization | `wd:Q43229` | Non-state actor, out of scope |
| National sports team | `wd:Q1194951` | Known Wikidata false positive via `P710` |

## 3. Entry and exit dates (temporal scope)

- **Conflict start**: `P580` (start time) — required. Conflicts without a
  start date are not retrievable by the query.
- **Conflict end**: `P582` (end time) if present; otherwise the conflict is
  treated as ongoing through the dataset's configured `end_year`
  (`COALESCE(?endDate, "{end_year}-12-31")`).
- **Country lifespan**: `P571` (inception) / `P576` (dissolution), with `P582`
  as a fallback for the dissolution date. These are used for sanity-checking
  that a historical state existed at the time it is listed as a belligerent,
  not for filtering the conflict itself.
- **Window inclusion test**: a conflict overlaps a query chunk
  `[chunk_start, chunk_end)` if `start < chunk_end` and `effective_end >=
  chunk_start`. Chunking exists only to page around Wikidata's result limits
  and must not change which conflicts are included versus a single
  unchunked query.

## 4. Typology

HistWarDB currently codes a single, undifferentiated conflict type
("war" and its Wikidata subclasses). It does **not** yet distinguish:

- Interstate wars vs. civil wars vs. extra-state conflicts
- Militarized disputes below the threshold of war (MID-equivalent)

Any of these distinctions, if added, must be implemented as an additional
classification layer on top of the existing `CONFLICT_CLASS` filter (e.g. by
checking subclass membership under `wd:Q198` for civil-war or interstate-war
subtypes), not by changing the base inclusion rule in Section 1.

## 5. Missing-end-date heuristic

A conflict without a recorded `P582` (end time) is not treated as
indefinitely ongoing when building per-record `end_year`
([`parse_bindings`](src/histwardb/core.py)):

- If `start_year >= 1800`: the conflict is assumed to run through the
  dataset's `end_year` (i.e. still open/contemporary enough that Wikidata's
  silence plausibly means "ongoing or recently closed, not yet dated").
- If `start_year < 1800`: the conflict is assumed to last 5 years
  (`start_year + 5`), a deliberately short default so that a single
  undated pre-1800 record cannot inflate a historical state's "at war" years
  across centuries.

This is a heuristic, not a verified end date. Read it as "unknown duration,
capped," rather than a date Wikidata actually recorded. It is the single
largest source of uncertainty in the dataset below the SPARQL filter layer
itself.

If both `P580` (start) and `P582` (end) are present, `finish_year` is
clamped to `max(finish_year, start_year)` so a data-entry error producing an
end date before the start date cannot invert the conflict's span.

## 6. Post-fetch cleaning (`clean_dataframe`)

Four filters apply, in order, after parsing and before a record counts as
"at war" for any country-year cell. Each is intentionally conservative: when
in doubt, a record is kept rather than dropped, except where noted.

1. **Deduplication.** Exact-duplicate `(country, conflict, dates)` rows are
   dropped (`drop_duplicates`).
2. **Unresolved-label exclusion.** Rows where `country` is still a raw
   Wikidata QID (regex `^Q\d+$`) are dropped — this means the SPARQL label
   service failed to resolve a human-readable name, which is treated as
   insufficient evidence to code a belligerent, not as a valid entity.
3. **Forced-dissolution anachronism filter.** A small manually curated table,
   `FORCED_MAX_YEARS` (see Section 6.1), overrides Wikidata where its
   `P576`/`P582` dissolution dates are known to be unreliable for specific
   historical states. Any record where `start_year` or `end_year` exceeds the
   state's forced maximum year is dropped outright, even if Wikidata itself
   lists the state or war as still open.
4. **Window and lifespan consistency.**
   - A conflict is discarded if it falls entirely outside the dataset's
     requested `[start_year, end_year]` window (`end_year_row < start_year`
     or `start_year_row > end_year`).
   - A conflict is discarded if it is chronologically inconsistent with the
     belligerent's own lifespan, when Wikidata provides `country_start`
     (`P571`) / `country_end` (`P576`/`P582`): the war's end must be
     `>= country_start`, and the war's start must be `<= country_end`.
     Missing lifespan data is treated as "no constraint" (`isna()` passes),
     not as a reason to drop the record — the filter only fires on a
     positive contradiction, never on absence of data.

### 6.1 Forced dissolution table (`FORCED_MAX_YEARS`)

| State | Forced max year | Why |
|---|---|---|
| German Empire | 1918 | Dissolved at WWI armistice; Wikidata's structured dates for this entity are inconsistently captured |
| Qing dynasty | 1912 | Ended at the Xinhai Revolution |
| Kingdom of Montenegro | 1918 | Absorbed into the Kingdom of Serbs, Croats and Slovenes |
| Principality of Montenegro | 1910 | Became the Kingdom of Montenegro |
| Russian Soviet Federative Socialist Republic | 1991 | Dissolution of the USSR |

This table exists because upstream `P576`/`P582` data was found to produce
anachronistic results (e.g. a "German Empire" war recorded after 1918). An
entry here is a last-resort patch for a confirmed Wikidata data gap. Prefer
fixing the source data on Wikidata itself when possible, since this table
has to be maintained by hand and will silently drift out of date.

## 7. Grid construction and derived views

Cleaned records are expanded into a dense **Country × Year** grid covering
every year in `[start_year, end_year]` for every country appearing in the
cleaned data (`build_long_grid`), not just the years where the country has a
recorded conflict. A country-year cell is marked `War_State = 1` if any
cleaned conflict record covers that year for that country; `Active_Conflicts`
concatenates the names of all such conflicts (`"; "`-joined). This long grid
is the single source of truth all other views are derived from:

| View | Derivation |
|---|---|
| `long` | The grid itself (`Country`, `Year`, `War_State`, `Active_Conflicts`) |
| `binary` | Pivot of `War_State` by `Country` × `Year` |
| `names` | Pivot of `Active_Conflicts` by `Country` × `Year` |
| `list` | Deduplicated, sorted conflict names across the filtered long grid |
| `countries` | Sorted countries with `War_State == 1` in the filtered window |
| `conflicts` | Sorted, deduplicated conflict names among `War_State == 1` rows |

Query-time filters (`countries`, `from_date`/`to_date`, `only_in_war`,
`keyword`) are applied to this grid before a view is rendered, so e.g. a
`keyword` filter matches against the already-concatenated
`Active_Conflicts` string, substring/case-insensitive, not against the
underlying conflict's full metadata.

## 8. Known limitations

- Coverage and accuracy are bounded by Wikidata's own curation; HistWarDB
  does not independently verify fatality counts, dates, or belligerent lists.
- No battle-death threshold means the dataset mixes major wars with minor,
  sparsely documented conflicts.
- `P710` (participant) is used as the belligerent-linking property; it does
  not encode side/alliance (e.g. Allied vs. Axis), only participation.
- The missing-end-date heuristic (Section 5) and the hand-maintained
  `FORCED_MAX_YEARS` table (Section 6.1) are both stopgaps for incomplete or
  inconsistent upstream data; both should shrink, not grow, as Wikidata's own
  coverage improves. A growing `FORCED_MAX_YEARS` table is a signal that the
  underlying SPARQL filters or upstream data need attention, not that more
  entries should be added reactively.
- Chunked fetching (Section 3) is purely a pagination mechanism around
  Wikidata's query result limits; any difference in results between chunked
  and unchunked queries over the same window is a bug, not an intended
  behavior.
