# Topobathy compositing: tools and interface

## State of play (2026-09-20) and how to resume

**Nothing is built.** This document is the agreed design, reviewed by Hig
over four drafts on 2026-09-20; the decisions below are his, not proposals.
A fresh session should read this file top to bottom. **Revision 2** (next
section) and the dated sections at the end supersede the original where
they conflict; the "Revised order of work" in Revision 2 is current. The
Portage regression (its step 1) is done and bit-exact. **Current focus
(2026-09-21): the topo DEM merge and surface classification** — phase-1
engine remainder and observation packages; the sparse-point
interpolation is paused with its own state-of-play section below.

Decisions taken, in the order they were made:

1. The composite is a **layer stack × mask stack, folded bottom-up** as
   `out = out·(1−m) + new·m`. This is Hig's own Portage method generalised
   (see "The Portage example"). Everything else is a way to make a layer or
   a mask; nothing edits the output.
2. **Masks are the hand interface.** 8-bit greyscale GeoTIFFs, auto and
   paint kept separate so regeneration never loses paint. **The paint
   raster is the record**; brush strokes are transport and session undo,
   never a versioned log (Hig: a gesture log becomes a second format to
   keep compatible as tools multiply).
3. The painting/selection UI must be **smoothly responsive**, so it runs
   local and offline against the dev server with the stack mounted, syncing
   to the repo at whatever cadence. Prod never hosts it. It is tier 3; the
   first composites use Photoshop/QGIS round trips through
   `import-mask`/`export-mask`.
4. `lowest` is the headline automatic generator (several good DEMs → the
   lower surface sees under canopy and snow), with **real change** handled
   as candidate blobs a human accepts or rejects.
5. The hand UI is a **Photoshop-idiom mask editor**: magic wand on any
   raster (especially the difference), feather by a distance, push the
   selection to 0 or 1. "Put the user in intimate interaction with the mask
   image." The brush is secondary.

Environment facts a fresh session needs (all verified 2026-09-20):

- Repo: `~/Claude_projects/landslidescience`. Pipeline: `tools/lidar/`
  (`build_lidar.py`, `make_catalog.py`, `datasets.json`); state of play in
  `tools/lidar/RESUME.md`. Archive COGs live on
  `/Volumes/Nunatak/lidar_build/cog/`, never on the droplet.
- Python: `/opt/anaconda3/bin/python3` (numpy 2.3, scipy 1.15, rasterio
  1.4; no pyproj/osgeo). GDAL: `/opt/homebrew/bin` 3.13 with
  `PROJ_NETWORK=ON` — **not** the QGIS GDAL 3.3 that is first on PATH.
  PDAL is on PATH (Homebrew).
- The viewer's **Difference** control on `/lidar/` is the review loop for
  tier 1: any dev-only composite can be differenced against any source.
- Bathymetry already in the collection, all NAVD88 via `vertical_shift_m`:
  lituya_2023, lituya_2025, resurrection_2016, resurrection_2024,
  kachemak_bathy_2009, seldovia_2019 (topobathy), grewingk_sonar_2023
  (gated), pedersen_2025. MLLW→NAVD88 routes are in `RESUME.md`.

Prior art read for this plan (all four reduce to numpy/scipy; none runs
outside its host):

- USGS DEMFusion — https://code.usgs.gov/spcmsc/DEMFusion/ (ArcGIS Pro
  notebook; adaptive weight surface).
- Cushing & Tyler 2024, *Mitigating disparate elevation differences between
  adjacent topobathymetric data models using binary code*, Remote Sensing
  16(18):3418 — https://www.mdpi.com/2072-4292/16/18/3418 (CoNED per-pixel
  state rules, weighted slope interpolation).
- GRASS r.mblend — https://grass.osgeo.org/grass-stable/manuals/addons/r.mblend.html
  (delta-surface blend).
- DEM_Smooth_Blend — https://github.com/RobWilsonTas/DEM_Smooth_Blend
  (PyQGIS; proximity → cosine feather).


## Revision 2 (2026-09-20, evening): three axes — time, surface type, data quality

This section supersedes the model below where they conflict. It comes
from one day of building the mockup (sections at the end of this file
record the details) and from one failure that matters: the
first-principles bathy rule found Portage Lake's level on its own and
traced Hig's shoreline, then rejected the 2026 sonar where Portage
Glacier had retreated, because the 2015 lidar there is ice at ~105 m,
not flat water. Elevation and flatness cannot decide that cell. Three
separate things can, and the model has to keep them separate.

### The three axes

Every source layer is an **observation** of a surface. Three independent
questions are asked of it at every cell, and they are stored, generated,
edited and displayed as three separate things:

| axis | question | varies | stored as | unit |
|---|---|---|---|---|
| **time** | when was this surface observed, and how much has it changed since? | date: one per layer (a raster for mosaics like 3DEP/IfSAR). Change: per cell, by surface type and process | `date` scalar on the layer; change *rates* in the recipe, per class | years; m/yr |
| **surface type** | what physical surface did the observation measure? | per cell, complexly | `class.tif`, 8-bit categorical | ground, water, bed, ice, subglacial, canopy, built, void |
| **data quality** | how well does it measure the surface it measured? | per cell, complexly | `sigma.tif`, expected vertical error | metres |

None of the three is a mask. The mask (weight) is the *decision*, kept
in its own rasters as today (auto + paint), and it is the only thing
the fold ever reads. The three axes are the *evidence* the automatic
decision is made from, and each can be corrected by hand on its own.

**Why age relevance varies.** A survey has one date, but the *cost* of
that age differs by cell: a glacier surface moves metres a year, a delta
or beach decimetres, a bedrock highland effectively nothing. So time
enters the decision as **staleness**, a derived per-cell error:

    sigma_t = rate(class at the cell) × (as_of − date)

with rates in the recipe per class (defaults: ground 0.01 m/yr, beach /
delta / channel 0.3, ice 3, water n/a — and, where the glacier apps have a
local dh/dt, that instead). Staleness is never stored, it is computed for
a product date. This is what makes a 2015 lidar at σ = 0.1 m still beat a
2024 IfSAR at σ = 2 m on bedrock, and lose to it on the glacier, with no
hand rule.

**Class changes, too.** An observation's class is what the surface *was*
then. The class *now* at a cell is the class of the newest observation
that has one. That is the Portage retreat: the 2026 sonar (bed, so water
now) makes the 2015 ice observation an observation of a surface that no
longer exists. For `terrain_as_of(2015)` the newest observation on or
before 2015 says ice, and the ice surface is the terrain of that product.

**Quality is where `lowest` lives.** Two ground DTMs of unchanged ground
that differ do so because one failed to reach the ground (canopy, snow),
a one-sided error. "Prefer the lower within the same class and no
expected change" is a quality tie-break, not a rule of its own. Other
quality generators: distance from real soundings for a sonar TIN
(extrapolation), distance from the footprint edge, slope, canopy height
from a DSM, point density from LAS, a datum-confidence scalar per layer
(the xGEOID17B trap). Start with a scalar σ per layer from its metadata
and add rasterised modifiers as sites need them.

### One combination, at the end

For a product with target surface T and date `as_of`:

1. **gate** — compatibility of the observation's class with T at that
   cell, using the class-now rule above: 1, 0, or a fallback rank (ice
   surface stands in for ground where no bed exists, flagged);
2. **expected error** — `sigma = sqrt(sigma_q² + sigma_t²)` per compatible
   observation;
3. **winner** — the smallest expected error, with the recipe's fold order
   as the tie-break and `lowest` as the within-class tie-break; then
   `despeckle` and `feather` as today.

The output of `state_rules` is the winner map turned into one auto mask
per layer, exactly as the plan already wants, so the editor, the paint
round trip and the fold do not change. Winner-take-all with a feather,
not inverse-variance blending: a blend everywhere smears and hides
disagreement; a choice with a seam is what a human can see and overrule.

Every product emits `source_index`, `source_date`, `class`, and
`sigma_total` rasters: what, when, which kind, how sure. The tool gets a
**"why" readout**: hover a cell and see, per layer, z, class, gate,
σ_q, σ_t, total — the whole decision in one line.

### Where hand edits go

Attach a correction to the axis it is about, so it is reused by every
product built from that observation:

