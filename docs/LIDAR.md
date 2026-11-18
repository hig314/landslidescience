# Self-hosted lidar DEMs and bathymetry; the /lidar/ viewer

Products, gating, R2, the build traps, client-side shading, the `/lidar/` URL and export, lidar in `/inventory/`. Publishing state and recipe: `tools/lidar/RESUME.md` and `/lidar/audit/`. Terrain comparison: `LIDAR_COMPARE_PLAN.md`.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

**Self-hosted lidar DEMs and bathymetry.** Alaska elevation surveys hosted as
**two products each** (archive COG + web PMTiles), built by
`tools/lidar/build_lidar.py` from the manifest `tools/lidar/datasets.json`.
**Live on production.**

**Do not look for the survey list here.** It changes every time one is
published and a list in this file would be wrong within the week. Two live
answers instead, in this order:

- **`/lidar/audit/`** (`publish_audit`, **signed-in data admins only** — it
  names gated surveys, whose existence is not public) — the authoritative
  state, checked rather than remembered: for every manifest entry it tests the four seams
  (built locally / present on R2 / present in the catalogue / what the gate
  actually does) and names any that disagree. Read it first.
- **`tools/lidar/RESUME.md`** — the hosting stream's state of play and the
  publishing recipe, plus the incidents behind it (BAG extraction modes,
  rclone filters, the overnight uploader). 50 public and 7 gated as of
  2026-11-06 (Homer 2019 and 2021 NCMP added that day), which is the number `/lidar/audit/` will confirm or correct.

