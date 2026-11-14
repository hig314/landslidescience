# Raster overlays and basemaps

The overlay framework; InSAR (OPERA, NRCan), coherence, OPERA DIST, Canadian and NOAA basemaps, erosion rates (off), shaded relief, the four susceptibility models.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## The overlay framework, InSAR, basemaps, erosion rates, shaded relief

**Raster overlays — one framework** (`OVERLAYS` registry + `_ovEnsure`/`_ovApply`/`_overlayRow` in map.js): the two USGS susceptibility models AND the two OPERA InSAR velocity mosaics. **Bedrock geology** (2026-09-26) is the USGS Geologic Map of Alaska (SIM 3340, 1:1.58M) as Macrostrat's carto tiles (CC-BY 4.0, CORS open, no proxy, no bake); a click looks the unit up at Macrostrat's point API. It is the first RASTER click target: `ls_tools.js` accepts a `reference` entry with no `layers` and a `test(e)` predicate for exactly this (lowest priority, so faults/scarps/staged win under the same click). `keyNote` on an overlay renders a sentence in the key instead of a ramp. Per-overlay state is `{left, right, opLeft, opRight}` persisted per-browser (`localStorage['ls_overlays']`; migrates the short-lived `pane` form and the retired shared-`opacity` form, seeding both sides): the sidebar's Overlays section carries an on/off checkbox + opacity slider for the **left (main) pane**, the wiper's floating panel carries the same rows for the **right pane** — opacity is fully independent per pane. So ESRI can sit on both sides of the wiper with ASCENDING velocity left and DESCENDING right — each side configured on its own panel, no combined selector. Overlays are imagery-like and therefore **per-pane by design** — the pane-parity rule applies to landslide DATA only (which stays mirrored); this replaced the old mutually-exclusive susc checkboxes and the unconditional susc mirroring in `_swipeAddData`. Layers occupy the old susceptibility stack slot (above basemap/trace/pending, below faults + data); susc layer ids are unchanged so the trace-raster insertion chain still works.

**NRCan InSAR velocity overlay (2026-10-29).** Row `nrcan-asc` (Surface change, beside OPERA): NRCan's *Pilot national scale maps of active deformation processes in Canada* (Samsonov, CCMEO; Sentinel-1 2017–2024 snow-free, GAMMA + MSBAS v10, 40 m, long-wavelength signal filtered out; Open Government Licence – Canada, Open Canada record `1da588c1-0dc6-45e4-9e63-9acf2fdc353a`). Covers Canada **and parts of SE Alaska**. Tiles come straight from the GeoDatacube WMS (`datacube.services.geo.ca/ows/land-deformation`, layer `mosaic-asc`, EPSG:3857, CORS echoes the origin), ~1.5 s a tile, **pre-coloured** with NRCan's turbo-like ramp over ±0.12 m/yr — so it cannot share OPERA's ramp or feed the asc × desc histogram. `tools/nrcan_insar_color_vel.txt` is their legend written down for the key/export. Sign convention matches OPERA (negative = away from the satellite).

*Deferred plan — host it ourselves* (Hig wants this eventually, not yet). Why: the WMS has only ascending; descending, vertical and east exist only as COGs; pre-coloured tiles cannot be recoloured, sampled or histogrammed; and the service is a dependency we do not control. The data: STAC collection `land-deformation-mosaic` (`datacube.services.geo.ca/stac/api/collections/land-deformation-mosaic`), four COGs `active_deformation_processes_sentinel1_2017-2024_{asc,dsc,vertical,east}.tif` on public S3 (`datacube-prod-data-public.s3.ca-central-1.amazonaws.com/store/land/land-deformation/land-deformation-mosaic/`), each **~33 GB**, float m/yr, EPSG:3979, 40 m, 136,329 × 98,358 px, x −2,411,600 … 3,041,560. Per-tile COGs (`land-deformation-tile-asc`/`-dsc`) also exist and may be the cheaper way to fetch only the west. Steps: (1) window to SE Alaska + BC + Yukon by `/vsicurl` reads or the per-tile collections — estimate ~5–6 GB per component, hours over the Homer link, so run it overnight and **check boot-disk space first** (memory: no bulk stdout, swap fills the disk); (2) bake **value** PMTiles like the permafrost value archives (8-bit or terrain-RGB code of m/yr, a stated range, alpha = coverage) rather than coloured tiles; (3) push to R2 `overlays/` and serve through `/overlays/<id>.pmtiles`; (4) colour client-side with the OPERA ramp via an `operacolor`-style protocol so all InSAR rows share one scale, decide whether to keep ±30 mm/yr (fast slides saturate) or widen; (5) four rows (asc, desc, vertical, east), and the OPERA asc × desc histogram could then take NRCan as a source; (6) retire the WMS row, keep its `ov=` id readable (stored views name `nrcan-asc`); (7) Methods paragraph updated in the same change.

