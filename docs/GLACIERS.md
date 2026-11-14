# /glaciers app

Tracers, IceBridge points, the experimental gate.

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
