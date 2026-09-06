# Lidar hosting — where this stopped (2026-09-06)

Everything below is **done and verified on dev**. Production is **untouched**.

## State

| Thing | Where | Status |
|---|---|---|
| Code | branch `lidar-hosting`, commit `618207d` | pushed to GitHub, **not merged to `main`** |
| Same work on `main` | commit `e54efd2` | local only — do not push `main`, it carries 19 unrelated InSAR kinematics commits |
| PMTiles (3.2 GB) | `data/lidar/pmtiles/` | local only — **upload not started** |
| Catalog | `data/lidar/catalog.geojson` | local only |
| Archive COGs (~28 GB) | `/Volumes/Nunatak/lidar_build/cog/` | local only, not intended for the droplet |
| Droplet | `root@143.198.140.54` | **nothing deployed**, 14 GB free of 78 GB |

`main` was deliberately left unpushed: it is 19 commits ahead with paused InSAR
kinematics work that was never signed off for production.

## To finish the deploy

1. **Upload the tiles** (~3.2 GB; the long part). Resumable — rerun it and rsync
   skips finished files and continues partial ones:

   ```bash
   cd ~/Claude_projects/landslidescience
   rsync -av --partial --progress --exclude 'cog/' \
     data/lidar/ root@143.198.140.54:/opt/landslidescience/data/lidar/
   ```

   Run it detached so a laptop restart doesn't kill it:
   `nohup rsync ... > /tmp/lidar_rsync.log 2>&1 &`

   Droplet has 14 GB free; 3.2 GB fits, leaving ~11 GB. Check `df -h /` after.

2. **Deploy the code.** Prod tracks `main`, so either merge `lidar-hosting` into
   `main` first, or check the branch out on the droplet:

   ```bash
   ssh root@143.198.140.54 'cd /opt/landslidescience && \
     git fetch origin && git checkout -B lidar-hosting origin/lidar-hosting && \
     docker compose -f docker-compose.yml -f docker-compose.prod.yml build && \
     docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate'
   ```

   No migrations, no new groups — this adds routes and static files only.

3. **Verify on prod**:

   ```bash
   curl -sI https://landslidescience.org/lidar/catalog.geojson
   curl -sI -H 'Range: bytes=0-999' \
     https://landslidescience.org/lidar/pmtiles/homer_2019.pmtiles   # expect 206
   ```

## Known gaps

- **`/lidar/cog/<id>.tif` will 404 in prod.** The archives are 28 GB and stay
  local, but `catalog.geojson` still advertises `cog_url`, so the preview page's
  "COG" download links are dead until the archives move to object storage.
  Either ship them somewhere, or drop the link when the file is absent.
- **Cloudflare R2 is the intended home** for both products (~$0–0.30/month; the
  free tier is 10 GB and zero egress). Radiant Earth's **Source Cooperative** is
  a free alternative for open geospatial data that Hig wanted looked at more
  closely. Neither is set up.
- **Not visually verified**: the wiper right-pane section, and the z10 footprint
  branch. Both were built after the browser-automation tab stopped rendering
  (hidden tab pauses `requestAnimationFrame`, so MapLibre's `load` never fires —
  see the note in `dem_shade.js`). Server-side checks all pass.
- The `lidar-hosting` branch is `origin/main` + lidar, i.e. **without** the
  kinematics commits. `map.js` there was syntax-checked and all integration
  hooks confirmed present, but that exact combination was never opened in a
  browser.

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
