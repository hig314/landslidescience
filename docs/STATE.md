# State of play

*Last revised 2026-11-14.* The one dated snapshot in the project notes: what
is waiting to be confirmed on production, what is open, and a short log of
what shipped. **It rots**; correct or delete an entry rather than working
around it. For lidar publishing state do not trust any number written down
anywhere: read `/lidar/audit/`.

How this file is kept: a feature's design lives in its `docs/` file, not
here. When something ships it gets one line in the log and, if part of it has
not been exercised on production, one line in the checklist. Log lines older
than about two months can go; git has them.

## To confirm on production

Each of these was deployed after Hig tested it in dev, but the part named has
not knowingly been used on prod. Most get confirmed by ordinary use and
nobody writes it down, so **from time to time Claude reads this list back to
Hig and strikes what he has used**; only what is still untried needs a
deliberate look. Add a line at deploy time whenever a path was checked only
by curl, by API or not at all.

- [ ] **Imagery review history** (0ec6b75, 2026-10-26): signed-in use on prod; the one-time notice has not been seen there.
- [ ] **ArcticDEM in the ∿ profile and Alt-click probe** (bd1a6a9, 2026-10-30): nobody has drawn a profile on prod.
- [ ] **Shaded relief overlays** (30dd97f, 2026-10-30): page loads and served JS only.
- [ ] **Overlay regrouping + fixed-scale slope rows** (dfe53d2, 2026-11-01): not drawn in a browser on prod.
- [ ] **NOAA chart basemap, NASA global susceptibility overlay** (9585f3d, 2026-11-05): neither drawn in a browser on prod.
- [ ] **NASA susceptibility and the five fixed-scale slopes as analysis axes** (df9247b, c0ce02a): checked by API only.
- [ ] **Edit-form checks** (069905e, 2026-11-11): nobody has saved through the banner / date-bracket / year-text checks on prod. Records 1166, 1174 and 1504 will trip them on their next edit (see *Edit-form follow-ups* below).
- [ ] **Terrain comparison** (2026-11-12/13): profiles, shared QMS bases, `hc=1`, JPEG export not browser-tested on prod.
- [ ] **Saved comparisons** (6c8dc13, 2026-11-13): nothing saved on prod yet; the first signed-in *Submit* is the first real test.
- [ ] **`/traffic/services/`** (2026-11-14): not opened signed in on prod.
- [ ] **Autosave error logging; `/manage/new/` redirect** (15186a4): nothing has failed on prod since, so the log line is unseen.
- [ ] **Restore from backup** (2026-11-14): the first backup uploaded 2.6 GB; nobody has restored anything from it.
- [ ] **`/lidar/` Save image** (a43bf5c, 2026-10-19): the export engine's polling-for-done change was not re-tested on `/inventory/`'s own PNG export.

## Shipped log

One line each, newest first; detail is in the `docs/` file named.

