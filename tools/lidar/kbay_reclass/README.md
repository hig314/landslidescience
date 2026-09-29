# KBay 2023/24 reclassification test patch

Started 2026-09-24. Goal: test ground-filter algorithms on the NOAA 2023/24
Kachemak Bay point cloud, calibrated against the Grewingk 2021 DGGS survey,
along the lines of the Kenai 2008 taught filter (tools/lidar/KENAI_2008_PLAN.md).

Patch: 1.5 x 1.5 km, EPSG:6334, centred on 59.57498, -151.16301
  (603766, 6605514) -> bounds x 603016..604516, y 6604764..6606264

Sources
- KBay: NOAA OCM dataset 10418 (points; DEM is 10419), NV5 Geospatial,
  flown 2023-08-01/02, 2024-06-15/17, 2024-09-21. EPT:
  https://noaa-nos-coastal-lidar-pds.s3.amazonaws.com/entwine/geoid12b/10418/ept.json
  EPSG:6334 + NAVD88 (5703, GEOID12B). 28.4 B points total.
  Report: .../laz/geoid12b/10418/supplemental/AK_Communities_2025_NIR_lidar_Report.pdf
- Grewingk: DGGS elevation portal project 295 (listed as "Grewingk 2019"),
  Classified Points dataset 1388. RDF 2023-2 says flown 2021-10-12 -- check
  the LAS headers to confirm which flight this is.

DTM agreement over the 3x3 km box (KBay minus Grewingk, full res):
  slope 0-5 +0.38 m, 5-15 +0.49, 15-30 +0.65, >30 +0.79 (medians).

## KBay patch (pulled 2026-09-25, 5 min from the EPT)
kbay_2024/kbay_patch.laz: 56,978,057 points (~25/m2), z 4..903 m.
ALL from one flight, 2023-08-01 (leaf-on). Classes: 1 unclassified 16.3 M
(29%), 2 ground 4.1 M (7%, ~1.8/m2), 5 vegetation 36.5 M (64%). No snow
(21), temporal exclusion (22), water (9) or noise here -- so this patch is a
pure under-canopy ground-filter test. Lake islands / snow need another patch.

NV5 report (AK_Communities_2025_NIR_lidar_Report.pdf, here): TerraScan
ground; hydroflattening reclassified ground INSIDE water polygons to class 9
(why islands in lakes vanish); class 21 snow, 22 temporal exclusion (flight
lines disagreeing across 2023-08, 2024-06, 2024-09) left out of the ground
model. Snow polygons are a listed deliverable but not on the NOAA bucket.

## Grewingk points
DGGS RDF 2023-2, one 21 GB zip (rdf2023_002_classified_points.zip) of 263
LAZ tiles. Server honours Range but throttles ~175 KB/s PER CONNECTION:
zip_ranges.py reads the central directory, fetch_one.sh pulls each member on
its own connection and verifies CRC. 13 tiles (1.2 GB) cover the patch
(tiles.txt/ranges.txt). NB the elevation portal's "Grewingk 2019" (project
295, dataset 1388) is a DIFFERENT, 2019-11 survey that barely reaches here.

LESSON (2026-09-25): never pipe point data to stdout in a background task --
the captured output lands on the boot disk and filled it.

## First comparison (2026-09-25) -- grids/ at 1 m, ground class only
Grewingk patch: grewingk_2021/grewingk_patch.laz, 74.4 M pts (~33/m2), flown
2021-10-12 (near leaf-off). Classes 1 7.0 M, 2 8.3 M, 3/4/5 veg 43 M, 18 16.1 M
= cloud/fog returns 80-550 m above ground (harmless, ignore).
Ground density: median ground pts per occupied 1 m cell KBay 7, Grewingk 18.

OFFSET: on cells bare in BOTH surveys (canopy <0.5 m) KBay - Grewingk =
+0.35..+0.38 m at every slope class (IQR ~0.05) -> systematic, not change.
Use 0.37 m. Horizontal shift fit ~0.2 m (x -0.09, y +0.18) -- negligible
for now. Which survey is "right" vs NAVD88 is not settled here.

After removing 0.37 m, KBay ground minus Grewingk ground by KBay canopy height:
  canopy 0-0.5 m   median  0.00  p90 +0.09
  canopy 0.5-2 m   median +0.09  p90 +0.91
  canopy 2-5 m     median +0.46  p90 +1.05   (21% of cells; alder?)
  canopy 5-10 m    median +0.37  p90 +0.95   (39% of cells)
  canopy >10 m     median +0.15  p90 +0.85
KBay has no ground point in 7.6% of cells (Grewingk has ground in 98% of them).
=> KBay ground rides high under mid-height canopy; Grewingk is a usable
   teacher there, once offset-corrected.

## Filter trials and review products (2026-09-25)
score.py scores a DTM (dtm/<name>.tif) against Grewingk (-0.37 m) by canopy band.
Tried on the patch: SMRF Kenai-steep, SMRF tight, SMRF scalar 0.1, SMRF
scalar 0 (thr 0.15; window 6), CSF, lower-envelope band. ALL are no better
than the vendor ground; generic SMRF/CSF are much worse (+0.6..0.85 m under
2-10 m canopy vs vendor +0.34..0.38).
Point-level check (reclass/kbay_hag.laz, HAG vs Grewingk+0.37): where vendor
ground is >0.5 m high, the LOWEST KBay return is itself +0.53 m (slope <15),
+0.37 m (30-45 deg). The Aug 2023 pulses did not reach the Oct 2021 surface:
leaf-on understory. Not a classification problem -- a correction would have
to be MODELLED (taught from Grewingk), not filtered.
review/: hillshades (multidirectional, z 2) of grewingk_2021, kbay_vendor,
kbay_envelope; diff_*_minus_grewingk(.tif float, _color.tif ramp +/-1.5 m);
overview.jpg. The envelope version breaks on steep cliffs (holes >30 m) --
a failed attempt, kept for the record.

