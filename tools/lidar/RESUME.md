# Lidar hosting — state of play (2026-09-20)

> Hosting is one of two streams under `tools/lidar/`. The other, topobathy
> compositing (`topobathy/`, `composite.py`, `grid_bathy.py`, `mask_tool/`,
> `composites/`, `TOPOBATHY_PLAN.md`), lives on branch
> `topobathy-compositing` in its own worktree. See `../../WORKSTREAMS.md`
> for the split and the boundary rules.

**41 public surveys on production**, 7 gated, all archive COGs in Cloudflare R2
behind `lidar.landslidescience.org/cog/<id>.tif`. `/lidar/audit/` is the live
answer to "where is everything" — it checks the four seams (built / on R2 / in
the catalogue / what the gate does) and is the thing to read first, not this
file.

**Bathymetry is now part of the collection**, which is what the 2026-09-08
version of this file said to design for. Three multibeam surveys and two sonar
sets are in: Lituya Bay 2025 (NOAA H14228), Resurrection Bay 2016 (F00683) and
2024 (E01001), Kachemak 2008-09, and Grewingk Lake 2023 (gated).

## Reading a BAG: use MODE=INTERPOLATED

Variable-resolution BAGs extracted with the obvious `MODE=RESAMPLED_GRID` come
back **full of holes**. That mode samples refinement NODES, so wherever a
supergrid's refinement is coarser than the cell you asked for, the cells
between nodes are empty. It cost **92% of Resurrection Bay 2024** (4.3 km2
recovered instead of 33.7) and **38% of Lituya 2025** (29.0 instead of 46.7) —
the latter already published, and visible only at native zoom, because
overviews average the lattice away. Compare the extraction's valid fraction
against the BAG's own low-resolution supergrid view before trusting it.

Fixed-resolution BAG deliveries (F00683 ships 1/2/4/8/16 m bands) stack
finest-on-top in one VRT with `-resolution highest`, and the VRT must read
**bilinear**: 63% of that survey's sounded area is genuinely 16 m, and nearest
replicates each cell into 256 identical metre cells — 61.5% of cells in a test
window had exactly zero gradient. Bilinear drops that to 0.3% and cannot
overshoot into pits the soundings do not contain, which cubic can.

## MLLW -> NAVD88, when nothing published will tell you

Every BAG is MLLW; the collection is NAVD88. Routes, in order of preference:

1. **The tide station's own bench mark.** A mark's height above MLLW is on the
   CO-OPS bench mark sheet; the same mark's NAVD88 height is on its NGS
   datasheet. Same disk, two datums, subtract. This is how Seward was settled
   (-0.118 m) after everything else failed. CO-OPS only prints a NAVD88 row
   when **two or more** marks have NGS elevations, so the tie often exists
   while the summary says nothing.
2. **A topobathy (green lidar) survey.** Spans land and shallow seabed, so it
   overlaps multibeam by millions of cells — how Kachemak got -1.38 m.
   Topographic lidar does NOT work: it stops at the waterline, above the
   sonar's shallowest return, so they share no surface (checked at Seward).
3. **BlueTopo.** Carries the same soundings already in NAVD88, so differencing
   it against the BAG gives the separation — how Lituya got -0.513 m. Only
   works in Southeast: BlueTopo's Alaska coverage stops at -138 deg, and so
   does VDatum's (three regions, all Southeast). That is not a coincidence —
   BlueTopo can only publish NAVD88 where a tidal grid exists.
4. VDatum's web API **cannot** do it in Alaska (circular frame requirements),
   and PROJ offers only a no-shift "ballpark" operation that would silently
   assert MLLW = NAVD88.

Watch the units: CO-OPS datums are in **feet** at these stations.

## Changing a datum on a built dataset

`vertical_shift_m` is stamped into the archive COG, and `build_archive` stops
if the manifest disagrees with the stamp. Before that guard, editing the shift
regenerated every tile from the **stale archive** and reported success — caught
only because a second survey covered the same seabed.

## State

| Thing | Where | Status |
|---|---|---|
| Code | `main` | GitHub, droplet, local in sync |
| Public surveys | `data/lidar/pmtiles/` + R2 `pmtiles/` | 38 in the production catalogue |
| Gated surveys | droplet `data/lidar/{pmtiles,cog}/` only — **never** R2 | 8 (Corax x4, Hoonah, Lituya 2023, Pedersen SfM, Grewingk sonar) |
| Catalogue | `data/lidar/catalog.geojson` | build with `--verify-r2` for anything going to prod |
| Archive COGs | `/Volumes/Nunatak/lidar_build/cog/` + R2 `cog/` | |
| Built, NOT published | pow_2018 (62.8 GB), resurrection_2016, resurrection_2024 | awaiting Hig |
| Paused InSAR kinematics | branch `insar-kinematics` | dev-only |

