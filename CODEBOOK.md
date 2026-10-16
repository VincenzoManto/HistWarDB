# HistWarDB Codebook

This codebook fixes the coding rules behind HistWarDB, so that two people
reading the same Wikidata snapshot reach the same dataset. It documents what
is already enforced in [`src/histwardb/query.py`](src/histwardb/query.py) and
[`src/histwardb/core.py`](src/histwardb/core.py), and should be updated
whenever those rules change.

## 1. Unit of analysis

A **conflict** is any Wikidata item that is an instance of `war` (`wd:Q198`)
or of one of its subclasses (`P31` then `P279*`: world war, civil war, proxy
war, war of independence, ...). By default there is no minimum-fatality
threshold: HistWarDB inherits Wikidata's own inclusion criteria rather than
imposing a COW-style battle-death cutoff. This is a deliberate scope choice that trades
precision for coverage across under-documented historical conflicts.

Wikidata's class tree decides what is in scope, and it is broader than a
war list would be. In the 2026-10-14 snapshot, 42 of the 502 conflicts are
typed "series of wars" (e.g. Balkan Wars, Anglo-Burmese Wars, American
Indian Wars), which Wikidata treats as a subclass of war. They overlap the
individual wars they group. Pass `series_of_wars="drop"` (or filter on
`conflict_type`) to leave them out when counting wars. Conversely, a war item that
lacks `P580` (start time) or a state participant is not retrieved at all.

### 1.1 Exclusions

A conflict item is dropped if any of its `P31` classes is, or is a subclass
of:

| Class | QID | Rationale |
|---|---|---|
| Peacekeeping mission | `wd:Q20082270` | Not a belligerent conflict |
| Military exercise | `wd:Q357104` | Simulated, not actual combat |
| Hypothetical war | `wd:Q124042044` | Did not happen |
| Interstellar war | `wd:Q2661982` | Fiction (e.g. "Space War I") |
| Fictional war | `wd:Q17198419` | Fiction |

The broader class "fictional entity" cannot be used: excluding it would make
the query enumerate every fictional item in Wikidata and time out.

### 1.3 Conflict structure (`conflict_type`, parents, roots)

- `conflict_type` lists the conflict's own Wikidata classes (`P31`, English
  labels joined with `"; "`), e.g. `war`, `civil war`, `world war`,
  `war of independence`, `colonial war`. It is descriptive only: the base
  inclusion rule in Section 1 does not depend on it.
- Many conflicts are recorded as part of (`P361`) another war: fronts,
  campaigns, phases. HistWarDB follows `P361` chains (through non-war items
  such as theatres, up to 8 levels) and records:
  - `parent_conflict_qid`: the nearest ancestor that is itself a war;
  - `root_conflict_qid` / `root_conflict_name`: the topmost such ancestor, or
    the conflict itself when it has none.
  Items typed "series of wars" (e.g. Napoleonic Wars, German–Russian wars)
  are never parents or roots, because they group wars that are separate
  units of analysis.
- `subconflicts=` decides how sub-conflicts are counted:

| `subconflicts` | Effect |
|---|---|
| `"keep"` (default) | A front and its war are separate conflicts. A state fighting on a front is counted in both. |
| `"drop"` | Sub-conflicts whose root war is in the dataset are dropped. |
| `"roll_up"` | Sub-conflict participations are re-attributed to the root war. This also recovers states that Wikidata lists only on a front or campaign. |

`"roll_up"` is the closest to COW's unit of analysis (one war per
belligerent per period) and is recommended for counts of wars.

### 1.2 Optional severity threshold (`min_casualties`)

Because "no threshold" mixes a world war with a two-day local rebellion,
callers can opt into a minimum severity filter with
`HistWarDB(min_casualties=...)`:

- Each conflict's recorded death toll is fetched from Wikidata property
  `P1120` (`CASUALTIES_PROPERTY` in [`query.py`](src/histwardb/query.py)) as
  an `OPTIONAL` field of the conflict entity query. When several estimates
  are recorded, the largest is used, which keeps the filter conservative.
  It is carried as `casualties` in the participation table.
