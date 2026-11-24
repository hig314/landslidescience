# /glaciers app

Glacier identity and retreat histories, flow tracers, IceBridge points, the experimental gate.

## Since 2026-11-16: a glacier browser, retreat histories first

Plan, decisions and the source survey: [GLACIERS_PLAN.md](../GLACIERS_PLAN.md).
Phases 0–2 are built (see docs/STATE.md for whether they are deployed). What
follows is the plumbing; the sections further down, written when the page was
only a flow viewer, still describe the Flow tab and the overlays.

**Three files where there was one.** `glaciers.js` is the page shell (map,
basemap, raster overlays, IceBridge points, tabs, URL hash, `window.GL`).
`gl_retreat.js` is the Retreat tab. `gl_tracers.js` is the tracer engine,
moved out mechanically with the engine code unchanged, **loaded only when the
Flow tab is first opened** and stopped (frame loop off, canvas hidden) while
another tab shows. Clicks, cursor and the popup slot go through `LSTools`,
as on `/inventory/` and `/lidar/`.

**Tabs and the hash.** `tab=retreat|flow|layers` is always written. A link
with `site=` or `t=` and no `tab=` is a pre-tabs flow link and opens on Flow.
Retreat adds `g` (selected glacier), `yr` (year bar), `src` (sources on;
absent = all), `gc=0` (ice cover off). Params a not-yet-loaded module owns
are carried through untouched.

**Identity: `glaciers`, keyed by RGI 7.0 id, not GLIMS id.** RGI 7.0 region
01, 27,509 rows, from the GLIMS WFS (no login). RGI gives one GLIMS id to as
many as eight glaciers (135 ids are shared), so the GLIMS id is an attribute.
Only 661 rows have a name. Deep link: `/glaciers/g/<id>/`.

**Mirrored sources: `glacier_obs`.** One row per published feature,
publisher attributes verbatim in `props`; beside them our reading (`kind`
outline / debris / terminus, `glacier_id` + `link_how`, `date_min`/`date_max`).
Registry, readers, matching rules and the schema are all in
`glaciers/sources.py`. A fetch replaces one source in one transaction and is
count-checked.

| Source id | What | Rows (dev, 2026-11-16) |
|---|---|---|
| `glims` | GLIMS outlines, Alaska box, via WFS | 68,760 (63,558 matched) |
| `nsidc0634` | McNabb & Hock tidewater termini 1948–2012 | 10,333 |
| `usgs_glacier_bay` | USGS Glacier Bay termini 1700–2019 | 233 (151 matched) |
| `usgs_benchmark` | USGS benchmark margins, Alaska | 46 |

- **GLIMS has no primary key over WFS**, so `startIndex` paging has no stable
  order. The fetch cuts the box into cells, asks each whole, and recognises a
  feature that straddles an edge by content. The total is checked against
  the server's.
- **Matching** (`link()`): GLIMS by id where the id names exactly one of our
  glaciers, else by largest overlap; polygons by overlap; a terminus line to
  the glacier it runs across furthest, else the nearest within 2 km;
  NSIDC-0634 once per file, a same-named candidate winning outright. A row
  that matches nothing stays unmatched and is still drawn.
- **The file-based sources are prepared on the workstation**
  (`tools/glaciers/download_sources.sh` then `prep_sources.sh`; NSIDC-0634
  needs the Earthdata login in `~/.netrc`, and wget fetches nothing there,
  curl works) into `data/glaciers/sources/*.geojsonl`, which travels to
  production by rsync. The container has no GDAL.
- **Tile geometry is stored in Web Mercator at two detail levels**
  (`geom_s` about 30 m, `geom_c` about 250 m). Transforming full outlines per
  request made a statewide tile take 3.2 s; it is 0.4 s now. Small-scale
  tiles are also kept per worker, keyed on the stamp.
- **The stamp** (`data/glaciers/.extent_stamp`) is bumped by every fetch and
  rides tile and JSON URLs as `?v=`.
- **Size**: the two tables are about 1 GB in PostGIS (824 MB + 193 MB in
  dev). The droplet had 12 GB free on 2026-11-14 (docs/STATE.md). Check before
  loading production.

Per environment, in order: `migrate_glaciers`, `fetch_glacier_source rgi7`,
`fetch_glacier_source --all`. `--relink` re-matches without refetching.

**Statewide ice cover (NSIDC G10040) is one raster archive of bit masks**,
`data/overlays/glac_cover_mask.pmtiles` (+ `glac_debris_mask`), built by
`tools/glaciers/build_cover_mask.py`: bit i of a pixel = ice in epoch
1986 + 2i. The `glcover` protocol in `gl_retreat.js` decodes a tile once and
colours it for the year on the bar, so all 18 epochs cost one download and
"ice lost since 1986" is a bit test. **The tiles are data: PNG, nearest
resampling at every zoom, alpha only 0 or 255.** The epoch list is held in
both the builder and the page; `check_invariants` compares them. Vector tiles
were tried first and abandoned: GDAL's writer filled whole tiles solid on
these polygons.

**Year colours** for every dated line are `LSColors.glacierYearColor`
(`ls_colors.js`).

### The flow network and ice faces (2026-11-16, later the same day)

Hig's refocus (GLACIERS_PLAN.md §1): area is not the target; **calving-face
position** is, then surface elevation, both against distance along a
**river-like network of centrelines with feeders**. Built: `glaciers/network.py`,
`gl_network.js`.