Tag `archive/main-2026-09-06-kinematics-plus-lidar` marks what `main` looked
like before it was rebuilt from `origin/main` (kinematics + a duplicate lidar
commit). The rebased `insar-kinematics` tree differs from it only by the
IceBridge guard, the manifest additions, and docs.

## Datasets added 2026-09-06

- **matanuska_2011** — Mat-Su Borough 2011, 9 ft posts, State Plane zone 4 in
  US survey feet, elevations in feet → metres on ingest. Checked against
  `matsu_2019` at Palmer airport / Sutton / Chickaloon: 70.78/137.04/414.87 m
  vs 70.94/137.17/415.10 m. Tiles decode to within 0.25 m of the COG at z15.
- **seward_2023** — compound CRS NAD83(2011)/UTM 6N + NAVD88, 0.5 m, arrives as
  a COG so the archive stage copies. Tiles decode to within 0.1 m of the COG at
  z17, ~1 m at z14 (average-resampled 9.5 m cells, expected).

## Known gaps

- **Archives on R2.** Public URL `https://lidar.landslidescience.org/cog/<id>.tif`
  (bucket custom domain, Cloudflare CDN, CORS on the bucket). `/lidar/cog/` in
  Django 302s there unless a local copy is mounted. No login gate: the droplet
  never touches the bytes. Credentials in `~/.r2.env`, never in the repo.
  **Cloudflare edge, >512 MB objects:** without a cache rule, the first ranged
  request at an edge triggers a cache-fill attempt that fails and returns a
  **200 full body** (a `/vsicurl` open would pull the whole file); later
  requests get 206. Hig's Cache Rule (host `lidar.landslidescience.org`, path
  `/cog/` → Bypass cache, 2026-09-08) fixes it — verified against a fresh
  700 MB object, first request 206 / `cf-cache-status: DYNAMIC`. Re-verify the
  same way if the rule changes; an already-requested object will not reproduce
  the fault.
- **Droplet disk: 9.3 GB free before the PMTiles move (2026-09-11)**; deleting the
  droplet copies frees 7.3 GB. Uploads run from the Mac with rclone reading
  `~/.r2.env` through `RCLONE_CONFIG_R2_*` env vars (no config file;
  `RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true` because the token cannot create
  buckets) at ~1.5 MB/s. The next big survey (one rclone
  command plus a catalog URL change) or a bigger droplet.
- Automation cannot see a rendered MapLibre page (hidden tab pauses
  `requestAnimationFrame`; headless Chrome hangs on WebGL — see the note in
  `dem_shade.js`), so every visual check is Hig's. Hig confirmed the wiper
  right pane and the 3D context on 2026-09-07.

## Re-deploying data

The upload is resumable — rerun it and rsync skips finished files and continues
partial ones. Run it detached so a laptop restart doesn't kill it:

```bash
cd ~/Claude_projects/landslidescience
nohup rsync -av --partial --exclude 'cog/' \
  data/lidar/ root@143.198.140.54:/opt/landslidescience/data/lidar/ \
  > /tmp/lidar_rsync.log 2>&1 &
```

Code deploys the usual way (`git push`, then on the droplet `git pull` +
compose `build` + `up -d --force-recreate`). Verify:

```bash
curl -sI https://landslidescience.org/lidar/catalog.geojson
curl -sI -H 'Range: bytes=0-999' \
  https://landslidescience.org/lidar/pmtiles/homer_2019.pmtiles   # expect 206
```

## Rebuilding

