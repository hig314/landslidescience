# /glaciers/ — from a flow viewer to a glacier-dynamics workbench

Plan written 2026-11-16. Reference for what exists: [docs/GLACIERS.md](docs/GLACIERS.md).
First topic: **retreat histories** — import published multi-epoch extents,
then refine them.

**Status (2026-11-16): phases 0–2 built in dev, not yet tested by Hig, not
pushed.** Decisions D1–D5 are answered (§7). Two things the data changed
after this was written are marked *[changed]* below.

## 0. Refocus, 2026-11-16 (Hig) — read this before §2–§6

**Area is not the target.** It is well-trodden, and for slope stability the
glacier margin matters at a detail where debris-covered ice and ice-cored
moraine make it ambiguous. Two properties are well-defined and matter for
landslides: **calving-face position** (first target) and **glacier surface
elevation** (second). Two consequences Hig stated:

- **Plot the face retreating along a path** (a bay or lake centreline). Face
  ages increase monotonically up-bay whatever the glacier is called at each
  date, which dissolves the Glacier Bay one-ice-mass-into-many problem.
- **The format must accept a line marking the ice face**, never asking for a
  complete polygon.

Proposed changes (not built; open questions at the end):

1. **The organising unit becomes the retreat path, not the glacier.**
   `retreat_paths`: a named line from the outer limit up-bay to (and on up)
   the present glacier. One path per present-day terminus, each running the
   whole way out, so branches share a trunk (Muir Inlet and the West Arm both
   start at the mouth of Glacier Bay) and a face that crosses the trunk
   counts for every path through it. RGI glaciers stay as reference identity
   (which glacier heads a path), no longer the key the history hangs on.
2. **The observation is a dated face line.** `kind = face`: any line along
   the ice front, partial allowed; date bracket; basis (image, map, chart,
   photograph, moraine, account); water (marine / lake); source. Import
   takes line GeoJSON / shapefile / KML with a date field, and the trace
   tool draws them. NSIDC-0634, USGS Glacier Bay and NPS Kenai Fjords are
   already this shape.
3. **Position = where the face meets the path**, as distance along it; a
   line that stops short of the path is projected and flagged as such.
   Polygon sources contribute only that one number (outline ∩ path), marked
   as read from an outline, not a face line. NSIDC-0634 ships its own length
   series, an independent check on our distances.
4. **The main view is a time–distance chart per path**, points by source,
   beside the map of faces coloured by year.
5. **The consensus layer (D1) becomes a consensus face history per path**:
   the merged time–distance series. A younger face down-bay of an older one
   is either an advance (Hubbard, Taku, Johns Hopkins) or an error, so those
   are flagged for an admin, not forced monotonic.
6. **Demoted**: per-glacier area series (old phase 3) dropped; GLIMS outlines
   and the ice-cover layer stay as context for tracing and default to off.
7. **The link to landslides**: project a slope onto the path to read when the
   face passed it, and how long ago.
8. **Surface elevation later, on the same path**: ice-surface profiles along
   it by date (map-era contours, IfSAR, ArcticDEM, lidar) and trimline traces
   for the oldest surface, giving the height of ice lost against a wall. So a
   path is drawn up the glacier, not stopped at today's face.

Open: paths as independent lines with shared trunks (proposed) or one
branching tree; whether lake-terminating faces are in the first pass; who
draws the first paths and for which bays.

### 0.1 The network (Hig, 2026-11-16, superseding item 1 above)

**A flow network like a river network, with feeders**, not independent
paths: lines continue up-glacier (future ice faces land on them), and a
reach is a unit of analysis in its own right, because slopes along one reach
may respond together to a shared thinning signal.

- **Reaches and nodes.** A reach is the line between two junctions (or a
  head, or the outer limit). Each has a downstream reach, an order, a length,
  and a chainage origin at the network's outlet, so any point is
  (network, distance from outlet).
- **What hangs on a reach or a chainage**: face positions through time; ice
  surface elevation profiles by date; later a mean or peak ice speed and bed
  geometry statistics. Landslides attach by projecting onto the nearest reach.
