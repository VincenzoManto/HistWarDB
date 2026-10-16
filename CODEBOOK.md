# HistWarDB Codebook

This codebook fixes the coding rules behind HistWarDB, so that two people
reading the same Wikidata snapshot reach the same dataset. It documents what
is already enforced in [`src/histwardb/query.py`](src/histwardb/query.py) and
[`src/histwardb/core.py`](src/histwardb/core.py), and should be updated
whenever those rules change.

## 1. Unit of analysis

A **conflict** is any Wikidata item that is an instance of, or subclass of,
`war` (`wd:Q198`). By default there is no minimum-fatality threshold:
HistWarDB inherits Wikidata's own inclusion criteria rather than imposing a
COW-style battle-death cutoff. This is a deliberate scope choice that trades
precision for coverage across under-documented historical conflicts.

### 1.2 Optional severity threshold (`min_casualties`)

Because "no threshold" mixes a world war with a two-day, zero-impact local
rebellion on the same analytical plane, callers can opt into a minimum
severity filter via `HistWarDB(min_casualties=...)` (see
[`clean_dataframe`](src/histwardb/core.py)):

- The query fetches each conflict's recorded death toll via Wikidata property
  `P1120` (`CASUALTIES_PROPERTY` in
  [`query.py`](src/histwardb/query.py)), as an `OPTIONAL` triple — a
  conflict is never dropped merely for lacking this field.
- When `min_casualties` is set, a conflict whose *recorded* `P1120` value is
  below the threshold is dropped. A conflict with **no** recorded value is
  kept regardless, because Wikidata's casualty-figure coverage is too sparse
  to treat "unknown" as "zero" — doing so would silently delete most
  pre-modern conflicts.
- Default is `None` (no filtering), matching prior behavior exactly; this is
  an opt-in refinement, not a change to the base inclusion rule in Section 1.

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

- If `start_year >= HEURISTIC_CUTOFF_YEAR` (default `1800`): the conflict is
  assumed to run through the dataset's `end_year` (i.e. still
  open/contemporary enough that Wikidata's silence plausibly means "ongoing
  or recently closed, not yet dated").
- If `start_year < HEURISTIC_CUTOFF_YEAR`: the conflict is assumed to last
  `PRE_CUTOFF_DEFAULT_DURATION_YEARS` years (default `5`, i.e.
  `start_year + 5`), a deliberately short default so that a single undated
  pre-1800 record cannot inflate a historical state's "at war" years across
  centuries.

Both the cutoff year and the default duration are named constants in
[`core.py`](src/histwardb/core.py) and are configurable per-instance via
`HistWarDB(heuristic_cutoff_year=..., pre_cutoff_duration_years=...)` — they
are rough, overridable estimates, not fixed law.

This is a heuristic, not a verified end date. Read it as "unknown duration,
capped," rather than a date Wikidata actually recorded. It is the single
largest source of uncertainty in the dataset below the SPARQL filter layer
itself. Every country-year cell produced from this fallback (rather than
from a recorded `P582` end date) is flagged `Duration_Estimated = True` in
the long grid (Section 7), so this uncertainty is visible in the output
instead of being indistinguishable from sourced data.

If both `P580` (start) and `P582` (end) are present, `finish_year` is
clamped to `max(finish_year, start_year)` so a data-entry error producing an
end date before the start date cannot invert the conflict's span; when this
clamp fires, the end date's month/day are discarded along with it (they no
longer correspond to the clamped year).

### 5.1 Date precision (month/day)

Wikidata always serializes a date value as a full `+YYYY-MM-DDTHH:MM:SSZ`
literal, even when the underlying statement only actually asserts a year or
a month — the unused components are silently filled with `01`. Reading the
literal alone cannot tell you which parts are real.

