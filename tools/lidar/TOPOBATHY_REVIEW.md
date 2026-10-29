# Topobathy compositing: a deep review (2026-10-15)

Prompted by one lake at 59.57943, -151.11156 that the Grewingk v6 composite shows as
flat ground, and by Hig's worry that six Grewingk iterations in two days were fixing
symptoms one at a time ("one fix creates another issue"). This reads the whole chain
(observe -> build -> fill -> build) as it stands at commit 2dd6aee, measures what it does
over the Grewingk box, and proposes the shape of a process that generalises. Every
number below was measured on the v6 outputs in `$LIDAR_BUILD/composite/grewingk/`.

Short version: the engine's bones are right (dated, classed, quality-weighted
observations; winner-take-all with a seam; the fill as a layer with provenance). What
has gone wrong is that **the judgements "what surface is this?" and "is this cell
evidence?" are made in four places with four definitions**, the composite trusts a
class it was never given evidence for, and the fill both consumes and emits classes
so the loop can chase its tail. The lake is one instance of that, and so are two
other defects found today that nobody had reported yet. The fix for all of them is
structural and small in code; the fix for *any one* of them in isolation would be a
fifth rule.

## 1. The lake, traced through the chain

At 606657 E, 6606091 N (EPSG:6334), a 13,500 m2 pond at 193 m:

| what | says |
|---|---|
| Grewingk 2021 vendor DTM | 192.85 m, exactly level (3x3 range 0.000): hydroflattened |
| Grewingk 2021 points (`obs/grewingk_2021_box/class.tif`) | **water** (94 % of the patch) - no returns at all, level DTM |
| 3DEP (`obs/water_3dep.tif`) | flat water (84 %) |
| KBay 2023 reclass v2 | 193.23 m, range 0.05: its own fill of a return-free water body from the rim level; the survey-wide `water_mask` says **water** (93 %), `fill_mask` 2 on 45 % |
| KBay class raster (`obs/kbay_2023_pts/class.tif`) | **no data** - that package covers x 595-606 km only (the Kachemak test box); the pond is at x 606.66 |
| recipe `kbay2023` class steps | default `ground`, then the point raster (absent here), then two Grewingk-lake masks (not here) |
| `state_rules_sequential` | class_now = the newest credible class = KBay's **default ground** (2023 > 2021); KBay's ground observation is compatible with ground, takes the cell; the 2021 water observation is never consulted |
| composite | flat ground at 193.2 m, winner kbay2023, class_now ground; no hole, no fill |

Three things had to line up: a class source that does not cover the layer, a default
class that counts as an observation of class, and a class-now rule that ranks classes by
date alone. None of the three is specific to this pond.

The same scan over the whole box (`class_now == ground` where the 2021 points say
water, inside the KBay footprint, outside its class raster) finds **13 patches >= 500 m2,
0.22 km2**. Three are real water bodies hidden the same way:

| lat, lon | m2 | z (m) | KBay water_mask |
|---|---|---|---|
| 59.57945, -151.11156 (the reported pond) | 13,524 | 193.2 | 0.93 |
| 59.61710, -151.18917 | 59,548 | 3.5 | 0.95 |
| 59.61668, -151.18597 | 5,936 | 3.5 | 0.97 |

The other ten (84,424 m2 at 320 m, 11,172 at 810 m, ...) are the opposite failure:
the 2021 point rules called them water and they are not. The 2021 points hold **no
returns at all** there (steep, shadowed ground), the vendor TIN bridged the gap with a
plane at 35-46 degrees slope (|Laplacian| 0.001, so planar), and `rules_grewingk`'s
"no returns and smooth DTM -> water" test accepts a sloped plane as readily as a level
one. KBay (rough, 3-16 m lower: the real ground) wins those by date, i.e. by luck. These
are the "glacier lakes >= 550 m" in the resume block's open item 5.

So the one date rule that saves the ten breaks the three. It cannot be tuned; the
evidence behind each class has to travel with the class.

## 2. Findings: the logic

Numbered for reference; each gives the evidence, the general mechanism, and what the
fix is in principle (not yet the code).

**L1. A class obtained by default outranks a class obtained from evidence, if it is
newer.** `class_raster` writes `default` into every valid cell, and `state_rules_sequential`
takes class-now as the newest credible class whatever its origin. A survey with a
class raster that stops at a box edge, or none at all, therefore reclassifies every
older survey's water as ground over its whole footprint. Fix: a class carries its
**provenance** (observed from points / inferred from the DTM / declared by a mask /
default), and class-now is decided provenance-first, date-second: a default never
overrides evidence, an inference never overrides a point observation. Where two
*point* observations disagree (2021 water, 2023 ground: a drained pond, a retreated
face), the newer wins - that is the case the date rule is for.

**L2. A class raster that does not cover the layer is accepted silently.** The `raster`
class step does `sel = valid & rvv` and moves on; nothing reports that 21.8 km2 of
KBay's 39 km2 footprint in this box has no class raster under it. Fix: the recipe (or
the observe stage) asserts that an observation's class source covers its footprint on
the grid, and the build refuses or warns loudly. The observe packages should be built
per survey over the survey, not per test box.

**L3. For Grewingk 2021 "void" in the class raster is read as ground.** The recipe maps
only code 2 -> water (`"map": {"2": "water"}`), written when the raster was 60 % void
from 36 tiles; with all 194 tiles it is 6.0 km2 void of 67.6, and those cells keep the
`default: ground`. Void means "the laser got nothing real back here"; the DTM there is
the vendor's interpolation (a TIN facet, a hydroflattened surface at whatever level
the vendor chose). **0.91 km2 of the composite shows the 2021 layer where its own class
raster says void**, including the 2021 water surface at +2.1 m over the lagoon in L6.
Fix: read the raster with `codes: true` like KBay (void stays void), and class the
vendor's bridged surface `modelled`, never ground. (`make_nohf_dtm` replaces the vendor
DTM only where returns exist; the rest of its output is still the vendor surface.)