- **`glacier_centerlines`** is the RGI 7.0 centrelines product as published
  (41,293 lines, reference only, tile layer `lines`). Lines run down-flow and
  stop at the glacier's outline and terminus. Load: `fetch_glacier_source
  centerlines` (file prepared on the workstation from the Bremen mirror; the
  NSIDC copy needs Earthdata).
- **`glacier_lines`** is what we edit: `kind = flowline` (adopted from RGI,
  drafted, or drawn) and `kind = face` (a dated line along an ice front; no
  polygon asked for, partial lines welcome). Kept apart from the reference so
  the editor's whole list stays small.
- **A join is a shared coordinate** (Hig, 2026-11-16). `normalize()` runs
  after every flow-line change: a line END that lies on another line (within
  2 m, which in practice means the editor snapped it there) becomes a vertex
  of both; a line END on another line's START **fuses the two into one row**,
  permanently (origin `mixed` when the halves differ). `rebuild()` then sets
  `down_id`, `join_frac`, `end_chain_m` (distance of the line's downstream
  end from the outlet) and `network_id`. The dots on the map are downstream
  ends: ring = joined, solid = outlet.
  - RGI tributaries stop 150 to 330 m short of the line they feed. On
    adoption `join_rgi()` carries each on to the line RGI names
    (`outflow_id`). The first version guessed joins by proximity (300 m):
    do not bring that back.
  - **An existing vertex within 2 m wins over inserting a new one.** The
    editor works at 6 decimals, so a trunk saved from the browser sits
    centimetres off its feeders' ends; inserting "the exact point" added a
    duplicate vertex at every join on every save.
  - `normalize()` is global: the first call after this rule shipped fused
    every end-to-end pair in the table, including two machine drafts that
    were about to be regenerated. Expected now; worth knowing.
  - A fused line no longer records where the RGI part ended, so the plot
    cannot shade "glacier today" against "drawn beyond". Not yet replaced by
    a present-terminus marker.
- **The editor is scarps.js** (options added for this page: docs/TRACES.md),
  mounted with `api: '/glaciers/lines/'`, explicit drawing and end snapping;
  `network.py` answers that module's contract (`api/scarps/`, `create/`,
  `<id>/`), which is the only reason those URLs say "scarps". Extra fields
  (name, date, basis, source, reverse, pin) ride its `save()`. The session is
  an `LSTools` mode (the ✎ control), editors only. **Start it only after the
  map style has loaded**: its list answer once beat the style and its first
  `addSource` threw.
- **Editing, after Hig's first sessions**: nothing is drawn until a ＋ button
  arms one line; a clicked vertex is active and Delete removes it; a **+** at
  each end of the selected line extends it from that end (the start too);
  geometry saves are serialised per line, latest wins, because fast deletes
  were being overdrawn by stale answers. All in scarps.js (docs/TRACES.md).
  The page's scripts carry `?v=<newest mtime>` so an edit is never run
  against a cached older file.
- **Getting lines in**: *Add its centrelines to the network* on a glacier's
  card (`api/adopt/`); *Draft the former bed* (`api/draft/`: the median midpoint of
  the mirrored faces in each km out from the terminus, smoothed, fused onto
  the main line; a guess to correct by hand. The first version chained
  midpoints one by one and zig-zagged where the terminus had sat still); draw; or
  import GeoJSON lines with a `date` property (`api/import/`). Dates are
  brackets: `1904`, `1904-08`, `1904-08-15`, `1894 to 1904`.
- **A path** (`api/path/<line id>/`) is one line followed down to its outlet.
  A face is placed at the mean of its vertices' positions along the reach it
  crosses (width-averaged; the bare crossing is returned too); a face that
  stops short but lies within 1.5 km is projected and marked; a pinned face
  (`pin_line_id`, `pin_frac`: schema and maths are in, the control to set a
  pin is not) sits where pinned. The plot is time across and distance up,
  measured down-flow from the HEAD of the line so a retreat reads as falling
  numbers (Hig, 2026-11-17; the API still counts `x_m` up from the outlet,
  the page converts) (Hig's preference; the other way round is a menu choice kept
  per browser), the distance axis fitted to where the faces are; `np=<line
  id>` in the URL.
- **Check against the publisher** (Icy Bay, dev): McNabb & Hock's own length
  plus our distance should be constant. NMAD 0.12 km at Tyndall over an
  18.5 km retreat, 0.26 Guyot, 0.38 Yahtse, 0.04 Tsaa; worst single faces 1
  to 4 km, on drafted lines not yet corrected.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## /glaciers app (experimental; tracer MVP ships, pair stack does not)

Sibling map app at `/glaciers/` (`glaciers/` Django app; public, like
/inventory/* — it keeps a `noindex` robots block rather than a login barrier,
since it is experimental rather than sensitive). Hig declared the tracer app
MVP on 2026-08-19, so it deploys under the normal flow. The RAW IMAGE-PAIR
stack does NOT: `/glaciers/pairs/`, the fitted-pair overlays and the
"robust pair fit" tracer field are all gated on `experimental_enabled()`
in `glaciers/views.py`, which tests for `data/glaciers/experiments/` — a
directory that is never rsynced to prod. Keep new research scaffolding
behind that same gate rather than inventing a second switch.

**Right-click → copy coords** (2026-11-22, Hig: "add our ctrl-click for
coordinates tool"): right-click (Ctrl-click on a Mac) copies `lat, lon` with a
toast, as on /inventory/ and /lidar/; glaciers.js keeps its own copy of
map.js's handler (same rule: `LSTools.blocked()` wins, except tracing with no
line in progress). This is the third copy (map.js, lidar_preview.html,
glaciers.js); moving them into one helper is a main-stream job.

**The retreat year bar is closed by default** (2026-11-22, Hig: "keep the
functionality but hide it by default, opening it with a control"): a
"▶ Retreat by year" button at the bottom of the map opens it on the Retreat
and Layers tabs; its × closes it and puts the year back to the present (so a
past year never stays applied to the ice cover and outlines unseen). A link
with `yr=` opens it. Flow keeps its own bar.

- **Shared-module discipline**: basemaps from `basemaps.js`, glacier
  overlays from `ls_overlays.js` (single source of truth with the inventory
  map — add/change glacier overlays ONLY there). Planned convergence
  (in order): shared hash codec (`ls_hash.js` — **done 2026-10-06**, map.js
  parses and writes through it), shared overlay framework
  (state/rows/ov= codec into ls_overlays.js), shared wiper (`ls_swipe.js`)
  — each extracted behavior-identical from map.js with the inventory as the
  regression baseline, then consumed by /glaciers.
- **Lagrangian tracers**: particles advected through the time-varying
  ITS_LIVE field (annual vx/vy 1984–2025 + climatological seasonal
  amp/phase superposition), density-managed respawn with fade-out. Engine:
  `glaciers/static/glaciers/js/glaciers.js`. Bundles: built by
  `tools/build_glacier_tracers.py` (venv w/ zarr; reads the v2 annual
  composite cubes; 240 m int16 gzipped) into `data/glaciers/<slug>.bin.gz`
  + `.json`, served at `/glaciers/data/` (Content-Encoding: gzip against
  the .bin URL). Composite-cube dir naming = TRUNCATION toward zero of
  center lon/lat (verified; script HEAD-probes candidates).
- **Seasonality is window-bounded, and must stay that way**: ITS_LIVE's
  `vx_amp`/`vx_phase` are *climatological over a stated window* ("[2014-
  2024]"), while the tracer timeline runs 1984–2025. `seasonWeight()` in
  glaciers.js fades the sinusoid to zero outside that window (one-year
  taper) so pre-2014 playback shows annual means only — replaying the cycle
  across the Landsat-5/7 era asserts seasonality the product never fitted.
  The window comes from `season_window` in the bundle header; both builders
  parse it from the cube attribute. Conventions verified against the cube
  metadata: amp is amplitude (not peak-to-peak), phase is the day of
  MAXIMUM (so cosine, peaking there), and the annual field is the
  sinusoidal fit's own mean — the added term is zero-mean, nothing is
  double-counted. Caveat worth remembering: across the pair-sweep work a
  single annual sinusoid explained only 12–34% of temporal variance, so the
  tracers show the climatological cycle, not the year's actual seasonality.
  Where observed quarterly fields exist (`recent` block, 2013–2025) they
  take precedence and no model is used at all.
- **Dev mount**: `./glaciers` is volume-mounted in docker-compose.override
  like the other app dirs.
- **IceBridge radar bed elevation / ice thickness (UAF HF + ARES)**: two
  point-data overlay toggles, statewide AK — a precursor layer toward a
  planned ice-dam flotation analysis (that analysis itself is a separate,
  still-evolving raster-stack workflow, tracked outside this app for now).
  Pipeline: `tools/fetch_icebridge_data.py` (workstation venv w/
  `earthaccess`; NASA CMR short_names **IRUAFHF2** 2013–2016 and **IRARES2**
  2016–2021, both Alaska + NW Canada, both the identical field layout —
  `lon_deg_e`/`lat_deg_n`/`bed_height_m`/`ice_thickness_m`; bed/thickness are
  left EMPTY, not a sentinel, on traces with no bed interpretation) → raw
  CSVs into `data/glaciers/icebridge_raw/<short_name>/` (2+ GB, gitignored,
  never rsynced to prod — the record of truth stays local) →
  `tools/build_icebridge_points.py` filters to rows with a `bed_height_m`,
  keeps lon/lat/bed_height_m/ice_thickness_m, and writes
  `data/glaciers/icebridge_{uaf_hf,ares}.json`. **Each NSIDC CSV opens with a
  block of `#`-prefixed metadata lines before the real header row** — skip
  them or every column comes back empty. **ARES alone is 2.88M qualifying
  rows** (a 545 MB GeoJSON, unfetchable client-side), so the build script
  grid-decimates to one point per `GRID_DEG` cell (~55 m) — a display
  simplification, not a science product; the raw CSVs remain authoritative.
  Named `.json` not `.geojson` so the existing `glaciers:tracer_data`
  route/regex serves them with zero backend changes (`_catalog()` already
  skips any `data/glaciers/*.json` without `name`/`center`/`bin` keys, so
  these don't get mistaken for a tracer bundle). **Not gated behind
  `experimental_enabled()`** — unlike the pair-fit stack, this is general,
  statewide reference data rather than a one-glacier research probe, so it
  ships through the normal dev→test→approve→push flow. Rendering is local to
  `glaciers.js`, not `ls_overlays.js` — that shared module is raster-tile-
  only (`sourceDef()` → `rasterDef()`), and this is sparse vector/point data;
  `ensureOverlays()`/`applyOverlays()` branch on `ov.type === 'circle'` to
  add a MapLibre `geojson` source + data-driven `circle-color` layer instead
  of a raster one. **Color-by is a shared toggle** (`ICEBRIDGE_RAMPS`, one
  control drives both instrument layers so they stay comparable): ice
  thickness is a plain magnitude (sequential, one hue light→dark); bed
  height renders as an ABRUPT seam at 0 m — blue below (flotation-relevant),
  green above — each arm still gradated by magnitude, medium-toned at the
  seam (not pale) so the hue swap itself reads clearly, fading darker toward
  each extreme. Domain bounds (-450..900 m bed height, 0..700 m thickness)
  come from the real pulled data's printed percentiles, not guessed. The
  legend bar's `stops` array doubles as its CSS gradient definition (a
  repeated value = the hard seam); tick labels are positioned by actual
  value percentage, NOT CSS flex `space-between` — space-between spaces
  labels evenly regardless of value, which silently put the "0 m" label at
  the visual center instead of over the real color seam. Deploy note: the
  two built JSON files are gitignored like all of `data/` — they need their
  own rsync to the droplet's `data/glaciers/`, independent of the code push
  (same pattern as the susceptibility/OPERA tiles).
