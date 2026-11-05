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

## 8. Bay run2 (2026-10-30/31) — Hig's review and the plan

**Run.** 48 boxes on 5ba5174. 27 built first time; 21 edge and open-water boxes crashed in seconds. The first
cause was `_solid_z` returning None in a box with no lidar (fixed, 250912a). The rerun built 2 more (kj_r2c3,
kj_r1c2) and exposed two more: the cross-check's `sum()` over no other sources collapsed to a scalar (fixed,
z0 start), and the water stage writes no bodies when no lidar saw water, which stops the shore stage (open). Gated:
kj_r4c1 (bed_labelled_land 1857/1151 = Seldovia Lagoon; solid_over_level 98/3 at 59.38243,-151.89133 and
59.45017,-151.72057), kj_r6c4 (face_sounding_misfit MISSING), kj_r3c0/kj_r4c0 (three water rows MISSING, no water).
The run log says "ok" for gated boxes: run_box.sh exits 0 when the gate fails (fix after runs).

**Hig's sites and the causes found.**
- 59.33061,-151.91360: a lake's surface at 10.41 m kept as ground from the Chugachmiut DTM (hydro-flattened). The
  DTM clean found 0 km2 of flat water: `flat_range` 0.01 m, the lake surface spans ~0.1 m.
- 59.43963,-151.18614 Upper Hazel (level ~528.6): KBay 2023 classed the lake surface ground at 528.95; Riordan's
  soundings reach 491 m. Soundings enter only at the fill, so the sea-sheet rule never saw them as a measured bed.
- 59.81125,-150.96966 upper bay: no solid observation; the tidal lens set the bed at a survey's 4.88 m water surface
  (above MHHW 4.24) and the shore stage labelled it land: a flat.
- 59.49045,-151.23921 Lower Hazel: level 90.24, soundings min 61.6 / median 65.9; the fill reached 48.9 between lines.
- 59.24565,-151.87629: be_mosaic.tif has no data within 100 m (it covers 26 % of its box); the surface there is the
  3DEP prior. The sea beside it has no bed source: NCEI/OCM/CUDEM end north of it, and our CGOA and CRM copies read
  nodata there (to check: clipped or land-masked when prepared?).
- 59.77316,-151.00348 upper bay void: lidars saw water; NCEI (+0.95) and OCM (+0.55) have values but are valid only
  below MLLW.
- 59.30840,-151.03540 south coast: near-shore voids filled with square flat plates (coarse grids upsampled).

**Hig's answers (2026-10-31).**
1. Where no source fills a gap, the surface must still cross it smoothly: no void, no terrace or step (the upper-bay
   void; the south coast's near-shore voids and plates).
2. Lakes by hypsometry, not a hard floor: soundings are a sample of the lake's depth distribution. Some of the lake
   is probably deeper than the deepest sounding, but it is very unlikely that many soundings at ~62 m sit over a
   true deepest part near 50 m. The fill's depth distribution should match the soundings', with a tail of about
   the area one more sample would find beyond the deepest.
3. The ocean is a basin that drops below the land: where no bed source exists, the sea floor still descends from
   the shore. ArcticDEM will not help there, and it is not hydro-flattened: it interpolates through water.
4. ArcticDEM (2 m) for mountains and upper-bay tide flats (Hig: decent there, better than Mapterhorn): about equal to
   IfSAR. Where IfSAR reads lower (less snow) IfSAR is the better reading. Lower quality than lidar but like it in
   that way. Use its data mask so interpolated surfaces through water never count.
5. Rerun selectively: first the areas with known or suspected issues, then the boxes that look good (Grewingk) as
   time permits.

**Plan, in order.**
1. Edge boxes: commit the one-source fix; a box with no lidar water still gets its sea (compilation bed below MLLW
   is the sea); the fill bridges every gap smoothly between what is known; and where the sea has no bed source the
   floor descends from the shore (a basin, not a void and not a plate at the coarse grid's cell).
2. Water surfaces and lakes: the DTM clean's flat-water test widened to a broad, near-level sheet at a lake's level;
   soundings count as a measured bed in the sea-sheet rule; the fill's lake interior mapped onto the soundings'
   depth distribution (rank-preserving), then conditioned to the soundings.
3. ArcticDEM 2 m mosaic (data mask, count, MAD), a derived ground source below every lidar, calibrated to lidar
   ground; yields to the 3DEP/IfSAR prior where that is lower by more than the snow tolerance; never over water.
   Check the CGOA and CRM copies.
4. Crop tests, gated: Upper Hazel, Lower Hazel, the upper-bay flats, the Chugachmiut lake, and outer-coast sites with
   voids and coarse-grid plates (59.24565,-151.87629; 59.30840,-151.03540; more found on the way).
5. Rerun the boxes with known or suspected issues; the good ones (Grewingk) as time permits.

**Steps 1-2 built and tested (2026-10-31 evening, 14e9261..600d7c7; crops tag fix1, hazel_u fix2; dev surface
kbay_joint_fix1_crops).**
- Plates (plates_t): coarse grids by cubic spline, their no-data ring dropped (joint obs_grid, coarse_factor 4): the profile
  across 59.30840,-151.03540 runs -66 .. -2 .. shore with no square.
- Open-water boxes (kj_r5c0): the compilations' sea when no survey saw one (water.py `_add_layer`, bed below MLLW at the
  grid edge): 346.5 km2 of sea at -1.38, the box builds; gate rows for water steps MISSING (none to count).
- Upper bay (upbay_t): the 8.6 km2 low void is a marine domain bridged at ~0.2 m (fill_mask 2); the 4.88 m flat is a
  slope at 3.6-4.6 m (449.79 ha of lens surface above lens_max_m 4.3 left to the prior). Class there is `modelled`
  with no shore label -- to look at.
- Upper Hazel (hazel_u fix2): the soundings a joint `points` source; the sparse-bed sheet rule (sparse_bed_min_n 5)
  judges sheets at any height (the sea rules keep the tide ceiling per sheet): KBay 2023's 27.7 ha surface sheet at
  528.95 came down, the lake is water at 528.58 with its bed by the soundings' hypsometry (130; 25 m at Hig's point).
