# The dataset stack for the full-area (Kachemak Bay) point-first run — lined up 2026-10-29

Hig's decisions (2026-10-29): run the full area next; Katie Hughes' multibeam may be **ungated**; the **Chugachmiut
lidar** may go into the merged product with its raw data kept gated, as a pointilised DTM (no cloud exists); the
review workflow stays as the mock-up for now. Every fact below was read from disk or the recipes today, not from
memory; "assumed" marks a number nobody has measured.

## 1. Extent

The whole-bay layout of v7 (`composites/kbay_2023/boxes.json`) is x 557–635, y 6566–6636 km (UTM 5N, 25 boxes of
~15.5 × 13.5 km cropped, 2 km margins, 5,460 km²). The Chugachmiut mosaic runs south to y 6560 (Nanwalek / Port
Graham), so the southern edge should move to **y 6558** (one more row of ~8 km, or taller southern boxes). CUDEM
and NCEI reach west to x 550–556; the bay's mouth is covered either way. **Decision for Hig:** 557–635 × 6558–6636.

## 2. The stack, by the stage each source enters

The chain has three places a source can enter, and the place fixes what is assumed about it:

- **A. Observations (`topobathy/joint.py`)** — the point-first stage. Every source becomes per-cell observations
  with a class (ground / water / bed / ice), a count, a date and a sigma, then the fuse decides cell by cell
  (cross-check against the others' own surfaces, the epoch/pool rule, over-water tests, derived-source
  calibration, the vintage map) and writes ONE joint layer with a class raster and sigma. A **lidar** source enters
  from its returns (`joint_pilot.py` units: 1 m grids of ground count/median/min, water count/level, max return,
  per survey); a **grid** source enters as one observation per cell with sigma = hypot(σ_source, slope·D/√12,
  σ_xy·slope) — "what gridding lost" at its own resolution D; a **points** source as its rows with their own sigma.
- **B. Stack layers (`composite.py` / `axes.py`)** — rasters folded in order with class rules, for what cannot be
  observations: the 3DEP prior with the fallback rule (footprints, dry voids), the fused joint layer itself, and the
  fill's modelled surface. (In the Grewingk recipe four vendor DTMs still sit here as legacy layers; for the bay they
  move to A, see §3.)
- **C. Water and fill inputs** — not elevation sources: mapped and reviewed polygons, flat-water detections on
  bare-earth models, soundings with bounds, water stamps, tide datums, dispositions.

### A. Observation sources