- "that is not water, it is ice" → paint the **class** raster;
- "this patch of the sonar is extrapolated junk" → paint **sigma** high
  (a quality veto), not weight 0;
- "I want this seam here" → paint the **weight**, per recipe, as today.

Class and quality corrections are properties of the observation and are
stored beside it; weight paint is a property of the composite. The mask
tool edits all three with the same wand, airbrush and paint files; the
stack panel gains a class and a quality thumbnail per row.

### Data layout

An **observation package** beside each archive COG, product-independent,
built once and cached:

    obs/<dataset_id>/
      date.json            {"date": "2020-10-05", "sigma_m": 0.15, "class_default": "ground"}
      class.tif            8-bit, from LAS classes / product default / detectors / polygons
      class_paint.tif      hand corrections, versioned
      sigma.tif            metres, from sigma_m + modifiers
      sigma_paint.tif      hand vetoes, versioned

Composite recipes add `target`, `as_of`, `rates` and per-layer overrides;
the Portage recipes keep working with class defaults from product type.

### Three serial stages

1. **observe** — `tools/lidar/observe.py`: build the observation package
   for a source (date, class, sigma). Usable alone; its outputs are
   viewable on `/lidar/` as overlays.
2. **composite** — this tool: stack × masks × fold, `state_rules` from
   the three axes. Everything built today stays valid.
3. **view / edit** — `/lidar/` shows products and their diagnostics; the
   mask tool edits weight, class and quality.

Reserved, not built: canopy (no DSMs organised), buildings and bridges
(footprints only when a risk product needs them), local dh/dt rates from
the glacier apps, a date raster for mosaics.

### Revised order of work

1. Finish the phase-1 engine: streaming full-grid builds, `datasets.json`
   sources, snapped grid, import/export-mask, COG write + catalogue entry,
   `report.json`. Unchanged.
2. Observation packages, minimal: date and σ from `datasets.json`, class
   from product default + flat-water detector + glacier-outline polygons;
   `state_rules` with gate → expected error → winner; the "why" readout.
   **Acceptance test: `portage_auto` resolves the retreat with no paint** —
   agreement with Hig's merge rises from 86 % and the south-west
   difference disappears — and a bedrock cell keeps the 2015 lidar over
   a newer coarse survey.
3. Lituya (offset report vs 0.13 m), then Seward (real intertidal gap,
   tide-derived level) as before.
4. Intertidal profile fill + Kachemak hold-out, as before.
5. Tier 2 on `/lidar/`; tier 3 continues locally with class and quality
   painting.
6. Products beyond `ground_current` when a site needs them.

### Scope guardrails (Hig's worry, 2026-09-20: data quantity and interface depth)

What is actually recorded per observation is small. Time is one date
that `datasets.json` already carries. Class and quality are one scalar
each (`class_default`, `sigma_m`) that every dataset gets from its
product type and method with no work. Rasters exist only where a rule or
a hand edit makes them non-uniform, and an 8-bit raster that is uniform
almost everywhere compresses to a few kilobytes. The derived
`class.tif` / `sigma.tif` are caches like `mask_auto`, regenerable; the
versioned things are the scalars, the paint and the polygons.

Interface depth is bounded by one rule: **there is one editing idiom,
paint**, and class and quality are two more paintable rasters on a layer.
The stack panel shows their thumbnails only when they are non-uniform,
the way the paint column appears only when there is paint. A site with
plain lidar over IfSAR never sees any of it.

Rule proliferation is the real risk, not storage. Guardrail: a quality
or class modifier enters the code only when a named site fails without
it, and the hand veto is always the fallback. Initial set, and no more
until a site demands it:

- quality: `sigma_m` per dataset; `density` from LAS where we have the
  points (Grewingk 2021 and Kachemak 2023 are DTM deliveries, so there a
  **facet detector** — local planarity of the DTM, which is what low
  point density looks like in a delivered raster — or the hand veto);
  `extrapolation` for sonar TINs (distance from soundings). IfSAR in
  narrow canyons: hand veto for now.
- class: product default; flat-at-level water; glacier-outline polygons.
- time: date; three rate numbers (ground, beach/delta, ice).

What the three axes buy that weight paint cannot: a veto or a class fix
on an observation is reused by every product made from it (the two bad
patches in Grewingk 2021 are fixed once, not once per recipe), a rule
can find such patches without a person, and a dated series
(`terrain_as_of` animations) stays stable because an old good survey is
not displaced by a new poor one. If a site only needs "prefer A here",
weight paint remains the right tool and nothing else is touched.

### Open questions for Hig (revision 2)

- Change rates per class: the defaults above, or per-site values, or the
  glacier apps' dh/dt where available?
- Glacier outlines for the flight year: RGI 7, the inventory's own
  polygons, or drawn per site?
- Class and quality corrections as polygons (categorical, re-rasterisable
  at any resolution) rather than paint rasters, or both?
- First release `ground_current` only, or `terrain_as_of` too?
- Subglacial bed inside composites (IceBoost as a modelled observation with
  a large σ) or kept as the separate product it is today?


## State of play: sparse-point interpolation (PAUSED 2026-09-21)

Hig's call: pull back, work on the topo DEM merge and surface
classification first, then return to the interpolation tool. Everything
needed to resume is here.

**What exists.** `tools/lidar/grid_bathy.py`, the observe-stage script
that turns soundings into a bathymetric observation package
(`bathy.tif`, `distance.tif`, `sigma.tif`, `domain.tif`,
`constraints.geojson`, `report.json`). Working command for Portage:

    W=/Volumes/Nunatak/lidar_build/composite/portage_subaerial
    P=/Volumes/Nunatak/Landslides/Portage/Portage_topobathy
    /opt/anaconda3/bin/python3 grid_bathy.py --method wtps --smoothing 1 --res 4 \
        --shore-step 15 --ext-step 20 \
        --points $P/Bathy_points/bathy_points_all_updated.gpkg --subaerial $W/subaerial.tif \
        --level 29.29 --edge $P/251018_10m_contours/Portage_bathy_edge.gpkg \
        --out-dir /Volumes/Nunatak/lidar_build/obs/portage_bathy_wtps --holdout 4

(~1 min; the subaerial merge comes from
`composite.py build composites/portage_subaerial/recipe.json --window
9500,13750,19650,23450 --out $W/subaerial.tif --diag-dir $W/diag`.)
The current output is in `obs/portage_bathy_wtps/` and is what
`composites/portage_axes_regrid` and the port-8767 tool instance use.

**What was settled** (details in the dated sections above):
- Interpolator: weighted thin-plate smoothing spline, global solve,
  per-site σ² on the diagonal, coordinates in km. Sounder hold-out 6.7 m
  within 50 m of data, 14 m at 50–100 m; the error is data-limited
  (~0.15–0.2 m per m of distance from a sounding), and no interpolator
  beats another by more than the hold-out noise (n = 24 / 12).
- Constraints: soundings (σ by type), waterline at the level where the
  subaerial merge crosses it (σ 1 m), one-sided slope extension from the
  land slope 5–25 m above the waterline (σ 3 m + 0.1 m/m, kept only where
  it is deeper than the unconstrained fit), topobathy bed cells from the
  merge where a topobathy layer wins (none at Portage).
- Outliers: soundings > 3 MAD and > 8 m from their six nearest
  neighbours get σ × 4 and are listed in `report.json` (9 at Portage; the
  pair at the north end differs by exactly ±20 m and looks like a slip).
- Rejected: linear-kernel RBF (cones at data), local-neighbour RBF (seam
  streaks), hard extension (bed ridge), trend + Gaussian process
  (shore-parallel terraces), home-made spline in tension (slow, worse).

**What is open** (resume here):
1. Domain from the lidar water class beyond the polygon (`--water-class`)
   scored worse on the near-data bin and took 7 min; not conclusive.
2. Hig's guide contours (`251018_Hig_interpolation_contours.gpkg`) as a
   constraint class — the human knowledge his TIN had and this does not.
3. A proper spline in tension (GMT `surface`, `brew install gmt`) if the
   bowing between sparse points matters at a site.
4. σ from the spline itself (the GP posterior, or a leave-one-out
   estimate) instead of the distance calibration.
5. The retreat basin: 1972 estimates dominate there and the waterline
   against the 2015 ice edge is the polygon edge; a dated glacier outline
   would place it.
6. Sea sites: level from tide/MSL, not the histogram mode.

