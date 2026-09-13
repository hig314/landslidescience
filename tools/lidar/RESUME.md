# Lidar hosting — state of play (2026-09-08, paused here)

Nineteen surveys built, verified, and **all on production**; all nineteen archive
COGs in Cloudflare R2 behind `lidar.landslidescience.org/cog/<id>.tif` (54 GB).
Glacier Bay is the complete 6,728-tile USGS delivery published at 1 m.
Paused 2026-09-08 with nothing pending on the lidar side; Hig switched topics.

**Imagery overlays** ("trace rasters") are now pre-baked locally with
`tools/imagery/bake_trace.py` (WebP q95, cubic, one zoom finer than native,
false colour NIR-R-G with contrast-limited equalisation and gamma 0.8, one
PMTiles per render mode) and uploaded as a file through the editor form. Planet
files stay on the droplet behind the editor gate. See CLAUDE.md "Trace rasters".

**If resuming:** the next data are more lidar, multibeam DEMs, and a topobathy
compositing facility. Design the vertical-datum reconciliation (NAVD88 vs tidal
MLLW) before adding multibeam; the manifest's `vertical_datum` is the hook.

## State

| Thing | Where | Status |
|---|---|---|
| Code | `main` | GitHub, droplet, and local all in sync (2026-09-08) |
| PMTiles (~7.3 GB, nineteen files) | `data/lidar/pmtiles/` local and R2 bucket `landslidescience-lidar` under `pmtiles/` (public `https://lidar.landslidescience.org/pmtiles/<id>.pmtiles`) | all 19 uploaded 2026-09-11 (sizes verified with `rclone check`); catalog `pmtiles_url` is now the R2 URL; `/lidar/pmtiles/<id>` serves a local copy if mounted else 302s to R2. Droplet copies deleted 2026-09-11 after prod verified on R2 (droplet 89% → 79% full); `/lidar/pmtiles/<id>` on prod now 302s to R2. Cloudflare bypass-cache rule extended to `/pmtiles/` by Hig and verified with first-ever ranged requests on kbay and glacier_bay (206, DYNAMIC) |
| Catalog | `data/lidar/catalog.geojson` (19 features) | synced |
| Archive COGs (~54 GB, predictor-compressed) | `/Volumes/Nunatak/lidar_build/cog/` + R2 bucket `landslidescience-lidar` under `cog/` | all 19 uploaded 2026-09-08; Cache Rule 'bypass cache' on /cog/ added by Hig |
| Paused InSAR kinematics | branch `insar-kinematics` (19 commits, rebased onto `main`, pushed) | dev-only; check it out to resume |

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