- **Future**: glacier-properties tab paralleling the inventory's landslide
  tab — eventual input to Bayesian failure-likelihood analysis surfaced in
  the landslides app. Keep sidebar/tab chrome and per-glacier identity
  (RGI ids) in mind when refactoring.

## Reference maps tab and tracing faces (2026-11-17)

- **The Layers tab is now "Reference maps"** (tab key still `layers`, the
  URL grammar is additive). It mirrors the inventory's tab with a subset:
  basemap cards by category (the card is shared, `LSBasemaps.buildCard`,
  and the inventory's `_buildBasemapCard` now calls it), the admin-shared
  QuickMapServices layers from `/inventory/api/qms/promoted/`, the glacier
  overlays as before, then lidar and (editors) uploaded GeoTIFFs and
  Sentinel-2 fetches. No wiper, no susceptibility / InSAR rows.
- **Lidar and imagery are the inventory's own modules** (`lidar_ui.js`,
  `trace_rasters.js`), mounted by `glaciers.js` with this page's map and
  stub context (no wiper, no susceptibility layers, no landslide link).
  Their APIs stay under `/inventory/`, their URL params are the same
  (`li=`, `im=`), and the template sets the three globals they read
  (`_isInventoryEditor`, `_canViewRestricted`, `CSRF_TOKEN`). The one hook
  added to both is `ctx.floorLayer(map)`: the layer id rasters go beneath.
  Here that is the first layer that is neither basemap nor raster, so
  outlines, faces and flow lines always draw over the image.