**L4. "Hydroflattened water" is defined three times, differently.** `observe_points.rules_grewingk`:
residual from a 5x5 mean < 0.05 m (smooth). `make_ice.py` lake_2021: 3x3 range < 0.02 m
and within 0.35 m of a known level (level). `flat_water.py`: 3x3 range < 0.01 m (level).
The smooth test is what produced the ten false lakes above; the level tests are right
(a vendor flattens water to the centimetre; a TIN facet is planar but not horizontal,
and real ground is neither). Fix: one function, one definition ("exactly level, in a
connected patch >= area"), used by every caller.

**L5. The fill judges evidence and writes classes back into the build.** `fill.py`
decides which observations inside a lake are "the water surface, not evidence"
(within `lake_surface_tol_m`, specks, low islands, ice, carried-over cells) and writes
`fill_surface.tif`, which the next build reads as a class step (`only: ground -> water`),
which changes class_now and the hole set, which changes the next fill's domains. That is
the loop that grew ponds into the outwash (v6) and needed the "ground at the level
joined to dry land is shore" rule to stop; it is also why build -> fill -> build is
only *claimed* idempotent. Each rule in that block is reasonable; the place is wrong.
"This return is off calm water" and "this 112 m2 sliver is not an island" are facts
about an observation, decidable from its own points (height above its own water level,
the reclass's floating-return test already does exactly this), and belong in the
observe stage where they are computed once and cached. Fix: the fill consumes classes
and never emits them; the feedback edge is deleted.

**L6. A modelled bed may stand above the water that was observed over it - everywhere
except lakes.** The lake clamp (`fill > level - 0.1`, v5) exists because the harmonic
residual and the thin-plate spline have no maximum principle. Marine domains have no
clamp and no ceiling. Measured: **0.76 km2 of marine fill stands more than 0.1 m above
the water surface a survey observed there, up to 17.7 m**. The worst case is the open
bay off the Grewingk outwash at 59.59804, -151.18007 (1.06 km2 of fill): KBay's sea
surface is at -2.4 m NAVD88 there (about 1 m below MLLW, a spring low on 2023-08-01),
the 2009 Hydropalooza multibeam reads **-44 m**, and the fill sits at median +0.94 m,
because the Grewingk recipe carries no bathymetry layer at all (its layers were chosen
for the lake) and 2021 vendor cells at +2.1 (its sea surface at a higher tide, class
void, shown as ground by L3) sit inside the domain as "islands" that the residual
surface is pulled up to. The missing layer is a recipe omission, not an engine defect;
the ceiling is the engine defect, and it would still put the bay bed above the water
wherever the multibeam lines leave a gap. Fix: the lowest observed water surface over
a cell is a hard upper bound on the bed, in every domain kind, applied as a constraint
in the solve (not a clamp after it), and the water surface itself is a product
(roadmap §4).

**L7. The marine floor is one number per box.** `low_tide_m = -2.9` is "the lowest sea
surface the KBay water class shows". The east end of Halibut Cove Lagoon at
59.57400, -151.18948 (0.39 km2 at the box's west edge; the Kachemak recipe had the
Hydropalooza lines in it, this one does not) is perched at +0.8 m behind its sill (the
reclass's own level raster says 0.76-0.87); its fill came out flat at +0.76, i.e.
depth zero, because the prior descended to -2.9 and the conditioning pulled it back to
the rim. A perched lagoon's floor is its sill level minus a depth, not the open sea's
low tide. This is the "lagoon fill" problem the Kachemak notes left open; it is the
same problem as L6 seen from the other side (no water-body model, so no per-body level
and no per-body floor).

**L8. The lake prior, the shore-gradient rescale, the spline residual and the clamp are
four mechanisms answering the same soundings.** Fill log, v6: the spline's residual to
the prior has |p90| 43 m and its correction field spans -27 to +45 m; 0.088 km2 of
modelled bed had to be clamped, by up to 23.7 m; 200 of 317 lower bounds were violated
after the first fit and 24 still by > 1 m after the active-set pass; the Berg sounder
NMAD went 0.22 -> 0.38 m and the 2026 lead lines +0.15 -> +1.3 m from v4 to v6 with no
explanation (resume block, item 3). A correction of +45 m means the prior's shape is
wrong there (the Dmax-at-the-farthest-point cone, with a free face, puts the deepest
water where the soundings do not), and the shore-gradient rescale (four parameters,
widening windows) is a second way of letting soundings reshape the prior before the
spline does it a third way. The interaction is a plausible cause of the v4 -> v6
regression and is untestable as built. Fix in principle: the prior takes its *shape
parameters* (Dmax, where it is, the shore exponent) from the soundings in one fit, the
spline conditions the residual once, bounds and the water ceiling enter the same solve
as inequality constraints. Then every sounding acts in one place.

**L9. The fallback layer's "stand in only where no survey has a cell" uses valid cells,
not footprints.** The recipe note says footprints were not given because "the 2021
class raster is ~60 % void"; that reason is stale. Where KBay v2 voids return-free water
(by design), 3DEP's hydroflattened sea at ~0 m (or, resampled at its edge, non-flat
ground near 0-2 m) comes through and becomes a *rim* for the fill. The lagoon in L6
shows it (cov = 1 on 65 % of the window: only 3DEP has a cell). Fix: pass the surveys'
footprints (the observe packages' `n_all >= 0` extent), as the step already supports.

**L10. Retreat, ice, islands and the lake outline are site code.** `make_ice.py` (2020
outline minus 2023 water, plus ice by lowering, retreat by low-drop, by non-agreement,
by closing, by hand seed) and `make_nohf_dtm.py` (`ISLANDS_KEEP`) encode general
reasoning - "a surface that came down > 5 m in two years was ice", "a later survey
confirms an earlier surface only if it reproduces its detail", "water-classed returns
0.65 m above the flat lake in a patch >= 50 m2 are an island" - as Grewingk constants
with Grewingk paths. The roadmap (§5) already wants the hand lists as GeoJSON edits; the
reasoning itself wants to be observe-stage tools with the site in the recipe.

**L11. Channel detection is off for the box by a sentinel** (`channel_min_m2: 1e12`)
because it read the lake's deep east end as a river; the roadmap's (d) ("the named
lake excluded") is the right fix and is the water-body model again: a domain that is
a known lake is not a channel candidate.

**L12. Scale.** The fill is one whole-grid pass (13 min, 738 s in the v6 log, for 21 M
cells); the plan knows KBay at 2 m is ~30x larger and that fill must go per domain.
Every structural change below makes per-domain fill easier, not harder, because a
water body becomes the unit of work.

## 3. Findings: the organisation

**O1. The three stages exist, but the observe stage has two homes and no contract.**
`$LIDAR_BUILD/obs/<survey>_pts/` (points -> cellstats -> class, per survey, good) and
`$LIDAR_BUILD/composite/grewingk/obs/` (ice, retreat, the 2021 lake, the non-hydroflattened
DTM, the bare SfM, 3DEP flat water: all observation facts, filed under one composite).
A fact about Grewingk 2021 computed for the Grewingk composite is not reused by the
Kachemak one. The plan's `obs/<dataset_id>/` package with `class.tif`, `sigma.tif` and
a `date.json` is still the right shape; it needs a stated contract (below) and to be
the *only* class source a recipe names.

**O2. The recipe is a program.** `grewingk2021` has seven ordered class steps (default,
points water, outline ice, lake water, retreat water, fill-surface water, islets, islands
ground) whose meaning depends on their order and on a file the pipeline itself writes
(`$out/fill_surface.tif`). A recipe should *name* observations and state product choices
(target, as-of, which surveys, their order, rates, the post rules); it should not
assemble a classification from parts.

**O3. `fill.py` is one 600-line function with 49 parameters, 17 commits in nine days.**
Domains, rim character, the marine band, lakes, channels, surface-judgement, sparse
points, shore gradient, the harmonic residual, the spline, bounds, the clamp and sigma
are one scope with shared locals (`lk`, `lvl`, `inside_obs` mutated mid-way). Each new
site rule lands as an `if` in the middle. It wants to be: (a) domains + per-kind priors,
(b) one conditioning step, (c) constraints, (d) sigma, as functions with inputs and
outputs, so a rule can be tested on one domain.

**O4. There is no check harness.** The two scans that found L1/L6 today (flat patches
the class axis calls ground; fill above the observed water) are twenty lines each and
should run on every build; `composite.py holdout` and the Portage `regress` exist but
nothing runs the tuned windows after a rule change. The roadmap names this (§1); it is
the thing to build *before* the next rule, because without it "did v6 make v4 worse"
is a feeling.

**O5. Site scripts with hard-coded paths and hand lists** (`composites/grewingk/make_ice.py`,
`make_nohf_dtm.py`) sit beside generic ones (`topobathy/dsm_bare.py`, `flat_water.py`)
that take a recipe and arguments. The generic pattern is the one to keep.

## 4. The coherent model

Not a rewrite: the same engine with its stages given contracts and its judgements
moved to where their evidence is. In order of data flow.

### A. Observe - per survey, product-independent, cached in `obs/<dataset_id>/`

Inputs: the archive COG, the points where we have them, the survey's own by-products
(the reclass's `water_mask` / `water_level` / `fill_mask` / `struct_mask`), a reference
surface for DSMs, outlines for glaciers. Outputs, each with the rule that made it
printed into `class.json`:

- `class.tif` (codes; **void is void**) and `class_src.tif` (provenance per cell: 3
  points, 2 the survey's own masks, 1 inferred from the DTM, 0 default). One
  "level water" detector (L4) for hydroflattened surfaces; the vendor's bridged
  surface over no-return cells is `modelled`, not ground (L3); floating returns and
  specks over a water body are judged here from the survey's own water level (L5).
- `water_level.tif` or a per-body table: the water surface this survey saw, per
  connected water body, with its date. (KBay's reclass already writes it; Grewingk's
  comes from its class-9 returns and its hydroflattened flats.)
- `sigma.tif` as now; `shift_m` as now but recorded here with its evidence, so the
  registry of vertical shifts (roadmap §1) is the obs packages.
- Coverage: the package covers the survey's footprint; a recipe box is a window on it,
  never the other way round (L2).
- Generic tools, site facts in arguments: `dsm_bare` (exists), `flat_water` (exists,
  becomes the one level-water function), `ice_from_dsm` (make_ice generalised: outline,
  lowering threshold, agreement test, hand seeds from an edits file), `dtm_from_points`
  (make_nohf generalised: islands as a rule, keeps as edits).

### B. Water bodies - new, between observe and composite

A small vector/label product on the grid: each connected water body (union of every
survey's water class plus the fallback's level flats), with per-survey levels and
dates, its kind (lake / sea-connected / perched lagoon / river reach, from level vs.
the sea and from connectivity), islands, and a *lowest observed level*. Hand facts
(a level, a Dmax, a hypsometry, "this is a lake") attach here as edits. This is where:

- retreat is decided per body (a 2021 ice cell inside the 2023 lake body is retreat,
  no per-cell closing/seeding rules);
- a lake vs a drained pond is decided from two surveys' levels, not from per-cell dates;
- the fill gets its domains, levels, floors (a perched lagoon's sill, L7) and ceilings
  (L6) without inferring them from rims;
- channels are the reaches, and a body that is a lake is not one (L11);
- the water-surface product (roadmap §4) falls out, dated per survey.

### C. Composite - the engine as it is, with three changes

1. Class-now by provenance first, date second (L1): among credible observations at a
   cell, the highest `class_src` wins; among equals, the newest. A default class is
   not evidence of class.
2. Coverage assertion (L2) and footprints for the fallback (L9).
3. No class step reads a build product (`$out/fill_surface.tif` goes; L5). The fill
   layer stays as it is: `modelled`, half rank, idempotent by construction once the
   feedback edge is gone.

### D. Fill - per water body, in four separable parts

1. **Domain + prior by kind**, taking the body's level, floor and shape parameters;
   the lake prior's Dmax and its position fitted to the soundings when there are any
   (one fit; the shore-gradient rescale becomes part of that fit or goes).
2. **Conditioning**: the weighted thin-plate smoothing spline, once.
3. **Constraints in the solve**: bed <= lowest observed water surface minus a margin
   (every kind, L6); lower bounds; a sounding's sigma. One constrained solve instead of
   fit -> clamp -> refit -> clamp.
4. **Sigma** as now.

Per body means the KBay-scale fill is a loop over bodies with a cache (L12), and a
rule can be run and timed on one lake.

### E. Check - every build, every window

`composite.py check <recipe>` writes `out/check.json` with, at least:

- soundings fit per survey (median, NMAD), lower bounds violated (n, worst);
- modelled bed above any observed water surface (km2, worst) - zero is the target;
- level patches (3x3 range < 2 cm, >= 1000 m2) whose class-now is ground, listed with
  lat/lon - the lake-as-ground detector (zero is the target unless a survey's points
  say ground there);
- class provenance summary per layer (how much of its shown area is default-classed);
- seam statistics (gradient p99 at layer edges), area by source, fallback area;
- the stored regression windows (Portage, Peterson-China Poot-Halibut Cove, Grewingk,
  the KBay reclass sites) re-run and diffed against their last accepted metrics.

A rule change is accepted when the check set does not get worse anywhere, and the
resume block records the numbers, not the impression.

### Hand edits

As the roadmap says (§5): small GeoJSON features with a kind, values, who and why,
applied every build. They are inputs to A (an island keep, a class correction), B (a
lake seed, a level, a Dmax, a hypsometry, a sounding run as lower bounds) and D (a
breakline or contour with depth and sigma). The paint rasters stay for weight.

## 5. Order of work, and where the lake fix is

1. **Check harness first** (E, minimal: the six metrics above over the current v6).
   Half a day; it is what makes everything after it safe.
2. **Observe-stage contract** (A): `codes: true` everywhere, void is void, one level-
   water function, provenance raster, coverage assertion; rebuild the KBay package
   over the whole survey (or at least every recipe box) and the 2021 package with the
   vendor-bridged cells as `modelled`. **This is the lake fix**: with KBay's package
   covering the pond its class is water from the reclass's own mask, and the pond is a
   hole and gets a basin. It is also the fix for the ten false lakes (level, not smooth)
   and for the 0.91 km2 of vendor interpolation shown as 2021 ground.
3. **Class precedence by provenance** (C.1). Needed even after 2, because the next
   survey without points will come.
4. **Remove the fill -> class feedback** (C.3, the surface judgement into A). Re-run
   Grewingk; the check set says whether anything was lost.
5. **Water bodies** (B) in the simplest form: labels, per-survey levels, lowest level,
   kind. Then the ceiling constraint (D.3) for every kind and the perched floor (L7).
   Re-check L6's 0.76 km2.
6. **Split the fill** (D) along the seams it already has, per body; fold the shore
   gradient into the prior fit; constrained solve. Then the Grewingk acceptance list
   (roadmap §0) has a chance of being met once and staying met.
7. The creek, as planned, on top of B (it is the river-reach kind of body).
8. Generalise the two site scripts into observe tools; hand lists to edits.

Steps 1-4 are small and mostly deletions and assertions; 5-6 are the real design
work and should be done once, with the check set, rather than as six more v-numbers.

## 6. Not verified, and questions for Hig

- The Berg NMAD regression (0.22 -> 0.38) read straight off the v6 fill at the
  soundings, no rebuild: it is entirely the first 50 m from shore (n 1,196, median
  +0.24 m, NMAD 0.89 m, at a median depth of 6 m); 50-150 m out NMAD is 0.40, beyond
  400 m 0.15, and the deep bed (100-200 m) 0.17. The fill is too SHALLOW near shore.
  Suspects, in order: the spline's boundary sites (residual pinned to 0 every 10 m
  along the shore with sigma 0.2 m) outvoting soundings 10-50 m off a shore whose
  prior is wrong; the "shore kept as ground" rule (v6) moving the rim out; the
  shore-gradient rescale. The 2026 lead lines (+1.3 m, n 4, depths 138-164 m) are a
  different thing: the prior is **40 m too shallow** at them (its deepest point is at
  the farthest-from-shore cell, not at the east end where the depths are), and a
  smoothing spline asked to carry a 40 m correction stops 1.3 m short. L8, measured.
- The 59.617, -151.189 ponds (59,548 and 5,936 m2 at 3.5 m): water bodies hidden as
  ground like the reported pond, by the same mechanism; worth a look in the viewer.
- Should "a default class is never evidence" also apply to the `ground` default of a
  bare-earth survey with no points at all (a vendor DTM delivered without a LAZ)? I
  would say its *hydroflattened* flats are inferred water and the rest is inferred
  ground (provenance 1), so an older survey's point water still wins there.
- The water-body product's datum for sea bodies (MHW / MLLW / flight tide) is the
  roadmap's open question 1; B only needs the flight-day levels to work.

## 7. Point clouds: the observe queue for Kachemak Bay (2026-10-15, after Hig's reply)

Hig: the lidar generally has point clouds and the observe stage should use them; the
2008 cloud is on hand; Homer 2019 is the big one; the green lidars mostly matter for
bathymetry detail; the Chugachmiut (Lower Kenai) cloud is probably not obtainable;
reclassifications only where they clearly pay. What is on disk, checked today:

| survey | points | on disk | observe package | queue |
|---|---|---|---|---|
| KBay 2023/24 (NOAA NIR) | 2,298 COPC tiles, 163 GB, `/Volumes/Powder/lidar_src/kbay_2023_laz/` | complete | `obs/kbay_2023_full/`: **full-survey cellstats already built** (35 blocks, 32500 x 23000 at 2 m, 2026-10-13 02:58); `classify` was only ever run on the Kachemak box (`obs/kbay_2023_pts`) | run `classify` over the full stats with the full mosaic's `water_mask` / `water_level` / `fill_mask` / `struct_mask`, point every recipe at it (minutes; this alone fixes the pond) |
| Grewingk 2021 (DGGS) | 259 LAZ tiles in the index, 194 local (the Grewingk box), 65 to fetch (DGGS zip route, ~0.5 MB/s) | partial | `obs/grewingk_2021_box/` over the box, all 194 tiles; void mapped to ground in the recipe (L3) | fetch the 65, rerun stats over the survey, `codes: true`, level-not-smooth water (L4) |
| Kenai 2008 (USGS EPT) | 14.05 B points, EPT mirror 73 GB at `/Volumes/Nunatak/lidar_src/kenai_2008/ept/` | complete (was 20/72 GB in September) | none; the taught-filter DTM work (`KENAI_2008_PLAN.md`) is paused on Hig's site vetoes | an EPT reader for `observe_points stats` (PDAL `readers.ept` with bounds); the USGS copy's classes are 2 ground / 7 / 10 noise with no water class, so water comes from the level-flat test on the DTM and from no-return cells. **NOAA's copy of the same survey** (NGS IOCM 2008, Digital Coast dataviewer ID 108, InPort 48198) publishes a class scheme 1 / 2 / **9 water** (InPort 72052) - if its points carry class 9 it is the better observe source for water; not yet checked. The reclassification itself stays parked unless the composite shows crest loss mattering. Lower Kenai (Chugachmiut) is a deprecated subset of this survey (Hig, 2026-10-15): set aside |
| Homer 2019 (DGGS RDF 2021-2) | 171 LAZ, 11.58 GB; portal dataset 1354 (whole-extent query, 2026-10-15: unchanged from September) | 157 tiles pulled, **85 readable, 72 unfinalised** (`homer_2019_pc/`) | none | **Verified 2026-10-15 that "unfinalised" is the right word and that no finalised copy is downloadable.** Two bad tiles re-fetched from the portal today are byte-identical to the September copies; the zip members are dated 2021-01-27; the LAS header says PDAL 2.2.0, LAS 1.2 point format 3, LASzip 3.4r3 chunked at 50,000 points, and the header fields (size 0, offset 0, count 0, bounds unset) and the chunk-table slot (holds its own offset, 333) are the placeholders a LAS writer leaves until it closes the file - the close never happened, in 72 of 171 tiles, in 2021. The DGGS publication page (doi 10.14509/30591) links only the PDF and the portal; USGS 3DEP (S3 and TNM), OpenTopography and NOAA Digital Coast have no Homer 2019 points. The point *data* is almost certainly complete (sizes are tile-sized); only the chunk table and header are missing, so a local repair (decode chunk by chunk, searching each boundary) is possible in principle but a day's work with no guarantee. **Route: ask DGGS (dggspubs@alaska.gov; authors Salisbury, Daanen, Herbst) to re-export the 72 tiles from their master copy, listing them by name (`unfinalised_tiles.txt`).** Until then the 85 readable tiles give a partial package and the DTM gives the rest (provenance "inferred") |
| Homer 2018 NCMP topobathy (green) | 97 COPC, 2.5 GB, `/Volumes/Nunatak/lidar_src/homer_2018_ncmp/` | complete | none | stats + classify; its value is the bed to ~-30 m and the beach profiles; the land classes are a cross-check on 2019 |
| Seldovia 2019 NCMP topobathy (green) | not on disk; NOAA DAV should hold the classified points | - | none | optional, for bathymetry detail only (Hig); the bare-earth grid is already a layer |
| Lower Kenai (Chugachmiut) | no cloud, probably not obtainable | - | - | DTM-only package: level-flat water, provenance "inferred", never a default |
| Dixon 2023 SfM, Pedersen SfM 2024 | no points (DSMs) | - | `dsm_bare` | as now |
| bathymetry (Hydropalooza 2009, OCM 2023 2 m, Grewingk sonar, Pedersen 2025) | gridded | staged | class `bed` | the Grewingk recipe must carry them (L6); the outer-bay set per `reference_outer_kachemak_bathy` |

Rule of thumb for reclassification (Hig: do not get bogged down): the observe stage
needs per-cell evidence (returns by class, lowest return, flatness, intensity) and a
class raster; it does not need a better ground filter. A reclassification is justified
only where the delivered DTM is wrong in a way the composite will show (KBay's roofs
and floating returns were; Kenai 2008's crests may be). Everything else is `stats` +
`classify`, an hour of machine time per survey.

**Datums at the bay, for reading water surfaces (NAVD88 GEOID12B):** MLLW is at
-1.38 m (the collection's value from the Seldovia green lidar vs the multibeam; the
gauge says -1.55), MHW at about +3.9 m, so MSL is roughly +1.3 m. KBay's sea surfaces
at -3.0 to -1.9 m are therefore 0.5 to 1.6 m *below* MLLW: the 2023-08-01 spring lows
(full moon 1 Aug). The bay cell discussed under L6 (-2.4 m) is ~1 m below MLLW and
~3.7 m below MSL. NIR lidar does not see through water, so no KBay return is a bed
return; the green lidars (Homer 2018, Seldovia 2019) do, and neither reaches the
Grewingk box.

## 8. Two messy-data patches, first pass (2026-10-15, Hig's request)

Recipes `composites/seldovia_bay/` (x 568-576 km, y 6587-6595 km) and `composites/bluff_point/`
(x 572-580, y 6609-6617), both 2 m, built with the v6 engine plus two general additions in
`axes.py`: a `canopy` class-now admits a `bed` observation, and `snow_vs` takes `only` (which class it
may recolour) so a green-lidar bed standing over a multibeam bed can be classed canopy (kelp). Chains
run by `$LIDAR_BUILD/composite/run_chain.sh` (build -> fill -> build; needs
`OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` on this Mac or a forked worker dies and the pool hangs).
Dev viewer ids `seldovia_bay_topobathy_v1`, `bluff_point_topobathy_v1`.

**Outer Seldovia Bay.** Composite: Hydropalooza 23.1 km2, Seldovia 2019 8.4, KBay 2023 30.3, OCM 1.5,
NCEI 0.3, fill 0.45, holes 0.01. The kelp did not show as canopy: where the green lidar and the
multibeam both have bed they agree to median -0.03, NMAD 0.14 m over 8.5 km2, and the "more than 1 m
above the multibeam" test fired on 3,400 m2. What the green lidar's bare-earth grid does under kelp is
leave VOIDS (the dark-blue blobs inside its swath in the source-index picture), and the composite
already fills those with the multibeam where it exists; in the shallows without multibeam they go to
OCM 2023 (which is not independent there - the OCM seamless almost certainly carries the same 2019
lidar: NMAD 0.06 against it) or to the fill. So the kelp question for Hig is WHERE he sees the kelp
artefact: a mound in the lidar bed (not found in the overlap), a void (handled where the multibeam
reaches), or the shallows (nothing independent to judge against).

**Bluff Point.** Composite: Homer 2019 25.7 km2, Homer 2018 (green) 8.4, Kenai 2008 0.4, H12146 8.0,
Hydropalooza 6.4, OCM 11.9, NCEI 3.3, no holes. Datums hold: 2019 - 2008 ground median -0.03 m
(NMAD 0.19, 26 km2), 2018 - 2008 -0.01, 2018 - Hydropalooza bed -0.11. The time axis gives the 2019
surface wherever it exists (rates.ground 0.05 m/yr in the recipe: an active coast), so the slide
complex is 2019 ground and the change itself is only in report.json's overlaps - a difference layer
(2019 - 2008 where both are ground) is the product that would show it. Visible seams: H12146's
single lines across the OCM grid (sigma 0.5 vs 1.0, so they win as 16 m stripes 0.3 m below the
OCM: h12146 - ocm2023 median -0.31, NMAD 0.40), and the NCEI 10 m grid in the NW where OCM is absent.

### 8.1 Hig's review of the two patches (2026-10-15, measured, nothing rebuilt)

**Seldovia lagoon (59.44566, -151.74870) too deep.** KBay classes it water correctly (water surface +0.80
m; 2,827 of 3,600 cells in a 120 m window). The composite then takes the bed from two places, neither
the shore-slope prior: (a) OCM 2023 carries values under the lagoon down to -2.1 m (median -0.68) and
the class gate `water -> bed` admits them (1,462 cells); (b) the rest went to the fill as a MARINE domain
(land rim median < 6 m) and the tidal-channel-by-shape model (`channel_beds`, depth = width / channel_wd,
w_med 54 m -> dmax 5.4 m) carved a 5 m channel under a lagoon ponded at +0.8 at a -2 to -3 m tide
(fill_mask 6; z p10/50/90 -4.3 / -2.2 / -1.5). The shore-slope prior never ran there. Two general
faults: the water level of a body is not a ceiling for anything (a channel below a perched lagoon
should be bounded by the lagoon's sill, which the tide at the flight pins at or above the sea's level
that day), and the channel model is told by shape alone. OCM's lagoon values are unverifiable (below).

**Shallow lakes not parsed as lakes** (59.44275,-151.75282; 59.42845,-151.75636; 59.43134,-151.75742;
Bluff Point 59.65119,-151.63721). All four are flat to 2-5 cm (std over 0.35-1.2 ha at tol 0.1 m:
0.017-0.050) and none holds a single vendor water return (cellstats n_water 0 at the three KBay lakes;
the lagoon has 7 per cell). The KBay reclass water model needs class-9/20 returns within 20 m for a
level, so its water_mask is 0 at all three: one (27.7 m) has no returns -> void -> a dry hole filled from
its rim at 27.57 (0.1 m below the water; fine); two (204.4, 231.3 m) have NIR returns OFF the water,
vendor class 2 -> ground at the water level. At Bluff Point the Homer 2019 DTM has the lake at 22.03
(std 0.017 over 0.88 ha) but the recipe's only water rule is `water_level` (the modal sea level), so
ground; Homer 2018 is 20 % void there (22.1), Kenai 2008 has it at 22.56 (lake 0.5 m higher in 2008).
`flat_water.py` (RANGE 0.01 m, hydroflattened models) would not catch any of them. Test of a NEAR-flat
detector (std < 0.03 m in a 14 m window, patches >= 2,000 m2) over both boxes: Seldovia 1.77 km2 flat,
of which 1.59 is the reclass's own water mask; the 0.2 km2 it adds is the rest of the lagoon (17.9 ha
at 0.74, the water mask covers under half of it), the three lakes, and a few 0.2-0.5 ha patches at
2-7 m near town (tidal flat / airstrip apron). Bluff Point: 4 patches, the lake first. A flat patch
that is also a local minimum (rim above it) is a lake; a runway is not. This is the "one level-water
function" of section 4A, with the threshold set by these numbers, run on every DTM, not only
hydroflattened ones.

**Seam at 59.64628, -151.70385 and the regional grids.** Bed -21.5 m, NCEI 2010 against H12146
single lines (no OCM, no lidar there). Transect: H12146 -20.0 where NCEI reads -22.9; the composite
steps 2.9 m over the 30 m feather at each line. Measured over the box (candidate minus reference,
median (NMAD), by reference depth below MLLW):

| | 0-1 m | 1-2 m | 2-5 m | 5-10 m | 10-20 m | 20-60 m |
|---|---|---|---|---|---|---|
| OCM - Homer 2018 bed | +0.01 (0.06) | -0.02 (0.11) | -0.03 (0.17) | -0.02 (0.04) | -0.01 (0.04) | - |
| NCEI - Homer 2018 bed | -0.12 (0.39) | -0.57 (1.51) | -1.94 (0.90) | -0.64 (0.69) | +0.07 (0.72) | - |
| NCEI - Hydropalooza | - | - | - | +0.05 (0.06) | +0.04 (0.05) | +0.00 (0.03) |
| H12146 - NCEI | - | - | - | - | +2.36 (0.31) | +0.71 (0.34) |
| H12146 - Hydropalooza | - | - | - | - | - | +0.05 (0.08) |
| OCM - Hydropalooza, no lidar within 20 m (Seldovia) | -0.27 (0.42) | -0.18 (0.42) | -0.08 (0.46) | +0.37 (0.53) | +0.04 (0.16) | +0.42 (0.37) |
| OCM - Hydropalooza, Seldovia 2019 void inside its swath (kelp) | -0.36 (0.24) | -0.73 (0.74) | -0.08 (0.58) | +0.12 (0.30) | -0.02 (0.21) | -0.08 (0.21) |

So: OCM IS the green lidar wherever the lidar exists (0.04-0.17), and NCEI IS Hydropalooza wherever
that exists (0.03-0.06) - agreement there says nothing about either grid's own quality. On their own:
NCEI's nearshore is 0.6-1.9 m too deep with NMAD 0.7-1.5 (old soundings interpolated to the beach),
and it is 0.7 m (20-60 m) to 2.4 m (10-20 m) deeper than the H12146 lines, which agree with
Hydropalooza to 0.08 - the "stripes" are H12146 being right. OCM's own bathymetry is NMAD 0.4-0.5 m
in the shallows (biased 0.2-0.3 deep at 0-2 m) and 0.4 beyond 20 m. The OCM data report
(supplemental DPR, in the scratchpad `ocm_meta/`) says why: of the Kachemak AOI's 1,013 km2, 825
km2 (81 %) is GMT `surface` interpolation (tension 0.8), SDB 9.7, topobathy lidar 42, sonar 0.9;
their own check against non-SDB sources: MAE 0.52 m, RMSE 1.17 m. The shore gaps Hig suspects are
real: at Bluff Point the regional grids win 0.98 km2 within 200 m of land shallower than 10 m
(median 21 m from the lidar's last bed cell, 128 m from land, 2.2 m deep); the largest (64 ha,
59.66396,-151.69759) is NCEI alone from the beach to 108 m out, where it meets the lidar 1.3 m too
deep (-4.27 vs -2.95).

**What to do with it (general).** A weight is not the fix: NCEI already ranks last (sigma 3), the
gaps simply have nothing better, so it shows at its own value with a step at the lidar's edge. The
fix is the fill's RESIDUAL step applied to fallback layers: a low-rank grid is a PRIOR, and the
residual of every higher-rank bed observation against it (lidar edge, H12146 lines, multibeam hull)
is carried into the prior harmonically / decaying over a correlation length, with the sigma growing
with distance from the conditioning data. That removes the 1.3 m step at the lidar edge, pulls NCEI
to the H12146 lines (no stripes), and keeps the regional grid's shape where nothing else exists. The
measured numbers are the sigmas: NCEI nearshore 1.0-1.5 m (own), OCM own 0.4-0.5 m (0.2-0.3 deep
bias at 0-2 m), NCEI deep-not-Hydropalooza 0.3-0.7 m plus a 0.7-2.4 m bias; per-source sigma should
be by depth, not one number. This is section 4D's "fill per body with constraints in the solve"
extended to the marine fallbacks, and belongs after the check harness (which should carry this table
as a regression window). OCM's kelp voids are no better than its open-water own (0.6-0.7 NMAD at
1-2 m): under kelp the composite should prefer the multibeam, then the conditioned prior.

### 8.2 Conditioned priors, built and measured (2026-10-15, Hig: "let's try out the approach")

`topobathy/condition.py`, a pre-pass of the build (with the water-level pins): a layer with a
`condition` block is a PRIOR. Read coarsely (20 m lattice for a 500 m length), the residual
reference - prior is taken at every cell where a named reference layer has bed (the prior's
`below_level` bounds both sides; |dz| > max_dz_m dropped), carried over the prior's domain as a
screened-harmonic field (Laplace with decay length `length_m`, pinned at the references, Neumann at
the edge), written as $out/cond_<layer>_dz.tif and _sigma.tif; the build adds dz on read
(stack.read_layer) and takes sigma from the raster (1.5 x the nearest reference's sigma at its cells,
growing to the prior's own over the length). In the rules a prior is gated like a modelled surface
(half gate, axes.state_rules_sequential): any real observation of the class beats it whatever its
sigma or date, priors rank among themselves by error. Two things learned on the way: with the full gate
a conditioned OCM dated 2023 beat Hydropalooza and H12146 outright on the time axis (bathy2009 6.4 ->
0.0 km2 in the first try), hence the half gate and the recipes dating OCM 2019 (its bathymetry is the
2018/19 lidar plus interpolation); and the report writer assumed every pin was a water level.
Recipes: ncei2010 against [bathy2009, h12146, homer2018, ocm2023] / ocm2023 against [bathy2009,
h12146, homer2018] at Bluff Point; ncei2010 against [bathy2009, seldovia2019, ocm2023] / ocm2023
against [bathy2009, seldovia2019] at Seldovia; length 500 m. The pre-pass takes 2-20 s.

Measured, first pass (out_v1) vs conditioned (out), same recipes otherwise:

| | Bluff Point v1 | v2 | Seldovia v1 | v2 |
|---|---|---|---|---|
| regional-grid cell at the lidar's edge (<= 6 m): composite - lidar, NMAD | 0.27 | **0.12** | 0.84 | 0.53 |
| ... share of those cells stepping more than 0.5 m | 28 % | **3 %** | 56 % | 41 % |
| regional-grid cells 4-20 m from an H12146 line: composite - line, NMAD | 0.13 | **0.06** | - | - |
| v2 - v1 where a regional grid won, 2-5 m: median / p95 | | +0.38 / +1.71 | | +0.14 / +1.57 |
| v2 - v1, 5-10 m: median / p95 | | +0.10 / +2.75 | | -0.01 / +0.63 |
| source areas (km2) | unchanged to 0.06 km2 in every layer | | unchanged | |

At the seam site the composite now reads -20.0 at the H12146 line (H12146 -19.95) and runs
continuously between lines where it stepped 2.9 m; at the 64 ha gap NCEI meets the lidar at -2.97
against the lidar's -2.92 (was -4.07 vs -2.92). The regional grids are pulled UP by 0.4 m median and
up to 1.7-2.8 m (p95) in the 2-10 m band, the size of NCEI's measured nearshore error. Seldovia
improves less: its regional-grid edges are mostly the 2019 lidar's kelp voids, where OCM on its own is
0.6-0.7 m NMAD (8.1) and the lidar's own edge is kelp-affected; what remains there is the kelp
question, not the prior. In the dev viewer as `bluff_point_topobathy_v2` and
`seldovia_bay_topobathy_v2` beside the v1s. Not done: the lagoon (channel-by-shape, 8.1), the lakes
(near-flat detector, 8.1), a per-depth sigma for the priors, and the Grewingk recipe (no bathymetry
layer at all, L6) - the obvious next user of this, with Hydropalooza as the prior's reference.
Grewingk 2021 point statistics now cover the whole survey (`obs/grewingk_2021_full`, 649 M points,
712 s); `rules_grewingk`'s water test changed from smooth to LEVEL (3x3 range < 0.02 m, returns or
not) at the same time; the smooth-rule classes are kept beside it as class0_smooth.*.

### 8.3 Water bodies (stage B) built; the two patches at v3 (2026-10-15, evening)

Hig's call after the lagoon came out 5 m deep (it is ~1 m): vector water-body maps as the carrier for both the
stitching and manual review.  Design and the lessons of the day are in TOPOBATHY_PLAN.md, "Water bodies
(stage B)"; code `topobathy/water.py`, `composite.py water`, `tools/lidar/water/edits.geojson`,
`tools/lidar/topobathy_chain.sh`.  Measured on the two patches (depth below each body's level, p50/p90/max,
v2 -> v3; bounded reads over each body's box):

Seldovia (24 bodies: 4 sea, 5 tidal, 5 perched, 5 lake, 5 river; KBay sea level that day -0.02 m, range
to +0.30):
- the lagoon pool, now body 566, perched, 19.2 ha at +0.75, Hig's edit (dmax 1 m): 0.1/3.6/5.6 -> 1.5/1.5/1.5
  m (the floor level - dmax - 0.5; OCM's -1.4..-2.4 under it are void, its -0.3..-0.7 cells admitted as bed
  and pinned exactly -- the flat 1.5 m is the floor, not a basin: the fill pins raster bed observations
  without their sigma, open item);
- perched bodies 6 (+2.25, 9.9 ha) 1.4/2.5/4.0 -> 1.0/1.5/2.6 and 8 (+3.70, 4.9 ha) 2.1/4.0/5.3 ->
  1.5/2.8/3.7 (the cap 1 m; what stays deeper is the 2019 green lidar, sigma 0.3, which the floor leaves --
  real bed or kelp, the 8.1 question);
- Hig's three lakes (59.44275/-151.75282, 59.42845/-151.75636, 59.43134/-151.75742) are bodies 20, 24, 23,
  0.3-0.6 ha, found by `flat_lakes` (two were void in KBay's classes: no returns at all) and given the generic
  lake basin (1.8/3.5/4.3 m for the 27.65 m lake -- the Hollister/glacial estimate, an edit would set it);
- the sea and tidal bodies unchanged (observations).

Bluff Point (4 bodies, all lakes; no sea in the Homer 2019 footprint): Hig's lake (59.65119/-151.63721,
body 6, 1.1 ha at 22.03) 0/0/0.1 -> 0.2/0.8/1.4 m; the other three 0.3-0.7 m.  It had never been a hole:
Kenai 2008's "ground" at 22.2 (its water surface) stood over Homer 2019's water, which the rules could not
displace -- hence the ceiling eviction in `state_rules_sequential`.

Both are `*_topobathy_v3` in the dev viewer; v2 outputs kept as `out_v2`.

**8.3a Hig's v3 review (2026-10-15, late): the detector took a mudflat, missed a reservoir.**  At
59.44597,-151.74439 v3 had carved 1.5 m into real lagoon floor (observed ground at 0.74-0.93 with 1-4 cm of
relief): `flat_lakes` on KBay had re-classed 14 ha of exposed, wet flats as water, they joined the pool's
body (19 ha), and the edit's floor was applied to all of it.  On every surface measure the flats and the
231 m lake are twins (0.2 m of coherent relief, 0.65 % slope, a basin ring), so flatness cannot separate
them; RETURN DENSITY does: inside the detected patches the lakes hold <= 8 returns per 2 m cell in 63-99 %
of their cells (the reservoir 99 %), the mudflat 24 % (it returns like dry ground, ~20 a cell), the
vendor-classed pool 22 % (its water class came from class-9 returns, 7 a cell, and is not the detector's
business).  `flat_lakes` takes `density` (the observe_points `cellstats` n_all band; a patch must be mostly
sparse), optional for DTM-only surveys.  The dammed reservoir at 59.44082,-151.68533 is VOID in KBay (no
returns) and its DTM value the vendor's TIN stretched inlet-to-dam at 1 %: void cells now skip the flatness
and slope tests, a void-dominated patch takes the loose level range (1 m) and its level from its LOW end
(the TIN bows up between shores: the median stood 0.9 m over the dam end, and the ring read "below" there).
v4: the mudflat is the observed ground again (v4 = v2 there), the pool body is 5.6 ha at 1.5 m, the
reservoir is body 567 (0.5 ha) -- kinded "river" because its TIN-derived levels slope, with the generic
basin (2.6/4.4/5.0 m; Hig: ~7 m behind the dam, a mapped-points case).  Open: a void-dominated observation
should be flat by construction (its level the TIN's p10, kind lake), which needs the class step to say
"void water" to the body stage.  No sill detector exists: a perched body's level IS taken as its sill; the
carving came from the body being 14 ha too wide, not from the sill.

**8.3b Hig's v4 review: a trough with spikes along the pool's rim.**  Not the body stage: at the trough
cells KBay's own returns read 0.70-0.80 (the lagoon floor) and its DTM 0.74, yet the composite read -2.3
and credited KBay.  The `rules_post` feather (30 m) ramped KBay's mask from 1 to 0 across its own WATER
hole, and the fold blended its rim with OCM's -2.3 m beneath -- the trough deepening toward the pool,
"spikes" where the blend varied; it was there in v2 too, under the channel model.  The feather is for
seams, never for a layer's own class holes: it is now measured from the edge of the mask UNION its
water/void/canopy holes, so a hole keeps a hard edge and a seam between surveys still blends
(`composite.post_masks`).  v5: deep cells in the first 6 m outside the pool 704 -> 19; the rim reads
0.56-1.28 (the floor); the pool 1.1/1.4/1.5 m.  Bluff Point's "abrupt depth change" of 8.1 is worth
re-checking under the same rule.

**Hig's hand-mapped lakes as a source (agreed 2026-10-15).**  `tools/lidar/water/mapped_hig_lakes.geojson`
(from Hig Lakes UTM5.shp, 3,144 polygons: 1,641 lakes, 1,403 islands, 74 rivers; `Class` on 135) and
`water/sources.json`.  Measured against v4: every detected lake and the reservoir sit in a mapped polygon
(70-100 %); two detected bodies are unmapped (Bluff body 11 at 174 m, Seldovia body 9 at +1.48 m);
the map holds water the detector misses -- Seldovia 12 of 19 polygons (2.9 ha, one 1.55 ha lake with no
detected water), Bluff Point 14 of 17, nearly all small Manmade ponds under the 2,000 m2 minimum.  Three
uses, never a stamp (a polygon has no level and may predate a shoreline): the detector's prior (inside a
polygon: 100 m2 minimum, no ring test; flatness, slope, density and the void rule still decide the cells),
mapped islands in the hole rule, and the report (`water.json` mapped_missed / bodies_unmapped; each body
carries mapped_frac / mapped_kind / mapped_name).  Mapped-only bodies (a mapped lake with no lidar water,
level from the DTM inside the polygon) are the next step.

**8.3c v6: the mapped lakes in the build (2026-10-15, night).**  The detector's mapped prior is its own
pass: the flat/void cells inside each polygon form their own patches, 100 m2 minimum, one level, no ring
and no density test (the map outranks the heuristic: a pond with vegetation at its surface returns like
ground) -- in the general pass a 1,636 m2 void lake had merged with the flat shore around it into a
ground-dominated patch the density rule rejected.  Result: Seldovia's mapped polygons with no body went 12
-> 6 (2.9 -> 0.22 ha, all under 600 m2); Bluff Point 14 -> 8 (all small Manmade ponds under 450 m2), and
Homer 2018 now sees the lakes too, so Hig's lake has two surveys (22.28 / 22.03 m, spread 0.25).  Unmapped
bodies: Seldovia's three perched lagoons and the +1.48 m shore body; Bluff body 11 (0.27 ha at 174 m).
"Dry" now means ground or bed observed more than flat_tol_m from the water level (a survey's returns off
the water, classed ground, said "ephemeral" at Bluff Point); Hig's lake still reads ephemeral because
half of Homer 2018's ground over it lies > 0.3 m from 22.28 -- its 2018 surface over the water is rough or
at another stage; the label does not change the fill (ephemeral is a flat kind) and is a review item.
Both patches are `*_topobathy_v6` in the dev viewer.


### 8.4 Hig's v6 review: the tidal lens (2026-10-16)

Hig's sites, each traced to its mechanism (62 m windows, every deciding raster):

| site | what v6 showed | why |
|---|---|---|
| the "pool" 59.44675,-151.75007 | a flat bed at -0.59 under water at +0.80 | the perched cap (1 m) + floor slack; the edit's ~1 m was a guess |
| slope above it 59.44679,-151.75056 | a 1.4 m step from the carved pool to ground at +0.98 | the basin's wall against observed ground |
| steep basin 59.44390,-151.74492 | -3.3 m pit, 1,400 m2, on a flat at +0.66 | a KBay RETURN HOLE (354 void cells, no water returns); OCM's satellite-derived bed showed through as "bed below MLLW"; the reclass DTM bridges it at 0.66 |
| channel deepens 59.44528,-151.73995 | the creek at +0.28 steps to -2.7 | OCM admitted under the sea body wherever it has data: the step is OCM's edge |
| troughs 59.44750,-151.74125 / 59.44199,-151.73801 | -3.6 m along the beach/flat junction | the sheet of water along the beach (sea body at -0.03) read as a TIDAL CHANNEL by width (24-43 m wide -> 2.4-4.3 m deep) + OCM |
| basin 59.43838,-151.70701 | -0.57 under water at +1.33 (v2 left it at 1.36) | body 9, kind "river" (sloped 1.18-1.70): the river channel model at 75 m width dug 5.8 m |
| Trena Lake plateau 59.45214,-151.70808 | flat at 2.20 between green-lidar bed at 0.2-0.6 | body 8 "perched" (3.70 < tide_max 4.5): the perched cap's floor 3.70 - 1 - 0.5 = 2.20 applied where the green lidar had no data, above the measured bed around |

**Returns, not flatness.**  The pool was classed water by the vendor's class 9 outvoting ground (3,011
water vs 2,031 ground returns over the 961 cells; returns sparser, median 9 per 2 m cell against 21 on the
mudflat 300 m away, 16 % of cells with none); `flat_lakes` added nothing there.  That evidence does not
separate water from mud too wet to return, which is Hig's point: it may never have been a pool.

**The tidal lens** (Hig: "low-relief intertidal areas tend to have a lot of sediment that moves around and
makes for quite flat surfaces. I would rather back off and make conservatively flat assumptions to start
with, and then measure").  Water the tide reaches -- kinds sea / tidal / perched (`TIDAL_KINDS`) -- is read
differently from a lake: its observed surface IS the surface until a measurement says deeper.  Built:
(1) `fill.py` `tidal_lens` (default): a marine domain's bed is its observed water surface (`water_z` per
cell, smoothed over 20 m, else its level), sigma 1 m at the shore + 2 m per 100 m; no low-tide floor, no
channel by width, no perched cap; measured beds under it (multibeam, green-lidar bed, soundings) still
condition it; the surface is a ceiling the conditioning never lifts the bed above; an edit's depth still
makes a basin.  (2) `water.py`: a sloped surface below `tide_max_m` is "tidal" (a creek draining the flat),
not "river"; a perched body Hig's map calls a lake is a lake; `intertidal.tif` = the tidal-kind bodies plus
everything within `intertidal_near_m` (100) of ground or water any survey observed between the fill's
`low_tide_m` and `tide_max_m` (Seldovia 9.5 km2, Bluff Point 4.2 km2).  (3) `below_level` `"intertidal":
"void"` on the prior layers (OCM, NCEI): no bed inside that zone.  (4) the pool edit withdrawn (kept in
`edits.geojson` with Hig's reason; `withdrawn` edits are not applied).  (5) a measured bed inside a lake
lowers an edit's floor to the measurement.

**v7** (`seldovia_bay_topobathy_v7`, `bluff_point_topobathy_v7`): the pool reads 0.79 flat (= the raw DTM
0.80); the slope above it is the observed ground (0.84-1.34) against 0.80; the return hole reads 0.66 (= the
DTM; a dry void domain, harmonic from its rim); the creek 0.20-0.24 at its surface; both troughs are now
monotone ramps from the beach (-0.05 / -0.08) down to the first measured bed 80-100 m out (multibeam at
-1.6 / -1.7, green lidar -2.1); the slough (body 9, now "tidal") 1.37 flat; Trena Lake follows the green
lidar's bed (0.08-0.99 where v6 had the 2.20 plateau); the mudflat untouched.  Depth below the level
(p50/p90/max): pool 566 0.0/0.0/0.1 (v6 1.1/1.4/1.5); body 6 (+2.25) 0.5/1.3/2.7 and Trena 2.2/3.3/3.7 --
both measured by the 2019 green lidar, not modelled.  Consequences to judge: body 571 (0.9 ha at +2.11,
mapped by Hig as a lake) is now a lake with a 2.8 m basin where v2 had 0; the sea domains drop 48,542 cells
of KBay "ground" within 0.2 m of the sheet as evidence (the lake rule applied to the sea; refilled at the
surface, so harmless by construction, but a thing to watch); Bluff Point saw no sea surface, so its only
change is the priors voided within 100 m of the shore -- its v1 "abrupt change" site reads a smooth NCEI
slope at -20 m.  Grewingk had 390 "marine" domains under the old floor conception; its regression with
the lens is in the roadmap memory / the resume block.

**Hig's v7 review (2026-10-16, ~1 am): "they look really good"; one oddity, a very circular lump at
59.63812,-151.60191.**  A 150 m round patch of Kenai 2008 "ground" at -0.9 m standing 2.5 m proud of OCM's
bed (-3.5), 300 m off Bluff Point, 1.2 km from shore: the 2008 lidar's returns off the sea at its tide,
round because a lidar gets its water returns in a spot near nadir, classed ground because the Kenai layer
had no `water_level` step (Homer 2019 has one), and in v7 the intertidal rule -- "within 100 m of ground
OR WATER between MLLW and tide_max" -- made the patch its own intertidal zone and voided the prior
around it (in v2 the prior had simply outranked the older ground).  Two minor tweaks: Kenai 2008 gets
`water_level` (tol 0.3, min_frac 0.02); the intertidal zone is anchored to observed GROUND only, plus the
tidal-kind bodies that touch that dilation -- a detached sheet of water far from any exposed ground says
nothing about flatness, and a prior under its ceiling stands.  v8 of both patches rebuilt with those.

**Grewingk under the lens (clean A/B, identical inputs = the v6 state, `out_lens/` vs `out_nolens/`):**
2.9 km2 changed by > 0.05 m, mean -0.01 m; > 2 m on 9.5 ha inside the 90 marine domains (+1.1 m: the
bay-front sheet instead of the -2.9 m floor) and on 4.1 ha outside them, in the lake near the ice face,
where the soundings spline and the lower-bounds pass respond to the changed evidence set (the sea domains
now drop their at-level ground as evidence, 10,148 cells vs 3,693); one ice-face cell flips 127 m.  The
no-lens code reproduces the v6 fill exactly.  Grewingk's `out/` is the v6 state and the dev viewer's v6 is
untouched; the lens goes into Grewingk when the creek work starts.

**The whole-bay run (Hig: "set up a run of the whole KBay -- I'm headed to bed so the processor is all
yours").**  `composites/kbay_2023/`: `template.json` (the two patch recipes merged: ctx3dep with the
full-survey class.vrt footprint, NCEI + OCM priors conditioned against bathy2009 / H12146 / Homer 2018 /
Seldovia 2019, both multibeams, Seldovia 2019 with the kelp test, Kenai 2008 with `water_level`, Homer
2018/2019, KBay 2023 reclass v2 with the whole-survey classes, density and mapped lakes; fill with the
soundings export; rates the defaults -- the Bluff patch's 0.05 is not carried), `make_boxes.py` (12 boxes:
16 km columns 557/573/589/605/622 km, rows 6566/6582/6598/6612 km, each built with a 1 km margin),
`run_boxes.sh` (chains in order of interest, BOX_DONE markers, crop to the nominal extents, VRT -> COG
`composite/kbay_2023/kbay_2023_topobathy.tif`, dev viewer `kbay_2023_topobathy_v1` at z16).  Known
limit: a fill domain cut by a box edge is solved per box (seams in fills, not in observations).

### 8.5 The whole-bay run, v1 (2026-10-16, 01:48-16:01): what it shows, two defects, the fixes

**The run.**  Twelve boxes in 14 h (not the 3.5 h expected: the Homer box's three builds ran 48/84/64 min
beside the Grewingk A/B; the two eastern boxes spent 3 h and 1 h in the FILL on 100 km2 dry domains, see
below), crops, VRT, COG (1.7 GB, ZSTD, 32500 x 23000 at 2 m, no nodata anywhere), dev viewer
`kbay_2023_topobathy_v1` (z16, "Kachemak Bay topobathy v1 (whole-survey run, 2 m; tidal lens)").  No
error in any box log; every box's water stage found its bodies (r2c0, open Cook Inlet, none: no survey
sees water there; the priors cover it).  **The mosaic reproduces the approved Seldovia v7 patch to the
centimetre at all eight of Hig's sites** (pool 0.79, creek 0.22, troughs -0.40/-0.59, Trena 0.45, slough
1.36) and the Bluff lump as in v8c (-1.58 m): the merged template changes nothing the patches settled.

**Defect 1 -- invented mountains (the main one).**  The southern row and the east column
(r0c0-r0c3, r1c3, r2c3) are 29-41 % holes each, 680 km2 in all, filled as "interior" by the lake
machinery as ONE dry domain a box (r0c2: 104 km2, sigma 31 m).  Cause: the ctx3dep `fallback`'s
`footprints` rule counts every valid cell of the KBay class VRT as "flown", and the class tiles hold
VOID (0, not nodata) across the unflown remainder of every tile square and the mountains the survey
never reached -- 33 % of r0c2 is class 0 against 1.7 % ground.  So 3DEP was suppressed exactly where it
was the only surface, and the fill modelled a 300 m-smooth blob instead.  Seams follow: along y 6582 km
the north/south boxes differ by a median 365 m over x 589-595 km and 400 m over x 609-619 km; along
x 589 km by 100-210 m over y 6576-6587 km.  (Within the lidar the seams are 0.5-1.3 m medians in 1 km
bands of steep ground -- lidar holes solved per box, the known limit.)  The overview hillshade shows it
at a glance: smooth grey blobs with hard box edges and a spiky margin against the real ground.

**Defect 2 -- the Bluff lump (unchanged from v8c).**  In a 1.2 km window around 59.63812,-151.60191
the zone's anchors are 45,892 "ground" cells between MLLW and tide_max: 30,585 INSIDE the Kenai 2008
sea body (its exclusion evidently not reaching them in v8c) and 15,307 outside any body but a median 22 m
(p90 56 m) from it, in the sheet's return gaps and along its edge, at -1.34..-0.77 m -- Homer 2018
(12,977) and Kenai 2008 (2,330) returns off a low-tide sea surface, admitted as ground.  Dilated 100 m
they put the whole nearshore in the zone, OCM is voided and the lens sets the bed at Kenai's -0.85 m
sheet over OCM's -3..-4.5 m.

**Also seen:** the Gulf of Alaska side (r0c1-r0c3 south of the divide) reads 3DEP's sea as flat 0 m
ground (101 km2 of exact zeros) and that "ground" anchors 139 km2 of intertidal zone in r0c3 -- harmless
there (no prior to void, no body to lens) but the ctx3dep layer should class its exact-0 cells water,
not ground, as the Grewingk recipe already does for 3DEP lakes.

**Fixes (built 2026-10-16 afternoon, general cases; test rebuilds of Bluff and box r0c2 running):**
1. `axes.py` fallback `footprints` gains `near_m` (default 300): a footprint's void counts as flown only
   within 300 m of that survey's returns.  Open water a lidar flew lies within a few hundred metres of a
   shore or a water-surface return (Halibut Cove Lagoon's multibeam lines, the case the rule was built
   for); ground a kilometre from any return was not flown and 3DEP stands.  Coarse distance grid (~25
   cells), so it costs nothing.
2. `fill.py` `dry_max_km2` (default 1.0): a dry (void) domain above it is left VOID with a log line --
   the fallback is the honest surface there, and a hole says so where even that is missing.  It also
   removes the 3 h fills.
3. `water.py` zone anchors: ground lower than a tidal body's water surface (`level_hi_m` + perched_tol)
   observed within `intertidal_near_m` of it is dropped as an anchor (logged) -- the sea is level, so
   ground below a surface seen 50 m away is under it: a bed or a water-surface return.  The beach above
   the sheet's level still anchors the zone.

**Afternoon, iterating on the two test boxes (Bluff v9a-g, r0c2 three times).**  Both defects had more
layers than the first diagnosis saw; every layer got a general rule, none a site rule.

*The fallback.*  `near_m` alone changed nothing: the real blocker was NCEI's LAND cells, classed void by
its `below_level` yet counted as "a survey has a cell" (the rule said "whatever its class").  The
fallback layer's class is now computed AFTER the other layers' (composite.py defers a layer with a
fallback step to the end of pass 2) and a survey's cell counts only if its class is not void.  r0c2:
holes 32 % -> 1.7 %, 3DEP shown over 297 km2, the fill 5 min instead of hours.  A 5 km2 dry void stayed
(void within 300 m of scattered KBay snow/ground returns on the ridges), so the fill now writes
`fill_dryvoid.tif` (the dry domains it left empty) and the fallback step's `dry_void` ("$out/...")
un-sees those cells on build 3 -- 3DEP after all, nowhere a hole where 3DEP exists.

*The lump, four layers deep.*  (1) The zone's anchors were 290 SPECKS of Kenai 2008 "ground" at -0.5 m
(wave-crest returns above its -0.85 m sheet, 4-200 m2 each) scattered over the shelf, each making a 100 m
disc of zone -- Hig's "very circular lump" was the zone's own dilation geometry.  Anchors are now PATCHES
>= `anchor_min_m2` (the body minimum, 400 m2).  (2) The specks themselves then stood as the surface:
Kenai 2008 and Homer 2018 get the `islets` step (ground under 200 m2 ringed >= 80 % by water / void / bed
-> water; bed added to the ring's "wet").  (3) Homer 2018, a green lidar, returns the SEA SURFACE at its
flight tide (-1.6..-0.5 m) as a smooth sheet (roughness 0.03 m), which `below_level` had split into
"bed" (below MLLW) and "ground" (above) -- 67 % of its cells; `flat_below` (flat over 10 m, below 0 m
NAVD88, blobs >= 2000 m2, run after below_level on every class) makes it water: Homer 2018 shown
10.4 -> 3.2 km2, OCM's measured bed now shows under it (the 600 m window at the lump: OCM 68 -> 93 %).
(4) The last -0.48 m cell: NCEI's conditioned -3.9 m blended toward ZERO by `fold_step`, which folded a
NaN cell at weight 0.87 as 0 m (the feather underlay reaches cells the lower layer has no value for);
a NaN now contributes nothing.  That one is general and old: every feathered seam beside a hole could
have been pulled toward 0 m.

*Also built:* every observed water surface is a CEILING for beds of any date (`state_rules_sequential`
keeps the lowest water surface seen and its date; a bed above it by more than CEIL_TOL_M + rates["bed"]
x the years between is not taken, and a running bed above a later water observation is evicted); it
did not decide the lump (the Bluff recipe's bed rate 0.05 m/yr x 15 yr is a 0.95 m tolerance) but it
is the physics.  Open: a Homer-type sheet body classed "tidal" (not "sea": it does not touch the box
edge) attaches to the zone WHOLE when it touches the beach dilation (11 such at Bluff); the attach rule
should want the body mostly inside the dilation, not merely touching.

**Evening: the last layer, the product file, v8 published, the bay re-launched as v2.**  (5) After the
fold fix the -0.48 m cell still stood, and a 200 m windowed replay (`build_window` at the lump, every
layer's z / class / weight at one cell) showed why: OCM's cell is classed VOID (its -0.54 m SDB value is
above the water cutoff) yet shown at full weight -- a post-processing step (the despeckle closes small
holes in a take mask) re-admitted it.  `post_masks` now zeroes a layer's weight wherever its class is
not a surface (void / water / canopy), whatever the post-processing did; the feather underlay got the
same test.  Lump centre: -1.48 (v7) -> -2.93 m (NCEI conditioned), OCM wins 93 % of the 600 m window
(68 % in v7), the Homer 2018 sheet is water.  (A caution for probes: `build_window` crops its halo from
the layer arrays but not from `info["_cls"]` or `rules["masks"]`, so a per-cell comparison of those is
offset -- one false lead cost half an hour.)

**The product is build 3's `<id>.tif`, not the fill stage's `<id>_filled.tif`.**  The chain's last
build folds the fill in as a layer and (now) shows the fallback in the fill's dry voids; at Seldovia the
two differ on 0.4 % of cells by > 5 cm (the fill-surface class), at r0c2 build 3 covers 100 % where
`_filled` had a 4.9 km2 hole.  `run_boxes.sh` crops `<id>.tif`; the v8 patches are published from it.

**Regression.**  Seldovia (no Kenai / Homer 2018 layers, so only the shared rules changed): all eight
sites within 4 mm of v7 except Trena +7.8 cm (0.45 -> 0.53: the fold had been blending the green-lidar
bed toward 0 m at a feathered edge -- the NaN fix).  Bluff: valid 1.0, Homer 2019 41 %, OCM 23 %,
the two multibeams 25 %, Homer 2018 3 % (10 % in v7).  Box r0c2: holes 32 % -> 1.7 %, 3DEP 93 %.

**Published:** `seldovia_bay_topobathy_v8`, `bluff_point_topobathy_v8` (dev viewer, titles as v7 with
v8).  **Launched 18:48:** the whole bay as `kbay_2023_topobathy_v2` (`KBAY_VER=v2 run_boxes.sh`, the
same 12 boxes, log `composite/kbay_run.log`), v1 left in place for comparison.  Still open: the
Gulf-side 3DEP sea at 0 m as "ground" (class the context layer's exact-0 cells water); seams inside the
lidar from fill domains solved per box (0.5-1.3 m medians on steep ground); a tidal body attaches whole
at >= 50 % inside the dilation (`attach_frac`), untested beyond the two patches.

### 8.6 The whole bay, v2 (2026-10-16 18:48 - 10-17 01:13; `kbay_2023_topobathy_v2` in the dev viewer)

6.4 h for the twelve boxes (14 h for v1; the Homer box 2 h because the patch rebuilds and publishes ran
beside it, the rest 9-28 min, r0c3 65 min), mosaic 1 min from build 3's COGs, bake 7 min.  **The
blobs and the box seams are gone**: across the five box edges the step between neighbouring cells is
the same as one cell inside (medians 0.08-0.57 m seam vs 0.07-0.57 m inside, > 2 m on 0.5-5.6 % vs
0.4-4.5 % -- the steep-ground seams of the lidar's own holes solved per box remain, as known).  Valid
99.9 %: 3.9 km2 of nodata, two ridge-top strips in r0c2 (x 594-605 km along y 6579-6582 km) where
the dry void the fill left met the box margin -- the dryvoid raster covers the margin but the fallback
in build 3 did not reach every cell; small, to look at.  Exact zeros 4.2 % (122 km2): the Gulf-side
3DEP sea at 0 m, now also at Port Graham (r0c0, 21 km2) where v1 had modelled it; the open item.
3DEP shows wherever the lidar did not fly; its edge against KBay's snow-covered ridges is a hard step
along the class tiles' squares (x 609-614 km, y 6586-6589 km -- the lidar's own edge, honest but
unfeathered: the base layer is never feathered).

**One regression against the patches: the Seldovia pool.**  v2 reads 0.06 m at the pool (v8 patch and
v1: 0.79), -2.1 at its p10.  Inside the box's pool body 43 rim cells carry OCM / Seldovia 2019 "bed"
at -2.5 m, which the fill's conditioning spreads over the pool; in the patch those cells are the fill
at 0.78.  Two differences between box and patch found: the water stage ran at 4 m in the box (the
9000-cell grid coarsened above `max_px` 6000) and at 2 m in the patch, so the bodies and the zone
differ at their rims; and the chain's first build read the PREVIOUS run's `intertidal.tif` /
`water_bodies.tif` through the recipes' `$out/` steps (stale in both box and patch).  Fixed: the chain
deletes the stage products before build 1; the template's water `max_px` 9100 (the body stage at the
grid's 2 m for a 16 km box).  r1c0 rebuilt with both to test; result in the next entry.

*The ridge-top nodata strips (r0c2, 5.1 km2 in the box, 3.9 in the mosaic) are the same stale-product
hazard*: the v2 run's build 2 folded in the `fill.tif` the afternoon's test rebuild had left in `out/`
(the fill layer is "optional": used when present), so the fill stage saw 0.04 % holes instead of 1.7 %
and wrote no dry void; build 3 then folded the new fill and had nothing for those cells.  Every v2 box
started with the v1 run's products in `out/`, so v2 is not a clean run of the new rules; the chain now
deletes them before build 1, and the bay goes again as v3 once r1c0 confirms the pool.

*r1c0 with the clean chain and the 2 m body stage (01:19-01:39, 20 min):* the box reproduces the v8
patch at all eight Seldovia sites (pool 0.791 vs 0.791, Trena 0.530 vs 0.530, the rest within 2 mm).
**The bay re-launched 01:41 as v3** (`KBAY_VER=v3`, markers cleared); v2 stays in the dev viewer until
v3 replaces it for review.  Expect ~6 h (the 2 m body stage adds a few minutes a box).

**v3 (01:39-10:36, `kbay_2023_topobathy_v3` in the dev viewer) -- the clean run.**  Valid 100 %
(0.02 km2 of nodata: three slivers of a few cells at box corners), the ridge-top strips gone, seams as
v2 (indistinguishable from the interior), the body stage at 2 m in every box, and **the mosaic matches
the v8 patches at all eleven review sites** (Seldovia pool 0.791 / 0.791, Trena 0.530 / 0.530, the Bluff
lump -2.93 / -2.93; the rest within 2 mm).  9 h because the Mac ran at a crawl between 02:36 and 07:05
(r1c0's first build 64 min against 3 min in the 01:19 test of the same box; every stage of r1c0 and
r1c1 slowed alike), then 13-29 min a box.  The known limits stand: the Gulf-side 3DEP sea as 0 m
ground (122 km2 of exact zeros), the unfeathered 3DEP edge on the snow-covered ridges, the per-box
fill seams on steep ground.  Dry voids left to 3DEP: 3-14 km2 a box in the mountains (none in the
three bay-front boxes).  v1 and v2 stay in the dev viewer for comparison until Hig has looked.

### 8.7 Hig's v3 review: the lakes (2026-10-17)

Hig: the oceans look good, the lakes are confused -- Turquoise has seams and low-gradient sides where
steep ground should run on under water; Wosnesenski is gentle with deep holes; China Poot (Riordan's
soundings) is mostly flat with odd deep parts while the upper lakes are right; Grewingk mostly
undeveloped with oddities at a seam; lakes outside every lidar form no basin (three in the mountains,
Gore Lake); and a rerun ~75 % larger to the north (the head of the bay, Anchor Point).

**Measured (v3 boxes, 600 m windows):** China Poot: NCEI wins 90 % of the lake, class_now water 98 % --
NCEI's hydroflattened surface a few cm under the lidar's level, admitted as BED by `below_level` with
"bodies" (the cutoff is the body's own level, a rule built for the Seldovia lagoon), so the soundings
only act in the 10 % holes (the "odd deep parts").  Turquoise: NCEI 10 %, conditioned 22 % -- the
shelf and the seams.  Wosnesenski: NCEI 6 %, the gentle floor with holes where NCEI dips.  Grewingk's
middle: 3DEP (2012, the glacier surface) as ground -- the lake is KBay void more than 300 m from any
return and the `near_m` rule let the fallback through.  Gore and the three mountain lakes: 3DEP ground,
flat -- the template's context layer had no water class (Grewingk's recipe classes 3DEP's hydroflattened
patches as water through `water_3dep.tif`).  Gore Lake has no soundings in the export (Riordan 2018 =
China Poot, lake_66m, lower / upper Hazel).  Turquoise sits on the x 605 km seam and Wosnesenski on
y 6598 km: lakes cut by a box edge are solved twice.

**Fixes, general:** (1) `below_level` "fresh": "void" on the NCEI / OCM steps -- inside a non-marine
body (`water.fresh_raster`: kind not sea / tidal / perched) a regional prior is never bed.  (2) the
fallback never stands inside an observed water body (`"bodies": "$out/water_bodies.tif"`).  (3) the
context layer reads `obs/water_3dep.tif` (topobathy/flat_water.py, now built by the chain before
build 1 for any recipe that names it): 3DEP's exactly-flat patches are water, so a lake outside every
survey is a body with a basin and 3DEP is a ceiling, not ground, inside one.  (4) the box layout:
x 557-635 km, y 6566-6636 km (5 x 5 boxes of 12-16 km, 2 km margins, `max_px` 10100), the edges
chosen by a search that cuts no mapped lake over 5 ha (x 572.5 / 587.5 / 603.5 / 619; y 6579.5 /
6591.5 / 6604 / 6620).  Tests on r2c2 (China Poot) and r1c3 (Turquoise, a 3DEP-only lake) running.

**Tests.**  r2c2 (China Poot): the lake is now one fill domain conditioned by Riordan's 157 soundings
(spline misfit 1.0 m; bed -15 m at p5, +7 m median under a 50.5 m surface), the upper lake unchanged,
Lower Hazel conditioned by its 202 soundings (28 m deep) -- but classed "river" by the level test (the
lidar's returns off a 94 ha lake spread more than flat_tol_m 0.3), as was Upper Hazel in r1c3; a mapped
LAKE now stays a lake whatever the level spread (the perched override extended; the consensus level is
the weighted mean of the survey levels).  r1c3 first pass: Turquoise is a 31 m basin (Hollister 27 /
glacial 36 m, rim slope 19.5 deg; NCEI gone from it); the 3DEP-only lake (8.6 ha at 789 m) became a
body but no basin: the new "no fallback inside a body" rule had voided the fallback's own WATER class
there, so the domain's water fraction was 0 and the lake test skipped it.  Fixed: the fallback withdraws
only its ground.  And `flat_water.py` leaves out patches at or below 0.5 m (`--min-level`): 3DEP holds
the sea as exactly 0 m, and the sea is the priors' and the tide gauge's.  r1c3 second pass running.

*r1c3 second pass:* the 3DEP-only lake is a basin (778-788 m under its 789 m surface, Hollister), Turquoise
31 m, 42 lakes in the box.  **v4 launched 21:00** over the larger extent (25 boxes, `KBAY_VER=v4`,
`kbay_2023_topobathy_v4` when done; ~10 h).  Open after v4: Gore Lake has no soundings in the export
(ask Hig where its bathymetry is); the two Hazel lakes' "river" classing is fixed by the mapped-lake
rule but untested in a build; lakes cut by no box edge now, but a fill domain crossing one is still
solved twice.

**Gore and Petrof depths (Hig: "a file with Gore and Petrof depths").**  Already ingested 2026-10-13 as
`higman_petrof_gore_2022` (59 lead-line points, 2022; `tools/soundings/ingest_higman_glacial_lakes.py`),
but with their lake LEVELS unknown, and the export writes only points with an elevation.  Levels set
2026-10-17 from 3DEP's hydroflattened surfaces (the flat-patch median; no KBay lidar reaches either
lake): Gore 1.59 m, Petrof 8.42 m NAVD88, sigma 1 m, the source recorded in the package and in the
ingest's `KNOWN_LEVELS` so a re-ingest keeps them; reloaded, exported as `soundings_20261017.csv`
(6,717 soundings), the template's `fill.points` and the box recipes updated while v4 runs (the Homer
box had started; Gore's box r0c3 and Petrof's r1c4 come later in the order).

### 8.8 Hig's v4-partial review: Beluga Lake's corner and Wosnesenski Lake (2026-10-18, small hours)

**Beluga Lake, south corner (59.63880, -151.51565):** Homer 2019 bridges a data gap with its vendor TIN
and the composite follows it 0.7 m (up to 4 m) above the 2018 surface.  Measured: in a 240 m window the
2019 DTM is planar to 2 cm over 4.5 m on 76 % of its cells (plane residual p50 0.004 m), the 2018 DTM
on 28 % (p50 0.033 m) -- real ground is never that flat; the two differ by > 0.5 m on half the window.
Neither point cloud is in the stack (both layers are the vendor DTMs; Homer 2019's cloud is 11.6 GB on
the Elevation Portal, unfetched).  **The universal rule is planarity:** a new class step `facets` (win_m
10, resid_m 0.02, min_area_m2 200 -> "modelled") on both Homer DTMs; the rules never let a modelled
surface set the class and take it only where nothing real is known (the compat table's "ground" row
has no "modelled" entry), so Homer 2018's rough, lower ground wins across the gap and the facet still
stands where it is the only surface.

**Wosnesenski Lake:** the island near 59.50409, -151.04553 is real in both KBay DTMs (ground returns at
61/cell, up to 169 m, 54 m above the lake); what reads as made-up terrain are the fill's own CHANNEL
SECTIONS: the lake is 8.8 times longer than round, past `channel_elong` 3, so the fill treated it as a
river reach -- channel beds down its middle (fill_mask 6 beside the islands = the plateaus and ridges)
and the depth capped at Hollister's 6.1 m from the 3.7 deg median rim slope, where the lake estimate
(sqrt of Hollister x glacial, the glacial 69 m) gives ~20 m.  Fixed: a body the water stage or Hig's
map calls a LAKE never gets the channel model (its id in `channel_beds` skip list) and takes the lake
depth whatever its elongation.  v4 stopped at 12 boxes (00:45); r3c1 and r2c3 rebuilt to test; then v5.

*Tests (01:22):* Beluga -- the composite follows Homer 2018 across the gap (median difference 0.00 m,
0.85 m below the 2019 bridge; Homer 2019 lost 3.3 % of its cells to "modelled" box-wide).
Wosnesenski -- no channel sections in the lake, bed to 16 m at p5 (was 5 m), islands' skirts from the
lake prior only.  **v5 launched 01:25** (the two tested boxes kept, 23 to build; ~8.5 h -> ~10:00),
`kbay_2023_topobathy_v5`.

### 8.9 Hig's v5 review: best data and point-cloud water (2026-10-18, evening)

**Outer coast.**  Hig found Zimmermann's Central Gulf of Alaska 100 m grid on Powder
(`Alaska GIS/Raster/Bathymetry/so_ak_crm_v1_flt/CGOA_bathymetry_topo_1.sdat`, Alaska Albers, positive depth below
MLLW, two nodata values: -99999 and the float minimum).  Staged as `bathy_src/cgoa_zimmermann/` (raw copy,
`cgoa_navd88_100m.tif` = -depth - 1.38 m, 1,970 km2 over the v5 extent, to -276 m); in the template as layer
`cgoa`, the lowest marine prior (below NCEI), conditioned against every finer marine layer over 2 km, bed only
below MLLW, void in the intertidal zone and fresh bodies.  It fills the outer coast where v5 had no value.

**The marine seam (59.530-59.585 N, 151.576 W).**  H12090's 4 m band 2 of 4: its east edge column 21 m shallow,
and behind it a strip reading -76 m where both 8 m bands read -118 m.  `topobathy/clean_bands.py` writes a
cleaned copy of the 66-band stack (the published one untouched): a band's outer 2 cells are dropped where another
band covers them, and a finer band's cell is dropped where it disagrees with the coarser bands under it by more
than max(3 m, 5 % of depth).  2.1 M cells dropped, the disagreement test 0.01-0.6 % of a band.  Across the seam
the per-row largest step: p50 20.4 -> 0.11 m, max 42.7 -> 3.4 m.  Template `bathy2009` now reads
`kachemak_2008_2009_mllw_nad83_clean.vrt` with shift_m -1.38 (a path source does not get the manifest's shift).

**Grewingk merged into the template.**  sfm2023bare, grewingk2021 (+0.36 m, `lowest` 0.3 / 50 m), sonar2023
(Katie Hughes' multibeam -- GATED: dev only without her permission), dixon2023 (ice, retreat as water); kbay2023
reads lake_2021 / retreat_2023 as water; `as_of` 2023-09-19 (the Sept 2023 SfM and ice were otherwise dated
after it and dropped); fill ice_face free, shore_grad 200 / 150 m, n0 3; `points_dz_m` now takes a per-survey
dict (fill.py) -- +0.36 m for the four Grewingk surveys only; the 150 m depth moved to `water/edits.geojson`
(`grewingk_lake_depth`, level 48.91 m = the 2021 water surface raised with that lidar).  Box r3c3 rebuilding.

**Kenai 2008 point-cloud water.**  observe_points.py gained an EPT reader (`kenai2008`: the local mirror in
2 km chunks, noise 7 / 10 dropped, vendor 11-16 -> unclassified, 3857 -> 6334); 16 km2 in 16 s alone, ~23 min a
box beside a composite build.  The ten northern boxes it reaches are running (`obs/kenai_2008/<box>/`).  On
Beluga Lake the vendor called 89 % of the lake-surface returns GROUND; water there is sparser (3 vs 6 returns a
cell), darker (intensity ~3-8 vs 15-20), smoother and at the local bottom.  `topobathy/water_taught.py` (new):
nine features per cell (relative density, empty share, intensity absolute and relative, 10 m roughness, 14 m
range, height above the 100 m minimum, ground share, in-cell flatness), labels where certain (water: mapped lakes
and rivers 6 m in, OCM seabed below -5 m; land: KBay 2023 ground above 6 m, or Homer 2019's DTM above 6 m where
KBay did not fly, 20 m clear of mapped water), gradient boosting with whole boxes held out for precision and
recall, `predict` -> per-box class.tif + pwater.tif -> class.vrt for the kenai2008 layer's `raster` step.
Next: train once the evidence pass finishes, review the held-out scores and the maps at Hig's sites, wire it in.

**Later the same evening.**  Grewingk: the 150 m edit was first refused by the kind guard (the lake's surface
read sloped: Dixon 2023's SfM water at 53.8 m with a 3 m spread, 3DEP's 2012 lake at 41.3, the 2021 lidar at
48.8) -- the guard now refuses only MARINE against FRESH; with the edit applied box r3c3 agrees with Grewingk v6
within 0.5 m on 93 % of the shared cells (the rest is the marine flats west of the lake, the tidal lens), lake
min -121 vs -124 m.  In the dev viewer as `grewingk_topobathy_v7`.  The dev catalogue pruned to the current
versions (8 entries); the 26 older ones are in `data/lidar_dev/catalog_archive.geojson`, tiles kept.
2008 water: land labels in the north row from 3DEP above 10 m; a 4 km checkerboard hold-out: lakes precision
0.965 / recall 0.902, sea 0.861 / 0.999, 1.2 % of held-out land called water.  At Hig's sites: the tide-flat
sheet 77 % water, Beluga's head 98 %, the mapped part of Beluga's corner 96 % (p50 1.00).  Wired into the
kenai2008 layer (template, Bluff Point).  The Woz delta and river lie south of the 2008 survey (it ends near
59.59 N): what goes wrong there is KBay 2023's own classification (observe_points rules_kbay), next on the list.
**v6 of the whole bay launched 21:38** (24 boxes; r3c3 kept): the outer-coast grid, the cleaned multibeam,
Grewingk merged, 2008 water.

**Taught water on KBay 2023 and Grewingk 2021 (first round) -- not yet better.**  Held-out scores looked good
(KBay lakes P 0.98 / R 0.96, 0.6 % of land called water; Grewingk lakes 0.97 / 0.87, 2.8 %), but on Hig's sites:
the Woz delta's mapped water 56 -> 96 % water, while land above 5 m called water 2.7 -> 19 %; the Woz river 1.4 ->
5.9 % (the very fault Hig named, made worse); the Grewingk outlet stream 97 -> 3 % (silty, turbulent water looks
like nothing the model was taught).  Cause: every land label sat above 6 m, so "low and flat" meant water.
Round 2 adds LOW-LAND labels (surveyed ground 1.5-6 m, 50 m clear of mapped water, outside OCM's underwater
footprint) and scores them; for Grewingk the vendor water class stays.  Written as `water_taught.tif/vrt`
(KBay) and `class_v2.vrt` (Kenai), never over the class rasters the recipes read.  Also: `make_nohf_dtm.py --full`
built the 2021 DTM over the whole survey (obs/nohf_full; identical inside the old box) and the template's
grewingk2021 layer reads it with the full-survey classes (Hig: 3DEP was standing at 59.64797,-151.15728).

**2026-10-18 ~22:15: Nunatak disconnected** (not mounted, not listed by diskutil) during round 2 and the v6 run;
both died (runner exit 1, training exit 120), their logs on Nunatak.  Box recipes NOT regenerated since the
template took the full 2021 DTM.  To resume: remount; check `composite/kbay_run.log` for how far v6 got; rerun
round 2 (`water_taught.py train/predict` for kbay_2023_full/blocks --name water_taught and kenai_2008 --name
class_v2); regenerate the boxes (make_boxes.py); then the whole bay as v7.
