# External landslide inventories

Mirrored third-party inventories and one-at-a-time promotion.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## External landslide inventories — mirrored, not merged

Other people's landslide inventories, shown on the map as their own overlays
and mirrored verbatim into their own tables. **Live on production since
2026-09-24** (77,142 features). Server code is self-contained in
`inventory/external.py` (registry, fetch, API, promotion); the fetcher is
`python manage.py fetch_external_inventory`; client code is the `_ext*`
block in `map.js`.

| Source id | What | Licence / attribution |
|---|---|---|
| `ak_dggs_dds23` | Alaska DGGS DDS 23 (Nicolazzo & Larsen 2025, doi:10.14509/31697) — 5 layers: points, deposits, flanks, scarps, toes | DGGS: name the source, describe modifications |
| `canada_pcld` | Preliminary Canadian Landslide Database (Brideau and others 2026, doi:10.5281/zenodo.20371365) — points, from a CSV | CC BY 4.0, credit required |
| `usgs_schaefer2026` | Deforming bedrock landslides in southern Alaska (Schaefer and others 2026, USGS data release doi:10.5066/P1B64SZT; paper npj Nat. Hazards doi:10.1038/s44304-026-00296-3) — 239 PSInSAR polygons, from a zipped shapefile on ScienceBase. Added 2026-11-24. Not the older `insar_schaefer` (Prince William Sound) work | CC0; cite release + paper |

**One table per source layer, columns from the publisher** (`ext_<sid>__<layer>`).
Hig's framing, and the reason the design is what it is: *"view these as distinct
datasets from ours … as an overlay it's just some data that you can click on to
see whatever fields they populated it with."* DGGS classifies by material
crossed with movement category, Canada by type/material/size class, we classify
by slow-versus-catastrophic plus creep behaviour and a resolvable age. There is
no honest way to push one into another without deciding, per record, things the
source never said. So nothing is translated on the way in, and adding a third
inventory is a registry entry plus a fetch rather than a schema negotiation.

- **Columns are discovered, never declared.** `ensure_table` builds the table
  from whatever the service or CSV header declares at fetch time and adds
  columns it has not seen before, so a publisher's v15 column widens the
  mirror instead of being silently dropped by hand-written DDL. Publisher
  field labels (the ArcGIS *alias*, e.g. `movement_category` for `mvmt_categ`)
  are stored as **column comments** and are what the popup shows.
- **A refetch is a REPLACE, inside one transaction** — not an upsert by their
  id. These publishers revise, retire and renumber between releases (Canada
  ships a `Modified_in_version` column for exactly that), so an upsert would
  accumulate rows the current release no longer contains and the mirror would
  stop being a mirror. The ArcGIS path also asks the service for its own
  record count first and **refuses to write a layer whose fetched total does
  not match**, because a mirror that is quietly two-thirds complete is worse
  than none: nothing downstream can tell.
- **CSV columns are all text, on purpose.** A service declares its types so we
  use them; a CSV declares nothing, and sniffing is where faithfulness dies
  (a volume of `">1000"` becomes a parse error or a silent null). Text keeps
  what the publisher wrote; interpretation belongs in promotion.
- **Zipped shapefiles** (`kind: 'zip_shp'`, `zip_shp_rows`): read with
  pyogrio, the one `.shp` inside located by name, Z dropped, declared numeric
  types kept. No service to ask for a count, so the layer's `expect` (the
  publisher's stated count) is checked instead. A shapefile has no aliases, so
  the layer's `labels` (short forms of their data dictionary) become the
  column comments. Integer ids stored as Real are written as `1`, not `1.0`.
  ScienceBase often answers 502; the download retries five times.
- **Polygon sources**: `record/` and `explore/` locate a polygon by
  `ST_PointOnSurface`; `nearby/` measures from their outline (0 m = ours sits
  inside). 78 of the 239 Schaefer outlines already hold one of our centroids.
- **ArcGIS Dates arrive as epoch milliseconds** despite being declared Date,
  so they are converted on insert (`to_timestamp(%s/1000.0)`) rather than
  stored as integers nobody will think to decode.
- **Endpoints** (`inventory/urls.py`): `api/external/` (registry + field
  labels + counts), `api/external/<sid>/<layer>/` (**bbox required**, limited),
  `.../record/<ext_id>/`, `.../promote/<ext_id>/`, `.../link/<ext_id>/`,
  `.../nearby/<ext_id>/` (the last three editor-only, 403 otherwise).
  The map fetches per viewport on `moveend`; active layers persist per-browser
  and ride the URL hash as `ext=<sid>/<layer>`.

### Promotion — one record at a time, deliberately