- **Tracing a face** (editing panel, "Trace an ice face"): date, read-from
  and source are set BEFORE drawing and saved with the line in the create
  request (`drawFields` in `scarps.js`; `api_line_create` takes `date`,
  `basis`, `source_note` for faces). "From the map" fills them from what is
  switched on (`GL.imagery()`: imagery overlays with their `image_date`,
  lidar surveys with their year, then the basemap; AHAP and BC orthophoto
  carry a bracket, the rest no date). A date the server cannot read does
  not cost the line: the face is saved undated with a warning. The reply
  carries `placement` (`network._placement`, the plot's own rule: crossing,
  else projected within `NEAR_M`), and the panel says which line the face
  landed on and how far from its head, or that it is on no plot. "keep
  tracing" re-arms after each save (`onCreated`).

## Typed dates (2026-11-17)

Face dates are read by `landslidescience/dates.py` (`parse_span`), the one
place on the site for reading a date a person typed; `span_text` writes them
back ("18 Aug 2003", "Aug 2003", "2003", "1894 to 1904"), and every text it
writes reads back to the same pair of days. python-dateutil does the free-form
reading; the module's docstring says how precision is kept (read twice against
two defaults) and what is refused on purpose (two-digit years, all-number
dates other than ISO). The inventory's single-field date save
(`views._parse_human_date`) falls back to `parse_day` after its own formats.

## Editing panel states and the Source pull-down (2026-11-17)

The editing panel (`#nw-panel`) sits above the tab panes, so it stays up on
Retreat and Reference maps, and shows one state at a time: `#nw-create` (new
flow line, trace a face, import) with nothing selected, `#nw-selBox` with a
line selected. Editors open it from the ✎ map button or the `.nw-open`
buttons in either tab.

A new face's Source is one pull-down (`nw-fFrom`), the trace-groups pattern:
layers on the map now (`GL.imagery()`; choosing one also fills date and
basis), every `source_note` already on a face, then "＋ New source…", which
opens a text box (`nw-fSrc`, which always holds the value that is saved). A
typed source joins the list for the session and, once a face is saved with
it, for good. There is no sources table: the list is the distinct notes.

## Along-line positions are metric; split, adopt, extend (2026-11-17)

Every position along a flow line (`join_frac`, a face's place on a reach, the
clipped path geometry) is a fraction of length taken in `METRIC_SRID` (3338,
Alaska Albers), not in degrees; see the comment above `_REBUILD_SQL` in
`network.py`. Lengths stay geodesic. `rebuild()` must be run once per
environment after this code lands, or stored `join_frac` / `end_chain_m` keep
the old skewed values.

A path is the clicked line plus only the part of each downstream line below
the join: `reaches[].length_m` is that part, `full_m` the whole line, and the
returned geometry is clipped the same way.

Server-side helpers with no button yet: `split_line` (cut a trunk at a
confluence vertex and name the lower part, as "Icy Bay" below the Guyot
join), `adopt` (the body of `api_adopt`), `extend_to` (one straight link from
a line's end to a trunk, meeting it slightly down-flow; a first guess to
correct by hand).

## Videos on the network (2026-11-17)

`glaciers/videos.py` + `gl_videos.js`; the module docstring is the design
note. A video is linked to one or more SEGMENTS (a flow line between two
junctions), stored as anchor points and resolved to segments at read time, so
links survive lines being split or redrawn. Kinds (`videos.KINDS`): `planet`
(a story slug; the mp4 is archived and served by `inventory/planet.py` at
`/inventory/planet/<slug>.mp4`, one copy shared with landslide records; a
comparison story is linked out) and `s1` (an uploaded .mp4/.webm/.gif under
`data/glacier_videos/`, served at `/glaciers/video/<name>`, 150 MB cap). A
new uploaded kind is one more row in `KINDS`.

Endpoints under `/glaciers/lines/api/`: `videos/` (public list + map
layers), `segment/?lon=&lat=` (the segment under a point, for the picker),
`videos/save/` (editors; multipart; create, change, delete). `api_path`
returns `videos` with the stretches of the path each covers; the plot draws
them as boxes (distance by years) and lists them under the chart.

