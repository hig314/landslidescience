# A generalised analysis tool — overlays as axes, the map as the sampler

*Plan written 2026-10-09 at Hig's request, after the permafrost panel shipped.
Nothing here is built. The permafrost panel (`permafrost.js`) and the
susceptibility scatter are the two concrete instances this generalises.*

## The idea, as stated

Pick two overlays — one on each side of the wiper — and open Analysis: the
panel's axes are those two quantities, with the terrain's joint distribution
as the backdrop and every landslide as a point in it. Click the map and the
cells inside a sample patch light up on the plot as a blotch, and the
landslides inside the patch are highlighted. The permafrost panel does this
for one hard-wired pair; the susceptibility scatter does it for three models
with a brush back to the filters; the OPERA panel does the viewport version
for two tracks. Three bespoke data paths for one shape of question:

> *Where does this ground sit in the space of two things we can map, and
> where do the landslides sit in that same space?*

## What the three existing panels share, and where they differ

| | Susceptibility scatter | OPERA asc × desc | Permafrost panel |
|---|---|---|---|
| axes | lw / n10 / DGGS (pickers) | asc, desc (fixed) | PZI×prob, MAAT×MAGT (picker) |
| terrain backdrop | precomputed 2-D histogram (90 m, EPSG:3338) | computed from the viewport's value tiles | precomputed (1 km and 60 m) |
| landslides | counts per cell from `susc_values.json` | none | points from `pf_values.json` |
| spatial selection | none (viewport only) | the viewport | a circle; 1 km cells from the server, 60 m from value tiles |
| brush | box → lw/n10 filter sliders | none | none |
| value source | none at runtime | 8-bit value tiles (proxy) | value PMTiles (terrain-RGB) + `.npy` grids |

The differences are all in the **data path**, not the plot. A general tool is
mostly a data contract plus one panel.

## The contract: a *quantity*

Every layer that can be an axis declares how to read numbers from it. This
is the piece nothing has today — overlays are colour tiles, and colour
cannot be read back (lossy WebP, banded ramps). Proposed descriptor on an
`OVERLAYS` entry (or a sibling `QUANTITIES` registry keyed by overlay id):

```js
values: {
  label: 'Gruber MAAT', units: '°C',
  // fine: client-side tiles, read with the profile tool's decoder
  tiles: { url: '/overlays/pfv_maat60.pmtiles', encoding: 'terrain-rgb', scale: 10, zoom: 10 },
  // coarse: a server grid, for the terrain density and the 1 km cells
  grid: 'maat',                 // key into data/quantities/ (EPSG:3338 .npy)
  range: [-24, 8], bins: 64,    // density axis
  landslide: 'maat60'           // key in ls_quantities.json
}
```

Two sampling backends behind one interface, chosen by the quantity:

- **Client tiles** (fine, native-ish resolution): what `permafrost.js`
  `read60()` and `profile.js` already do. Costs an archive per quantity
  (200–600 MB at z10 for 60 m fields; a 90 m field fits z9 at ~150 MB; a
  1 km field at z7 is a few MB). Northness showed the limit: noise-like
  fields do not compress and are better derived (slope, aspect, northness,
  curvature all come from the elevation tile for free).
- **Server grids** (coarse, 1 km or 500 m, EPSG:3338 `.npy`, ~22 MB each):
  what `inventory/permafrost.py` does. Equal-area, cheap, enough for a
  statewide density and for the "published cells" view of any 1 km product.

The **joint density for any pair** then needs no precomputation: the server
holds a cube of 1 km grids and answers `api/analysis/density/?x=&y=` from
two of them in milliseconds (5.9 M cells; numpy `histogram2d`). Fine
densities (60 m) stay precomputed for curated pairs only, as now — they
take minutes to build and are a few KB to ship.

**Landslide values** generalise `susc_values.json` + `pf_values.json` into
one `ls_quantities.json` — every quantity sampled at every centroid, by one
offline script (`tools/sample_quantities.py`, superseding `sample_susc.py`
and `export_web.py --values`), regenerated when landslides change.

### Which layers earn an axis

Already readable: OPERA asc/desc (8-bit, ±30 mm/yr), lidar DEMs and
Mapterhorn (terrain-RGB; **elevation, slope, aspect, northness and relief
are the most useful axes of all and are not overlays**), the four permafrost
fields. Worth adding value archives for: coherence (banded, 93 m, small),
ITS_LIVE speed and dh/dt (120/100 m), IceBoost thickness and bed, lw / n10
/ DGGS (90 and 20 m — DGGS is categorical, which the density must respect:
class position on the axis, as the scatter does), Pastick (30 m; 2.8 GB as
colour, so a z11 value archive ~1 GB — borderline). Not readable and not
worth it: bedrock geology (categorical with 400 units — a *grouping*, not
an axis), the trace rasters, basemaps.

## The panel

One `LSAnalysis` panel replacing the three:

- **Axes**: two pickers listing axis-capable layers, plus the derived
  terrain quantities. **"Use wiper panes"** fills them from the current
  left/right overlays — the wiper is a natural and legible way to choose a
  pair, but making it the *only* way couples an analysis to a display state
  (you would have to open the wiper to ask a question); the pickers stay
  primary and the wiper is a shortcut. Orientation: left pane = x.
- **Backdrop**: the terrain density (server, on demand; or the precomputed
  fine one where it exists), log-scaled, with the scatter's proportion
  mode (landslides per terrain cell) as an option since that is the mode
  that says anything.
- **Points**: landslides from `ls_quantities.json`, map symbology, hover
  and click as in the permafrost panel; respect the active filter and
  "Limit to map view" (the permafrost panel currently matches the
  scatter — all records — and should move to the filter).
- **Sample**: the permafrost patch tool, extracted as `LSSample` — a
  circle of chosen radius; later a drawn polygon, and the profile tool's
  line (a profile *is* a sample along a path, and the profile panel could
  show the two quantities along it). The sample's cells draw as the blotch
  on the plot (fine source if the quantity has one, else the server cells),
  the landslides inside the sample are ringed on the plot and on the map,
  and a per-quantity summary (median, range) sits under the plot.
- **Brush**: a box on the plot highlights the landslides inside it on the
  map (client-side, from the values file — no slider per quantity needed),
  and, where both quantities have client tiles, **paints the map** where the
  terrain falls inside the box: a `analysiscolor://` protocol like
  `operacolor` that decodes both value tiles and colours the in-box pixels.
  That is the question the tool exists for ("where is PZI > 0.6 *and*
  slope > 30°?"), and nothing on the site answers it today. The
  susceptibility brush keeps driving its sliders for lw/n10, because those
  filters exist.
- **URL**: `ax=<x>|<y>`, `smp=lat,lon,r` (generalising `pp=`), `bx=x0,x1,y0,y1`
  for the brush, `an=analysis` for the panel; the old `an=scatter` and
  `an=pf` map onto it.

## Things to decide before building

1. **Area weighting.** The server densities are in EPSG:3338 (equal-area —
   right). The client tile samples are in Web Mercator, where a z10 pixel
   covers 1.6× more ground at 55° N than at 70° N; a blotch is fine, a
   histogram of a sample is slightly biased. Either weight samples by
   cos(lat), or read samples at a fixed ground spacing (the profile tool's
   approach). Small, but should be decided once.
2. **Land only?** The permafrost grids mask to Gruber's land. A general
   density over, say, coherence × slope will include sea unless the cube
   carries a land mask. Carry one.
3. **Categorical axes** (DGGS classes, geology units if ever): class
   position on the axis, no interpolation, no brush painting across
   classes. The scatter already handles DGGS this way.
4. **Resolution mismatch** on a pair (30 m Pastick × 1 km PZI): the density
   lives on the coarser grid; the fine source still supplies the sample's
   blotch. Say so in the panel.
5. **Which existing panels fold in.** Susceptibility scatter: yes, its
   brush→slider behaviour kept as a special case. Permafrost: yes, that is
   the prototype. OPERA: its viewport-as-sample behaviour becomes "sample =
   current view" — one more sample shape. Seasonal timing and time-series
   are not this shape and stay.
6. **The sample shapes.** Circle now; the view; a polygon; the profile
   line. Each is a small addition once the sample→values pipeline is
   generic. A landslide's own polygon as the sample ("what does this
   slide's ground look like in these two axes?") is the most useful and the
   cheapest, and should probably come second after the circle.

## Phasing

- **Phase 1 — the contract and the extraction.** `values` descriptors on the
  layers that already have readable sources (OPERA, permafrost, terrain
  from Mapterhorn, lidar DEMs). `LSSample` pulled out of `permafrost.js`
  with the circle shape. The general panel built against precomputed
  densities only, with the permafrost pairs as the first axes; the
  permafrost panel becomes a preset of it. No new archives.
- **Phase 2 — the quantity cube.** `tools/build_quantity_cube.py` writes 1 km
  `.npy` grids for every axis-capable layer plus a land mask;
  `api/analysis/density/` serves pairs on demand; `ls_quantities.json`
  replaces the two values files; the susceptibility scatter folds in.
- **Phase 3 — the brush that paints the map**, the wiper shortcut, the view
  and polygon sample shapes, and value archives for the layers that justify
  them (coherence and ITS_LIVE first; they are small).

Phase 1 is a refactor with one new behaviour (highlighting landslides inside
the sample); 2 is where the tool becomes general; 3 is where it becomes
something the site does not have today. Each phase ships on its own.

## Costs that are not obvious

- Every client-readable quantity is an archive on R2 and a few hours of
  upload from Homer; the permafrost set (1.7 GB for five fields) is the
  benchmark. Precision chosen for plots, not science, keeps them small.
- `pmtiles.js` opens an archive per quantity per session (one 16 KB header
  read each); the sample reads 4–9 tiles per quantity. Cheap.
- The landslide values file grows by one number per quantity per record;
  at 30 quantities it is still under 1 MB.
- The paint-the-map brush decodes two tiles per screen tile per frame of
  interaction; `operacolor` shows this is fine at 256 px tiles.