- **Pinned faces.** For a front too wide or complex for a line crossing to
  mean anything (Bering, Malaspina), an observation can carry a hand-set
  chainage on a named reach instead of a computed one. Computed positions
  should not be the bare centreline crossing either: Lea and others (2014)
  found it the worst of the usual methods and the width-averaged
  "extrapolated centre-line" the best.
- **Plots.** Distance along the network on x; y is time (face retreat) or
  elevation (surface profiles, bed, trimlines), with the choice of trunk or
  feeder as the path plotted.

**Canonical centrelines: found for the ice, not beyond it** (web check
2026-11-16).

- **Backbone: the RGI 7.0 centrelines product** (the Kienholz and others
  2014 method as implemented in OGGM, CC BY 4.0, keyed to the RGI 7 ids we
  already use). Main branch and major tributaries with `outflow_id`, an
  order and `is_main`. Region 01 is a 30 MB zip, downloadable without login
  from a Bremen mirror; the NSIDC copy needs Earthdata. OGGM's 2025 re-run
  for RGI 7 (22 MB, inspected): 39,614 segments for 27,509 glaciers, 77% of
  glaciers a single line; Columbia 20 segments, Hubbard 49, Mendenhall 7.
  Smoothed lines and widths along them come with it.
- **Its limits, which are our work**: (1) networks stop at each glacier's
  own outline, so a tributary that RGI counts as a separate glacier is not
  joined to its trunk; (2) nothing continues past the present terminus, and
  OGGM's downstream routine skips tidewater glaciers outright; (3) one
  terminus per glacier, and the method's authors name piedmont lobes as a
  failure case; (4) the "Strahler" field is a depth count, not true
  Strahler order.
- **Not usable**: Kienholz's hand-corrected Alaska lines were never
  published; Zhang and others (2022) is RGI 6 and declared unsuitable for
  tidewater glaciers; no fjord-centreline dataset for Alaska was found; no
  published velocity-derived flowlines for Alaska.

**So: adopt, join, extend.** Adopt the RGI 7 lines; join networks across
glacier boundaries where a tributary glacier flows into a trunk (following
ITS_LIVE flow from the tributary's terminus to the trunk line is the natural
test); extend each outlet down-valley. For the extension Hig's suggestion
fits: where ice is gone there is no velocity, so the path is a simplified
valley geometry (centre of the fjord or lake polygon, or the valley floor of
the DEM), hand-corrected. ITS_LIVE flow paths are also the check on the
adopted lines where the geometric method is weakest (wide lobes, icefields).
The annual 240 m ITS_LIVE fields for all Alaska are already on disk
(`data/glaciers/region/`).

### 0.2 Built (dev, 2026-11-16) and first site

Hig: ITS_LIVE reaches back only to about 2000, so lines past the present ice
are extended **by hand**, which is fine given a good editing tool. First
exploration: **Icy Bay / Taan Fiord**, from historic documentation to modern
observations. Built: reference centrelines, the editable network with derived
junctions, the trace editor mounted on it, adopt / draft / import, face lines
as their own kind, and the distance-time plot (docs/GLACIERS.md). Next for
Icy Bay: correct the four drafted lines, draw the common trunk out to the
bay mouth, enter the pre-1948 faces, and join the arms to the trunk.

## 1. Where /glaciers/ is today

- One page, one purpose: `glaciers.js` (1,771 lines) is the Lagrangian tracer
  engine over ITS_LIVE plus a small floating panel (site picker, basemap,
  four raster overlays, IceBridge bed/thickness points). `pairs.js` and the
  fitted-pair overlays sit behind `experimental_enabled()`.
- No database table, no per-glacier identity, no sidebar or tabs, no click
  target, no editing, no methods page. `noindex`, and `robots.txt` disallows it.
- A "site" is a tracer bundle on disk (Columbia, Grand Plateau, a regional
  tile set). Nothing else knows what a glacier is.
