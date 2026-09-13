# Kenai Peninsula 2008 lidar: rebuilding the DTM from the point cloud

Status 2026-09-12: plan, not yet run. Written after the search that found no
public DTM for this survey anywhere (USGS: point cloud only; NOAA Digital
Coast: no DEM product; DGGS: point cloud only). Hig's local `lower_kenai`
raster is ~1,190 km² of the 11,740 km² project and carries bare-earth errors
he wants gone, so the DTM is to be produced here, from the points.

## What exists

- **Point cloud**: USGS 3DEP `AK_Kenai_2008`, Aero-Metric 2008-05-21 to
  09-22, 14,054,081,685 points, ~1.21 pts/m² over 11,586 km². Public, no
  sign-in, as an Entwine Point Tile (EPT) octree on AWS:
  `https://usgs-lidar-public.s3.us-west-2.amazonaws.com/AK_Kenai_2008/ept.json`
  (LAZ nodes, EPSG:3857 horizontal; dims X Y Z Intensity ReturnNumber
  NumberOfReturns Classification ScanAngleRank GpsTime PointSourceId …).
  Bucket size: >6 GB in the first 61,000 objects, listing capped; expect
  roughly 15–30 GB in all.
- **Vendor classes** (LAS 1.1, non-standard): 2 ground, 7 low noise, 10 high
  noise, 11 first-and-only (not ground), 13/14/15 first/second/third of
  many, 16 last of many (not ground). So "ground" is class 2 only, and
  everything that is not 2/7/10 is a candidate for re-classification.
- **Boundary and tile index**: USGS legacy metadata, saved to
  `/Volumes/Nunatak/lidar_src/kenai_2008/usgs_metadata/`
  (`AK_Kenai_2008_Bounds_geo.shp`, one polygon, 11,740 km²;
  `AK_Kenai_2008_Tiles_geo.shp`, 3,161 tiles). Extent 151.87–148.85°W,
  59.59–61.04°N: the western lowlands at 4 ft posts and the upper Kenai
  River watershed at 10 ft.
- **Vertical reference**: NAVD88 per the project metadata; **Z units must
  be verified** from the points before anything else. The EPT's conforming Z
  range is −1,353 to 3,723, which reads as feet with noise (3,723 ft = 1,135
  m suits the upper watershed; 3,723 m does not exist there). Check against
  a lake surface (Skilak Lake ≈ 60 m NAVD88) and the 2019 Kachemak Bay lidar
  where they overlap.

## Disk