To avoid fabricating precision Wikidata never asserted, the query
([`build_sparql_query`](src/histwardb/query.py)) fetches each date's
`wikibase:timePrecision` via the statement's value node (`p:P580/psv:P580`
and `p:P582/psv:P582`), and [`parse_date_parts`](src/histwardb/core.py) uses
it to decide what to keep:

| `timePrecision` | Meaning | Columns populated |
|---|---|---|
| `11` | Day | year, month, day |
| `10` | Month | year, month |
| `9` or coarser | Year or less precise | year only |

A missing precision value (e.g. a hand-built record bypassing the live
query) is treated as day precision for backward compatibility. Every
conflict record carries `start_year`/`start_month`/`start_day` and
`end_year`/`end_month`/`end_day` — not a single ambiguous date string — with
the unknown components set to `None` rather than defaulted to `01`.

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

Cleaned records are expanded into a dense **Country × Year × Month** grid
covering every month of every year in `[start_year, end_year]` for every
country appearing in the cleaned data (`build_long_grid`), not just the
months where the country has a recorded conflict. A conflict with real
month/day precision (Section 5.1) only lights up the months it actually
covers; a conflict known only to year precision is treated as covering all
12 months of that year, rather than fabricating a specific month. A
country-year-month cell is marked `War_State = 1` if any cleaned conflict
record covers that month for that country; `Active_Conflicts` concatenates
the names of all such conflicts (`"; "`-joined). This long grid is the
source of truth for the aggregated views:

| View | Derivation |
|---|---|
| `long` | The grid itself (`Country`, `Year`, `Month`, `War_State`, `Active_Conflicts`, `Duration_Estimated`) |
| `binary` | Pivot of `War_State` by `Country` × `"YYYY-MM"` period |
| `names` | Pivot of `Active_Conflicts` by `Country` × `"YYYY-MM"` period |
| `list` | Deduplicated, sorted conflict names across the filtered long grid |
| `countries` | Sorted countries with `War_State == 1` in the filtered window |
| `conflicts` | Sorted, deduplicated conflict names among `War_State == 1` rows |
| `records` | One row per cleaned conflict (`build_records_view`): `Country`, `Conflict_Name`, `Start_Year/Month/Day`, `End_Year/Month/Day`, `Duration_Estimated`, `Casualties` |

The `records` view exists because a single grid cell can contain several
overlapping conflicts with different date precisions — a single pair of
date columns on the grid itself would be ambiguous whenever more than one
conflict is active. `records` instead carries the precise per-conflict
dates from Section 5.1 directly, unaggregated.

Query-time filters (`countries`, `from_date`/`to_date`, `only_in_war`,
`keyword`) are applied before a view is rendered. `from_date`/`to_date`
accept a plain year (`"YYYY"` or `int`), or `"YYYY-MM"` for a specific
month; an omitted month defaults to January for `from_date` and December
for `to_date`, so year-only bounds still select whole years. For the
`records` view, these filters test for date-range overlap against
`Start_Year/Start_Month` and `End_Year/End_Month` instead of the grid's
`Year`/`Month`. A `keyword` filter on the aggregated views matches against
the already-concatenated `Active_Conflicts` string (substring,
case-insensitive); on `records` it matches `Conflict_Name` directly.

## 8. Known limitations

- Coverage and accuracy are bounded by Wikidata's own curation; HistWarDB
  does not independently verify fatality counts, dates, or belligerent lists.
- No battle-death threshold by default means the dataset mixes major wars
  with minor, sparsely documented conflicts, unless the caller opts into
  `min_casualties` (Section 1.2) — and even then, only conflicts with a
  recorded `P1120` figure below the threshold are excluded; conflicts
  Wikidata never annotated with a death toll are not filtered by severity at
  all.
- `P710` (participant) is used as the belligerent-linking property; it does
  not encode side/alliance (e.g. Allied vs. Axis), only participation, and
  may include purely formal participation with no real economic or military
  impact on the listed state. `min_casualties` (Section 1.2) is the primary
  lever for suppressing this kind of low-signal `P710` noise.
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