- Shared with the inventory already: `basemaps.js`, `ls_overlays.js`,
  `ls_hash.js`, `ls_proj.js`. Planned but not done: the shared overlay
  framework and the wiper.
- Glacier-extent data on hand: one epoch. The inventory's "Glacier covered
  area 2020" overlay is the 2020 file of NSIDC G10040; the product has 18.

## 2. What it becomes

The inventory is: a table of things with identity, a map that filters and
selects them, a record view, editors who refine, a methods page, an export.
/glaciers/ gets the same skeleton with a glacier as the thing:

| Inventory | Glaciers |
|---|---|
| `landslides` row | `glaciers` row, keyed by RGI 7.0 id *[changed: not the GLIMS id, see §4]* |
| polygons with roles | **extent observations**: dated outlines and terminus lines |
| date bracket on an event | date bracket on every observation |
| external inventories, mirrored verbatim | published extent datasets, mirrored verbatim |
| promote one record | a consensus layer, merged from the sources, that admins improve (decision D1) |
| pending queue, review form | same, per observation |
| `/inventory/<slug>/` | `/glaciers/g/<glacier id>/` |
| `/inventory/methods/`, export | `/glaciers/methods/`, per-glacier GeoJSON |

**Topics are tabs on one glacier identity.** Retreat history is the first;
flow (the tracers) becomes the second, loaded only when its tab is opened;
thickness/bed (IceBridge), thinning (Hugonnet) and ice-marginal lakes follow
as their data are attached to the same ids. The pair stack stays where it is,
behind the experimental gate.

**The time bar is the shared control.** It exists for the tracers; the retreat
tab drives it over a longer range (1700 to now) and it selects which epoch is
drawn. One time control, two consumers.

## 3. Data sources (checked on the web 2026-11-16)

Ranked for first import. "Lines" = terminus positions, "polys" = outlines.

| # | Source | What | Coverage | Access | Terms |
|---|---|---|---|---|---|
| 1 | **GLIMS database** | polys, one per glacier per analysis | AK + NW Canada bbox: 63,553 outlines, 35,744 glacier ids, 10,070 glaciers with ≥2 epochs, 2,174 with ≥5; epochs 1770, 1948–57, 1970s, 1986, 1990, 1999, 2005, 2010, 2015, 2019 | open WFS `https://www.glims.org/geoserver/ows`, layer `GLIMS:GLIMS_Glacier_Outlines`, GeoJSON by bbox, no login | "must cite"; no named licence |
| 2 | **USGS Glacier Bay termini** (Avdievitch, Bliss, Coe 2023, doi:10.5066/P9WDA76U) | 233 dated lines, 1700–2019, with source and comment fields | Glacier Bay | ScienceBase zip, 192 KB | CC0 |
| 3 | **McNabb & Hock, NSIDC-0634** | >10,000 terminus lines + length series, 1948–2012 | 50 Alaska tidewater glaciers | Earthdata login, 6.8 MB, one shapefile per glacier | cite |
| 4 | **Roberts-Pierel, NSIDC G10040** | automated statewide ice cover, even years 1986–2020 (18 epochs), debris split | Alaska only | open HTTPS, ~60 MB per epoch; also an NPS FeatureServer | cite |
| 5 | **Bevington & Menounos 2022** (Zenodo 5900363) | automated annual outlines 1984–2021 | BC + Alberta | one 5 GB GeoPackage | CC BY 4.0 |
| 6 | **RGI 7.0** regions 01, 02 | single epoch (~2000); carries `glims_id`, RGI 6 links, centerlines, glacier complexes | global | same GLIMS WFS, no login | cite |
| 7 | **USGS benchmark glaciers** (doi:10.5066/P9R8BP3K) | historic + current margins since the 1940s | Gulkana, Wolverine, Lemon Creek and others | ScienceBase, 13.6 MB | CC0 |
| 8 | **WGMS front variations** | length-change numbers, no geometry | 136 series in the bbox; Columbia from 1899 | 868 MB zip of CSVs | cite |
| 9 | **LIA outlines**: Carrivick 2026 (5,289 LIA glaciers), Reinthaler & Paul 2023 | polys | Alaska | article supplement; Zenodo 8113575 | CC BY |
| 10 | **NPS Kenai Fjords termini** | lines for 19 glaciers, 1984–2021, with centerlines | Kenai Fjords | IRMA file geodatabase | not stated |
| — | Rick 2022 ice-marginal lakes | polys, four epochs 1984–2019 | Alaska | Arctic Data Center | CC BY 4.0 |

