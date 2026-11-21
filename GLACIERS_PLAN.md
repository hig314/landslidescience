# /glaciers/ — calving-face histories on a flow network

Rewritten 2026-11-20 around the flow network. The first version (2026-11-16)
planned per-glacier area and outline histories. Hig refocused the same day,
and that plan is in git history (`git log -- GLACIERS_PLAN.md`). How the
built pieces work is in [docs/GLACIERS.md](docs/GLACIERS.md); this file holds
the aim, the decisions, and what comes next.

## 1. The aim

**Two quantities, measured along a network of flow lines.** Calving-face
position comes first. Glacier surface elevation is second. Both matter for
landslides: a face retreating past a slope removes its buttress, and a
surface thinning against a valley wall unloads it. **Area is not the
target.** It is well studied already, and the margin is ambiguous at the
detail slope stability needs (debris-covered ice, ice-cored moraine).

- **The network works like a river network.** Flow lines with feeders run
  from the heads down through today's ice and on past the present face, down
  the fjord or lake to the outer limit. A reach is the line between two
  junctions, and it is a unit of analysis in its own right: slopes along one
  reach may respond together to one thinning signal.
- **A face is a dated line along the ice front.** It can be partial, and no
  polygon is asked for. Its position is a distance along the path.
- **The main view plots time against distance along a chosen path** (one
  line followed down to its outlet), with points by source. The map beside it
  shows the faces coloured by year.
- **Face ages increase steadily up-bay**, whatever the glacier was called at
  each date. That gets round the Glacier Bay problem, where one ice mass split
  into many named glaciers.
- **The link to landslides**: project a slope onto the network to read when
  the face passed it, and the ice surface beside it through time.

## 2. Where it stands (2026-11-20)

**Prod is the working copy** (Hig, 2026-11-20). Hig has been building
networks there since the 2026-11-18 deploy: 352 flow lines and 9 faces on
2026-11-20, up from 89 and 8. Dev still holds the copy from that night, so
**refresh dev's glacier tables from prod before testing glacier work in dev**
(the reverse of the COPY the deploy used: docs/STATE.md).

Built and deployed (plumbing in docs/GLACIERS.md):

- **Identity and context.** RGI 7.0 region 01 (27,509 glaciers) is the
  identity table. GLIMS, NSIDC-0634, USGS Glacier Bay and USGS benchmark are
  mirrored in `glacier_obs` (79,372 rows). The G10040 ice cover for 18 epochs
  is one bit-mask raster.
- **The network.** RGI 7.0 centrelines are the reference. The edited lines
  are in `glacier_lines`. A join is a shared coordinate; touching ends fuse;
  a line that starts on another is a distributary. The network gets lines by
  adopting RGI centrelines, drafting the former bed, drawing, or importing.
- **Faces.** They can be traced on any reference map (lidar, AHAP, uploads,
  Sentinel) with date, basis and source set first, or imported as GeoJSON.
  Typed dates are read by `landslidescience/dates.py`. Position is
  width-averaged (the mean of the face's vertices along the reach, after Lea
  and others 2014). A face that misses every line is projected onto one line
  within 1.5 km.
- **The plot.** Time runs across. Distance runs up, measured down-flow from
  the line's head. A distributary's path runs up its parent to the head.
  Videos (Planet stories, uploaded Sentinel-1) sit on segments and show as
  boxes on the plot.
- **Check against a publisher.** At Icy Bay, McNabb & Hock's own length plus
  our distance stays nearly constant. The NMAD is 0.04 to 0.38 km, depending
  on the glacier.

### 2.1 What the plot reads, and the Portage gap

Hig found this on prod on 2026-11-20: Portage shows many fronts on the map
but one point on the plot. **This is not an association problem.** The plot
reads only line faces: faces we traced, plus terminus lines from the mirrored
sources (NSIDC-0634, USGS Glacier Bay). Portage is in neither source. Its
fronts on the map are 17 GLIMS outlines, 1951 to 2019 (Wisconsin-Milwaukee,
Oslo, Zurich, Colorado, USFS), and the plot never reads outlines. The one
point is Hig's 1850 face (line 378). The first plan meant outlines to
contribute one number each (outline ∩ path, §3.1). It was never built, and
Portage is the first glacier with no terminus-line source to show the gap.

## 3. Next work, in recommended order

