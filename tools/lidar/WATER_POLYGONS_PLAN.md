# Water polygons and hazard rocks on `/lidar/` — proposal (2026-10-23)

> **2026-10-24, stage C.** `topobathy/shore.py` now produces the object this plan edits: `out/shoreline.geojson` (one
> line per ring of the water outline, with body, kind, level, bank-slope statistics, confidence and the slope at every
> vertex), `shore_label.tif` (land / water / floating / glacier) and `shore_level.tif`. A hand edit to a shoreline is a
> prior for the next cut (the same term the trusted mapped polygons use, `prior_cost`), never a stamp; a rock point is a
> floor for the fill. See TOPOBATHY_PLAN.md "The shoreline stage (stage C)".

Hig's ask: expose the water bodies as polygons in the lidar viewer for review and editing; the ocean as a
stack of polygons at ~0.5 m vertical spacing, edited under the rule that a lower stage can never be more
extensive than a higher one (no overhung cliffs); two purposes -- read the shoreline off the lidar once a
section's tide level is known, and correct interpolated shorelines from orthoimagery, above all at low tide.
Beside it a navigational-hazard rock tool: points at least as high as a -6 ft tide (6 ft under MLLW), some
imaged by lidar or multibeam, others only known from field mapping and imagery (Hig's `Rocks` shapefile,
1,185 points; NOAA's chart features). Both feed constraints back into the topobathy and come out as vector
objects for map illustration.

## 1. One idea underneath both tools: a polygon with a level is an inequality on the bed

Every water polygon the tools handle carries a **level** (NAVD88 m) and says three things about the bed:

| where | statement | σ |
|---|---|---|
| inside | bed < level (wet at that stage) | an upper bound |
| the boundary | bed = level (the shoreline at that stage) | from the basis (below) |
| outside, within a margin | bed ≥ level (dry at that stage) | a lower bound |

The ocean stack is then nothing special: stage k is the polygon "wet at level z_k", and **nesting is
automatic** because the stages are contours of one single-valued surface. The no-overhang rule becomes a
constructive edit rule rather than a check: a patch that says *land at z* holds at every stage at or below z
(dry at a higher tide ⇒ dry at every lower one); a patch that says *water at z* holds at every stage at or
above z. One stored patch, the whole stack consistent.

A rock is the point form of the same thing: bed ≥ z_lo at the point (a **floor**), z_lo = -6 ft under MLLW
by default (-1.83 m; at the Seldovia datums in the catalogue, MLLW -1.38 → **-3.21 m NAVD88**), or the
measured height where lidar or the multibeam imaged it.

The fill already consumes points with sigma, *lower bounds* in the lead-line sense (bed at or BELOW z, the
active-set pass), lake levels (bed ceiling), a hand-set *floor* (bed held at or above it), the tidal lens
and the rim. The missing piece is one general **interval raster** `[lo, hi]` on the bed built from every
polygon and point, with its sigmas, consumed in the same active-set pass in both senses. That single addition
is the constraint pathway for both tools (section 5).

## 2. Objects and storage

Follow the landform-trace pattern (`inventory/scarps.py`, PostGIS tables from an idempotent management
command, writes editor-only, Terra Draw in the page; the session/handle/undo mechanics of `scarps.js` carry
over). Two tables:

**`water_polygons`** -- `id`, `kind` (`sea` | `lake` | `pond` | `river` | `lagoon`), `op` (`water` | `land`;
for lakes/ponds `water` with a level is the body itself), `level_m` (NAVD88), `level_sigma_m`, `basis`
(`derived` | `lidar_waterline` | `ortho` | `field` | `chart` | `hand`), `basis_ref` (survey id and GPS time,
image id and date, note), `who`, `created_at`/`updated_at`, `status` (`active` | `withdrawn`, with `why` --
the `edits.geojson` convention, never delete), `geom` MultiPolygon 4326. The water stage's existing
`edits.geojson` (Point/Polygon with kind / level_m / dmax_m / why / withdrawn) is the same thing by hand;
the export (below) writes the table in that format plus the new fields, so `water.py` and `fill.py` read one
file whether the edit was typed or drawn.

**`hazard_rocks`** (a PRIOR, uncertain until the data or a systematic pass confirm each one -- Hig 2026-10-23:
the mapping "was a bit unsystematic, and we should work towards consistency"; the shapefile's `Type` is kept as
`source`, never as the classification) -- `id`, `source` (`hig_seen` | `hig_chart` | `hig_sub` | `hig_data` | `hig_deep` |
`noaa_enc` | `noaa_awois` | `hand`), `source_id`, `z_m` (measured height, when the data show it), `z_lo_m`
(the floor; default -3.21), `z_hi_m` (optional: a "covers and uncovers" rock is ≤ MHHW), `pos_sigma_m`
(5 m for a GPS'd field point, 15-30 m for a chart point), `r_m` (the footprint radius the floor applies
over, default 4 m), `resolution` (`imaged` | `constraint` | `rejected` | `open`), `note`, `geom` Point.
Hig's shapefile loads once (`load_hazard_rocks`), its `Type` → `source`; only two points carry a note ("-7.8
feet in Hydropalooza", "-5.8 feet in Hydropalooza", two `Data` rocks at 585339,6593805 and 585406,6593877 --
depths read off the 2009 NOAA multibeam, in feet below MLLW): they load as `z_m` with a 0.5 m sigma and the
note kept, not as fixed truth (Hig: not sacred). The one `Flag` ("Bing image - uncertain, check", a `Seen` rock
at 608235,6571743) loads as `resolution = open`. NOAA ENC `UWTROC`/`OBSTRN` features (ENC Direct to GIS;
`WATLEV` 3 submerged / 4 covers and uncovers / 5 awash, `VALSOU` depth below MLLW) map to bounds the same way.

