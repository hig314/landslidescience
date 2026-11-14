# Inventory explorer

`/inventory/table/`.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## Inventory explorer — `/inventory/table/`

A spreadsheet over the whole inventory: per-column autofilters, multi-sort, a
column chooser, live aggregates, a group-by cross-tab, charts, and CSV/TSV
export. Complements the map (spatial) and `/inventory/manage/` (record
editing) with the attribute-space view neither offers. Code: `table_data.py`
(server), `explore.html`, `static/inventory/{css/explore.css,js/explore.js}`.
Public, like everything else under `/inventory/`.

**The whole table ships to the browser, once.** ~1,500 records x ~70 columns
is 2.8 MB as row-JSON but ~1.1 MB columnar with dictionary-encoded text, and
170 kB gzipped. `/inventory/api/table/` serves that, cached per audience
(`table_public` / `table_editor`) in both raw and gzipped form. Every sort,
filter and pivot then runs client-side in tens of milliseconds — which is the
point: an autofilter that round-trips per click is not an autofilter. The
response is compressed **in the view**, not left to Caddy, because Caddy's
`encode` directive lives in the monitoring stack's config outside this repo.

**Column types are inferred, not listed.** `information_schema` gives the
Postgres type; a text column with <= `_CAT_MAX_DISTINCT` (60) distinct values
becomes a dictionary-encoded checkbox filter, above that a "contains" box.
So a column added to Postgres appears in the explorer automatically — the same
principle as the edit form's `_discover_editable_columns`. Only columns whose
inferred type would be wrong are named explicitly (`_FORCE_TEXT` for prose,
`_FORCE_LINK` for URLs). Column *grouping* in the chooser is read from
`views._EDIT_FIELD_GROUPS`, so the explorer and the edit form bucket a field
the same way for free.

**Computed columns** (`_COMPUTED`, not in `landslides`): `year_num` (the
resolved event age — a *third* copy of the era expression, kept textually
identical to `_FILTER_PROPS_SQL` so the table and the map can never disagree
about a record's age), `n_polygons`, `area_total`, `subsets` (multi-value),
`n_photos`, `n_stories`, `status`.

**Cache invalidation is unconditional.** `views._invalidate` drops the two
table keys on *every* call via `_ALWAYS_INVALIDATE`, rather than each of its
dozen call sites remembering to name them. One forgotten key would serve a
silently stale table; rebuilding after a settings-only invalidation costs one
query.

**Everything is in the URL hash** — filters, sort, visible columns and order,
group-by, chart, footer statistic, column widths — so any view you build is a
link you can paste to a colleague. This page's hash grammar is its own (see
the block comment above `writeHash` in `explore.js`); it shares nothing with
`ls_hash.js` beyond the `&`-separated `k=v` shape. Categorical filters encode
the *values*, not dictionary indices, so a shared link survives a data reload
that renumbers the dictionary.

A row's name opens `/inventory/#id=<id>`; a link naming a record with no
`map=` camera goes to the record's default view (or its centroid at z13) once
the panel opens, as a double-click does (`_idOnlyLink` → `_goToRecordView`,
2026-11-11; before that the new tab opened statewide).

**Hand-off to the map**: "Show on map" opens `/inventory/#ids=<id,id,...>`.
`map.js` parses `ids` into `_idsFilter`, `buildFilter` ANDs it into the layer
filter, and `writeHashState` re-emits it so panning doesn't drop it. A notice
in the sidebar's pinned strip (`#ids-notice`) says how many records the
selection covers and offers "Show all" to drop it — without it a shared link
looks like a map that has mislaid most of its data. Two deliberate omissions:
the selection is **not** written to `localStorage['ls_map_view']` (a one-off
selection must not become the view the map restores days later) and **not**
into `_currentViewString()` (a record's stored `default_map_view` is permanent
state; a transient row selection has no business in it). Absent `ids=`, every
pre-existing link and code path behaves exactly as before.

**Known limits / deferred**: no one-row-per-polygon mode (role / area /
thickness) yet; no "only records in the current map view" spatial filter; the
`ids=` hand-off is capped at 1,200 records before the fragment gets unwieldy.