1. **Outline crossings on the plot** (the Portage fix). *Built in dev
   2026-11-20 (docs/GLACIERS.md, last section); the rules below are as
   built.* For each outline
   linked to a glacier whose line is on the path, plot one position: the
   furthest-down-flow point where the path leaves the polygon. Mark it `how =
   outline` with its own symbol, so it is never mistaken for a traced face.
   Rules to settle while building:
   - only outlines of glaciers on the path (by `glacier_id`), never a
     neighbour's;
   - skip an outline whose exit lies on a divide or the head rather than the
     front. The test: the exit is in the lower part of the line, or near the
     RGI terminus;
   - plot every submission, not one per date. Two submissions of the same
     date that disagree are themselves information;
   - debris-covered fronts (the GLIMS `debris` rows) are not faces. Leave
     them out at first.
   G10040 could later add one point per even year 1986–2020, read from the
   bit mask along the path. That needs the raster read on the server, and
   the container has no GDAL, so it is a second step.
2. **Load network lines by map view.** At 352 lines the list
   (`lines/api/scarps/`) is 816 KB, but 190 KB as sent (gzip), about 0.5 KB
   a line, loaded by every visitor. Not urgent yet (checked 2026-11-20). The
   change is in `scarps.js`, which is `main`'s (WORKSTREAMS.md), and it touches
   snapping and the in-memory edit state, so do it there when the list nears
   1 MB as sent. Do not trim the coordinate precision instead: joins rely on
   the 2 m vertex rule at 6 decimals.
3. **Pin a face.** The schema and the maths are in (`pin_line_id`,
   `pin_frac`); the control to set a pin is not. This is for fronts too wide
   for a crossing to mean anything (Bering, Malaspina).
4. **Present-terminus marker.** Fusing an RGI line with a drawn extension
   lost the record of where the RGI part ended, so the plot cannot shade
   "glacier today" against "drawn beyond". Store the point when a line is
   adopted (or take it from the RGI outline) and draw it on the plot.
5. **Split-line and simplify-line buttons.** `split_line` exists on the
   server. Simplify is new; it is for drawn or drafted lines with too many
   vertices.
6. **Joins across glaciers.** Join a tributary glacier's network to its
   trunk by following ITS_LIVE flow from the tributary's terminus. This also
   checks adopted lines where RGI's geometry is weakest (wide lobes,
   icefields). The annual 240 m fields are on disk (`data/glaciers/region/`).
7. **Consensus face series** (D1): every point kept (Q2); review comes
   later.
8. **Surface elevation on the y axis.** Ice-surface profiles along a path by
   date, and trimline traces for the oldest surface; the USGS map-derived
   DEMs first among the old ones (Q1).
9. **The landslide link.** For a slope in the inventory, the nearest reach,
   when the face passed, and how long it has been ice-free. This is the
   debuttressing input the inventory wants.
10. **Loose ends.** Byte ranges for Planet mp4s (`/inventory/planet/`,
    `main`'s file). A retry for a failed Drive fetch. A video upload larger
    than gunicorn's 30 s allows has never been tried on prod.
11. **Publish.** `/glaciers/methods/`, citations per source on the page,
    lift `noindex` and the robots block.

## 4. Questions for Hig

- **Q1. Which DEMs to trust before 2000?** *Answered (Hig, 2026-11-20):* DEMs
  are generally robust on glacier surfaces; the trouble with old ones is that
  their dates are often unclear. **Include the DEMs rasterised from the
  mid-20th-century USGS topographic maps.** So each surface profile carries a
  date bracket like a face, and for a map-derived DEM the bracket comes from
  the map's photography dates (on the quad), not the DEM's production date.
  Still to find: where the map-derived DEMs for Alaska are served, and how
  to read each quad's photo date. Candidates after that: AHAP (1978–86, no
  statewide DEM known), SPOT or ASTER for the late 1990s; from 2000,
  Hugonnet dh/dt, IfSAR, ArcticDEM and our lidar.
- **Q2. What does "consensus" mean for faces?** *Answered (Hig,
  2026-11-20):* **keep every point.** Small advances are quite plausible and
  some are large, so nothing is forced monotonic and nothing is dropped for
  disagreeing. Review rules wait until there is enough on the plots to see
  what is needed.
- **Q3. Outlines on the plot: on or off by default?** *Answered (Hig,
  2026-11-20):* on, light. Built that way, with a switch.

Answered since the first plan: lake-terminating faces are in the first pass
(Portage); Hig draws the first networks himself, by hand where ITS_LIVE
stops (about 2000).

## 5. Decisions (Hig, 2026-11-16, still in force)

- **D1. How source data becomes ours.** Mirrored sources are layers to
  switch on and off. Beside them sits a provisional consensus that an admin
  improves over time. For this design that means a consensus face series
  per path (Q2). It must survive a source refetch without overwriting an
  admin's change, so every consensus point records whether it is still as
  merged or has been edited.