**Export** -- `export_water_edits` writes `tools/lidar/water/polygons.geojson` and `rocks.geojson` (like
`export_soundings` → the CSV the fill reads), so a build is reproducible from the repo alone and the chain
on the Mac never talks to the database. Rocks also go out as rows of the points CSV with `bound=floor`.

## 3. The derived stack (what the viewer shows before anyone edits)

A new chain step after `water_surface.py`, `water_polygons.py`: from the filled bed and the sea mask,
polygonise "bed < z_k and connected to the sea" for stages z_k from **MLLW - 2.0 m to MSL in 0.25 m steps and
MSL to MHHW + 0.5 m in 0.5 m steps** (Hig 2026-10-23: finer near MLLW, where the low-tide shoreline matters;
Seldovia: -3.38 … 1.59 by 0.25 = 20 stages, then 2.09 … 4.62 by 0.5 = 6, plus the named datums MLLW / MLW / MSL /
MHW / MHHW and -6 ft MLLW as stages of their own), plus the
water stage's bodies (`water_bodies.geojson` already carries kind, level, `consensus_rule`, `mapped_name`).
Simplified for display (~1 m), written as GeoJSON per stage and as one vector PMTiles when tippecanoe is
installed (it is not today; `brew install tippecanoe`), attached to the survey's catalogue entry beside
`surfaces` (`vectors: [{kind: 'water_stack'}, {kind: 'bodies'}, {kind: 'rocks'}]`) by `dev_viewer_add.py`.

In the page, **the tide scroll** (Hig: scrolling up and down through the tide levels as the interaction): the
tide becomes a stepped control over the stage list -- mouse wheel over the tide widget, the up/down arrow keys
while it has focus, and the slider itself snapping to stages -- and every step moves the water drape
(`LSWater`, `state.tide`), the stage's shoreline line, the tide-stripe fill below it and the rock classes
together, with the stage's name and height read out (e.g. "MLLW - 0.75 m"). In 3D the water mesh rises and
falls with it, which is the view that shows an intertidal error at once. Hover a body: its level, rule and
edits; hover a rock: its source, bound, resolution. The named datums are keyboard shortcuts (Home = MLLW,
End = MHHW) so a reviewer can flip between the two charts' states.

## 4. Editing

Tool group modes on `/lidar/` (editors only, `ls_tools.js` registration like `trace`):

- **Water patch** -- draw a polygon with `op` water or land at the current stage (Terra Draw polygon mode,
  `pointerDistance: 8`, as in `terra_draw.js`/`revise.js`). The stack recomputes with the no-overhang rule
  (water ⇒ this and every higher stage; land ⇒ this and every lower). Patching, not vertex-dragging a
  60 km contour: a patch is small, has a reason, and re-applies to the next run's derived stack.
- **Body** -- draw or reshape a lake/pond/lagoon with its level (the `water` op of a body kind); set
  `dmax_m`; withdraw with a reason. Hig's lakes layer stays the mapped prior; a body edit overrides it.
- **Known-tide waterline** -- the first purpose. Pick a survey and a stretch of coast; the tool takes the
  lidar waterline there (the joint layer's water/dropout edge against ground, from `water_obs.geojson`) and
  the **tide at acquisition**: the joint observation stage records median GPS time per cell (LAS GPS time is
  in the clouds), the CO-OPS verified record for Seldovia 9455500 gives the water level, and the gauge's
  bench-mark relation converts MLLW to NAVD88 (the route that works, see memory). The waterline becomes a
  `lidar_waterline` polygon at that level with σ ~0.15 m: the strongest shoreline evidence we have, and it
  replaces the fill's guess between the lidar edge and the sonar's shallowest return.
- **Ortho waterline** -- the second purpose. Trace the water's edge on an orthomosaic (the SfM orthos in the
  catalogue, or imagery whose date and time are known); its tide comes from the gauge the same way; σ ~0.3 m
  plus the image's own uncertainty. Low-tide imagery is the prize: it draws the -1 to -2 m zone no lidar sees.
- **Rock** -- place, move, attribute a point; accept a data match or reject.