## The Portage example

`/Volumes/Nunatak/Landslides/Portage/Portage_topobathy/Crops/Topobathy_merge/`
holds Hig's rigid first version of this tool, and is the regression target
for step 1:

- `merge_settings.py`: a 30000 × 20000 grid (EPSG:32606, 1 m), a list of
  five `{'DEM': path, 'mask': path}` entries in fold order — IfSAR base with
  no mask, then Anchorage 2015, Whittier topobathy, Portage 2020, Portage
  bathy 2026-01-04 — each mask an 8-bit GeoTIFF painted in Photoshop
  (`Merge.psb` beside the crops), 0 = black = 0.0 and 255 = white = 1.0.
- `topobathy_merge.py`: initialises the output to NaN, copies the base,
  then for each entry streams 1024-row blocks and does
  `out = out·(1−m) + new·m` in place. No nodata guard, so the masks had to
  be painted to avoid NaN leaks. Output `Portage_topobathy_merge.tif`
  (float32, DEFLATE, BigTIFF).
- Some masks were re-georeferenced by hand with `.tfw` sidecars after
  Photoshop stripped the tags — the thing `import-mask` does in code.

The plan below generalises this: the same fold, with masks that are
generated and then edited rather than painted from scratch, a nodata guard,
derived layers for blends and fills, and a build that is reproducible from a
recipe.

## The model: a layer stack and a mask stack

A composite is an ordered list of **layers** on one common grid, each with a
**mask** in [0, 1], folded from the bottom up:

```
out = layer[0]
for i in 1..n:  out = out·(1 − m_i) + layer[i]·m_i
```

Everything else in the tool is a way of producing a layer or a mask:

| Thing Hig wants | How it appears in the model |
|---|---|
| Rote combination | Layer = a survey's archive COG warped to the grid. Mask = its valid footprint with the seam feathered (auto-generated). |
| Fade between two surveys | The seam feather *is* the mask ramp. Width, shape and adaptivity are parameters of the mask generator. |
| "Prefer X here, Y there" | Painted into X's mask (hard or soft brush) or a polygon rasterised into it. |
| Void an artifact | Paint 0 into that layer's mask. Lower layers show through. |
| Hand-corrected DEM patch | A layer (the patch raster) with a mask (its footprint, feathered). |
| Delta-surface blend (r.mblend) | A **derived layer**: the lower survey plus the interpolated seam offset, masked over the transition zone. |
| Intertidal profile fill | A derived layer (the fill surface) with a mask covering the void, feathered into both neighbours. |
| CoNED state rules | A mask generator: per-pixel category state → which layer's mask gets 1 here. |

So the two kinds of hand intervention Hig named (masks and correction DEMs)
and the algorithmic fixes all reduce to *(layer, mask)* pairs, and the
composite is only ever the fold. Nothing touches the output directly. That
keeps the composite reproducible when a source is rebuilt, and it means a
painted mask survives regenerating the automatic one (see Masks below).

Nodata is a mask concern, not a fold concern: the fold runs on float32 with
NaN, and the *effective* mask is `m_i · valid_i`, so a brush stroke of 1
over a layer's hole cannot punch NaN into the composite. The Portage script
has no such guard, which is why its masks had to be painted carefully.

## Masks

Per layer, three rasters on the common grid, all 8-bit greyscale GeoTIFFs
(0 = black = 0.0, 255 = white = 1.0) so they open in QGIS, Photoshop and
GIMP unchanged:

- `mask_auto.tif` — regenerated from the recipe (footprint, feather, state
  rules, fills). Never hand-edited.
- `mask_paint.tif` — two bands, value and alpha. Hand edits only. Empty
  alpha means "no opinion", so regenerating `mask_auto` does not lose paint.
- `mask.tif` — the effective mask, `auto·(1−α) + value·α`, times the valid
  footprint. This is what the fold reads and what the viewer shows.

**The paint raster is the record.** Brush strokes are not stored; they are
the message a painting tool sends to whatever splats them into
`mask_paint`, and a per-session undo stack, nothing more. (An earlier draft
proposed a versioned stroke log. Hig's objection stands: as the set of
tools grows, a log of every tool's gestures is a second format to keep
compatible forever, for a benefit — replay at another resolution — that
resampling the paint raster gives well enough.) What is versioned is the
`mask_paint` raster, which is small when compressed because most of it is
untouched alpha. Polygon edits ("prefer here", "exclude") are rasterised
into `mask_paint` too; the polygon itself may be kept as a convenience for
re-drawing, but the raster is authoritative.

Photoshop, GIMP and QGIS stay first-class painting tools:
`composite.py import-mask <layer> edited.tif` diffs an externally edited
mask against `mask_auto` and folds the difference into `mask_paint` (alpha
1 where they differ). Photoshop strips geotags, so the importer takes the
georeference from the recipe grid and ignores the file's own, with a
size-and-shape check. That is the `.tfw` sidecar dance from Portage done
once, in code. `composite.py export-mask <layer>` writes the effective mask
back out for the next round.

Mask operations are recipe steps, not hand work: blur (the Portage feather),
erode/dilate, threshold, invert, clip-to-polygon. Applied to `mask_auto` in
order, so a recipe can say "footprint, erode 5 m, blur 20 m" and get the
Portage-style soft edge without a brush.

### Mask generators

Per layer (`footprint`, `feather`, `waterline`) or across a set of layers
(`lowest`, `state_rules`). The set-level ones write into several layers'
`mask_auto` at once.

**`lowest`** is the common case Hig named: several good DEMs of the same
ground, and the lower surface is the better bare earth wherever they differ,
because it is the one whose ground filter got under the vegetation or that
was flown snow-free. Kachemak 2023 against Grewingk 2021 around Halibut
Cove is the picture: two sound lidars whose difference is mostly canopy
penetration and snow, not ground change. The generator, over a candidate
set in fold order:

- where only one candidate is valid, its mask is 1 (footprint rule);
- where several are valid and the lowest is lower than the highest-priority
  candidate by more than `tol_m` (the pair's noise, ~0.1–0.2 m for two
  lidars), the lowest gets 1 and the others 0 at that pixel; within
  tolerance the priority layer keeps it, so the mask is not per-pixel
  speckle in the noise band;
- `smooth_m` majority-filters the winner map before rasterising, then the
  usual `feather` op softens the edges so the fold does not step between
  filters at every canopy edge;
- a `max_drop_m` cap: a candidate lower by more than that is not adopted
  but written to `report.json` and `attention.tif` — it is a pit, a missing
  bridge deck, a water surface that one survey had and the other did not,
  or real change (a landslide scar, a cut bank), and which of those it is
  is a human call. Run `despike_dtm.py` on inputs first; the minimum is a
  magnet for negative spikes.

**Real change** is where `lowest` is wrong by construction, and it is
common: glacier thinning is the extreme, but landslide deposits, cut and
fill, delta growth, a rebuilt road, a beach after a storm all move the
ground between surveys. Where the newer surface is *lower* (thinning, a
scar) the minimum happens to pick the newer one and a "current ground"
composite is right by accident. Where the newer surface is *higher*
(deposition, advance, construction) the minimum keeps the old ground and
is simply wrong, and nothing in the elevations alone says whether a rise
is canopy, snow, or a new landslide toe. That is a human call, and the tool
has to make the call fast:

- the generator writes `change_candidates` — connected blobs of
  `|newer − older| > change_m` with area above `min_area_m2`, as polygons
  with area, mean dz, sign, and which layer `lowest` chose — alongside the
  masks, and `attention.tif` marks the same cells;
- in the editor (below) each blob is one click: **accept** pushes the newer
  layer's mask to 1 over the blob, feathered by the current feather
  distance, so the newer surface wins there whatever `lowest` said;
  **reject** leaves the auto mask alone; both are ordinary paint;
- `prefer_year` is the bulk form of accept: over a drawn or wanded region,
  the newest valid layer wins, for a glacier where every blob is real.

For anything historical (a composite meant to show the ground as of a
date) `lowest` is the wrong generator and `prefer_year` with a cutoff is
the right one. Where the rule is wrong locally, the fix is a selection and
a fill on the loser's mask, which is why this is a mask generator and not
a derived "min of N" layer — the two are equivalent in the fold, but one
mask per layer is what an editor can reach.