- Lower Hazel (hazel_l): hypsometry from 197 soundings, tail by the last gap again (36.0 -> 43.3): modelled depth
  p10/50/90/max 2.8/20.6/27.5/41.1 vs the soundings' 2.8/20.4/26.2/36.0; Hig's point 30 m deep (was 41).
- Chugachmiut lake (chug_t): dtm_clean's level test per mapped lake polygon (p90-p10 < 0.3 m inside the polygon;
  the surface was level, not flat: 3x3 range median 0.09 m): 2.45 km2 of lake surface out of the DTM (v2), the lake
  is water at 10.51 with a modelled basin.
- Tip (tip_t): fully covered now -- CRM v2 (3.2 km2 of bed) and the bridge; 68 % is the 3DEP prior on land. The
  Chugachmiut DTM has no data at 59.24565,-151.87629 (26 % of that box): the surface there is 3DEP.
Open: ArcticDEM (12 GB downloading to Powder, ~0.2 MB/s); the selective rerun.

### 8.1 fix2c / fix2d (2026-11-01) — Taylor Bay's plateau, and the fill's points in the viewer

Hig on fix2c: "a plateau at 59.30782, -151.04115 that may be related to one or more bad soundings". It was not the
soundings. `fill_ceiling.tif` = 2 there: the guard's clip. Three things stacked, all in fill.py, all fixed as general
rules (uncommitted):

1. The ceiling over a compilation sea domain was the stage's level = KBay 2023's tide-time sea surface (2.9 m), so the
   ceiling pass held the bed 0.5 m under 2.9 m and the guard cut a 1 deg cone under it (-0.85 m at the site). In a
   floor-path (nolens) sea domain the ceiling is now low_tide_m (`lt_ceil`: the sounding drop, the ceiling pass, the guard).
2. The prior under the 1930 line's -2.6/-3.8 m soundings stood at -36 m: fix2c's floor was the NEAREST sounding smoothed
   over a fixed 50 m and the interior a harmonic blend of the band's edge. The spline lifted +33 m at sigma 10 and carried
   the lift to the shore, through low tide, into the guard. Now the floor and the interior prior of such a domain are the
   soundings' kernel mean (`sound_prior`): equal weights, bandwidth max(floor_sound_smooth_m 20 m, distance to the nearest
   sounding), multi-scale Gaussians interpolated in log scale. 1/sigma^2 weights were tried and were wrong (a sigma-2.5
   line 100 m off outweighed a lone sigma-10 sounding on its own cell); 50 m smeared a flank dropping 40 m in 100 m.
3. The ceiling pass planted its sites on a flat plane (level - 0.5) while the guard cut at a cone descending from the
   shore: the sites themselves built the shelf. The pass now holds under the same cap (`cap_` computed once).

plates_t fill-only tests at the site: -0.85 (fix2c) -> -5.1 (1) -> -5.1 (2, weighted) -> -5.1 (2, equal weights) ->
-22 m sloping (3 + 20 m). Residual of the soundings to the prior p10/p90 -28/+20 -> -12/+0.2 m; the guard's area
0.93 -> 0.35 km2, now only the head-of-bay flats where no sounding reaches. Pass 15 (plates_t, tip_t full chains; gates
MOVED water/land only) -> `kbay_joint_fix2d_crops` on dev (26 dev surveys). Koyuktoolik lagoon -3.3 -> -4.1 m, no
regression.