Nunatak has 472 GB free (2026-09-12, after today's rebuilds); Powder 393 GB.
Budget for the full job:

| item | size |
|---|---|
| EPT download (all nodes) | ~15–30 GB |
| Ground-classified LAZ per tile (working copy) | ~10–20 GB |
| DTM at 1.22 m (4 ft), 11,740 km², float32 ZSTD COG | ~10–15 GB |
| Point-density and residual rasters (8-bit / int16) | ~5 GB |
| Web pyramid + slope pyramid | ~2–3 GB |

Comfortable without freeing anything. If Hig frees space anyway, the win is
keeping the intermediate LAZ so the classification can be re-run.

## Tooling

`brew install pdal` (2.10.2 available). PDAL reads EPT directly with a
bounds filter, so the octree never has to be downloaded whole; but a local
copy is faster for a multi-pass job and lets the work run offline.
Everything else is GDAL, already in place.

## Pipeline, tile by tile (the 3,161 vendor tiles, or 2 km squares)

1. **Fetch** `readers.ept` with `bounds` = tile + 30 m buffer (so ground
   filtering near edges sees its neighbours), all classes except 7 and 10
   (noise). Write the tile's LAZ locally once.
2. **Verify Z units** on the first tiles (step 0 above). If feet, scale Z by
   0.3048 in a `filters.assign`/`filters.python` step and record it.
3. **Re-classify ground** rather than trusting class 2. Two candidates, run
   both on a few tiles and compare:
   - `filters.smrf` (Simple Morphological Filter, Pingel 2013): `cell`
     1.2 m, `slope` 0.15–0.2, `window` 18–30 m, `threshold` 0.45–0.6 m,
     `scalar` 1.25. Good on rolling terrain and wetlands, the Kenai
     lowlands' character.
   - `filters.pmf` (progressive morphological): comparable; keeps
     breaklines slightly better on bluffs.
   Keep the vendor class 2 as a third surface for the comparison.
4. **Grid** with `writers.gdal`: `resolution` 1.2192 (4 ft), `output_type`
   `idw` (or `mean`), `radius` ~1.7 m (√2 × cell), `window_size` 3–5 to fill
   one-cell holes only. Larger voids stay nodata: no interpolation across
   water or unflown gaps, consistent with the rest of the collection.
   Output float32, `gdal_translate -of COG` with ZSTD + predictor 3 as in
   `build_lidar.py`, in NAD83(2011) / UTM 5N (EPSG:6334) like the Kenai
   surveys already online.
5. **Companions**: a point-density raster (ground returns per cell) and, per
   tile, the residuals between the three ground surfaces. Density tells a
   viewer where the DTM is trustworthy; the residuals are how the
   classification choice gets made and defended.
6. **Merge** the tiles with `gdalbuildvrt` → translate, as for Glacier Bay,
   then the normal `build_lidar.py` stages (archive is already a COG:
   `archive_mode: translate`; web; slope).

## Making the best DTM: what to decide on the comparison tiles

The known defect in the vendor DTM (Hig): **sharp ridges between gullies are
cut off**, treated as vegetation or structures. That is the classic failure
of morphological ground filters with a fixed window and a low slope
tolerance: a narrow ridge is "an object smaller than the window that stands
above its surroundings", exactly the definition of not-ground. So the
parameter that matters most here is the filter's slope tolerance, and the
comparison tiles must include the gullied bluffs (Kenai River and Kasilof
bluffs, the Caribou Hills drainages), not just the flats:

- SMRF: raise `slope` (max terrain slope, as a ratio) toward 0.7–1.0 on
  the bluffs and keep `window` short there (12–18 m), so a ridge crest 5 m
  wide with 40 degree flanks is a legal surface. Run it slope-adaptive: a
  first pass with lowland parameters, a second pass restricted to cells
  whose first-pass slope exceeds ~15 degrees with the steep parameters, and
  take the union of ground points.
- PMF: same idea via `max_window_size` and `slope`; usually cuts ridges
  harder than SMRF at equal settings.
- CSF (cloth simulation, `filters.csf`) with high `rigidness` and small
  `resolution` follows crests better on steep ground and is worth a third
  candidate on the bluff tiles.
- Judge on the residual maps: where the 2019 Kachemak Bay QL1 surface
  exists, ridge-crest heights should agree to the noise level; elsewhere,
  compare crest continuity and the count of ground points on the crest.

- Where the vendor class 2 and SMRF disagree, look at the terrain: dense
  spruce and alder (vendor ground too high: vegetation kept), steep bluffs
  and gullies (SMRF too low or ground removed: relief clipped by the window),
  buildings and bridges (both), wetlands with floating vegetation.
- Tune `window` per terrain if needed; a two-pass approach (large window
  for the lowlands, smaller for the bluffs by slope class) is legitimate.
- The 2019 Kachemak Bay lidar (0.5 m, QL1) overlaps the south end: use it
  as truth for the accuracy numbers, on stable ground only.
- Hydro-flattening: not done. Lakes come out as the water-return surface
  with holes; honest, and consistent with the other surveys here.
- Publish the density raster alongside the DTM so the map can fade or
  hatch low-confidence ground.

## Order of work

1. `brew install pdal`; pull one lowland tile and one upper-watershed tile;
   settle Z units.
2. Run vendor-2 vs SMRF vs PMF on ~6 tiles spanning the terrain types;
   review residual maps with Hig; fix parameters.
3. Full run, tile-parallel (4 workers), overnight; then the standard build.

## First test patch, 2026-09-12 (Bluff Point / Diamond Ridge, 59.654 N 151.632 W)

Work folder `/Volumes/Nunatak/lidar_build/kenai_test/` (fetch.json, prep.json,
dtm_*.json, density.json, compare.py, comparison.png, bluff_crop.png).

- **Fetch**: `readers.ept` with a 2 km bounds straight from the remote
  octree, 7.8 M points, 403 s with 4 threads (16 threads tripped a
  truncated-tile read: keep it at 4, or read the local mirror once it is
  complete). Noise classes 7/10 dropped, reprojected 3857 -> 6334.
- **Z units are metres, NAVD88**: every surface lands within 3–5 cm median
  of Homer 2019 on stable ground. No scaling needed.
- **Density**: ~1.7 returns/m² here (writers.gdal `count` within a 1.75 m
  radius, median 16 per 1.22 m cell); 4 % of cells empty.
- **Results vs Homer 2019** (whole patch; real 2008->2019 change on the
  bluff face and beach dominates the tails):

  | surface | median dz | MAD | share of cells off by > 1 m |
  |---|---|---|---|
  | vendor class 2 | +0.03 | 0.16 | 8.4 % |
  | SMRF lowland (slope 0.2, window 18) | +0.04 | 0.21 | 10.0 % |
  | SMRF steep (slope 0.8, window 12) | +0.05 | 0.22 | 10.4 % |
  | CSF (rigidness 3) | +0.04 | 0.21 | 11.3 % |

- **On the gully crests** (2019 across-slope local maxima, 4,638 cells in a
  400-cell crop of the bluff): share of crest cells more than 1 m BELOW the
  2019 crest = vendor 9.9 %, SMRF lowland 10.0 %, **SMRF steep 5.6 %**, CSF
  9.0 %. Visually the vendor surface shows the stepped, truncated crests Hig
  described; SMRF (both) restores continuous crests; SMRF-steep keeps the
  crests best but lets small vegetation clumps through on the flats; CSF
  drops the ground in the steep gully bottoms entirely (holes) and is out.
- **Decision direction**: slope-adaptive SMRF, steep parameters where the
  first-pass slope exceeds ~15 degrees, lowland parameters elsewhere, union
  of ground points; then re-run the crest metric. Vendor class 2 remains
  the reference for "did we lose anything the vendor had".

## Survey of smarter ground classifiers (2026-09-12)

Constraint: Hig's machine is an Apple M1 Max, 64 GB, no CUDA. A 12-hour
CPU job is acceptable.

**Deep learning, state of the art but not usable here yet**
- OpenGF (Qin et al. 2021; github.com/Nathan-UW/OpenGF): 47 km², >500 M
  labelled points, 4 countries, 9 terrain types. Baselines on its test set:
  KPConv 96.1 % IoU / 0.20 m RMSE, PointNet++ 95.8 %, RandLA-Net 93.7 %,
  DGCNN 93.8 %, all beating the classical filters in the paper. **No
  trained weights released**; only RandLA-Net training code is linked.
  Training our own on OpenGF is a GPU-week, not a CPU-day.
- ALS foundation model (arXiv 2501.05095, github.com/martianxiu/ALS_pretraining):
  BEV-MAE pre-trained on USGS 3DEP tiles sampled by land cover and relief,
  downstream DALES segmentation. Weights "coming soon"; CUDA-only stack
  (spconv). Watch it.
- PTv3 on DALES (huggingface.co/jayakumarpujar/Ptv3): ground IoU 95.7 %,
  MIT, but Pointcept's serialised attention needs custom CUDA kernels, and
  DALES is urban Ohio at high density; domain mismatch for Kenai spruce
  and 1–2 pts/m². Not runnable here.
- Elevation-offset attention and Multi-KPConv papers (2023–2025): ideas,
  no released weights.

**Classical, better than what we have run so far**
- **Progressive TIN densification (Axelsson 2000)**: the lidR book's flat
  recommendation ("outperforms all others", 20 s on a mountainous tile vs
  156 s CSF / 1,800 s PMF), with terrain presets (steep mountains: `res
  10, angle 40, distance 3`). Available in R's lidR (`classify_ground(las,
  ptd(...))`) with buffered catalog processing of many tiles; not in PDAL.
  R is not installed (brew has 4.6.1). Known weakness: steep slopes and
  low objects, which is why the seed grid and angle matter.
- **MCC (Evans & Hudak 2007)**: built for forested terrain; retains ground
  under canopy well; lidR `mcc(1.5, 0.3)`, GRASS `v.lidar.mcc`, standalone
  MCC-LIDAR. Worth a run on the same patches.
- WhiteboxTools `lidar_segmentation_based_filter` and slope-based
  `lidar_ground_point_filter`: a third family (segment-then-classify) that
  handles ridges differently; pip `whitebox`.

**The route that fits this project: a supervised filter taught by the
newer lidar.** We hold two later, higher-quality surveys over the same
ground: Homer 2019 (0.5 m, 90 km²) and Kachemak Bay 2023 (0.5 m QL1,
1,160 km²), both inside the 2008 footprint. On stable ground the 2019/2023
DTM is the truth for the 2008 points, so:
1. Label 2008 points in the overlap: ground if within ±0.3 m of the newer
   DTM (excluding areas of known change: bluff faces, beaches, new roads
   and buildings, found by |vendor − newer| on the vendor DTM itself).
2. Compute per-point features with PDAL: `filters.covariancefeatures`
   (linearity, planarity, sphericity, verticality, omnivariance,
   anisotropy, eigenentropy, surface variation at 2–3 radii),
   `filters.hag_delaunay` / `hag_nn` (height above a coarse ground), return
   number / number of returns, intensity, local density
   (`filters.radialdensity`), and normal-Z. These are exactly the cues a
   morphological filter never sees: a ridge crest is planar-linear with
   returns all at the same height; a spruce canopy is spherical, multi-
   return, with a large height-above-ground.
3. Train gradient-boosted trees (scikit-learn 1.6 is installed; LightGBM
   is a pip away) on a few million labelled points across terrain and
   vegetation types; validate on held-out tiles with the crest metric.
4. Apply to all 14 B points tile by tile. Feature extraction is the cost:
   roughly 1–3 M points/minute on the M1 Max ⇒ 14 B points in ~4–8 days of
   CPU, or less with 8 workers. If that is too long, apply it only where the
   morphological filters disagree with each other (the uncertain 5–10 %).
This is the "smarter classifier" that is actually runnable here, and it is
trained on this survey's own terrain and vegetation rather than Ohio.

**Plan**: (a) install R + lidR, run PTD and MCC on the Bluff Point and
Woodard patches alongside SMRF, same crest metric; (b) prototype the
taught filter on the Woodard patch using Homer 2019 as teacher; (c) pick.

## Co-registration (added 2026-09-12)

Before labelling points against the 2019/2018 teachers, solve a horizontal +
vertical shift per ~500 m block (Nuth & Kaab: dz = -sx*gx - sy*gy + c on
sloping cells, 3 robust iterations) and apply it to the 2008 points. Even with
the archive datum bug fixed, 2008 sits ~0.35 m from 2019 and 2018, and on a
40 degree wall 0.35 m horizontal is 0.3 m vertical -- larger than the label
tolerance. Aspect-banded red/blue residuals on hillslopes are the signature
of an uncorrected shift (visible in the first review sheet, superseded).
Woodard Canyon (7.06 M pts) results after co-registration: at sharp crests
SMRF-steep median -0.04 m / 2.3% >1 m low; vendor -0.12 m / 9.7%; SMRF
-0.12 m / 7.2%; CSF -0.24 m / 22.5%. Whole patch |dz|>1 m: vendor 0.7%,
SMRF 0.9%, SMRF-steep 4.0% (buildings/veg retained), CSF 3.4%.

## Training and refining the taught ground filter (process agreed 2026-09-12)

**Labelled set**
1. Align first: after the datum-corrected homer_2019 archive lands, refit the
   2008->2019 shift per 500 m block (`kenai_coreg/shift_field.py`) and apply it
   to the 2008 POINTS (filters.transformation per block) before labelling.
   Residual ~0.35 m horizontal = ~0.3 m vertical on a 40 degree wall.
2. Sites, not points: start from the 8,776 automatic 100 m sites
   (`kenai_teacher/make_sites.py`). Tier A = 2018 and 2019 agree; tier B =
   2019 only. Flag 2008->2019 change (coherent blobs > few hundred m2,
   |dz| > 1 m, low texture) as change candidates. Hig vetoes from the sheet.
   Stratify toward where the model fails, not where the land is.
3. Asymmetric labels: |dz| <= tol -> ground; dz > ~0.5 m -> non-ground;
   dz < -tol -> UNLABELLED (2008 may have seen deeper through vegetation, or
   the teacher is wrong). tol = 0.15 m + 0.5 * residual_shift * tan(slope).
4. Vendor class is a FEATURE (available at inference), never a label; test
   with and without it.

**Features / model**
- PDAL per point: covariancefeatures at 1/3/8 m, height above coarse minimum
  surface, height above SMRF-steep and PMF provisional grounds (the key
  features), return number/count, intensity, density, normal, residual from
  2 m and 5 m local medians.
- sklearn HistGradientBoosting (installed) on 2-5 M points from 500-1000
  sites; leave-block-out validation, never random point splits.
- Score by the DTM built from predicted ground on held-out sites with the
  crest metric (`kenai_test_woodard/crest3.py`), baselines = vendor and
  SMRF-steep.

**Refinement loop** (~1 h CPU per round, 3-5 rounds)
1. Cluster worst held-out residuals: crests, gully floors, roofs, canopy,
   water edges. 2. Label wrong -> veto site / tighten rule; model wrong ->
   more sites from that stratum, new feature; worst sites go on a sheet for
   Hig each round. 3. Post-prediction consistency clean (isolated high
   ground points dropped, holes filled) removes SMRF-steep building leakage.
4. Stop when the crest metric moves less than the block-to-block spread.

**Production** (all 14 B points): 1 km tiles + 50 m buffer (~7 M pts each,
like Woodard), 1-2 min/tile -> ~2,000 tiles in 6-8 h with 8 workers.
Outputs: classified LAZ per tile (vendor class kept in an extra dim), 1 m
DTM COG -> new dataset via build_lidar, per-cell confidence raster.
Sanity outside the Homer footprint: per-tile ground fraction, difference
distribution vs the vendor DTM, the 2018 NCMP coastal strip.

**Known residual biases**: leaf state 2008 vs 2019 (asymmetric label is the
only guard); where 2019 itself cut a crest we would teach the same error
(2018 agreement + Hig's veto are the guard).

### Prototype result, Woodard Canyon, 2026-09-12 evening (`lidar_build/kenai_taught/`)
Features: `features.sh` (PDAL: hag above SMRF-steep / SMRF / 5 m minimum,
covariance shape at knn 12 and 48, radial density, intensity, returns).
Labels (`taught.py`): ground |dz| <= 0.15 + 0.15 tan(slope); non-ground
dz > 0.5 + tol; below -tol unlabelled -> 51.5 / 31.3 / 17.2 %. Vendor class 2
against these labels: precision 99.5 %, recall 62 %. HistGradientBoosting,
5-fold CV by 200 m block, OOF ground P/R 99.0 / 99.6. Top features: the three
height-above-provisional-ground dims; shape features add little.
Crest metric, all surfaces co-registered (`kenai_test_woodard/crest4.py`):
sharp crests median / >1 m low / >0.5 m high / |dz|>1 --
taught -0.06 m / 4.0 % / 3.2 % / 4.5 %; vendor -0.10 / 10.2 / 2.9 / 10.5;
SMRF-steep -0.03 / 1.4 / 5.6 / 3.5 (per-surface fitted shifts, `crest2.py`; PDAL rasters must share one grid -- `bounds` now pinned). Whole patch |dz|>1: taught 0.8 %,
vendor 0.8 %, SMRF-steep 4.0 %. First pass halves the vendor's crest loss
with no building leakage. Known slack: label tolerance admits low
vegetation (90 % of points 0.3-0.5 m above 2019 predicted ground); IDW
DTM recipe is a shared floor at crests; only one patch of training.

### Full-footprint checkerboard, run1 (2026-09-13 00:30-01:30, `kenai_taught/full/`)
60 train / 61 held-out 1 km tiles, 6.6 M labelled points from 4,048 sites (built-up capped 10 %, 3 vetoes,
3 promotes). Sharp crests, held-out: vendor -0.147 m / 12.1 % >1 m low / |dz|>1 2.6 % overall;
SMRF-steep -0.070 / 5.3 % / 4.1 %; taught -0.113 / 7.8 % / 2.7 %. Hybrids (`hybrid.py`): prob>0.3 -> 6.7 %;
taught OR (near SMRF-steep surface AND prob>0.15) -> 6.5 %, overall unchanged at 2.7 %. Verdict: teaching from
the 2019 DTM plateaus at ~half the vendor's crest loss; the remaining gap to SMRF-steep is the label truth
(2019 DTM keeps low vegetation) and IDW interpolation -> merged-cloud labels next (Hig's proposal).
Homer 2019 point cloud: many DGGS tiles are unfinalised LAZ (see `homer_2019_pc/NOTES.md`); 2018 NOAA
topobathy cloud complete (`lidar_src/homer_2018_ncmp/`, 89 COPC, flown 2018-06-09..12 and 08-21..22).