`highest` and `median` come free with the same code. `median` over three or
more renderings of one point cloud is a cheap despiker in its own right.

## Layers

`layers[i].source` is one of:

- a `datasets.json` id — the archive COG, already NAVD88 with its
  `vertical_shift_m` stamped, warped to the grid with `-novshift` and the
  `retag_generic_nad83()` / `apply_vertical_shift()` helpers from
  `build_lidar.py`; bilinear for BAG-derived layers per `RESUME.md`;
- a local path — for crops, patches, and things not yet in the manifest
  (this is how the Portage inputs come in);
- a **derived layer** spec — `{"derive": "delta", "over": "B", "from": "A"}`,
  `{"derive": "intertidal_profile", ...}`, `{"derive": "smallhole_fill",
  "of": "A"}`, `{"derive": "plane_shift", "of": "A", "dz": -0.4}`. Derived
  layers are computed from other layers in the stack and cached as rasters
  like any other; their auto masks come from the same generator.

Per-layer options: `waterline` (void topographic lidar below an elevation
or inside a drawn polygon — sea-surface returns and hydro-flattened fill at
flight-time tide would otherwise beat the bathy), `category` (topo,
topobathy, bathy_hr, bathy_mr, patch — for the state rules), `resample`.

## What the references contribute

All four reduce to mask generators or derived layers, and all are a few
hundred lines of numpy/scipy. None runs outside its host (ArcGIS Pro, GRASS,
QGIS console), so none is run as-is.

