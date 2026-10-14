# Changelog

All notable changes to HistWarDB. Data snapshots are listed under the
release that first ships them.

## Unreleased

### Added
- Conflict structure (#3): `conflict_type` (Wikidata classes), `parent_conflict_qid`
  and `root_conflict_qid` from `P361` (part of) chains, and a `subconflicts=`
  option (`keep`, `drop`, `roll_up`) to avoid counting a front or campaign as a
  separate war.
- Sides and roles (#12): coalition participants (e.g. "Axis Powers") are expanded
  into their member states with a `side`; `P710` qualifiers give each
  participant's `role` and own start/end dates; new `dyads` and `conflicts`
  tables.
- Country crosswalk (#8): Correlates of War and Gleditsch-Ward codes
  (`cow_code`, `gw_code`) for system-member states, shipped with the package
  and built by `scripts/build_crosswalk.py`.
- Validation (#11): `scripts/validate.py` and `validation/report.md` comparing
  a snapshot with COW inter-/extra-state wars and UCDP/PRIO.
- SPARQL syntax tests with rdflib, and a weekly workflow running the live
  checks (#13). `.zenodo.json` so each GitHub release is archived with its data
  snapshot (#10).
- From PR #15: participation and entity queries split so a live fetch no
  longer times out; retries and `FetchError`; lifespan clipping; QIDs and ISO
  codes; `State_Exists` and `War_Imputed`; snapshots with checksums; opt-in
  live tests (#2, #4-#10, #13).

### Changed
- Hypothetical, interstellar and fictional wars are excluded (Wikidata types
  them as subclasses of war).
- The organization exclusion from 0.3.0 is removed: sovereign states are
  subclasses of organization in Wikidata, so it excluded every state (#1).

### Fixed
- A live fetch with 0.3.0 returns an empty dataset (timeouts plus the
  organization exclusion); fixed by PR #15.

## 0.3.0 - 2026-10-14

- Optional severity threshold `min_casualties` (`P1120`), configurable
  missing-end-date heuristic, `Duration_Estimated` flag (#14).
- Exclusion QIDs corrected, `P279*` path fixed, exact `countries=` match,
  last-chunk boundary fix (#1, #2, #7, #9).
- Codebook.

## 0.1.0 - 2026-10-08

- First public version.

A commit titled "Release v0.2.0" exists in the history, but no 0.2.0 tag or
package was published; the release after 0.1.0 is 0.3.0.