```bash
# needs Homebrew GDAL >= 3.11 and pmtiles; NOT the QGIS-LTR bundle (3.3)
brew install gdal pmtiles
env -u PROJ_LIB -u PROJ_DATA python tools/lidar/build_lidar.py --list
env -u PROJ_LIB -u PROJ_DATA python tools/lidar/build_lidar.py <id> --stage all
env -u PROJ_LIB -u PROJ_DATA python tools/lidar/make_catalog.py

# For anything going to PRODUCTION, verify the bytes are actually on the
# bucket -- this is what stops a catalogue advertising a pmtiles_url that
# 404s (shipped once, 2026-09-19, caught on the post-deploy check):
env -u PROJ_LIB -u PROJ_DATA python tools/lidar/make_catalog.py \
    --verify-r2 --out /tmp/catalog_prod.geojson

# For DEV, point at the local routes so unpublished surveys resolve:
env -u PROJ_LIB -u PROJ_DATA \
  LIDAR_PMTILES_PUBLIC_BASE=/lidar/pmtiles LIDAR_COG_PUBLIC_BASE=/lidar/cog \
  python tools/lidar/make_catalog.py

# Gated companion catalogue (droplet routes; gated bytes never reach R2):
env -u PROJ_LIB -u PROJ_DATA \
  LIDAR_PMTILES_PUBLIC_BASE=/lidar/pmtiles LIDAR_COG_PUBLIC_BASE=/lidar/cog \
  python tools/lidar/make_catalog.py --gated-only \
    --out data/lidar/catalog-gated.geojson
```

Always strip `PROJ_LIB`: a QGIS-bundled PROJ leaking into another GDAL makes it
write an `ENGCRS` with an empty datum, which then fails much later with an
unrelated-looking "cannot find coordinate operations" error.

`build_lidar.py` reads `datasets.json` once at startup, but only when the
dataset is looked up — do not `git checkout`/`reset` the manifest out from
under a queued build (that is how the first Seward attempt died with
"unknown dataset").

## Tiled deliveries (USGS 3DEP style)

A survey that arrives as hundreds of 1 km tiles is built from the ORIGINAL
tiles, never from a hand-merged copy: `gdalbuildvrt` them into a VRT under
`/Volumes/Nunatak/lidar_build/vrt/` (with `-srcnodata/-vrtnodata` set to the
tiles' nodata and an absolute `-input_file_list` beside it) and point the
manifest `src` at the VRT with `archive_mode: translate`. Glacier Bay 2019–20 (the full USGS delivery: 6,728 tiles in four work units,
two at 0.5 m and two at 1 m, 5,490 km² inside a 185 × 145 km box) built in
~40 min that way. It is published at **1 m** (`-resolution user -tr 1 1
-r average`): the 0.5 m mosaic came to a 48 GB archive and 6.4 GB of tiles,
the 1 m one to 13 GB and 2.3 GB, and Hig judged 1 m enough for the purpose.
The full tile set is kept in `/Volumes/Nunatak/lidar_src/glacier_bay_2019/`;
the download was `scratchpad/gb_download.py`-style (pooled, size-checked,
resumable) from rockyweb.usgs.gov's OPR staging index.


## Slope pyramids (2026-09-12)

Every survey now gets a third product, `<id>_slope.pmtiles`: slope in degrees
computed ONCE from the float32 archive in its UTM zone (`gdaldem slope`, true
metres), resampled per zoom with `average`, quantised to 0.5° as 8-bit
greyscale PNG (255 = nodata). Why: terrain-RGB tiles hold elevation in 0.1 m
steps, so a slope taken from them in the browser staircases on gentle ground
(false ~10° lines at z17 on the Anchorage flats). Hillshade stays client-side
(the sun is a slider); the mod-5 banding keeps reading the raw tile values.
Build with `build_lidar.py <id> --stage slope` (needs the archive; `all` now
includes it). `make_catalog.py` writes `slope_url` / `slope_bytes` /
`slope_step` when the file exists; the package reads it when present and
falls back to the in-browser gradient otherwise. Full rationale in the
`build_lidar.py` docstring ("WHY SLOPE IS PRE-BAKED").

## Kenai 2008 from the point cloud

See `KENAI_2008_PLAN.md`: no public DTM exists; the EPT point cloud (14 B
points) and the USGS boundary/tile index are the raw form; PDAL SMRF/PMF
re-classification vs vendor class 2, gridded at 4 ft, density raster
alongside. Disk budget fits on Nunatak as is.

## 2026-09-12 (evening): datum shift in eight archives, rebuild queued

- **Bug found**: sources tagged generic NAD83 (EPSG:4269 / datum 6269) were
  NADCON5-shifted 0.5-1 m by the archive warp to NAD83(2011). Proven at Homer:
  archive sits 0.67 m E / 0.58 m N of its own source; USGS 2008 EPT and USACE
  2018 NCMP both agree with the *source* (~0.35 m) and not the archive.
  Affected: homer_2019, anchorage_2015, matanuska_2011 + 5 siblings. Fix in
  build_lidar.py (`retag_generic_nad83`, docstring section) re-tags via VRT.
- **Running**: `logs/datum_fix_chain.sh` (local only: rebuild the 8, then slope
  pyramids for all; log `datum_fix_chain.log`, superseded products in
  `lidar_build/superseded/*.nadcon5-shifted.*`). Publishing is
  `logs/datum_fix_publish.sh` (R2 push + catalog install) -- needs Hig's OK.
- **Testing trap**: interactive `gdalwarp` is QGIS-LTR GDAL 3.3 (no NADCON5,
  no compound vertical conversion); use /opt/homebrew/bin explicitly and
  /opt/anaconda3/bin/python3 for rasterio. Co-registration scripts in
  `lidar_build/kenai_coreg/` (Nuth & Kaab fit: `shift_field.py`,
  `src_vs_archive.py`, `override_test/`).
- **Kenai 2008 teacher work**: Woodard patch done (`kenai_test_woodard/`,
  `crest*.py`); after co-registration SMRF-steep keeps sharp crests best
  (median -0.04 m, 2.3% >1 m low) vs vendor (-0.12 m, 9.7%), CSF worst.
  Teacher sites (`kenai_teacher/`) must be regenerated after the homer_2019
  rebuild with per-block co-registration (2008 still ~0.35 m off 2019).

## 2026-09-12 late: rebuilds done, four new surveys built, publish pending

- `datum_fix_chain` finished 22:29: the 8 generic-NAD83 archives rebuilt (all
  `retagged=1`), slope pyramids now exist for all 25 original surveys.
- `online_next_chain` finished 22:46: homer_2018 (NCMP, 0.54 m), juneau_thane_2019
  (translate), juneau_2012 and hoonah_2015 (re-tagged) built locally; units of all
  four confirmed metres against 3DEP (`lidar_build/unit_check/check3dep.py`).
  **hoonah_2015 is `gated: true`**: make_catalog skips it (use `--include-gated`
  for an admin catalog), r2_sync excludes it. Admin-only serving path still to build.
- **Publish step** (`logs/datum_fix_publish.sh`: r2_sync cog + pmtiles, make_catalog,
  scp catalog to prod) waits for Hig's OK. Catalog would then list 28 surveys.
- Kenai 2008 taught filter: Homer 2019 point cloud (DGGS RDF 2021-2, portal dataset
  1354, 11.6 GB) cannot be pulled from elevation.alaska.gov (30 KB/s, truncated
  zips) -> Hig to ask DGGS for a direct link. NOAA 2018 Homer topobathy points
  (ID 8686, 2.5 GB, COPC) mirroring to `lidar_src/homer_2018_ncmp/` for the
  merged-cloud prototype (2008 + 2018). Full-footprint 2008 tiles/features in
  `kenai_taught/full/` (fetch verifies each LAZ now). Review map artifact:
  https://claude.ai/code/artifact/47d62994-1f5a-4fbe-ba63-16a304624e8f (db
  collection `sites`, docs {sid,state}).

