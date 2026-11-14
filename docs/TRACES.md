# Landform traces, lake soundings, faults layer

Traced lines (scarps, moraines…), groups and the group profile; sparse soundings; the three-source faults layer.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## Landform traces — traced observations (public reads, editor-only writes)

**Generalised 2026-10-11** (Hig) from scarps to every hand-traced line: a
moraine crest and a scarp are the same thing as a tool. Two columns added by
re-running `migrate_fault_scarps` (idempotent; **run it right after the deploy
that ships them** — the list query SELECTs both, so `api/scarps/` 500s until
then): `kind` (text, default `'scarp'`, so every earlier row is a scarp) and
`traced_on` (jsonb `{page, surveys[], basemap}` — which surfaces the line was
read from, shape-checked on the way in by `_clean_traced_on`; NULL for rows
traced before). **The vocabulary is `TRACE_KINDS` in `inventory/scarps.py`**
(id, label, colour, dash, hint) and rides the list response as a GeoJSON
foreign member `kinds`, so the client builds its styles, filters and pickers
from it: adding a kind is one entry there, never a schema change; never rename
an id rows use. `?kind=a,b` filters the list; create and update take `kind`.
`scarps.js` draws a white casing under every trace, one line layer per dash
style (data-driven `line-dasharray` is not in MapLibre 5.5), colour by kind,
and offers `setHiddenKinds`, `setDrawKind`, `save({notes, kind})`, `raise()`.

**Groups** (same day; Hig: outputs specific to one glaciation / fault / lake
stand). `trace_groups` (PostGIS, same migration): `name` (unique ignoring
case — `trace_groups_name_key` on `lower(name)` — so a typo cannot split a
group), `notes` (age, interpretation, citation), `created_by`. **One group per
trace** (`fault_scarps.group_id`, FK `ON DELETE SET NULL`: deleting a group
ungroups its traces, never deletes them). **A group has no kind** — one
glaciation holds moraines, trimlines and strandlines together. The list
response carries every group with its count as the foreign member `groups`,
and each trace its `group_id` + `group` name. `?group=<id>` filters (and then
carries the group record as `group`); `&download=1` makes it a
`traces_<slug>.geojson` attachment (`application/geo+json`) — **the per-group
output**, today the only one. Writes: `api/scarps/groups/create/` and
`api/scarps/groups/<id>/` (rename / re-note / `{"delete": true}`), editors
only; traces take `group_id` on create and update. On `/lidar/`: the sidebar's
*Show* selector filters the map to one group and shows its note, count and
download (editors: *edit group*); the trace panel picks the group for new lines
and for the selected trace, with an inline **＋ New group…**.

**Editing in the session** (`scarps.js`, shared by both pages). With no
line in progress a click decides its own meaning: **on a trace it selects it**
(vertex + midpoint handles appear), **on open ground it deselects and starts a
new line**. Drag a vertex to move it, drag a midpoint to insert one, right-click
a vertex to delete it, Ctrl/⌘-Z undoes (per session, 50 deep), Esc deselects;
every change POSTs the geometry on release and reverts on failure. Mechanism:
Terra Draw listens for pointer events on the map **canvas** and takes a
non-drag pointerup as a click, so the editor's listeners sit on the map
**container** in the capture phase, `stopPropagation` + `preventDefault` (the
latter suppresses the compatibility mouse events MapLibre pans on) and
`setPointerCapture` for any gesture on a trace or handle; open-ground
gestures pass through untouched, and so does anything whose target is not the
canvas (markers, popups). **Mid-line, a secondary click (right-click,
Ctrl-click on a Mac) ends the line and saves it** — the point clicked becomes
the last vertex, or on a vertex already drawn the line ends as it is
(`finishSecondary`: it takes the click from Terra Draw, ends Terra Draw's line
by switching modes, and saves through the same `saveNew` as Terra Draw's own
finish; the contextmenu that follows is swallowed, not a coordinate copy).
Terra Draw alone ends a line only on a click within `pointerDistance` of its
last vertex. **Choosing a group for new lines takes the kind of the group's
most recent trace** (a group nearly always holds one kind; the kind can still
be changed after). "Line in progress" = a LineString in Terra
Draw's snapshot not yet seen by its `finish` event (it has no flag of its own).