- 2026-11-14 · Baked overlays (susceptibility, coherence, IceBoost, ITS_LIVE, Hugonnet) read from PMTiles on R2 (fad9b74); the five tile folders deleted from the droplet · docs/OVERLAYS.md
- 2026-11-14 · `check_invariants`, service probe + `/traffic/services/`, nightly `site_backup` to R2, local-only lidar manifest, autosave error logging, `/manage/new/` retired, Scoops tools committed with the row off (15186a4) · docs/OPERATIONS.md, docs/LIDAR.md
- 2026-11-14 · CLAUDE.md split into an index plus `docs/` (725b3c8)
- 2026-11-13 · Terrain comparison later rounds: re-order panes (09fcec9), QMS bases in the link (0af7cb2), `hc=1` (e4f502f), equal panes + JPEG (f2b7e33), saved comparisons (6c8dc13) · docs/LIDAR.md
- 2026-11-12 · Terrain comparison `/lidar/compare/` with profiles (7c93ea1, bf27a7c); `/lidar/` *Point cloud* link · docs/LIDAR.md, LIDAR_COMPARE_PLAN.md
- 2026-11-11 · Edit-form checks; Explore opens the record's view (069905e) · docs/RECORDS.md, docs/EXPLORER.md
- 2026-11-11 · Stereonet equal-area cells + aspect effect · docs/ANALYSIS.md
- 2026-11-10 · Slope-aspect stereonet (b3d1906) · docs/ANALYSIS.md
- 2026-11-08 · Fixed-scale slopes as analysis axes (c0ce02a) · docs/ANALYSIS.md
- 2026-11-07 · NASA susceptibility as an analysis axis (df9247b); stale-values-file fix (`DATA_V`) · docs/ANALYSIS.md
- 2026-11-06 · Homer 2019 + 2021 NCMP published (8a7a653); Kenai 2008 point cloud on R2 · tools/lidar/RESUME.md
- 2026-11-05 · NOAA chart basemap + NASA global susceptibility (9585f3d); erosion-rate overlays built and switched off · docs/OVERLAYS.md
- 2026-11-05 · Grand Plateau preliminary DSM, gated (64d2006); gated `_slope` pyramids closed to anonymous requests (c309b1c) · tools/lidar/RESUME.md
- 2026-11-01 · Overlay regrouping + slope at fixed scales (dfe53d2) · docs/PERMAFROST.md
- 2026-10-31 · Analysis panel: reviewed area + subsets (df4d19c..e0d93cb); drops resume mid-file (a400820) · docs/ANALYSIS.md, docs/FILES_DROPS_PHOTOS.md
- 2026-10-30 · Default views with restricted layers (806b024); shaded relief (30dd97f); ArcticDEM in profile/probe (bd1a6a9) · docs/MAP_UI.md, docs/OVERLAYS.md
- 2026-10-29 · Canadian layers: US / Canada Topo, BC Orthophoto, NRCan InSAR (4f4c5af, 9c504c2, 5a24b7d); emailed game cams · docs/OVERLAYS.md, docs/FILES_DROPS_PHOTOS.md
- 2026-10-26 · Edit-form fixes (c3232c0); imagery review history (0ec6b75) · docs/RECORDS.md, docs/MAP_UI.md
- 2026-10-19 · `/lidar/` links (`ce=`) and PNG export (9534b42, a43bf5c) · docs/LIDAR.md
- 2026-10-12 · Lake soundings · docs/TRACES.md
- 2026-10-11 · Landform traces (kinds, groups, group profile); the brush paints the map · docs/TRACES.md, docs/ANALYSIS.md
- 2026-10-10 · The analysis panel · docs/ANALYSIS.md
- 2026-10-09 · Permafrost overlays + Surface properties · docs/PERMAFROST.md
- 2026-10-05 · Photo drops · docs/FILES_DROPS_PHOTOS.md
- 2026-09-24 · External inventories; pointer policy (`ls_tools.js`) · docs/EXTERNAL_INVENTORIES.md, docs/MAP_UI.md

## Open threads

In no particular order.

- **Edit-form follow-ups (2026-11-11).** Prod records the year-text check
  will catch on their next edit (read-only survey of 256 dated records: 212
  already agree, 42 will show the "dates do not constrain" warning):
  *Caribou Divide 2001* (1166: "Modern" with both dates in 2001) is refused
  on its next full save until year text reads 2001, and the banner says so;
  *Chitna Creek A Holocene* (1174: "Holocene", `date_min` 1979) gets the
  amber Holocene warning but saves; *Jan Mayan 2024* (1504: blank) is filled
  with 2024 on its next save. An autosave on dev 1490 returned 500 at 03:13
  that day from the catch-all in `manage_edit_field`, which logs nothing, so
  the field and cause are unknown (the catch-all now logs the traceback, field and user: 2026-11-14, in dev).
  Dev 1490's `updated_at` and audit row were stamped by a diagnostic POST
  (dates written then cleared, both NULL again). Unanswered offers: a
  "Saved" notice after the edit page's own reload; grid-north aspect in the
  x-y panel and aspect overlay; the analysis panel `draw()` canvas ordering;
  `docker builder prune` on the droplet. Pre-existing on prod: console
  "Style is not done loading" from `ensurePendingLayers`.

- **Homer 2019 + 2021 NCMP green lidar, and point clouds on R2**
  (2026-11-06). `homer_2019_ncmp` and `homer_2021_ncmp` are **published**
  (8a7a653; 50 public surveys). Point clouds have a registry
  (`tools/lidar/points.json`), a tool (`points_publish.py`) and a bucket
  layout (`points/<survey>/<rev>/`); Kenai 2008 r1 (our ground
  classification, 67 GiB, 3,110 tiles) is the one test upload — **done and
  verified 2026-11-06 23:22**, public at
  `lidar.landslidescience.org/points/kenai_2008/`. The `/lidar/` *Point
  cloud* link and its Methods paragraph **shipped 2026-11-12** with the
  terrain comparison (Hig approved); the prod catalogue is rebuilt with
  `points_url` per the recipe in `tools/lidar/RESUME.md`. Hig's rule: link a cloud an agency already
  serves well, do not copy it; nothing beyond Kenai 2008 until the layout is
  judged; Grewingk waits on the reclassification work. State and commands:
  the last entry of `tools/lidar/RESUME.md`.