## Option 2 groundwork (2026-09-25) -- model/
labels.py: point dz vs Grewingk+0.37, BILINEAR at the point (per-cell lookup errs
~0.5*tan(slope)). Label tolerance 0.20 + 0.25*tan(slope).
Vendor: 8.3% of ground points are false (>0.5 m + tol above); 2.87 M non-ground
points sit ON the teacher surface vs 2.31 M true ground it kept -- missed ground
dominates on crests (68% missed) and slopes >45 deg (64-76%).
features.py: KBay-only features. Slope-aware low reference h_low3/h_low7 (lowest
point per cell in a gradient-detrended frame, neighbours projected along the
gradient) -- the fix for the envelope's steep-slope holes.
train_eval.py: HistGradientBoosting, 3-fold spatial CV on 500 m blocks.
  p>=0.85: falseG 3.2% (vendor 8.3%), true-ground pts 3.76 M (2.31 M).
  DTM (score.py): cells >0.5 m high 25.8% (vendor 30.8%), low 3.0% (3.5%).
  crests: too-low cells 28.0% -> 21.9%; slopes >60: 37.7% -> 27.8%. No holes.
  Channels/gullies unchanged (~37% high). Tall forest slightly worse.
Gap check: cells with no confident ground within 3 m = 0.7% of area and hold
0.4% of the high cells -> the residual bias is NOT in gaps, it is a continuous
layer of real returns ~0.3-0.5 m up (herb layer). Sparse gap-infill can't reach it.
herb_test.py: among accepted points, herb vs ground AUC 0.75-0.83 on held-out
blocks, driven by slope/canopy/curvature (context), not point physics.

## Island / hydroflattening test patch (2026-09-25) -- island/
Hig: island clearly in the points but hydroflattened out, 59.53698 -151.19699
(601961, 6601231). 1x1 km KBay pull: 18.3 M pts, all 2023-08-01.
Classes: 1 4.85 M, 2 0.87 M, 5 12.0 M, 6 3.4 k, 9 water 509 k, 20 ignored-ground 1.6 k.
Lake surface is flat in the water returns: median 50.48 m (IQR 50.47-50.49).
~7,000 class-9 points >0.5 m above it; one coherent cluster of ~1,800 m2 up to
~5 m = the island (Grewingk 2021 DTM shows it; NOAA DEM erased it). Also a
yellow fringe of class-9 points 0-1 m above the lake along most of the shore:
NV5's water polygon overreaches onto land -> shorelines can be sharpened too.
Large parts of the lake returned nothing (normal) -> leave as nodata, not flat.
island_overview.jpg.

## Expanded training sites (2026-09-25) -- sites/
sites/overview.py: 10 m canopy (Grewingk DSM - DTM), slope, and a stability
mask |KBay-0.37-Grewingk|<3 m (drops glacier ice / change) + hydroflat mask.
60 km2 of stable overlap; 302 candidate 1 km windows.
TRAP (again): gdalwarp of the KBay COG (compound NAVD88 CRS) to plain
EPSG:6334 applies a geoid shift -> KBay read ~10 m high. Always pass
-s_srs EPSG:6334 for it. Also the Grewingk rasters' zeroed-TM CRS makes
rasterio.reproject return nothing; gdalwarp -s_srs EPSG:6334 works.
Sites (1 km, centre E/N): forest_tall 604050/6602650 (41% canopy >=12 m),
alder 604300/6599650 (67% canopy 2-6 m), meadow_shrub 606050/6607650,
bare_gentle 603050/6606900 (60% bare <15 deg). Steep open ground is rare in
the stable overlap (best window 6%) -- patch 1 carries the cliffs. The island
patch is also a training site. sites_map.jpg. 37 more Grewingk tiles (3.1 GB).

## Six-site, leave-one-site-out results (2026-09-25) -- site/, model/
model/sitekit.py prepares a site (clip, TIN DTMs, offset, labels, KBay-only
features). OFFSET varies by site when measured strictly (bare in BOTH surveys,
canopy <0.2 m each, slope <20): patch1 0.36, bare 0.363, island 0.337,
alder 0.326, forest 0.254, meadow 0.178 (IQR .12-.24, n 51k) -> per-site
offsets (site/offsets_strict.json); a constant would mislabel meadow ground.
model/loso.py: train on 5 sites, score the 6th. Pure model ground (p>=0.85)
is mixed: alder much better (20.1 -> 11.5% cells >0.5 m high), patch1 better,
forest slightly worse, others ~equal. Promotion of veg points hurts overall.
model/edit_rules.py: DROP50 = vendor ground minus points with p<0.5.
  Improves EVERY held-out site, never worse (cells >0.5 m high):
  patch1 31.6->30.6, forest 17.2->16.5, alder 20.1->17.0, meadow 24.4->23.4,
  bare 3.8->3.7, island 12.2->11.5. Point false-ground e.g. alder 6.3->3.4%.
  Demotes ~7-9% of vendor ground; demoted cells are widespread (alder 11.9%).
Crest diagnosis: on crests where the vendor DTM is >0.5 m low, 42-52% of cells
hold a real KBay return AT the Grewingk surface that NV5 called non-ground; the
vendor TIN chords across from the flanks. model/crest_fill.py promotes only:
cell has no vendor ground, 0.3 < h_vendor < 3, h_low3 < 0.15, p >= 0.5.
  Sparse (150-2,600 pts per site, 0.01% of cells at alder), 53-86% correct
  (bare_gentle only 28% -- keep conservative); crest too-low cells drop 2-4
  points (patch1 27.7->25.7, forest 30.6->26.9, island 26.1->23.5).