**The manifest is two files** (2026-11-14): the tracked `tools/lidar/datasets.json` and an optional, gitignored `datasets.local.json` of the same shape for surveys that must not leave this machine (the repository is public, and a manifest row alone publishes a survey's name and source). `tools/lidar/lidar_manifest.py` `load()` merges them and is what `build_lidar.py`, `make_catalog.py`, `publish_audit.py`, `r2_put.sh` and `r2_sync.sh` read; a local entry must be `dev_only`, and the upload scripts refuse any id that is gated, owner-only or in neither file. `composite.py publish --write` still writes to the tracked file.

**An owner-only tier sits inside gating** (2026-11-10): `"owner_only": true`
beside `"gated": true` rides the gated catalogue, and `lidar_serve.owner_only_ids()`
limits that survey and its `_slope`/`_ortho` to superusers (bytes, gated
catalogue, `/lidar/audit/`, stored default views). Losing the flag falls back
to ordinary gating, never to public; `make_catalog.py` refuses `owner_only`
without `gated`. **Deploy the code before installing a catalogue that uses it**:
older code ignores the flag and serves the survey to every data user.

**Gating is by manifest flag and enforced on the BYTES.** `"gated": true` on
a dataset keeps it out of `catalog.geojson` and into the companion
`catalog-gated.geojson` (`make_catalog.py --gated-only`), which
`lidar_serve.gated_ids()` reads as the one list — so the pyramid, the COG
route and the listing all refuse together rather than merely hiding a row.
Gated surveys are served `private` cache headers and are **not** pushed to
public R2. Two traps worth knowing: gating is **not retroactive** (`rclone
copy` never deletes, so a survey gated *after* an upload keeps its bytes on
the public bucket until they are removed by hand — `publish_audit` reports
this as `GATED BUT ON PUBLIC R2`), and a gated survey's **orthomosaic is a
separate archive** that has to be gated with it.

**Orthomosaics** (`"ortho": …` in the manifest, SfM flights only) publish
beside the DEM and surface as the third render option in `/inventory/`
(see *In `/inventory/`* below). A lidar DTM has no imagery, so most surveys
carry none and the option is offered only where the catalogue has
`ortho_url`.

- **Archive**: a COG in the correct local UTM zone (NAD83(2011)), on `/Volumes/Nunatak/lidar_build/cog/` (~54 GB, *not* in git and *not* on the droplet). Public copy in **Cloudflare R2**, bucket `landslidescience-lidar`, key `cog/<id>.tif`, served through the bucket's custom domain `https://lidar.landslidescience.org/cog/<id>.tif` (Cloudflare CDN, bucket CORS exposes ETag/Content-Range for browser range reads). Pushed with `tools/lidar/r2_sync.sh`, credentials in `~/.r2.env` (never in the repo). R2 was chosen over a DO Space because egress is free and the bucket is no farther from a browser in Homer than the droplet is; no login gate is needed because the droplet never touches the bytes. **Cloudflare edge gotcha (2026-09-08):** on the FIRST ranged request for an object over 512 MB at a given edge, Cloudflare tries to cache-fill, gives up (`cf-cache-status: BYPASS`) and returns a **200 full body** to that request; every later request gets a correct 206. Small archives get 206 from the start. A GDAL `/vsicurl` first-open could therefore pull a whole 13 GB file. Fixed by a Cache Rule on the zone (host `lidar.landslidescience.org` + path starts with `/cog/`, `/pmtiles/` (added 2026-09-11, when the pyramids moved to R2) or `/permafrost_build/` (2026-10-31) → **Bypass cache**), added 2026-09-08 and **verified against a fresh 700 MB object**: the very first ranged request returned 206 with `cf-cache-status: DYNAMIC`. Re-verify the same way if the rule ever changes — an object that has already been requested will not reproduce the fault. Only `.tif` actually needs it: `.pmtiles` is not on Cloudflare's default cacheable-extension list, so `overlays/` (not in the rule, two archives over 512 MB) answers 206/DYNAMIC anyway — checked 2026-10-31 with every prefix 206/DYNAMIC and gdalinfo over `/vsicurl` opening a 21 GB COG in 1 s. A new prefix holding big `.tif`s must be added to the rule (permafrost_build/ answered 200/BYPASS until it was). Readable over `/vsicurl`, so QGIS opens one straight off a URL; that is the point of hosting them, since the public sources are awkward to get data out of.
- **Web**: one Mapbox terrain-RGB `.pmtiles` per survey in `data/lidar/pmtiles/` (~7.2 GB total, ~28,700 km²), read by MapLibre as a `raster-dem`/`raster` source over HTTP range requests. One file per survey rather than ~10⁵ loose PNGs, which matters for both rsync and object-storage cost.
- **Catalog**: `data/lidar/catalog.geojson` (`tools/lidar/make_catalog.py`) — one feature per survey with the **real data footprint** from `gdal_footprint`, not a bounding box. Homer is 90 km² of data inside a 602 km² bbox, so a bbox would overstate coverage ~6.7× and send people looking for data that isn't there.

**Archive modes** (`archive_mode` in the manifest): `warp` reprojects into the target zone (and applies `vertical_scale` for feet); `translate` is for a source already in the target zone — `gdal_translate -of COG` with `OVERVIEWS=IGNORE_EXISTING` keeps the CRS, including a compound NAVD88 vertical, and the pixels bit-for-bit, where a warp onto its own grid would drop the vertical CRS. Overviews are always recomputed from the pixels we ship (`OVERVIEWS=IGNORE_EXISTING`) so their provenance is known; `copy` mode exists but is unused.

**The compound-CRS geoid trap (found 2026-09-06).** Four sources carry a COMPOUND CRS (UTM + NAVD88 height): KBay, Seward, Glen Alps, Eagle River. Given such a source and a 2-D EPSG:3857 target, `gdalwarp` applies the NAVD88→ellipsoid geoid shift (+8.4 m at Anchorage) when it reads the source at **full resolution, but not when it reads an overview**. With `-multi`, chunks of one warp took each path, so every build got a random ~800 m band of rows 8.4 m high — the "two parallel lines with the mod-5 bands jumping between them" in Glen Alps, visible at the zooms warped from overviews and gone at max zoom. The per-zoom warp therefore passes `-s_srs EPSG:<target_epsg> -novshift` so heights go through untouched as NAVD88 orthometric, like every other survey. Diagnose with `gdalwarp -ovr NONE` vs `-ovr AUTO` on the same grid: a *uniform* offset between them is a datum shift, never a resampling error.

*A cautionary tale from the same evening:* before finding that, I "proved" the vendor overviews were misregistered by 1–3 px with seams and rebuilt two 10 GB archives. They were fine — checked with exact overview dimensions and factor-aligned windows, the vendor overviews are closer to a true block mean (0.18 m RMS) than GDAL's cascaded ones (0.35–0.55 m on a non-divisible width). The "shifts" were the tests' own: an `out_shape` one column short makes GDAL resample the overview with a one-pixel jump at the middle column, and a window whose origin is not a multiple of the factor is shifted by exactly `origin mod factor`. When comparing overviews to full-res: `ceil(dim/factor)` dimensions, factor-aligned windows, and RMS ≈ 0 before believing a shift.

**Why the build resamples per zoom level.** Terrain-RGB packs one height across three bytes of differing significance (`h = -10000 + (R·65536 + G·256 + B)·0.1`). Averaging two neighbouring blue values across a carry boundary (255 and 0 → 127) invents a 12.7 m error, so the naive pipeline — encode once at native resolution, let the tiler build overviews — is **silently wrong at every zoom below max**. Instead the *elevation* is warped to each zoom's exact ground resolution and encoded per level. The per-level warps form a geometric series, so this costs ~1.33× the max-zoom warp. (`nearest`-only resampling is also correct but aliases badly: at z13 it samples one 0.5 m post per 8 m cell and the hillshade turns to noise.)

**Two source defects worth remembering.** Mat-Su ships tagged UTM 5N but spans 150.5°W–147.0°W — almost entirely zone 6, up to 3° past the zone edge (→ EPSG:6335). Its elevations are also **in feet**: the raw grid reads 246.1 at Palmer and 341.5 at Wasilla against published 240 ft and 344 ft, and a metres reading would put downtown Palmer at 807 ft; `vertical_scale: 0.3048` is applied at ingest so both products agree. Homer and Lower Kenai cross-check against *each other* in metres at an overlap point (19.84 vs 20.06). Kachemak Bay arrives in a **compound CRS** (NAD83(2011)/UTM 5N + NAVD88 height) for which `to_epsg()` returns `None`; its archive is **copied, not warped** — warping to a 2D EPSG:6334 is an identity resample that also silently drops the only declared vertical datum in the collection. Hence `archive_mode` is explicit per dataset in the manifest rather than sniffed.

**Serving needs HTTP range requests, and Django doesn't do them.** As of **Django 5.2, neither `FileResponse` nor `django.views.static.serve` implements `Range`** (verified against the installed source, not assumed). PMTiles fetches tile byte-spans and a COG is only cloud-optimized if a client can fetch just the header, so `landslidescience/lidar_serve.py` parses Range itself: `/lidar/pmtiles/<id>.pmtiles`, `/lidar/cog/<id>.tif`, `/lidar/catalog.geojson`, plus the standalone preview page at `/lidar/`. Single ranges only — multi-range is legal HTTP but no PMTiles or GDAL client emits it. Dataset ids are anchored to a slug regex, which is what keeps `../` out of the path join. The catalog's `cog_url` points straight at R2; `/lidar/cog/<id>.tif` serves a local copy where one is mounted (dev) and otherwise **302s to R2**, so a 12 GB download never occupies one of the two gunicorn workers.

**Client-side shading (`inventory/static/inventory/js/dem_shade.js`).** Registers a `demshade://` protocol — same canvas-protocol trick as `operacolor`, one step further: that one recolors a value, this one differentiates a surface. Renders hillshade, slope ramp and mod-5 m banding from the elevation tiles, so those cost **no extra storage**. Not using MapLibre's `hillshade` layer is deliberate: **`hillshade-opacity` does not exist** (zero occurrences in the 5.5.0 bundle), so a hillshade can only be faded by abusing `hillshade-exaggeration`, which changes the shading rather than blending it; and `hillshade-illumination-altitude` is ignored under several `hillshade-method` values, so a sun-altitude control silently does nothing. A plain `raster` layer has real `raster-opacity`.
- Colour ramps are copied **verbatim** from Hig's QGIS project `~/Documents/Outreach/KBSP_Map/Spring_2026/260104_KBay_map.qgs`, so a site rendered in the browser matches one rendered there. The 5 m interval is **fixed, not a slider** — holding it constant is what makes banding comparable between sites.
- Hillshade composites over the ramps as an **Overlay**, switchable to normal transparency. Overlay is an *identity* wherever the ramp beneath sits at mid-tone (the slope ramp passes through `#ebece2`/`#f5f5f5` at 10–32°), which is exactly why it reads faint on moderate terrain. With nothing underneath it composites normally regardless — Overlay against white returns white.
- Slope needs neighbours, so each tile is built from a 258×258 grid lifted off the 3×3 tile neighbourhood; without that halo every tile border grows a false ridge.
- **Two caches, two distinct costs**: finished PNGs keyed by full request URL (makes flipping between two DEMs over the same ground ~1 ms vs ~250 ms cold), and decoded elevations keyed by tile (makes slider drags pure recompute). The elevation cache stores **in-flight promises, not results** — a 4×4 tile block requests 144 neighbours covering only 36 distinct tiles, and they all start together, so caching resolved values let every one of them miss and decode redundantly (measured 0 hits / 144 misses before the fix, 108 / 36 after).

**The `/lidar/` URL** is a query string, not `/inventory/`'s hash grammar: `writeUrl()` rebuilds it from the page on every change (camera, `bm`, `ds`, `df`, every `CONTROLS` slider and `CHECKS` box, the group profile `gp*`, trace filters `trk=` kinds off and `trg=` the one group shown), so a link freezes every setting; it is read once at boot from `Q`. **State that lands late is held from the link until applied** (`urlHeld`: `ds`/`df` until the catalogue's restore, `gp*` until the profile is set, `trk`/`trg` until the traces load) — the same rule as `/inventory/`'s pending `li=`/`ext=`/`base=` — because an early write (a pan, a slider, Copy link during load, the catalogue failing) otherwise dropped them for good. *Copy link* writes first rather than copying the address bar as it stood 250 ms ago. **In 3D the camera needs `ce=`** (the centre elevation, exaggerated metres; 2026-10-19): z/lat/lon fix the camera only together with it, every `setTerrain()` resets it from whatever DEM tiles exist (0 m on a fresh terrain), and the terrain tracker then keeps the camera where that put it — a Barry Arm link at ex=10 reopened underground. The link's camera (`linkCam`) is held and re-applied on a 250 ms timer until it has stood still for 2 s with the survey's terrain loaded (not on `idle`, which a load can end without ever firing); a link from before `ce=` takes the terrain height under the centre. A new control is added to `CONTROLS`/`CHECKS`, or to `writeUrl` plus its restore, and its change handler calls `syncUrl()` (2026-10-16: choosing a survey from the list did not, so the address bar kept the previous `ds=` until the next pan).