## 2026-09-13 17:15: cordova_2023 built; publish pending (Hig runs it)

- cordova_2023 (DGGS RDF 2024-6, translate) built locally: cog 2.8 G, pmtiles 477 M,
  slope 573 M, 1.3-1683 m. Manifest now 30 datasets (hoonah_2015 gated).
- Site code deployed 0a60dba/9a5312f (li= view state, QMS scope toggle, public-safe
  default views, pool fixes). Prod catalog simplified in place to 40 m footprints.
- **Publish still to run**: `logs/datum_fix_publish.sh` (R2 cog+pmtiles, catalog,
  prod install) -- the auto-mode classifier blocks Claude from launching it; Hig runs
  it with `! nohup ... &` from the terminal. Covers the 8 datum-corrected archives,
  homer_2018, juneau_2012, juneau_thane_2019, cordova_2023 and all slope pyramids.
- Homer 2019 point cloud: 79 complete / 72 unfinalised (DGGS-side) / coverage pass
  fetching the last ~13 (`homer_2019_pc/download_pass2c.log`, `tile_status2.json`).
  Kenai 2008 metadata unpacked (`homer_2019_pc/kenai2008_meta/`): Optech ALTM Gemini,
  1.4 m spacing, NAD83(CORS96)/GEOID06, flown 2008-05-21..23, 06-05/06, 09-22.
  Skyhub (AGO catalog, services1.arcgis.com/7HDiw78fcUiM2BWn/.../Alaska_Skyhub_Database)
  has Homer 2019 DTM/DSM on geoportal.alaska.gov/public_data/elevation/ but no LAZ.