- **NOAA's chart server fails about 1 request in 20** (measured 2026-11-05:
  4 of 72 tiles at 6 in flight came back as an HTML "ArcGIS Web Adaptor:
  Could not access any server machines" page with status 200 and no CORS
  header, which the browser reports as a failed fetch). MapLibre does not
  retry, so the chart shows an occasional missing tile until the map moves.
  Not fixed: a retrying `addProtocol` loader in `basemaps.js` (like `reproj`)
  is the obvious route; a proxy would cost droplet disk and gunicorn time.
- **Overlay regrouping + fixed-scale slope shipped** (2026-11-01, dfe53d2; R2
  synced, not yet drawn in a browser on prod). The old `sp_slope` archive stays
  on R2 until Hig says to remove it. Coverage stops at the Alaska border, so the
  Canadian side of St. Elias is blank until coverage grows.
- **Required strength from topography (Scoops3D rebuild), exploratory,
  committed 2026-11-14 with its overlay row switched off (`CREQ_ACTIVE` in
  `map.js`; Hig: fine for it to be public, harmonise when work resumes)** (2026-11-01..05): `tools/terrain/scoops/` plus a dev-only
  overlay row `sp-creq-20-200`. State, results and held decisions:
  [tools/terrain/scoops/README.md](../tools/terrain/scoops/README.md). **No
  comparison with landslide mapping until Hig lifts that.**
- **Grand Plateau 2026 preliminary DSM is on prod, gated** (2026-11-05,
  64d2006): `grand_plateau_sfm_2026_prelim` from the drop `z2udka3Z6dGU`,
  z9-17, pyramids on the droplet only, no COG there (dead download link,
  accepted). Hig looked at it after the deploy: "this looks good"
  (2026-11-05). Nothing further is owed on it. The final
  NPS release (winter 2026-27) is to be a new public entry, with this one
  retired and its 1 GB removed from the droplet. Same day (c309b1c): the
  `_slope` pyramids of gated surveys had been open to anonymous requests;
  now gated. Details: the last entry of `tools/lidar/RESUME.md`.
- **Droplet disk**: 12 GB free of 78 (85%) on 2026-11-14, after a Docker
  build-cache prune (3.5 GB) and moving five tile trees to R2 (2.3 GB).
  What is left: the monitoring stack's Timescale database 29 GB (the other
  repo), Docker images 18.5 GB, `/var/log/journal` 2.9 GB (a vacuum to
  500 MB freed nothing: the files are active, not archived), and this site's
  `data/` ~10 GB: gated lidar 4.8, field photos 1.5, planet_stories 0.7,
  quantities 0.5, glaciers 0.5, trace_tiles 0.4, opera cache 0.3,
  permafrost 0.25.
- **Leftovers of the tile move**: the `/tiles/susc|coherence|iceboost|itslive|hugonnet/`
  routes and views in `landslidescience/urls.py` serve nothing now and can
  go; so can the `CREATE` branches in `terra_draw.js` / `_polygon_map.html`
  (the retired `/manage/new/` page). The dev trees under `data/*_tiles/`
  are the only tile-tree copies left (the archives on R2 hold the same bytes).
- **Emailed game-cam feed moved to the monitoring project**; this site's
  `drops_mailcam` code and `/drops/cam/…/latest` routes are stale and
  undecided (docs/FILES_DROPS_PHOTOS.md).
- **Shaded relief / ArcticDEM follow-ons, deferred** (not to start
  unprompted): bake the 2 m ArcticDEM ourselves; deeper Mapterhorn zooms
  outside Alaska; ArcticDEM through the tile Worker. The PGC service gives
  flat grey, blocky shading at low zoom (z5): that is display quality, not
  speed. Hig accepted it.

- **Tatonduk River C (prod id 1575) is hidden from the map** because its
  class is a bare `Catastrophic`: full size, 2018, creep not evaluated.
  That label is not in map.js `CLASS_ORDER`. The fix for this record is
  to tick *creep evaluated* in prod by hand. **Tatonduk River B 2024**
  has a `body` polygon on a catastrophic record and should be `source`.
  Still undecided (Hig deferred it): whether the rule should return
  NULL/magenta instead of bare `Catastrophic` (and `Slow Cryptic`), or
  map.js should get a fallback for unknown labels.
- **Review history loose ends**: floating panels covering the map are
  ignored; reviewer and since are not in the URL; there is no pause
  switch. ~40 of Hig's dev review rows were deleted by a test cleanup on
  2026-10-26 (no backup); dev history restarts from then.