| source | kind → class | res / date | datum, shift | σ (m) | where it is | state |
|---|---|---|---|---|---|---|
| **KBay 2023** (NOAA OCM 10418) | lidar → ground, water; **reference** survey for alignment | 28.4 B pts, COPC; 2023-08 | NAVD88 GEOID12B | 0.05 | Powder `lidar_src/kbay_2023_laz/tiles` (2,298 tiles, whole bay) | in the joint stage; ground = points within 0.25 m of reclass v2 |
| **Grewingk 2021** (DGGS) | lidar → ground, water | 261 LAZ; 2021-10-12 | NAVD88, **+0.36 m** per point | 0.08 | `kbay_reclass_test/grewingk_2021/laz` | in |
| **Grewingk 2019** (DGGS) | lidar → ground, water | 35 LAZ, 427 M pts; 2019-06-03 | NAVD88 GEOID12B (no CRS in headers) | 0.08 | Powder `lidar_src/grewingk_2019` | in |
| **Homer 2018 NCMP** (OCM 8686) | lidar → ground, water, **bed** (green lidar) | 89 COPC, 2.5 GB; 2018-06 | NAVD88 | 0.08 ground; bed assumed 0.3 | Nunatak `lidar_src/homer_2018_ncmp` | **to add** (today a DTM layer in B with facets / flat_below / islets / flat_lakes) |
| **Seldovia 2019 NCMP** (OCM 8837) | lidar → ground, water, bed | 31 COPC, 1.3 GB; 2019-06-03 | OCM copy is **GEOID12B** (+0.13 m vs KBay, NMAD 0.30); the vendor grid is xGEOID17B, 1.42 m low — use the points, not the grid | 0.08 / 0.3 | Nunatak `lidar_src/seldovia_2019_ncmp` | **to add** (today a DTM layer in B) |
| **Kenai 2008** (USGS) | lidar → ground; water from no-return holes (USGS classes carry no water class) | 3,130 LAZ 2 km tiles, PTD-reclassified, 67 GB; raw EPT 73 GB; 2008-06 | NAVD88 | 0.15 assumed (older sensor; crest loss known) | Powder `lidar_build/kenai_2008/classified`; Nunatak `lidar_src/kenai_2008/ept` | **to add** as the oldest lidar; NOAA's copy (class 9 water) not checked |
| **Homer 2019** (DGGS RDF 2021-2) | lidar → ground, water | 85 complete tiles; 72 unfinalised (DGGS re-export needed); 2019 | NAVD88 | 0.08 | Nunatak `lidar_build/homer_2019_pc` | **to add** where tiles are complete; elsewhere the DTM (B) or nothing |
| **Chugachmiut lidar** (BIA) | **grid → ground, pointilised**: one observation per 1 m cell; TIN facets (`facets` test, 2 cm over 10 m) → void or σ 1.0; hydro-flattened water (`flat_water.py`) → water at its level, never ground | 1 m; file date 2018-12, flight date **unknown** | **vertical datum undeclared** — calibrate on bare ground against KBay 2023 and Kenai 2008 where they overlap (x 559–592, y 6560–6589) | 0.3 after calibration, assumed; larger where the overlap residual says so | Powder `Alaska GIS/Raster/Chugachmiut_lidar_GeoTiff_Mosaic/be_mosaic.tif`; raw tiles on the unmounted St Augustine drive | **to add**; raw stays gated |
| **Grewingk sonar 2023** (Hughes, pass-levelled) | grid → bed | 1 m; 2023-05-18 | NAVD88 GEOID12B, +0.36 | 0.3 | `grewingk_sonar_level/levelled_1m.tif` | in; **to be ungated** on publish |
| **Kachemak 2008–09 multibeam** (Hydropalooza, H12089/90/114 merged) | grid → bed | 1 m; 2009 | MLLW, **−1.38** (the collection's measured value, not the gauge's −1.553) | 0.5 | `bathy_src/kachemak_2008_2009_mllw_nad83_clean.vrt` | in |
| **H12146 BAG** | grid → bed | 1 m; 2010 | MLLW −1.38 (0.00 ± 0.003 vs the above) | 0.5 | `bathy_src/outer_kachemak/grids/H12146_mllw.tif` | in (+9 km²) |
| **Pedersen 2025 multibeam** | grid → bed | 0.5 m; 2025 | NAVD88 | 0.3 | `bathy_src/pedersen_2025.tif` | **outside** the extent (x 678–683) — not used |
| **NOAA OCM 2023 seamless** 2 m | grid → bed, **derived** (satellite-derived bathymetry + compilation), valid **below −1.38 only** | 2 m; dated 2019 | NAVD88 (−0.02 vs Homer 2018; +0.37 below 20 m) | 1.0 | `outer_kachemak/cmp_ocm/cand_2m.tif` | in; calibrated to measured beds it overlaps (`derived_adjust_m` 150) |
| **CUDEM 1/9″** n59x75_w152x00 | grid → bed, derived, valid below −1.38 | 3 m (4 m warp); 2022 | NAVD88 (−0.19 vs Hydropalooza) | 1.0 assumed | `outer_kachemak/cmp_cudem/cand_4m.tif` (+303 km², Cook Inlet off Anchor Point) | **to add** |
| **NCEI Kachemak 1/3″** 2010 | grid → bed, derived, valid below −1.38 | 10 m; 2010 | MHW, **+3.91 measured** | 3.0 | `outer_kachemak/grids/ncei_kachemak_13_mhw.tif` (+456 km², the outer/SW bay) | in |
| **CGOA (Zimmermann) 100 m** | grid → bed, derived | 100 m; 1995 | NAVD88 | 5.0 | `bathy_src/cgoa_zimmermann/cgoa_navd88_100m.tif` | in, last resort |
| **SfM bare 2023** (Dixon DSM beyond the lidar, bare against 3DEP) | grid → ground | 2 m; 2023-09-19 | NAVD88 GEOID12B | 1.0 | Powder `joint_v3/obs/sfm_bare_2023.tif` (Grewingk box only) | in; rebuild over the bay if the Dixon DSM's extent (x 602–634) warrants |
| **Dixon ice 2023** | grid → ice | 0.3 m; 2023-09-19 | NAVD88 GEOID12B (a 10.7 m datum slip was found and fixed 2026-10-25) | 0.5 | Powder `joint_v3/obs/dixon_ice_2023.tif` | in; the only ice source |
| Soundings (see C) | points → bed, with `min` lower bounds | — | NAVD88 via each lake's level row | per row (CSV `sigma`) | `obs/soundings/soundings_20261017.csv` | in |