Hig: *"the promotion … should be singular — some way of selecting a single
item, and promoting it to the main dataset … a natural flow that allows
creation of a polygon, then manipulation of the fields."* So there is **no
bulk adopt**. Clicking a mirrored feature opens its popup; an editor sees a
**Promote into the inventory** button, which creates one of our records seeded
from that point, stamps provenance, and drops the editor into the draw flow to
trace geometry and then the review form. It lands in the **pending queue**
like any other induction, never straight onto the public map.

- **Provenance** is two nullable columns on `landslides` (schema:
  `migrate_external_promotion`, run once per environment): `external_source`
  (registry id) + `external_id` (**the publisher's own id, not our mirror row
  id** — the mirror is replaced on every refetch, so our row ids do not
  survive it). Indexed, **not unique**: two of our records may legitimately
  derive from one of theirs, and forbidding the more careful reading would be
  backwards.
- **Per-source promotion rules** live in the registry's `promote` dict and
  are applied by `apply_promotion()` (PROMOTION RULES block in `external.py`),
  the one path for promote, link and backfill:
  - `ongoing_work` gets the provenance sentence from `promotion_text()`: the
    source's `noted_in` lead, `, as <their id>` (+ `id_note`, DGGS adds
    `(OBJECTID n)`, the number their popup shows), then each `details` entry
    quoted verbatim, e.g. `Their comment: "…". Their reference: "…".`
    (Canada: `comment`, `reference`; DGGS: `mapper` + `data_sourc` folded by
    `_dggs_source`, and `notes` unless it is only "Confidence= x"). Hig,
    2026-11-24: the id and their comment/citation were missing. The sentence is
    **merged**, not overwritten: `merge_promotion_text` replaces the current
    or a `legacy` wording in place (the quotes give the block a findable end)
    and keeps what editors wrote beside it ("Described in doi:…"); with none,
    it goes in front.
  - `noted_by` on a **new** promoted record is **cleared, not filled with the
    editor who promoted it** — everywhere else a blank `noted_by` auto-fills
    with the editor, which here would credit them with noticing something
    somebody else noticed. `canada_pcld` names `Marc-Andre Brideau`, who built
    that inventory. On a **link** it is only filled when blank.
  - The id is checked against the mirror (`mirrored_row`, by their id) before
    anything is stamped; 404 otherwise. Three DGGS records had been stamped
    with the OBJECTID, which the "Promoted from" card could not find.
  Add a source-specific rule in the registry, not at the call site.
- **Link to an existing record** (2026-11-24): the popup's *⇄ Link to an
  existing record…* lists our six nearest non-deprecated records (centroid
  distance, `api/external/<sid>/<layer>/nearby/<ext_id>/`) or takes an id;
  second click confirms (no dialog); POST `.../link/<ext_id>/`
  `{landslide_id}`. Re-linking replaces the stamp (one source per record).
  Written to the edit log as the linking editor.
- **Backfill**: `python manage.py backfill_promotion_text` (dry run; `--apply`
  writes; once per environment) rewrites every stamped record to the current
  wording, re-stamps an id that is their OBJECTID, and stamps unstamped
  records whose text says they were promoted with the nearest source point
  (`--max-distance`, default 3 km; `--pair ID:EXT_ID` overrides). Never
  changes `noted_by`.
- **The "Promoted from" card** (`_promoted_from.html`, included by both the
  edit and review forms) shows the source record's fields verbatim beside our
  own, read from the form's own `external_source`/`external_id` inputs so the
  card cannot disagree with what is about to be saved. Nothing in it is an
  input: copying a value is a deliberate act of typing it, which is the whole
  point of promotion being separate from mirroring.

### Review — an editor's verdict on one of their records

Hig (2026-11-16): a categorical field on every mirrored record, default
*unreviewed*, some *promoted*, plus review categories he grows ad hoc with a
`+`. Built as:

- **Its own tables, not a column on the mirror** (`migrate_external_review`,
  run once per environment): `ext_review_categories` (one **shared** list for
  every source, so one judgement has one name), `ext_reviews` (current
  category + optional note per record, keyed by **source, layer and the
  publisher's id**, so it survives a refetch, which replaces the mirror), and
  `ext_review_log` (every change, append-only). No row = unreviewed.
- **"Promoted" is never stored**: it is read live from
  `landslides.external_source/external_id`, so it cannot disagree with what
  was actually promoted.
- **Editors only** (Hig's choice). `api_external` adds `_review*` and
  `_promoted` properties to each feature for an editor and nothing for anyone
  else; a leading underscore cannot collide with a publisher column because
  `_col` strips it. Endpoints: `api/external/review/categories/` (GET list
  with counts, POST `{label}`, which reuses a label that matches ignoring case
  and spaces) and `api/external/<sid>/<layer>/review/<ext_id>/` (POST
  `{category_id | null, note}`; null = back to unreviewed). Both answer 503
  until the tables exist, so the code can deploy before the migrate command.
- **UI**: a *Review* box at the top of an editor's popup on a mirrored
  feature: category drop-down, `+` (adding a category files the record under
  it), note (saved on change), who and when, and a *Promoted as …* line
  linking our record(s).
- The review hangs on the layer the editor clicked: a DGGS deposit and the
  DGGS point for the same landslide carry different publisher ids (only 1,687
  of 19,128 deposit ids match a point id), so they are reviewed separately.

### Explorer — the Inventory tab's "Showing" switch (2026-11-16)

Hig chose this over a separate page or a table-plus-map: one select at the top
of the Inventory tab (`#xex-ds`, rendered from the registry by
`views._explore_sources`) swaps our filter list for the source's own. Mock-up
approved first (a private claude.ai artifact made from real dev data).

- **Data**: `api/external/<sid>/explore/` ships the source's primary layer
  whole, columnar and dictionary-encoded, gzipped (Canada 365 kB, DGGS
  579 kB), cached per worker and keyed on the mirror's `max(fetched_at)`, so a
  refetch (another process) is noticed on the next request. Filters, counts
  and colours run in the browser. Editors also get
  `api/external/<sid>/<layer>/reviews/` (who is reviewed as what, uncached)
  for the Review filter.
- **Configuration is the source's `explore` block** in `SOURCES`: type field,
  type → family table (`family_by: 'code'` reads DGGS's "RA: rock avalanche"
  by its code), facet fields (a facet may be an SQL `expr`, e.g. Canada's
  "cites a reference"), colour-by choices, presets (`fam`, `type` = only these
  types, or a facet key), the ring / size / fade channel, and the map's home.
- **Process families** (`external.FAMILIES`, colours in
  `LSColors.EXT_FAMILIES`) are one colour per family across all outside
  sources, clear of our class colours. Hig's grouping, 2026-11-16: debris
  avalanche goes with rock avalanche (so Canada's family holds 340 + 2,528;
  the "Rock avalanches" preset is the 340), slope deformation is its own
  family. Second channel: Canada ring = InSAR flag, size = their size class;
  DGGS ring = field verified, fade = `conf_exist`. Hig expects to tweak these.
- **Field filters use the three-way switch** of the Landslide features filter
  (✓ only these · no preference ✗ exclude; Hig: reuse semantically similar
  forms). One value per record, so several ✓ mean any of them. The switch's
  CSS is shared through the `.ft-list` class (was `#ft-filter`-scoped). In
  `xf`, `<facet>.` lists ✓ values and `<facet>-x.` lists ✗ values.
- **Spelling fold** (Hig: merge, reversible, on the methods page):
  `EXPLORE_FOLD_SPELLING` folds outer spaces and case for the filters only;
  the payload carries `variants` so each merged value is marked `*` with its
  spellings. Mirror and popups untouched. Off = every spelling its own value.
- **Map**: layer `xex-pts` under our `points`, on both panes (landslide data
  is the left/right-parity case; `replay()` runs where map.js rebuilds the
  landslide layers on either map). Our own records stay as context unless
  *Show our own records too* is unticked, which sets visibility on the
  `_landslideFilterLayers` ids (nothing else toggles their visibility).
- **Click** fetches the full record (`.../record/<ext_id>/`, which now also
  returns the review for editors and the point) and opens the overlay popup
  (`LSExternal._extShowRecord`), so Review and Promote are the same code.
  **Promote** fires `ls:ext-promote`; the explorer switches the tab back to
  our records before the outline is drawn. A saved review fires
  `ls:ext-review`, and the explorer reloads its review list.
- **URL**: extras `xs` (source), `xf` (filters), `xc` (colour-by), grammar in
  `ext_explore.js` (`encodeState`), written to the address bar and the
  restored view, never to a record's default view. Per-source state is also
  kept in `localStorage['ls_xex']`, so switching away and back keeps a focus.
- **Dev trap met while testing**: the browser kept a stale `ls_colors.js`
  (dev serves unhashed static URLs), so `EXT_FAMILIES` was missing and the
  module silently returned its no-op. A hard reload fixes it.

### Refetching, and what a new source costs

```bash
python manage.py fetch_external_inventory --list          # the registry
python manage.py fetch_external_inventory ak_dggs_dds23   # one source
python manage.py fetch_external_inventory --all --dry-run # fetch, report, write nothing
```

Runs **once per environment** like every other PostGIS command (dev and prod
are separate databases). A new source is: one `SOURCES` entry (title, short,
citation, url, licence, colour, fetch spec, `id_field`, `primary_layer`,
optional `promote` rules), then the fetch. No schema work, no client change —
the overlay list, the popup field order and the legend all come from the
registry.
