# Reviewed water polygons and flat water — plan (2026-10-28)

Builds on [WATER_POLYGONS_PLAN.md](WATER_POLYGONS_PLAN.md), which covers the tables, the tide-stage stack, the
editing tools and the rocks. This plan adds two things. The first is the **order of work**: automatic, then human
review, then final. The second is the **flat-water rule**. Nothing here has been run yet.

## Hig's ask (review of runs 20g / 20h, 2026-10-28)

> I wonder if the right answer here for upper Grewingk face is to lean a bit on human interpretation - this should be
> resolvable by refining the water polygon, with the algorithm recognizing that areas within a water polygon with no
> bathy data should be treated as open space for interpolation. If the algorithm identifies above-water points that
> may be real, it should flag these in a way that facilitates human review. [...] the water surface should be flat.
> The rivers need to be thought of slightly differently - we want to resolve topography on those - but it shouldn't
> be hard to get rid of topography on the ocean.

The flow he outlined:

1. Water polygons identified from mapping and the point clouds.
2. Fully automatic topobathy, treating those polygons as priors nobody has checked.
3. Human review of the polygons, inviting revisions.
4. Final topobathy.

## What the four spots in 20h actually are (read from out_run20h2/, 2026-10-28)

| Spot | What's there | Mechanism |
|---|---|---|
| 59.60383, −151.06424 (face) | Bed 48.81 m under a 48.91 m lake: a flat shelf 10 cm deep. Unobserved (fill, conditioned); inside water body 136, kind `river` | The soundings spline overshot above the water, and the rule that holds a bed below its water **clipped** it to level − 0.1. The clip is what makes the flat shelf. |
| 59.60013, −151.06323 (face) | The same: every cell in a 50 m box at 48.81 m | The same clip |
| 59.60118, −151.06276 (face) | Labelled **floating** (3) with a level of 50.87 m, 2 m above the lake it floats on; bed beneath 29–50 m | The floating ice kept its own surface as the "water level". Rule 5 gave the lake's level to water cells but not to floating ones. |
| 59.63493, −151.21814 (bay) | The bed is the multibeam at −53 m. The water is split into "tidal" bodies at 0.53–0.67 m and three **"perched"** bodies at 2.75 m (one survey's level each, 7–10 ha) | A single survey's water surface on the open sea was classed *perched*, which means a level held above its surroundings. `water20.sh` keeps a perched body's own level inside the sea mask. So at low tide those patches stand 4 m above the flooded sea, and that is the "ocean-surface topography". |

Two rules cause these, and neither is specific to Grewingk:

1. **A constraint applied after the fit instead of inside it.** Clipping an overshooting surface makes a plateau
   exactly at the constraint, which is a false platform. The fix belongs in the solve.
2. **Water levels that aren't the sea's or a lake's.** A flight's water surface on the sea was treated as a body
   with its own level, and floating ice as a level of its own. In Hig's terms, the sea is one flat surface at a
   stand of the tide. A lake is one flat surface at its level. Ice floats *on* a body's level.

## 1. The flat-water rule

**Every water surface is flat except a river's.** Flat means one of two things:

- **The sea** has no level of its own. Its surface is the tide stand being displayed or modelled. What each flight
  saw (the water surface at the moment of acquisition) is **evidence about the shoreline and the bed** at that
  stand: the bed is below it where wet, and at it along the waterline. It is never a surface to draw.
- **A lake, pond or lagoon** holds one level, from its trusted polygon, else the consensus of the surveys.

**A river** has a surface that slopes monotonically downstream, and its bed is resolved as topography (the channel
model, the soundings). It is the only water allowed a non-flat surface. It is a river because a reviewed polygon
says `river`, or because the automatic stage finds a drop along a channel-shaped body. It can never be a river just
because a body's levels disagree.

What falls out of this:

- **Perched requires a sill.** A body can hold a level above the sea only if its outlet (the lowest point on its
  rim) stands at or above that level. A body joined to the sea through water whose bed lies below the lowest tide is
  the sea, whatever level one survey gave it. This is a test on the bed and the outlet, not on the level, so it
  can't be tricked by a flight tide. All three 2.75 m "perched" bodies in the bay sit over −53 m multibeam with no
  sill; under this test they are the sea.
- **Floating ice takes the level of the body it floats on.** Its own surface is the ice, not the water. The bed under
  it is the body's bed.
- **The sea's surface is not carried in the water-surface product.** The sea mask is flooded to the viewer's tide
  everywhere, and only lakes, ponds and rivers keep a level (`water20.sh --keep` loses `perched` unless the sill
  test passed).
- **The topobathy is still continuous.** The water laps onto the real bed, so there is no hydro-flattened cliff at
  the margin, and good below-water returns are kept (Hig's point about why this beats a hydro-flattened DTM). A flat
  surface never edits the bed. It only bounds it: bed below level inside, at level along the shoreline.

## 2. Inside a water polygon: open space for interpolation

Inside a water polygon the bed is **unknown wherever nothing measured it**. Those cells are open space for the
interpolation, which uses three kinds of information:

- **Data:** soundings, multibeam, green-lidar bed, ground returns *below* the level.
- **Edges:** the shoreline at the level (bed = level ± σ), except an ice face, which is free (no boundary
  condition: the bed runs under the ice).
- **Shape:** the depth prior (hypsometry, the shore-gradient profile), as now.

Two changes from the current fill:

1. **The ceiling is a constraint in the solve, not a clip afterwards.** "Bed < level − ε" joins the existing
   active-set pass, which today takes the lead-line lower bounds. Cells where the fitted surface violates the
   ceiling become points at the ceiling, and the domain is refitted. The surface bends down to the constraint
   instead of being cut flat at it.
2. **The spline's correction decays away from data.** A thin-plate spline extrapolates without bound: the face
   correction ran to +45 m with soundings hundreds of metres away. The correction should fade to zero beyond a few
   correlation lengths of the nearest sounding (a tension term, or the residual blended by distance), leaving the
   depth prior to stand there. This is why the face is so sensitive. The prior is reasonable there; the correction
   is noise far from its data.

Then above-water observations inside the polygon need deciding. Today they're kept if two surveys agree, voided if
water-blind, or turned into the lake surface if within σ_w of the level. Under review they become **flags**.

## 3. Flags for review

The automatic run writes `out/water_flags.geojson`. Each feature is one cluster of cells, with a kind, its numbers
and a suggested disposition. Shown on `/lidar/` dev as a layer that a click opens.

| Kind | Trigger | Shown with | Suggested |
|---|---|---|---|
| `above_water_obs` | ≥ N observed cells inside a water polygon more than 2σ_w above its level | Height over the level; area; surveys and dates; the class (ground / ice / SfM); whether a second survey agrees | Island or rock if two surveys agree, the shape is compact and it's under ~5 m; otherwise noise (SfM over water, ice since calved) |
| `ceiling_bound` | The fit wanted the bed above the water over an area (cells the active set had to hold) | How far over and the area | Usually the polygon is too big there (it's land), or the prior is shallow |
| `level_conflict` | Surveys' levels for one body disagree by more than their σ, or a body's level and its polygon's disagree | Each survey's level and date | Pick one, or split the body |
| `sill_fail` | A body asked to be perched or a lake with no sill above its level | Its outlet height | Sea, or a missing sill (the polygon's edge is wrong) |
| `edge_mismatch` | The polygon's edge and the data's waterline disagree by more than X m along a stretch | The distance and the side | Move the edge |
| `ice_face` | Where the water polygon meets glacier | The face line, the 2021/2023 positions | Confirm the face line (calving moves it) |

Each flag carries a stable id (hash of kind + rounded centroid) so a disposition survives the next run if the
cluster is still there.

**Dispositions** live with the polygons, as `water/review.geojson` beside `water/polygons.geojson` (the repo-file
pattern of WATER_POLYGONS_PLAN.md §2):

- `island`: the observation stays; the cells are land, cut from the polygon.
- `noise`: the observation is void inside the polygon; open space.
- `polygon_edit`: the reviewer redrew the polygon instead; the flag is resolved by the redraw.
- `defer`: left for later; the automatic choice stands.

Unreviewed flags take the suggested disposition so the automatic run is always complete.

## 4. The four stages, as runs

| Stage | Inputs | Outputs | Who |
|---|---|---|---|
| **W1 polygons** | Mapped lakes (Hig's layer, NHD, 3DEP hydro-flattened areas), make_ice's 2021 lake / 2023 retreat stamps, the water stage's bodies from the point clouds | `water/polygons_auto.geojson`: each polygon with kind (sea / lake / pond / lagoon / river), level and σ, basis, `status: auto` | The chain |
| **W2 automatic topobathy** | W1 as priors | The current chain, with §1–2's rules, plus `water_flags.geojson` | The chain |
| **W3 review** | W2's surface, flags and polygons on `/lidar/` dev | `water/polygons.geojson` (edits: redraws, kind or level changes, `status: reviewed`) and `water/review.geojson` (dispositions) | Hig |
| **W4 final** | The reviewed polygons as **trusted** (rule 5: the polygon decides the label; its level is the level), the dispositions | The published surface and water surface. The flags remaining are only unreviewed ones, listed in the run's notes | The chain |

What changes between a W2 polygon and a W4 polygon is only the trust. An `auto` polygon is a prior: the cut may
move its edge where the data disagree, and a flag is raised. A `reviewed` polygon is a constraint: the edge holds,
and data inside above the level is island or noise per the disposition. This is the existing `prior_cost` /
trusted-polygon distinction in `shore.py`, extended to every polygon.

**Grewingk's upper face** is the first W3 test. One reviewed polygon for the lake up to the 2023 face, plus
dispositions on the few above-water clusters there. That should settle the platforms and the floating patch
without another rule.

## 5. Implementation, in order

1. **Flat water** (`shore.py`, `water.py`, `water20.sh`, `water_surface.py`):
   - Remove sea-level bodies: anything joined to the sea that fails the sill test is the sea.
   - Floating ice takes its host body's level.
   - `--keep` only for lakes, ponds, lagoons and rivers that pass the sill test.
2. **Ceiling in the active set and the decaying spline correction** (`fill.py`): replaces the clip, which stays
   only as a last-resort guard, logged as a flag when it fires.
3. **Flags** (`topobathy/water_flags.py`, a chain step after the fill): the table in §3; each kind's thresholds in
   the recipe.
4. **Polygon files and trust** (`water/polygons_auto.geojson` from W1, `water/polygons.geojson` +
   `review.geojson` read by `shore.py` and `fill.py`).
5. **Review in the viewer**: phase 1 (show) of WATER_POLYGONS_PLAN.md, plus the flags layer. Editing can start as
   editing the GeoJSON in QGIS against the published dev surface, before the `/lidar/` tools exist. The format is
   the same either way.

Items 1–2 are rule changes that can go in the next run with no review step. Items 3–4 make W2/W3 possible. Item 5
is the comfortable version of W3.

## 6. Gate rows

Add:

- **Sea level spread**: p99 − p1 of the water-surface product over sea cells at a fixed tide. Should be 0.
- **Perched over deep bed**: area of bodies holding a level with bed below the lowest tide and no sill. 0.
- **Flat shelves**: wet unobserved cells with depth < 0.2 m in a lake more than 5 m deep. The clip's signature,
  which `face_shallow_frac` only partly sees.
- **Floating level mismatch**: floating cells whose level differs from the host body's. 0.

Replace `face_bed_p1_m` (a remembered model depth) with **misfit to the soundings in the face window**. 20h2 was
better than 20g on it (deepest four: 20g −4.0/+4.9/−3.0/+35.5 m, 20h2 −1.6/+1.8/+1.9/+7.6).

## 7. Hig's answers (2026-10-28)

- **Flags:** anything over **100 m² or 1 m** above the level in a first pass; tune by use.
- **Review medium:** a file to look at in QGIS first, before building the `/lidar/` tool.
- **The sea's default tide:** MLLW.
- **Rivers:** keep proposing them automatically ("most of our polygons are outdated"), and where a mapped polygon
  modestly agrees with the lidar, take the polygon as a prior.
- **Lakes can hold more than one level:** "when there are multiple lidar surveys one might catch the water lower and
  thus capture more data. It may be that lakes should be associated with one or more levels -- one by default if no
  data is available to find more."

## 8. What is built (2026-10-28, uncommitted; run 20i is its first full run)

| Piece | Where |
|---|---|
| Sill test: a perched body (or an unmapped lake under tide_max) joined to the open sea through ground or bed below MLLW is re-kinded tidal. Tested on 20h2's products: 33 bodies, 174.5 ha, including the bay's three (24, 30, 64); the 28 kept have sills at or above their level | `shore.sill_test` |
| The shoreline stage's corrections reach every consumer: `shore_bodies.json` (overrides + sills), applied by `water.load_bodies` (the stage itself reads the raw bodies) | `shore.run`, `water.load_bodies`, `water20i.sh` |
| Floating cells' levels cleared before routing: ice takes the level of the water it floats on | `shore.run` |
| Ceiling in the fit: where the fitted bed would stand within 0.1 m of its water (and over 10 m from the domain's edge), sites at level − 0.5 m join the soundings and the domain is refitted, two passes; the clip stays as a guard. Both recorded in `fill_ceiling.tif` (1 fit, 2 guard) | `fill.fill_holes`, `composite.cmd_fill` |
| The soundings' correction fades beyond 150 m of its nearest data site, Gaussian over 150 m (`points_decay_m`, `points_decay_scale_m`) | `fill._spline_residual` |
| Flags and automatic polygons, `out/water_flags.geojson` + `out/polygons_auto.geojson` (EPSG:4326). The sea takes no flags (it has no level). On 20h2: 13 above-water, 287 edge mismatches, 1 ice face, 430 polygons (113 agree with a mapped polygon at IoU ≥ 0.5); 20 s | `topobathy/water_review.py` |
| Reviewed polygons and dispositions: two new sources in `water/sources.json`. `polygons.geojson`: only `status: reviewed` features are read, kind lake / river / sea / island, optional `level_m`; it replaces the hig_lakes polygons at least half under it. `review.geojson`: `disposition` island → an island, noise → no observation inside the water (shore voids it; the fill reads `water_blind.tif`) | `water.load_mapped`, `shore.run`, `composite.cmd_fill` |
| Gate rows: `perched_over_deep_ha` (20g/20h2: 174), `clip_shelf_cells` (40k / 46k), `floating_level_mismatch_cells` (135 / 589), `face_sounding_misfit_p90_m` (3.43 / 3.25 m) replacing `face_bed_p1_m` | `topobathy/gate.py` |

**Not yet:**

- **Multiple levels per lake.** The water stage sees the fused `joint` layer as one survey, so 2021 and 2023 can't
  disagree there yet (0 level conflicts on 20h2). Per-survey levels need the joint stage's sources (gk2021, kbay2023,
  gk2019) read separately by the water stage. Until then a reviewed polygon's `level_m` sets the one level.
- **Demoting outdated mapped polygons automatically.** They keep their current trust and are flagged instead
  (`edge_mismatch`). The automatic polygons carry `agree` (IoU with the run's water) so a later rule can treat the
  low ones as priors only.
- **Rivers from mapped river polygons as priors** (Hig's last answer): not yet used by the water stage's kinds.
- **A sea-level-spread row** on the water-surface product (the gate runs before the water surface is built).

## 9. Reviewing in QGIS (W3)

1. Load the run's `out/joint_v3.tif` (hillshade), `out/water_flags.geojson` and `out/polygons_auto.geojson`.
2. **Flags:** fill in `disposition` (island / noise / polygon_edit / defer) on the ones that need a decision; save the
   layer as `tools/lidar/water/review.geojson`. A flag's `id` is stable while the thing is still there, so a later
   run's flags can be joined to it.
3. **Polygons:** copy the features to fix into `tools/lidar/water/polygons.geojson`, redraw, set `kind` (lake / river /
   sea / island), optionally `level_m`, and `status` = `reviewed`. Unreviewed copies are ignored.
4. The next run (W4) reads both. Start it at the water stage when polygons changed (bodies and kinds come from the
   polygons), at build 2 when only dispositions changed.