Not sources: the Corax drone surveys (Matanuska, 61.8° N), Pedersen (Resurrection side), the Lower Kenai DEM
(a derivative of Kenai 2008, retired), the Seldovia vendor grid (datum), the CRM South Alaska NetCDF (coarser than
CUDEM/NCEI everywhere it reaches), the CIOFS grid, the IfSAR DTM (5 m, hydro-masked ORI; its hydro polygons are a
water prior, §C).

### B. Stack layers

| layer | role | rule |
|---|---|---|
| **3DEP 1/3″** (`ctx_3dep/all.vrt`, 2012) | the prior where no survey saw anything | `fallback` with the joint layer's class raster as footprint (`near_m` 60): a void within 60 m of a survey's cells belongs to the fill, not the prior; dry voids over `dry_max_km2` go to 3DEP; `water_3dep.tif` (flat_water on 3DEP) marks its lakes as water; 3DEP ground below water seen nearby is void |
| **joint layer** | the observations, fused | class raster (ground / water / bed / ice / void) + sigma; where it is void the layer below shows |
| **fill** | the modelled bed and dry voids | `class: modelled`, sigma from `fill_sigma.tif` |

The four vendor DTMs now in the Grewingk recipe as B layers (OCM 2019 grid, Kenai 2008, Homer 2018, Seldovia 2019)
**leave B** once their points are in A; a DTM only re-enters B for ground the points stage cannot give (Homer
2019's unfinalised tiles).

### C. Water and fill inputs

| input | what it is | how it is used |
|---|---|---|
| Hig's mapped lakes (`water/mapped_hig_lakes.geojson`, 3,144) | prior polygons, outdated in places | detector pass, islands, report; trust `prior_cost`; flagged `edge_mismatch` where the cut disagrees |
| reviewed polygons / dispositions (`water/polygons.geojson`, `review.geojson`) | W3 output (the review tool) | trusted: the edge holds, kind and level set the bodies under it; noise = no observation inside the water |
| water stamps (make_ice `lake_2021`, `retreat_2023`) | Grewingk only | mapped water of their own kind, with a ceiling |
| `water_3dep.tif` | flat lakes on 3DEP | 3DEP's hydro-flattened water as water |
| soundings CSV | Berg 2017 Grewingk (5,735 + 316 lower bounds), Riordan 2018 KBSP lakes (573), Higman Petrof/Gore 2022 (59), Grewingk 2019–21 (18), 2026 (4), hand (9) | the fill's spline with sigma; `min` rows as lower bounds in the active set; despiked against their own track |
| tide datums | Seldovia: MLLW −1.38, MLW −0.88, MSL 1.59, MHW 3.98, MHHW 4.24 (NAVD88) | `low_tide_m`, `tide_max_m`, the sea's default stand (MLLW), the viewer's tide levels. **Open:** one set for the whole bay, or Homer's at the head — the range differs by ~0.1–0.2 m |
| IfSAR hydro polygons (`lidar_src/ifsar_grewingk`, Grewingk cells only) | where 3DEP is water | optional water prior beyond the lidar; the statewide set is not on disk |
| hazard rocks (Hig's shapefile, 1,185; NOAA ENC) | floors for the fill | **not built** (WATER_POLYGONS_PLAN §5); after the bay run |

## 3. Interfaces and the assumptions that carry

- **Alignment.** Every lidar aligns to KBay 2023 on stable ground (`align_min_cells` 1,000); Grewingk 2021's +0.36 m
  is a per-point shift; Seldovia uses OCM's GEOID12B points. Chugachmiut has **no declared datum**: its shift comes
  from the overlap with KBay 2023 and Kenai 2008 (both cover x 559–592), measured before the run.
- **Pooling and epochs.** Ground pools across surveys within 5 years, bed within 30, ice within 0.5
  (`pool_years`); older observations widen by `rates` (ground 0.01, bed 0.02, ice 3.0 m/yr). Vintage zones take one
  survey: active floodplains (the most recent flown), beaches (KBay 2023), glaciers (newest). The zone list is
  Grewingk's; the bay needs zones for every major river delta (Wosnesenski, Fox, Anchor, Bradley) — **to be mapped**
  from the channel detector or by hand before the run.
- **Cross-check.** A return standing `over_water_m` above the others' water with no dry ground within
  `over_water_dry_m` is water surface; a bed is superseded only by a newer bed; derived compilations never reject a
  measured bed and are calibrated to measured beds by a smooth field (150 m).
- **Sigma.** Lidar 0.05–0.08 (0.15 assumed for 2008); grids hypot(σ, g·D/√12, σ_xy·g); derived 1–5; the fill's own
  sigma grows with distance from data. These are stated, not fitted — the overlap statistics the fuse already prints
  (`derived_adjust`, alignment NMAD) are the check; the KBay/Chugachmiut and Homer 2018/2019 overlaps are the two new
  ones to read before trusting the numbers.
- **What the stage cannot know.** A DTM-only source (Chugachmiut, Homer 2019's missing tiles) has no return counts:
  support is 1 per cell, so it can neither out-vote a lidar nor be judged "sparse"; facets and flat water are its
  only internal evidence. That is the assumption Hig accepted in taking it at face value with a sigma.

## 5. Set up (2026-10-29, late morning) — what was built and what the first reads showed

**Built** (all committed): `topobathy/dtm_clean.py` (a DTM with no cloud → cleaned grid + mask); `joint_pilot.py index`
and `units --recipe` (surveys from the recipe's `joint.units` block: COPC/LAS readers, per-survey classes, tile
indexes by `pdal info` or by name, a per-file CRS with reprojection, bed classes to `<name>_bed.npz`, a shared cache
on Powder, units outside a box's window skipped); `joint.py` vintage `"allow": "newest"` (one zone per lidar in date
order) and floodplain zones without a fixed survey; `gate.py --gate-dir` with site rows outside the product skipped;
`composites/kbay_joint/` — `make_template.py` (from the Grewingk recipe), `make_boxes.py` (30 boxes, 6 × 5, the extra
southern row), `make_seeds.py` (sea / channel / water-surface seeds from the v7 boxes), `run_box.sh`, `run_bay.sh`.

**Read from the data, and changed accordingly:**

- **NCMP classes are not the USACE scheme.** OCM delivers Homer 2018 and Seldovia 2019 as 1 unclassified, 2 ground,
  23 submerged aquatic vegetation, **29 bathymetric point**; there is no water-surface class. The template takes 2 as
  ground, 29 as bed, drops 23 (kelp canopy). Open water is a hole between ground and bed, for the hole rule.
- **Seldovia's COPC is in Alaska Albers (EPSG:6393)**, Homer's in UTM 5N. Reads are bounded in the file's CRS and
  reprojected; the index is written in UTM.
- **Chugachmiut is not hydro-flattened to the centimetre and has almost no TIN facets**: the clean pass kept
  254.3 km², removed 0.025 km² of facets and no flat water (3 min). So its lakes stay "ground" at a near-flat level
  and only the mapped polygons / 3DEP flat water can call them water — an open item (a looser flatness test on it, or
  trust the mapped lakes there). Its offset against KBay 2023 is **not one number**: −0.26 m (NMAD 0.11) on one
  2 km crop, −0.02 m (NMAD 0.15) on another, which is why it enters as `derived: true` and takes the fuse's smooth
  calibration field rather than one shift.
- **Homer 2019**: 85 of 157 tiles readable (the rest fail `pdal info`, the known unfinalised export); indexed.
- Smoke units (one each): Seldovia 28.7 M points → 33 % of cells ground, 5 % bed (35 s); Homer 2018 offshore unit
  3.9 M points, all bed at −8 m (6 s); Kenai 2008 3.3 M points, 1.9 M ground (8 s); KBay 2023 20 M points (16 s).

**To run (afternoon):** `caffeinate -is zsh composites/kbay_joint/run_bay.sh run1 kj_r1c1` first — the Seldovia /
Bluff Point box (the outer-coast test against the v8 patches), then `run_bay.sh run1` for the rest (resumable; each
box's gate in `composite/<box>/gate/`). The units stage is the long pole: ~5,500 units at 10–50 s each on 4 workers.

## 6. Hig's answers (2026-10-29, late morning) and what changed

- **Extent: x 540–635, y 6540–6636 km** (lon −152.3 to −150.65, lat 58.98 to 59.85), west and south far enough for the
  tip of the Kenai Peninsula (Cape Elizabeth x 560 / y 6558, the Chugach Islands to y 6548.5): a logical map
  boundary that nests in a coarse model grid. 48 boxes (6 × 8), the southern and western ones mostly open water.
  Beyond CGOA's 1995 100 m grid (x < 545, y < 6550) the only bed is NCEI's Coastal Relief Model (~0.5 km, already
  on disk; warped to `bathy_src/crm_southak/crm_6334_500m.tif`), added as the last derived source (σ 10 m).
  South of 59.32° N nothing finer than CGOA exists in the stack.
- **Hig's mapped lakes stay the prior they were**: `water/sources.json` `hig_lakes` (edge trusted, 2017–2022) → the
  water stage's detector pass inside each polygon and the kind as a prior; the shore stage's shoreline cost
  (`prior_cost`) and rule 5 (unobserved cells inside are water); replaced where a reviewed polygon covers it.
- **3DEP's lakes as a prior too**: `flat_water.py` over the whole extent at 10 m → `water/make_3dep_lakes.py` →
  `water/mapped_3dep_lakes.geojson`, listed in `sources.json` before `hig_lakes` with no `edge` (detector pass and
  kind prior, no shoreline cost); `hig_lakes` replaces it where both map the same water. This is what reaches the
  Chugachmiut ground, which is not hydro-flattened.
- **Seeds** for the beach zones outside the v7 product: 3DEP's sea (flat at 0 m) where v7 had nothing.

## 7. Bluff Point crop, Hig's review (2026-10-29, 11:15) — why this patch was worse, and the fixes

Bluff Point has **no KBay 2023**: no reference survey (so no alignment) and no dense lidar. The surface there is
sparse older surveys interleaved with NCEI's 10 m compilation, and two things the raster chain did are missing in
the point-first one:

| Hig's site | run 1 | cause | fix |
|---|---|---|---|
| pimples 59.67027, −151.71544 | Kenai 2008 "ground" at −0.84 m in 39 % of cells, 1 point each, between the bed at −6 m; 86 % land | Kenai 2008's sea-surface returns: its classes carry no water, and the raster chain removed them with the taught water model (`obs/kenai_2008/class.vrt`, precision 0.97) | units: `water_raster` per survey — ground returns in cells the model calls water are water |
| chaotic 59.66450, −151.70424 | the same returns at −0.76 m in 12 % of cells among NCEI at −10 m; classes mixed cell by cell | the same | the same |
| spikes 59.66086, −151.74702 | Homer 2018 green-lidar bed at −17.7 m in 12 % of cells beside NCEI at −19 m | NCEI's calibration is a 150 m field; at the green lidar's margin it cannot fit locally | joint 2c: a fine stage (`derived_fine_m` 10) spreads the residual at measured cells over 10 m, fading to 0 away from them |

Run 2 (`kbay_joint_bluff_t2`): rough cells (|Laplacian| > 1 m) at the three sites 6 / 50 / 47 % → 0 / 0 / 0 %; whole
crop 4.2 → 2.5 %, below MLLW 1.7 → 0.7 %; 2.1 km² of false land back to water; every invariant row 0. The fine stage
is opt-in (`derived_fine_m` 0 by default, 10 in the bay template) so the Grewingk recipe is unchanged until its bay
boxes are gated.

## 4. What to build before the run

1. `joint_pilot.py`: a source list instead of three hard-wired surveys — readers for COPC (Homer 2018, Seldovia
   2019), LAZ tiles (Kenai 2008 classified, Homer 2019), EPT (Kenai raw, optional); per-survey class mapping
   (NOAA class 9 water; USGS no water class); the units cache keyed by survey.
2. Chugachmiut as a grid source with the facet/flat-water pre-pass (reuse `flat_water.py` and the `facets` test as a
   one-off raster → class map), its shift measured on the overlaps.
3. CUDEM added as a derived bed source; Pedersen dropped; the SfM/Dixon sources re-cut to the extent they cover.
4. Vintage zones for the bay's rivers and beaches; tide datums per sub-area decided.
5. Box generation from one template (`make_boxes.py` already exists for the old chain), with the joint block's
   window per box and the units cache shared; the gate's invariant rows only, Grewingk's site rows as a per-box
   list.
6. One outer-coast box first (Seldovia or Bluff Point) against the v8 patches, then the rest.