- When `min_casualties` is set, a conflict whose *recorded* toll is below the
  threshold is dropped. A conflict with **no** recorded toll is kept, because
  treating "unknown" as "zero" would silently delete most conflicts.
- Default is `None` (no filtering).

**Coverage caveat.** In the 2026-10-14 snapshot only 19 of 502 conflicts have
a `P1120` value, and `min_casualties=1000` removes 4 of them. Until Wikidata's
casualty coverage improves, this filter barely changes the dataset and cannot
be used to approximate a COW-style threshold.

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

### 2.3 Correlates of War and Gleditsch–Ward codes

Wikidata has no property for COW or Gleditsch–Ward codes, so HistWarDB ships
its own crosswalk ([`src/histwardb/data/crosswalk.csv`](src/histwardb/data/crosswalk.csv),
built by [`scripts/build_crosswalk.py`](scripts/build_crosswalk.py)) and adds
`cow_code` / `gw_code` to each participation and `COW_Code` / `GW_Code` to the
grid:

- historical predecessors are assigned by hand to the system member they are
  coded as (e.g. Kingdom of Prussia, German Empire, Nazi Germany → COW 255;
  Ottoman Empire → 640; Kingdom of Sardinia → 325; Qing dynasty → 710);
- other states are matched when their English label or an alias equals a
  COW / G-W state name and their lifespans overlap;
- states that were never members of the interstate system (colonies,
  protectorates, sub-national and most pre-1816 states) have no code.

