# Topobathy compositing: tools and interface

## Resume here (2026-10-14, evening) — Grewingk at v6, creek next

**Where things are.** Grewingk Lake is at **v6** in the dev viewer (`grewingk_topobathy_v6`), built from
`composites/grewingk/recipe.json` by `build -> fill -> build` (chain scripts were in the session scratchpad; the
recipe + `make_ice.py` + `make_nohf_dtm.py` + `topobathy/dsm_bare.py` + `topobathy/flat_water.py` are the
real record). Branch `topobathy-compositing`, last commit 640225d, **nothing pushed**; the re-levelled
soundings are in dev only (prod needs Hig's approval). All 194 Grewingk 2021 point tiles are on disk
(`kbay_reclass_test/grewingk_2021/laz`); the 2021 classes over the whole box are `obs/grewingk_2021_box`.
Sections "Grewingk Lake v3 / v4–v5 / v6" below are the change log; each v answered Hig's review of the one
before, always as a general rule rather than a Grewingk tune (Hig's standing instruction).

**v6 numbers.** Lake 4.90 km2, max 173 m, 0.249 km3, 0.001 km2 above water; Berg sounder median +0.01 but
NMAD 0.38 (was 0.22 at v4 — unexplained); Hig 2026 lead lines +1.3 m (were +0.15 at v4); one 2019/21
bound violated by 4.2 m; 43 ice-face bounds violated (p90 0.7 m, worst 57 m at the run's south end, which
sits on lidar rock). Emerald Lake 0.37 km2 / 50 m / 24 m mean by hypsometry.

**Open, in order.** (1) **Grewingk Creek** — roadmap steps (a)–(e) below (reclassify the corridor; one
non-increasing water surface from the water returns; link pools across bars; channel detection back on
with the lake excluded; sections per thread). Start with (a)+(b). (2) The ~4,500 m2 shallow strip below
the SE cliff (59.59976,-151.06609): no soundings within 150 m, basin prior 2–3 m; "shores keep the band"
was tried and reverted (v6 notes) — needs Hig's call on how a cliff shore should run. (3) Why the Berg
NMAD and the 2026 lead lines got worse between v4 and v6 (suspects: the KBay layer changed the shore
rim; the shore-kept-as-ground rule). (4) Radial streaks in the basin shape near shore (widening window;
Hig to decide). (5) Hig to look at the 3DEP flat patches at 840/448/364 m and the glacier "lakes" ≥ 550 m.

## State of play (2026-10-09) and how to resume

**The pipeline runs end to end on Portage.** Review of 2026-10-09 (two
weeks idle, 59 commits behind `main`, merged clean): the decision model
and the mask tool had outrun the plumbing — every recipe was a Portage
test window, nothing could write a deliverable DEM. Hig's call: finish
step 1 of the revised order before any more physical awareness (glacier
thinning, seasonal water levels — see "Deferred: physical awareness" at
the end of this section). Done the same day:

- **Streaming full-grid build** (`composite.py build recipe.json`, no
  `--window`): tiles of 2048² built with the existing halo, in `--jobs`
  forked workers (6 workers: 193 s for the 30000 × 20000 mask recipe,
  ~9 min for the axes recipe), written into tiled sparse GeoTIFFs under
  `<work_dir>/` — `out/<id>.tif` (archive COG, ZSTD, overviews,
  `-mo vertical_shift_m=0 composite_recipe=<sha> composite_paint=<sha>`),
  `out/source_index.tif`, `masks/<layer>.tif` + `<layer>_auto.tif`, and
  for axes recipes `out/{class_now,winner,date_now,sigma,fallback}.tif`
  (`--diag` adds per-layer class/σ). **Verified bit-identical** to a direct
  window build over the same cells; the legacy regression stays bit-exact.
- **Water levels are pinned once per grid.** A coarse pre-pass (1/12
  sample) estimates every `"auto"` level over the whole grid and writes it
  into the step, so tiles cannot disagree about where the water is.
  `--pin` gives a window build the same levels. Note the mask recipe's
  single level under the sonar came out **28.99 m** over the whole lake
  against 29.21 on the test window — the lidar surveys hold the lake at
  different levels (28.9–29.2) and the whole-footprint mode picks the
  commonest; the axes recipes detect each survey's own level, which is
  the better model.
- **`out/report.json`**: per layer valid/shown/feathered km², class
  fractions, tile-median info; composite area by source; **per overlap
  pair** (every layer against every lower layer, by class of the upper
  one) n, median, NMAD, p16/p84 from a 2 cm histogram over ±250 m, and
  **`rate_m_per_yr` = median dz / Δt** where both are dated. This is the
  glacier-thinning readout: measure before modelling.
- **Class / σ paint is read back at build time** (`axes.apply_class_paint`
  / `apply_sigma_paint`, shared with the tool) — before this, a saved class
  correction changed nothing in a build.
- **Paint files cover the full grid, sparse** (`topobathy/paint.py`):
  first save creates the file, later saves are windowed `r+` writes, old
  domain-box files migrate on first touch. Several windows accumulate into
  one record; the tool reloads all three kinds on restart.
- **`import-mask` / `export-mask`** (the Photoshop round trip) and
  **`publish`** (prints, or `--write`s, the `datasets.json` entry:
  `archive_mode: copy`, `dev_only: true`, zooms from the grid).

Next: the hosting side runs `build_lidar.py <id>` + the dev catalogue
for the two dev_only entries so they show on `/lidar/` (WORKSTREAMS
boundary). Hig redirected the second site from Lituya to **Kachemak Bay**
(next section). The rule set is frozen until a named site fails.

## Roadmap (2026-10-14, Hig's outline, refined)

Hig's sequence after Grewingk: lessons -> preliminary KBay -> broad dataset -> water surface/depth ->
hand edits -> publish -> explore (glacier vs ground, sediment vs bedrock).  Refinements, agreed or open:

**0. Grewingk "dialed" -- an acceptance list, so done is checkable.**  Soundings fit (median ~0, NMAD
< 0.3 m); lower bounds honoured or explained; no lake cell above the water; no seams or streaks in the
deep bed; the ice face vertical, no glacier as ground; the outlet CREEK a channel, not a string of ponds;
v-numbered in the dev viewer with the as-of date.  Open now: the creek (below), the basin shape's radial
streaks (widening window, Hig to decide), 30 ice-face bounds, 0.12 km2 above water.

**The creek is step 4 pulled forward.**  Grewingk Creek needs the water surface model anyway: it is ~45
wet domains (pools between bars), each given a flat level and a little basin.  Measured 2026-10-14: the
points' water returns slope continuously 48.8 -> 21 m over ~2.3 km; the vendor DTM's creek is NOT the
water (40.2 m where the returns are 42.5-43.4 m at x 605.4 km: stream hydroflattening drops each reach to a
downstream level); class 1 (unclassified) is as common as ground there.  So: (a) reclassify the creek
corridor (water / bar / ground) where class 1 hides it; (b) ONE water surface for the network from the
water returns, smoothed along the flow and non-increasing downstream; (c) link the pools across bars into
one channel system (threads of a braid share the surface); (d) channel detection back on with the named
lake (lake_dmax) excluded -- it read the lake's east end as a river because the retreat zone's water came
from the SfM; (e) sections per thread from width (fill.channel_beds), conditioned by any depths Hig has.
The water surface this builds IS the first piece of the water-surface DEM.

**1. Lessons -> defaults, and a regression set.**  Not a prose review alone: each lesson becomes a recipe
default or a rule with a test.  Candidates: SfM never over water; ice by multi-epoch lowering (and the
SfM's +3.4 m water bias); vendor DTMs hydroflattened (rebuild from points where a lake or river matters);
per-survey vertical shifts in ONE registry with their evidence (Grewingk +0.36, Seldovia xGEOID17B 1.42,
MLLW via bench marks); sounder sound speed; lower bounds; free ice faces; shore-gradient with widening
windows; bergs are not islands; lake levels from returns not shores.  The regression set: the windows we
have tuned (Portage, Peterson-China Poot-Halibut Cove, Grewingk, the KBay reclass tests), each with its
metrics (soundings fit, holdout, above-water lake cells, seam/curvature score), run after any rule change.
`composite.py holdout` exists; a `regress` over all windows is the missing piece.

**2. Preliminary KBay -- scale is the issue, plan for it.**  build is tiled and streams; FILL IS NOT (one
whole-grid pass: 13 min for Grewingk's 21 M cells; KBay at 2 m is ~30x larger, and domains such as the
bay itself span tiles).  Needed: fill by domain (each wet domain with its own bbox + halo, small ones
batched), coarse-to-fine for the big marine domain, and a cache so an unchanged domain is not refilled.
Iterations then cost minutes where something changed, not hours.  Also: which other surveys are
hydroflattened and need the point rebuild, and the point fetches that implies (the DGGS zip route, ~0.5 MB/s
here -- start early).

**3. Broad dataset.**  Decide the extent (Kenai Peninsula? every hosted survey? Kachemak + Cook Inlet
margin?), the resolution (2 m composite with 1 m where surveys allow?), and the time model: one as-of date
per region, or the newest credible observation everywhere with a date raster (the build already writes
date_now).  The outer bay: NOAA OCM 2023 seamless 2 m, underwater only (reference_outer_kachemak_bathy).

**4. Water surface and water depth.**  Products: water surface elevation (lakes flat at a stated level and
date; rivers sloping, from returns; the sea at a stated tidal datum, MHW or MLLW -- not the flight's
tide), water depth = surface - bed, both with sigma.  Hydraulic awareness: a non-increasing-downstream
surface along each channel, sections that conserve area across braids, bars and pools; a flow network to
carry it.  Decide whether the surface is the flight-day stage (what was seen) or a reference stage (what
modellers want) -- probably both, the second a parameter.

**5. Hand edits.**  The paint/mask tool already edits classes, sigma and masks (Tier 3).  What Grewingk
needed and the tool cannot do: a lake-here seed (make_ice HAND_LAKE), an island keep (make_nohf
ISLANDS_KEEP), a lower-bound run of soundings, a lake level, a bed shape (a breakline / thalweg / contour
with depth and sigma -- the sounding_contours table is already that shape).  Proposal: edits as small
GeoJSON features in the repo, each with a kind, the values, who and why, applied every build, listed in
the report; drawn on /lidar/ with the landform-trace machinery.  The scattered hard-coded lists become
the first edits.

**6. Publish.**  Topobathy, its sigma, the source index (provenance per cell), water surface, water depth;
a methods page section; versioned ids.  LICENSING: a composite that includes a gated survey (Katie
Hughes' multibeam, gated pending attribution) cannot be public until that survey can be -- either
permission first, or a public build without it.

**7. Explore.**  Glacier vs ground (multi-epoch lowering generalised, the ortho, ITS_LIVE velocity, the
G10040 outlines; a dated ice-front layer -- the face retreats every year); sediment vs bedrock (roughness
and slope signatures, depth-to-bedrock priors, the lake beds as sediment traps); buoyancy constraints at
calving faces; uncertainty that a user can query; ADF&G contour maps and the Emerald Lake depth
distribution as soft constraints.

**Questions for Hig.**  (1) Which first application drives priority -- hazard modelling (landslide into a
lake, outburst), navigation/recreation, habitat?  It sets the water-surface datum and how much hydraulics
matters.  (2) Broad extent and resolution.  (3) Any depths or cross-sections on Grewingk Creek?  (4) The
gated multibeam in public products.

### Grewingk Lake, first attempt (2026-10-13)

Hig: build a Grewingk topobathy with the ice as Mark Laker's SfM shows it (19 Sep 2023), the ice face
VERTICAL and putting no constraint on the depth, the bed 150 m deep in the middle. Recipe
`composites/grewingk/recipe.json` (2 m, x 602-614 km, y 6605-6612 km), ice from `make_ice.py` (the 2020
G10040 outline minus what the lake had taken by 2023; face at x 609.19-609.50 km, matching the ortho).
In the dev viewer as `grewingk_topobathy_v1` (`tools/lidar/dev_viewer_add.py`, new, generic).

- **SfM is used for the glacier only** (Hig: SfM is bad over water and at the lake shore; measured here
  NMAD 2.9 m over the lake, +3.4 m above the lidar on land beside it): the dixon2023 layer is a copy
  holding only the 2023 ice and the retreat zone. Given everywhere with a canopy default it became the
  newest class over the lake and the lake got no basin. Lake level pinned to the 2021 lidar's 48.55 m.
- **Grewingk 2021's point classes are ~60% void over this box**: only their water is used; the lake is
  also taken from the DTM itself (flat at 48.55 +- 0.35 m, connected to the class lake), else its surface
  read ground along the face and the fill met a "shore" at water level instead of the ice.
- **fill.py, new**: `ice_face: "free"` -- ice rim beside a WET domain is neither shore nor boundary value
  (residual and spline free there; the solver skips such neighbours = no flux); ice inside a lake
  (bergs, the face fringe the closing wraps in) is dropped as evidence (as an "island" it pinned the bed
  20-30 m above the water); and the lake shape's distance-to-shore is to any non-ice land, as if the
  lake ran on under the glacier (to the land rim only, the shape tapered to the face). `lake_dmax:
  [{x, y, dmax_m, level_m}]` overrides one lake's deepest depth and level. **Lower bounds** get one
  active-set pass: violated bounds join the points at their bound and the domain is refitted.
  `channel_min_m2` huge in this recipe: channel detection read the deep east end as a river.
- **Result**: lake 4.8 km2, max depth 170 m by the face, mean 50 m, 0.26 km3; soundings honoured (Berg
  median 0.00 m, NMAD 0.29; lead line +0.15/0.56); bounds within 1.8 m (sigma 2.4). Face: middle
  42-72 m deep, its ends shallow where they meet the valley walls.
- **Open**: lake cells above the water (0.13 km2, to check); the TPS correction extrapolates toward
  the free face from soundings ~100-300 m off it; berg / glacier buoyancy constraints (Hig: later);
  the ice front moves (Hig: our face data is already out of date) -- a dated ice-front layer.


### Grewingk Lake v2 (2026-10-14)

Hig's review of v1: (1) Ed Berg's sounder track reads a bit high where it overlaps dry 2021 lidar -- drop it
wholesale; (2) one obvious bad point at 59.60800,-151.13920; (3) the track's run along the 2017 ice face
(59.60011,-151.07073 to 59.60665,-151.08217) reads unrealistically shallow, probably off-nadir -- make it
lower bounds; (4) the outlet bay is shallower than the fill made it -- use measured near-shore gradients
to inform the assumed one. Done:

- (1)-(3) in the soundings ingest (main, `tools/soundings/ingest_grewingk.py`): the level is 50.05 m, not
  50.5. On the RAW track, rows over 2021 lidar > 49.0 m (dry; the 2021 water is 48.55, 48.7-48.8 near the
  inflows) on ground < 5 deg: 152 rows, ~0.6 m deep, +0.46 m (NMAD 0.32) above the lidar, and all three
  overlap areas agree (west end +0.40, outlet +0.33, +0.67). Raw rows 21131-21135 (24-43 m after a 1.34 km
  position jump, on a shoal dry in 2021) rejected; the ice-face run (raw rows 41712-44273, 316 thinned
  points) loaded as lower bounds.
- KATIE'S DATUM in that light: on the multibeam's flat floor (~23 m, 122 raw rows, NMAD 0.06) Ed now reads
  1.30 m DEEPER than the multibeam (0.85 m at the old level); at 6-10 m they agree (+0.2, NMAD 1.0). A
  constant datum error in the multibeam would show at every depth; a ratio of 1.063 (Ed's depth / the
  multibeam's) is what a sounder reading ~6 % deep gives -- e.g. one assuming ~1500 m/s in ~2-4 C fresh
  water (~1420 m/s). Not applied: one depth band cannot tell a scale from an offset, and a 6 % scale would
  take ~5 m off Ed's 87 m and ~9 m off its 149 m readings. Settles with the sounder's speed setting, or a
  lead line dropped on the multibeam's floor.
- (4) `shore_grad_m` in fill.py (docstring SHORE GRADIENT): every depth within 200 m of shore (soundings,
  multibeam bed one per 10 m) gives rho = measured / prior depth; log rho is smoothed along the lake
  (sigma 150 m, 3 sites' weight on rho = 1) and multiplies the lake prior's depth within 200 m of shore,
  fading out by 400 m. 4,137 depths, rho p10/50/90 0.17/0.71/1.73. The outlet bay north of the
  cross-outlet line: bed 9.5 m (39 m deep) in v1, 40.8 m (8 m deep) now.
- Result (`grewingk_topobathy_v2` in the dev viewer): lake 5.08 km2, max depth 168 m, mean 50.8 m,
  0.258 km3; soundings fit median 0.00 / NMAD 0.23 (Berg), +0.05 / 0.28 (Higman 2019/21). Lower bounds:
  22 of 316 ice-face bounds still above the surface after the one active-set pass, most by ~1 m; worst
  30.8 m at the run's south end, where two ordinary 0.9 m readings sit 2 m from a 61 m bound -- the same
  off-nadir problem just outside Hig's endpoint. Above-water lake cells 0.151 km2 (0.13 in v1).

### Grewingk Lake v3 (2026-10-14)

Hig's review of v2, all in `grewingk_topobathy_v3` (dev viewer; an interim from the tiles then on disk was
`..._v3_interim`):
- Berg sounder depths x 1421.6/1500 (seawater speed of sound assumed, cold fresh water): now agrees with
  the multibeam floor to 0.2 m (was 1.3 m deep); level re-tied to dry 2021 lidar, 50.01 m.
- Whole composite +0.36 m to the other DEMs: grewingk2021 and sonar2023 layers shift_m 0.36, lake level
  48.91, fill `points_dz_m` 0.36 (the soundings database stays in the published lidar's datum).
- 2021 DTM rebuilt from the classified points without hydroflattening (make_nohf_dtm.py; all 194 tiles
  over the box range-fetched from the DGGS zip). Water-classed returns > 0.65 m above the lake in patches
  >= 50 m2: 13 found, only Hig's island (59.60571,-151.11924) kept -- the rest sit where Ed reads 8-74 m,
  i.e. October 2021 icebergs; listed as candidates in obs/nohf/islands_2021.json.
- Ice where the 2021 lidar stands > 5 m above the 2023 SfM, connected to the glacier (rock near it reads
  -0.9 m median, outlined ice +11 m): +0.32 km2 ice on the south edge, and the lake's SE corner (2023
  surface at the water) as retreat.
- Deep-bed seams: the shore-gradient factor is smoothstep-blended and smoothed over the lake; and its
  shoreline window WIDENS with distance from shore (Hig: one cliff patch had big effects down-slope), as
  does the land rim slope in shore bands (`rim_widen`).
- Result: lake 5.10 km2, max 170 m, 0.248 km3; Berg fit 0.00 / NMAD 0.22, lead lines within ~0.1 m;
  above-water lake cells 0.12 km2; 30 ice-face bounds still violated (most < 1 m, worst 31 m at the run's
  south end). Open: radial streaks in the basin shape's first few hundred metres (distance to the nearest
  shore cell -- the widening window would apply there too, Hig to decide).

### Grewingk Lake v4–v5 (2026-10-14)

v4: the SE-corner patch Hig marked (HAND_LAKE seed, low-drop retreat) and the rim residual capped at the lake
level. v5, Hig's review of v4 -- with the instruction to solve each thing as the general case, not tune one basin:
- **Stale class raster (general trap).** The 2021 point classes were built (2026-10-11) before the tiles north
  of y 6610.5 km were fetched; "no returns + flat DTM = water" turned the outwash gravels there into ponds.
  observe_points.py now records unfetched tiles' footprints and `classify` voids them; rebuilt over the whole
  box (obs/grewingk_2021_box). Outwash water 22 % -> 1.6 %.
- **A hydroflattened lake is exactly flat.** make_ice's lake_2021 was "within 0.35 m of 48.55 and connected",
  which took gravel at 48.2-48.9 m; now also a 3x3 range < 2 cm. Same idea, generalised: flat_water.py finds
  hydroflattened water in any bare-earth model (3DEP: Emerald Lake, nine other patches), used as a fallback's
  water class so a lake where 3DEP is the only data gets a basin.
- **Agreement means detail, not level.** The last of the SE patch: 2021 ice barely above the water, where the
  2023 SfM over water (smooth, a few metres high) matched it in level. A later survey confirms an earlier
  surface only if it reproduces its detail (surface minus an 8 m gaussian, correlated over 18 m): shore rock
  0.9, 2021 ice now under water 0.05. Retreat where < 0.4 in the 2023 water band, connected to retreat or ice.
- **Feather underlay (composite.post_masks).** A feathered edge had nothing to blend with where the layer
  below is a fallback shown only where its rules took it, so the lidar edge was a hard step. The nearest lower
  layer with data, within reach of its own take, is now laid under any partial edge; feather 15 -> 30 m.
  Gradient p99 at the lidar edge 4.2 -> 2.7.
- **A DSM as bare ground (dsm_bare.py).** Beyond the lidar the 2023 SfM rather than 3DEP -- but it is a DSM:
  0-0.5 m off the lidar on bare ground, 3-7 m high in forest and alder; the ortho's greenness does not find
  the canopy (dark spruce, shadow). The DSM's 30 m median against 3DEP does: < 1 m above = bare (matches the
  lidar, -0.3 m), > 2 m mostly canopy. SfM where bare (8.2 km2 beyond the lidar, levelled -0.24 m, NMAD
  0.78), 3DEP under canopy (5.3 km2), never over water or ice.
- **Hypsometry as a lake constraint (fill lake_dmax `hypsometry`).** Emerald Lake's sounder data (NAU,
  via Ed Berg 2017) is likely corrupted (Hig): not conditioning, only its deepest depth and area-depth
  distribution (nearest-sounding area per 10 m track cell). The lake profile's depths are remapped by rank
  onto it -- shape from the profile, the share at each depth from the data. Result: 0.373 km2, max 50 m,
  mean 24 m, 9.1 Mm3.
- **A lake's modelled bed never stands above its water.** The shore residual + spline overshoot put a
  10-30 m fringe below the SE cliff 0.5-5.6 m above the water; clamped (0.18 km2 box-wide, incl. an upland
  pond at 59.58333,-151.11495 whose whole bed sat above its level -- worth a look).
- v5: lake 4.90 km2, max 175 m, 0.246 km3, above water 0.001 km2; Berg fit +0.01 / NMAD 0.37 (was 0.22 --
  look at), lead lines +0.2 to +0.6; ice-face bounds: 45 violated (most < 1 m, worst 57 m at the run's
  south end, which sits on rock in the lidar). Open: creek channel work; glacier "lakes" at 550 m and
  higher; whether the 3DEP flat patches at 840/448/364 m are all water.

### Grewingk Lake v6 (2026-10-14)

Hig's review of v5:
- **KBay 2023 (kbay_2023_reclass_v2) added; the LOWER lidar wins where they differ.** It covers the SW half of
  the box. On ground it agrees with the shifted 2021 (median -0.05 m), but 2021 stands > 1 m high on 4 %: bad
  low-density returns 11-13 m high at 59.62226,-151.18886 and in a 2021 data hole at 59.58333,-151.11495
  (the v5 "upland pond"). kbay2023 sits below grewingk2021, which has the opt-in `lowest` rule (takes only
  where lower by > 0.3 m over 50 m). Both spots now read KBay.
- **Ponds carved into the outwash: a feedback loop in the fill.** Ground within 0.2 m of a pond's level was
  judged "water surface" -> fill_surface.tif -> water class at the next build -> a larger pond. That rule
  is for stray returns off calm water, which lie out on the water: a patch whose edge borders more dry land
  (not ice) than water is now shore and stays ground. Outwash water 0.156 -> 0.052 km2; what remains is
  where class 9 outnumbers ground (the creek itself).
- **Low "islands" are the water.** Inside a lake, a patch whose MEDIAN stands < 0.65 m above the water
  (make_nohf_dtm's island threshold) is surface. Removed the last SE sliver (112 m2, 0.2-1.1 m above the
  water; the maximum let it through once) that had pinned a 9,000 m2 flat bed below the cliff.
- **Tried and reverted: shores keep the band** (the deeper of the basin shape and the land slope carried on
  under the water, as the fill's design note says but never coded). It made the cliffs plunge, but max depth
  went 173 -> 245 m and the 2026 lead lines +1.3 -> +4.8 m. The ~4,500 m2 shallow strip below the SE cliff
  (59.59976,-151.06609, no soundings within 150 m) remains; its prior is only 2-3 m deep there.

### Kachemak test: Peterson Bay – China Poot Bay – Halibut Cove (2026-10-10)

`composites/kachemak_pedersen/recipe.json`, Hig's choice of a setting with
several datasets, partial overlap and real gaps, centred on
59.57368 N, −151.30133 W. **First recipe from `datasets.json` ids on a
stated grid**: `source` is a dataset id (→ archive COG, already NAVD88
with its `vertical_shift_m`), `grid` is `{"epsg": 6334, "res_m": 1,
"bounds": [...]}` snapped outward to whole metres (x 595–606 km, y
6602–6610 km, 88 Mcells; a full build is **54 s**). Layers in fold
order: `kachemak_bathy_2009` (Hydropalooza, class `bed`, no TIN hull to
reject), `grewingk_2021`, `kbay_2023_reclass` (the reclass, not the
vendor DEM: that one's compound NAVD88 CRS trips the gdalwarp geoid
shift). The three-way overlap is around Halibut Cove; China Poot Bay is
lidar + bathymetry only. Outputs and 1/8-scale previews in
`$LIDAR_BUILD/composite/kachemak_pedersen/out/`; entry
`kachemak_pedersen_topobathy` (dev_only) in `datasets.json`.

What the site taught, in order of importance:

1. **Grewingk 2021 sits ~0.36 m LOW in the collection's frame.** On bare
   smooth flat ground (both DTMs < 0.05 m rough, slope < 3°, 2–20 m)
   KBay − Grewingk = **+0.36 m, NMAD 0.06** (n 108k at 2 m); KBay −
   Seldovia 2019 NCMP topobathy (the survey the bathymetry's datum was
   tied to) = +0.014 m, NMAD 0.048; the reclass equals the vendor DEM to
   1 cm. Two independent products against one; no third overlaps it
   (Homer 2019, Lower Kenai have no data over Grewingk). Both say
   NAVD88 (GEOID12B) — the [[reference_navd88_geoid_model_trap]] again.
   Applied in the recipe as an explicit, reported per-layer **`shift_m`
   +0.36** (new; the stop-gap the plan allows: "applied only if the recipe
   says so"); **the real fix is `vertical_shift_m` on `grewingk_2021` in
   `datasets.json` + an archive rebuild, which is hosting's call after
   its own check.** With it, KBay − Grewingk on all ground overlap falls
   from +0.51 to +0.15 m (NMAD 0.41 — canopy and change, `lowest`'s job).
2. **A lidar's sea surface comes at several tide levels.** KBay holds the
   water at −3.0…−1.9 m NAVD88 on different lines (exactly flat,
   roughness 0.00–0.02 over the deep bay) and the reclass's own harmonic
   fill of the lagoons at +0.8 m (flagged in its `fill_mask.tif`, 2 =
   filled). The single-mode `water_level` detector cannot see that, so
   two class steps were added, each because this site failed without it:
   **`flat_below`** (flat below a level cap, opened, min area — low land
   at 1–4 m is ~0.6 m rough, water ~0.02, so they separate) and
   **`raster`** (class from a categorical raster that travels with the
   observation: the fill → `modelled`). The existing `snow_vs` detector
   gained an `as` option so it also classes a lidar surface that is
   > 1.5 m above a known bed as water. Result: KBay 86 % ground, 9 %
   water, 5 % modelled; the bay is bed, the channels and shore band
   water, the lagoons modelled with the Hydropalooza lines showing
   through as bed.
3. **Equal-quality surveys need `margin_m`** or the newer one wins
   everywhere by its 0.001 m smaller σ_t and `lowest` never decides.
   `margin_m: 0.05` in the recipe; with it Grewingk keeps ~30 km² and
   KBay replaces it only where lower by > 0.3 m at 50 m scale.
4. **The two things still visibly wrong are the two things the plan
   has not built yet**, now with a real site to build them on: the
   **intertidal gap** (the composite shows the lidar's water surface as a
   flat shelf at the flight tide, stepping down to the 2009 bed — the
   Seward "ghost shoreline", here with 1.98 km² flagged `fallback`), and
   the **lagoon fill** (a flat modelled surface at +0.8 m cut by sparse
   sounding lines — the sparse-point interpolation's case, paused). Both
   are honest in the diagnostics (`fallback.tif`, `class_now`), neither is
   hidden by a blend.

**Point-cloud classes (2026-10-10, later).** Hig: the DEM-derived
inference is the wrong level — classify from the point cloud (ground,
water, snow/ice, others if useful), and the reclass's flat lagoon fill is
meaningless: mask it out and interpolate the Hydropalooza instead.
`tools/lidar/observe_points.py` is the observe stage from the point
cloud: `stats` bins a survey's points (PDAL per tile → uncompressed LAS
1.4 → numpy; KBay from the 2,298 COPC tiles on `/Volumes/Powder/
lidar_src/kbay_2023_laz/` — all local — Grewingk from the DGGS LAZ tiles
on Nunatak) into `obs/<id>_pts/cellstats.tif`, 16 evidence bands at 2 m
(returns by class, lowest-return z per class, intensity per class, the
flatness of the lowest surface); `classify` applies a per-survey rule
set (`RULES`, printed with its outcome) to write `class.tif` in topobathy
codes, which the recipe reads with `{"raster": {"path": …, "codes":
true}}`. What the points said:

- **KBay 2023 (NIR, 1 Aug, leaf-on): no returns IS the water signal.**
  Over 7.5 km² of lagoon and bay the vendor's class 9 holds only 44 k
  points; the laser simply gets nothing back, and the reclass filled
  those holes. Rules: ground where the reclass DTM has a value and the
  cell has returns; **void** where there are no returns (opened, blobs
  ≥ 50 m²) and wherever the reclass's `fill_mask` is 2; water where
  class-9 returns outnumber ground — checked against the evidence
  bands: those cells are flat (0.05 m vs 0.23 for ground), sparse (14
  vs 48 returns) and sit at −2.9…−2.3 m (sea, 1.18 km²) or 50 m (the
  lake, 0.77 km²), so NV5's over-reaching water polygons are not biting
  here (0.07 km² in the suspect 0.5–5 m band); built where class 6
  outnumbers ground (the structures the reclass kept as ground); snow
  from class 21 (none on the 2023-08-01 lines). Result 96.5 % ground /
  3.4 % water / 0.1 % built of classified cells, 27 km² void. In the
  composite the lagoons are now **holes with only the sounding lines in
  them** ("none" 0.27 → 2.39 km²), the shore band is bathymetry up to
  where the lidar has ground, and `fallback` all but vanishes. The
  DTM-side `flat_below` / `snow_vs` steps are no longer used on KBay;
  they stay in the engine for surveys without points.
- **Grewingk 2021 (NIR, 12 Oct): no snow signal in the classes.** With
  36 of 119 tiles local the vendor's ground class seemed to stop at
  ~830 m, and `rules_grewingk` carried a snow hypothesis for DTM cells
  with only unclassified returns. With all 119 tiles (the other 83
  fetched 2026-10-10; `fetch_one.sh` compared the CRC against the wrong
  column of `ranges3.txt`, the inflated `.part` files were verified
  against the right one and renamed) the ground class covers 93–99 % of
  DTM cells in every 100 m band to 1100 m, and the rule finds nothing.
  Result 94.9 % ground / 5.1 % water (DGGS class 9). Snow on
  2021-10-12, if any, is inside the vendor's ground class and only a
  reference surface (the `snow_vs` step) or Hig's knowledge of the
  season can find it; intensity drops ~20 % above 900 m, which is
  ambiguous. The Grewingk layer now reads its `class.tif` like KBay's.
- A free measurement from the class-by-class report: **the lake at
  Halibut Cove sits 1.07 m higher in KBay (Aug 2023) than in Grewingk
  (Oct 2021), NMAD 0.02 m** — the seasonal-level reading, which the
  water class now isolates from ground.

### The reclass as the classification (2026-10-10, evening)

Hig: use *our* reclassified points, not the vendor's classes, and tweak
the reclass where it is wrong — (1) structures should be their own
ground class so the DTM can be planed under them; (2) bad returns over
water survive as topography (six sites given), but do not over-filter
the water surface: real water returns matter in running water and for
shorelines that hydroflattening moves.

The reclass (`kbay_reclass_test/model/`, hosting's product
`kbay_2023_reclass`) never wrote relabelled points — it writes surfaces
per 1 km unit — so "our points" means the labels its chain assigns, and
the observe stage reads them through the surfaces and masks the chain
now writes. What the six sites held, probed at full density
(`observe_points`-style pulls from the COPC tiles):

- **Three of the six "bad return" blobs contain no returns at all.**
  They are the chain bridging return-free water: TIN facets up to 30 m
  (A, a 413 m tarn), the zone solver extrapolating over a pond with no
  water class at all (D: +5 m on the rim, then the harmonic fill
  inherited the rim, 22–40 m over a 17 m pond), a facet reaching
  **592 m in a 528 m lake** (C).
- **Real floating returns**: single returns of ground-like intensity
  0.5–5 m above the water in vendor classes 1 *and* 2 (the vendor's own
  ground holds them), over tarns (A: 427 class-1 returns at +1.4 m over
  600 m² with 5 water returns), in bays (602361,6608610: 530 returns
  1.2 m over the sea), a boat (E, 15 m, +1–2 m), the shrub fringe of a
  lake (C's north shore: both flights see it, 1 m apart in height).
- **F is the other flight**: the 2024 water at +0.23 m admitted as
  water in the 2023 water's holes (RULE 2) while the 2023 water sat at
  −2.76 — 3 m islands of the higher tide.
- **An islet at C is real**: both flights put it at the same height
  (83 % of cells within 0.3 m). The vendor flattened it away.
- **Structures**: at Seldovia 108 of the 118 blobs the reclass raised
  over the vendor are vendor class 6, and 65 of 551 class-6 buildings
  were lifted — the larger roofs passing the rock rule, as the README
  guessed. Only 3 raised smooth blobs lack a class-6 label, so the
  vendor's building class is a good structure source; at Halibut Cove
  the TIN already passes under every class-6 building.

**Changes to the chain** (live copy on Nunatak; backup
`model_backup_20261010/`; the repo copy `tools/lidar/kbay_reclass/model/`
on `main` is hosting's to sync):

- `EXCL` gains **6 and 17** in all 17 modules: a roof is never a ground
  candidate, the TIN passes under it. `apply.py` writes `struct_mask`
  (class 6/17 cells, closed) and `fullrun.py` keeps it.
- **`model/water.py`** (new): a local water **level** W from classes
  9/20 (sigma-clipped mean, nearest-neighbour to 20 m; a median needed
  most of its window to be water and gave the tarn no level);
  **floating** returns → class 7 by two rules — (a) 0.5 m above the
  lowest return *in its own cell* when that return is at the water
  (a 3×3 reach stripped a 3–5 m ring off every bank steeper than
  ~10°), (b) 0.5 m above W with ≥ 60 % of the 9×9 water or return-free,
  iterated so a field of floating returns peels inward; **persistent**
  returns (class 22 within 0.3 m in the cell: the other flight saw it)
  are never flagged — that is what keeps the islet; **void**: a water
  cell (W defined, 5×5 floor at the water or empty) with no kept return
  within 3×3 gets no surface, so nothing bridges it, and the enclosed
  fill then fills it from a rim at the water level (`fill_mask` 2).
  `water_mask.tif` and `water_level.tif` are written per unit.
- `admit_low_tide` RULE 2 takes W: class-22 returns are admitted only
  at or below W + 0.3 — the other tide stays excluded (F).
- **A ceiling under the solver** (`sitekit.return_ceiling`): ground is
  never above a return in its own cell (+0.3), nor 2 m above the 5×5
  floor over a return-free cell; the mirror of the 2026-09-26 floor
  (D).
- `mosaic.py` mosaics the three new rasters beside the DTM (units from
  before read as 0 / nodata).
- What is **not** filtered, by design: dense objects over water whose
  own cells show no water — boats and docks (Seldovia harbour) stay,
  as a structure would; rocks under ~4 m in open water are lost unless
  the other flight saw them; the one cell straddling a steep bank
  loses its upper returns.

**Observe stage**: `observe_points.py classify` takes `--struct`,
`--water`, `--level` — built from the reclass's structure mask, water
where its DTM sits at its water level — so the composite's classes come
from our chain, with the vendor's class 6/9 counts as the fallback.

Verified on the seven test units (before/after against `full/out`, the
pictures in the session scratchpad): A, C, D, F fixed; E's dome gone and
the boat left as a bump; Seldovia's roofs planed under (struct mask 4 %
of the unit); 0.1–1 % of each unit changed, nearly all of it lowered and
in water cells. **Hig, 2026-10-10 13:10: rerun the whole survey.** Every
old unit output is in `full/out_v1/` (never deleted); `fullrun.py
--workers 3` with `FULLRUN_FIRST="595 6602 606 6610"` (a new ordering
hook: those units first) is running, log `full/run_v2.log`, resumable
as before. Then `mosaic.py --bbox 595 6602 606 6610 --out
full/mosaic_kachemak` for the composite (a waiter does it), and the
whole-survey `mosaic.py` + republish of `kbay_2023_reclass` on the
hosting side when all 1,350 are done. The repo copy of the model under
`tools/lidar/kbay_reclass/model/` is stale until hosting syncs it.

**Composite rebuilt on the new reclass (2026-10-10, evening).** The
box mosaic (`full/mosaic_kachemak/`: 74 units, 9 enclosed holes — the
lagoons — filled from their water-level rims, 3.1 km²) is the KBay
layer's source until the republish (recipe `source` is its path, with
a note to switch back to the id). Observe rules, both surveys: a fill
or a return-free DTM cell that sits flat at the water level is classed
**water**, not void — it is the water surface, a stand-in the bed
outranks (KBay from the reclass's `water_mask`/`water_level`; Grewingk
from DTM flatness, since DGGS hydroflattened the lake and the laser saw
13 % of its cells). KBay now 94.2 % ground / 5.7 % water / 0.1 % built;
Grewingk 91.0 / 9.0. Composite: bathy 25.7, Grewingk 30.2, KBay 30.2,
none 1.9 km²; no fallback cells; water-class overlap 0.84 km² with
KBay − Grewingk **+0.97 m** (the lake's level, Aug 2023 vs Oct 2021);
ground overlap +0.15 m (canopy residual on top of the 0.36 m shift).
Previews in `out/preview/`.

**Water is not a surface (Hig, 2026-10-10, night).** "A concept we want
in the first version is that water is not a real surface — it's
topobathy after all. Eventually we'll want a way of also generating a
water surface, but that would be more in line with glacier surface
evolution." So in `ground_current` a water observation never stands in
for the bed (the `water: 0.5` gate entries are gone), and both folds
lost their *last resort* (the newest observation of any class, flagged,
where nothing compatible existed — written for exactly this case). Where
only water was seen the composite is a hole whose class is `water`: bed
unknown, the place the bathymetry interpolation fills next. Kachemak now:
bathy 25.6, Grewingk 28.6, KBay 29.3, **none 4.4 km²** (was 1.9), no
fallback cells; the lake, the lagoon interiors between sounding lines,
the tidal channels and the ponds are the holes. A water-surface product
(level per water body per date, from the water class) is a later, separate
layer.

### Hole interpolation: a context-aware first guess (2026-10-10, night)

Hig: "where we're aiming here is a context-aware first-guess ... first
create a synthetic DEM for the interpolation — in many cases it might be
a simple smooth interpolation, but for shorelines we want a shore-like
transition: smooth shore-parallel contours where there are beaches, and
steep cliffs extending down to low-tide where there is bedrock."

`topobathy/fill.py` + `composite.py fill <recipe>` (module docstring is
the specification; recipe block `fill`, every parameter overridable
with `--param k=v`):

1. **Domain** — the composite's holes, closed over the sounding lines
   that cross them (60 m, done as two distance transforms — a
   structuring element that size on 88 M cells never finished), so a
   lagoon is one domain with its lines as observations *inside* it.
   Holes touching the grid edge are margin and stay empty (1.34 km²
   here, mostly the bay edge and the lake in the corner).
2. **Rim character** — each land-rim cell's slope over the 10 m behind
   it (p10/50/90 here 1.8 / 8.7 / 36°): beach or cliff is that number.
3. **Prior, the synthetic DEM** — nearest-rim extension z = z_rim −
   tan(slope)·d along distance-to-shore (contours are iso-distance, so
   shore-parallel), smoothed with a distance-growing kernel, down to a
   **floor**: the low-tide level (`low_tide_m`, −2.9 here = the lowest
   sea surface the KBay water class shows, standing in for MLLW) for
   marine domains (land-rim median < 6 m), rim − `lake_depth_m` for
   lakes. That band is fixed; the interior is harmonic between the
   band's inner edge and any bed rim, *with the sounding cells as
   unknowns* — the prior ignores soundings by design, and leaving them
   out of the system made pockets ringed by lines boundary-less (the
   solve wandered to +30 m before that was found).
4. **Residual** — r = sounding − prior at sounding cells, 0 on the rim,
   harmonic between; fill = prior + r. Soundings are honoured exactly.
5. **Sigma** — grows from the rim (1 m + 2 m per 100 m, + 5 m for
   lakes) and relaxes toward 0.3 m within ~30 m of a sounding.

Solver: geometric multilevel — direct at 16 m, conjugate gradients at
8/4/2/1 m warm-started from the level above; 2 M unknowns in 8 s,
harmonic to machine precision (a cold CG at 1 m sat at its mean after
300 iterations). Kachemak: 652 domains, 3.08 km² filled in 2.5 min
(band 0.64, interior 2.44, 2.85 conditioned), σ p50 0.95 m. What it
looks like: Halibut Cove Lagoon's −60 m basin between the lines with
the beach band grading off the west shore and a steep face off the east
rock; the Peterson Bay lagoon as a bowl from its beach rims. Outputs
`out/fill.tif`, `fill_prior.tif`, `fill_mask.tif` (1 band, 2 interior,
3 conditioned, 4 margin left), `fill_sigma.tif`, `<id>_filled.tif`
(COG), `fill.json`.

Open: the sounding lines' own gridded width still reads as a faint
texture in the filled hillshade; roughness is not yet used to separate
rock from beach (slope does it here); lakes get a guessed floor; the
filled surface is a sibling product, not yet a layer in the gate (it
could enter as class `modelled` with `fill_sigma` as its σ).

**The interpolation is a layer (2026-10-10, late).** Hig: "there should
be a layer representing the interpolations" — and the black on the
composite hillshade was the holes themselves. The recipe now carries
`{"name": "fill", "optional": true, "source": "$out/fill.tif", "class":
"modelled", "sigma": [{"raster": {"path": "$out/fill_sigma.tif"}}]}`:
`$out/` resolves to the composite's out_dir, an optional layer is
skipped (with a note) until its file exists, and `composite.py fill`
treats the cells the fill layer won as holes again, so build → fill →
build is idempotent. Two gate changes were needed: a `modelled`
observation never sets `class_now` (it is derived, not an observation
of what is there — with it on the class axis the fill blocked itself,
since the table has no `modelled` row), and a `void` row admits
`modelled` at half rank (where the laser got no returns at all, only a
modelled surface may stand in). Kachemak: fill 1.63 km², bathy's own
hull TIN 26.7 (it outranks the fill between the lines, gate 1 vs 0.5),
holes 1.70 km² of which 1.34 is margin. The mask tool shows `fill` as a
layer like any other, plus the two fill views. Open question: between
sounding lines the bathy layer's linear TIN beats the context-aware
fill because it is class `bed`; whether that is the right ranking is
Hig's call (the fill could be given gate 1 under `water`/`void` where
the bed observation's σ is the hull's).

### Default lake-basin guess: the literature (2026-10-10, late)

Hig: "one thing we should start building is a default lake-basin
guess — research the literature on lake basin geometry inference."
What exists, and what it gives us:

- **Hollister, Milstead & Urrutia 2011** (PLoS ONE 6:e25764; R package
  `lakemorpho`): depth at a point = distance from shore × median slope
  of the surrounding topography, in a buffer as wide as the lake's
  greatest in-lake distance to shore; max depth = the farthest point.
  Raw predictions run high — the fitted corrections are **0.46–0.55**
  (basins are shallower than their shores extrapolated, by erosion and
  sedimentation) — and after correction RMSE 5–6 m, r 0.7–0.8,
  explaining 50–60 % of max-depth variance over ~28,000 NE-US lakes.
  This is exactly our shore-band extension, with the factor.
- **Sobek, Nisell & Fölster 2011** (Can. J. Fish. Aquat. Sci.): 6,943
  Swedish lakes; lake area and the **maximum slope in a 50 m zone
  outside the shoreline** are the best predictors — 92 % of volume
  variance but < 40 % of max or mean depth; regional sub-models alike,
  "probably applicable for glacially formed lakes in general".
- **Heathcote et al. 2015**: elevation change in a buffer scaled by lake
  area explains 95 % of volume variance for 433 Quebec (glacial) lakes.
- **Messager et al. 2016** (HydroLAKES, Nat. Commun.): a geostatistical
  volume model from surrounding terrain for every lake > 10 ha.
- **Khazaei et al. 2022** (GLOBathy, Sci. Data): max depth by random
  forest on shoreline length, area, volume, watershed area and
  elevation (test NSE 0.86, n 1,503); the bathymetry itself is
  **D = l·Dmax / L** — depth linear in distance to shore, Dmax at the
  farthest point (a cone, Hollister's shape); head–area–volume as power
  laws A = a·hᵇ, V = c·hᵈ.
- **Håkanson 1981** (A Manual of Lake Morphometry): basin shape in one
  number, the **volume development Vd = z̄/z_max** (a cone is 1/3); a
  hypsographic form parameter Hd fits area–depth curves to r² ≈ 0.99.
  For a basin whose depth goes as (l/L)ᵖ, Vd = 2/((p+1)(p+2)): p = 1 is
  the cone, p ≈ 0.56 gives Vd = 0.5 (flatter-bottomed, the glacial
  norm), p → 0 a box.
- **Cook & Quincey 2015** (Earth Surf. Dynam. 3:559): Alpine glacial
  lakes — volume–area r² 0.91 (power law, Huggel et al. 2002's
  V = 0.104·A^1.42 is the usual form), depth–area only r² 0.38; the
  outliers are overdeepened, complex basins — our case.
- **Haeberli et al. 2016** (ESPL 41:1980): overdeepenings sit where
  glacier surface slope was < 5–10°, are hundreds of metres long, about
  half as wide, tens of metres deep, with the adverse slope on the
  down-flow side.

**Design that falls out of it**, as the lake branch of `fill.py` (not
built yet): (1) **Dmax** two ways — Hollister: (max distance to shore)
× (median rim slope) × ~0.5; glacial scaling: mean depth from area
(Huggel/Cook & Quincey), Dmax ≈ mean / Vd — and the spread between
them is the depth σ; (2) **shape**: depth = Dmax·(l/L)ᵖ with p from a
target Vd (0.5 default for glacial basins, 1 for a cone), Dmax at the
farthest-from-shore point — or, where the rim says which way the ice
came (highest rim side = headwall), shifted toward it, per Haeberli;
(3) **shores** keep our band: the deeper of the rim-slope extension
(capped at Dmax) and the shape model, so rock drops steeply and beaches
grade; (4) soundings condition it exactly as in marine domains; (5) σ
of order 5 m plus the Dmax spread — honest about how little the shore
knows. The Halibut Cove lake lies in the box's corner (a margin hole);
testing it needs a window that encloses it.

**Continuous coverage, and the lake branch roughed out (2026-10-11).**
Hig: "our target should be entirely continuous coverage"; some holes
"look more like a bug". They were three bugs: (1) the `rules_post`
despeckle/feather applied to the fill's take (ponds < 2000 m² and
channels narrower than the feather vanished) — a layer may now set its
own `rules_post`, the fill's is `[]`; (2) domains touching the box edge
were "margin" and never filled — `fill_margin` (default true) fills
them, the grid edge a no-flux boundary; (3) two surveys' takes over a
pond were complementary speckle, both despeckled, and the hole admitted
no fill under a `ground` class-now — post-processing may now never
leave a cell uncovered that the rules had covered (it reverts to the
rules' winner). Also the `void` gate row admits `bed` (the bathy's own
hull TIN is still the best where the newest survey saw nothing; without
it the fill displaced the hull between passes and the hole set never
converged). Kachemak: **fill 4.35 km², none 0.00 km²**, stable under
fill → build → fill → build.

Lake branch (`fill.py`, rough for review): per lake domain ≥ 2000 m²,
L (greatest distance to shore), A, median rim slope; Dmax = geometric
mean of Hollister's 0.5·L·tan(s) and the glacial scaling (Huggel
V = 0.104·A^1.42 → mean depth ÷ Vd 0.5); shape Dmax·(l/L)^p with
p from Vd (0.56); shores keep the band (deeper of band and shape);
σ = half the Dmax spread + 5 m, scaled by the shape; observations inside
a lake within 1 m of the water level are dropped as evidence (a boat
track or seam of returns off calm water, classed ground, had pulled the
fill up to the surface), and islets of such cells < `island_max_m2`
are swallowed into the domain (each had been a rim throwing a cone).
**Sparse point depths**: `fill.points` = a CSV with x,y and z (absolute)
or depth (below the lake's rim level) — they condition the prior exactly
like soundings. `out/fill_lakes.json` is the per-lake table. Halibut
Cove lake: A 0.89 km² (cut by the box edge), rim 5.4°, Dmax Hollister
25 / glacial 66 → 41 m; the south lake at 601301,6602221 (also cut):
rim 34.5°, Hollister 144 / glacial 47 → 82 m — Hollister's factor is
not a cap, and the geometric mean only tempers it. Both big lakes are
truncated by the box; a review area that encloses whole lakes (extend
NE and S, or a lake-rich box elsewhere in the Grewingk footprint, at
2 m) is the next step, with a few hand depths in the CSV to exercise
the conditioning.

### Deferred: physical awareness (decided 2026-10-09)

Hig asked how far to go toward a physically aware merge — glacier
thinning rates from overlapping DEMs of different age to put a glacier
at one epoch; seasonal water levels. Decision: **not now, and nothing
needs to change in the engine for later.**

- Thinning is a **derived layer** (shift an ice observation to `as_of` by
  a rate raster, σ growing with the gap) — the slot already exists
  (`plane_shift`, "local dh/dt from the glacier apps" reserved). Current
  behaviour on ice — newest observation wins, a feathered step at each
  seam of ~rate × age gap — is an honest picture of what the data knows,
  and `date_now.tif` makes it legible. The trigger to build the layer is
  a product that fails without it (the ice-dam flotation analysis, where
  a 10 m surface step is ~9 m of water level); the first implementation
  should use a **published** rate field (Hugonnet et al. 2021 100 m dh/dt
  grids, 2000–2019, include Alaska — not verified in session) or a dated
  ArcticDEM strip, not our own estimator. Self-estimating from overlaps
  mixes thinning with season, snow, IfSAR penetration and co-registration,
  and needs rates where there is no overlap — a geodetic mass-balance
  pipeline. `report.json`'s `rate_m_per_yr` tells whether the seam step
  at a site is 2 m or 30 m before anything is modelled.
- Seasonal water: each survey's water is detected at its own level and a
  ground observation outranks a water one, so the lowest-water survey
  already wins the shore band. What remains is one product level per
  waterbody (to flatten stand-in water) and the tide level at sea sites —
  recipe scalars, not a model.

(Previous state of play, 2026-09-20/21, for the record:) This document
is the agreed design, reviewed by Hig over four drafts on 2026-09-20;
the decisions below are his, not proposals. **Revision 2** (next
section) and the dated sections at the end supersede the original where
they conflict; the "Revised order of work" in Revision 2 is current. The
Portage regression (its step 1) is done and bit-exact. The sparse-point
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
   `report.json`. **Done 2026-10-09 except `datasets.json` sources and the
   snapped grid**, which belong to the first non-Portage recipe (Lituya).
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