The fill's points as sounding surveys: `tools/soundings/ingest_nos_geodas.py` -> 38 NOS packages + `crm_southak_500m`
(dense = true; API `?dense=1&bbox=`, soundings.js per view from z 12.5). Verified on a scratch DB; NOT loaded in dev
until hosting merges this branch (main's API would serve all 350k points whole). tools/soundings/README.md.

### 8.2 The 1915-31 NOS positions are shifted (2026-11-01)

Hig, seeing the Taylor Bay casts as points: "they often fall on land rather than in the water, and are systematically
offset from Taylor Bay". Checked first that it is not our frame: NCEI's GEODAS files record the horizontal datum as
NAD83 and the ORIGINAL as "Early Alaska Datums" (each survey's local astronomic datum); we read NAD83 -> EPSG:6334
correctly, and a NAD27-as-NAD83 error would be ~120 m W / 75 m S here, the wrong direction and size. NCEI's conversion
of the local datum is what is short.

`topobathy/nos_offsets.py` measures one translation per survey two independent ways over a +-800 m grid: the shift
that puts the fewest casts on the modern lidar's ground above MHHW (summed over the run2 box products), and, where a
1977-84 echo-sounder survey overlaps, the shift that minimises the MAD of old minus modern depth (a constant vertical
difference allowed). They agree to ~50 m (H05187: +400/+340 land vs +420/+340 depth). The 1980 control (H09879)
sits at zero. Result (`$LIDAR_BUILD/obs/soundings/nos_offsets.json`): 1930-31 surveys +380..420 m E / +300..360 m N
(H05082, H05101, H05186 by land; H05187, H05188, H05192 by depth; H05083/87/91/92/00, H05191, H05194 inferred at the
season median +400/+320), H03802 (1915) +240/+260 by depth, the 1927 pair no evidence (left). After the shift the
1930s depths read ~1 m shallower than 1977-84 where they overlap -- the 1964 subsidence's sign.

Applied by `nos_soundings_csv.py --offsets` (-> `nos_1915_1931_20261101.csv`, `nos_1977_1984_20261101.csv`; the
template and all 56 recipes repointed) and by `tools/soundings/ingest_nos_geodas.py --offsets` (the packages' title,
notes and `ingest.position_shift` carry the shift, method and evidence; raw files untouched). Explained for readers
in /lidar/methods/ section 11. Of the review crops only plates_t holds 1915-31 points (tip_t and upbay_t carry 1980
lines, unshifted), so pass 16 reran plates_t -> `kbay_joint_fix2e_crops`. Dev viewer cut to the minimum the same
day: kbay_2023, kbay_2023_reclass_v2, grewingk_sonar_2023_levelled, joint_v3j, kbay_joint_v1 and the latest crops
mosaic (20 surfaces, 14.6 GB retired). Not done: a rubber sheet -- the residuals after one translation per survey
are within the test's 20 m step where measurable; Hig's point offsets would be the input if a trend appears.

### 8.3 fix2f: the sea-bed prior between soundings is no narrower than their spacing (2026-11-01)

Hig, on the fix2e boxes at Taylor Bay: the casts "often have flat-bottomed circular basins around them, with the
sounding at the center"; the lakes (Grewingk) integrate soundings smoothly. Cause: the floor-path prior (8.1) is the
soundings' kernel mean with bandwidth max(20 m, distance to the nearest sounding), so each cast owned a disc at its own
depth out to a third of the way to its neighbours, the ramp between casts crowded into the middle third, and the
spline, which fits the residual to the prior, found none at the casts. A lake's prior is a smooth basin and the
thin-plate spline carries its soundings, which is why Grewingk shows none of this.

Fix (fill.py, `sound_prior_gap_mult`, default 1): the bandwidth's floor is the LOCAL HALF-GAP between soundings -- the
distance transform's maxima (midpoints between casts and between lines) over a window of twice the domain's typical
gap, on an 8-cell lattice, smoothed -- so a lone cast is averaged with its neighbours, a line is its local mean and
lines ramp across their whole gap; the spline then fits the real point-to-point scatter as a minimum-curvature surface.
Measured on plates_t (fill only, 2.7 min): half-gap median 121 m; "disc index" (share of the modelled bed 15-40 m
from a cast within 0.15 m of the cast's own cell) median 0.08 -> 0.03, casts with > 0.5 of their ring flat 6 % -> 0 %;
modelled cells flatter than 0.2 deg over 10 m 0.4 % -> 0; spline misfit RMS 7.9 m before the ceiling passes (the
casts' sigma, 2-10 m, governs) and 3.0 m after; guard area 0.306 km2 (was 0.312). Sites: the plateau -65 -> -71 m, the
ovoid -10.4 -> -12.2, the low-angle area -66.5 -> -64.3, the plates -2.7 -> -2.1. Scratchpad `disc_index.py` is the
measure. kj_r3c4 fix2e for comparison: 22 % of casts owned a flat ring, flat share 2.5 %.

Run: the four fix2e boxes restart at the fill stage (`fix2f_fill.sh`: fill -> build 3 -> review -> gate -> out_fix2f;
the fill alone was ~60 min of a box's 75 in fix2e), then the outer-coast boxes end to end (`run_bay.sh fix2f kj_r1c2
kj_r2c4 kj_r1c3 kj_r2c1 kj_r1c1 kj_r3c5 kj_r2c5 kj_r4c5`; in run2 the southern rows crashed in the joint stage before
the fix2 open-water fixes, and were never tried since), mosaic -> `kbay_joint_fix2f_boxes` on dev. The first outer
run under fix2e was stopped at kj_r1c2 for this fix (its products would have been redone).

### 8.4 fix2g: the sea's prior is a lake's; ground returns inside a lake (2026-11-01, evening)

Hig, on the fix2f boxes: "the marine soundings still have rather odd shapes around them ... still involving basins
around the points, and totally unlike the way the soundings are interpolated in lakes. It seems like we should be using
the same infrastructure as we built for lakes."

**Cause.** A kernel mean of the casts is a sum of bumps whatever its bandwidth: every cast carries its own anomaly,
spread over the bandwidth; the spline, which fits the residual to the prior, carries the bumps through.  The half-gap
field of fix2f also lived on an 8-cell lattice, which put a faint lattice texture into the prior (visible in a
hillshade of `fill_prior.tif`).  A lake's prior has neither: one smooth profile of depth against distance from the
shore, and the thin-plate spline alone carries the soundings' structure as a minimum-curvature surface.

**Rule (fill.py, `sea_prior` "profile", the default; "kernel" keeps fix2f).** In a floor-path sea domain with
soundings the prior is a profile FITTED to the domain's casts: the casts cut into up to `sea_profile_bins` (20)
quantile bins of distance from the shore (at least `sea_profile_min_n` 30 a bin), each bin its median distance and
median depth below low tide; the medians made non-decreasing with distance (pool-adjacent-violators: a flat bottom is
allowed, a trough is the spline's); zero at the shore, linear between the bins, flat beyond the last; read at every
cell's distance from the shore and smoothed over half the casts' spacing.  It is the floor and the interior prior,
the band's rim-slope extension descends to it, the spline fits the casts about it, the ceiling passes and the guard
are unchanged.  The lake loop's Hollister / glacial profile is not used: the sea's deepest point is not at its
farthest from shore, and a box's sea domain is cut by the box.

**plates_t (fill only, 3 min).** Profile: 0 m at the shore, 16 m at 35 m, 23 m at 117 m, 36 m at 160 m, 42 m at
327 m.  The spline's residual to the prior grew (|p90| 20.8 → 37.6 m: the bay's real roughness, a shoal of 1-5 m casts
beside 43 m, is now the spline's to carry), the correction field p5/p95 ±37 m; ceiling held-by-site 0.348 → 0.371 km²,
guard 0.306 → 0.329.  Cast-to-ring anomaly p90 4.35 → 2.34 m, |Laplacian| p50 0.0067 → 0.0044; the disc index stays 0.
Sites: plateau −71.2 → −67.8, ovoid −12.2 → −12.1, low-angle −64.3 → −63.4, plates −2.2 → −2.2.  Hillshades of the
fill (scratchpad hsc_*.png) show the bay as one smooth basin with a deep channel instead of a lumpy, latticed sheet.
Open: the shoal casts (−1.4 m at 6 m from the ovoid site, −43 m 100 m off) are weakly held at sigma 10; a shoal cast
as an upper bound on the bed within its position radius is the fix if rocks matter (reported to Hig).

**Ground returns inside a lake (fill.py, with the water-blind drop).** Hig: flats at 59.43508,−151.18673 and
59.43856,−151.18314 (Upper Hazel / Kinnikinnick) and 59.48302,−151.21596 (Lower Hazel), "probably water surface points
classed as ground".  They are patches of 0.3-0.6 ha classed ground 0.2-1.6 m under the lake, too large for the
domain closure, standing as observed bed.  In the Hazels they are ArcticDEM's interpolated surface where the lidar had
no water returns -- `arcticdem` has been water-blind in the joint layer since the fix2 template (the dev crops
predate it; a rerun drops them, 1.93 + 1.27 ha).  The general rule added beside it: a return inside a LAKE's water
label that is not a bathymetric (bed) class and lies at or below level + lake_surface_tol_m is a hole, not bed -- a
topographic laser does not see a lake bed; ground above the level (islets, shore) stays; lakes only (the sea's label
holds tide flats a low-tide flight saw under a later flight's surface).  In the four fix2f boxes it catches one
further case: 1.06 ha at 59.26500,−151.44024 (kj_r3c3), a perfectly flat sheet 2.5 m under the body's level -- a
lower stand of the lake seen by another survey, which the single-level body cannot represent (the multi-level lake of
§7 is the open item; until then the sheet is modelled as bed from the basin).

**Also found.** A fill-only rerun reads build 3's `source_index.tif`, in which the previous fill's cells are the
fill layer, so the water-blind mask counts fewer cells than the chain's fill saw (kj_r3c4 33.7 → 23.5 ha); the
cells are holes again either way (composite's "a previous fill's cells are holes again"), so the product is the
same -- noted so the different count is not read as a change.

**Run.** fix2g: the six review crops and the four boxes fill-onward (scratchpad `fix2g_fill.sh`) → dev
`kbay_joint_fix2g_crops` and `kbay_joint_fix2g_boxes`; then the eight outer-coast boxes end to end
(`fix2g_outer.sh`).  The fix2f outer run was stopped at its first box.

#### 8.4a (2026-11-02, 00:00) — the fill's drop never reached the product; the rule moved to the shore stage

Checking the fix2g crops product at the Hazel sites: `fill_mask` 3 (modelled) and `fill.tif` 507 m, but the product still
527.85 m -- the joint's ArcticDEM cell.  Build 3 voids the joint only where the shore stage's `water_blind.tif` says so
(the `blind_water` class step), and that file is written by the shore stage alone: the crops' copies were from Oct 31, before
ArcticDEM was water-blind, and no stage wrote the new ground-inside-a-lake rule into it.  So a fill-only rerun could not
change the product over those patches, and the rule in fill.py could never have (the 2026-10-25 lesson, again: a drop made
after the shore stage is a drop the build does not see).

- **The rule now lives in `shore.py`** (after the final labels and levels, before the bank slope; `lake_ground_tol_m` 0.2
  in the shore DEFAULTS): a land-classed return (ground, built, canopy, snow) inside a lake's water label at or below its
  level + tol joins `blind_in`, so `water_blind.tif` carries it -- the build voids the joint there, the fill makes it a hole.
  Lakes only; land below a lake's level outside the water label stands.  The copy in fill.py (after the water-blind drop)
  is now redundant and is to be removed once no run is loading fill.py (it differs only in taking every non-bed class).
- **Rule for the chain:** anything that declares an observation not an observation belongs in the shore stage, which
  writes `water_blind.tif`; the fill reads it, the build reads it.  The fill never drops observations by rules of its own.
- Limit worth knowing: only the joint layer carries the `blind_water` step in the recipes.  A separate observation
  layer's (homer2019's) ground return inside a lake would still stand in the product; the general fix is the step on every
  observation layer (template + regenerated recipes), not done.
- **A build-2-onward rerun** (fix2g_outer2.sh `run_one`: remove the previous fill/shore products as the chain does, then
  build 2 -> shore -> fill -> build 3) reproduces a clean chain from the water stage's products.  Upper Hazel test: the
  shore stage drops 3.36 ha of water-blind cells and 0.04 ha of ground inside the lake; the product over the two patches is
  the fill's bed (507.1 m and 518.9 m under the 528.58 m level; soundings 11-27 m from the first read 17-33 m deep); gate
  passed, no row regressed.
- Queue (fix2g_outer2.sh, after the fill-only boxes publish): the six crops build-2-onward -> republish
  `kbay_joint_fix2g_crops`; the eight outer-coast boxes end to end; the four south-shore boxes build-2-onward (kj_r3c3's
  lower-stand sheet at 59.26500, -151.44024 will become modelled bed) -> republish `kbay_joint_fix2g_boxes`.

#### 8.4b (2026-11-02, 01:00) — Hig's Taylor Bay shallows review of fix2g: two causes found, not yet fixed

1. **Deep trough bounded by flats at 59.32151, -151.03754 (the delta front).**  The product there is the profile prior
   (-16.6 m: the bay-wide depth-by-shore-distance, 16 m at 260 m) lightly corrected (-15.7).  The nearest casts the recipe
   uses (nos_1915_1931_20261101, H05101 of 1930-31, shifted +400/+320 m) are 380 m east: a north-south line of 1-2 ft casts
   (the delta front as sounded), the 55-125 ft channel 90 m beyond it, the fjord (140-170 ft) beyond that; shoreward of
   the 2 ft line, for 800 m, no casts at all (the lead line stopped at the flat).  So the whole delta apron is modelled
   as a fjord basin 7-17 m deep by the one bay-wide profile, and the 2 ft line holds only its own neighbourhood.  The
   dead-straight seam through the site is the KBay lidar's water body (shore label 2, observed surface 1.68 m) against the
   unobserved void (label 0): two fill domains, two splines, a 4.4 m step in 8 m where they meet (the prior is continuous
   across it; the fill is not).  (A duplicated-cast hypothesis was tested and rejected: with foot-quantised depths and
   dense casts, same-depth matches at a random offset are as common as at any particular one; the doubled lines in a
   first map came from reading the 20261031 unshifted export beside the 20261101 one.)
   Fixes to consider: (a) the prior is LOCAL -- depth by shore distance fitted from the casts within reach along the
   shore, not one profile per bay; and shoreward of the nearest casts it is capped at their depth (non-decreasing seaward
   is the profile's own assumption: a 2 ft line at 400 m means the apron inside it is no deeper); (b) a sea domain is not
   split by a flight-line edge: the observed water body and the void it touches below low tide are one domain for the
   prior and the spline (the lens/ceiling stay per cell).
2. **Cliff steps at the land edge, e.g. the grass flat "island" at 59.32305, -151.02262.**  The lidar observed the grass
   at +2.4 m (ground, label land); the mud around it is void (no returns), in the intertidal zone, with no water body, so
   it joins a floor-path marine domain whose ceiling is low tide descending at 1 deg: the bed is forced to -1.5..-2.0 m
   within 30 m of the +2.4 m grass, a 4 m cliff.  Box-wide in kj_r3c4: 39 km of observed-land (< 6 m) edges against fill
   more than 2.5 m lower (27 km more than 3.5 m), nearly all in the intertidal zone.
   Fix to consider: the low-tide ceiling does not start at the land rim.  Beside observed land the ceiling descends from
   the rim's own height (capped at MHHW) to low tide across the intertidal, at the local profile's gradient (at a delta
   the 2 ft line 400 m out stretches it; on a fjord wall the first bins make it steep), and only beyond that is low tide
   the cap.  The rim residual harmonic already carries the rim height inward; lt_ceil clips it today.

#### 8.4c (2026-11-02, 01:30) — fix2h: local sea profiles, bounded by the first cast in every direction; one sea across coverage edges

Hig: "There is no lidar here -- the data sources are IfSAR / 3DEP (= Mapterhorn) and ArcticDEM.  Regardless, I think that
local profiles are a good idea."  (Correction to 8.4b: the "water body" east of the seam is a DEM's sea surface, not a
lidar's; the seam is still that product's coverage edge.)  Built and tested on a copy of the Taylor Bay crop
(`plates_lp`, a scratch copy of the code, so the running queue was untouched), in four steps, each one shown necessary
by the one before:

1. **Local profiles** (`_sea_profile_local`): one profile of depth by shore distance per 200 m anchor, from the casts
   within 1.5 km (radius doubled, twice, below 40 casts; the domain's own profile when still too few; bins of 8), a
   cell's prior the Gaussian blend of its four nearest anchors' profiles at its own shore distance.  Alone: the delta
   site went from 32 m to 20 m deep.  Why not further: the 2 ft line and the channel 90 m east both run away from the
   shore, so at any shore distance both a 2 ft cast and a 30 m cast exist -- a profile by shore distance cannot
   separate them.
2. **The first cast in every direction bounds the prior**: at each cell the nearest cast in each of 8 sectors (among
   its 32 nearest); the prior depth never exceeds the deepest of them.  A plain "deepest of the 8 nearest" failed 150 m
   from the apron's edge (one channel cast among the eight).  Still 4.5 m at the site, because...
3. **The apron had no prior at all**: it was a marine void with no cast inside it, beside the DEM's water body holding
   every cast; `sound_prior` was NaN west of the seam and the apron was the rim's descent plus the harmonic bridge.
   **Touching floor-path marine domains are now one domain** (merged after `nolens`; each group keeps its smallest old
   id, so the per-id tables stay valid; lens domains and lakes are never merged): one profile, one spline, one ceiling.
   The prior was then right (1 m below low tide, continuous across the seam) but the product 3 m deeper, because...
4. **The ceiling's 1-degree cone** demanded 7 m of water 400 m from the shore.  The shallowest first-cast relaxes the
   cap (`sea_relax`, never above low tide less 0.1 m).  Result at 59.32151, -151.03754: product -2.0 to -2.5 m over the
   whole apron (the 2 ft line's depth), the seam gone, the channel a smooth tongue; crop-wide 3.7 km2 changed, 1.06 km2
   raised and 0.86 km2 lowered by more than 2 m.
- Also found: a local named `shore` in the no-shore-stage branch shadowed the stage flag (crashed the first test, which
  had raced the queue for its products); renamed.  `fill_sound_prior.tif` is written beside `fill_prior.tif` (the
  interior sound prior alone, the diagnostic that found step 3).  The fill-side lake rule of 8.4 is removed (the shore
  stage's is the one that reaches the product).
- Not done: the cliff steps at the land rim (8.4b item 2) -- the ceiling still starts at low tide beside observed land.
- Queue `fix2h_run.sh` (after the fix2g fill-only boxes publish): crops fill-onward -> `kbay_joint_fix2h_crops`; the
  four south boxes build-2-onward -> `kbay_joint_fix2h_boxes`; the eight outer boxes end to end -> republished.
  Retired from dev: `kbay_joint_fix2e_crops`, `kbay_joint_fix2f_boxes`.

### 8.4d kj_r4c1, the multibeam's western edge, fix2h end to end (2026-11-02 03:28)

Published alone as `kbay_joint_fix2h_boxes`. Gate: two rows flagged against run1, neither new to fix2h --
`bed_labelled_land_cells` 1943 (run1 1151, run2 1857) and `solid_over_level_water_cells` 98 (run1 3, run2 98).

**The blend from the multibeam into the soundings that Hig asked to see does not exist in this box.** West of
E558194 the sea is the NCEI 2010 Kachemak 1/3 arc-second grid (`ncei2010`, sigma 3 m; 172 km² of the box's 300;
source bit 64), a gridded observation. The soundings govern only 0.57 km² of floor-path voids. The 842 NOS 1977-84
casts west of the edge (63 m spacing, sigma 0.88 m) are never consulted, because a cell with an observation is not
the fill's to model. The NCEI grid agrees with those casts at a median of 0.04 m (NMAD 0.71, p10/p90 -1.1/+1.6 m):
it is their gridding. Its pocks (30-60 m across, 0.6-1.3 m of relief at 50 m; |Laplacian| p90 0.037 against 0.005
on the smooth NCEI strip to the north and 0.067 on the multibeam's real texture) are gridding noise between the
casts, upsampled by the cubic spline with a visible 10 m lattice. The multibeam's edge is a hard line (100 m |dz|
across it p90 2.9 m, interior 2.1, worst 7.5). NCEI also fills 12.3 km² of gaps inside the multibeam's extent, at
38-78 m depth.

Proposal, not done (Hig's own suggestion of 2026-11-01: "treat the low-res bathy as sparse low-confidence
points"): a compilation grid whose sources are in the stack as casts is not an independent observation.
`ncei2010` and `cudem2022` as points at their cell centres (as CRM already is) or voided where casts lie within
some distance, so the sea there is a floor-path domain the local-profile spline fills from the casts, conditioned
on the multibeam at its edge. It changes every outer-coast box and is Hig's decision.

## 8.5 The intertidal: review and the first structural test (2026-11-02)

**Hig's review of fix2h:** the only remaining severe issue is stepping in the intertidal, in every box with one. He
asked for a review through the lens *terrain : intertidal : basin*, with the intertidal semantically and structurally
its own thing, and raised two axes a coastline could be placed on -- erosive / depositional (sea cliffs with wave-cut
platforms against coastal plains with beach ridges) and strength of geomorphic expression (a protected bedrock shore
where extrapolating the terrain into the subtidal is plausible, against deep notches and broad beaches) -- with the
caveat that parameters must never conflict with observations and garble the surface.

**How the band was handled (before this test):** there was no intertidal stage.  Four stages each carried a tide
constant: the joint's sea sheets below tide_max_m 4.5; the water stage's intertidal zone (tidal bodies + 100 m round
observed ground between MLLW and 4.5 m), inside which the gridded prior was void; the shore stage's low-tide river and
tide range; and the fill, where a void with a rim under marine_rim_max_m 6 m was the sea's, the ground prior was kept
only at or above void_ground_m 4.5 (fill_mask 8), and everything below became a marine band under a ceiling of LOW
TIDE everywhere (the floor-path rule of 2026-11-01), so the bed was forced under MLLW at the land itself.

**The step decomposition** (`scratchpad/step_decomp.py`, now `step_decomp2.py` for any product): every step steeper
than 0.75 m per 2 m cell inside -3.5..6 m, classed by what lies on each side and by the height of its top.  Three
rule-made families, each clustering at a height that names its rule:

| family | top height | kj_r3c4 | kj_r2c3 | kj_r2c2 | kj_r4c1 (lidar) |
|---|---|---|---|---|---|
| terrace: ground prior kept (mask 8) vs marine band | +4.5 m in 57-71 % | 108 km | 38 km | 43 km | none |
| rim cliff: observed land (ArcticDEM, IfSAR) vs marine band | +1..+5 m | 74 km | 28 km | 32 km | 10 km |
| water line: a survey's sea-sheet edge vs the fill | 0..+0.5 (lidar), -0.5 (Chugachmiut DTM) | small | 20 km | 31 km | 60 km |
| observed texture within one source, for scale | spread | 130 km | 87 km | 190 km | 121 km |

The first two are one defect from two sides (the band's upper boundary a constant contour, not the land as seen); the
third is value-without-slope continuity where a survey's returns stop at that flight's water line.

**The test (scratch copy `scratchpad/lidar_lp`, `patch_intertidal.py` v4, not ported):** in a floor-path sea domain
(1) the band is the rim extension's reach OR wherever the 3DEP ground prior stands above MLLW + 0.3 m (not its
hydroflattened water -- `ground_water` = water_3dep.tif codes 1 lake / 2 sea -- not a body's cells, not exactly
flat), and there the prior IS the DEM; (2) the band's ceiling is the nearest land-rim cell's own height (capped at
tide_max_m, under an observed sheet, under the DEM + 1 m), no slope demanded; the interior's ceiling ramps from the
band's edge to low tide at band_edge_deg 3 and the guard's 1 deg cone starts at the band's outer edge, so low tide is
no level of the ceiling any more (it stays the prior's floor); (3) the points filter and the cap read that ceiling
field outright; (4) the band's DEM cells join the points at band_ground_sigma_m 1 m, one per 10 m block.  New fill
params: tide_max_m, band_ground, band_ground_sigma_m, band_ground_min_m, band_edge_deg, ground_water.

Two crops, each against a baseline: Taylor Bay (plates_t fix2h -> plates_lp; depositional, DEM only) and Seldovia
(seldovia_t rerun end to end as tide0 -> seldovia_lp; erosive, lidar).  On dev: `kbay_joint_tide_after` (both crops,
v4), `kbay_joint_tide_before` (Seldovia tide0; Taylor's baseline is `kbay_joint_fix2h_crops`).  Versions v1-v3 kept as
`plates_lp/out_v1..3`, `seldovia_lp/out_v1..3`.

Results.  Seldovia: the floor-path water-line steps along the lidar's edge are gone (band steps 125 -> 108 km; the
`kbay2023 | fill` family at +0.5..1 m vanished); the lens-domain family is untouched, as expected.  Taylor: the +4.5
terrace and the rim cliff are gone and the island's grass flats read the DEM exactly (z = pz along the profile), BUT
(a) 3DEP flattens the SEA at exactly 0 m NAVD88, which is above MLLW, so of 385 ha of DEM "above low tide" 334 ha is
no data at all and the band ends at 3DEP's 0 m line; (b) in the 3DEP-sea interior the spline between the deep
local-profile prior (-20 m, the delta front) and contradictory 1931 casts (-1 and -45 m within 100 m, sigma 10)
overshoots and rides its ceiling: with low tide as the ceiling (fix2h, v2) that ride was the plausible -2.5 m flat Hig
liked; with the edge height (v3) it rode at +1 m; v4's ramp brings it back to -2.3 but a basin at -6..-10 m now sits
between the island and the flat; (c) 33 km of steps remain inside the conditioned fill (tops 0..+1 m), the overshoot's
plateaus; (d) the SUBTIDAL moved with no change to its data -- ovoid -6.9 -> -35, plateau -66.8 -> -49, deep-bounded
-2.1 -> +0.9 -- because `_spline_residual` picks ONE lambda per domain by chi = 1 over all its sites, and 4,000 DEM
sites at sigma 1 re-tuned the whole domain's smoothing (the same effect seen between the crop and the box, 8.4d).

**What the test says about the structure.** The ceiling was the right first target and it is fixed for the rim and
the terrace.  The remaining stepping is the spline's, and it is structural: one smoothing per sea domain cannot serve
an intertidal band with 10 m DEM sites and a delta front with 100 m casts at sigma 10 at once.  The intertidal needs
(i) its own fit -- the band and its DEM/lidar/cast data as their own spline region (or a lambda chosen locally),
joined to the basin's with slope continuity at the band's outer edge -- and (ii) its own prior between the band's
edge and the first casts, where 3DEP has flattened everything: a gentle descent from the edge to the casts' level,
not the sea profile measured from the land rim.  Hig's two axes map onto exactly those two pieces: the rim extension
at the rim's own slope IS the erosive/"extrapolate the terrain" model (strong where the rim is steep), the band's
data supply the depositional flat where one exists, and expression strength is measurable as how far the band's data
pull the surface from the extrapolation -- a diagnostic to report per reach, not a parameter to set.  Not built.

### 8.5a The band as its own fit (v5), the profile's shore as the low-tide line (v6) -- 2026-11-02 evening

tide1 (v4 on the 13 boxes, fill-onward) was STOPPED by Hig after the four south boxes (on dev as the partial
`kbay_joint_tide1_boxes`): on kj_r3c4 the terrace and rim cliff were gone but 23,626 DEM sites at sigma 1 m in the sea's
one spline re-tuned its single smoothing, 252 km of steps appeared inside the conditioned fill (34 km in fix2h) and a
third of the subtidal moved by over 1 m with no change to its data.  fill.py is PORTED to the main tree with all of it
behind `intertidal_band` (default off; the gate-off fill reproduces fix2h bit for bit on plates_t); composite.py's
`--param` keeps a non-numeric value as a string (`ground_water=<path>`).

**v5** -- the band is its own fit: in a floor-path sea domain the band's sites (the DEM, casts inside it) fit the band's
cells alone (the land rim the zero boundary, the outer edge free), the interior's prior is re-based on the band's FITTED
edge (a blend weighted by distance-to-edge against distance-to-the-nearest-interior-cast, capped at band_blend_max_m
500), the interior's sites fit the interior with the band as a further zero boundary, the bounds and ceiling passes refit
the interior only.  Taylor crop: the band's own spline corrects ~0 (its prior is the DEM, as it should be); the subtidal
sites move less than v4 but still move (ovoid -6.9 -> -2.3, plateau -66.8 -> -42.7); steps inside the conditioned fill
28 km (fix2h 8.7, v4 33.7); a 5-9 m basin west of the island where the interior's profile prior (-20 m, the delta front)
meets the band's edge at +1.4.

**v6** -- the sea profile's shore is the land rim PLUS the 3DEP ground above low tide (`_dem_band` found once, early, and
reused by the band), so the casts' distance counts from where the flat meets the sea.  Taylor: almost no change -- the
DEM band is only the 35 ha of grass flat; the 334 ha 3DEP flattens at 0 m is not shore, so west of the island the
profile's origin is still the island and its anchor's casts are the delta front's: the prior there stays at -11..-21 m
and the basin remains (now 2-3 m deep).  Steps 27.8 km; the sites as v5.

**Where this leaves the intertidal.**  Fixed and worth keeping: the band hangs from the land rim (no terrace, no rim
cliff), the band is its own fit, the DEM is data in it, the guard's cone starts at the band's edge, the profile's origin
includes the band.  Not solved: the prior across the gap 3DEP flattened (between the band's edge and the first casts),
where the sea profile -- a median over all directions round an anchor -- carries the delta front's depth onto a mud flat
and the spline between it and contradictory 1931 casts (-1 and -45 m within 100 m, sigma 10) overshoots and rides its
ceiling.  Candidate next step: a third zone between the band and the casts, the 3DEP-flattened sea adjoining the band,
whose prior is a shore descent from the band's edge (the tide's reach, not the profile) until the first cast line, with
the profile only beyond it.  Versions kept: plates_lp/out_v1..v5 (+ out_v4 = v4, out_equiv = gate-off), seldovia_lp
likewise; the current out/ = v6.

**Box seams (Hig, 2026-11-02 evening):** steps of 0.5-3.2 m at crop lines in modelled sea (59.14583 -151.73269;
59.21898 -151.08855; the four-box corner 59.21637 -150.91537): each box's own spline, two surfaces in the 2 km overlap, a
hard crop between them.  `composites/kbay_joint/mosaic_blend.py` feathers the boxes across their overlap (weight = distance
to the box's own build edge over the margin); observed cells are unchanged by it.

### 8.5b v7: the basin code for the subtidal, the lower of ArcticDEM and 3DEP (2026-11-02, late)

Hig's review of v6: fine at Seldovia (nearly continuous data), rough at Taylor Bay -- "the profiles aren't well aligned to
the measured soundings -- the soundings are substantially off the bottom, often deeper -- and there are clear
near-sounding artifacts where the model tried to bring the bottom into better alignment.  Also the profile
extrapolation is leading to strong seams.  The profiles are most meaningful quite close to the intertidal, which is also
where there's no data.  Use the existing basin code for deeper areas, with effective blending from that basin model into
the intertidal profile-based analysis; or smooth contours rather than profiles."  And on land round Taylor: "irregular
low-relief seams that sometimes follow ridgelines ... ArcticDEM and IfSAR ... if we always take the lower of the two
they should blend smoothly."  Instructions: stop runs, clear dev and disk except v6, build fixes, a new Taylor model.

**Cleared:** every kbay_joint layer on dev except `kbay_joint_tide_v6` (the catalogue backed up first); on disk the tide1
archives, the tide mosaics, the unblended fix2h mosaic and the crop test versions v1-v5.  Kept: `out_fix2h` on all 13
boxes (the gate's baseline; Hig's "hold onto fix2h" of the morning), the feathered fix2h mosaic COG
(`kbay_joint_fix2h_blend/`, republishable in 5 min), `plates_t/out_fix2h` and `seldovia_t/out_tide0` (the crops'
baselines), `plates_lp/out_v6` and `seldovia_lp/out` (v6).

**The land seams, measured (kj_r3c4, 8 x 6 km round Taylor Bay):** 552k cells on the ArcticDEM / 3DEP boundary; across
it z(ArcticDEM) - z(other) p10/50/90 -1.75 / -0.29 / +0.77 m, 57 % over 0.5 m.  At the gate's edge ArcticDEM - 3DEP is
+0.70 m (p50; p90 +0.95, i.e. just under the 1 m gate), 20 m inside it is -3.7 m: the seam IS the gate contour, and it
follows ridgelines because canopy puts ArcticDEM over the gate there.  **No horizontal offset**: hillshade correlation
over three Taylor windows puts ArcticDEM within one 2 m cell of 3DEP (best shift +0 / -2 m, correlation ratio 1.05-1.08),
and at Seldovia ArcticDEM, 3DEP and the KBay 2023 lidar all sit at zero shift against each other (a first
gradient-magnitude test that said 10 / -12 m was that method's artifact).  Vertical, on the hills: ArcticDEM 1-2.5 m
below 3DEP (p50), NMAD 2-3.7 m; against the lidar at Seldovia ArcticDEM is +2.5 and 3DEP +3.9 m.

**Fix (Hig's rule):** `arcticdem_ground_cand.py --lower` -- inside the gate the candidate is the LOWER of ArcticDEM and
3DEP, so the product equals 3DEP on both sides of the gate contour and is continuous there, and ArcticDEM's detail stands
wherever it reads lower.  Rebuilt as `/Volumes/Powder/lidar_build/kbay_joint/obs/arcticdem_ground_2m_v2.tif` (163 s; the
v1 file untouched, the box recipes still point at it).  At the gate edge: A - 3DEP p50 0.00 m, 7 % of edge cells over
0.3 m (v1: 0.70 m, 86 %).

**Fix (the sea):** v7 of the fill (`patch_intertidal.py`, ported to the main tree; every piece behind `intertidal_band`,
off by default -- the gate-off fill reproduces fix2h bit for bit on plates_t).  `sea_prior: "kernel"` with
`sound_prior_gap_mult` 2.5 and `floor_sound_smooth_m` 60: the subtidal prior is the casts' kernel mean at a bandwidth no
narrower than 2.5 x their local half-gap -- smooth contours through the casts, no disc at any cast, the basin code's
smooth prior -- and the ceiling's cone is relaxed to it + `kernel_relax_m` 1 m (the local profiles relaxed it to the
first casts); the intertidal band (its own fit, v5) blends into that surface across the gap (band_blend_max_m).  The
local profile stays available as `sea_prior: "local"`.

**The Taylor model:** `composites/kbay_joint/crops/plates_lp/recipe.json` (own joint dir
`/Volumes/Powder/lidar_build/kbay_joint/crops/plates_lp`, ArcticDEM v2, the v7 fill params in the recipe) run end to end
by `run_box.sh plates_lp v7`; then the box.

## 8.6 The contour shore model (2026-11-03)

Hig's review of v8: "first nail ocean bathymetry where we have to rely on soundings, and then figure out the intertidal ... generate smooth contours in the
intertidal consistent with the data ... shorelines convex immediately below low tide, agreeing with soundings ... the contours become tide levels, so I can edit
them", with the reference datum below MLLW (the tide falls below it, and waves rework sediment to a bit below low tide).  Built as `topobathy/shoreface.py`
(`fill.shore_model: "contour"`, off by default), read its docstring.

**Measured for the design.**  Base datum: in kj_r4c1's lidar + multibeam the p75 slope peaks at -1..0 m (6.4 deg) and relaxes to the deep background (3.2-3.5 deg) by about
-4 m, and the lowest tide stand is about MLLW - 1.5 m: `shore_base_m` = MLLW - 2 m = -3.38 m NAVD88.  Casts: 1977-84 against the multibeam (45,129): median -0.34 m,
NMAD 0.32 / 0.52 / 0.92 / 1.60 m for assigned sigma classes < 1.5 / 1.5-3 / 3-6 / 6-9.9.  1915-31 against the nearest 1977-84 cast within 60 m (6,725; only 3 reach
the multibeam): median +1.40 m, NMAD 1.6 / 2.5 / 4.2 / 3.0 m.  `topobathy/nos_calibrate.py` writes `nos_*_20261103.csv`: 1977-84 sigma x 0.35 (floor 0.4); 1915-31 sigma x 0.5
(1.5-4 m), each survey shifted by its own median (-1.1 to -3.0 m, n >= 100 pairs) or the group's -1.40.  A pre-1964 survey standing high is what the 1964 subsidence
would produce; that attribution is a hypothesis, the offset is measured.  z_raw keeps the export.

**Faults found building it, in order** (each visible only by looking): the land's Gaussian tail outvoted all data for 190 m and stranded the fjord walls' deep casts inside the
intertidal, so the level field now starts from the rim's slope and only data move it; a fixed rollover width kept narrow fjords' prior shallow; a descent unbounded by the
casts ran the prior 100 m deep; 3DEP land above the DEM band's limit fell through as sea (a stair-stepped wall under ArcticDEM's blocky no-data edge), so the ground prior
above the tide's reach now stands everywhere, its misfit to observations fading; a smoothed slope field with a fallback beyond its kernel's reach jumped 0.01 -> 0.52 and
drew thin arcs across the bay, so slopes are carried from the nearest shore and then smoothed.

Test crops `crops/ct_*` (`make_shore_test.py`: ArcticDEM v2, the calibrated casts).  Edited lines: `water/tide_lines.geojson`, LineStrings with level_m, EPSG:6334.

### 8.6a Casts against 3DEP, and an idea parked (2026-11-03, Hig's review of the fifth test)

3DEP land wins over a sounding.  A cast it contradicts also puts its neighbours in question: a cast is wrong in its position rather than its depth, and the quasi-regular spacing
of a survey line means moving one cast to the nearest place its depth could be right would not fit the line it belongs to.  `out/cast_review.geojson` lists every cast with what
the model did with it, and flags the same survey's casts within 150 m of a contradiction (`near_land`).

**Idea, not for now (Hig):** a "distort horizontal position of survey" tool, moving a survey's casts together rather than one at a time, on top of the constant shifts of 8.2.

Casts are moved, not rejected (Hig): NOAA's casts are unlikely to be truly wrong, only in the wrong place, so a reviewer's correction records the translation and keeps the depth.  Workflow: SHORE_REVIEW_WORKFLOW.md.