Recommended stage-1 rule so far: drop50 + crest fill (p>=0.5).

## Hydroflattening repair (island) -- model/hydro.py
Lake level from class-9 returns (50.48). Class-9 points >0.15 m above it are
released (9,113), the ground model keeps 7,842 as ground; class 20 restored.
Island + shoreline cells (4,755): vendor ground covers 8%, repair 99.6%,
median vs Grewingk +0.13 m, 84% within 0.5 m.
Two outputs: dtm_hydro_noflat.tif (open water = nodata) and
dtm_hydro_reflat.tif (lake = measured level; lake = connected empty region
containing water returns, bounded by land from the points).
review/island_repair.jpg, island_reflat.jpg, alder_edits.jpg.

## Certainty-weighted surface in dense-veg zones (2026-09-25) -- Hig's direction
Hig: NO re-hydroflattening -- water surface = water returns (rivers; avoids
lakeshore embankments). Inferred low ground only in strong cases; no fabricated
points; instead low-certainty points + heavier smoothing, or down-weight true
returns in dense veg so sparse low returns pull the surface down.
Premise (model/lowpoint_potential.py): in too-high cells, a real return within
0.25 m of truth exists within 3 m in 39-66% of cells, within 5 m in 65-90%.
model/cellzone.py: LOSO cell classifier P(surface >0.5 m high), KBay-only cell
features (slope-aware gap of lowest nearby return below the TIN, ground counts,
distance to ground, canopy, near-ground fraction). P>=0.7 precision: patch1
80%, meadow 72%, forest 55%, alder 54%, island 43%, bare 38%.
model/surface.py: 1 m grid, weighted least squares + second-difference
smoothing, IRLS. Inside zones: points ABOVE the surface weighted tau=0.15,
local-low non-ground candidates (h_low3<0.3) weight 0.3, far-below (>1.5 m)
x0.1 (no pits), smoothing 2.0 vs 0.005 outside. Alone (no zones) it is 1-2.5
points WORSE than the TIN, so it is only used inside zones:
model/composite.py: TIN of true returns (drop50 + crest fill + water returns,
no flattening) everywhere; solver inside zones, feathered 3 m.
Cells >0.5 m high, whole site: vendor -> final_tin -> composite z70d1
  patch1 31.6 -> 30.8 -> 30.1 | forest 17.2 -> 16.7 -> 16.4 | alder 20.1 ->
  16.9 -> 13.3 | meadow 24.4 -> 23.5 -> 23.2 | bare 3.8 -> 3.9 -> 3.9 |
  island 12.2 -> 11.8 -> 11.2. Too-low rises <=0.4 points.
Inside zones only: high cells -2..-10 points, low +0.3..+0.8, median -0.05..-0.08 m.
Zone area (z70d1): patch1 9.8%, forest 6.1, alder 39.5, meadow 9.6, bare 1.3, island 7.1.
review/patch1_zones.jpg, alder_zones.jpg, river_water_surface.jpg.

## Looser weighting sweep + dev viewer (2026-09-25)
model/sweep.py (zone_p:dilate:tau:cand_w:lam_in). In-zone too-high cells:
  a 0.7/1/0.05/1.0/2   zones 1-10% (alder 40%): patch1 58->48, alder 27->14
  b 0.7/1/0.02/1.0/5   same zones: patch1 58->43, alder 27->10, island 27->17; low +1..2 pts
  c 0.8/1/0.02/1.0/5   zones 1-4% (alder 17%)
  d 0.9/...            zones <0.5% (alder 2%): gains small
  e 0.8/1/0.0/1.0/5    zones 1-4% (alder 17%), above-surface points IGNORED in zones:
                       patch1 67->53, forest 33->19, alder 33->14, meadow 55->47,
                       island 33->21; too-low +0.1..1.5 pts. Hig's "tiny subset,
                       strong effect" -> e is the default candidate; b for contrast.
Even inside confident zones about half of patch1's cells stay too high: the low
returns there are not close enough.
Dev viewer: model/viewer_build.py <tag> -> <repo>/data/lidar_dev/{pmtiles,catalog.geojson}
(OUTSIDE data/lidar/, which the redeploy rsync copies to prod), served by
lidar_serve.dev_catalog/dev_pmtiles (404 unless DEBUG); open /lidar/?catalog=dev.
Per site: rc_<site>_grewingk (raised by the site offset; catalogue year 2023 on
purpose so differences are plain metres), rc_<site>_vendor, rc_<site>_final_e,
rc_<site>_final_b. Sites: patch1, alder, island, bare_gentle.

## Hig's review of the viewer (2026-09-25) and what the data says
- Lake shore-parallel band: NOT lake-bed returns (0 of 509k water returns lie
  >0.15 m below the surface; the final surface is flat at the level +-0.02 m
  everywhere on the lake). Grewingk 2021 lake level 49.78 m (DGGS DTM flat
  value) vs KBay 2023 50.48 m: the lake stood 0.70 m lower in Oct 2021, so a
  strip of shore is beach in 2021 and water in 2023 -> real band in any diff.
  Keep water handling as is (Hig).
- Offset: island measured 0.34 (739 bare cells, many on that shore strip);
  >40 m from water 0.27 (n 234). Within-site 3x3 blocks vary: island .19-.53,
  alder .18-.62, patch1 .04-.41 -> a per-site constant is the wrong model;
  needs a smooth offset field. Hig saw a ~0.34 m shift at the island in mod5.