What GLIMS already contains, so it need not be imported separately: Kienholz
2015 (the RGI base), the Juneau Icefield series 1770–2019 (Newcastle), western
Prince William Sound 1950s–2018 (Maraldo), Kenai (Oslo), map-era 1948–57
outlines, a 2019 Forest Service Chugach set.

Not usable or not found: TermPicks, CALFIN, AutoTerm (Greenland only); Bolch
1985/2000 epochs, Yukon polygons (Barrand & Sharp), the Loso NPS geodatabase,
Glacier Bay isochrones other than the USGS compilation (no download located);
PP 1386-K, Field, Post & Meier (narrative only, no GIS).

**Things the sources will do to us**
- **Divides differ between submissions.** Columbia is 936, 944 and 774 km²
  under one GLIMS id. An area series across submissions is not a retreat
  signal. Terminus position along a flowline is robust to this, so length is
  the first derived quantity and area comes second, computed inside a fixed
  basin (the RGI 7 glacier or complex).
- **Dates are soft.** RGI/GLIMS `src_date` is per glacier and sometimes a
  nominal mid-year; Glacier Bay is year-only; LIA is "about 1770". Every
  observation needs a bracket, not a date.
- **Ids are uneven.** GLIMS id is the stable key; RGI 7 ids may change again;
  0634 and Glacier Bay carry names only; G10040 and (unchecked) Bevington
  carry no per-glacier id. LIA glaciers map one-to-many onto modern ones.
- **The WFS drops fields** (`analysts`, `proc_desc`, `anlys_time`). The full
  download has them but sits behind Earthdata login. Mirror the WFS first;
  enrich from the login download later.
- **Editing rights.** CC0 and CC BY sources are clear. GLIMS, RGI, 0634 and
  G10040 say only "cite". Publisher records are shown verbatim and our
  refinements are separate records naming their parent, which is the
  defensible shape; still worth an email to GLIMS before the refinements go
  public (decision D4).

## 4. Data model

PostGIS in `tethys_db`, raw psycopg2, `migrate_glaciers` run once per
environment, as for landslides.

- **`glaciers`** — `id` (key; the RGI 7.0 id today), `glims_id`, `name`,
  reference outline (RGI 7), `term_type`, every RGI attribute in `props`.
  Seeded from RGI 7.0 region 01. *[changed]* **The key is not the GLIMS id**:
  RGI 7.0 gives one GLIMS id to as many as eight separate glaciers (135 ids
  shared among 323 of the 27,509), so it cannot identify a row. It stays as
  an attribute and is the first thing a GLIMS outline is matched on when it
  names exactly one glacier. Only 661 of the 27,509 carry a name, so search
  by name is thin until names are brought in from elsewhere (GNIS, GLIMS).
  Name-only sources are matched by name then by position; unmatched rows stay
  unmatched and are shown, never guessed.
- **`glacier_links`** — many-to-many between glaciers (LIA parent → modern
  children, complex → members, RGI 6 ↔ 7).
- **Mirrors** — `glacier_obs`, one row per published feature, the publisher's
  attributes untouched in `props`, replaced whole per source on refetch
  inside a transaction, count-checked (the rules of `inventory/external.py`).
  Beside `props` sit our reading of the row: which glacier, what kind
  (outline / debris / terminus), the date bracket. Registry: `glaciers/sources.py`.
  *[changed]* One table with a `source` column instead of a table per source:
  every source reduces to the same few derived columns, and the map draws
  them from one tile layer.