| Source | Becomes | Note |
|---|---|---|
| DEM_Smooth_Blend | Mask generator `feather`: distance-from-edge (`scipy.ndimage.distance_transform_edt`) → cosine ramp over `width_m`. | The default seam. Two dozen lines. |
| r.mblend | Derived layer `delta`: difference sampled in a ring along the seam, tapered to zero (or to the mean, r.mblend's `-a`) across the transition zone, added to the lower layer. | Right for offset-dominated seams; the upper survey is untouched and the lower's morphology survives. |
| DEMFusion | Adaptive `width_m` per seam pixel from `clamp(|Δz| / tan(slope), w_min, w_max)`, smoothed along the seam; polygon ceilings; diagnostic rasters. | Skip the Euclidean→contour→natural-neighbour weight surface, 40× storage and day-long runs. |
| CoNED (Cushing & Tyler 2024) | Mask generator `state_rules`: per pixel, per category, has-data and below-MSL; rules set masks. Receded shoreline (old topo above MSL, new bathy below → fill, not ghost beach) and topobathy-above-MSL-where-everything-else-is-below (→ topo wins) fall out directly. Their weighted slope interpolation is a `delta` variant boosted by the coarse layer's slope. | Keep a uint8 per category, not a 16-bit pack. Their 15 m / 50 m zones are Florida numbers. |

## Architecture

Package `tools/lidar/topobathy/`, CLI `tools/lidar/composite.py`, working
directory `$LIDAR_BUILD/composite/<id>/`. Runtime as the rest of the
pipeline: `/opt/anaconda3/bin/python3` (numpy 2.3, scipy 1.15, rasterio
1.4), `/opt/homebrew/bin` GDAL 3.13 for warps, `PROJ_NETWORK=ON`. Runs on
the Mac where the archive COGs live.

```
composite/<id>/
  recipe.json            committed to the repo
  masks/<name>_paint.tif hand; committed (the record of every hand edit)
  edits.geojson          committed: polygons and notes from the web tool
  patches/*.tif          committed if small, else listed with a hash
  grid.json              derived: lattice, dims, transform
  layers/<name>.tif      derived: float32 on the grid (+ valid band)
  masks/<name>_auto.tif  derived
  masks/<name>.tif       derived: effective
  attention.tif          derived: where a generator declined to decide
  out/<id>.tif           archive COG
  out/source_index.tif   argmax of effective masks (which layer won)
  out/delta.tif          composite minus naive priority mosaic
  out/state.tif          CoNED state, if used
  out/report.json        offsets per overlap, seam lengths, fill areas, hold-out RMSE
```

Recipe sketch:

```json
{
  "id": "resurrection_topobathy_2024",
  "grid": {"epsg": 6335, "res_m": 1.0, "extent": "union"},
  "msl_navd88_m": 0.93,
  "layers": [
    {"name": "ctx",   "source": "ctx_3dep",          "category": "topo_mr"},
    {"name": "topo",  "source": "seward_2019_topo",  "category": "topo",
     "waterline": {"below_m": 1.6}},
    {"name": "b2016", "source": "resurrection_2016", "category": "bathy_hr",
     "resample": "bilinear"},
    {"name": "b2024", "source": "resurrection_2024", "category": "bathy_hr",
     "resample": "bilinear"},
    {"name": "b2024_ext", "source": {"derive": "delta", "from": "b2024", "over": "b2016"},
     "mask": {"auto": "transition"}},
    {"name": "beach", "source": {"derive": "intertidal_profile",
     "between": ["topo", "b2024"], "library": "auto", "max_gap_m": 300}}
  ],
  "mask_defaults": {"auto": ["footprint", {"feather": {"width_m": [10, 80], "adaptive": true}}]},
  "mask_rules": [{"lowest": ["topo", "topo_2021"], "tol_m": 0.15, "smooth_m": 3, "max_drop_m": 3}],
  "state_rules": "coned_default",
  "auto_offset": {"measure": true, "apply": false}
}
```

Layer order is fold order: first entry is the base and has no mask, exactly
as in `merge_settings.py`. The Portage recipe is the same file with five
local paths and `"mask": {"file": "../Portage_2020_mask.tif"}` on each.

Stages, each cached on disk and windowable with `--window bbox`:

1. **grid** — target lattice snapped the way `kenai_ptd_run.py` pins tile
   bounds (unsnapped bounds gave 51 cm seams once already). For Portage the
   grid is simply the base crop's.
2. **stack** — each layer warped to the grid, float32 + valid band.
3. **derive** — derived layers, in dependency order.
4. **masks** — auto masks from the generators; effective masks from auto,
   paint and footprint; measured overlap offsets into the report (applied only if
   the recipe says so — datum errors belong in the source's
   `vertical_shift_m`, not hidden inside a composite).
5. **fold** — the multiply-add, block by block, as in `topobathy_merge.py`
   but NaN-aware and with the effective mask. This is the cheap stage.
6. **write** — archive COG in house format (float32, ZSTD, −9999, overviews
   rebuilt, `-mo vertical_shift_m=0`, `-mo composite_recipe=<sha>`,
   `-mo composite_paint=<sha of the paint rasters>`), diagnostics, then a `datasets.json` entry
   with `archive_mode: copy`, `product: "Topobathy composite"`, `dev_only`
   until Hig lifts it. `source_index` and `delta` are baked to PMTiles like
   the slope layer, so the viewer can tint "who won" and "what changed".

Because stages 1–4 are cached and stage 5 is trivial, a mask edit costs one
fold over its window, not a rebuild. That is what makes live painting
possible later.

### The intertidal fill

The gap between the lidar waterline and the sonar's shallowest return is
the piece none of the references solve. As a derived layer:

- Shore-normal frame from the void's own distance transforms: distance from
  the topo edge and from the bathy edge give each void pixel a position
  `t ∈ [0, 1]` across the gap and a gap width; edge elevations `z_topo`,
  `z_bathy` are known along the perimeter.
- **Profile library.** Where the gap is closed by real data (topobathy
  coverage, or narrow voids the delta layer handles) cast transects normal
  to the shoreline every ~25 m, normalise to `t` and to
  `(z − z_bathy)/(z_topo − z_bathy)`, keep the ensemble. In the void, apply
  the median normalised profile of the nearest N transects, scaled to the
  local edge elevations. Fallback where no library is near: parametric
  (Dean `h = A·x^(2/3)` for beaches, linear for rock), selected per polygon.
- Its auto mask is the void, feathered into both neighbours; the fold does
  the rest.
- Reject where the gap exceeds `max_gap_m` or the edges invert (bathy edge
  above topo edge means the waterline is wrong, not the beach); report
  those as voids for hand attention. A painted mask can also simply turn
  the fill off where it looks wrong.

**Validation by a different route than the fit:** Kachemak Bay has
`seldovia_2019` topobathy spanning the intertidal. Void its intertidal band,
fill from `kachemak_bathy_2009` + topo, score against the withheld truth.
Do this before the fill is trusted at Resurrection Bay, where there is no
truth.

## Interface

Three tiers, in order. Painting tools are tier 3 and are not needed for the
first composites; the file-based route (QGIS/Photoshop → `import-mask`)
covers hand edits until then.

### Tier 1 — recipe + CLI, review in the existing viewer

```
composite.py build       recipe.json [--stage grid|stack|derive|masks|fold|all] [--window bbox]
composite.py masks       recipe.json [--layer NAME]      # regenerate auto masks only
composite.py import-mask recipe.json --layer NAME edited.tif
composite.py export-mask recipe.json --layer NAME [--effective|--auto|--paint]
composite.py export-ref  recipe.json --diff A B | --layer NAME   # 8-bit images on the grid, for wanding in Photoshop
composite.py report      recipe.json
composite.py holdout     recipe.json --truth seldovia_2019 --band intertidal
composite.py publish     recipe.json                     # datasets.json entry + build_lidar --stage all
```

Review loop: the composite is a dev-only survey, and the viewer's
**Difference** control shows it minus any source; `source_index` and
`delta` PMTiles show who won and what the blend changed. First deliverable
is a regression: rebuild `Portage_topobathy_merge.tif` from its five crops
and masks and match it.

### Tier 2 — polygons and parameters on `/lidar/`, editor-gated

The scarp-trace pattern (`inventory/scarps.py`, `scarps.js`, and the pencil
in `map.js`): Terra Draw (`pointerDistance: 8`, `doubleClickZoom` off), CSRF
header, JSON-vs-HTML response check, writes editor-only, a PostGIS table
`composite_edits(id, recipe_id, layer, kind, params jsonb, geom, edited_by
text, timestamps)` from an idempotent management command. Unlike scarps,
reads are editor-only too: a half-made mask polygon is not something the
public map should show. `kind` is `prefer` / `exclude` (polygons rasterised into a layer's
`mask_paint` at build), `waterline`, `seam_width`, `slope`,
`profile_shape`, `attention` (a note, no effect). A "Composite" panel
lists recipes from `/lidar/composites.json`, shows layers in fold order,
and shows the current effective masks as a tinted overlay (PMTiles baked
at build time). `composite.py build` exports the table to `edits.geojson`
and stamps the hash into the COG, so a build is reproducible from the repo
alone while drawing happens wherever the gated layers are visible,
including prod.

### Tier 3 — the mask editor, local and offline-first

The user works on the mask image directly, in the Photoshop idiom Hig
described: **select** a region, **modify** the selection, **fill** it.
The mask is the document; the DEMs, hillshades and difference rasters are
reference layers you can see through and select on. A brush is just one
selection tool among several, and every tool ends in the same place: a
region of `mask_paint` set to 0, 1, or a value in between, with a feather.
That is what makes the editor generic across generators — `lowest`,
`feather`, the state rules and the intertidal fill all just produce a
starting mask and, where they are unsure, a set of candidate selections.

Selection tools:

- **Magic wand** on any visible raster: click a seed, take the contiguous
  region within `±range` of the seed's value. On the *difference* raster
  this selects a thinning glacier or a landslide deposit in one click; on
  the *mask* it selects a blotch the generator made; on a *DEM* it
  selects a flat (a lake surface, a hydro-flattened fill). Tolerance,
  contiguous or global, and 4/8-connectivity as in Photoshop.
- **Candidate blobs** from the generators (`change_candidates`,
  `attention`): listed and outlined, click to select, keyboard to accept
  or reject, next. This is the efficient path through a glacier with fifty
  real changes and three canopy artifacts.
- **Polygon / lasso**, and the **brush** (radius in metres, hardness) as a
  selection brush or a direct paint.
- **By value range** on a raster (a threshold, no seed) for bulk work.

Selection modifiers: grow / shrink by a distance, **feather** by a
distance (the one Hig named — the selection edge becomes a ramp so the fold
does not step), invert, add / subtract / intersect with the current
selection, and "select same on the other layer" (a `lowest` decision is
a pair of complementary masks, and pushing one up should pull the other
down).

Fills: set to 0 or 1, set to a value, push toward 0 or 1 by an amount,
restore auto (clear paint alpha in the selection), blur within. Undo is
per operation, a session stack; the paint raster on disk is the record.

Implementation:

- Runs where the stack is: on the Mac, against the dev server with the
  composite working directory mounted, no network in the loop. Sync to the
  repository (paint rasters, `edits.geojson`, the built composite) at
  whatever cadence Hig chooses, by the usual commit and publish steps;
  prod never hosts the editor.
- **Client** draws selections and brush strokes on a canvas over the map
  at once, for feel; nothing is authoritative until the server answers. A
  wand click is posted with the seed and tolerance, and the server floods
  at grid resolution (`scipy.ndimage.label` on the thresholded window,
  bounded by a maximum region) and returns the selection as a small
  raster tile set or a polygon, whichever is smaller.
- **Server, local only**: holds the session selection as a raster, applies
  modifiers and fills to `mask_paint`, re-folds the cached stack over the
  dirty window, invalidates tiles, and serves mask, selection and composite
  as tiles from `/lidar/composite/<id>/{mask,sel,dem}/{z}/{x}/{y}`. A fill
  over a glacier-sized blob at 1 m is a few million cells: a second, not a
  frame, but it is one click, not a stroke. Latency for strokes is the
  fold over the dirty window, which is why stages 1–4 are cached.
- The same code path serves `import-mask` (an external edit is a fill with
  a raster as the selection), so a Photoshop edit and an editor edit land
  in the same place and the build sees no difference.

The plain image-editor route stays valid for anything the editor cannot do
yet: `export-mask` the effective mask and the difference raster as
matching 8-bit images, wand and feather in Photoshop, `import-mask` the
result. Getting this round trip right in tier 1 means the editor is a
convenience, not a prerequisite.

If a browser canvas over MapLibre cannot be made responsive enough under
the demshade worker, the fallback is a small native window (Qt + numpy)
over the same local endpoints; the model does not change. Two checks
before committing either way: whether the demshade package accepts a tile
URL template rather than a PMTiles archive, and how a MapLibre `canvas`
source behaves under the demshade compositor.

## Order of work

1. **Model + fold + masks as files.** grid, stack, `footprint`,
   `feather` and `lowest` generators, mask ops, effective-mask rule, fold,
   write, `import-mask` / `export-mask`. Regression: reproduce the Portage merge. Then Lituya
   (`lituya_2025` over `lituya_2023` over topo): two surveys of the same
   seabed with a known 0.13 m residual, so the offset report can be checked
   against an established number.
2. **Derived layers + state rules.** `delta` with adaptive width, CoNED
   state rules, `waterline`. Seward: `resurrection_2024` + `_2016` + topo,
   the real intertidal gap and ghost-shoreline case.
3. **Intertidal profile fill** + the Kachemak hold-out. Stop and read the
   RMSE before going further.
4. **Tier 2** polygons and panel; migration dev-only first as with fault
   scarps.
5. **Tier 3** the mask editor, local only: wand + candidate blobs +
   feather + fill first (that is the real-change workflow), brush second.
6. Publish the first composite on Hig's call, then the auxiliary PMTiles.

## Open questions for Hig

- Which site first after Portage: Seward (the real problem) or Lituya (the
  easy check)? The plan says Lituya for the engine, Seward for the rules.
- MSL in NAVD88 per site — from the CO-OPS bench mark sheets, or already on
  hand for Seward, Seldovia and Lituya? (CO-OPS prints feet there.)
- Are the topographic lidars over these bays hydro-flattened, or do they
  carry water-surface returns? That decides whether `waterline` is an
  elevation threshold or has to be a drawn polygon.
- Should measured overlap offsets ever be applied inside a composite, or
  always pushed back into the source's `vertical_shift_m`? Report-only by
  default in the plan.
- Paint rasters in the repo, or in `data/` shipped by rsync like the
  pyramids? Compressed they are small (mostly untouched alpha), but the
  Portage masks are full-frame paintings with no auto/paint split, and
  would be committed whole unless `import-mask` is run against a
  regenerated auto mask first.
- For `lowest`: should big *drops* (newer lower by more than `max_drop_m`)
  be adopted by default, since a current-ground composite wants them, or
  go to the candidate list like rises do? The plan currently lists both
  signs and lets the human accept.
- Do composites get their own region in the catalogue or sit beside their
  sources with a `composite` product tag?

## Disk and compute

At 1 m a bay-scale composite is 10⁷–10⁸ cells: 40–400 MB per float32
layer, three masks per layer at 8-bit, three to six layers, plus derived
layers and diagnostics. Distance transforms run on seam-zone bounding boxes
only. Expect single-digit GB per composite on Nunatak, minutes for a full
build, seconds for a windowed fold. Portage (30000 × 20000, five layers) is
the upper end of what is in hand today and the Portage script already
streams it in 1024-row blocks; the fold keeps that block discipline.

## Development phases and mockup status (2026-09-20, second session)

A phase-1 mockup exists and passes the Portage regression. Code:
`tools/lidar/topobathy/` (grid, recipe, stack, masks, fold) and the CLI
`tools/lidar/composite.py` (`build`, `regress`, `masks`, all windowed);
recipe at `tools/lidar/composites/portage_legacy/recipe.json`. Recipes
live in the repo under `tools/lidar/composites/<id>/`; the working
directory with derived rasters stays on `$LIDAR_BUILD/composite/<id>/`.

Facts learned from the Portage inputs that the plan above did not know:

- The five crops are **not on one grid**: origins differ by up to 0.9 m
  (IfSAR 379999.63/6750001.05, Portage bathy 380000.00/6750000.00) and
  the painted masks carry **no georeferencing at all**. The legacy script
  stacks by row/column index. So the regression contract is pixel-index
  alignment (`"align": "pixel"` per layer), and a properly gridded rebuild
  will differ from the legacy output by sub-pixel resampling. That is
  expected, not a bug.
- Regression on a 2048 x 2048 window with all five layers active (rows
  12096-14144, cols 21440-23488): **bit-exact** on 4 194 179 of 4 194 304
  cells. The other 125 are cells where a mask of 1-2/255 sat over
  Anchorage 2015 nodata; the legacy output there is wrong by up to 80 m
  (the -9999 leak the plan predicted). The guarded fold gets them right.
- A bare `footprint` mask is the wrong auto mask for a TIN-interpolated
  bathy: the Portage sonar raster extends over land, and footprint +
  feather differs from Hig's painted mask over 30 % of the window, by up
  to 416 m. Bathy layers need a `waterline` / polygon / `state_rules`
  mask, or the paint. Footprint + feather is right for lidar tiles.

Phases (each ends in something Hig can look at in the viewer):

1. **Engine, done in mockup form.** Fold, file masks, `footprint`,
   `feather`, mask ops, paint + valid guard, geo and pixel readers.
   Remaining for a real phase 1: blockwise streaming for full-grid builds
   (the mockup holds one window in memory), `datasets.json` sources via
   `build_lidar.py` helpers, a snapped grid spec, `import-mask` /
   `export-mask`, COG write + `datasets.json` entry, `report.json`. Then
   Lituya 2025 over 2023 to check the offset report against 0.13 m.
2. **Generators: `lowest`, `state_rules`, `waterline`, `change_candidates`.**
   Seward (`resurrection_2024` + `_2016` + topo). First place a generated
   mask has to beat a painted one.
3. **Derived layers: `delta` (adaptive width), `intertidal_profile`.**
   Kachemak hold-out before the fill is used anywhere without truth.
4. **Tier 2 viewer panel**: composites list, source-index and delta
   PMTiles, polygons through the scarp-trace pattern, editor-gated.
5. **Tier 3 mask editor**, local only: wand + feather + fill on the
   difference raster, candidate blobs, brush last.
6. Publish on Hig's call.

### Mask domains and the local mask tool (2026-09-20, later)

**Domains.** A mask no longer has to cover the grid. Per layer, `domain`
is `"overlap"` (default: the rectangle that exactly encloses where the
layer overlaps any layer below it, plus `domain_pad_m`, default 50 m),
`"full"`, or an explicit box. Outside the domain the mask is the footprint
(the layer is either the only data or absent, so there is nothing to
decide). Mask files and paint rasters are written on the domain box and
say nothing outside the cells they cover (`covered`, not 0). The Portage
recipe says `"domain": "full"` because its masks are full-frame paintings;
the regression is bit-exact under both settings. Synthetic test in the
session log: a mask file covering only the overlap rectangle hides the
layer there and leaves it as footprint where it is the only data.

**Local mask tool** (tier 3 brought forward in a low-flexibility form):
`tools/lidar/mask_tool/server.py recipe.json --window r0,r1,c0,c1`, Flask
on 127.0.0.1:8765, `index.html` is one canvas page. Server holds one
window in memory (layers, auto masks, paint, selection, undo); client
shows PNGs (composite or layer hillshade, layer-minus-below difference,
effective mask, paint, footprint, selection) on stacked canvases with
wheel zoom and drag pan. Tools: wand on any reference raster with a
tolerance (scipy label flood), rectangle, grow/shrink/fill-holes/invert/
∩footprint, fill selection to 1 or 0 with an inward cosine feather, restore
auto, undo, save. Save writes `masks/<layer>_paint.tif` (value, alpha) on
the domain box into the recipe work dir (`work_dir` key, else
`$LIDAR_BUILD/composite/<id>`), which `layer_mask` reads by convention.
Measured on the 2048² Portage window: wand a few ms, fill + refold 0.28 s,
composite hillshade PNG 0.26 s. Known limits: one window per session
(save overwrites, no merge with an existing paint file); no MapLibre, no
tiles; brush not implemented. `/lidar/` exposure of the result is phase 1's
`write` stage (COG + datasets.json dev_only + source_index/delta PMTiles),
not this tool.

**Stack panel** (added the same day): the tool's left panel is a thumbnail
stack, composite on top then layers in fold order down to the base. Two
thumbnails per row: elevation on one grey ramp shared across the stack
(1st–99th percentile of all layers, so the rows compare), nodata in one
purple, the orange "shown" tint of the effective mask over it; and the
effective mask itself as a plain grey ramp. Thumbnails are strided
downsamples served from `/thumb/{composite,dem,mask}` in a few ms and
refresh on every refold. Hover gives data %, shown %, z range, paint %.
Clicking a row's elevation thumbnail opens that layer in the hillshade
view with the orange overlay; clicking its mask thumbnail opens the mask
itself in the main view as a grey ramp (nodata purple), with the orange
overlay suppressed. Background options "this layer's mask" and "this
layer's auto mask" do the same from the menu. All selection and fill
tools work identically in either view.

**Paint column and airbrush** (same day). The stack has a third column,
paint, shown only for layers that carry hand paint: the paint value as a
grey ramp where alpha > 0 over a checker where there is no opinion.
Clicking it opens the paint in the main view (also a background option).
The airbrush is a raster-editor airbrush over the paint raster: size (m),
hardness (fraction of the radius at full strength before a cosine
falloff), opacity (cap on what one stroke can lay down), flow (how fast
each dab builds), and three "colours": show (value 1), hide (value 0),
erase (removes alpha). The client previews dabs on a canvas at once and
streams points to `/api/stroke_pts`; the server dabs at 12 % of the
diameter, accumulates a per-stroke buffer capped at opacity, and
recomposites the paint from the pre-stroke copy ("over" compositing of
(value, stroke alpha) on (value, alpha)) so crossing a spot twice cannot
exceed the cap. `stroke_end` pushes undo and refolds (0.27 s on the 2048²
window, ~0.9 s to the refreshed view). `[` and `]` change size.
Clicking the composite row (name or thumbnail) shows the final result
clean: composite hillshade, all overlays off; the current layer stays
selected so the tools keep working.

**Component toggles** (same day). Under each stack thumbnail a small
switch, on by default, session-only (never saved, never in the recipe):
DEM = the whole layer in or out of the fold (the base too: off empties
it); auto = the generated or file mask, off means a plain footprint so the
view shows what the auto mask removes; paint = the hand paint, off means
auto only. Each toggle refolds (~0.25 s) and refreshes the composite,
overlays and thumbnails, so in the composite view you can pull components
out one at a time to see what each contributes. Rows with DEM off are
dimmed.

### First-principles composite: `portage_auto` (2026-09-20, evening)

`tools/lidar/composites/portage_auto/recipe.json`: the five Portage inputs
with **no hand masks**. Generators now in `topobathy/masks.py`, each a
few lines of numpy/scipy taken from the prior art:

- `footprint` → `erode` → `despeckle` → `feather` (DEM_Smooth_Blend: EDT
  distance inward, cosine ramp) for the lidar and topobathy layers.
- `water` for the bathy (CoNED state rule, generalised): the layer counts
  where the surface below it is water — flat at the water level within
  `tol_m` — and the layer is beneath that level. The level is `"auto"`:
  the mode of the fold-below elevation under the layer's footprint (a
  lake has no tide gauge; the lidar over water is flat, so the histogram
  peak is the level). Portage Lake came out at **29.21 m** with no input.
  A TIN extrapolated over land is above the level and drops out.