- **`/lidar/` loose ends from 2026-10-19.** (1) Switching surveys
  mid-session can still drop the camera: every terrain rebuild resets the
  centre elevation and only a link's camera is held. (2) The DemShade
  package's `trackTerrainCenter` `relativeThreshold` reads
  `getFreeCameraOptions`, absent in MapLibre 5.24, so it is stuck at its
  0.5 m fallback. (3) The package's URL-keyed tile cache hands a second map
  a buffer already transferred to the first map's worker — worked around in
  the export with `&export=1`; worth raising with the package owner (shared
  with trailgeek). (4) Export: no legend, HTML markers (A/A′, go-to ring)
  missing, scale bar off by default when pitched; the export engine's
  polling-for-done change was not re-tested on `/inventory/`'s export.

- **A corrected online copy of the Portage drop.** The time-corrected,
  stamped frames exist only on Nunatak. Hig's call (2026-10-06): a second
  drop holding them (pushed with rclone, rows via `drops_reconcile`) is the
  right shape, and it is **on hold**. Viewing stays login-gated, decided the
  same day.

- **The generalised analysis tool, phase 3 — parked 2026-10-11 to move to
  another topic.** Phases 1–2 of [ANALYSIS_TOOL_PLAN.md](../ANALYSIS_TOOL_PLAN.md)
  were built, reviewed in four passes and deployed 2026-10-10; the painting
  brush (phase 3's first piece) deployed 2026-10-11. **The agreed next
  step is the sample shapes**: a landslide's own polygon as the sample
  first ("what does this slide's ground look like in these axes?" — the
  cheapest and most useful), then the current view (which folds the OPERA
  panel in as "sample = view"), then a drawn polygon; it means generalising
  `LSSample` from a circle to a shape, a polygon form of
  `api/analysis/sample/` (shapely is in the container), the 60 m reader
  testing inside-polygon, and a URL form per shape. After that: value
  archives for coherence / ITS_LIVE (which would let them paint), dh/dt +
  IceBoost warped onto one grid, and two findings parked on the way — the
  MAAT/MAGT ramp is white at 0 °C (invisible over the Blank White basemap;
  RdYlBu would need four archives re-baked) and aspect below 1° of slope
  piles up at 45° multiples (HAZARDS §Raster sources).
- **Rule-based derivation from mirrored inventories.** Promotion today seeds
  provenance and two fields, then the editor types the rest. `external.py`'s
  docstring anticipates a `derive` step with per-field rules (how to read
  their movement category, their size class, their timing). Bulk adoption is
  deliberately absent and should stay a decision, not a default.
- **Induction Phase 2–3** (supersede/merge UI for `block`/`review`
  collisions) — see *Induction safety & collision detection*.
- **Transient-overlay Escape stack.** The photo lightbox and the draw-choice
  menu still keep their own Escape listeners outside `ls_tools.js`; they are
  modal overlays rather than tools, and want a small stack of their own.
- **`/lidar/` now loads `ls_tools.js`** (2026-10-11, for the landform trace
  tool — its first pointer tool; tool group top-right). The profile and the
  Alt-click probe are the obvious next tools there; they register with the
  same dispatcher rather than growing a policy of their own.
- **Landform traces, deployed 2026-10-11** (with the group profile and the
  shared profile chart; first group on prod: Moosehorn-Seldovia moraines,
  15 traces copied from dev): the
  scarp table generalised to every traced line (`kind`: scarp, moraine
  crest, trimline, strandline, lineament; `traced_on`; named groups, one
  per trace, with a GeoJSON download each), traced on `/lidar/`.
  First use: Pleistocene moraines. See *Landform traces* below. Open: a
  profile along a trace; `/glaciers/` reading the traces as a reference
  layer; whether tracing other kinds on `/inventory/` is wanted.
- **Self-host the NRCan InSAR mosaics — deferred (Hig, 2026-10-29).**
  The NRCan overlay shows only the ascending mosaic, pre-coloured, from
  their WMS. Hig wants the data hosted here eventually; the plan is in
  *NRCan InSAR velocity overlay* below. Not started.
- **trailgeek.org** is a sister site that copies four of this repo's files
  by a pinned sync and reads the lidar catalogue; its own trace editor and
  3D are richer than ours. Its open ask of this repo: adopt `dem_stack.js`
  in `/lidar/`. Relationship and rules: WORKSTREAMS.md *Sister sites*.

## Known bugs left alone

**Two pre-existing bugs found 2026-09-24 and deliberately left alone**, so
nobody rediscovers them as regressions:

- The wiper's second map adds `survey-circles-label` and `insar-click-label`
  without a style `glyphs` property, logging two console errors whenever the
  wiper is enabled.
- Double-clicking a landslide emits `landslide_open` three times (the
  default-view path re-renders the panel). Analytics counts are inflated for
  double-clicked records.