- **`glacier_extents`** (ours; the **consensus layer**, phase 4) — seeded by a
  best-guess merge of the sources, then improved by admins. One row per
  observation:
  `glacier_id`, `kind` (outline, terminus, debris, LIA limit), geometry,
  `date_min` / `date_max`, `date_basis` (image, map, moraine, account),
  `image_ref` (scene id or basemap), `method`, `source` + `source_ref` (the
  publisher's own ids, e.g. `glac_id`+`anlys_id`, since mirror row ids do not
  survive a refetch), `parent_extent_id` (what it refines), `status`
  (pending / reviewed), `superseded_at`, `notes`, `created_by`. Field names
  follow GLIMS where one exists, so a series can be offered back to GLIMS.
- **`glacier_flowlines`** — the reference line retreat is measured along.
  Seeded from RGI 7 centerlines, which stop at the ~2000 terminus, so editors
  extend them down-valley for earlier positions (trace editor).
- **Derived, not stored by hand:** length-vs-time (terminus ∩ flowline) and
  area-vs-time inside the fixed basin, each point carrying its date bracket
  and source.

Edit history goes in SQLite beside `LandslideEditMeta`, keeping the Tethys
schema clean.

## 5. Interface

- **Sidebar with tabs**, matching the inventory's chrome: *Glaciers* (search,
  filter by region / terminus type / number of epochs), *Retreat*, *Flow*,
  *Overlays*.
- **Map**: glaciers drawn from the reference outline; click selects one.
  Selected glacier shows all its observations coloured by year (ramp in
  `ls_colors.js`), terminus lines on top; the time bar steps through epochs.
- **Glacier card**: names and ids, links out (GLIMS, WGMS), the length and
  area charts with bracket whiskers, the list of observations by source with
  show/hide, and a download.
- **Background time layers** (G10040, Bevington): statewide ice cover by
  year as PMTiles on R2, driven by the same time bar. These are the "what the
  ice looked like that year" context and the starting material for a trace,
  not records.
- **Refining** (editors): adopt a source's series for one glacier; move an
  outline's vertices; trace a new terminus on dated imagery; set the date
  bracket and basis; mark superseded. Lines reuse `scarps.js`, polygons reuse
  the inventory's Terra Draw editing, pointer ownership through `ls_tools.js`.
  Imagery to trace on is already here: AHAP 1978–86, USGS historical topo, BC
  orthophotos, Sentinel fetches and GeoTIFF uploads (`trace_rasters.js`).
- **Landform traces as a reference layer** (an open thread since October):
  moraine crests and trimlines are the field evidence for the oldest
  positions, and a trace group can be cited as the basis of an LIA limit.
- **URL state** through `ls_hash.js`; existing /glaciers/ hash keys keep their
  meaning (additive only).

## 6. Phases

Each ends in something testable in dev, and ships only on sign-off.

**0. Make room (no visible change).** Split `glaciers.js` into a page shell
and `gl_tracers.js`, behaviour-identical, tracers lazy-loaded. Decisions D1–D5.

**1. Identity and the first imports (read-only).** `glaciers` table from
RGI 7; `fetch_glacier_source` for GLIMS (AK + NW Canada bbox), the USGS
Glacier Bay lines, NSIDC-0634 and the USGS benchmark margins; matching report
for the name-only sources. Sidebar, glacier search, click → card listing
epochs by source, time bar over the selected glacier's observations.
*Test: Columbia, Muir, Mendenhall, Grewingk, Portage each show a history.*

**2. Statewide time layers.** All 18 G10040 epochs (and the debris split) as
PMTiles on R2 with the time bar; Bevington for BC/Alberta once its attributes
are inspected. The inventory's 2020 overlay row is unchanged.

**3. Derived series.** Flowlines from RGI 7 centerlines, length and area
charts with brackets, WGMS front-variation numbers plotted beside ours as an
independent check, per-glacier GeoJSON download.