- Outwash lumps: the crest fill (72% of its 610 promotions there are veg).
  canopy 0.5-2 m cells too high 26% in vendor AND ours -- untouched.
- sitekit.flat_mask also flags NODATA as flat (max/min of a constant fill);
  harmless so far (nodata is excluded anyway) but fix before reuse.

## The four changes together (2026-09-25) -- model/v2.py, v2_build.py, viewer
1. OFFSET FIELD (v2.offset_field -> site/<s>/offset_field.tif): island 0.20-0.23
   (was 0.34 constant -- Hig was right), patch1 0.19-0.39, meadow 0.12-0.26,
   outwash 0.32-0.44. Residual IQR on bare cells shrinks where support is good
   (meadow .122->.085, patch1 .073->.056). Every stage now uses it
   (sitekit.offset_grid): labels, loso.score, cellzone, sweep.
2. Water unchanged (returns = surface).  3. Crest fill removed everywhere.
4. Surface-built ground (skeleton / quadratic lump prune / densify): FAILED on
   held-out sites. Densify adds nothing. Pruning removes brush blankets but cuts
   ridges (crest too-low 30-50% vs vendor 16-31%). Restoring connected runs
   restores nearly everything. Forcing a candidate into every 2 m cell picks
   understory under tall forest/outwash shrub: v2c too-high forest 30 (vendor
   16), island 34 (19), outwash 10 (3.4). "Zero ground over a large area is
   unlikely" does not hold under this canopy. v2a kept in the viewer only for
   judging crests/lumps by eye.
BEST (= drop50 + water surface + zones e, on the field, no crest fill), held out:
  cells >0.5 m high vendor -> best: patch1 31.8->30.4, forest 15.8->14.5,
  alder 19.2->13.9, meadow 23.1->21.6, outwash 3.4->3.3, island 18.7->17.8;
  too-low +0.1..0.5 pts. Zones (P>=0.8, dilated 1) 0.1-16% of sites.
Viewer (fresh each build, old entries deleted): rc_<site>_{grewingk,vendor,best,v2a}
for patch1, alder, island, bare_gentle.

## Best <-> v2a blend (2026-09-25) -- model/blend.py, best_strength.py
Hig: v2a resolves real highs (summit 59.52435 -151.15522: Grewingk 454.48, v2a
454.40, vendor 452.20, Best 452.20 -- truncated 2.3 m) but let a lone shrub top
through as a cone (59.52474 -151.15386: v2a 438.4 vs Grewingk 433.4).
Cause: the quadratic test needs >= 8 alive neighbours; a survivor among pruned
cells was never tested. Fix: such points are tested against the median of alive
points within 4 cells, removed if > tol+0.5 m above or with < 3 to compare.
Best stronger: drop vendor ground where p < 0.9 (was 0.5): cells >0.5 m high
patch1 30.4->24.0, alder 13.9->8.9, island 17.8->13.8, forest 14.5->11.4,
meadow 21.6->18.1; too-low +0.2..1.9 pts; removes 21-52% of vendor ground in veg.
Blend = Best90 + w (v2a_fix - Best90), w from a LOSO classifier on KBay-only cell
features (returns within 0.15 m of each surface, spread of the 5 lowest returns,
skeleton support, brush, slope, curvature, roughness), smoothed 3 m.
Held out (high/low): patch1 24.6/4.6 (best90 24.0/5.0), forest 11.9/2.0 (11.4/2.4),
alder 8.7/3.0 (8.9/3.9), meadow 18.4/4.4 (18.1/4.8), outwash 3.0/1.4, island
14.2/3.5 (13.8/3.8). Summit 454.37 (w .99), cone gone (432.90, w .07).
CAUTION: the 'crest' score takes Grewingk's convex spots as truth, including
Grewingk's own vegetation lumps, so it penalises removing them -- do not trust it.
Viewer: rc_<site>_{grewingk,vendor,best,v2a,blend}.

## Open steep ground (2026-09-25) -- model/surfacefit.py, open_learned.py, open_floor.py
Hig: blend is weak on open, steep, highly curved terrain; asked whether a self-consistent but
highly variable surface can be told apart from canopy chaos.
Diagnosis (bare in both, slope>30): every method sits too LOW there; patch1 curved: vendor 18%,
Best90 27%, blend 31%, v2a 51% of cells >0.5 m low.
Discriminator (surfacefit.py): plane fit to ALL returns in each 1 m cell. Open ground: RMS
1.5-4 cm at any slope, 100% within 0.1 m, 100% single returns. Brush: RMS 0.65-1.5 m, 4-12%
within 0.1 m, 32-62% single. Intensity does NOT separate open ground from herb mats; herb/fern
mats are tight too (RMS ~4.5 cm vs ~2.5 cm) and sit inside brush (share of non-open cells in
7x7 m ~0.65 vs ~0-0.3).
Replacing the blend by a TIN of tight-surface returns fixes open steep but lets mats through
(alder high 8.7 -> 16.1). Learned mat-vs-ground (open_learned) is safe but timid.
Remaining open-steep 'too low' cells: KBay's own returns are ~0.53 m below Grewingk there
(42-55 deg faces; 0.2-0.35 m horizontal misregistration is enough) -- partly NOT fixable by
reclass; but the blend sat a further ~0.4-0.5 m below the returns.
FLOOR (open_floor.py 0.9 0.75 30): on slope >= 30, cells with >= 90% single pulses and >= 75%
of returns within 0.10 m of the cell plane (n >= 6): surface >= plane - 0.10 m. Only raises.
Open steep too-low: patch1 14.5 -> 8.9 (vendor 9.6), meadow 14.8 -> 11.6 (11.5), island
12.1 -> 9.8, alder 28.9 -> 23.0 (18.3). Overall too-high +0.1..0.6 pts. Raises 0.2-5.5% of cells.
Viewer: rc_<site>_{grewingk,vendor,blend,floor} (Best and v2a dropped from the list).

