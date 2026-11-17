# /glaciers app

Glacier identity and retreat histories, flow tracers, IceBridge points, the experimental gate.

## Since 2026-11-16: a glacier browser, retreat histories first

Plan, decisions and the source survey: [GLACIERS_PLAN.md](../GLACIERS_PLAN.md).
Phases 0–2 are built (see docs/STATE.md for whether they are deployed). What
follows is the plumbing; the sections further down, written when the page was
only a flow viewer, still describe the Flow tab and the overlays.

**Three files where there was one.** `glaciers.js` is the page shell (map,
basemap, raster overlays, IceBridge points, tabs, URL hash, `window.GL`).
`gl_retreat.js` is the Retreat tab. `gl_tracers.js` is the tracer engine,
moved out mechanically with the engine code unchanged, **loaded only when the
Flow tab is first opened** and stopped (frame loop off, canvas hidden) while
another tab shows. Clicks, cursor and the popup slot go through `LSTools`,
as on `/inventory/` and `/lidar/`.

**Tabs and the hash.** `tab=retreat|flow|layers` is always written. A link
with `site=` or `t=` and no `tab=` is a pre-tabs flow link and opens on Flow.
Retreat adds `g` (selected glacier), `yr` (year bar), `src` (sources on;
absent = all), `gc=0` (ice cover off). Params a not-yet-loaded module owns
are carried through untouched.

**Identity: `glaciers`, keyed by RGI 7.0 id, not GLIMS id.** RGI 7.0 region
01, 27,509 rows, from the GLIMS WFS (no login). RGI gives one GLIMS id to as
many as eight glaciers (135 ids are shared), so the GLIMS id is an attribute.
Only 661 rows have a name. Deep link: `/glaciers/g/<id>/`.

**Mirrored sources: `glacier_obs`.** One row per published feature,
publisher attributes verbatim in `props`; beside them our reading (`kind`
outline / debris / terminus, `glacier_id` + `link_how`, `date_min`/`date_max`).
Registry, readers, matching rules and the schema are all in
`glaciers/sources.py`. A fetch replaces one source in one transaction and is
count-checked.

| Source id | What | Rows (dev, 2026-11-16) |
|---|---|---|
| `glims` | GLIMS outlines, Alaska box, via WFS | 68,760 (63,558 matched) |
| `nsidc0634` | McNabb & Hock tidewater termini 1948–2012 | 10,333 |
| `usgs_glacier_bay` | USGS Glacier Bay termini 1700–2019 | 233 (151 matched) |
| `usgs_benchmark` | USGS benchmark margins, Alaska | 46 |

- **GLIMS has no primary key over WFS**, so `startIndex` paging has no stable
  order. The fetch cuts the box into cells, asks each whole, and recognises a
  feature that straddles an edge by content. The total is checked against
  the server's.
- **Matching** (`link()`): GLIMS by id where the id names exactly one of our
  glaciers, else by largest overlap; polygons by overlap; a terminus line to
  the glacier it runs across furthest, else the nearest within 2 km;
  NSIDC-0634 once per file, a same-named candidate winning outright. A row
  that matches nothing stays unmatched and is still drawn.
- **The file-based sources are prepared on the workstation**
  (`tools/glaciers/download_sources.sh` then `prep_sources.sh`; NSIDC-0634
  needs the Earthdata login in `~/.netrc`, and wget fetches nothing there,
  curl works) into `data/glaciers/sources/*.geojsonl`, which travels to
  production by rsync. The container has no GDAL.
- **Tile geometry is stored in Web Mercator at two detail levels**
  (`geom_s` about 30 m, `geom_c` about 250 m). Transforming full outlines per
  request made a statewide tile take 3.2 s; it is 0.4 s now. Small-scale
  tiles are also kept per worker, keyed on the stamp.
- **The stamp** (`data/glaciers/.extent_stamp`) is bumped by every fetch and
  rides tile and JSON URLs as `?v=`.
- **Size**: the two tables are about 1 GB in PostGIS (824 MB + 193 MB in
  dev). The droplet had 12 GB free on 2026-11-14 (docs/STATE.md). Check before
  loading production.

Per environment, in order: `migrate_glaciers`, `fetch_glacier_source rgi7`,
`fetch_glacier_source --all`. `--relink` re-matches without refetching.