## 5. Constraint pathways into the topobathy

1. **Interval raster.** `fill.py` builds `lo`/`hi` per cell: from each active polygon (wet ⇒ hi = min(hi,
   level); dry margin ⇒ lo = max(lo, level); boundary cells ⇒ a point with sigma, like a sounding) and each
   rock (lo = max(lo, z_lo) within `r_m`; `z_hi_m` ⇒ hi). The existing active-set pass takes both senses
   (today it takes lead-line bounds only). The sea stack's *derived* polygons carry no constraint (they are
   the product); only edited patches and known-tide waterlines do.
2. **Water stage.** Body edits with a level enter `water.py` as they do today (`edits.geojson`); a `land`
   patch inside a body removes it from the body; a trusted-edge body bounds the lake as the shoreline rule
   built tonight (`fill._mapped_lake_sides`) already does for Hig's lakes.
3. **Rocks resolved by data -- the consistency pass.** `rocks_resolve.py` (chain step) treats every rock the
   same way whatever its source: probe the current bed within `pos_sigma_m` for a local maximum ≥ `z_lo`,
   and read what each survey saw there (lidar ground above the water, a multibeam high, the SfM ortho's
   breaking water, nothing). Found ⇒ `imaged` (position snapped, `z_m` and the surveys recorded, no
   constraint needed); not found ⇒ `constraint` (the floor applied as a bump of radius `r_m` in the fill, σ
   from the source) -- or, where the data cover the spot well and show nothing above the floor (multibeam
   over the whole footprint, or lidar ground below -6 ft), ⇒ `contradicted`, listed for Hig to keep or
   reject. The classification in section 6 is then computed from the bed for every rock alike, so the set
   becomes consistent regardless of how each point was first mapped; the `Type` survives only as provenance.
   Hig's 21 `Data` rocks are the first test: all of them should come back `imaged`, and the two Hydropalooza
   depths should agree with the multibeam within its sigma.
4. **Intertidal shoreline.** A known-tide waterline pins the bed along a line at one level; two waterlines
   at different tides (a 2021 and a 2023 flight, or a low-tide ortho) bracket the foreshore slope, which is
   exactly the number the tidal lens now guesses from the shore gradient.

## 6. Illustration outputs (the same objects, exported)

`export_water_vectors` writes a GeoPackage: the tide stripes (polygon per stage pair), the shoreline at each
named datum, bodies by kind with level, and rocks classed by the final bed height against the datums --
**islet** (z ≥ MHHW), **dries** (MLLW ≤ z < MHHW, covers and uncovers), **awash** (|z - MLLW| < 0.3),
**submerged hazard** (-6 ft ≤ z < MLLW), **deep** (below, not a hazard) -- the chart conventions, from our
own surface.

## 7. Phases

1. **Show** (first): the derived stack, bodies and rocks (Hig's shapefile + ENC) on `/lidar/` dev, tide-
   linked; `rocks_resolve.py` and the rock report; rocks into the fill as floors. Mostly chain + display
   code; no schema yet beyond the rock load. Value immediately: the stack makes the tidal lens and the
   intertidal fill reviewable at every stage, and the rocks give the fill ~1,000 floors it never had.
2. **Edit**: the two tables, the patch tool with the no-overhang propagation, the export, the interval
   raster in the fill.
3. **Known tides**: GPS time per cell in the joint stage, the gauge lookup, the lidar and ortho waterline
   tools.
4. **Illustration export** and the public/gated decision for reads.

## 8. Open questions for Hig

- Stage spacing and range: 0.5 m from -6 ft MLLW to MHHW + 0.5 m, plus the named datums -- or finer near MLLW
  where the low-tide shoreline matters most (0.25 m below MSL)?
- Rock footprint radius (`r_m`, default 4 m) and position σ per source (`Seen` 5 m, `Chart` 20 m?).
- `Seen` rocks: is "seen" at an unknown tide, so -6 ft stands, or do some notes carry the tide?
- Reads public (like traces) or editor-gated (as Tier 2 proposed for mask polygons) while the stack is a
  dev product?
- Whether the Hydropalooza heights in the notes should become `z_m` directly (they are 2009 multibeam
  depths below MLLW).

## 9. What exists to build on

`water.py` bodies/kinds/edits and `sources.json` trusted edges; `fill.py` points with sigma, lead-line
bounds, floors, the tidal lens; `water_surface.py --flood-to` (the sea mask is the stack's z = tide stage);
`ls_water.js` `levelTile(tide)` and the tide slider; `scarps.py`/`scarps.js`/trace groups (tables, editor
gate, Terra Draw session, undo, GeoJSON download); `terra_draw.js` polygon mode; `export_soundings` as the
file-export pattern; `dev_viewer_add.py --surfaces-only`. Hig's `Rocks` shapefile is readable from this
process (1,185 points, UTM 5N WGS84: Seen 897, Chart 171, Sub 93, Data 21, Deep 2).