## Hig's tree + steep crags (2026-09-26)
Tree 59.58686 -151.17293 (outwash site): NOT in the vendor surface -- created by v2a and
trusted by the blend (w 0.97). A 10 m crown, 3-4 m up: 140 returns within 1.5 m, all class 5,
lowest 10.21 m vs ground 7.1 m; 93% single pulses, cell plane RMS 4 cm -- it passes every
tightness test. v2a kept interior crown cells (their neighbours were crown too).
Wall-drop removal: no effect (the pruned rim leaves no surviving neighbour within reach).
Shape features (protrusion / rim slope / area): do not separate it from Hig's summit (the v2a
TIN smooths the crown's walls). What separates them: skeleton SUPPORT -- summit 25/25 cells
alive, crown 11/25. Rule in blend.py: v2a may raise > 0.5 m above Best only where >= 70% of
5x5 skeleton cells are alive (ALIVE_MIN). Tree 9.90 -> 6.96 (truth 7.13); summit kept 454.38;
cone still gone. Too-high falls at 5 of 6 sites (patch1 24.6 -> 23.3, island 14.2 -> 13.2),
too-low +0.1..0.8 pts.
Floor (open_floor.py) REMOVED: pimply per-cell steps, no real fix (Hig).
Co-registration (coreg.py): horizontal shifts 0.02-0.25 m; correcting them changes open-steep
'too low' by only +-2 pts -> the ~0.5 m KBay-below-Grewingk on steep open faces is NOT
misregistration (earlier claim withdrawn). Real change, Grewingk crag vegetation, or look
angle -- unresolved.
Viewer: rc_<site>_{grewingk,vendor,blend}.

## Hig's spots, round 3 (2026-09-26)
- Crag 59.57281 -151.15884 (patch1, 57 deg barren rock): Best's p<0.9 trimming dropped correct
  vendor ground (the ground model scores crag returns like brush: 53% single pulses, 0.77 m
  plane scatter). Not the zones (none there). The 5 m-smoothed slope read the crag gentler
  (39-63 deg vs 57 at 1 m, 64 max-3). best_slope.py: drop threshold 0.9 up to 30 deg -> 0.5 at
  45 deg, slope = 1 m vendor-TIN slope max-filtered 3x3. Crag: blend 643.16 (truth 643.42,
  vendor 643.41); LO=0 (keep all vendor ground on steep) matches the vendor exactly but costs
  ~1 pt of too-high at vegetated sites -- LO 0.5 kept.
- Summit shoulder 59.52441 -151.15532: the per-cell support rule zeroed v2a on a shoulder with
  cell support 0.66 inside a raised area averaging 0.88. Rule now per RAISED AREA (mean alive5
  >= 0.7): shoulder 452.19 (truth 452.18); tree area mean 0.38 still rejected.
- 59.50236 -151.02577 ('vendor did poorly'): OUTSIDE Grewingk's footprint -> site vendor_poor,
  model/apply.py (all models trained on the six Grewingk sites; no scores). The point is in a
  lake. NOAA's DEM flattens it at 114.90 vs water returns 115.07 (p5-p95 115.03-115.11); ours
  keeps the returns and shows a few raised features near the north shore (rocks/islands?).
  On land blend and vendor TIN differ by > 0.5 m in 0.4% of cells. review/vendor_poor*.jpg.
Held-out (high/low): patch1 25.8/4.2, forest 12.3/2.1, alder 10.0/2.8, meadow 18.5/4.6,
outwash 2.9/1.4, island 14.2/3.6 (vendor 31.8/3.3, 15.8/1.7, 19.2/2.0, 23.1/4.1, 3.4/1.2, 18.7/3.1).

## Upper Woz boulder pile (2026-09-26) -- apply.py woz_boulders, rock.py
Hig: 59.50074 -151.00479, a landslide boulder deposit the vendor classification struggled with
(outside Grewingk; all models trained on the six Grewingk sites).
First pass: blend 9.2% of cells > 0.5 m BELOW the vendor in the 80 m around it. Those cells were
bare rock (100% single returns, 0.2 m plane scatter); in 41% the blend sat > 0.3 m below EVERY
return. Best's ground model scored boulder tops like shrubs (drops 67% of vendor ground);
v2a pruned boulders as lumps (2.1 m below every return).
Geometry cannot separate boulders from dense leaf-on single-return shrub (rough, texture at
0.5 m, context -- all fail on the Grewingk sites). Fixes:
- best_slope.py KEEP_SINGLE: never drop vendor ground in >= 95% single-return cells (no
  layering evidence). Cost: +0.1..0.5 pts too-high (meadow +1.6).
- blend.guard(): v2a may not LOWER Best by > 0.5 m in single-return cells (mirror of the
  raise/support rule). Boulder field then matches the vendor (91 vs 87 cut cells).
- rock.py: INTENSITY separates rock from leaves here (boulder cells median ~34k; vegetation on
  every Grewingk site ~54k). Rock cell = single >= 0.95, 15 m context >= 0.9 single, mean
  intensity < 45k, vendor TIN > 0.3 m below every return, patch >= 4 cells -> every return is
  ground. Fires 0-422 cells on Grewingk sites (scores +-0.1), 4,826 at the boulder pile; cut
  cells in the 80 m window 87 (vendor) -> 72.