- `lowest` (ours): layer wins where lower than the fold below by > tol.
- `despeckle` (drop blobs and fill holes below `min_area_m2`), `smooth`
  (majority filter).

Generators that compare with the fold below (`water`, `lowest`) get `z`
and the running fold from `build_window`, which now folds as it goes.
Windowed builds compute masks on a halo (`halo_px`, default 128) and
crop, so the window edge is never mistaken for a layer edge.

Result on the 2048² window against Hig's painted merge: 86 % of cells
identical, 6.7 % differ by more than 1 m, and 90 % of those are places
the rule kept lidar where Hig painted bathy in — the SW shallows where
the lidar surface is not flat at lake level. The generated bathy mask
otherwise traces his painted shoreline. That is the intended workflow:
generate, then fix by hand in the mask tool (`server.py` runs on this
recipe like any other; paint saves to its work dir).

Stack panel no longer rebuilds its DOM on refresh: thumbnails are
preloaded and swapped, classes patched in place.

Next generators from the plan, not built: adaptive feather width
(DEMFusion), `delta` seam-offset layer (r.mblend), `state_rules` with a
category table, `change_candidates` for `lowest`, seam offset report.

**Side-by-side examples**: `mask_tool/run_examples.sh` starts
`portage_legacy` on 8765 and `portage_auto` on 8766 on the same window
(`WIN=` to change). A recipe's `reference` (or `legacy_output`) raster
is loaded into the session and exposed as the "composite − reference"
background and wand raster, so a first-principles build can be compared
with, and hand-corrected toward, an existing merge. `mask_tool/README.md`
documents the tools and the paint round trip (save → restart / build).