- **D2. Scope.** Alaska first (RGI region 01, which takes in the Canadian
  side of the St Elias and Coast Mountains). Yukon and BC later.
- **D3. Who edits.** `inventory_editors`; no new group.
- **D4. Terms.** Public scientific data, cited as such; no permission
  request. Every source carries its citation and DOI in the registry
  (`glaciers/sources.py`) and shows them on the page. DOIs on file: GLIMS
  10.7265/N5V98602; RGI 7.0 10.5067/F6JMOVY5NAVZ; NSIDC-0634
  10.5067/3OOVUDFK3R0W; G10040 10.7265/8esq-w553; USGS Glacier Bay
  10.5066/P9WDA76U; USGS benchmark 10.5066/P9R8BP3K; Bevington & Menounos
  10.5281/zenodo.5900363; WGMS FoG 10.5904/wgms-fog-2026-02-10; Reinthaler &
  Paul 10.5281/zenodo.8113575; Rick 2022 10.18739/A2MK6591G; Carrivick 2026
  10.1038/s41598-026-70307-w. None found for the NPS Kenai Fjords termini
  (IRMA reference 2293309).
- **D5. Earthdata-login sources** are fetched and converted on the
  workstation (`tools/glaciers/download_sources.sh`, `~/.netrc`) and loaded
  per environment from `data/glaciers/sources/`. The droplet never holds the
  credential.

## 6. Sources

Mirrored now: GLIMS (outlines), NSIDC-0634 McNabb & Hock (tidewater termini
1948–2012, 50 glaciers, with their own length series), USGS Glacier Bay
termini 1700–2019, USGS benchmark margins, G10040 ice cover (raster),
RGI 7.0 outlines and centrelines.

Candidates for face lines, in order of use to this design:

| Source | What | Why it matters here |
|---|---|---|
| NPS Kenai Fjords termini | lines for 19 glaciers, 1984–2021, with centrelines | face lines, lake and tidewater; IRMA geodatabase, terms not stated |
| WGMS front variations | length changes, no geometry; 136 series in the box, Columbia from 1899 | an independent check on plotted distances, as 0634's lengths are |
| LIA outlines (Carrivick 2026; Reinthaler & Paul 2023) | polygons | the oldest limit, through the outline crossing of §3.1 |
| Bevington & Menounos 2022 | annual outlines 1984–2021 | BC and Alberta only, so for after D2 |
| Rick 2022 ice-marginal lakes | four epochs 1984–2019 | lake-terminating fronts and their lakes |

Not usable or not found: TermPicks, CALFIN, AutoTerm (Greenland only);
Kienholz's hand-corrected Alaska centrelines (never published); Zhang and
others 2022 centrelines (RGI 6, unsuitable for tidewater); any Alaska fjord
centreline or velocity-derived flowline dataset; PP 1386-K and Field, and
Post & Meier (narrative only, no GIS).

## 7. Things the data do to us

- **RGI centrelines stop at each glacier's own outline and at the present
  terminus.** So joins across glaciers and every reach past today's ice are
  our work. OGGM's downstream routine skips tidewater glaciers. The
  "Strahler" field counts depth; it is not a true Strahler order.
- **Divides differ between submissions.** Columbia is 936, 944 and 774 km²
  under one GLIMS id. Position along a line is robust to this; area is not.
  The same fact is why §3.1 reads only an outline's down-flow exit.
- **Dates are soft.** Every observation is a bracket, not a date.
- **Ids are uneven.** One GLIMS id names as many as eight RGI 7 glaciers;
  0634 and Glacier Bay carry names only; 661 of 27,509 RGI rows have a
  name. Unmatched rows stay unmatched and are drawn, never guessed.
- **Glacier Bay's 82 unmatched lines** are positions of the one former ice
  mass, down-bay of anything in RGI. The network takes them, because a
  line drawn down the bay crosses them; no glacier-to-glacier link table is
  needed for that.
- **Side-by-side glaciers.** A face that crosses no line is projected onto
  ONE line, its own glacier's if one is in reach. Before that rule, Tsaa's
  plot carried 318 of Grotto's faces. Any new placement rule (outlines
  included) must keep to it.
- **NSIDC-0634 matching**: `cascade` lands on an RGI row named Barry and
  `moraine_apron` on Grotto (Hig has since made Moraine/Apron a
  distributary of Grotto). `yakutat/turner` has no projection file; its
  `proj` attribute gives the zone.

## 8. Not yet verified

Bevington's attributes (whether it carries glacier ids); which years the
USGS benchmark margins cover; whether the Carrivick LIA supplement can be
downloaded as stated; the WGMS 2026 table layout; the accuracy of any
pre-2000 DEM along a glacier (Q1).