## 2026-09-21: pedersen_sfm_2024 ungated; two gate leaks found in the publish path

- **pedersen_sfm_2024 is public.** Hig established that the NPS publishes this
  same survey openly, which settles the "cannot be further distributed in its
  original form" stamp on the copy delivered to us: IRMA DataStore reference
  2310424, doi:10.57830/2310424, issued 21 May 2025, `licenseType: Public
  Domain`, visibility and fileAccess both Public, carrying the four DSM tiles
  and fourteen orthomosaic tiles this survey was built from. Checked against
  `irmaservices.nps.gov/datastore/v7/rest/Profile/2310424`, which is where to
  re-check if the terms are ever questioned; the web profile page is
  JavaScript-rendered and tells you nothing. Manifest: `gated` removed,
  `source_url` set to the DOI, note rewritten (and a factual slip fixed -- the
  flight was two days after the tsunami, not a year). COG + DEM + slope + ortho
  (1.79 GB) pushed to R2 and verified publicly readable. The first ranged
  request against the fresh 1.03 GB COG returned **206 / DYNAMIC**, so the
  zone's over-512 MB bypass-cache rule is still doing its job -- an object that
  has already been requested cannot re-test that, so this was the chance.
- **`r2_sync.sh` never once excluded a gated dataset.** rclone discards the
  entire `--exclude` list whenever any `--include` is present. It is not an
  ordering problem, so the 2026-09-08 "caller flags go first" change fixed
  nothing; measured both orders against rclone 1.75. Now expressed as ordered
  `--filter` rules (`- id.*`, then `+ pattern`, then `- *`) and re-verified by
  dry run. That also covers `_ortho.pmtiles`, which was never in the list at
  all -- the next `--pmtiles` run would have published three gated Corax
  orthomosaics. `r2_put.sh` was already written around this ambiguity and is
  the safer per-dataset publish path; prefer it.
- **`hoonah_2015`'s bytes are on public R2** -- archive COG and both pyramids,
  fetchable anonymously at a guessable URL. Pushed 2026-09-12, gated about a
  week later, and `rclone copy` only ever adds, so gating afterwards did
  nothing and the droplet's gate does not cover the bucket. `publish_audit.py`
  now says **GATED BUT ON PUBLIC R2 (...)** where it used to say "gated, served
  from the droplet", which is why this sat unnoticed. **Open decision for Hig:**
  purge the three objects to make the gate real, or ungate hoonah if its
  provenance is now settled.

### Shipped 2026-09-21. The recipe, for the next ungating

Hig validated the data and approved production; `git push` 9a60686, droplet
`git pull` + prod `build` + `up -d --force-recreate`, then both catalogues
installed. Verified after: public catalogue **39 surveys** with
pedersen_sfm_2024 listed, all four advertised URLs answering 206, the gated
catalogue still 403 to anonymous, the six other restricted surveys still 403
on the droplet routes, and the audit reading `public PsoC PsoC yes yes ok`.
Prior catalogues on the droplet are backed up as
`catalog{,-gated}.geojson.bak.20260922-021631`.

```bash
# 1. code: the manifest change (and here, the sync/audit fixes)
git push origin main
ssh root@143.198.140.54 'cd /opt/landslidescience && git pull && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate'

# 2. catalogues (NOT in git -- data/ is gitignored). The public one must be
#    built with --verify-r2 so a survey whose bytes are absent cannot be
#    listed; that is what keeps pow_2018 and both Resurrection surveys out.
env -u PROJ_LIB -u PROJ_DATA python tools/lidar/make_catalog.py \
    --verify-r2 --out /tmp/catalog_prod.geojson
env -u PROJ_LIB -u PROJ_DATA \
  LIDAR_PMTILES_PUBLIC_BASE=/lidar/pmtiles LIDAR_COG_PUBLIC_BASE=/lidar/cog \
  python tools/lidar/make_catalog.py --gated-only --out /tmp/catalog_gated.geojson
scp /tmp/catalog_prod.geojson  root@143.198.140.54:/opt/landslidescience/data/lidar/catalog.geojson
scp /tmp/catalog_gated.geojson root@143.198.140.54:/opt/landslidescience/data/lidar/catalog-gated.geojson

# 3. confirm: survey count up by one, it is listed, gated still refuses anon
env -u PROJ_LIB -u PROJ_DATA python tools/lidar/publish_audit.py
```