The map marker's click is an LSTools `reference` entry at priority 50, above
the network's own (40), so it plays the video instead of plotting the path.
The picker is its own hold (`glvideo`) with an `action` entry, so it cannot
overlap a line-editing session.

Tables `glacier_videos`, `glacier_video_anchors` are created by
`migrate_glaciers` (once per environment). `data/glacier_videos/` is data: it
is not in git or the image and must be copied to prod with the rows.

Also 2026-11-17: a new face's Source and Read from default to the topmost
thing on the map (`autoSrc` in `gl_network.js`) until the editor chooses or
types one; a reused source brings the Read from it was last saved with.

**Which line a face lands on (2026-11-17, evening).** A face that crosses a
flow line is placed where it crosses. A face that crosses none is projected
onto ONE line: a line of the glacier it was published for if one is within
`NEAR_M`, else the nearest (`proj_id` in `_FACES_SQL`). Before this every
face within `NEAR_M` of a line was projected onto it, so side-by-side
glaciers plotted each other's faces (Tsaa's plot carried 318 of Grotto's).

## Distributaries (2026-11-17, evening)

A flow line that STARTS on another line is a distributary: ice leaving a
trunk for a second front (Moraine/Apron off Grotto). Like a join it is
nothing but a shared coordinate; `rebuild()` derives `up_id` / `up_frac`
from it as it derives `down_id` / `join_frac` from where a line ends.

- **What counts.** A start anywhere on another line except that line's own
  start. At the other line's END it is a fork only when a second line starts
  there too (a trunk splitting); a single line starting where others end is
  a confluence carried on under a new name (Icy Bay) and gets no up-link.
- **normalize()** makes a start that lands on a line a shared vertex (step
  1b), and fuses end-to-start only when one line ends there and one starts.
- **Network.** `network_id` is now the connected set over joins AND forks,
  named for the outlet most lines drain to, so a distributary with its own
  outlet stays in its parent's network.
- **Path and plot.** `api_path` for a distributary starts at the PARENT's
  head: the parent down to the fork (`to_frac` < 1, labelled "above the
  fork"), then the line, then whatever it flows into. A reach is now the
  stretch `from_frac`..`to_frac` of its line, `end_m` the distance from the
  outlet at `to_frac`. The parent's own path is unchanged (it follows its
  own line).
- **Segments** (videos) are cut at forks as well as joins.
- **Map.** `api_nodes` adds an orange dot where each distributary leaves.

The editor needed nothing: it already snaps a dragged or drawn FIRST vertex
onto other flow lines, as it does the last.

**Edit cost (2026-11-17, late).** `normalize()` and `rebuild()` run on every
geometry edit. They used to compare every line end with every vertex of every
line (3 s an edit at 89 lines). Now `normalize()` looks only at lines whose
bounding box holds the point and `rebuild()` finds candidates through the
GIST index (`c.geom && ST_Expand(point, 0.001)`): 0.35 s, identical results.
What still grows with the network: `normalize()` loads every flow line's
coordinates, and the editor downloads every line as one GeoJSON
(`lines/api/scarps/`, 2.5 KB a line). Fine for hundreds of lines; adopting
all 41,293 RGI centrelines at once is not what this design is for.

## Outlines on the plot (2026-11-20)

Portage plotted one point although its map showed fronts from 1951 to 2019:
the plot read only face LINES (ours, NSIDC-0634, USGS Glacier Bay), and
Portage's history is 17 GLIMS outlines. Now an outline gives one point,
`how = outline`: where the path LEAVES it, furthest down-flow
(`_outline_points`, `_OUTLINES_SQL` in `network.py`).

- **Only outlines of glaciers whose lines are on the path** (`glacier_id`
  of the path's reaches), never a neighbour's. A drawn line with no
  `glacier_id` brings none.
- **Dropped**: an exit within `OUTLINE_DIVIDE_M` (150 m) of another glacier's
  RGI outline (the ice runs on into that glacier, so it is a divide, not a
  front); an exit at the end of the part of a reach that is on the path (the
  next reach gives the exit, or the path stops inside the ice and measured
  no front); `debris` rows (not faces).
- **Every submission is plotted**, several on one date included. One
  position, so `x_m` = `cross_m` (no width averaging).
- **Page**: light points under the faces, in the source's colour (GLIMS
  grey-blue, USGS benchmark brown), an "outlines (n)" switch in the plot
  header, remembered per browser (`gl_nw_outlines`); on by default (Hig).
- **Checked against McNabb & Hock** on dev's Icy Bay network: the eight GLIMS
  outlines of 2000 and 2010 on Guyot, Tyndall, Tsaa and Yahtse sit within
  30 m of the 0634 centreline crossing of the same date, one at 0.37 km
  (Tsaa 2010). Portage (prod's line 377, run in a rolled-back dev
  transaction): 12 of 17 outlines plot, 10.63 km from the head in 1951 to
  8.31 km in 2018; the other five are 0.01 to 0.05 km² fragments off the
  line, among them the whole of the 2019 USFS "outline" (that submission maps
  the tongue as debris).
- **Cost**: each outline is cut to the line's box before reprojection;
  Yahtse's path went 0.6 s → 1.0 s, the others less.

## Sentinel-1 timelapses from a Drive folder (2026-11-20)

Hig makes monthly S1 RGB timelapses in Earth Engine into one Drive folder
(`1glvNdBsjprtNPjXyIZhASgbF8Lyng3Mc`, 145 videos on 2026-11-20). Bringing them
in is three steps; the record of each run is in the main checkout's
`data/glaciers/s1_review/` (not in git).

1. **Probe** (workstation): `tools/glaciers/s1_videos.py probe <folder>` lists
   the public folder (no login) and, by HTTP range requests, saves each
   video's index, the label corners of its first and last frames and a
   thumbnail of the last frame. The month is burned into every frame;
   `s1_labels.py` reads it against digit templates (`s1_glyphs.json`).
   `gap` finds the S1B outage (S1B failed 23 Dec 2021; S1C from 2025) by
   seeking to the frames either side of January 2022.
