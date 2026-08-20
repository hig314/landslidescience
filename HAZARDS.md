# HAZARDS.md — footguns, each earned by a real incident

Read before editing. Entries state the hazard, the incident that earned it,
and the correct move. Deep background lives in [CLAUDE.md](CLAUDE.md);
orientation in [ONBOARDING.md](ONBOARDING.md).

## Deployment & environments

- **Prod runs code baked into the image.** The only bind-mount on prod is
  `data/`. A `docker restart` deploys *nothing*; the sequence is `git pull &&
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build &&
  up -d --force-recreate`. (Incident: "deployed" changes that weren't there.)
- **Never push/deploy untested.** GitHub is the sync point with production.
  Build in dev, get the owner's explicit approval, then push. Load-bearing
  rule, top of CLAUDE.md.
- **Two environments = two PostGIS databases.** Dev and prod hold different
  data. Every management command touching landslide data (schema DDL, data
  migrations, flag scans, `init_groups`) runs **once per environment**. When
  new code SELECTs a new column, add the column on prod *before* the code
  goes live — the old code never references it, so pre-adding is a free
  no-op; the reverse order 500s the public API during the build.
- **PostGIS schema changes are idempotent management commands** (`ADD COLUMN
  IF NOT EXISTS`), never Django migrations — there are no ORM models for
  landslide data. Django migrations apply only to SQLite.
- **`data/` is gitignored and volume-mounted.** Tiles/bundles/media ship by
  a *separate* rsync, not the git deploy. `data/glaciers/experiments/` and
  `fit_tiles/` must **never** reach prod — the dev-only research stack
  (`/glaciers/pairs/` etc.) is gated on that directory existing
  (`experimental_enabled()`). Rsync with `--exclude 'experiments/'
  --exclude 'fit_tiles/'`. Put new research scaffolding behind the same
  gate; don't invent a second switch.
- **Port 8000 is the Tethys stack, not this project** — and it serves an
  older copy of the map, so it *looks* right while showing stale code. Dev
  is **8001**.
- **gunicorn kills requests at ~30 s.** Long work (tile bakes, MP4
  downloads) runs in background threads with a status column the UI polls
  (`trace_views._spawn_bake` is the pattern). Never do slow work inline.

## Django & templates

- **`{# #}` comments are single-line only.** A multi-line `{# … #}` renders
  as visible page text (Django doesn't scan across newlines). Use
  `{% comment %}…{% endcomment %}`. Check:
  `grep -n '{#' file | grep -v '#}'`. (Incident: explanatory comment
  rendered above the export panel, 2026-08.)
- **Static JS/CSS edits in dev need `collectstatic` + container restart** —
  WhiteNoise serves *collected* static, cached at startup. Templates and
  Python are live from the bind mount. (Incident: repeatedly "my JS change
  does nothing".)
- **Module-level `_cache` in `inventory/views.py`** memoizes features/
  counts/events. Edit paths invalidate it automatically, but out-of-band DB
  writes (management commands, manual SQL) do not — restart the web
  container after them.
- **Serving URLs are contracts.** Published snapshot bundles embed
  `/inventory/planet/<slug>.mp4` and `/inventory/photo/<id>/…` — never
  change those shapes without redirects. Same for the URL-hash grammar
  (`map=`, `base=`, `swipe=`, `sx=`, `ov=` incl. the `~s` variant suffix,
  filter params): saved/shared URLs and `default_map_view` strings decode by
  it forever.

## Raw SQL & the landslides schema

- **Adding a landslide column touches several hand-maintained SQL sites** —
  see the checklist in ONBOARDING.md. The traps: `_FILTER_PROPS_SQL` feeds
  *both* the points and polygons sources (adding a filterable prop to only
  one silently blanks the other when the filter is on — this happened with
  `flagged`); the timed/timeline event feeds unpack rows **by position**
  (`r[20]`…), in two nearly identical functions; `api_detail` alone is free
  (`row_to_json`).
- **The filter bitmask is append-only.** URL `f=` bits (molards=1 …
  super_elevated=2048) decode saved URLs by position. Add new bits at the
  top; never renumber.
- **`public_landslide_filter()`** is the single visibility predicate
  (reviewed + not deprecated). Any new public query must apply it; leaking
  pending/deprecated records is a real-data breach, not a cosmetic bug.

## map.js and the frontend

- **map.js is a ~6,500-line single IIFE.** When editing by search-replace,
  anchor on exact unique strings and assert the match count before
  replacing; a regex that matches a nested `{% endfor %}` or a similar block
  once deleted a whole event-handler run and killed every click on a page
  (strict-mode ReferenceError from one missing `var`). After surgery, run
  the structural checks: referenced DOM ids exist in templates; assigned
  identifiers are declared; JS parses (osascript JXA — there is no node).
- **Single sources of truth**: basemaps only in `basemaps.js`, symbology
  only in `ls_colors.js`, glacier overlays only in `ls_overlays.js`,
  hash codec in `ls_hash.js` (note: map.js still carries its own embedded
  hash parser with identical grammar — change both or neither until the
  planned migration lands), EPSG:3413 in `ls_proj.js`, PNG export in
  `ls_export.js`. Duplicated registries drift; that's why these exist.
- **Verify discoverability, not existence.** A control can be in the DOM,
  clickable by script, and invisible to a human (it rendered at y=2515 in a
  504-px panel). Screenshot the actual page and look.
- **`?v=` cache tokens**: all self-hosted tiles/bundles serve
  `Cache-Control: immutable`. Rebuilding pixels without bumping the matching
  token (`SUSC_TILE_V`, `OPERA_TILE_V`, `ITSLIVE_TILE_V`,
  `HUGONNET_TILE_V`, `TRACER_DATA_V`, `DATA_V`) leaves users on stale data
  indefinitely.
- **Terra Draw silently rejects coords with >9 decimal places**
  (`addFeatures` returns `valid:false` without throwing). Stored geometry is
  15 dp — round to 9 dp on the way in (`round9`), or polygons "load" empty.

## Maps, projections, and the globe

- **The map runs MapLibre's globe projection.** Consequences, all hit in
  practice:
  - `queryRenderedFeatures` over the whole viewport **degrades as you zoom
    out** (measured: 1,143 features in view at z4 collapsing to 28 at z3
    with the dots plainly on screen). Never use it for counting; use the
    shared `makeViewportTest()` predicate in map.js (geometry against
    `getBounds()`).
  - **North is not "up"** away from the center meridian. Measure it
    (`project` a point to the north) rather than assuming `-bearing`
    (the export's north arrow shipped pointing south).
  - **Ground scale**: measure via `unproject` across a screen span;
    `cos(latitude)` formulas drift under globe.
- **The antimeridian is live here even though no record crosses it.** A view
  panned across 180° (western Aleutians) can have bounds reported as e.g.
  `[174, 185]`; naive `lon < west` comparisons then drop every record in
  the state. Use `makeViewportTest()` — don't roll new bounds tests.
- **`pixelRatio` does NOT deepen raster tile requests.** A high-DPI export
  canvas gets the *same* tiles as the screen, upscaled (measured: 4× export,
  identical z10 tiles). MapLibre picks tile z from `zoom +
  log2(512/tileSize)`, so the export clone divides each raster source's
  declared `tileSize` (see `deepenRasters()` in `ls_export.js`).
- **Self-hosted science rasters have baked zoom ceilings** (susceptibility/
  ITS_LIVE/Hugonnet z10, OPERA z12). Past those they upscale, no matter
  what. Deeper detail requires re-baking pyramids, not client tricks.

## Science-data conventions (glaciers)

Summarized here because they're easy to violate silently; full detail in
CLAUDE.md §/glaciers:

- **ITS_LIVE seasonal amp/phase are climatological over a stated window**
  (2014–2024). Applying that cycle outside the window asserts seasonality
  the product never fitted — `seasonWeight()` fades it out; keep it that
  way. Amp is amplitude (not peak-to-peak); phase is day of *maximum*.
- **Mask fill values to NaN *before* any averaging** — fill-as-data produced
  diagonal "rocket" artifacts in tracer fields. Then apply physical
  plausibility clamps (nothing flows 25 km/yr).
- **ITS_LIVE `v_error` is scene-level and scales ~1/dt** — it is *smallest*
  exactly where errors are worst (short-baseline pairs). Don't use it as a
  per-pixel quality weight without a dt cap; a 450:1 precision weighting
  once collapsed a fit to near-zero on fast ice.
- **Formal standard errors lie under correlated residuals** (a 2σ gate
  passed 89% of noise). Gate on *replication* (split-half agreement)
  instead.

## Verification discipline

- **Measure, don't assume.** The costliest wrong claims in this project's
  history were plausible statements that were never checked (pixelRatio
  fetches deeper tiles — false; queryRenderedFeatures counts what's on
  screen — false at low zoom; "records exist past the antimeridian" —
  false). When a claim matters, instrument it (CDP network capture, unit
  tests on extracted functions, count reconciliation to exact arithmetic).
- **Reconcile totals exactly.** "About right" counts hid a measurement
  artifact (+28 from an unrelated element) and two legitimately absent
  records. When numbers don't reconcile to zero, find out why before
  shipping.
- **When a fix has siblings, fix the siblings.** The viewport-count bug
  existed in three hand-copied variants; fixing one left two. Grep for the
  pattern you just fixed.