### Revision 2 built: `topobathy/axes.py` and `portage_axes` (2026-09-20, night)

- `axes.py`: `CLASSES` (void, ground, water, bed, ice, subglacial, canopy,
  built, modelled), `TARGETS` (compatibility table by class-now for
  `ground_current` and `top_surface`), `DEFAULT_RATES`, `years()`,
  `class_raster()` (steps: `default`, `water_level` on the layer's own z
  with a modal-bin guard, `below_level` against the fold below for
  sonar), `sigma_raster()` (`sigma_m` + `facets` + `edge`),
  `state_rules()` (credible = valid, classified, σ < `sigma_max`, dated
  ≤ `as_of`; class-now = newest credible; gate from the table; σ_total =
  √(σ_q² + (rate·Δt)²); winner = min σ_total at the highest gate level,
  later layer on ties, `lowest` tie-break among ground observations).
- `composite.py`: a recipe with `target` runs the two-pass build (read
  all, axes per layer against a provisional footprint fold, rules,
  `rules_post` despeckle + feather, paint, fold). Recipes without
  `target` are unchanged.
- **Facet size as a sonar quality proxy is real but needs a coarse
  threshold.** Inside the painted lake 96 % of cells sit in TIN facets
  < 50 000 m² (survey lines are far apart, and that is legitimate
  interpolation); outside, 94 % sit in one 690 000 m² hull facet. A cut
  at 100 000 m² separates them; `k·√area` with k = 0.05 beyond that puts
  extrapolated cells above `sigma_max`. With the small default cut the
  whole sonar went non-credible and the lidar water surface won the lake.
- **Water detection must use the layer's own elevations** (a topo lidar
  is flat at its own lake level), not the fold below; the fold below is
  right only for the sonar, which has no water surface of its own.
- `portage_axes` vs Hig's merge on the window: the retreat basin
  resolves with no paint (cells differing > 1 m in the south-west box:
  55 % → 23 %, the rest is the feather ring); overall 11 % of cells
  differ > 1 m, three quarters of them the sonar adopted along the shore
  where the painted mask stopped short, and a block on the right where
  the 2020 lidar sits far below IfSAR in a canyon (IfSAR's own quality
  problem, now expressed as a winner). Dates for IfSAR and Whittier are
  assumed in the recipe and must be corrected.
- Tool: class and quality thumbnails per row (shown only when
  non-uniform or painted), backgrounds for class / σ / σ_total / class
  now, a **why readout** on hover (per layer z, class, gate, σ_q, σ_total,
  shown; winner marked), and a **paint axis** selector: weight (as
  before), class (pick a class; hard-edged), quality σ (good / ok / poor /
  veto bands). Class and σ paint rerun `state_rules` and refold; they
  save to `obs/<layer>/{class,sigma}_paint.tif` in the work dir, not to
  the composite's mask paint. Reading them back at build time is not
  wired yet (next).

### Observe stage experiment: re-gridding the Portage soundings (2026-09-20, late)

`tools/lidar/grid_bathy.py` grids sparse soundings into a bathymetric
observation using the above-water merge (`composites/portage_subaerial`,
built over the whole lake with `--diag-dir`) for the shoreline:
soundings (σ by type: 2024 sounder 0.5 m, Mayo 1977 3 m, 1972 estimates
5 m), waterline points at the lake level along the domain boundary, and
slope-extension points that continue the land slope 5–25 m above the
waterline down to 60 m below it (skipped where the land is flat or
steeper than `--max-slope`, i.e. deltas and ice fronts). Domain = Hig's
edge polygon (else water class + a sounding buffer). Outputs `bathy.tif`,
`distance.tif` (to the nearest real sounding), `sigma.tif` (σ from type
+ k·distance, k fitted on the hold-out), `constraints.geojson`,
`report.json`. `--holdout N` withholds every Nth sounding and reports
error by distance, separately for the 2024 sounder points.

Findings (355 points over 5.3 km², a fjord-shaped basin to −167 m):