**Statewide ice cover (NSIDC G10040) is one raster archive of bit masks**,
`data/overlays/glac_cover_mask.pmtiles` (+ `glac_debris_mask`), built by
`tools/glaciers/build_cover_mask.py`: bit i of a pixel = ice in epoch
1986 + 2i. The `glcover` protocol in `gl_retreat.js` decodes a tile once and
colours it for the year on the bar, so all 18 epochs cost one download and
"ice lost since 1986" is a bit test. **The tiles are data: PNG, nearest
resampling at every zoom, alpha only 0 or 255.** The epoch list is held in
both the builder and the page; `check_invariants` compares them. Vector tiles
were tried first and abandoned: GDAL's writer filled whole tiles solid on
these polygons.

**Year colours** for every dated line are `LSColors.glacierYearColor`
(`ls_colors.js`).

### The flow network and ice faces (2026-11-16, later the same day)

Hig's refocus (GLACIERS_PLAN.md §0): area is not the target; **calving-face
position** is, then surface elevation, both against distance along a
**river-like network of centrelines with feeders**. Built: `glaciers/network.py`,
`gl_network.js`.

- **`glacier_centerlines`** is the RGI 7.0 centrelines product as published
  (41,293 lines, reference only, tile layer `lines`). Lines run down-flow and
  stop at the glacier's outline and terminus. Load: `fetch_glacier_source
  centerlines` (file prepared on the workstation from the Bremen mirror; the
  NSIDC copy needs Earthdata).
- **`glacier_lines`** is what we edit: `kind = flowline` (adopted from RGI,
  drafted, or drawn) and `kind = face` (a dated line along an ice front; no
  polygon asked for, partial lines welcome). Kept apart from the reference so
  the editor's whole list stays small.
- **A join is a shared coordinate** (Hig, 2026-11-16). `normalize()` runs
  after every flow-line change: a line END that lies on another line (within
  2 m, which in practice means the editor snapped it there) becomes a vertex
  of both; a line END on another line's START **fuses the two into one row**,
  permanently (origin `mixed` when the halves differ). `rebuild()` then sets
  `down_id`, `join_frac`, `end_chain_m` (distance of the line's downstream
  end from the outlet) and `network_id`. The dots on the map are downstream
  ends: ring = joined, solid = outlet.
  - RGI tributaries stop 150 to 330 m short of the line they feed. On
    adoption `join_rgi()` carries each on to the line RGI names
    (`outflow_id`). The first version guessed joins by proximity (300 m):
    do not bring that back.
  - **An existing vertex within 2 m wins over inserting a new one.** The
    editor works at 6 decimals, so a trunk saved from the browser sits
    centimetres off its feeders' ends; inserting "the exact point" added a
    duplicate vertex at every join on every save.
  - `normalize()` is global: the first call after this rule shipped fused
    every end-to-end pair in the table, including two machine drafts that
    were about to be regenerated. Expected now; worth knowing.
  - A fused line no longer records where the RGI part ended, so the plot
    cannot shade "glacier today" against "drawn beyond". Not yet replaced by
    a present-terminus marker.
- **The editor is scarps.js** (options added for this page: docs/TRACES.md),
  mounted with `api: '/glaciers/lines/'`, explicit drawing and end snapping;
  `network.py` answers that module's contract (`api/scarps/`, `create/`,
  `<id>/`), which is the only reason those URLs say "scarps". Extra fields
  (name, date, basis, source, reverse, pin) ride its `save()`. The session is
  an `LSTools` mode (the ✎ control), editors only. **Start it only after the
  map style has loaded**: its list answer once beat the style and its first
  `addSource` threw.
- **Editing, after Hig's first sessions**: nothing is drawn until a ＋ button
  arms one line; a clicked vertex is active and Delete removes it; a **+** at
  each end of the selected line extends it from that end (the start too);
  geometry saves are serialised per line, latest wins, because fast deletes
  were being overdrawn by stale answers. All in scarps.js (docs/TRACES.md).
  The page's scripts carry `?v=<newest mtime>` so an edit is never run
  against a cached older file.
- **Getting lines in**: *Add its centrelines to the network* on a glacier's
  card (`api/adopt/`); *Draft the former bed* (`api/draft/`: the median midpoint of
  the mirrored faces in each km out from the terminus, smoothed, fused onto
  the main line; a guess to correct by hand. The first version chained
  midpoints one by one and zig-zagged where the terminus had sat still); draw; or
  import GeoJSON lines with a `date` property (`api/import/`). Dates are
  brackets: `1904`, `1904-08`, `1904-08-15`, `1894 to 1904`.
- **A path** (`api/path/<line id>/`) is one line followed down to its outlet.
  A face is placed at the mean of its vertices' positions along the reach it
  crosses (width-averaged; the bare crossing is returned too); a face that
  stops short but lies within 1.5 km is projected and marked; a pinned face
  (`pin_line_id`, `pin_frac`: schema and maths are in, the control to set a
  pin is not) sits where pinned. The plot is time across and distance up,
  measured down-flow from the HEAD of the line so a retreat reads as falling
  numbers (Hig, 2026-11-17; the API still counts `x_m` up from the outlet,
  the page converts) (Hig's preference; the other way round is a menu choice kept
  per browser), the distance axis fitted to where the faces are; `np=<line
  id>` in the URL.
- **Check against the publisher** (Icy Bay, dev): McNabb & Hock's own length
  plus our distance should be constant. NMAD 0.12 km at Tyndall over an
  18.5 km retreat, 0.26 Guyot, 0.38 Yahtse, 0.04 Tsaa; worst single faces 1
  to 4 km, on drafted lines not yet corrected.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## /glaciers app (experimental; tracer MVP ships, pair stack does not)

Sibling map app at `/glaciers/` (`glaciers/` Django app; public, like
/inventory/* — it keeps a `noindex` robots block rather than a login barrier,
since it is experimental rather than sensitive). Hig declared the tracer app
MVP on 2026-08-19, so it deploys under the normal flow. The RAW IMAGE-PAIR
stack does NOT: `/glaciers/pairs/`, the fitted-pair overlays and the
"robust pair fit" tracer field are all gated on `experimental_enabled()`
in `glaciers/views.py`, which tests for `data/glaciers/experiments/` — a
directory that is never rsynced to prod. Keep new research scaffolding
behind that same gate rather than inventing a second switch.

- **Shared-module discipline**: basemaps from `basemaps.js`, glacier
  overlays from `ls_overlays.js` (single source of truth with the inventory
  map — add/change glacier overlays ONLY there). Planned convergence
  (in order): shared hash codec (`ls_hash.js` — **done 2026-10-06**, map.js
  parses and writes through it), shared overlay framework
  (state/rows/ov= codec into ls_overlays.js), shared wiper (`ls_swipe.js`)
  — each extracted behavior-identical from map.js with the inventory as the
  regression baseline, then consumed by /glaciers.
- **Lagrangian tracers**: particles advected through the time-varying
  ITS_LIVE field (annual vx/vy 1984–2025 + climatological seasonal
  amp/phase superposition), density-managed respawn with fade-out. Engine:
  `glaciers/static/glaciers/js/glaciers.js`. Bundles: built by
  `tools/build_glacier_tracers.py` (venv w/ zarr; reads the v2 annual
  composite cubes; 240 m int16 gzipped) into `data/glaciers/<slug>.bin.gz`
  + `.json`, served at `/glaciers/data/` (Content-Encoding: gzip against
  the .bin URL). Composite-cube dir naming = TRUNCATION toward zero of
  center lon/lat (verified; script HEAD-probes candidates).
- **Seasonality is window-bounded, and must stay that way**: ITS_LIVE's
  `vx_amp`/`vx_phase` are *climatological over a stated window* ("[2014-
  2024]"), while the tracer timeline runs 1984–2025. `seasonWeight()` in
  glaciers.js fades the sinusoid to zero outside that window (one-year
  taper) so pre-2014 playback shows annual means only — replaying the cycle
  across the Landsat-5/7 era asserts seasonality the product never fitted.
  The window comes from `season_window` in the bundle header; both builders
  parse it from the cube attribute. Conventions verified against the cube
  metadata: amp is amplitude (not peak-to-peak), phase is the day of
  MAXIMUM (so cosine, peaking there), and the annual field is the
  sinusoidal fit's own mean — the added term is zero-mean, nothing is
  double-counted. Caveat worth remembering: across the pair-sweep work a
  single annual sinusoid explained only 12–34% of temporal variance, so the
  tracers show the climatological cycle, not the year's actual seasonality.
  Where observed quarterly fields exist (`recent` block, 2013–2025) they
  take precedence and no model is used at all.
- **Dev mount**: `./glaciers` is volume-mounted in docker-compose.override
  like the other app dirs.
- **IceBridge radar bed elevation / ice thickness (UAF HF + ARES)**: two
  point-data overlay toggles, statewide AK — a precursor layer toward a
  planned ice-dam flotation analysis (that analysis itself is a separate,
  still-evolving raster-stack workflow, tracked outside this app for now).
  Pipeline: `tools/fetch_icebridge_data.py` (workstation venv w/
  `earthaccess`; NASA CMR short_names **IRUAFHF2** 2013–2016 and **IRARES2**
  2016–2021, both Alaska + NW Canada, both the identical field layout —
  `lon_deg_e`/`lat_deg_n`/`bed_height_m`/`ice_thickness_m`; bed/thickness are
  left EMPTY, not a sentinel, on traces with no bed interpretation) → raw
  CSVs into `data/glaciers/icebridge_raw/<short_name>/` (2+ GB, gitignored,
  never rsynced to prod — the record of truth stays local) →
  `tools/build_icebridge_points.py` filters to rows with a `bed_height_m`,
  keeps lon/lat/bed_height_m/ice_thickness_m, and writes
  `data/glaciers/icebridge_{uaf_hf,ares}.json`. **Each NSIDC CSV opens with a
  block of `#`-prefixed metadata lines before the real header row** — skip
  them or every column comes back empty. **ARES alone is 2.88M qualifying
  rows** (a 545 MB GeoJSON, unfetchable client-side), so the build script
  grid-decimates to one point per `GRID_DEG` cell (~55 m) — a display
  simplification, not a science product; the raw CSVs remain authoritative.
  Named `.json` not `.geojson` so the existing `glaciers:tracer_data`
  route/regex serves them with zero backend changes (`_catalog()` already
  skips any `data/glaciers/*.json` without `name`/`center`/`bin` keys, so
  these don't get mistaken for a tracer bundle). **Not gated behind
  `experimental_enabled()`** — unlike the pair-fit stack, this is general,
  statewide reference data rather than a one-glacier research probe, so it
  ships through the normal dev→test→approve→push flow. Rendering is local to
  `glaciers.js`, not `ls_overlays.js` — that shared module is raster-tile-
  only (`sourceDef()` → `rasterDef()`), and this is sparse vector/point data;
  `ensureOverlays()`/`applyOverlays()` branch on `ov.type === 'circle'` to
  add a MapLibre `geojson` source + data-driven `circle-color` layer instead
  of a raster one. **Color-by is a shared toggle** (`ICEBRIDGE_RAMPS`, one
  control drives both instrument layers so they stay comparable): ice
  thickness is a plain magnitude (sequential, one hue light→dark); bed
  height renders as an ABRUPT seam at 0 m — blue below (flotation-relevant),
  green above — each arm still gradated by magnitude, medium-toned at the
  seam (not pale) so the hue swap itself reads clearly, fading darker toward
  each extreme. Domain bounds (-450..900 m bed height, 0..700 m thickness)
  come from the real pulled data's printed percentiles, not guessed. The
  legend bar's `stops` array doubles as its CSS gradient definition (a
  repeated value = the hard seam); tick labels are positioned by actual
  value percentage, NOT CSS flex `space-between` — space-between spaces
  labels evenly regardless of value, which silently put the "0 m" label at
  the visual center instead of over the real color seam. Deploy note: the
  two built JSON files are gitignored like all of `data/` — they need their
  own rsync to the droplet's `data/glaciers/`, independent of the code push
  (same pattern as the susceptibility/OPERA tiles).
- **Future**: glacier-properties tab paralleling the inventory's landslide
  tab — eventual input to Bayesian failure-likelihood analysis surfaced in
  the landslides app. Keep sidebar/tab chrome and per-glacier identity
  (RGI ids) in mind when refactoring.