**4. Refinement.** `glacier_extents`, adopt, vertex edit, new dated terminus
trace, flowline extension, pending queue and review, edit history,
`check_invariants` entries for the kind vocabulary and colour ramp.
*This is the phase where Hig's own histories start to exist.*

**5. Publish.** `/glaciers/methods/`, attribution per source, lift `noindex`
and the robots block, deep links.

**Later, on the same ids:** LIA outlines and the one-to-many links; ice-marginal
lakes (Rick 2022) beside retreat; thickness/bed and flotation; per-landslide
"ice-free since" read from the retreat history at a slope, which is the input
the inventory wants for debuttressing.

## 7. Decisions for Hig

Answered by Hig, 2026-11-16.

- **D1. How source data becomes ours.** Not adoption glacier by glacier.
  The sources are layers that can be switched on and off; beside them sits a
  **provisional consensus layer, a best-guess merge of everything available,
  which an admin then improves**, and which is actively improved over time.
  So phase 4 starts with a merge rule (which source wins for a given glacier
  and epoch, how duplicates across submissions collapse, how a terminus line
  and an outline of the same year reconcile) run over the whole mirror, not
  with an adopt button. The rule has to be re-runnable when a source is
  refetched without overwriting what an admin changed: a consensus row
  records whether it is still as merged or has been edited.
- **D2. Scope.** Alaska first (RGI region 01, which includes the Canadian
  side of the St Elias and Coast Mountains). Yukon and BC later.
- **D3. Who edits.** `inventory_editors`, no new group.
- **D4. Terms.** These are public scientific data; cite them as such, no
  permission request. Every source carries its citation and DOI in the
  registry and shows them in the page. DOIs on file: GLIMS 10.7265/N5V98602;
  RGI 7.0 10.5067/F6JMOVY5NAVZ; NSIDC-0634 10.5067/3OOVUDFK3R0W; G10040
  10.7265/8esq-w553; USGS Glacier Bay 10.5066/P9WDA76U; USGS benchmark
  10.5066/P9R8BP3K; Bevington & Menounos 10.5281/zenodo.5900363; WGMS FoG
  10.5904/wgms-fog-2026-02-10; Reinthaler & Paul 10.5281/zenodo.8113575;
  Rick 2022 10.18739/A2MK6591G; Carrivick 2026 by its article DOI
  10.1038/s41598-026-70307-w. **No DOI found** for the NPS Kenai Fjords
  termini (IRMA reference 2293309 only).
- **D5. Earthdata-login sources**: fetched on the workstation
  (`tools/glaciers/download_sources.sh`, `~/.netrc`), converted there, loaded
  per environment from `data/glaciers/sources/`. Nothing on the droplet holds
  the credential.

## 8. Found while building (2026-11-16)

- **G10040 carries a glacier id.** Every polygon has `RGIId` (RGI 6.0) and
  `GN_area`, that year's area. So a per-glacier area series every two years
  1986–2020 is one table join away (phase 3); it needs the RGI 6→7 links.
- **The former-ice problem is real in Glacier Bay.** Of the 233 USGS lines, 82
  match no glacier: they are positions of the single Glacier Bay ice mass, far
  down-bay of anything in RGI. Muir's own card starts in 1980. Showing a
  glacier its ancestors' termini needs the many-to-many links of §4.
- **Matching the 50 NSIDC-0634 files**: 48 land on the glacier of the same
  name. `cascade` lands on an RGI row named Barry Glacier and `moraine_apron`
  on Grotto; both want a look.
- **One NSIDC-0634 file has no projection** (`yakutat/turner`); its `proj`
  attribute supplies the zone.

## 9. Not yet verified

Bevington's attribute list (whether it carries glacier ids); which years the
USGS benchmark margins cover; the identity of several GLIMS submissions
(inferred from affiliation and dates); whether the Carrivick LIA supplement
is downloadable as stated; the WGMS 2026 table layout; how heavy the full
GLIMS geometry is for the bbox (only attributes were pulled), which decides
between vector tiles from PostGIS and PMTiles built at fetch time.