| interpolator | sounder hold-out RMSE 0–50 m | 50–100 m |
|---|---|---|
| plain TIN of soundings (≈ Hig's, without his contours) | 6.9 | 15.3 |
| thin-plate spline, soundings only | 7.3 | 17.4 |
| thin-plate + shoreline constraints, local solve | 7.1 | 21.6 |
| thin-plate + constraints, global solve | 7.7 | 18.6 |
| **linear-kernel RBF + constraints, global solve** | **6.6** | **16.5** |

- The error is set by the data density, not the interpolator: ~7 m
  within 50 m of a sounding, 15–20 m at 100 m, for every method (n = 24
  and 13 test points, so the differences between methods are within
  noise). σ grows at ≈ 0.2 m per metre of distance from data.
- The shoreline constraints do not reduce the hold-out error, but they
  fix the *shape*: a bowl-shaped rim from the land slope instead of a TIN
  hull, and no interpolation over land. That is what they are for.
- A **local** RBF (`neighbors=64`) leaves visible seam streaks in the
  hillshade; the global solve (≈2 300 sites, 14 s) does not. Thin-plate
  bows deeper than the TIN between sparse points (bias −5 m at 50–150 m,
  −21 m at 150–400 m from data); the linear kernel overshoots less and
  is the default.
- With this observation in the axes recipe (`portage_axes_regrid`,
  σ from the raster, `class_sigma_max` large because the domain polygon
  is the class evidence), the lake bed wins the whole lake including the
  retreat basin. `state_rules` was changed for this: class credibility
  (which observation says what is here now) is separate from elevation
  eligibility (σ < `sigma_max` to compete), and where nothing eligible
  is compatible the best compatible observation wins anyway, flagged
  `fallback`. Without that, a big-σ bed observation lost to an obsolete
  ice surface.
- What would actually lower the error: more soundings (each 2024 track
  is worth more than any interpolator), and Hig's guide contours as a
  constraint class (`--contours`, not yet wired; they are in
  `251018_Hig_interpolation_contours.gpkg`).

**Interpolator choice (2026-09-20, late).** Hig: the linear-kernel surface
makes sharp points at each sounding, and the slope extension left a
ridge on the lake bottom. Tried, all with hold-out on the 2024 sounder
points (n = 24 within 50 m, 13 at 50–100 m, so differences under ~2 m are
noise):

| method | 0–50 m | 50–100 m | look |
|---|---|---|---|
| linear-kernel RBF, global | 6.6 | 16.5 | cones at every sounding |
| thin-plate, global, exact | 7.7 | 18.6 | smooth; bows deep between sparse points |
| **thin-plate, global, smoothing 20, one-sided extension** | **7.4** | **16.5** | **smooth, no ridge, natural bowl** |
| trend (depth vs shore distance) + Gaussian process, Matérn 5/2, nugget = σ² | 8.6 | 22.2 | shore-parallel terraces from the binned trend |
| spline in tension t = 0.3, sparse direct solve on 8 m grid | 9.0 | 27.5 | crude implementation, 4 min; dropped |

Decisions: (1) thin-plate spline, global solve, `--smoothing 20`, kernel
smooth at data (no cones); (2) the **slope extension is one-sided**: fit
without it, keep only extension points below the unconstrained surface
(the bed is at least as deep as the continued land slope, never forced
shallower), refit — this removed the ridge (513 of 1125 points kept);
(3) sigma from distance stays; the GP posterior would be the principled
σ but its trend model needs work before it is worth it.

Background: Smith & Wessel 1990, continuous-curvature splines in tension,
is the standard for bathymetry (GMT `surface`; tension suppresses the
minimum-curvature overshoot we see as bowing); kriging is equivalent to
a spline under a particular covariance and gives σ directly; GEBCO
grids sparse soundings with splines in tension. A proper tension spline
(GMT, `brew install gmt`) is the next thing to try if bowing between
sparse points matters at a site; the smoothing parameter does part of
that job here.

**Shoreline, point uncertainty, profile and band tools (2026-09-21).**
Hig: the shoreline still looked strange (suspected merge artifacts
compounding with the shore handling), and closely spaced soundings with
big disagreements made quirky bottom; asked for a profile tool and an
elevation band view.

- **The merge artifact was real and is fixed in the rules.** Inside the
  lake the subaerial merge had no compatible observation (class now
  water, no bed layer), so the fold fell through to the IfSAR base,
  whose lake sits 13 m below the real level. `state_rules` now has a
  last resort: where nothing compatible exists, the newest dated
  observation of any class wins (the water surface), flagged, never a
  stale base. The lake interior of `portage_subaerial` is now the lidar
  water surface at 28.9–29.2 m.
- **The waterline is now where the merge crosses the level** (first dry
  cell beside a wet one), not the drawn polygon edge, which sits ~10 m
  inside the lidar water for 73 % of its length. Slope is measured only
  through dry cells; extension marches only through wet cells. Where a
  topobathy layer contributes bed inside the lake those cells become
  observations (`--winner`, `--bed-winner`); at Portage that is 0.003
  km², so none.
- **Soundings inconsistent with their neighbours** (|resid| > 3 MAD and
  > 8 m against the median of the 6 nearest within 200 m) get σ × 4 and
  are listed in `report.json` (9 at Portage, up to 103 m apart between
  neighbours 70 m apart — a decimal-point or unit error to review).
- **Weighted thin-plate spline** (`--method wtps`): the classic smoothing
  spline with per-site σ² on the diagonal, coordinates scaled to km
  (in metres the system is ill-conditioned and gave 18 m errors).
  Sounder hold-out 6.7 m (0–50 m) / 14.0 m (50–100 m), the best of
  everything tried; weights are what let a 1972 estimate or a flagged
  fix pull less than a 2024 fix.
- Waterline and extension σ are now parameters (`--shore-sigma` 1 m,
  `--ext-sigma` 3 m + 0.1 m/m); with the level-crossing waterline there
  are ~6 000 waterline and ~11 000 extension points, so their σ must
  stay well above the soundings' 0.5 m.
- Open: using the lidar water class to extend the domain beyond the
  polygon (`--water-class`) scored worse on the 0–50 m bin (11.1 vs
  6.7 m) and took 7 min; with n = 24 that is not conclusive. Default
  stays polygon domain + level-crossing waterline.
- Tool: **profile** (drag a line; chart of composite, every layer and
  the reference, `/api/profile`), **elevation band** background
  (composite or layer, viridis ramp clipped to a band, two vertical
  handles or typed limits, "fit to view" takes the 1–99 % range of the
  visible composite via `/api/range`).

### Sequential rules (2026-09-21): a mask is "better than the composite so far"

Hig, looking at the Anchorage 2015 mask: the global rule ("where this
layer beats every other layer", with the per-cell `lowest` tie-break
between three lidars of similar σ) made 18 617 winner fragments that
despeckle and feather turned into an organic 185-blob patchwork. His
model instead: walk the stack bottom-up; IfSAR is the base; Anchorage
2015 replaces it everywhere it is an improvement, even though later
layers will replace much of that again; Whittier then replaces large
parts of the composite so far; and so on to the top. **A layer's mask
is where it beats all layers below it, not where it beats all layers.**

`axes.state_rules_sequential()` is now the default (`"rules":
"sequential"`; `"global"` keeps the old one). Per cell, observation i
replaces the running composite when its class is compatible with the
class-now (the newer of the running newest and i), and either the
composite's shown class is no longer compatible (a newer water/bed
observation makes old ground obsolete), or its expected error beats the
running one by more than `margin_m`, or `lowest` is switched on for
that layer in the recipe and the smoothed (default 50 m) difference is
lower by more than its tolerance. Nothing compatible shown yet + this
is the only observation → last resort, flagged. Masks then get the usual
despeckle + feather.

Results on the test window against Hig's merge: cells exact 82 % → 90 %;
differing > 1 m 11.0 % → 5.4 %; retreat box 23 % → 18 %. The Anchorage
2015 mask is its footprint minus its water class, one piece; Portage
2020's is its full footprint. `lowest` is off in every recipe; Anchorage
2015 carries σ 1 m for its snow (Hig: multi-metre errors in places)
until a snow modifier exists. The legacy regression is still bit-exact.

### Snow, and the water surface in the stack (2026-09-21)

**The lake surface stays in the composite until the bathymetry replaces
it.** Hig: the water surface is accurate data, it just needs classing as
water rather than ground. The compatibility table now lets a `water`
observation stand in at half rank where the class now is water or bed,
so under sequential rules Anchorage 2015 replaces IfSAR over the lake
with its water surface (its mask is its whole footprint, 87 % of the
test window), Whittier's water surface does not beat it, and the sonar
(gate 1) replaces it. The subaerial merge's lake is therefore the lidar
water level with no special case. Snow stands in for ground the same
way.

**Snow is a class, not a sigma.** Added `snow` (code 9) with these
rules, which are a deliberate compromise between snow-awareness and not
mapping every patch:
- A snow surface is ground plus an unknown 0–few m. Compatibility with a
  ground target is half rank, and the sigma raster adds
  `snow_sigma_m` (1.5 m) in quadrature where the class is snow. So a
  snow-free survey (σ 0.15) beats a snowy one even if older, and where
  only snowy data exists it still shows, flagged.
- **Detection is a guess at region scale** (`snow_vs` class step):
  higher than a reference surface by more than `dz_m` (0.5) averaged
  over `smooth_m` (50 m), AND smoother than the reference (roughness
  ratio < 0.7), opened and cleaned of blobs < 2000 m². The reference is
  a named snow-free layer or the composite so far. At Portage the
  reference is Portage 2020 (October); Anchorage 2015 comes out 2 %
  snow on the test window, at the high elevations.
- Residual snow that the detector does not see stays inside σ, and
  "ground" in every DEM is accepted to include some snow surface.
  Users correct with the class brush (snow is in the picker).
- What would make this better: a snowline elevation per survey date as
  a prior, and DSM/DTM pairs where they exist.

**Water and glacier classing for users.** The class brush already
applies any class. Added wand rasters **roughness** (m, local residual
from the mean; water, snow and ice are flat — tolerance ~0.1 m floods a
lake or a glacier tongue in one click) and **slope** (degrees). The
recipe can also class from vector files (`polygons` step: glacier
outlines, drawn lakes) — implemented, not yet used at a site. The
water-level detector remains automatic; for the sea the level is an
input.