**OPERA velocity overlays.** ASF's displacement portal serves its OPERA DISP-S1 velocity mosaics as a public CloudFront pyramid of **VALUE tiles** (8-bit gray 1–255 ≙ ±30 mm/yr from their `extent.json` scale_range, alpha = coverage, maxZoom 12; the portal colors client-side — verified 2026-07-14). CloudFront CORS is locked to ASF's origin, so `inventory/opera.py` proxies raw tiles same-origin at `/inventory/tiles/opera/<asc|desc>/z/x/y.png` with a disk cache (`data/opera_tiles/`, upstream 404s cached as `.404` markers); the `operacolor` protocol in map.js canvas-decodes and applies **ASF's exact ramp** (extracted from their bundle) client-side — same-origin tiles make canvas reads legal, and later phases (view-scoped histograms, centroid sampling) read the same cached value tiles. When ASF refreshes the mosaic: `python manage.py purge_opera_tiles` + bump `OPERA_TILE_V` in map.js. Caveats (documented in Methods): 8-bit quantization ≈ 0.24 mm/yr steps, values clip at ±30 mm/yr (fast slides saturate) — visualization/exploration grade, not publication numbers; the upgrade path is DISP-S1 GeoTIFFs via Earthdata. Attribution: OPERA DISP-S1 © NASA/JPL; mosaic service ASF.