2. **Resolve and review** (dev): `manage.py s1_videos resolve <dir>` names each
   video's glacier (rules in `tools/glaciers/s1_sites.json`; Hig's named lines;
   RGI and GLIMS names; GNIS points from `data/glaciers/gnis/`) and picks one
   video per glacier and subject (no gap, then the latest). Hig reviews one
   frame per video on a page (https://claude.ai/artifact/48wufseowgxA4pztWZKKwe):
   approve, reject, or type the glacier (GLIMS id, RGI id or name). Export its
   `decisions` collection, then `s1_videos.py collect <exported dir>` and
   `manage.py s1_videos fixes <dir> <dir>/decisions.json`, which reads the typed
   corrections; republish the page's `videos.json` to show what they became.
3. **Import** (prod, after copying `manifest.json` and `decisions.json` to the
   droplet's `data/glaciers/s1_review/`): `manage.py s1_videos import ...
   [--dry-run]`. Approved, or assigned and not rejected, videos become public
   `s1` videos; the glacier's RGI centrelines are adopted if the network has
   none; the anchor is the RGI terminus, moved onto the nearest of Hig's lines
   when they miss it. Each video is one transaction with its download; a failed
   download rolls back its adoption and the run carries on. What the site
   already has (by Drive file id) is skipped, so reruns are safe.

**Drive's download quota.** A publicly shared file allows only a few full
downloads a day, and every ffmpeg open counts as one. The probe, a full-stream
date read (abandoned: Drive serves a 35 MB file in minutes) and two gap passes
used it up on most of the folder on 2026-11-20; Drive then answers with a
"Quota exceeded" page, from the droplet too. Touch each file as few times as
possible. Bounded ranges still work, but do not use them to get round the quota.

## Placing S1 timelapses from their content (2026-11-21/22)

`tools/glaciers/s1_georef/`. Per video: register frames on edge strength,
drop frames that correlate badly with the median over stable pixels (median
- 8 MAD; Brady-Abyss 2022-05 is a warped scene), take per-pixel statistics
(median colour, p10/median/p90, temporal spread, sign persistence, median
edge strength), simulate S1 from 3DEP (cos local incidence, cast shadow,
look 77 deg asc / 283 deg desc, 38 deg incidence) and match by NCC. Hig's
exports are **10 m in Web Mercator units** (ground pixel 10 cos(lat));
`match.py --merc 10` fixes the scale. The ctx_3dep bake is holey away from
lidar surveys; `sim.py` reads Mapterhorn z11 (same 3DEP) by default.
Exploration write-up: https://claude.ai/artifact/WBGwjcCGMxY1FHk9wz1RQr

Two rules learned on Hubbard (2026-11-22), which has little rock in frame:
**water is filled dark** in the simulation (DEM <= 2 m; it was the land mean,
which threw away the coastline, the strongest stable feature of a tidewater
scene), and a third product `stab` weights the template by stability
(|sign persistence| smoothed, thresholded, grown 15 px), which demotes the
calving face and flowing ice and keeps islands and shore. With both, Hubbard
lands within ~130 m of a hand tie point (Gilbert Point); before, 1.6 km off.

**Second method, `match2.py` + `fine.py` (2026-11-22), the one in use.**
`match.py` left three things free that are not: the scale (its +-5 % search
let Hubbard's candidates range 4.5..5.02 m; the reference is Web Mercator
too, so the template size is exactly 10 / reference pixel), the orbit (the
video's name says it; it agreed with the winner on 53 of 56 accepted), and
which product to trust (decide.py voted over six separate peaks). match2
sums the three products' significance maps into one consensus, runs it with
water filled dark AND with water left out of the correlation (two-sided
weighted NCC; Icy Bay's mélange is bright, so the dark fill is wrong there
and Guyot only placed this way), keeps the better, and uses the anchor as a
soft prior (-1 significance a km beyond 3 km from the frame). `fine.py`
repeats it on a 10 m simulation (Mapterhorn z13) around the result, at the
video's own pixel. 3 s a video for match2, ~15 s for fine (tile fetches).
On the 76 site videos: 60 of the 63 accepted unchanged (< 300 m), three
moved and better (Hubbard ~730 m, now on the ascending orbit its name says:
Gilbert Point's coast on the rock and the islet on its outline; Grand
Pacific; Columbia), and 8 of the 13 rejected placed (two Guyot, two Tsaa,
Margerie, Skilak, Tyndall, Alsek). Still not placed: Grand Pacific "Ferris",
Knik (3b7679), both Agassiz, Bering East (flat piedmont ice, little to hold).
`compare.py` is the regression gate (against the eye-checked placements);
`look.py` the eye check (median beside the DEM hillshade, coast + RGI on both).

Pipeline (all of it on the workstation; work dir `/Volumes/Nunatak/glacier_s1_georef/`):
1. `batch.py site_videos.txt`: per site video (list = prod's s1 videos with
   their anchors) fetch, `stack.py`, `sim.py` (±30 km around the anchor),
   RGI export from dev, then (first method) `match.py --merc 10`.
   One at a time: `stack.py` is row-blocked float16 (~5 GB peak; the first
   version took 28 GB on a 2572x1960x105 video).
2. `m2_all.sh m2_p1` (match2 per video), `fine_all.sh` (fine.py), eye check
   with `look.py`, the accepted ids into `accepted.txt`, then
   `decide2.py m2_p1 accepted.txt` -> `v/<id>/placement.json` (the first
   method's kept as `placement_v1.json`; first method: `decide.py`, which
   voted over match.py's six candidates and graded them with an RGI score).
3. `web_products.py OUT [--skip ...] [--manual tie.json]`: five WebP images per
   video (median, range, variability, sign, edges; the frame's own grid,
   label box transparent) and `georef.json` keyed by video file name.
4. rsync the WebPs to `data/glacier_videos/georef/` (each environment), copy
   georef.json, `manage.py s1_videos georef <georef.json> [--dry-run]` (it
   refuses a video whose images are not there yet).

On the map (`gl_videos.js`): a placed video is a faint dashed box with a
dot at its NW corner; hovering the edge lights it, clicking the edge or dot
opens a menu (the video, or a product); the choice zooms to the box and
draws it in place, directly above the topmost raster (outlines and flow
lines stay on top), with a panel (product, opacity, play/pause + seek for
the video, zoom). A placed video's dot and segment on the glacier path are
left off (Hig, 2026-11-22: one way in per video); unplaced videos keep them.
Column `glacier_videos.georef` (jsonb, `migrate_glaciers`).

## Building S1 timelapses ourselves (2026-11-23)

`tools/glaciers/s1_build/build.py NAME LAT LON [--stage]` makes a monthly
Sentinel-1 timelapse for a frame centred on a point (default 10.6 x 6.7 km,
the median of the placed Earth Engine videos), georeferenced from the start:
no matching. Source: Microsoft Planetary Computer `sentinel-1-rtc` (terrain
corrected GRD, 10 m COGs, anonymous SAS token); only the frame's window is
read from each scene. Rendering repeats Hig's Earth Engine script (monthly
mean in dB, 3x3 Lee, RGB VV [-20,0], VH [-25,-5], VV-VH [1,15]); the one
difference is the source, gamma0 flattened for terrain rather than sigma0,
so slopes facing the radar are less bright. Work dir
`/Volumes/Nunatak/glacier_s1_build/NAME/`: per-scene windows (cached, so a
rerun fetches only new scenes), `stack.npz` (monthly VV/VH in dB, the input
for any further analysis), frames, a clean and a labelled mp4, `meta.json`.
~30 min for ~270 scenes at 8 threads.

**Which pass.** Every direction + relative orbit over the frame is scored:
months from the catalogue, then one scene a year (nearest mid-July) read at
1/10 resolution to check it covers >= 70 % of the frame; the most months in
covered years wins. The candidate table (first/last month, gaps) is printed
and kept in meta.json, so a run forced onto the other direction (`--orbit
desc`, `--rel N`) shows how far it goes. Over the test frame (58.329,
-133.436) descending stops in Dec 2021 (Sentinel-1B); ASF's own archive has
no descending scenes there 2022-2026 either, so that is the acquisition
plan, not the catalogue. A stopped pass can resume (S1C from 2025, S1D from
2026): rerun later and the new scenes come in.

**Onto /glaciers/.** `--stage` copies the clean mp4 + meta.json to
`data/glaciers/s1_build/NAME/`; `manage.py s1_videos built
data/glaciers/s1_build/NAME [--caption ..] [--anchor lon,lat] [--dry-run]`
adds it as a placed `s1` video (georef.method 'built'), anchored
on the nearest flow line in the frame (adopting the RGI centrelines of the
glacier at the centre if there is none). Rerunning it for the same NAME
replaces the file and keeps the video's id. For prod: rsync the staged dir to
the droplet's `data/glaciers/s1_build/` and run the same command there.

No date is burned in: `georef.months[k]` is frame k at `georef.fps`, and
`gl_videos.js` shows the month as the video plays, as a label at the frame's
NW corner on the map, beside the time slider in the panel, and over the video
in the popup.

Products (2026-11-23): build.py computes the same five images as the Earth
Engine videos (median, brightness range, variability, sign persistence,
edges) from its rendered frames, with stack.py's definitions and
web_products.py's rendering, minus registration and the frame check (the
frames are already on the grid); `built` copies them to
`data/glacier_videos/georef/<video stem>_<product>.webp`. In the overlay
panel, Left / Right step one frame while the panel has the focus (any video;
fps from georef.fps, else 10, which is what the Earth Engine exports use).

Open: a layover mask from the viewing geometry (the streaked faces are where
terrain correction smears slopes tilted toward the radar, opposite slopes in
the two directions) and an ascending + descending merge; feature tracking on the stack.

**Months for the Earth Engine timelapses too (2026-11-24).** Their date is
burned in, so the page showed none while they played. `python3
tools/glaciers/s1_videos.py months data/glacier_videos --out
/Volumes/Nunatak/glacier_s1_georef/video_months.json` reads every frame's
label from the SERVED files (dev and prod hold the same file names; only
built videos are not in the main checkout) with the glyph reader; then
`manage.py s1_videos months <that json>` writes `georef.months` / `fps`
for every placed s1 video that is not built (same keys as a built one, so
the panel, map label and popup need nothing new). The exports skip months
(2014-12 then 2015-04), so a frame's month is its own label, never counted
from the first; an unread frame between two read ones is filled only when
the months between match the frames one for one, else null and no month is
shown for it (`gl_videos.frameMonth`). Some exports use a larger font that
matches the glyph templates less well (Ellsworth: every frame at 0.10
against the 0.03 limit), so labels up to 0.15 are taken and a misread is
dropped by order: two labels k frames apart must be at least k months
apart (Ellsworth frame 40, misread 05 as 06, dropped and refilled as
2022-05, checked against the frame extracted by number; note `-ss` before
`-i` lands a frame late, so check by `select=eq(n,K)`). 10,427 of 10,466
labelled frames got a month; every video at least 92%. The json is
`data/glaciers/s1_video_months.json` (dev) for the prod run. Unplaced videos get none (the page
reads `georef` as "placed").

## Ice faces from our S1 timelapses (2026-11-23)

Hig: map only the faces the radar shows clearly; a face every month is not
needed for the story. Tried on Endicott (Dawes) against Hig's four traces:
edges of bright ice (floating ice is bright too), change dates, then
month-to-month texture persistence and offset tracking. Tracking separates
the slow trunk from the fjord, but the last few hundred metres before the
face decorrelate within 12 days at 10 m, which is exactly where the bergs
sit, so it was dropped (Hig). What ships is a confidence score.

**`tools/glaciers/s1_build/faces.py NAME [--line ID] [--min-score 0.6]
[--stage]`** on a build.py stack (method in its docstring; the parts in
`facekit.py`, which also keeps the exploration):
- the flow line: the network's (read from prod's public list), the line
  with most length in the frame followed down its `down_id` chain;
- the channel: what moves (low month-to-month texture correlation), joined
  to the line; rock keeps its speckle, ice and water do not;
- per month two candidates, *consistent* (bright through the months either
  side, not water in > 25% of the past 12) and *single* (this month's
  smoothed image), each the glacier's edge against the fjord inside the
  channel;
- score = fraction of the face that is a > 3 dB bright-to-water step ×
  straightness × reaches both walls × lies within 250 m (along the line) of
  where it crosses it; kept at >= 0.6; then a kept face more than 200 m
  down-fjord of the kept faces on both sides is dropped (winter ice or
  packed melange read as glacier). Endicott: 70 of 120 months, Mar–Apr 2020
  out, Dec 2019 and Jul 2020 in; Hig judged two of the 47 kept since 2021
  questionable (2024-05, 2026-08) and accepted that rate.
- after South Sawyer (Tracy Arm, narrow and full of bergs): a bright
  component counts as glacier only if the line runs along it for 500 m
  (`--connect 50`, px; a berg patch is short where the line crosses it), and
  the single-month candidate also refuses pixels that were water in more
  than 40% of the past 12 months (`--single-wet 0.4`; at 0.25 Endicott's
  winter readvances of 2016–17 went, at 0.55 Tracy Arm's 2022 melange fronts
  came back). Endicott 65 kept, South Sawyer 12 (all on the real front).
  A walk down the line from the trunk ("continuous with the trunk across
  gaps") was tried first and lost Endicott's summer months, when the trunk
  is as dark as water along stretches of the line.
- Sitkagi (debris-covered Malaspina lobe at the coast): the texture test
  calls the slow debris-covered ice rock, so the channel is a few patches
  and the lake-cliff series stops in 2022; `--strip-m 1500` (channel = a
  strip along the line) finds the ocean beach instead. Hig: the lagoon's
  ice cliff is the face, followed to its logical end, "the part that's
  changing" (coastal erosion is a possible later topic). **`--lake`**: the
  lagoon each month (dark water the line crosses, not touching the frame
  edge, i.e. not the sea) and its shore next to pixels that turned from
  ice to water once in the record (`facekit.changing_mask`, the change-date
  step > 3 dB, t > 6); the longest such run of shore is the face, so it
  runs the whole moving arc and stops at stable shore. Scored by the clear
  fraction alone. 102 of 113 months, 2.4 to 3.7 km up the line 2017–2026.
- The spike filter is two-sided: down-fjord spikes first (> 200 m below
  both kept neighbours), then up-glacier ones (> 500 m above both: another
  pond on the line); in the other order a true face next to two false
  down-fjord ones was dropped (South Sawyer 2017-08).

Writes `faces/faces.geojson`, `scores.json`, `kept.jpg`, `position.png`;
`--stage` copies the geojson beside the staged mp4.

**Into the app**: `manage.py s1_videos faces data/glaciers/s1_build/NAME`
loads them as source `s1auto` in `glacier_obs` (sources.py `S1AUTO`: ours,
not mirrored, `fetch: None` so `fetch_glacier_source --all` skips it,
`link: 'given'`). `ext_id = '<build>:<month>'`; a re-run replaces that
build's rows. The glacier is that of the flow line the detector followed,
else of the line most faces cross, else the nearest RGI glacier; `props`
carry month, score, method, along_m, build, video_id. Being a source, the
faces get everything a mirrored terminus has: the year colours, the year
bar, a source switch, the glacier card and the plot.

**Rejecting a face** (site admins and superusers): on the glacier's card,
click face rows of a terminus source to select them (white on the map,
whatever the year bar says; for admins a click there selects instead of
moving the year bar), then *Reject selected*; or *Reject this face* on a
face's map popup, or click its point on the plot. Any terminus row can be
rejected, published ones too. `glacier_face_rejects` (source, ext_id, who,
when) is the record and survives a refetch or a re-run; `glacier_obs.rejected`
is the flag every face query filters on (tiles, card, plot, draft bed,
outline points), re-applied after each load (`apply_rejects`). The extent
stamp is bumped (now in ms), so tiles reload under a new URL. Undo is
offered at once; later, *Rejected faces* on the glacier's card lists them,
select and *Restore selected*. API: `POST /glaciers/api/face/reject/
{ids, undo}` (all or nothing),
`GET /glaciers/api/face/rejected/?glacier=`.

**Face switches**: the top of *Extent sources* has an *Ice faces* bar with
*all on* / *all off* (traced here + every terminus source; outline sources
are not faces and are left alone) and *traced here* (the network editor's
face kind, `LSScarps.setHiddenKinds`; `tf=0` in the URL). The distance plot
follows the same switches and says how many faces are switched off.
Starting an editing session turns traced faces back on.

Also 2026-11-23: card dates read "18 Aug 2003", a whole month "Mar 2020"; a
selected glacier is dropped once the map has moved wholly off it (a stored
view had left Columbia selected, card and `g=`, with the map at Endicott);
the year bar ends at the current year (it stopped at 2025, hiding 2026).

New schema: `manage.py migrate_glaciers` once per environment.
