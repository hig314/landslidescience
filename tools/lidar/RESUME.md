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
| PMTiles (~7.2 GB, nineteen files) | `data/lidar/pmtiles/` local and `/opt/landslidescience/data/lidar/pmtiles/` on the droplet | synced; droplet 8.3 GB free |
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
- Droplet has ~11 GB free; the web tiles fit there for now. Moving them to the
  same bucket is one rclone command plus a catalog URL change.
- **Never visually verified by automation**: the wiper right-pane section and
  the z10 footprint branch. The browser-automation tab pauses
  `requestAnimationFrame`, so MapLibre's `load` never fires there, and headless
  Chrome hangs on the WebGL page (see the note in `dem_shade.js`). Everything
  server-side and the tile data path is verified; the visual pass is manual.

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