**Save image (PNG) on `/lidar/`** (2026-10-19, rough cut): the sidebar's *Save image* box drives `/inventory/`'s engine `ls_export.js` with `terrain3d` (DEM tile size kept, centre elevation carried, unclamped), `editStyle` (the copy's `dem` tiles get `&export=1` — the package's URL-keyed tile cache hands back a buffer already transferred to the live map's worker, "Failed to construct 'ImageData'") and `onClone` (the terrain-minimum floor). The engine also drops `undefined` source keys (they failed validation: a blank white export) and polls for done as well as `idle`. No legend; HTML markers (A/A′, go-to ring) are not in the picture.

**Terrain context in `/lidar/` (2026-09-28).** 3D terrain composites the active survey over two context layers: our own 3DEP bake (`ctx_3dep`, around the surveys) and, beyond it, **Mapterhorn**'s global terrain (`tiles.mapterhorn.com`, 512 px terrarium WebP, CORS open, Cloudflare-hosted, no key or stated limits). In Alaska Mapterhorn is the same 3DEP 1/3 arc-second data statewide -- median difference 0 m against the bake at Homer, the Kachemak glaciers and Seward -- with Copernicus GLO-30 in Canada; its 1 m 3DEP does not reach Alaska. It replaced the live USGS ImageServer as the fill beyond the bake. The survey itself fills to its boundary with `fillMode 'outside'` (footprint outer rings), so voids inside a survey stay voids. Attribution (Mapterhorn + USGS + Copernicus) rides the terrain source. The package reads 512 px XYZ sources and, with overzoom, falls back to the parent tile where the publisher's depth runs out (z12 in Yukon). Should the service go, Mapterhorn publishes PMTiles for self-hosting an Alaska extract. The context is also the terrain when no survey is active, and it is **hillshaded** with the surveys' own shading stack beneath them (`ctxshade` layer, below the footprints): the terrain mesh alone is too coarse to show detail, which is why Mapterhorn's viewer looked sharper than ours over the same tiles.

**In `/inventory/`.** Lidar and editor GeoTIFF uploads share one zoom-gated section ("Elevation & imagery overlays"), built by `_buildRasterUI` in `map.js`. Below `RASTER_UI_ZOOM` (10) only footprints are offered — a 0.5 m survey at z8 is a smudge and listing every Alaskan survey is noise; above it, the surveys **covering the current view**. Footprints persist at all zooms, fading from fill to a thin edge line, because zoomed out they answer "where is there data?" and zoomed in "am I tracing off the edge?". Lidar is **public**; uploads stay editor-only inside the same section. Three fixed render options per survey and an opacity slider — no sun sliders, no 3D. **Hillshade** and **Preset** are client-side shadings of the DEM tiles (`dem_shade.js`); **Ortho (imagery)** is the orthomosaic from the same flight, a plain `pmtiles://` raster with no shading pipeline, offered only where the catalogue carries `ortho_url` (today the SfM flights — a lidar DTM has no imagery). All three share ONE layer per survey, one state field and one `li=` code, so the ortho is a different thing to draw rather than a second overlay competing for stack position; a stored view naming `ortho` for a survey that has none falls back to hillshade. The wiper's right pane gets the same lidar list (`_lidarActiveR`) so two surveys can be compared across the divider; uploads remain main-pane-only, which is the pre-existing deliberate choice. **Inactive layers sit at `raster-opacity: 0`, never `visibility: 'none'`** — a hidden layer marks its source unused and MapLibre then evicts exactly the tiles we want warm. `raster-opacity-transition` and `raster-fade-duration` are both zeroed, or switching surveys cross-fades through the basemap.

## Terrain comparison, `/lidar/compare/`

*Moved here from the state of play 2026-11-14. Plan, decisions and measurements: [LIDAR_COMPARE_PLAN.md](../LIDAR_COMPARE_PLAN.md) §10–11.*

**Terrain comparison, `/lidar/compare/`** (2026-11-12; plan, decisions and
measurements in [LIDAR_COMPARE_PLAN.md](../LIDAR_COMPARE_PLAN.md) §10–11).
Up to 12 panes (9 measured), one MapLibre map each, at one shared ground
scale (m/px at the centre, so zoom differs by latitude; each centre kept
on the ground, ×0.999 measured through the camera), tilt, VE, shading,
base + opacity; per pane place, bearing, survey (Auto = finest then newest
covering the centre; pull-down lists surveys in view), title. Frame /
Together modes, locks, page + pane titles, ground-draped scale bar and
north arrow (shared spec, per-pane position and angle relative to the
look direction), Hide controls, Export PNG 2×/4×. **Round 2 (deployed
the same day):** statute units (`u=us`), VE 0–10 (0 = flat map),
settings docked under the key, and **profiles**: one line per pane
(`pl=`), insets on one shared scale (`pf=` 1:1 / match 3D VE /
freeform), movable and resizable, other panes' profiles drawn faint at
this pane's mid-height; exports render each pane in turn at full
resolution (`setRttQuality`, HAZARDS). State is the URL only;
public catalogue only. *Start comparison* on `/lidar/` for editors carries
only public survey ids. The perf readout shows with `?perf=1`. Nine panes
are slow to settle (single shading worker; options in the plan §11). Not
built: server-side sheets, publish/branch, the `lidar_scene.js` extraction.
**Later rounds, 2026-11-13, each tested in dev by Hig and deployed:** drag
to re-order panes, bar/arrow labels at one ground size (09fcec9); shared
QMS base layers and `bm=none` survive the link (0af7cb2); *Hide controls*
kept as `hc=1` (e4f502f); equal pane sizes and a JPEG export option
(f2b7e33). **Saved comparisons** (6c8dc13): `pages.SavedComparison`
(3-hex `code`, name, query without `sv=`, notes; migration
`pages/0003`), edited in the Django admin; public list
`/lidar/compare/saved/`; short link `/lidar/compare/s/<code>/` redirects
to the latest query + `sv=<code>`, so a shared link follows updates. On
the page, superusers or holders of the add/change perm get a name box
(follows the title until typed over), *Submit* (new code) and *Update
<code>* (overwrite); `compare_save` refuses a pane naming a gated survey.
Also fixed: blurring an empty title no longer writes
`t=Untitled+comparison`. Prod checked anonymously by curl only — nothing
has been saved there yet, so the first signed-in Submit on prod is the
first real test. Prod also not yet browser-tested: profiles, QMS bases,
`hc=1`, JPEG export.
