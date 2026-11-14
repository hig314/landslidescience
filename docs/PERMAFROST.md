# Permafrost overlays and Surface properties

Gruber / Obu / Pastick, the 60 m downscale, fixed-scale slopes, contours.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## Permafrost overlays — three products and a terrain downscale

Ten overlays across the reorganised categories (`_OV_CATS` in `map.js`,
order per Hig 2026-10-09: **Climate · Surface change · Landslide
susceptibility · Glaciers · Subsurface**): Gruber 2012 MAAT + PZI (1 km),
Obu 2019 MAGT + probability (1 km), each as published; our 60 m terrain
downscale of all four; Pastick 2015 near-surface probability (30 m, as
published); and the **Glacier covered area 2020** outline (NSIDC G10040,
Roberts-Pierel & others 2022, Landsat, RGI ids carried) in the Glaciers
category. Built and **deployed 2026-10-09** (archives on R2, grids on the
droplet). Methods §1
*Permafrost overlays* is the reader-facing account; this is the plumbing.

- **Three quantities, three hues, never one ramp.** PZI is a fraction of
  the cell expected to hold permafrost, Obu's probability is the ensemble
  share with MAGT < 0 °C, Pastick's is the probability of permafrost in the
  top metre. MAAT and MAGT share one diverging ramp on purpose (they sit
  together in Climate). Ramps in `tools/permafrost/*_color_*.txt`,
  registered in `views._RAMP_SPECS` as `permafrost/<file>`.
- **Baked PMTiles, served like the lidar pyramids.** `/overlays/<id>.pmtiles`
  (`lidar_serve.overlay_pmtiles`, ranged, CORS, `[a-z0-9_]` ids) serves a
  local copy from `data/overlays/` where mounted (dev), else 302s to R2
  `lidar.landslidescience.org/overlays/`. `robots.txt` disallows
  `/overlays/`. 1 km products to z9 (PNG), 60 m to z11 and Pastick to z12
  (**lossy WebP, `BAKE_FMT=webp`** — Pastick as PNG was 10 GB, as WebP 2.8;
  the 60 m layers ~200 MB each). Pushed with
  `tools/permafrost/r2_sync_overlays.sh` (bucket prefix `overlays/`, 7.2 GB
  on 2026-10-09 including the value archives and Surface properties).
- **Pipeline, `tools/permafrost/`**, sources on Nunatak (`permafrost_src/`,
  incl. `glaciers/`); the working grids `permafrost_build/` are **only on
  R2** since 2026-10-31 (lidar bucket, `permafrost_build/`, public,
  `/vsicurl`-readable at `https://lidar.landslidescience.org/permafrost_build/…`;
  the Nunatak copy was deleted after a 0-difference `rclone check`, so a
  rebuild first pulls it back with rclone or sets `PF_BUILD`). EPSG:3338,
  `-te -1041000 443000 1665000 2622000`): `fetch_mapterhorn*.sh` →
  `mapterhorn_mosaic.py` → `terrain60.sh` (elev/slope/aspect/northness at
  60 m + 1 km means) → **`kernel_fit.py`** → **`apply_downscale.py`** →
  `rastercalc.py pastick` + 60 m average → `validate_pastick.py` →
  **`patch.py --lon --lat [--radius 10]`** (the site-by-site check: 1 km
  cells vs the 60 m cloud and the fitted lapse/curve, CSV + PNG into
  `permafrost_build/patches/`) → `bake_overlay.py`. The glacier outline is
  `gdal_rasterize` fill=1 + boundary=2 at 30 m → the same bake.
- **The method (replaced the 10 km block fits the same day, at Hig's
  request).** `kernel_fit.py` regresses the product on elevation (Obu: +
  northness) at EVERY 1 km cell with Gaussian weights (σ 15 km) by
  convolving the weighted moments (`scipy.ndimage.gaussian_filter` —
  **it normalises its kernel**, so the summed weight is G(w)·2πσ², not
  G(w)); too little relief under the kernel (sd < 75 m) → the σ 50 km fit,
  else the domain median; coefficients clipped to physical ranges. The
  60 m field is **anchored on the published 1 km value**: T60 = T1k +
  b(e60 − e1k) [+ c(n60 − n1k)], all 1 km fields bilinear to 60 m, so the
  product's own numbers are preserved. Land mask = Gruber MAAT validity
  (defined over every glacier). **Obu is inpainted**, not masked: the
  kernel's own fitted value stands in for MAGT/STD where Obu has none
  (118 k cells, 5 % of land), and the 60 m layers are written over ice —
  the glacier outline makes that honest. **PZI(MAAT)** is Gruber's own
  normal CDF (T₀ −4.75, σ 2.55 reproduce his values to 0.002 rms) rescaled
  to reach 0 at 0 °C and refitted to his ≥ 0.1 cells (T₀ −4.58, σ 2.67;
  `kfit/pzi_curve.json`) — his published index floors at 0.01 below 0.1,
  which put a step in every elevation profile.