Several QIDs can share a code (France's successive regimes are all COW 220).
A code does not imply the state was a system member in every year it appears.

### 2.4 Sides, coalitions and roles

`P710` records participation only. Two other sources give more:

- **Coalitions.** When a participant is an alliance (`wd:Q878249`, military
  alliance `wd:Q1127126`, multinational military coalition `wd:Q100906234`, or
  a subclass), its member states (`P527` on the coalition, or `P463` on the
  state) are added as participants with `via_coalition = True`, and every
  member gets `side` / `side_qid` = that coalition. A direct participant that
  is also a member gets the coalition as its side. A state listed in two
  coalitions of the same conflict gets both, joined with `"; "`, and is left
  out of the dyads. Coalition members get the conflict's dates; Wikidata's
  membership dates are not used.
- **Qualifiers.** `role` holds the `P3831` (object has role) labels recorded on
  the `P710` statement (e.g. "aggressor", "defender"), and `P580` / `P582`
  qualifiers clip that state's participation to its own entry and exit years.

`dyads` lists every pair of states on different single known sides of the
same conflict, with the years their participations overlap.

These sources are sparse: most wars have no coalition participant and no
qualifiers, so most participations have no side. Section 9 gives the
coverage.

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
never hides this. Every such participation is flagged
`duration_estimated = True`. In the Country × Year × Month grid, every
month covered by such a conflict is flagged `Duration_Estimated = True`,
and every month that is "at war" *only* because of the estimate is flagged
`War_Imputed = 1` (the conflict's own start month counts as observed, and a
month also covered by a dated conflict is not imputed).

How far an undated conflict is extended is set by the `missing_end`
parameter:

| `missing_end` | Rule |
|---|---|
| `"heuristic"` (default) | Started in `HEURISTIC_CUTOFF_YEAR` (1800) or later: runs to the dataset's `end_year`. Started earlier: lasts `PRE_CUTOFF_DEFAULT_DURATION_YEARS` (5) years. Both are configurable via `HistWarDB(heuristic_cutoff_year=..., pre_cutoff_duration_years=...)`. |
| `"open"` | Runs to the dataset's `end_year`, whatever the start year. |
| `"start_only"` | Counted for its start year only. |
| `"drop"` | Discarded. |

Whatever the rule, the extension stops at the belligerent's dissolution
(Section 6).

The default is a heuristic, not a verified end date. In the 2026-10-14
snapshot it affects 44 of 1,500 participations and 910 of 12,520 at-war
country-years. Some of those conflicts are really ongoing; others simply
have incomplete Wikidata items (for example a "Chilean War of Independence"
item with a start date of 1827 and no end). For analysis, either filter on
`War_Imputed == 0` or use `missing_end="start_only"` as a robustness check.

If both `P580` (start) and `P582` (end) are present, the end year is clamped
to `max(end, start)` so a data-entry error producing an end date before the
start date cannot invert the conflict's span; when the clamp fires, the end
date's month/day are discarded along with it (Section 5.1).

### 5.1 Date precision (month/day)

Wikidata always serializes a date as a full `+YYYY-MM-DDTHH:MM:SSZ` value,
even when the underlying statement only actually asserts a year or a month
-- the unused components are silently filled with `01`. Reading the literal
alone cannot tell which parts are real.

To avoid fabricating precision Wikidata never asserted, the participation
query fetches each date's `wikibase:timePrecision` via the statement's value
node (`p:P580/psv:P580`, `p:P582/psv:P582`), and `parse_date_parts` uses it
to decide what to keep:

| `timePrecision` | Meaning | Kept |
|---|---|---|
| `11` | Day | year, month, day |
| `10` | Month | year, month |
| `9` or coarser | Year or less precise | year only |

Every participation carries `start_month`/`start_day` and
`end_month`/`end_day` alongside the year columns, with unknown components
`None` rather than defaulted to `01`. The conflict's own (unclipped)
`war_start_month`/`war_start_day`/`war_end_month`/`war_end_day` are kept
separately: when a participation's `start_year`/`end_year` is clipped away
from the conflict's own date by a participant or country-lifespan bound
(Section 6), the clipped `start_month`/`end_month`/`*_day` are `None` --
those bounds are year-only in Wikidata, so no real month survives the clip.

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
| `start_month`, `start_day`, `end_month`, `end_day` | Month/day of the clipped start and end, to the precision Wikidata actually recorded (Section 5.1); `None` when only the year is known, or when the clip came from a year-only bound |
| `war_start_year`, `war_end_year` | The conflict's own first and last year |
| `war_start_month`, `war_start_day`, `war_end_month`, `war_end_day` | Month/day of the conflict's own (unclipped) start/end (Section 5.1) |
| `duration_estimated` | `True` when the conflict has no recorded end date |
| `casualties` | Largest recorded death toll of the conflict (`P1120`); usually missing |
| `conflict_type` | The conflict's Wikidata classes (Section 1.3) |
| `parent_conflict_qid`, `root_conflict_qid`, `root_conflict_name` | Part-of structure (Section 1.3) |
| `side`, `side_qid` | Coalition side, when known (Section 2.4) |
| `via_coalition` | `True` when the state is listed only through a coalition |
| `role` | `P3831` role qualifiers on the participation |
| `cow_code`, `gw_code` | Correlates of War / Gleditsch–Ward codes (Section 2.3) |
| `country_start`, `country_end` | The country's lifespan; missing when Wikidata has no date |

### 7.2 Country × Year × Month grid (`view="long"`)

Derived deterministically from the participation table (`build_long_grid`):
one row for every month of every year in `[start_year, end_year]` for every
country that appears in at least one participation. A participation known
to real month precision (Section 5.1) only lights up the months it actually
covers; one known only to year precision covers all 12 months of that year,
rather than fabricating a specific month.

| Column | Meaning |
|---|---|
| `Country`, `Country_QID`, `ISO3`, `COW_Code`, `GW_Code` | Belligerent |
| `Year`, `Month` | Calendar year and month (1-12) |
| `War_State` | `1` if at least one participation covers the month, else `0` |
| `Active_Conflicts` | Names of those conflicts, sorted, joined with `"; "` |
| `Duration_Estimated` | `True` if any active conflict has no recorded end date |
| `State_Exists` | `0` if the year is outside the country's known lifespan, else `1` |
| `War_Imputed` | `1` if `War_State` is 1 only through a missing-end-date extension |

`War_Imputed`'s "the start counts as observed" exception (Section 5) is now
month-grained: only the conflict's own start *month* is observed, not the
rest of its start year, so a year-precision conflict shows `War_Imputed = 1`
for 11 of its 12 start-year months.

**Reading `War_State = 0`.** A zero means "no war recorded in Wikidata for
this country-year". Combine it with `State_Exists`:

- `War_State = 0`, `State_Exists = 1`: the state existed and no war is
  recorded. This is the closest the data gets to "peace", and it still
  includes wars missing from Wikidata.
- `State_Exists = 0`: the state did not exist. The cell is not an
  observation and must be excluded from any denominator (the cell count in
  the 2026-10-14 snapshot's narrative predates the move to monthly cells).

Pass `only_existing=True` to drop those rows; in the `binary` view they
then read `<NA>` instead of `0`.

### 7.3 Conflict and dyad tables

- `HistWarDB.conflicts`: one row per conflict with `conflict_type`, parent and
  root war, first and last year, `duration_estimated`, `casualties`,
  `n_states` and `n_states_with_side`.
- `HistWarDB.dyads`: one row per pair of states on opposing known sides of a
  conflict (`country_a`, `side_a`, `country_b`, `side_b`, overlap years).

### 7.4 Derived views

| View | Derivation |
|---|---|
| `long` | The grid itself |
| `binary` | Pivot of `War_State` by `Country` × `"YYYY-MM"` period |
| `names` | Pivot of `Active_Conflicts` by `Country` × `"YYYY-MM"` period |
| `list` | Deduplicated, sorted conflict names across the filtered long grid |
| `countries` | Sorted countries with `War_State == 1` in the filtered window |
| `conflicts` | Sorted, deduplicated conflict names among `War_State == 1` rows |

`from_date`/`to_date` accept a plain year (`"YYYY"` or `int`) or a specific
month (`"YYYY-MM"`); an omitted month defaults to January for `from_date`
and December for `to_date`, so year-only bounds still select whole years.

Query-time filters are applied to the grid before a view is rendered:

- `countries` matches exactly, ignoring case, on the label, the QID or the
  ISO code. `"Russia"` does not match `"Prussia"`. A name that matches
  nothing raises a warning with close matches.
- `keyword` is a literal, case-insensitive substring search in
  `Active_Conflicts`. It is not a regular expression.

## 8. Retrieval and reproducibility

- Data comes from the public Wikidata Query Service. Two kinds of query are
  issued: a participation query per 25-year chunk, then entity queries for
  labels, lifespans, ISO codes and death tolls. They are separate because the combined
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
  Agreement with Correlates of War and UCDP/PRIO is measured in
  [`validation/report.md`](validation/report.md): for the 2026-10-14 snapshot,
  79% of COW inter-state wars are matched (62% of those starting before 1900),
  and 90% of COW inter-state war-years are also at war in HistWarDB. It has not
  been compared with Brecke's Conflict Catalog or COW intra-state wars.
- Belligerent lists are incomplete for major wars. Coalition expansion brings
  World War II to 35 states and World War I to 16 (46 and 26 with
  `subconflicts="roll_up"`); COW codes 25 and 15 system members.
- Sides are known only where Wikidata lists a coalition as a participant: 84
  of 1,500 participations, in 6 conflicts, and dyads exist only for the two
  world wars. Where both sources code a side, they agree with COW on all 185
  comparable state pairs. Coalition members get the whole war's dates, so a
  state that joined late (e.g. the United States in 1941) is counted from the
  war's start unless a `P580` qualifier says otherwise. Role qualifiers are
  recorded on 7 participations only.
- A country appears in the grid only if it has at least one recorded
  participation. States never recorded at war are absent, so the grid is not
  a complete state-year panel.
- No battle-death threshold by default means the dataset mixes major wars
  with minor, sparsely documented conflicts, and with items that are not single wars
  (fronts, long-running "conflicts", and occasionally list articles typed
  as wars). The optional `min_casualties` filter (Section 1.2) cannot fix
  this yet, because few conflicts have a recorded death toll.
- The missing-end-date rule (Section 5) is a stopgap for incomplete upstream
  data and is the largest source of uncertainty below the SPARQL filter
  layer.
- Chunked fetching (Section 3) is purely a pagination mechanism; any
  difference in results between chunked and unchunked queries over the same
  window is a bug, not an intended behavior.
