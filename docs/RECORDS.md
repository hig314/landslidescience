# Landslide records: editing, geometry, naming, subsets, induction, round-trip

Everything about how a record is created, edited, named, classified into subsets, imported and exported.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## Editing landslide records (workflow for editors)

1. Sign in at `/inventory/login/` (the collaborators' door; `/admin/login/` admits staff only — see [ACCOUNTS.md](ACCOUNTS.md)).
2. Hit `/inventory/manage/`.
3. Search by name or filter by type/class/subset; click into a record.
4. Edit non-geometry fields. Save.
5. The audit log records who and when. The list view shows it.

To bulk-add new landslides, use the upload path — see *Inventory induction workflow* below.

Both the edit and review forms embed a shared imagery-switchable preview map (`_polygon_map.html`) — the landslide's polygons over selectable basemaps (ESRI / Sentinel-2 / **AHAP 1978–86** / topo …), so the editor can flip modern↔historic imagery to spot slow change. Default fit zoom is capped at 15 (~the most real detail AHAP carries). `polygons_geojson` is built in both modes for it (and carries `landslide_class` + `role`). (AHAP loads slowly via the USDA ImageServer exportImage endpoint, and returns blank outside its AK extent — a pre-rendered AHAP tile cache near landslides is a possible future optimization.)


**Polygon primary convention is enforced on every automatic entry path** via `derived.normalize_primary()` (called by draw commit create+attach, `manage_polygons_save` adds *and* deletes, and `apply_import`): slow → body primary; catastrophic → exactly ONE source primary, deposits never primary; catastrophic with no source → no primary (centroid falls back to the deposit by role order). Keeps an already-flagged candidate, else lowest id — matching the centroid LATERAL's ordering, so compliant records never move. The review form's explicit per-row primary radio remains the manual escape hatch (not normalized). `python manage.py normalize_primary_polygons [--dry-run]` sweeps the backlog (restart web after a live run to drop cached centroids); run once per environment after deploy.

Polygon **roles** (source/body/deposit) ARE editable in the edit/review form (`polygon_role_<id>`), so a mis-typed landslide can be corrected end-to-end — switch `landslide_type` *and* the role (slow↔body, catastrophic↔source/deposit), and the rule cascade re-runs on save (also triggered outside review when type/role changed) to recompute the centroid/areas/class. The edit/review form also offers location-seeded reference links via `_imagery_suggestions` (centroid, or the polygon-union centroid for pending records): ESRI Wayback / Google satellite as paste-into-field suggestions next to `esri_wayback_link`/`google_images_link`, read-only OPERA InSAR ascending/descending links next to `insar_opera`, and a USGS TopoView (historic topo, zoom 13) link. The public map detail popup (`api_detail` → `map.js` imagery list) carries the deterministic OPERA + TopoView links too (`topoview_link`).

**ESRI Wayback link format (maintenance note).** `esri_wayback_link` uses ESRI's current **center+zoom** hash: `…/wayback/#mapCenter=<lon>%2C<lat>%2C<zoom>&mode=explore[&active=<release>]` (`active` = which historical capture is shown). ESRI **retired the old bounding-box hash** (`#ext=<lonW>,<latS>,<lonE>,<latN>`) — the new app silently ignores it, so legacy links opened to the whole world. The conversion (bbox → centroid + equivalent zoom, preserving `active`) lives in `views.py`: `_wayback_url` builds a link, `_convert_wayback_ext_url` converts one (handling `ext` at **any** hash position, e.g. `#active=N&ext=…`, and leaving links that already carry a `mapCenter` untouched) — shared by the auto-seeded suggestions and the migration. Two idempotent, `--dry-run`-capable commands fixed the stored data (run once per environment — the dev and prod databases/snapshots are separate):

- `migrate_wayback_urls` — convert stored `landslides.esri_wayback_link` values.
- `patch_snapshot_wayback_urls` — rewrite the frozen links inside published snapshot bundles (each `api/landslide/<id>/index.json` **and** the downloadable GeoJSON zip's `landslides.geojson` + `landslide_polygons_flat.geojson`) in place, without disturbing the bundle's scientific data or citable manifest.

If ESRI changes the format again, update those two `views.py` helpers and re-run both commands per environment.

**Links in description / notes.** The two free-text fields support a
**links-only markdown subset**: `[text](https://…)` plus bare http(s) URLs
render as anchors in the map detail panel (`richText()` in map.js —
tokenize-links-first-then-escape, hrefs scheme-anchored to `https?://`, so
`[x](javascript:…)` and any pasted markup stay literal text). Values remain
plain text in the DB — exports/QGIS/api_detail see the raw string; nothing
else renders markdown (deliberately: no accidental formatting from stray
`*`/`#` in existing prose). Pasting rich text (copied from a web page/doc)
into these textareas converts anchors to `[text](url)` automatically
(`_paste_links.html`, shared by edit + review); if the clipboard HTML
carries no links, the normal plain-text paste proceeds untouched.

**Form ergonomics.** Scalar fields **autosave on blur** (`autosave.js` → `manage_edit_field`), with a per-field status (blue ✓ / red error); the polygon role/primary table autosaves too (→ `manage_polygon_role`, 2026-10-26: a role change also runs `normalize_primary`, an explicit primary pick does not; the response refreshes the radios, the derived fields and the preview map); the "Save changes" button remains for subset memberships. **A red `*` marks what a record needs to show on the public map** (`forms.DISPLAY_REQUIRED`, tooltip per field, "one of" where it is): type; an age for catastrophic; creep evidence for slow; *creep evaluated* (or evidence) for full-size catastrophic ≥ 2012. **That table is hand-kept against the class rule — whenever `compute_landslide_class`, the creep ladder, the era resolution or the map's class list changes, re-derive it**, and check every label the rule can return is in map.js `CLASS_ORDER` and `views._CLASS_COLOR`: a label missing there hides the record with no sign (Tatonduk River C 2018, 2026-10-26, class a bare `Catastrophic`; `Slow Cryptic` is the other such label the rule can produce). Date fields are typeable in the unambiguous **`14-Sep-2010`** form (and ISO). **The bracket is enforced per field and drives `year_text`** (2026-11-11): autosave refuses a `date_min`/`date_max` that would invert the bracket (record 1626 stored one and every review save then failed); `forms.check_year_text` / `year_text_auto` (used by the autosave and the full form's `clean`) make year text the year when both dates share it (anything else refused), fill a blank with "Modern" for a `date_min` of 1851–2011 (never from 2012: the class rule reads year text before `date_min`, so "Modern" would demote a post-2012 record — Hig's call), refuse a year outside the bracket, and warn (amber status, still saved) for a year the bracket does not pin or "Holocene" after 1850. A date edit refills year text in place (`fields` in the response) and re-runs the cascade. A refused full-form save shows a **banner** at the top (`_form_errors.html`) listing the fields with jump links; before it the only sign was a red line under the field. A field that feeds a rule re-runs the cascade and refreshes the derived fields in place. `owner`/`noted_by` auto-fill with the editor's identity (username / full name) on blank records (and on draw-created ones). The polygon primary (centroid-defining) is selectable per-row in the role table. Saving geometry no longer reloads the page — it refreshes the derived fields in place so unsaved field edits survive. On the public map a record missing the dimension its display rests on (slow without `creep_behavior`, catastrophic without a resolvable age) draws **magenta** (see *Map symbology* above) — a visible "incomplete" signal, not a hard error.

## In-app polygon geometry: edit & draw (Terra Draw)

Geometry can be created/edited in-browser via **Terra Draw** (loaded from CDN, editor-only; globals `terraDraw` + `terraDrawMaplibreGlAdapter` — note the casing). Draw with **click vertices → Enter to finish, Esc to cancel**; `pointerDistance` is tightened so clicks near existing vertices don't snap-close.

- **Edit existing** — on the edit/review preview map (`terra_draw.js`), the **✎ Edit geometry** button: reshape (drag/add/delete vertices), add a polygon (role), delete a polygon → **Save** POSTs to `manage_polygons_save`. Round-trip-safe: only changed rows rewritten (server `ST_Equals` skip); pre-edit geom snapshotted to `landslide_polygons_history`; rule cascade re-runs. Terra Draw select mode adds vertex/midpoint **handle Points** to its store — filtered out via `realPolys()` before diffing (else they look like new polygons). Geometry is `MULTIPOLYGON,4326`; drawn Polygons are `ST_Multi`-wrapped. **Coordinates are rounded to 9 dp on the way into Terra Draw** (`loadFeatures` → `round9`): Terra Draw rejects coords with >9 decimal places (`addFeatures` returns `valid:false, "invalid coordinates"`, *silently* — no throw), and stored geometry is served at 15 dp, so without rounding every existing polygon loaded empty (the editor showed nothing to edit). 9 dp ≈ 0.1 mm — far below mapping accuracy.
- **Draw new on the main map** — the **✏ draw** control (`DrawModeControl` in `map.js`): trace a polygon → name + role popup → staged server-side in `provisional_polygons` (per-editor, survives reload). **Same name = same landslide.** A draft teal overlay shows staged polygons **only while the tool is open** (hidden on Exit so closed sessions leave no unexplained “ghost” polygons; components persist server-side and reappear with the tool — Exit with staged work confirms and says so; own source, re-added on `style.load`, MeasureControl pattern; `__drawActive` flag suppresses landslide clicks + is mutually exclusive with measure; basemap locks only while a polygon is open). The queue panel groups by name (`manage_draw_preview` flags dispersed/duplicate/collision), and each group has a **✎ rename** button (`manage_draw_rename/` — renames all of the editor's components under that name; renaming onto another staged name merges groups) as the recovery path for a flagged collision or blocked commit; **Commit** (`manage_draw_commit`): new names → `apply_import` synthesize-by-name (type inferred from roles); **names that already exist → polygons are *attached* to that landslide** (e.g. a source for a committed deposit), `is_primary` normalized to the role convention. Then cascade + redirect into review. **Attach hard-block:** if the staged name matches an existing landslide whose centroid is **> ~5 km** away (`_PROV_DISPERSED_M`), commit is **refused** (409) with "… exists ~N km away — give this one a distinct name (e.g. 'X 2')" rather than silently merging two different features under one name (this is how record 1446 merged two 100 km-apart "Moose Creek" landslides). The legit nearby-attach case still works.
- **Scarp traces** — line tracing under the same ✏ pencil (kind *Scarp traces*), same Terra Draw plumbing and `pointerDistance` tightening; see *Landform traces* below.
- **Provisional (pending) records on the map** — editors see them in **magenta** (`api/provisional/`, editor-only, not cached; `pending-*` layers in `map.js`), click → review form. Public never sees pending. The map also restores the last view (localStorage) when you return with no hash, and the edit/review forms have a **↩ Map** link, so you can bounce between mapping and form-filling.

**Migrations (run on prod at deploy, idempotent, like `migrate_deprecated`):** `migrate_polygon_history`, `migrate_provisional_polygons`, `migrate_flag_review` (adds `flagged` + `flag_reason`), `migrate_default_view` (adds `default_map_view`), `migrate_regions` (adds `subsets.kind`/`region_geom`, `landslides.country`, `countries` table; follow with `load_countries` + the `load_region_geometry` runs — see *Subsets* section). `migrate_features` + `backfill_features` + `check_features` (2026-10-06, see *Landslide features* below; add the column by psql first). `migrate_its_live` (2026-10-06) adds `landslides.its_live_creep`, a nullable boolean beside `planet_labs_creep`: motion that shows in the ITS_LIVE velocity fields, which the `creep_behavior` rule reads as **Obvious creep** (the Planet patchy flag still overrides it, as it overrides the plain Planet flag — that ordering was the conservative reading, not something Hig specified). **Run it right after the deploy that names the column**: the rule cascade SELECTs every rule input by name, so until the column exists a review-save fails on `creep_behavior`. Nothing else needed telling — the edit form, the explorer and the GeoJSON round-trip all discover columns from the database; the hand-kept places are the rule (`derived.py`), the label and hint (`forms.py`), the form block (`_EDIT_FIELD_GROUPS`), the explorer's creep preset, the detail panel's evidence list (`map.js`) and the Methods page. **Landslide features (2026-10-06/07, all three phases done).** `landslides.features` is a multi-valued text column (`", "`-joined values) that replaced the eight feature booleans as the thing editors set: molards, exclusively_supraglacial, super_elevated_deposits, tsunamigenic, precursory_headscarp (catastrophic) and post_2012_activity_increase, creeping_permafrost_mass, glacier_contact (slow). **The eight booleans were kept for one day as rule outputs mirroring `features` and dropped on 2026-10-07** (`drop_feature_mirrors`: refuses unless every boolean agrees with `features`, then drops the `landslide_overview` view that named five of them, the columns, and re-creates the view without them; `derived.FEATURE_MIRRORS` remains as the legacy-column → value map that the transition commands and the old `f=` URL bits translate through). The volume rule now tests `features` directly (`derived.feature_sql('Creeping permafrost mass')`); on dev the rule diff agreed with every stored polygon volume under the new test (1,827 polygons, 0 changes). Exports made before the drop carry the booleans, which an import ignores with a warning — **Phase 2 (same day) moved the map filters onto `features` itself**: the sidebar's *Landslide features* section is one three-way control per vocabulary value (must have / neutral / exclude, grouped by the type it applies to, rendered by `home` from `_feature_filter_groups()` so a value added in the form is a filter on the next load); it travels as `ft=<slug>,!<slug>` (by name, so no bit table to extend; `_ftSlug` in map.js), the eight feature bits of the old `f=` bitmask are read as must-have and never written, `features` rides `_FILTER_PROPS_SQL` as a JSON array on both map sources and on the two chart-event payloads (`_ftPass`), and the explorer splits every `VOCAB_COLUMNS` column into values like Subsets. The remaining *Include only* boxes (dammed streams, site-specific volume, time bracketed, seismic, flagged) still use `f=`. **Also found on the way (fixed 2026-10-07): `landslides.updated_at` was never stamped by the editor** — neither the full-form save nor the per-field autosave touched it, so the audit log (`LandslideEditMeta.last_edited_at`) was the only record of an edit; both now set `updated_at = NOW()`, and `check_features` reads "edited since the backup" from the audit log rather than trusting the column for anything earlier. A record whose `features` is NULL has not been backfilled (or came from an older import) and its booleans are left alone; `''` means "no features". Per-value metadata — applies to slow / catastrophic / both, a tooltip, order, the legacy column — is `inventory.models.FeatureVocab` in SQLite (Django admin; config like `QmsLayer`); the values themselves are the data, and the picker's "add" registers a new one with an applies-to choice via `api/features/vocab/add/`. **Transition commands, run in this order once per environment** (dev done 2026-10-06; on prod `ALTER TABLE landslides ADD COLUMN IF NOT EXISTS features text` by psql *before* the deploy, because the new rules SELECT it): `migrate_features` (column; `landslides_backup_20261006` copy of the table — the thing to validate against, drop by hand after Phase 3; seeds the 8 vocabulary rows), `backfill_features --dry-run` then live (fills `features` from the booleans for NULL rows only; mirror pass turns NULL booleans into FALSE — 9,406 cells in dev, visible as 0 rather than blank in the explorer and `false` rather than `null` in exports), `check_features` (proves mirrors ⇔ features ⇔ backup, record by record; exit 1 on any disagreement). The form: a *Landslide features* group after the core fields with one picker that shows the values for the record's type (following the type select live) and the mirrors in the collapsed Computed group; the old *Failure & deposit character* and *Slow-mass attributes* blocks are gone, stream damming stays in the event group. `_EDIT_FIELD_GROUPS` nesting forms are enumerated by `views.group_field_names`, which the explorer's column grouping also uses — a new nesting form goes there, or `/inventory/api/table/` 500s (it did, in dev).

**`merge_vocab_values`** (2026-10-06, `--dry-run` first, once per environment; `--column` for one) unified `other_subtle_creep`'s 28 spellings into 14 values ("change" not "creep" for post-AHAP / post-Maxar / post-LIA; one "OPERA coherence gap"; rockfall folded into recurrent failures — Hig's calls). The column stays free text holding `", "`-joined values; the edit form renders it as a multi-select of the values in use, most common first, with "add a value" (`_vocab_picker.html`, vocabulary from `views._value_vocab`) — the data is the vocabulary, nothing is registered. **`stream_damming` is the second such column** (same day; the picker and the merge command are generic — `views.VOCAB_COLUMNS` + a `CANON` table in `merge_vocab_values` give any text column the treatment). The creep section itself is now ordered weakest to strongest: Geomorphic → Subtle (InSAR box beside the picker) → Obvious (ITS_LIVE; Planet Labs creep ▸ Patchy only) → Slow-mass attributes; `_EDIT_FIELD_GROUPS` block entries support `{'field','sub'}`, `{'group','fields'}` and `{'field','cell':True}` for that (see the comment there). (The standalone `/manage/new/` draw page was superseded by the main-map tool and retired 2026-11-14: the address redirects to the map, the ☰ menu's *New record* item is gone. Its client half — the `CREATE` branches in `terra_draw.js` and `poly_create_mode` in `_polygon_map.html` — is now unreachable and still to be removed.)

**Permanent delete (superuser-only).** The edit/review forms carry a "Danger zone" (`_danger_zone.html`) that **hard-deletes** a landslide and everything keyed to it — polygons, polygon edit-history, subset memberships, Planet-Story links, the SQLite `LandslideEditMeta`, and clears any inbound `superseded_by` pointers — in one transaction via `manage_delete` (`POST manage/<id>/delete/`), then invalidates caches and redirects to the list with a banner. Itemizes the impact counts up front, gated behind an "I understand" checkbox **and** a `confirm()`. Distinct from **deprecation** (the soft, provenance-keeping retire); data editors only deprecate — hard delete is restricted to superusers (in-view `is_superuser` check, not just the editor decorator).

## Naming & disambiguation standard

Alaska placenames recur across distinct landslides (five "Moose Creek"s), and many features sit *near* a named feature without being on it. Names are **human-readable base + compact, admin-interpretable disambiguators**, read left→right from most to least significant:

- **Single landslide at a unique placename:** `Toyota Creek` (slow) / `Toyota Creek Holocene` or `Toyota Creek 2004` (catastrophic — the catastrophic event gets a time qualifier).
- **A slow + a catastrophic at ~the same spot** share the base name (`Toyota Creek` + `Toyota Creek Holocene`). If they're in *somewhat different* spots, add a **letter**: `Toyota Creek A`, `Toyota Creek B Holocene`.
- **Multiple of the same type at different spots** on/near the feature: letters — `Toyota Creek A`, `Toyota Creek B`.
- **The same slope failing catastrophically more than once:** distinguish by year/epoch; when more than one event falls in the **same time-bin** (same year, or both Holocene/Modern), append a **`.N`** index — `Toyota Creek B 2016.1`, `Toyota Creek B 2016.2`; `Barabara Creek B Holocene.1`, `… Holocene.2`. (The old trailing-letter form `… Holocene A`/`B` is deprecated — flag scan heuristic (c) catches it for conversion.)
- **A genuinely *different* feature that shares a name** (the Moose Creek case): append a **number** then letters — `Moose Creek 2 A`, `Moose Creek 2 B`. The number distinguishes the feature; letters distinguish slopes on it.
- **Weakly-associated placename** (near, not on, a named feature): a **`~`** distinctor right after the base name (reads as "approximately/near" — it replaced the old `X`, which collided with the slope-letter sequence at the 24th slope, also `X`). `Eureka Glacier ~ 2 C 2016.2` = a landslide on a feature *near* Eureka Glacier (2nd such feature borrowing the name), 3rd failing slope (`C`) on it, 2nd catastrophic failure (`.2`) in 2016. (`~` collapses to `-` in URL slugs like any non-alphanumeric, so it's URL-safe; slug collisions are auto-de-duped via `-<id>`.) **The side of `~` matters:** everything before it is the base feature; a number *after* it counts near-features — `Toyota Creek ~ 2` (2nd area near Toyota Creek) vs `Toyota Creek 2 ~` (near the distinct feature "Toyota Creek 2", case 5).

GNIS-based auto-naming (snap to the nearest placename + auto-suffix) is **future work**; today the standard is applied by hand via the rename workflow below.

### Flag + rename workflow (editor-only)

Tooling to find and fix non-conforming names without page reloads:

- **`flagged` (boolean) + `flag_reason` (text)** — editor-only review metadata (schema: `migrate_flag_review`). Not shown on public surfaces; the map's **Flagged** filter is editor-gated. `flagged` is editable like any field (autosave / inline), so editors clear it when a name is fixed.
- **`flag_name_issues` management command** (re-runnable; `--dry-run`; `--reset` clears the scan's own prior auto-flags first, by reason text, so a re-run is a clean sweep that drops stale flags while leaving hand-set flags alone) — scans active records and sets `flagged`+`flag_reason` (never clobbering a hand-written reason) on names that **(a)** contain `trib`/`neighbor` ("weakly-associated placename — consider the ~ distinctor"), or **(b)** are an un-disambiguated **token-prefix** of another name ("base name of 'Eagle Creek A' — needs disambiguation"). Token-prefix (not fuzzy ratio) so properly-disambiguated siblings ("… A 2014" vs "… 2015") aren't false-flagged; and a sibling whose only extra token is a **time qualifier** (a 4-digit year or `Holocene`/`Modern`, each optionally with a `.N` repeat index — `2024.1`, `Holocene.2`; standard case 2), a **bare number** (`Moose Creek 2`, case 5), or a **`~`** near-feature distinctor (case 6) is intended and **not** flagged. Heuristic **(c)** flags the **deprecated letter-suffix repeat form** — a single letter trailing a time qualifier (`… Holocene A`, `… 2016 B`) — for conversion to `.N`, with the bare first event getting a "first of repeat failures … use the '.N' form" note. Heuristic **(d)** flags every record carrying a **lone `X` token** (the retired distinctor — ambiguous against the 24th slope letter) so an editor can rename it `~` or clear the flag. `_is_time_qualifier` (regex `^(\d{4}|holocene|modern)(\.\d+)?$`) is the shared definition. `--reset` matches reasons by **prefix LIKE** so it also clears flags written by an earlier wording (e.g. the pre-`~` weak reason).
- **Other flag scans** (same pattern: re-runnable, `--dry-run`, `--reset` clears only its own reason-prefix, `COALESCE` never clobbers a hand-set reason, `_invalidate` at the end — and **always run without `--reset`** in normal use so they *add* flags without dropping unresolved ones):
  - **`flag_undated_catastrophic`** — active catastrophic with **no resolvable age** (the `year_num`-NULL / magenta set): no seismic, `year_text` not a 4-digit year nor Modern/Holocene token, no era token in the class, no `date_min`.
  - **`flag_weak_event_timing`** — active catastrophic with **weakly-constrained timing**: a one-sided date bracket (only a max date = a recency guess from the first image showing the event; or only a min date) or a specific 4-digit year with no bracket at all. The `flag_reason` notes the case so an editor can triage (max-only is often legit — confirm; a specific year with no support like "Your Creek D 2010" is a real guess; a field-known year like Great Mageik 1912 is a one-click clear).
  - All flag scans funnel into the **one `flagged` boolean** (distinguished by `flag_reason`), shown under the single editor **Flagged** filter. A record can match more than one scan; the first to run wins the reason (COALESCE).
- **Map labels (pin a field)** — the editor "Needs attention" panel has a **Map labels** dropdown (`#pin-field`); choosing a field draws its value as a label on every landslide (`pin-label` symbol layer on the `landslides` source, font **`Noto Sans Regular`** — served by both glyph servers; re-added by `initDataLayers` on basemap switch; choice persisted in `localStorage['ls_pin_field']`). The label respects the active filter. The pinnable set is **manually-entered text only** (`unique_name`, `owner`, `noted_by`, `year_text`) — `_PIN_LABELS` in map.js + the `#pin-field` `<option>`s in home.html must stay in sync; rule-derived columns (class, creep behavior) and `flagged` are deliberately excluded.
- **Inline edit in the info-box** — when a field is pinned, clicking a landslide shows an inline editor for that field in the detail panel; it POSTs to `manage_edit_field` (the same per-field autosave endpoint) and **live-patches the `landslides` source** (`_patchFeatureProp`) so the label + filter update without a reload.
- **Flag banner + Clear** — a flagged record shows a `⚑ Flagged for review … [Clear flag]` banner in the info-box (editor-only, independent of what's pinned); **Clear flag** POSTs `flagged=false` via `manage_edit_field`, live-patches the source, and the record drops out of the Flagged filter. (Clearing the flag is a review action, not a field-edit — so it's not done by pinning `flagged`.) When the reason is a `base name of '<NAME>'` reference, `<NAME>` renders as a **jump link** (`linkifyFlagReason` → `a.flag-jump`, resolved via `_featureByName` against `_featuresData`) that flies to + opens the referenced landslide — it's often nowhere near the flagged one. Reuses the permalink click handler's hash-nav.
- **The workflow:** run `flag_name_issues` → on the map filter to **Flagged** + pin **name** → spot duplicates / `trib` / `neighbor` from the labels → click a record → rename inline per the standard (`Moose Creek 2 A`…) → **Clear flag**.
- **Rename collision block:** `manage_edit_field` (the per-field autosave endpoint behind both the inline pinned-field editor *and* the edit/review form) refuses a `unique_name` rename that matches another non-deprecated record (normalized case/whitespace, the import/draw `name_key`) with a 409 — the rename-side counterpart to the draw-commit attach hard-block. Without it a rename to an existing name slipped through silently (it's a generic UPDATE). It blocks duplicate *naming*, not a merge — two same-named rows were never actually merged here.
- **Naming page:** the standard above is reader-facing at `/inventory/naming/` (`naming` view + `naming.html`), linked from **Methods** §1. 

**Reviser saves move the dot too.** The landslide points source is fetched once at load (`initLayers`), never by `onMoveEnd`, so after a save the reviser splices the response's recomputed centroid/areas/class into that one feature (`_reviseSplicePoint`) as well as the polygons; without it the outline redrew and the dot stayed put until a reload. In the draw tool's name popup, Enter is Add.

## Subsets: tags vs spatial regions, and the country attribute

Subsets come in two kinds (`subsets.kind`, schema via `migrate_regions`):

- **`tag`** — hand-curated, e.g. `alaska-2025` (publication subset, frozen by
  its snapshot — untouched by everything below).
- **`region`** — defined by a polygon (`subsets.region_geom`, MultiPolygon
  4326). Membership = the record's stored **primary-polygon centroid** falls
  inside (ST_Covers), computed by `inventory/regions.py` and **materialized
  in `landslide_subsets`** — so every read path (map `?subset=` filter,
  counts, exports, snapshots, facets) is unchanged; regions.py is the only
  writer. Refreshes: per-record at the end of `apply_rules_for_landslide`
  (regions are derived state and ride the cascade choke point); per-subset
  via `load_region_geometry` (loads/replaces a polygon from any
  pyogrio-readable file, prints the full membership diff — **run --dry-run
  first and keep the output; that diff is the change record**); globally via
  `refresh_region_subsets [--dry-run]`. Guards everywhere: frozen
  (snapshotted) subsets are never touched, deprecated records keep their
  memberships, NULL-centroid records belong to no region, and each polygon
  PART must span <180° longitude (antimeridian split required — the whole
  MultiPolygon may span the dateline via split parts, like TIGER Alaska).
  UI: region memberships render as read-only "computed from location" chips
  on the edit/review forms (excluded from the checkbox sync alongside frozen
  subsets); the import picker excludes regions.

Current regions (converted 2026-07-12; per-record diff record in
`/Volumes/Nunatak/Landslides/AK_systematic_surveys/region_polygons/`):
`alaska` = TIGER/Line 2024 state boundary (extends to the 3-nmi
territorial-sea limit — smooth offshore edge, exact land border, all inside
waters interior; already dateline-split), `kim` = Kim_all study area
(deliberately crosses into Canada), `schaefer`, `higman` = study-area
polygons from the inventory paper. Seed files live in `data/regions_seed/`
(gitignored — copy to the droplet before running the loads on prod).

**`landslides.country`** is a derived rule (`compute_country`,
`/inventory/rules/country/`): centroid tested against the `countries`
reference table — Marine Regions **"EEZ + land union"** polygons
(`load_countries`; source layer `MarineRegions:eez_land` via WFS **1.0.0**
— the 2.0 export is lat/lon axis-swapped; **CC-BY, attribute Flanders
Marine Institute / marineregions.org** wherever country values are
published). Land + waters to 200 nmi per sovereign, so fjord/nearshore
centroids resolve by pure containment; `sovereign1` is used (country level,
not territory). The table is created empty by `migrate_regions` so the rule
is always valid; country is NULL until `load_countries` runs. This is the
universal floor beneath the region layer: a site outside every region still
carries its nationality.

## Inventory induction workflow

New landslides enter the inventory via upload at `/inventory/manage/import/`. The flow:

1. Editor uploads a GeoJSON zip, `.geojson`, `.gpkg`, `.shp` (+ sidecars), or `.kml`. Multi-format ingestion goes through `pyogrio` + `shapely` → normalized GeoJSON FeatureCollections.
2. Upload preview shows a diff (would-add / would-update / would-skip) and surfaces normalization warnings.
3. **Common-fields form** at Apply: blanket values for fields the user wants to set on all new records (e.g. `noted_by`, `landslide_type`). `owner` is auto-populated from the logged-in user (data-admin only). Subset choices exclude locked subsets (e.g. `alaska-2025`). `unique_name` is excluded from blanket population (must be unique per record).
4. On Apply, new landslides are inserted with `reviewed_at` NULL — i.e., **pending**.
5. The user is redirected to `/inventory/manage/review/<first_pending_id>/` — a review form with a mini-map (basemap selector only, no measure/circles, polygons embedded server-side). The form excludes rule-populated columns (those should be computed at save time; see below). On save, `reviewed_at` is stamped and the view redirects to the next pending record.
6. Pending records survive logout / browser close. They surface again next time the editor visits review.

Upload-side normalization (in `io_geojson.py`):
- `_normalize_controlled_vocab` / `_norm_against` — generous matching of `role` (source/body/deposit) and `landslide_type` (slow/catastrophic): case-insensitive + trim, punctuation-stripped, depluralized, `_VOCAB_ALIASES` (e.g. `bodies`→body), and a conservative fuzzy typo-correction (difflib ratio ≥ 0.8, unambiguous only — so `sorce`→source, `depsit`→deposit, but `flow`/`head-scarp`/`src` are left for validation). Every correction emits a warning shown in the preview.
- `_synthesize_landslides_from_flat_polygons` — when a flat-polygons-only file is uploaded, polygons grouped by `unique_name` are inferred into a synthesized landslide record (landslide_type inferred from the polygon roles).
- `LANDSLIDES_AUTO_COLS = ('created_at', 'updated_at', 'reviewed_at')` — these are server-managed and ignored in uploads.

## Induction safety & collision detection

A landslide is **publicly visible only when `reviewed_at IS NOT NULL AND deprecated_at IS NULL`** — i.e. inducted (reviewed) and not superseded. The predicate is centralized in `views.public_landslide_filter(alias)` and applied to every public surface (home counts, features/polygons/detail APIs, chart data, slug map; the snapshot inherits it through the API client). Two hidden states:
- **Pending** (`reviewed_at IS NULL`): freshly uploaded, not yet reviewed.
- **Deprecated** (`deprecated_at IS NOT NULL`, `superseded_by` → new id): retained for provenance but retired by a merge (see below). Schema via `migrate_deprecated.py`.

On review-save, `derived.apply_rules_for_landslide(cur, ls_id)` runs the full rule cascade for that record (centroids, areas, volumes, class — identical to the batch rule-apply) **then** stamps `reviewed_at`, in one transaction (cascade failure → stays pending). `/inventory/manage/` has a `?status=pending|active|deprecated|all` filter (default hides deprecated) with status badges.

**Centroid change-detection is distance-based.** `derived._equal(old, new, column)` decides whether a rule's recomputed value differs from the stored one. The four centroid columns (`centroid_albers_x/y`, `centroid_lat/lon`) are *positions*, so equality is judged as a true ground **distance in meters** (lat/lon degrees scaled by `_M_PER_DEG_LAT`, a conservative equator scale for longitude; Albers X/Y are already meters), thresholded at `_CENTROID_TOLERANCE_M` (0.5 m). The generic 1.0 tolerance — meant for areas in m² — previously applied to lat/lon **degrees**, so a centroid wrong by up to ~1° (~100 km) was treated as "equal" and never corrected, and lat/lon could sit inconsistent with the Albers columns (the mixed-axis centroid that corrupted merged-record 1446). Per-column metric comparison means no axis is masked, so all four columns correct toward the same computed centroid and stay consistent. Pre-existing drifted centroids (stored ≠ recomputed by >0.5 m) now surface in the rules-admin diff and self-correct on the next rule-apply/review-save.

**Collision detection** (`io_geojson._detect_collisions`, surfaced in the import preview, **report-only** for now): each would-add landslide is flagged if it collides with an existing non-deprecated record:
- **Name** — `name_key()` (NFC + whitespace-collapsed + casefolded) matches an existing name; classified `exact` vs `case` (case/whitespace-only diff). Names are compared, never auto-rewritten.
- **Location** — a candidate polygon within `COLLISION_NEAR_M` (200 m, geography `ST_DWithin`) AND polygon-pair IoU > `COLLISION_IOU` (0.80, EPSG:3338).

Each collision gets a `resolution` (one source of truth for preview + apply): **`update`** — polygons identical (IoU ≥ `COLLISION_IDENTICAL_IOU` = 0.999): the same landslide re-applied (master-file workflow) → `apply_import` UPDATEs the existing record in place, keeping its id/history (matched-updated count on the done page); **`block`** — name-exact dup with non-identical geometry: would violate the `landslides_unique_name_key` UNIQUE constraint, so apply refuses up front with an actionable message (never the raw 500 it used to throw — and any other DB error during apply is also caught → clean message, full rollback); **`review`** — case/whitespace name dup or a near (non-identical) overlap: inserts as new, surfaced for the editor. The preview's collision block shows the per-row action.

**Still to build (Phase 2–3, task #61):** the supersede/merge UI for `block`/`review` collisions that aren't simple identical re-imports — keep the improved upload, deprecate the original (`deprecated_at`/`superseded_by`), carry valuable linked data (Planet stories, subsets, null-only fields) forward via an explicit picker; plus a `[Place][Letter][year]` name suggester for distinct-location name clashes. **The plan file for this is gone** — whoever picks it up writes a fresh one rather than hunting for it; the paragraph above is what survived.

## GeoJSON round-trip

The export/import flow lets you snapshot the current inventory, edit in QGIS or by hand, then re-apply. Format:

```
landslidescience_inventory_YYMMDD.zip
├── manifest.json                       # export format version, timestamp, column lists
├── landslides.geojson                  # 1 feature/row, Point at representative centroid, all columns in properties
├── landslide_polygons.geojson          # 1 feature/row, MultiPolygon, properties carry landslide_id/role/area/thickness
├── landslide_polygons_flat.geojson     # denormalized: same polygons + parent landslide attrs merged in (export-only)
├── landslides.qml                      # QGIS style for the points layer (categorized by landslide_class)
├── landslide_polygons.qml              # QGIS style for the polygons layer
└── landslide_polygons_flat.qml         # byte-identical copy of landslide_polygons.qml so QGIS auto-loads it on the flat file
```

Column names match PostGIS exactly (snake_case). Geometries use `ST_AsGeoJSON(geom, 15)` — full IEEE 754 precision so round-trip is byte-stable.

Each landslide feature carries a **Point geometry** at its representative centroid (slow → body polygon; catastrophic → primary source, fallback deposit), so QGIS can use `landslides.geojson` directly as a point layer without joining the polygons file. The centroid is computed in EPSG:3338 (NAD83 / Alaska Albers, equal-area), then reprojected to WGS84 for the Point. Four explicit derived properties accompany it: `centroid_albers_x`, `centroid_albers_y` (meters), `centroid_lat`, `centroid_lon` (decimal degrees). These derived values are computed at export time and ignored on import — they're not DB columns.

**Flat polygons file**: the same 1731 polygons but with each parent landslide's columns and centroid_* fields merged into the polygon's properties. A single-file alternative to (polygons + landslides + join). Export-only — re-uploading the zip silently ignores the flat file. Useful when you want one drag-and-drop layer in QGIS that already carries the landslide attrs.

**QML styles**: the three `.qml` files match the inventory map's color scheme exactly — colors are read from `map_settings` at export time, so admin customizations propagate. QGIS auto-loads a `.qml` only when its basename matches the `.geojson` exactly, so the two polygon `.qml`s are byte-identical copies under different filenames (`landslide_polygons.qml` and `landslide_polygons_flat.qml`) — that way both polygon files auto-style on drag-and-drop. The polygon style relies on the `landslide_class` column: it's already present on the flat file; on the normalized `landslide_polygons.geojson` it requires a QGIS table-join to `landslides.geojson` (with empty join-field-prefix so the column appears unqualified).

**First-shot scope**: import only UPDATEs existing records (matched by id). Records or polygons with new ids are previewed but not inserted; records present in DB but missing from upload are kept silently. INSERT support and upload-driven deletion are deferred.

Schema fingerprint: `python manage.py roundtrip_test` verifies that download → upload-without-changes is a byte-identical no-op against the live DB.

Workflow:
1. Click "⬇ Export" on `/inventory/manage/`. Save the zip.
2. Unzip in QGIS — drag the `.geojson` files in (styles auto-apply from the sibling `.qml`), edit attributes/geometries.
3. Re-zip (must contain `landslides.geojson` + `landslide_polygons.geojson` at the top level; other files are ignored).
4. Click "⬆ Import" → upload → preview → confirm.

## Planet Stories integration

Two Planet Stories formats are referenced from landslide records:

- **timelapse** — multi-frame animation with a backing MP4 at
  `https://storage.googleapis.com/planet-t2/<slug>/movie.mp4` (anonymous,
  public). These get archived locally to `data/planet_stories/<slug>.mp4` by
  the `archive_planet_stories` management command and served through this
  app via a stable URL (see below). The in-app player renders these without
  leaving the site.
- **comparison** — before/after wiper widget rendered by Planet's SPA; no
  MP4 to archive. Records pointing at these keep an external link to
  planet.com; the link survives only as long as Planet does.

Schema:

| Table | Role |
|---|---|
| `planet_stories` | One row per distinct slug — `story_type`, `mp4_archived_at`, `mp4_size_bytes`, `last_probed_at`, `manually_set`. |
| `landslide_planet_stories` | M:N membership — a landslide can reference multiple stories; a story can be referenced by multiple landslides. `sort_order` controls per-landslide display order. |

The legacy `landslides.planet_story_link` text column is still written by
the edit form and kept in sync with the join tables on save. It will be
dropped after the edit form gains a proper multi-story management UI.

**Runtime sync + auto-archive (`inventory/planet.py`).** Every path that
writes `planet_story_link` keeps the N:M tables in step and archives new
animations automatically — no manual command needed for the common case:
`planet.sync_story_link` (autosave `manage_edit_field` — the main path, which
previously updated only the text column, so review-form links never embedded
— and the `manage_edit` full-form save, whose old inline block it replaced)
plus `planet.ensure_story_rows` (additive, `apply_import` for file uploads,
filtered so a master-file re-import doesn't re-probe known stories). A
newly-linked slug is then probed + archived by `planet.ensure_archived_async`
in a background thread (downloads can exceed gunicorn's 30 s window):
HEAD 200 → timelapse → download to `data/planet_stories/<slug>.mp4` + stamp;
404 → comparison; network hiccup → left NULL for the batch commands to retry
(same disk layout + COALESCE stamping semantics, so hook and batch are
idempotent with each other). Slugs are charset-validated (`[A-Za-z0-9_-]+`,
matching the serving route) before becoming GCS URLs/filenames. Once
archived, `api_detail` returns `mp4_url` and the map detail panel embeds the
player on next open.

### Stable serving URL — load-bearing for snapshots

Archived MP4s are served at `/inventory/planet/<slug>.mp4` (no trailing
slash; `.mp4` suffix in the URL). **This URL is load-bearing**: published
snapshots embed it and reference it from their static HTML. The backing
storage may change (S3, CDN, etc.) but the URL pattern must remain stable.
If it ever has to change, add a redirect — do not just rename it.

The slug shape is regex-constrained at the URL layer (`[A-Za-z0-9_-]+`) so
the view can safely treat it as a filename without traversal risk.

### Operational commands

```bash
# Probe + classify all slugs, then download any timelapse MP4s not yet archived.
docker compose exec web python manage.py migrate_planet_stories
docker compose exec web python manage.py archive_planet_stories
```

`migrate_planet_stories` is idempotent — re-running it picks up any new
slugs added through the edit form, classifies them by HEAD-probing GCS, and
stamps disk-archive metadata. `--no-probe` skips the GCS check (useful when
offline). `--dry-run` rolls back at the end.

## Known future work: derived-value layer

Several fields in the inventory have a "preferred" value that's chosen from multiple sources by domain rules (e.g., `volume_preferred` overridden by `volume_site_specific` when present; otherwise inferred from `polygon_volume` = thickness × area aggregated across polygons). Today, the editor UI shows all of these as independent free-text fields with no enforced relationship — it's possible to set `volume_preferred` to something inconsistent with the polygon-derived total.

When ready, this wants a unified inference / validation layer: rules that compute derived values from inputs, flag inconsistencies, and let the editor either accept the computed value or explicitly override (with the override recorded as the source). Affects volume, area, dates (date_min / date_max / seismic_datetime priority), and possibly classification (when class implies type or vice versa).

Tracking this as "deferred but anticipated" so when we get to it, the UI design accounts for it rather than retrofitting.