Final (dtm_final = blend + rock), held out: patch1 26.3/4.1, forest 12.1/2.1, alder 9.9/2.8,
meadow 19.5/4.4, outwash 2.9/1.4, island 14.5/3.5. Spots: crag 643.18 (truth 643.42), summit
454.39, shoulder 452.17, tree gone, cone gone.
Note: a blend.py run died silently once (no traceback, no disk shortage) -- rerun completed.
Viewer: rc_<site>_{grewingk,vendor,blend=dtm_final} + rc_woz_boulders_{vendor,blend}.

## Viewer: basemaps, lidar opacity, 3D collar (2026-09-26)
- /lidar/ basemap dropdown from basemaps.js (+ shared QMS), a single bottom raster layer.
- 'op' is now LIDAR opacity: always shown (was hidden without an ortho, and reset to 1.00),
  scales shading, drape, vegetation height and difference together.
- 3D 'flattened area around the patches': a flat 0 m collar inside every tile the survey only
  partly covers. Two causes: (1) the dev catalogue lacked `context`, so the page fell back to
  the live 3DEP ImageServer -- now copied from the public catalogue (ctx_3dep); (2) the default
  fill_mode 'missing' fills only whole missing tiles, not empty pixels inside a partial tile --
  exactly the Corax small-survey case in datasets.json. Dev surfaces now fill_mode 'holes'
  (safe here: water is carried as water returns, not voids). Edge tile 700 m W of alder:
  60.5% at 0 m -> 0%, heights 370-637 m.
- Docker file sharing: after the atomic rename the web process got a steady 404 until the
  directory was listed; lidar_serve.dev_catalog now lists and retries once.

## Cliffs shown as tall vegetation (2026-09-26) -- rock.py cliff variant, viewer veg measure
Hig: bare-rock cliff 59.49864 -151.00824 (woz site; 64 deg median, up to 88) showed tall
vegetation in vendor and blend. Two causes:
1. CLASSIFICATION: the vendor called most of the face class 5/1 and the surfaces ran along the
   base. rock.py's 'vendor below every return' never fires on a face (a cell's lowest return is
   at the base). CLIFF variant: slope >= 45 (1 m, max 3x3), >= 95% single, 15 m context >= 0.9,
   intensity < 45k -> every return is ground. Grewingk check: all-returns plane within 0.5 m of
   truth in 93-97% of such cells (patch1 4,140, meadow 36,942); scores unchanged (meadow
   19.4/4.5 -> 19.6/4.3). ~224k cells at the woz site.