Local `data/lidar/catalog.geojson` is the **dev** build (droplet-relative
URLs) and must not be rsynced to prod as-is; prod's copy is the `--verify-r2`
build with `lidar.landslidescience.org` URLs.

**Back up the droplet's catalogues before scp'ing over them** (`cp -p` with a
timestamp suffix). They are the only copy of what prod is actually serving:
the local `catalog.geojson` is a dev build, so a bad install cannot be undone
from the working tree. Note also that no pyramid rsync is needed for a survey
published this way — the prod catalogue points at R2, so the droplet's own
`/lidar/pmtiles/` copy is only a fallback and never enters the path.

**Still open:** `hoonah_2015`'s bytes on public R2 (decision above). Hig chose
on 2026-09-21 to leave those where they are for now.

## 2026-09-21 18:21: Resurrection Bay published; pow_2018 in flight

Hig approved publishing all three of the built-but-unpushed surveys.

- **resurrection_2016 and resurrection_2024 are live** (41 surveys). Both are
  small, 0.20 and 0.13 GB, so they were queued FIRST and verified within six
  minutes; the catalogue was installed for them immediately rather than waiting
  on pow_2018. Both NOAA Office of Coast Survey, public domain, F00683 and
  E01001. Byte integrity checked on the bucket: PMTiles v3 magic on all four
  pyramids, BigTIFF magic on both archives.
- **pow_2018 is UPLOADING and is NOT yet published.** 58.5 GB in three files
  (archive 31.5, terrain pyramid 11.3, slope pyramid 15.7) at about 1.1 MB/s,
  so roughly 12 hours from 18:27. Run via
  `tools/lidar/r2_publish_overnight.sh`, which holds sleep off and verifies
  each file by byte count. **When it finishes, the catalogue must be rebuilt
  and installed again** with the recipe above; `--verify-r2` deliberately
  refuses to list it until its pyramid is on the bucket, so until then prod is
  correct at 41 and simply does not mention it.
- Progress and outcome without reading a transcript:
  `/Volumes/Nunatak/lidar_build/logs/r2_overnight.{log,status}`. Re-running the
  script is cheap and idempotent, so a died-overnight run just gets run again.

## 2026-09-21 21:43: pow_2018 FAILED -- Nunatak dropped off the bus

**pow_2018 is NOT published and nothing of it reached the bucket.** Production
is correct at 41 surveys; `--verify-r2` never listed it, so nothing advertises
bytes that 404. Verified: no `pow_2018` object under `cog/` or `pmtiles/`, and
`rclone backend list-multipart-uploads` returns empty, so no half-finished
upload is accruing storage.

**The volume is gone, not merely unmounted.** `diskutil list` shows disk0
(internal), disk3 (synthesized) and disk5 (Powder) only -- the disk4 that
carried Nunatak is absent from the device tree, so this wants a physical
reconnect, not a `diskutil mount`. It disappeared about three hours into the
31.5 GiB archive upload, which had been running fine.

**What survived:** both pow_2018 pyramids are on the INTERNAL disk
(`data/lidar/pmtiles/`, 12.1 GB and 16.9 GB) and are intact. Only the archive
COG lives on Nunatak. Nunatak also holds every other survey's archive, so if
the drive is actually failing that matters far beyond this upload -- though R2
already holds a copy of all but the gated ones, which is the backup that was
never called that.

**To resume once the drive is back:**

```bash
env -u PROJ_LIB -u PROJ_DATA sh tools/lidar/r2_publish_overnight.sh pow_2018
# then rebuild + install the catalogue per the recipe above; it should go to 42
```

**Two bugs this earned, both now fixed in the script.** They are why the
failure was quiet rather than loud:

- `expected()` lists a file only when it exists locally, so an unreachable
  volume DROPS it from the work list instead of failing. The archive was
  therefore never retried and never reported missing. The dangerous version is
  worse than what happened: had every product of a survey been on that volume,
  `expected()` would return nothing, `missing_for()` would be empty, and the run
  would log "all files verified on the bucket" and write `ok` to the status
  file having uploaded nothing at all.
- `>> "$LOG"` pointing into the missing volume makes the redirect itself fail,
  so `r2_put.sh` never ran and all six retries burned in thirty seconds.

Now a `preflight()` checks every directory the run reads from or writes to,
both before starting AND before each attempt, since the whole point is that it
vanishes mid-run; it aborts with exit 2 naming the directory. `say()` also
suppresses tee's own error, which otherwise printed a "No such file" line per
message and buried the abort.
