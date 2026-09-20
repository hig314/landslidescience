# Topobathy compositing: tools and interface

Status 2026-09-20: plan, nothing built. Third draft. Second draft came after Hig pointed at
his Portage merge (`/Volumes/Nunatak/Landslides/Portage/Portage_topobathy/
Crops/Topobathy_merge/`): a sequence of identically cropped DEMs, one 8-bit
mask per DEM painted in Photoshop, folded in order as
`out = out·(1−m) + new·m`. That is the model this plan generalises. The
references (USGS DEMFusion, CoNED "binary code", GRASS r.mblend,
DEM_Smooth_Blend) supply the automatic mask generators and the derived
layers; they do not change the model.

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

Real change is the caveat: for a "current ground" composite the minimum is
right (the post-failure surface is the ground now), for anything
historical it is not, and a `prefer_year` variant exists for that. Where
the rule is wrong locally, the fix is a brush on the loser's mask, which is
why this is a mask generator and not a derived "min of N" layer — though
the two are equivalent in the fold, one mask per layer is what a painter
can reach.

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

The fault-scarp pattern: Terra Draw (`pointerDistance: 8`, `doubleClickZoom`
off), CSRF header, JSON-vs-HTML response check, editor-only including
reads, PostGIS table `composite_edits(id, recipe_id, layer, kind, params
jsonb, geom, edited_by text, timestamps)` from an idempotent management
command. `kind` is `prefer` / `exclude` (polygons rasterised into a layer's
`mask_paint` at build), `waterline`, `seam_width`, `slope`,
`profile_shape`, `attention` (a note, no effect). A "Composite" panel
lists recipes from `/lidar/composites.json`, shows layers in fold order,
and shows the current effective masks as a tinted overlay (PMTiles baked
at build time). `composite.py build` exports the table to `edits.geojson`
and stamps the hash into the COG, so a build is reproducible from the repo
alone while drawing happens wherever the gated layers are visible,
including prod.

### Tier 3 — the brush, local and offline-first

Painting has to feel like painting, so it runs where the stack is: on the
Mac, against the dev server with the composite working directory mounted,
with no network in the loop. Sync to the repository (paint rasters,
`edits.geojson`, the built composite) happens at whatever cadence Hig
chooses, by the usual commit and publish steps; prod never hosts the brush.

- **Client**: a canvas layer over the map showing the effective mask for
  the selected layer; brush with radius in metres (zoom-independent),
  hardness, value, opacity. A stroke draws on the canvas immediately and
  is posted on pointer-up as a LineString plus brush params. Undo pops
  the session stack and re-posts.
- **Server, local only**: splats the stroke into `mask_paint` (a
  Gaussian-falloff disc stamped along the path, alpha-composited; a 200 m
  stroke at 1 m is a few tens of thousands of cells, well under a frame),
  re-folds the cached stack over the stroke's bounding window, invalidates
  those tiles, and serves both the mask overlay and the composite as tiles
  from `/lidar/composite/<id>/{mask,dem}/{z}/{x}/{y}`. Latency is bounded
  by the fold over the dirty window, which is why stages 1–4 are cached.
- The same splat code serves `import-mask` (which is a stroke with a
  raster instead of a path), so a Photoshop edit and a brush edit land in
  the same place and the build sees no difference.

If a browser canvas over MapLibre cannot be made responsive enough under
the demshade worker, the fallback is a small native painting window
(Qt + numpy) over the same local endpoints; the model does not change. Two
checks before committing either way: whether the demshade package accepts
a tile URL template rather than a PMTiles archive, and how a MapLibre
`canvas` source behaves under the demshade compositor.

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
5. **Tier 3** brush and live preview, local only.
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
- For `lowest`: is "current ground" the default intent (min wins, real
  change adopted), or should real change above `max_drop_m` always wait for
  a human?
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