2. DISPLAY: a 1 m raster holds one height per cell; on a 60-88 deg face rock returns in the
   upper part of a cell sit metres above it. Vegetation height is now per RETURN (height above
   the surface at the return's own xy, cell max), compared with the 3x3 max of the surface on
   ground >= 45 deg only (elsewhere unchanged -- the envelope everywhere cut alder's > 2 m
   share from 74% to 64%). Cliff 17x17 m, cells > 2 m: vendor 52% -> 38%, final 47% -> 12%.

## Cliff crenulations (2026-09-26) -- toltin.py, rock.py STEPFIX
Hig: the cliff result showed tight crenulations; asked whether horizontal position error on steep
faces is to blame, and for coarse-to-fine TIN refinement (as discussed for Kenai 2008).
- toltin.py: tolerance TIN. Seeds = median-height point per 16 m cell; levels 8/4/2/1 m; per cell
  a point is inserted only if >= 3 returns exceed eps (0.15 m) on the same side, measured
  PERPENDICULAR to the facet (|dz| cos slope), and the MEDIAN of those is inserted (greedy
  'worst point' chases outliers). Rock roughness (|laplacian| p90) -15% (patch1 1.52 -> 1.35,
  meadow 1.67 -> 1.45, woz 3.10 -> 2.60), accuracy unchanged. eps 0.3: no further gain.
  (ptd_ground -- Axelsson PTD from the Kenai work -- is a ground CLASSIFIER, not a surface builder;
  its angle test is weak on steep slopes.)
- The visible artefact was a CHECKERBOARD along every cliff lip, from two sources: (1) top and
  base returns interleave horizontally across the lip, so a 1 m 2.5D surface flips level cell by
  cell; (2) the rock mask fires in only ~3/4 of lip cells, so rock/blend alternated per cell.
  Fixes: close the mask's gaps + smooth fade (gaussian 1 m) instead of a per-cell switch, and a
  3x3 median on steep rock cells (STEPFIX=median3). Rock roughness p90: patch1 1.08 (Grewingk
  1.19), meadow 1.16 (1.28), woz 1.80 (from 3.10). Accuracy within 0.5 m: patch1 97 -> 94%,
  meadow 92 -> 89%. Gaussian smoothing (0.7/1.0 m) was smoother still but less accurate and
  still dashed (mask alternation). review/cliff_*.jpg.
Scores: meadow final 20.1/4.2 (blend 19.4/4.5); others unchanged.

## Breaklines + run-time estimate (2026-09-26)
Breaklines (rock.add_breaklines, toltin force=): lip/base = edge cells of steep (>= 45 deg, on the
surface smoothed 1.5 m) bands; lip if the plateau beside it is higher, base if lower; vertex =
highest (lip) / lowest (base) return per cell, forced into the tolerance TIN (soft breaklines --
no constrained-Delaunay library; Triangle is non-commercial and not installed). Effect small:
rock roughness p90 patch1 1.28 -> 1.26, woz 2.32 -> 2.34, accuracy kept (97 / 91%). The
remaining sawtooth is WITHIN the face (returns interleave across all of it), not bridging of lip
and base; the 3x3 median still does the cleaning (with it: 1.07 / 1.79, accuracy 94 / 89%).
A real fix would fit the face as a surface (oriented along it) rather than interpolate.
TIMING (apply.py, woz_boulders, 1 km2, 20.06 M points, models cached; timing.json):
  vendor TIN + max 14 s | export + features 20 | ground model + drop50 58 | zones 2 |
  zone solver + Best 30 | v2a + blend 43 | rock + cliff (toltin + breaklines) 96 (worst case:
  a fifth of the site is steep rock) | TOTAL 262 s = 13.0 s per million points. Peak RSS 7.7 GB.
  One-off training: ground 86 s, zone 33 s, blend 363 s.
FULL SURVEY (28.4 B points, ~1,160 km2): ~103 h single process; with 1 km tiles + ~60 m
buffers (+25%) ~129 h single, ~21 h on 6 workers (memory-bound: 7.7 GB each in 64 GB).
Download at the measured 0.19 M pts/s: ~42 h single stream, ~7 h with 6 (or mirror the S3 LAZ
tiles directly). Raw LAZ ~171 GB vs 115 GB free on Nunatak -> stream: fetch, process, delete.

## Flight-line check + face-frame fit (2026-09-26) -- flightlines.py, facefit.py
Flight lines (PointSourceId; 6 per site) on steep rock: scatter across the face (PCA plane per
1 m cell, distance along the normal) all lines 9.7 cm vs within one line 9.3 cm (woz, 98,880
cells); patch1 5.5 vs 5.0. Line-pair median differences ~3 cm; best 3-D shift per pair < 1 cm at
woz (explains ~0%), 3-6 cm at patch1 (14-26%). => NOT misregistration. The returns are good
(~10 cm across the face); the crenulations are made by gridding: 10 cm across a 70-80 deg face
is 30-60 cm vertically, and a surface through individual returns sampled at 1 m jumps that much.
facefit.py: per cell, PCA plane of the 3x3-cell neighbourhood (perpendicular least squares),
one robust pass (drop returns > 2.5 x 0.10 m off), height = vertical line through the cell centre
meets the plane; |n_z| < 0.1 -> neighbourhood median. STEPFIX default now 'facefit'.
Rock cells, roughness p90 / within 0.5 m of Grewingk:
  patch1 TIN 1.26/97%, median3 1.07/94%, FACEFIT 0.67/96% (Grewingk itself 1.19)
  meadow TIN 1.34/91%, median3 1.17/89%, FACEFIT 0.66/90% (1.28)
  woz    TIN 2.34,     median3 1.79,     FACEFIT 1.22
Rock step 45-108 s (similar to before). Visually: the cliff face is continuous, checkerboard
gone; rocky slopes look softer (3 m plane rounds sub-3 m texture) -- a smaller neighbourhood or a
quadratic in the face frame would keep more. review/cliff_facefit.jpg.

## Curved face fit + cliff-top holes (2026-09-26)
Holes just in from cliff tops (Hig): the plane fit ran on steep cells + a 1-cell margin; a plateau
cell just back from a lip got a plane tilted by the face in its neighbourhood and sank below the
plateau. Fixes: use the fit only where the cell is steep AND its fitted plane is > 35 deg, feathered;
single-cell pit guard (> 0.3 m below all 8 neighbours -> their median); small closed depressions
(<= 6 cells, >= 0.3 m, in/next to the cliff-fit region) filled to spill level (skimage
reconstruction). Closed single-cell pits at woz: plane fit 462 -> 130 -> 41 (vendor 446); closed
depressions within 45 m of Hig's cliff point: 11 cells -> 0.
Curved fit (facefit.face_fit_quad, CURVED=1 default): quadratic w(u, v) in each cell's face frame
(3x3-cell neighbourhood, returns > 3 sigma off the plane dropped), height = vertical line meets
the quadric (root nearest the plane's). Rock cells, roughness p90 / within 0.5 m of Grewingk:
  patch1 plane 0.65/96%  CURVED 0.93/98%  (Grewingk 1.19; TIN through every return 1.26/97%)
  meadow plane 0.66/90%  CURVED 0.97/94%  (1.28; TIN 1.34/91%)
  woz    plane 1.34      CURVED 1.66
Most accurate version so far, keeps more texture than the plane. Rock step 53-128 s per km2.
review/cliff_curved.jpg.

## 2026-09-26 — stress patches (tides / snow / marsh) and the class 21/22 fix

Bug found by `model/stress.py`: every candidate step treated NV5's exclusion classes as usable
returns. Now `EXCL = (7, 18, 21, 22)` (noise, high noise, snow, temporal exclusion) in every model
file; nothing in those classes can become ground, skeleton, rock or water.

After the fix:
- tides: final higher than vendor > 0.5 m in 0.00% of cells (was: all such cells rested on class 22,
  23.7% of cells touched it); pits 0 (was 9); roughness over water p90 0.21 m (vendor 2.46).
- snow: surface lies ON the snow in ~60% of the patch. Not a regression: there are no ground returns
  there (1.2% of snow cells have any class 2); the only returns left are snow tops that NV5 left as
  class 1. NOAA's published DEM (cog/kbay_2023.tif) is the same snow surface: final - NOAA median
  +0.01 m, p5-p95 -0.02..+0.08 m over the 597k cells where the class-2 TIN is empty.
- woz_boulders: its 7.6k class-22 returns now excluded; pits 50 (vendor 447).
- marsh: clean (lower than vendor > 0.5 m in 0.17% of cells, 83% of those on vendor ground).

## 2026-09-26 — floor under the zone solver (snow-edge trench, Hig at 59.37384 -151.53443)

The certainty-weighted solver (dtm_s_e) extrapolates freely where it has few points; at the edge of
the snow (vendor ground ends) it dove up to 28 m below every return, and Best/blend carried a trench.
Fix (apply.py + best_slope.py): drop solver cells more than 1 m below `K.return_floor` (lowest
return in a 5x5 m window; noise and class 22 excluded, snow kept as an upper bound on ground).
Calibration: at the six Grewingk sites truth is > 1 m below that floor in <= 0.23% of cells,
> 1.5 m in <= 0.045%. Rerun: snow/tides/woz/marsh (apply.py). The six Grewingk sites' LOSO chain
(best_slope -> blend -> rock) has NOT been rerun with the floor yet.

## 2026-09-26 — snow bridges, hole fill, water policy, low tide

- Blend plausibility test (blend.ceiling): a branch that floats above the lowest return in its
  cell with NO return within 0.15 m of it in 3x3 (the vendor TIN bridging ground-less snow holes,
  0.8-1.2 m up), or dips > 1 m under the 5 m return floor, loses to the other branch, ramped over
  5 m. Ungated, the "above" test cut the Woz boulders (6.3% vs 0.33% of cells > 0.5 m under the
  vendor); gating on support restored them exactly. Edge feather: blend -> v2a over the last 5 m of
  Best's coverage. The solver "single-return" rule was tried and dropped (cost false ground).
- Hole fill (fill_holes.py, in rock.write_final): ENCLOSED no-data regions only, harmonic fill,
  fill_mask.tif beside dtm_final (2 = filled). Margin gaps stay empty however narrow the mouth.
  Hig: water is "what the data shows" -- no flat water fill (a first version had one; removed).
  In the full run this must run on the MOSAIC, not per tile.
- Low tide (sitekit.admit_low_tide, apply.py step 2): class 22 returns > 0.15 m BELOW the kept
  water level (class 9, 15 m median) become class 2. At the tides patch NV5 kept the low tide
  already (2023 water -2.9 m; class 22 = 2024 water at -0.07 m), so 3 returns are admitted; with the
  flights swapped the rule recovers 278k of the 2023 low-tide returns (-2.95 to -0.81 m).

## 2026-09-26 — FULL RUN: model/fullrun.py (queued; launch after Hig's restart)

- 1,350 units (1 km cores, 60 m buffer) over the COPC tile index full/tileindex.json (2,298
  tiles, 28.36 B points, built from S3 header range reads). A unit runs only when all its tiles
  are fully downloaded, so the run can start while fetch.py is still going.
- Test unit u_586_6588: 9 tiles, 83 M points (overlapping flights: 4x the test patches' density),
  896 s processing (10.8 s / M pts), output verified (1 km core, 0 NaN, final-vendor p1/p99
  -0.37/+0.08 m). Estimate: ~35 B points incl. buffers x 10.8 s = ~105 h single -> roughly a day
  on 4-5 workers, gated by the download (~2 days).
- MEMORY not yet measured at 83 M points (7.7 GB peak was measured on ~20-33 M point patches);
  start with --workers 3 and watch before raising.

Launch / resume (both safe to repeat; each skips what is finished):
    # 1. the download, if the machine restarted
    cd /Volumes/Powder/lidar_src/kbay_2023_laz && nohup caffeinate -i python3 fetch.py > fetch.out 2>&1 &   (its own documented start line)
    # 2. the run
    cd /Volumes/Nunatak/lidar_build/kbay_reclass_test && nohup caffeinate -i /opt/anaconda3/bin/python model/fullrun.py --workers 3 >> full/run.log 2>&1 &
    /opt/anaconda3/bin/python model/fullrun.py --status
After all units: mosaic full/out/*/dtm_final.tif (EPSG:6334) and run fill_holes on the MOSAIC for
holes cut by unit edges (not yet written).

## 2026-09-28 — KNOWN ISSUE, not fixed: structures are classified as ground

Hig, reviewing the full mosaic in the dev viewer: buildings (and presumably bridges) survive into
`dtm_final` as ground. Not holding publication for it, but it is the first thing to fix next time.

**Why it happens.** `EXCL = (7, 18, 21, 22)` in apply.py, sitekit.py, v2.py and blend.py keeps
noise, high noise, snow and temporal exclusion out of ground. The vendor's structure classes are NOT
in it: **6 (building)** and **17 (bridge deck)** reach the ground model as ordinary candidates. A
roof is flat, smooth and single-return, which is exactly what the ground model and especially the
rock rule (rock.py: "in dark, all-single-return cells whose vendor surface sits > 0.3 m below every
return, every return is ground") accept. The vendor TIN goes under the roof; ours goes over it.

**Suggested fix (Hig's idea: override with the vendor's structure classification).**
1. Add 6 and 17 to `EXCL` in all four modules (and check class 19 / 20 in this delivery). Then a
   roof return is never ground and the vendor-TIN-like surface under it is what remains.
2. Rerun ONLY the units that contain class 6/17 points. The COPC headers give class counts
   cheaply (`pdal info --stats --filters.stats.count=Classification`), so list affected units from
   the tile index first; `fullrun.py` reruns a unit once its `full/out/<unit>` is moved aside.
3. Re-mosaic (`model/mosaic.py`, ~3 min) and rebuild the dev entry (`model/viewer_build_mosaic.py`).
4. Check: difference against the vendor surface over a few towns (Homer, Seldovia, Anchor Point)
   should lose its building-shaped positive blobs; nothing else should change.

The surface as built on 2026-09-28 (full/mosaic/) has this defect; say so in its notes wherever it
is published.
