# Lidar hosting — state of play (2026-09-06)

Ten surveys built and verified on dev; six deployed to production, four (Glen Alps, Eagle River, Columbia, Sitka — added 2026-09-06 evening) on the droplet awaiting the next code deploy. Archive COGs are moving to Cloudflare R2 (`tools/lidar/r2_sync.sh`); the catalog already links there.

## State

| Thing | Where | Status |
|---|---|---|
| Code | `main` (lidar + IceBridge guard + manifest) | pushed to GitHub, deployed to the droplet |
| PMTiles (~4.7 GB, ten files) | `data/lidar/pmtiles/` local and `/opt/landslidescience/data/lidar/pmtiles/` on the droplet | uploaded |
| Catalog | `data/lidar/catalog.geojson` (6 features) | uploaded |
| Archive COGs (~42 GB) | `/Volumes/Nunatak/lidar_build/cog/` + R2 bucket `landslidescience-lidar` under `cog/` | first 38 GB uploading overnight 2026-09-06; rerun `r2_sync.sh` for the four new ones |
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