**Group profile** (`trace_profile.js`, `LSTraceProfile`, on `/lidar/`; built
2026-10-11 for moraine crests). *∿ profile* beside a group in the sidebar:
every member sampled along its length (step max(1, min(20, length/300)) m)
and projected onto one vertical plane. **The plane** is the direction of
greatest horizontal extent — the farthest pair of the group's vertices
(convex hull, then all pairs; Hig's choice over a principal axis) — placed at
the **middle of the members' offset range** (a mean of vertex offsets was
tried and leans toward whichever trace has more vertices), spanning their
projected extent A→A′; drawn on the map (`tgp-*` layers, A/A′ HTML markers)
only while the panel is open; Flip swaps the ends. Distances in a local
equirectangular frame about the group's centre. **Each sample reads the first
surface with data**: the trace's `traced_on` surveys, the page's active and
difference surveys, other covering surveys finest first, then Mapterhorn
(footprint test is its own ray-cast — `/lidar/` loads no turf). **A group is
one series** — one colour, one legend line, one row of the window table,
however many traces (Hig); the code takes a list of groups so several can
later share one plane in distinct colours (the plane is then built from all
their members). **A and A′ are draggable** (2026-10-11): the plane becomes
the line through the two ends as placed, spanning them and the members;
dragging re-projects the samples already read (live, no re-read) and *Auto
plane* returns to the farthest pair; URL `gpa=latA,lonA,latB,lonB`. **The
profile follows the traces**: the page calls `LSTraceProfile.notify()` on
every `LSScarps` change, and it re-reads (after 600 ms) when the open groups'
members differ from what it drew. Hover picks the nearest sample and marks it and its foot on
the plane; the brushed window's slope is the group's trend along the plane
(for moraine crests, the former ice-surface gradient). CSV: group, trace,
along-plane distance, signed offset, lon/lat, z, surface; `#` lines carry
A/A′, azimuth, length and the window. URL `gp=<id>[,<id>…]`, `gpf=1`,
`gpl=1`, `gpw=d0,d1`; a cold `gp=` waits for the catalogue. **The panel and
chart are shared with the inventory's ∿ line profile** (2026-10-11):
`profile_chart.js` (`LSProfileChart` — panel DOM, 1:1/VE, legend, column or
nearest-sample hover, brushed window + stats, SVG/CSV, the map cursor paint)
inside a `.float-panel` from **`ls_float.js`** (`LSFloat.make`, extracted from
map.js's `makeFloatingPanel`, which now wraps it to add the hash rewrite),
styled by **`ls_panels.css`** (moved out of main.css; base.html and `/lidar/`
both load it). Each tool keeps only its sampling, map layers and URL state. **The readers moved to `dem_read.js`
(`LSDemRead`) the same day** — decode, tile cache, bilinear read, zoom choice,
one PMTiles reader per archive, the Mapterhorn source — and `profile.js` (the
∿ elevation profile and the Alt-click probe on `/inventory/`) now uses it;
`home.html` loads it before `profile.js`. `_test` exposes the plane geometry
for headless checks.

**Where tracing happens.** `/lidar/` is the main tracing page (sun angle,
slope, 3D, Nadir): an `LSTools` mode `trace` (✎, top-right, editors only), a
floating `#trace-panel` (kind for new lines, the selected trace's kind and
note, delete, Exit), and for everyone a sidebar *Traces* section (show/hide =
`trv=` in the query string; a per-kind filter with counts) and a popup on
click — editors get an **Edit** button there, which opens the session with
that trace selected (in a session, clicks belong to Terra Draw). The page
wraps `map.addLayer` so every layer it adds lands **below** the traces —
surveys and shading come and go all session. `/inventory/`'s pencil still
traces scarps only (kind `scarp`, with `traced_on`); it shows every kind,
labelled; its pinned-strip box now reads **Traces**. Whether the inventory
should trace other kinds (e.g. mapping a slide's own scarps) is open (Hig,
2026-10-11: decide when it comes up).

Say **scarp**, not fault, in anything user-facing: many are of ambiguous or multiple origin (Hig, 2026-09-20). The PostGIS table and management command keep the original `fault_scarps` name.

Mapping landslides on the lidar surfaces turns up scarps that are **not** landslide headwalls: straight breaks cutting across drainage, uphill-facing steps, lineaments that look tectonic. They belong with the Alaska active faults and folds database rather than the landslide inventory, and there was nowhere to record one at the moment it was noticed — which in practice means it was lost.

`fault_scarps` (PostGIS, created by `python manage.py migrate_fault_scarps`, idempotent): `geom` MultiLineString/4326, `notes` text, `traced_by` text, `created_at`/`updated_at`. `traced_by` is the **Django username as text, not a foreign key** — the auth tables live in the app's SQLite database and PostGIS is separate, so a real FK is not available across the two.

- **Reads are public, writes are editor-only** (`inventory/scarps.py`; `api_scarps` is open, create/update behind `inventory_editor_required`). The first cut kept reads editor-only on the argument that a half-formed "possible scarp?" reads as a claim; Hig decided (2026-09-20) the traces are shown to everyone by default, labelled in the panel as working observations, not a fault map. Everyone gets the layer, the show/hide toggle and the read-only note; only editors get trace/save/delete. A `published` column is still the route if one ever needs hiding.
- Endpoints: `api/scarps/` (list, optional `?bbox=`; **optional on purpose**, a forgotten bbox returning 400 was a real trap in the reviser), `api/scarps/create/`, `api/scarps/<id>/` (notes and/or geometry; `{"delete":true}` removes). Writes go through `_SCARP_GEOM_EXPR`, mirroring the polygons' `_GEOM_WRITE_EXPR` but with `ST_CollectionExtract(…, 2)` for lines, so the column stays MultiLineString whatever the client sends. Lines under 5 m are refused with their measured length; a polygon is refused as "not a line".
- Client `inventory/static/inventory/js/scarps.js` (layers, Terra Draw session, API) + `map.js` (`_scarpInit`, `_scarpOpenPanel`, `_scarpShowPopup`). **No Analysis entry and no card**: same pattern and placement as the landslide draw tool. **Tracing starts from the ✏ pencil**, which asks *Landslide polygons* or *Scarp traces* (`DrawModeControl.choose()` / `activate(kind)`): both kinds are the one `LSTools` mode `draw`, so the polygon tool, the tracer, measure and InSAR are mutually exclusive, and the reviser is checked by hand on the way in (it is not a tool-group mode). In a session a bottom-right `.inv-draw-panel` holds the count, the selected trace's note (save/delete) and Exit; draw → saves immediately and selects itself → write the note. Outside a session, for everyone, clicking a line opens a popup with the note and who traced it. Purple dashed lines with a wide transparent hit layer (a 2 px click target is not usable).
- **`pointerDistance: 8` is load-bearing**, same as the polygon tool: Terra Draw's default of 40 px means a click within 40 px of the previous vertex *finishes* the line, so at normal tracing spacing you get a string of single segments instead of a path. `Enter` finishes, `Escape` cancels, and double-click zoom is disabled while tracing (double-click also finishes).

Deliberately thin. Certainty, sense of motion, or a link to a USGS fault id are `ALTER TABLE ADD COLUMN` away, which is why the migration is written to be re-run.

## Lake soundings — sparse depths (deployed 2026-10-12)

Scattered lake and nearshore depths (handheld sounder, lead line, digitised
ADF&G contour maps) that are too sparse to be a raster survey. PostGIS tables
`sounding_surveys` / `sounding_levels` / `soundings` / `sounding_contours`
and view `soundings_z` (`migrate_soundings`, idempotent); loaded whole per
survey by `load_soundings <package>` from a package a per-source ingest script
writes (`tools/soundings/`); `export_soundings` writes the dated x,y,z,sigma
CSV the topobathy composites read. **Depth is stored, elevation derived**
(`z = level − depth`, the level a row of its own with source and sigma), and
**wrong rows are flagged `rejected`, never deleted**. Reads at
`/inventory/api/soundings/` — public surveys for everyone, `public = false`
ones only for `can_view_restricted` accounts; no write endpoints yet. Shown by
`soundings.js` on `/lidar/` (Soundings) and `/inventory/` (Reference layers →
Lake soundings, `ref=soundings`), and as dots with ±σ on the ∿ profile
(`marker` series in `profile_chart.js`). Workflow and the contour-map plan:
**[tools/soundings/README.md](../tools/soundings/README.md)**. First survey:
Nick Riordan's 2018 KBSP lakes (574 soundings, public).

## Faults & folds reference layer (three sources, one layer)

`inventory/static/inventory/ak_qff.geojson`, `ak_adem2021.geojson`, `ak_nshm2023.geojson` are **display copies built by `tools/faults/build_faults.py`** — edit the script, never the files. Sources: DGGS DDS 3 (Koehler 2013; pristine copy in `tools/faults/src/`), USGS *Alaska Fault Trace Mapping 2021* (Bender & Haeussler, 648 traces at 1:10,000 on ArcticDEM, interior/western AK) and the *NSHM 2023 Alaska fault sections* v2.0 (Bender, Haeussler & Powers, 105 simplified model lines with rate/dip/rake — the only GIS with the Susitna basin scarps of Haeussler et al. 2017). The two USGS files download from ScienceBase into `data/faults_src/` on first run.

- One map source/layer (`faults` / `faults-line`), features tagged `_src` ∈ `qff` (magenta) / `adem2021` (orange) / `nshm2023` (blue); the popup switches schema on `_src`. Same in `_polygon_map.html`.
- **`DEPRECATED=true` pieces are hidden by the layer filter, not deleted** (Hig, 2026-09-20): the four DGGS seismic-zone ovals (Minto Flats, Fairbanks, Salcha, Rampart), and every stretch where a coarser line duplicates a finer one from *another* source — the rule is "same structure (names share a word) within 2.5 km, vertex spacing ≤ 0.65× mine ⇒ I am the generalisation here". Castle Mountain and Totschunda lose their NSHM model lines where DGGS is detailed; the Denali fault NW of Haines loses its DGGS inferred lines where NSHM is finer. Lines are split at the boundary, so the un-duplicated remainder stays live. The 2021 traces are never deprecated. `--report` prints every deprecated piece with what covers it; `DEPRECATED_WHY` / `DEPRECATED_BY` say so in the file.
- Not deprecated on purpose: same-source neighbours (two DGGS strands), and pairs whose names share nothing (Queen Charlotte vs. Fairweather, Cape Cleare vs. Patton Bay) — both draw.