- **Surface properties** (2026-10-09/10, Hig: the axes most analyses want,
  so they must exist as overlays): `sp-contours`, `sp-elev`, `sp-slope`,
  `sp-aspect`, `sp-north`; own category after Climate.
  - **The sea mask is the 60 m grid's own** (`rastercalc.py maskzero`:
    Mapterhorn is exactly 0 over sea, land near sea level is not; 342 M
    zero cells vs 227 k in (0, 5 cm)). The first bake used Gruber's 1 km
    validity and the coast came out in 1 km blocks, wrong both ways.
  - **Slope at fixed horizontal scales** (`tools/terrain/`, Hig 2026-11-01;
    replaced `bake_slope_pyramid.py`, whose per-zoom slope tied the scale to
    the pyramid). Six rows on one ramp: *local* (ordinary Horn slope on the
    ~9 m z12 grid) and 60 m, 200 m, 500 m, 1 km, 2 km, each the **steepest
    diameter of a circle of that diameter centred on the pixel** (24
    directions, bilinear ends; `chord_slope.py`, whose `selftest` checks a
    plane reads its own slope). Scales are computed on grids of about L/12
    (z12, z12, z11, z10, z9, z8 in 512 px tiles) from Mapterhorn tiles fetched
    by `fetch_mapterhorn.sh` (coverage polygon + 3 km), kept on Nunatak. Going
    down the pyramid averages the **coloured picture** (premultiplied RGBA,
    2x2), never the slope: a scale's meaning stays constant at every zoom.
    `bake_slope.py` masks sea (height <= 0 or exactly flat in an 11 px window:
    some source cells carry a constant -3 m or +2.5 m) and coverage only for
    colouring, not in the calculation, so a coastal cliff keeps its height
    difference. Coverage is `data/regions_seed/alaska_3338.geojson`, to grow
    with the reviewed area. Blocks are z8, fail independently, resume from
    `done_blocks.txt`. **Prebaked to z11**, `sp_slope_<sid>.pmtiles`, 17 GB in
    all (local 5.7, 60 m 4.2, 200 m 2.8, 500 m 1.9, 1 km 1.3, 2 km 1.0), ~3.5 h
    on 6 workers. **local and 60 m run at z12 in the browser**
    (`slope_scales.js`, `lsslope://`, same Mapterhorn tiles, a worker pool);
    the z11 parent's alpha is the mask, so the client needs no polygon and
    open sea or Canada costs no Mapterhorn request. Check after changing either
    calculation: `LSSlope._compare(sid, x11, y11)` must match the baked tile
    (2026-11-01: alpha rms 0.5 of 255; differences only at the mask edge, where
    half-resolution masking leaves a fringe of at most one z12 pixel). **In
    dev the archives stay on Nunatak**: the local override line mounts
    `/Volumes/Nunatak/terrain_build/pmtiles` at `/app/nunatak_overlays` (not
    committed, like the COG line) and `data/overlays/sp_slope_*.pmtiles` are
    symlinks to it, because Docker refuses a file mount inside the `data/`
    bind. Push with `OVERLAYS_SRC=/Volumes/Nunatak/terrain_build/pmtiles
    tools/permafrost/r2_sync_overlays.sh`. Row id `sp-slope` is the 60 m row,
    kept so stored views still work. The ramp is the lidar viewer's slope
    standard (`dem_shade.js` `SLOPE_STOPS`: white to 32 deg, yellow 40, red 45,
    purple 50), nearly clear below 32 deg. The new scales are not yet analysis
    axes or brush sources.
  - **Contours are generated in the browser** by maplibre-contour (unpkg,
    MIT) from Mapterhorn's terrarium tiles — nothing baked or hosted — with
    the interval by zoom (1000 m at z≤6 … 10 m at z≥14) in
    `_contourSourceDef`. The first **vector overlay**: `_ovEnsure`/`_ovApply`
    take an `ov.vector` entry (line layer + label layer where the style has
    glyphs, which the wiper's second map lacks), opacity → line-/text-opacity.
  - Aspect's flat cells are 0 = north in the baked LAYER (gdaldem
    `-zero_for_flat`), said in the row text and Methods; northness is the
    layer for slope direction. In the analysis CUBE and the landslide
    values they are masked (NaN where slope == 0; `build_quantity_cube.py`,
    `sample_quantities.py`, `ls_sample.js terrain()`) after Hig saw the
    3× spike at north (2026-10-10). The 45°-multiple spikes below 1° of
    slope remain — HAZARDS §Raster sources.
- **The permafrost patch detail (`permafrost.js`).** Until 2026-10-10 this
  file also held the Obu × Gruber density panel, which became the first
  preset of the general analysis panel (see *The analysis panel* below).
  What remains is Panel B: with permafrost products on both axes and a
  sample circle set, the panel's **Permafrost detail** button draws
  `patch.py`'s four plots in the browser (CSV + PNG) from the published
  1 km cells (`api/permafrost/patch/`, `inventory/permafrost.py`,
  memory-mapped `.npy` grids in `data/permafrost/`, 11 × 22.5 MB, written
  by `export_web.py --grids` and **rsynced to the droplet with the rest of
  `data/`**) and the 60 m downscale read through `LSSample.fine()` from the
  **value PMTiles** `/overlays/pfv_<field>.pmtiles` (`bake_values.py`:
  terrain-RGB of value × scale at z10 only, scale table in `ls_sample.js`
  `FINE`; precision is plot precision — 1 m, 0.1 °C, 0.001 — because a
  finer low byte is noise PNG cannot compress).
- **Source traps** in HAZARDS.md §Raster sources (Obu NaN inshore, Pastick
  codes 101–105, Gruber `.hdr` cellsize, Homebrew `gdal_calc.py`).
- **Licences / attribution**: Gruber 2012 (cite the paper), Obu 2019
  (PANGAEA, CC BY 4.0), Pastick 2015 (USGS, public domain), G10040 (NSIDC,
  cite); `PF_ATTR` / `GLAC_ATTR` in map.js ride the sources. Downscaled
  layers are ours and say so in their row text ("our inference").