**Canadian basemaps (2026-10-29).** *US / Canada Topo* is the old `usgs-topo` entry (id kept: stored views and `/lidar/`'s default name it) with NRCan's **Toporama WMS** drawn over it through the descriptor's `labelTiles` slot (`labelAttr` carries its credit), **from z11 only** (`labelMinzoom`): below that Toporama draws a small-scale atlas whose land and sea fills cover Alaska too. From z11 it is transparent outside Canada and where it has no fill, and it sends CORS. *BC Orthophoto 1995-2004* (`bc-ortho`, Historical) is GeoBC's 1 m mosaic via **`inventory/bcortho.py`**, a same-origin proxy + disk cache (`data/bcortho_tiles/`) at `/inventory/tiles/bcortho/z/x/y.png`: their ERDAS WMS sends `access-control-allow-origin: (null)` so a browser cannot use it directly, and its nodata is opaque black, which the proxy turns transparent (brightest channel <= 4; very dark shadow can show as pinholes). Tiles outside the province's box are refused without an upstream call. Cold tiles hold a gunicorn worker ~0.5 s each, like the OPERA proxy.

**NOAA chart basemap (2026-11-05).** `noaa-chart` in `basemaps.js`: NOAA's Chart Display Service WMS (`gis.charttools.noaa.gov/.../MCS/NOAAChartDisplay/.../WMSServer`, ENC data in paper-chart symbology; `MCS/ENCOnline` is the same in ECDIS symbology), read directly — EPSG:3857, CORS echoes the origin, ~1 s a tile, no proxy. It rides the descriptor's `labelTiles` slot over Esri's ocean base, **from z7** (`labelMinzoom`): below that one tile draws every light and traffic lane in the Gulf. From z7 the chart is opaque over US waters and transparent elsewhere, so the ocean base shows outside coverage. **Layers 1–7 only**: 0 and 8–12 are display notes, data-quality triangles and overscale hatching. **The service draws depths in feet by default** (311 off Homer Spit is 95 m; land heights are metres either way); `DISPLAY_PARAMS` with `DisplayDepthUnits=1` switches soundings and depth contours to metres, percent-encoded in the tile URL. No other streamable source exists: the `MarineChart_Services/NOAACharts` tile cache is empty past z7 and the old raster-chart tile host is gone.

**Erosion rate overlays — built, evaluated, switched off (2026-11-05).** Hig's verdict after seeing both in dev: too little coverage to be useful. Nothing was deleted: `EROSION_ACTIVE = false` in `map.js` keeps the five rows out of `OVERLAYS` (and with them the *Erosion rates* category), the ramp keys stay in `views._RAMP_SPECS` like `dist-alert`'s, and the two data files are **not shipped** (not in git, not on R2). What was learned, so nobody re-derives it:

- **Herman et al. 2013** (Nature 504, 423–426, doi:10.1038/nature12877), global exhumation rates from a linear inversion (GLIDE) of ~18,000 thermochronometric ages. **The gridded result exists only as the paper's Supplementary Table, a 207-page PDF** (`…MOESM338_ESM.pdf` on static-content.springer.com): longitude, latitude, erosion rate (mm/yr), resolution, one block per interval. `tools/erosion/build_herman2013.py` reads it with `pdftotext -layout` (minus signs are a soft hyphen plus a non-breaking hyphen; five rows repeat verbatim in each block) into a GeoJSON of **4,116 grid nodes, 9,471 values** (2–0 Ma 4,000; 4–2 2,458; 6–4 1,679; 8–6 1,334; row count confirmed by a second extraction mode). Only nodes with resolution > 0.25 were published, at ~0.06–0.13° spacing around the dated samples, so it is point clusters, never a field. **Alaska: 316 nodes, all St. Elias–eastern Chugach** (58.3–61.4°N, 144.5–136.1°W), 0.15–1.26 mm/yr at 2–0 Ma, 119 nodes at 4–2 Ma, 11 at 6–4, none at 8–6. The age compilation is a separate spreadsheet (Extended Data Fig. 1 source data, 17,850 rows, ~414 in Alaska/Yukon); the GLIDE code and inputs are on Zenodo (doi:10.5281/zenodo.3992784), with no gridded output. Schildgen et al. 2018 (Nature 559) attribute the reported acceleration largely to spatial-correlation bias; Willett et al. 2021 (ESurf 9, 1153) is the authors' reply. No data licence (Nature supplementary material). Sources and the last built GeoJSON: `data/erosion_src/herman2013/`.
- **OCTOPUS v2.3** (Codilean et al. 2022, ESSD 14, 3695; CC BY 4.0; `octopusdata.org`, also https://ro.uow.edu.au/data/60), basin-averaged ¹⁰Be / ²⁶Al denudation rates with each sample's drainage basin. Open GeoServer **WFS** (`octopusdata.org/geoserver/wfs`, layers `be10-denude:crn_{int,aus,xxl,inprep}_{basins,outlets}`) and WMS (pre-styled, ~1.3 s a tile); **neither sends CORS**, so a browser needs a mirror or a proxy. Counts: global 5,376, Australian 297, large basins 261 (**concentrations only, every rate −9999.99**), UOW in-prep 157. 93 fields a basin; the rate is `EBE_MMKYR` ± `EBE_ERR` (mm/kyr, recalculated by OCTOPUS with CAIRN), `EBEGLA` is a glacier-corrected variant, −9999 / 'NA' are missing. Outlets carry only ids, so rates are joined by `OBSID1`. **Alaska + Yukon: 24 basins from two studies** — Matmon 2020 (Alaska Range, 17 basins, 196–3,934 mm/kyr where computed) and Bender 2018 (Yukon–Tanana, 7 small basins, 10–19 mm/kyr). `tools/erosion/build_octopus.py fetch` mirrors the two rate-bearing collections (250 MB of outlines, ~2 min, refuses a partial mirror); `tiles` packs them with GDAL's PMTiles writer (vector, z0–9, `basins` + `sites`, 20 MB, under a minute). **Sort the basins in Python, not with `ogr2ogr -sql ORDER BY`**: that held every outline in memory and added ~20 GB of swap in ten minutes beside the topobathy jobs. Mirror and last archive: `data/erosion_src/octopus/` (deliberately not `data/overlays/`, which the R2 sync pushes whole).
- **The display** put both on ONE log scale in mm/yr (`tools/erosion_color_rate.txt`, half-decade classes; `map.js` builds its `step` expression from `api/ramps/`): Herman as four rows, one per interval, so two can face each other across the wiper; OCTOPUS as basin fills with site dots. To bring it back: run the two builders, put `herman2013_rates.geojson` in `inventory/static/inventory/` and `octopus_crn.pmtiles` on R2 `overlays/`, set `EROSION_ACTIVE = true`, and write a Methods section from the bullets above (the one drafted in dev was never committed).
- **What stays live:** the **`features` overlay kind**, third in `OVERLAYS` after raster and `vector`: `ov.features.layers` (each a `suffix`, the paint properties the row's opacity drives, optionally `color: [paint prop, data prop, colour when missing]`), `lazyData` (a GeoJSON source filled on first show) and `popup(hits)`; `_ovEnsure` / `_ovApply` branch on it. One LSTools reference entry, `erosion` (priority 6), asks **the pane under the click** (`_swipe.map` right of the divider), since a right-pane-only overlay has no visible layer on the main map. Both worked in dev on both panes; with no such overlay registered they do nothing.

**Shaded relief overlays (2026-10-30).** Now in *Surface → DEM visualizations* in `_OV_CATS` (a category may carry `subs`; *Surface* holds *DEM statistics* and *DEM visualizations*, the latter with Contours): Hillshade and Preset (the lidar's `LIDAR_PRESETS`) of two DEMs nobody here hosts, shaded by the vendored maplibre-gl-demshade package (`/inventory/` uses it through `dem_shade_bridge.js`, not `dem_shade.js`). `mapterhorn` = the same spec `/lidar/` uses (512 px terrarium, maxzoom 13 + overzoom, sea 0 m → void); `arcticdem` = `DemShade.addImageServer` on PGC's public `di-pgc.img.arcgis.com/.../arcticdem_latest/ImageServer` (float32 TIFF exports, ~260 KB and ~0.5 s a tile, no renderingRule so **WGS84 ellipsoidal heights**; the old Esri `elevation2` ArcticDEM service is gone). Registered on first use in `_reliefSourceDef`. The rows carry `underlay: true`, which `_ovEnsure` places before the first non-basemap layer (`_ovUnderlayBefore`: under the basemap's labels, lidar, uploads and the other overlays). No zoom floor (Hig: zoomed out they show where there is data). `DemShade.loadTile` returns an `ImageBitmap`, not `{data}` — the shape to expect when probing tiles headless. **The ∿ profile and the Alt-click probe read ArcticDEM too** (2026-10-30): `LSDemRead.ARCTICDEM` is an `imageServer` source on the same PGC service, asked for `Height Orthometric` (EGM2008) rather than the raw ellipsoidal heights the shading uses (those sit ~14 m high at Barry Arm; orthometric reads ~1 m above Mapterhorn's NAVD88 in Anchorage and Palmer); its float32 TIFFs are decoded by `LSDemRead.decodeTiff` (uncompressed, strips or tiles). Asked only north of 50°N (`arcticCovers`), and dropped from the chart/popup when it returns no data. It reads the sea as ~0 m rather than a void.

## Radar coherence overlay (Sentinel-1, summer)

One statewide pyramid, `/tiles/coherence/summer/`, built by
`tools/fetch_coherence.py` + `tools/build_coherence_tiles.sh` from the
Sentinel-1 Global Coherence Dataset (Kellndorfer et al. 2022, CC BY 4.0, free
and unauthenticated on AWS Open Data). It lives in the **InSAR** overlay
category because its first job is to say where the OPERA velocity layer above
it can be believed.

**Why it ships**, per Hig after looking at it: low-coherence patches sit over
some very active landslides and it "highlights a few that are otherwise easy
to miss" — a second, independent way for an active slide to announce itself.
That, not the InSAR-validity reading, is what earned it a place.

**Winter was built and cut** (2026-09-10): it wins in the dry interior and
loses badly on the wet-snow coast, and on the map it added nothing summer did
not already show. The route regex and both build scripts still accept any
season, so re-baking one is a command, not a code change.

- Source tiles are 1x1 degree, uint8 **PERCENT** coherence (not 0-255),
  nodata 0, ~93 m, EPSG:4326. **The tile name is the UPPER-LEFT corner** —
  N61W150 spans lat 60..61. They are **not true COGs** (no overviews, 6-row
  strips), hence the local bake. Alaska is 666 tiles, ~450 MB a season for
  COH12; `tau`/`AMP` are ~1.9 GB, so check before reaching for those.
- **The ramp is banded, and not only for readability.** Coherence is
  speckle-noisy at 93 m, so a continuous ramp gives every neighbouring pixel
  a different colour and PNG cannot compress it: the first cut was **902 MB**
  for Alaska (78 kB/tile, against 33-43 kB for every other pyramid here).
  Banding took it to **270 MB**. Bands are written as entry PAIRS so gdaldem
  interpolation yields hard edges without `-nearest_color_entry`; verified
  100% of opaque pixels land exactly on a band colour.
- Transparent below 20%, because Alaska in summer is mostly below that
  (median 16%) and an opaque floor would blanket the state in meaningless
  colour.
- `PROCESSES=1` — gdal2tiles' multiprocessing path dies under QGIS's bundled
  Python (`module '__main__' has no attribute '__spec__'`), same as the other
  build scripts here. The warp-reuse cache under `KEEP_TMP` is keyed on
  season/pol/var; a season-agnostic key would let a winter run silently reuse
  the summer warp.

**How to read it — measured precision vs. real roughness.** These are not the
same thing, and conflating them is the easy mistake:

- **Precision is high.** Over genuinely uniform open water (Cook Inlet, true
  coherence ~0) the product reads median 3 %, **sd 1.0 %**, max 7 %. The
  seasonal median has averaged the per-pair estimator noise right down.
- **The field is genuinely rough.** Pixel-to-pixel scatter on land in coherent
  terrain is ~6 points (5x5 detrended). Since precision is ~1 point, most of
  that 6 is REAL ground variability at 93 m, not measurement noise.
- **So a gap is believable at patch scale, not pixel scale.** A single 93 m
  pixel can sit a whole 10 % band either side of its neighbours. Averaging
  3x3 (~280 m) drops the sd of the mean to ~2 points, which is well inside
  one band. The landslide cases work because they are big: a patch at 20 %
  inside terrain at 50 % is a 30-point drop — unambiguous at any scale.
  Distinguishing 25 % from 35 % in one pixel is not.
- **Read it relative to surroundings, never against an absolute threshold** —
  the baseline swings from 4 % (water) to 50 % (Talkeetna) by land cover.
- **The shipped `rmse` band is NOT the precision of COH12.** It is the
  residual of the exponential-decay fit across COH12/24/36/48 (median ~10
  coherence points here, and *larger* where coherence is high). A big residual
  means coherence does not decay exponentially at that pixel, which is its own
  signal, not an error bar on the value you are looking at.
- **The biggest interpretive limit is temporal, and nothing ships to fix it.**
  This is a seasonal MEDIAN: a pixel at 20 % might be steadily 20 %, or 50 %
  for half the season and 0 % for the other half. There is no temporal-variance
  band. So a low patch says *something decorrelates here*, never *when*. And
  low coherence has several causes — water, wet snow, dense vegetation, and
  motion — which this product alone cannot separate. What makes the landslide
  cases legible is context: a low patch with a slide-like shape inside ground
  that is otherwise coherent.

**What the data actually says**, measured rather than assumed — the intuition
"bare rock high, vegetation low" is wrong for Alaska. Median coherence over
~1.5 km boxes, summer / winter:

| Talkeetna Mtns 50 / 44 | Anchorage urban 46 / 18 | Kenai lowland 43 / 18 |
|---|---|---|
| **Koyukuk tundra 7 / 20** | **Chugach nr Valdez 8 / 4** | **Columbia Glacier 5 / 4** |
| open water 4 / 4 | | |

Steep snowy coastal alpine ground is the worst case in *both* seasons, and
the dataset's own layover/shadow mask does not single those sites out, so it
is snow and ice rather than radar geometry. Winter is not a uniform
improvement either: it wins in the dry interior and loses badly on the
wet-snow coast. Consequence worth stating plainly: this layer does **not**
give a route to talus activity — 93 m is too coarse for it, and the terrain is
radar-dark besides. Its value is the active-landslide flagging above.

## OPERA DIST — surface disturbance overlay

One overlay on the inventory map, **Surface disturbance — annual**
(`dist-ann`), backed by NASA GIBS's ready-coloured Web-Mercator tiles for
`OPERA_L3_DIST-ANN-HLS_Color_Index`. `inventory/dist.py` proxies and disk-caches
them (`data/dist_tiles/<layer>/<date>/<z>/<x>/<y>.png`, `.404` markers,
`purge_dist_tiles`); `map.js` recolours them through the `distcolor://`
protocol. Routes: `tiles/dist/<alert|ann>/<date>/<z>/<x>/<y>.png`,
`api/dist_dates/` (GIBS time domain), `api/dist_coverage/` (below).

- **GIBS axis order is `z/y/x`**, unlike every other tile route here. The swap
  happens once, in `_upstream`. Verified by md5 against the upstream URL.
- **GIBS paints class 0 "no disturbance" opaque #121212** — fine for a
  Worldview backdrop, fatal for an overlay (~97% of a tile). `_distLut` holds
  the PAINTED classes 1-8 only, so class 0 *and anything unrecognised* fall
  through to transparent. Matching class 0 by colour instead would mean one
  missed match paints near-black over the whole map.
- **Colours are ours, not GIBS's** — `tools/dist_color_status.txt` is the
  source of truth and also feeds the colour key and export legend via
  `api/ramps/`. Darkness = importance (certainty x recency), so *confirmed* is
  darkest and *finished* steps back; magnitude rides the same channel. The red
  is hue 353, not 0: the trace of blue is what keeps the freshest class
  separable under red-green CVD.
- **`api/dist_coverage/`** resolves "latest" against the viewport rather than
  the calendar, because the daily product publishes only that day's granules.
  Probes at a low zoom, crops to the viewport in tile-pixel space, walks back
  at most 20 dates. The zoom floor is 0 so the 6-tile budget is always
  satisfiable — with a floor of 3 an all-Alaska view fell through to 64 tiles.
- **Every overlay row has a collapsible colour key** (`_rampLegendEl`), fed by
  the same `api/ramps/` the PNG export uses.
- **`date=all`** (annual only) serves a MERGED composite of every year — the
  cumulative disturbance footprint, which is the view a landslide inventory
  usually wants. It merges in **palette-index space**: GIBS sends 8-bit
  colormap PNGs whose index IS the class code and all years share one palette,
  so the merge is a per-pixel index pick and the output is byte-compatible
  with a single-year tile (the client needs no special case). Which class wins
  is the ramp's own darkest-first importance order, not a second ranking —
  `tools/check_dist_palette.py` asserts that, and that map.js agrees with the
  ramp file class by class. The three year-fetches run concurrently, so a cold
  merged tile costs 1.2x a single year rather than 3x; warm it is identical
  (~6 ms), and annual composites never change so the cache is permanent. Cache
  dir is `ann/_all_<first>_<last>_<n>/`, and the client folds the year set into
  the `?v=` token, so a new annual release lands in a fresh cache rather than
  staling the old one.

### The daily layer is retired (2026-09-10) — `DIST_ALERT_ACTIVE = false`

Built, evaluated against real events, switched off; nothing was deleted, and
one line in `map.js` brings it back. Why, so nobody re-litigates it blind:

- **~Half of Alaska is unobserved on any given date** (50.5% data / 20.1%
  no-data / 29.4% no tile, Talkeetna box, 2026-09-08). An empty view reads as
  "no disturbance here".
- **It does not resolve progressive change**, which was the reason to want
  daily. The one unambiguous event tested — a Nepal landslide at
  28.288/85.518 — was a single step: 1.62 km2 of >=50% vegetation loss, every
  pixel dated 2026-08-26, next-largest date 76 px.
- The Alaska daily signal was dominated by **seasonal snow** (86% of generic
  detections at Portage fall in October) and by the alert layer's own **annual
  reset**, which makes disturbance appear to vanish each year.

### Limits that bite, and are not obvious

- **VEG only.** GIBS publishes no `GEN-*` (generic / non-vegetated) layer on
  either projection endpoint, so the overlay is blind above treeline — bare
  rock, talus, fresh debris. `GEN-*` exists only in the LP DAAC COGs; read it
  with `tools/imagery/dist_probe.py`.
- **Permanent snow and ice are masked outright.** Never-observed fraction,
  stacking a season of granules: Columbia Glacier 32.4%, Portage 21.4%,
  Kachemak 7.8%, non-glaciated Talkeetna 0.0%. Not water (only 2% of
  DATA-MASK water goes unobserved). The mask is a fixed footprint, so ground
  newly exposed by glacier retreat — the most interesting ground on a
  retreating tidewater glacier — stays masked. A supraglacial landslide
  (Iliamna, Aug 2026) is invisible.
- **Nov-Jan blackout** at Alaska latitudes: 3 DIST-ALERT granules in all of
  December 2025.
- `DATA-MASK` is the CURRENT acquisition; `*-DIST-STATUS` is carried-forward
  state and stays populated through cloud. **Rank granules by clear-land
  fraction, never by date.**
- `VEG-ANOM-MAX` is uint8 **percent**; every `GEN-*` metric and both
  `*-DIST-CONF` are int16 **unitless 0-32000**. Not comparable.
- `*-DIST-DATE` is days since **2020-12-31**; value 0 means *never disturbed*.

Research tools, kept but not shipped: `tools/imagery/dist_probe.py` (read any
DIST layer from the COGs for a point, VEG vs GEN side by side) and
`tools/imagery/dist_animate.py` (animate from GIBS tiles; accumulates by
default, because raw daily playback strobes on the coverage gaps).

## Susceptibility

**Susceptibility tiles** (self-hosted): USGS Slope-Relief Threshold models (Belair et al. 2024, 90 m). **Alaska coverage** — this is why they're self-hosted rather than the old `tiles.arcgis.com/.../US_Landslide_Susceptibility` MapServer, which is conterminous-US only.

How it works (pre-colored tiles):
- `tools/build_susc_tiles.sh` reprojects each AK GeoTIFF (EPSG:3338, Int32 0–81 = count of susceptible 10 m sub-cells per 90 m cell, NoData = 2147483647) to EPSG:3857, clips to an Alaska window (the Aleutians straddle the antimeridian; an unclipped warp yields a globe-width canvas), bakes color with `gdaldem color-relief`, tiles z3–10 (XYZ), and prunes fully-transparent ocean tiles. Output: `data/susc_tiles/{lw,n10}/{z}/{x}/{y}.png` (gitignored, never in the image).
- **Color scheme = discrete frequency-ratio classes** derived from sampling the models at the 314 catastrophic source centroids (see *Susceptibility analysis* below). Per-model break files: `tools/susc_color_n10.txt` (breaks `0 / 1–62 / 63–80 / 81`) and `tools/susc_color_lw.txt` (`0 / 1–74 / 75–80 / 81`) → Low / High / Very High (value 0 transparent). The script picks `tools/susc_color_<model>.txt` if present, else the shared `tools/susc_color.txt`. **To recolor / re-break: edit those files and re-run the script** (cost = re-tiling, a few minutes).
- **Why pre-colored, not recolored client-side:** MapLibre GL JS (5.5) does **not** implement Mapbox's `raster-color`/`raster-value` paint properties — confirmed absent from the 5.5.0 bundle — so a single-band value raster can't be colorized in the browser without a custom WebGL layer. `map.js` renders the tiles as a plain raster layer (`raster-opacity: 1`; per-pixel alpha is in the tiles).
- Served by the `susc_tile` view in `landslidescience/urls.py` at `/tiles/susc/<model>/{z}/{x}/{y}.png` with an immutable cache header. `data/` is volume-mounted (`docker-compose.yml`) in both dev and prod, so one route serves both. The build script runs locally (the web container has no GDAL); tiles ship to the droplet via a **separate** rsync (the main deploy rsync excludes `data/`).
- **Cache-busting:** tile URLs are not content-hashed but carry a 1-year immutable header, so `map.js`'s `SUSC_TILE_V` token is appended (`?v=N`) and **must be bumped whenever the tiles are rebuilt with different pixels** (recolor/reclass), or clients keep the stale image. Currently `v2` (discrete classes; v1 was the continuous YlOrRd ramp).
- Snapshots: tiles are not bundled (too big); `susTileBase` stays `/tiles/susc/`, so a toggled overlay is simply inert in an offline bundle.

**DGGS susceptibility (third model, 20 m).** Alaska DGGS *Susceptibility to deep-seated landslides in Alaska* (PIR 2025-3, Wikstrom Jones & Larsen, doi:10.14509/31691) — rock strength (3 classes, from the 1:1,584,000 state geologic map) crossed with slope class (8 bands, from the 5 m IFSAR DTM), giving susceptibility classes 0/3/5/6/7/8/9/10 plus a lake-river-glacier class.

DGGS publishes it **only as a dynamic ArcGIS MapServer** — no download, no tile cache, no image service; the report itself is a PDF and a map sheet, and the whole portal was searched. That service is also unreliable (504 for half an hour on 2026-09-19, flapping after). So the raster is **recovered** by `tools/harvest_dggs_susc.py`: ~1,125 requests of 4096 px at 20 m in EPSG:3338, one output pixel per source cell, rendered symbology decoded back to integer classes. Three measurements make that exact and are recorded in the tool — the source is a 20 m grid on a 20 m phase (100% of colour runs even-length *and* even-start at 10 m/px), the nine legend colours come back exact with no antialiasing, and the 4096 px service cap makes the state 1,125 requests rather than a million tiles. Licence permits reuse with attribution and requires describing modifications; both are stamped into every chunk's GeoTIFF tags.

- Tiles: `tools/build_dggs_susc_tiles.sh` → `data/susc_tiles/dggs/`, served by the same `susc_tile` view (`/tiles/susc/dggs/…`), cache-busted by `DGGS_SUSC_V` in map.js. Colour ramp `tools/dggs_susc_color.txt` is the USGS YlOrRd ramp sampled at `class/10`, so the three layers read on one scale; class 0 transparent like theirs.
- **The tiling bound is derived, never written down** (`tools/dggs_susc_bounds.py`). A raster's reported `wgs84Extent` comes from its four **corners**, and under Alaska Albers a corner is not the extreme latitude — the top edge bows north of both its ends. Hardcoding from the corners cut the tiles at **67.65 N** while the data reached **71.53 N**, silently losing the Brooks Range and the North Slope. The tool samples all four edges.
- The raster crosses the **antimeridian** (data to 179.9 E), so a single warp to Web Mercator spans the globe and produces non-square pixels. The script warps the two sides separately; XYZ tiles merge with no seam.
- `inventory/susc_dggs.py` is a live proxy of the same service, kept as a fallback (and as the record of why proxying was needed). The overlay reads the baked tiles.

**NASA global susceptibility (fourth model, ~1 km; 2026-11-05).** Row `susc-nasa`: Stanley & Kirschbaum (2017, doi:10.1007/s11069-017-2757-y) in the February 2023 update, the only file NASA still distributes (`gpm.nasa.gov/sites/default/files/downloads/global-landslide-susceptibility-map-2-27-23.tif`, 33 MB; their map server `maps.nccs.nasa.gov` no longer resolves). 30 arc-seconds, 72°N–60°S, Int8: 0 = sea (transparent), 1–5 very low … very high. `tools/build_nasa_susc.py` downloads it to `data/susc_src/nasa/`, colours exact classes from `tools/nasa_susc_color.txt` (YlOrRd like the other three), tiles z0–8 globally (nearest at z8, averaged below) and writes **`data/overlays/susc_nasa.pmtiles`** (19,393 tiles, 307 MB) — PMTiles through `/overlays/`, not a tree under `data/susc_tiles/`, because of the droplet's disk. Checked at 412 points against the source (4 differ, all at cell edges). **An analysis axis since 2026-11-07** (`nasa` in `quantities.py`: ordinal classes 1–5, nodata 0, warped nearest onto the lattice by the cube builder; 196 of 197 random lattice cells agree with a direct read, 17 of 17 landslide centroids agree with `gdallocationinfo`; reviewed-area km² by class 1→5: 510k / 407k / 319k / 188k / 71k). No slider.

**Susceptibility value sliders.** Each landslide's sampled n10/lw value (0–81) comes from `inventory/static/inventory/ls_quantities.json` (every axis-capable quantity at every centroid — see *The analysis panel*), produced offline by `tools/analysis/sample_quantities.py` because its source rasters (17.7 GB) are not on the droplet (the container has rasterio; the sampling itself takes seconds); committed, so it goes stale when landslides are added/moved (`--centroids` refreshes the centroid file from the **production** database first, `--from dev` from dev — until 2026-10-31 it read dev, whose ids above 1489 are different records, so eight prod landslides carried dev test records' values) — the analysis panel's note counts the landslides missing from it and names the command, so staleness is visible rather than silent. `map.js` merges `lw`/`n10`/`dggs` into the point features and the two dual-handle sliders in the Inventory filter panel filter on them (reusing `_setupDual`/`addRangeFilter`), with a live "N of 1424 in range" readout. Key finding from the analysis: catastrophic source values **saturate at the 81 ceiling** (median 81; ~53% n10 / ~68% lw at max), both models score AUC ~0.96–0.97 (lw marginally higher), so the predictive signal is a sharp low-vs-high contrast rather than a graded ramp — which is why landslide-quintile bands collapse and FR-based class breaks are used instead.

Task #69 (self-hosted recolorable susceptibility) — **done**. Follow-on: sample lw/n10 values at each landslide centroid and expose susceptibility-based filtering — **done** (sliders + scatter above).
