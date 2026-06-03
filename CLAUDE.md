# CLAUDE.md — landslidescience.org

Public-facing Django site at <https://landslidescience.org>. Companion to the
private Tethys stack at `github.com/hig314/tethys-timescale-grafana`.

## Development workflow — **dev → test → (revise → test) → GH + production**

This is a load-bearing principle, not a default. **Never push to GitHub or
deploy to production until the user has tested the feature in local dev and
explicitly approved**. Public-facing site with real users (or about to have
them); a regression visible at landslidescience.org is real harm.

The flow:

1. Build in local dev.
2. Tell the user it's ready, ask them to test.
3. If they report issues: revise in dev, ask them to retest.
4. **Only after explicit "ship it" or equivalent**: `git push origin main`,
   then SSH to the droplet and `git pull && docker compose ... up -d`.

Commits can land in `main` of the user's local repo before testing — that's
fine, it's local — but the *push* to GitHub waits for sign-off, because GH
is the sync point with production. If a fix is needed after a push, that's
also fine: another commit, another push, another deploy. What we avoid is
pushing untested code to GH.

## Layout

| App | Purpose |
|---|---|
| `pages` | Editable site content (homepage, `/tracyarm2025/`). `Page` model in SQLite, edited via `/admin/`. |
| `inventory` | Public landslide inventory map. Reads `tethys_db.landslides` (PostGIS) over the shared Docker network via raw psycopg2 — no Django ORM models for landslide data. The only Django model in this app is `LandslideEditMeta` (audit log, in SQLite). |

## URL map

| Path | Audience | Notes |
|---|---|---|
| `/` | public | Homepage (Page model, edited from /admin/) |
| `/tracyarm2025/` | public | Time-aware embargo page (Page model) |
| `/inventory/` | public *(behind preview password during review)* | Public landslide inventory map |
| `/inventory/methods/` | public *(behind preview password)* | Methods doc |
| `/inventory/<slug>/` | public *(behind preview password)* | Slug deep-link → map at the named landslide |
| `/inventory/api/*` | public *(behind preview password)* | GeoJSON / JSON endpoints used by the map |
| `/inventory/preview/` | anyone | Login page for preview password |
| `/inventory/manage/` | inventory_editors + Hig | Searchable list of all records |
| `/inventory/manage/<id>/` | inventory_editors + Hig | Edit form for non-geometry fields |
| `/inventory/manage/settings/` | inventory_editors + Hig | Map display settings (colors, point sizes) |
| `/inventory/export/` | public *(behind preview password)* | Download zip of GeoJSON + QGIS .qml styles |
| `/inventory/manage/import/` | inventory_editors + Hig | Upload zip/.geojson; preview diff; confirm to apply |
| `/admin/` | site_admins (Page perms) + Hig | Django admin — Page model + User/Group management |

## Auth & permissions

Two non-superuser groups (created/maintained idempotently by `python manage.py init_groups`):

| Group | What they can do | Where they work |
|---|---|---|
| `inventory_editors` | Edit landslide records via custom UI | `/inventory/manage/` |
| `site_admins` | Edit Page content (homepage, /tracyarm2025/) | `/admin/` |

Adding a user (do this via `/admin/auth/user/`):
1. Create user with a temp password.
2. Set `is_staff=True` (required to log in at /admin/login/, which is the only login page).
3. For inventory editors: add to the `inventory_editors` group. They will see an empty Django admin landing — they navigate to `/inventory/manage/` for their work.
4. For site admins: add to the `site_admins` group. They get full CRUD on Page in /admin/.

Hig (superuser) bypasses all role checks.

If the "empty admin landing for editors" friction becomes annoying, wire up `django.contrib.auth.urls` at `/accounts/login/` and update `inventory.auth.inventory_editor_required` to redirect there. For now, deferred.

## Pre-launch preview password

While `INVENTORY_PREVIEW_PASSWORD` is set, all `/inventory/*` paths require either authentication OR a session flag set by entering the password at `/inventory/preview/`. **Unset the env var to make `/inventory/*` fully public (post-launch).**

To set or change the preview password on production:

```bash
ssh root@143.198.140.54 '
  cd /opt/landslidescience
  # Set or replace INVENTORY_PREVIEW_PASSWORD in .env
  sed -i "/^INVENTORY_PREVIEW_PASSWORD=/d" .env
  echo "INVENTORY_PREVIEW_PASSWORD=YOUR-PASSWORD-HERE" >> .env
  # Restart container so the new env takes effect
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate
'
```

To remove the barrier post-launch: same flow but with `INVENTORY_PREVIEW_PASSWORD=` (empty value), or delete the line entirely.

## Production

Droplet: `root@143.198.140.54`, deployed at `/opt/landslidescience` (git clone of this repo). Caddy (running in the Tethys monitoring stack at `/opt/monitoring`) reverse-proxies `landslidescience.org` to the `landslidescience-web` container. The container joins both `monitoring_external` (so Caddy can reach it) and `monitoring_internal` (so it can reach `tethys_db`).

Deploy:

```bash
ssh root@143.198.140.54 'cd /opt/landslidescience && \
  git pull && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate'
```

After a deploy that adds new migrations or new groups: run `python manage.py migrate` and/or `python manage.py init_groups` once via `docker exec landslidescience-web ...` (migrations also auto-run on container startup via `entrypoint.sh`, so usually only `init_groups` is needed).

Production `.env` lives at `/opt/landslidescience/.env`, mode 600, never committed. DB credentials in there mirror the monitoring stack's `.env`.

## Local dev

```bash
docker compose up -d                     # uses docker-compose.override.yml automatically
# → http://127.0.0.1:8001/
```

The local container joins the running local Tethys stack's `tethys-timescale-grafana_internal` network (declared external in the override), so the inventory map page works locally if the local Tethys stack is up.

If the Tethys stack isn't running locally, the homepage and admin still work; only `/inventory/*` will fail (no DB to read from).

Local dev `.env` has `INVENTORY_PREVIEW_PASSWORD=devpreview2026` for testing the barrier; change it freely.

## Editing landslide records (workflow for editors)

1. Log in at `/admin/login/` (yes, even though you're not going to /admin/).
2. Hit `/inventory/manage/`.
3. Search by name or filter by type/class/subset; click into a record.
4. Edit non-geometry fields. Save.
5. The audit log records who and when. The list view shows it.

To bulk-add new landslides, use the upload path — see *Inventory induction workflow* below.

**Not yet supported via UI**: polygon editing in-browser, click-on-map landslide creation. Those flow through QGIS/PostGIS for now.

## Inventory induction workflow

New landslides enter the inventory via upload at `/inventory/manage/import/`. The flow:

1. Editor uploads a GeoJSON zip, `.geojson`, `.gpkg`, `.shp` (+ sidecars), or `.kml`. Multi-format ingestion goes through `pyogrio` + `shapely` → normalized GeoJSON FeatureCollections.
2. Upload preview shows a diff (would-add / would-update / would-skip) and surfaces normalization warnings.
3. **Common-fields form** at Apply: blanket values for fields the user wants to set on all new records (e.g. `noted_by`, `landslide_type`). `owner` is auto-populated from the logged-in user (data-admin only). Subset choices exclude locked subsets (e.g. `alaska-2025`). `unique_name` is excluded from blanket population (must be unique per record).
4. On Apply, new landslides are inserted with `reviewed_at` NULL — i.e., **pending**.
5. The user is redirected to `/inventory/manage/review/<first_pending_id>/` — a review form with a mini-map (basemap selector only, no measure/circles, polygons embedded server-side). The form excludes rule-populated columns (those should be computed at save time; see below). On save, `reviewed_at` is stamped and the view redirects to the next pending record.
6. Pending records survive logout / browser close. They surface again next time the editor visits review.

Upload-side normalization (in `io_geojson.py`):
- `_normalize_controlled_vocab` — case-folds + trims `role` and `landslide_type`, applies vocab aliases (`Deposit` → `deposit`, etc.).
- `_synthesize_landslides_from_flat_polygons` — when a flat-polygons-only file is uploaded, polygons grouped by `unique_name` are inferred into a synthesized landslide record (landslide_type inferred from the polygon roles).
- `LANDSLIDES_AUTO_COLS = ('created_at', 'updated_at', 'reviewed_at')` — these are server-managed and ignored in uploads.

**Half-implemented (tracked as task #61)**:
- Per-record rule cascade is NOT applied on save (review-form save just stamps `reviewed_at`). Rule-populated columns are saved as whatever was uploaded — i.e. blank/defaults if not provided. The columns are also excluded from the form, so the editor cannot supply them by hand either. Net: those fields get silently dropped to NULL on induction.
- Pending records (`reviewed_at IS NULL`) are NOT hidden from public views — the home counts, features API, snapshot build, and slug lookups all include them. This is intentional only until the rule cascade lands; once it does, the public surfaces should filter on `reviewed_at IS NOT NULL`.
- `/inventory/manage/` has no "show only pending" filter UI yet.

## GeoJSON round-trip

The export/import flow lets you snapshot the current inventory, edit in QGIS or by hand, then re-apply. Format:

```
landslidescience_inventory_YYMMDD.zip
├── manifest.json                       # export format version, timestamp, column lists
├── landslides.geojson                  # 1 feature/row, Point at representative centroid, all columns in properties
├── landslide_polygons.geojson          # 1 feature/row, MultiPolygon, properties carry landslide_id/role/area/thickness
├── landslide_polygons_flat.geojson     # denormalized: same polygons + parent landslide attrs merged in (export-only)
├── landslides.qml                      # QGIS style for the points layer (categorized by landslide_class)
├── landslide_polygons.qml              # QGIS style for the polygons layer
└── landslide_polygons_flat.qml         # byte-identical copy of landslide_polygons.qml so QGIS auto-loads it on the flat file
```

Column names match PostGIS exactly (snake_case). Geometries use `ST_AsGeoJSON(geom, 15)` — full IEEE 754 precision so round-trip is byte-stable.

Each landslide feature carries a **Point geometry** at its representative centroid (slow → body polygon; catastrophic → primary source, fallback deposit), so QGIS can use `landslides.geojson` directly as a point layer without joining the polygons file. The centroid is computed in EPSG:3338 (NAD83 / Alaska Albers, equal-area), then reprojected to WGS84 for the Point. Four explicit derived properties accompany it: `centroid_albers_x`, `centroid_albers_y` (meters), `centroid_lat`, `centroid_lon` (decimal degrees). These derived values are computed at export time and ignored on import — they're not DB columns.

**Flat polygons file**: the same 1731 polygons but with each parent landslide's columns and centroid_* fields merged into the polygon's properties. A single-file alternative to (polygons + landslides + join). Export-only — re-uploading the zip silently ignores the flat file. Useful when you want one drag-and-drop layer in QGIS that already carries the landslide attrs.

**QML styles**: the three `.qml` files match the inventory map's color scheme exactly — colors are read from `map_settings` at export time, so admin customizations propagate. QGIS auto-loads a `.qml` only when its basename matches the `.geojson` exactly, so the two polygon `.qml`s are byte-identical copies under different filenames (`landslide_polygons.qml` and `landslide_polygons_flat.qml`) — that way both polygon files auto-style on drag-and-drop. The polygon style relies on the `landslide_class` column: it's already present on the flat file; on the normalized `landslide_polygons.geojson` it requires a QGIS table-join to `landslides.geojson` (with empty join-field-prefix so the column appears unqualified).

**First-shot scope**: import only UPDATEs existing records (matched by id). Records or polygons with new ids are previewed but not inserted; records present in DB but missing from upload are kept silently. INSERT support and upload-driven deletion are deferred.

Schema fingerprint: `python manage.py roundtrip_test` verifies that download → upload-without-changes is a byte-identical no-op against the live DB.

Workflow:
1. Click "⬇ Export" on `/inventory/manage/`. Save the zip.
2. Unzip in QGIS — drag the `.geojson` files in (styles auto-apply from the sibling `.qml`), edit attributes/geometries.
3. Re-zip (must contain `landslides.geojson` + `landslide_polygons.geojson` at the top level; other files are ignored).
4. Click "⬆ Import" → upload → preview → confirm.

## Planet Stories integration

Two Planet Stories formats are referenced from landslide records:

- **timelapse** — multi-frame animation with a backing MP4 at
  `https://storage.googleapis.com/planet-t2/<slug>/movie.mp4` (anonymous,
  public). These get archived locally to `data/planet_stories/<slug>.mp4` by
  the `archive_planet_stories` management command and served through this
  app via a stable URL (see below). The in-app player renders these without
  leaving the site.
- **comparison** — before/after wiper widget rendered by Planet's SPA; no
  MP4 to archive. Records pointing at these keep an external link to
  planet.com; the link survives only as long as Planet does.

Schema:

| Table | Role |
|---|---|
| `planet_stories` | One row per distinct slug — `story_type`, `mp4_archived_at`, `mp4_size_bytes`, `last_probed_at`, `manually_set`. |
| `landslide_planet_stories` | M:N membership — a landslide can reference multiple stories; a story can be referenced by multiple landslides. `sort_order` controls per-landslide display order. |

The legacy `landslides.planet_story_link` text column is still written by
the edit form and kept in sync with the join tables on save. It will be
dropped after the edit form gains a proper multi-story management UI.

### Stable serving URL — load-bearing for snapshots

Archived MP4s are served at `/inventory/planet/<slug>.mp4` (no trailing
slash; `.mp4` suffix in the URL). **This URL is load-bearing**: published
snapshots embed it and reference it from their static HTML. The backing
storage may change (S3, CDN, etc.) but the URL pattern must remain stable.
If it ever has to change, add a redirect — do not just rename it.

The slug shape is regex-constrained at the URL layer (`[A-Za-z0-9_-]+`) so
the view can safely treat it as a filename without traversal risk.

### Operational commands

```bash
# Probe + classify all slugs, then download any timelapse MP4s not yet archived.
docker compose exec web python manage.py migrate_planet_stories
docker compose exec web python manage.py archive_planet_stories
```

`migrate_planet_stories` is idempotent — re-running it picks up any new
slugs added through the edit form, classifies them by HEAD-probing GCS, and
stamps disk-archive metadata. `--no-probe` skips the GCS check (useful when
offline). `--dry-run` rolls back at the end.

## Inventory map UI structure

The sidebar at `/inventory/` is a three-tab layout with a pinned strip on top:

- **Pinned strip** (always visible above the tabs): basemap quick-select, type checkboxes, "Limit to map view" toggle. The Limit toggle is universal — it affects inventory class counts AND the seasonal histogram + time-series chart.
- **Inventory tab**: class checkboxes + dot-color legend + record count breakdown.
- **Reference maps tab**: categorized basemap cards (Imagery / Topo / Historical / Other) with thumbnails, Windy.com-style. "Reference layers" section at the bottom for toggleable overlays (currently: Survey circles, and the two USGS susceptibility models lw / n10).
- **Analysis tab**: triggers for the seasonal histogram, time-series chart, and the lw × n10 susceptibility scatter — each opens as a floating, draggable, resizable panel (see *Floating analysis panels* below). The histogram + time-series respect the Limit toggle.

(The n10/lw susceptibility range sliders live in the **Inventory** filter panel alongside the source-area / deposit-area / volume / age dual-range filters.)

There is no on-map legend or floating basemap-picker — those got removed in favor of the Inventory tab's color key.

**Basemap thumbnails** ship as committed static assets at `inventory/static/inventory/img/basemap-thumbs/*.{png,jpg}` (~180 KB total). The HTML template injects a `basemapThumbs` dict into `LS_CONFIG` via `{% static %}` → WhiteNoise serves them with content-hashed filenames + `Cache-Control: max-age=315360000, public, immutable`. The snapshot build sets `staticBase: './static/'` so the same machinery works in the offline bundle.

**Susceptibility overlays** (Reference layers → lw / n10): two toggleable, self-hosted raster overlays of the USGS Slope-Relief Threshold landslide-susceptibility models (Belair et al. 2024, 90 m). Mutually exclusive in the UI (one model at a time). **Alaska coverage** — this is why they're self-hosted rather than the old `tiles.arcgis.com/.../US_Landslide_Susceptibility` MapServer, which is conterminous-US only.

How it works (pre-colored tiles):
- `tools/build_susc_tiles.sh` reprojects each AK GeoTIFF (EPSG:3338, Int32 0–81 = count of susceptible 10 m sub-cells per 90 m cell, NoData = 2147483647) to EPSG:3857, clips to an Alaska window (the Aleutians straddle the antimeridian; an unclipped warp yields a globe-width canvas), bakes color with `gdaldem color-relief`, tiles z3–10 (XYZ), and prunes fully-transparent ocean tiles. Output: `data/susc_tiles/{lw,n10}/{z}/{x}/{y}.png` (gitignored, never in the image).
- **Color scheme = discrete frequency-ratio classes** derived from sampling the models at the 314 catastrophic source centroids (see *Susceptibility analysis* below). Per-model break files: `tools/susc_color_n10.txt` (breaks `0 / 1–62 / 63–80 / 81`) and `tools/susc_color_lw.txt` (`0 / 1–74 / 75–80 / 81`) → Low / High / Very High (value 0 transparent). The script picks `tools/susc_color_<model>.txt` if present, else the shared `tools/susc_color.txt`. **To recolor / re-break: edit those files and re-run the script** (cost = re-tiling, a few minutes).
- **Why pre-colored, not recolored client-side:** MapLibre GL JS (5.5) does **not** implement Mapbox's `raster-color`/`raster-value` paint properties — confirmed absent from the 5.5.0 bundle — so a single-band value raster can't be colorized in the browser without a custom WebGL layer. `map.js` renders the tiles as a plain raster layer (`raster-opacity: 1`; per-pixel alpha is in the tiles).
- Served by the `susc_tile` view in `landslidescience/urls.py` at `/tiles/susc/<model>/{z}/{x}/{y}.png` with an immutable cache header. `data/` is volume-mounted (`docker-compose.yml`) in both dev and prod, so one route serves both. The build script runs locally (the web container has no GDAL); tiles ship to the droplet via a **separate** rsync (the main deploy rsync excludes `data/`).
- **Cache-busting:** tile URLs are not content-hashed but carry a 1-year immutable header, so `map.js`'s `SUSC_TILE_V` token is appended (`?v=N`) and **must be bumped whenever the tiles are rebuilt with different pixels** (recolor/reclass), or clients keep the stale image. Currently `v2` (discrete classes; v1 was the continuous YlOrRd ramp).
- Snapshots: tiles are not bundled (too big); `susTileBase` stays `/tiles/susc/`, so a toggled overlay is simply inert in an offline bundle.

**Susceptibility value sliders & analysis.** Each landslide's sampled n10/lw value (0–81) lives in `inventory/static/inventory/susc_values.json` (`{id: {n10, lw}}`), produced offline by sampling the rasters at `landslides.centroid_albers_x/y` (already EPSG:3338) — the web container has no GDAL, so this is regenerated locally and committed, going stale when landslides are added/moved. `map.js` merges it into the point features as `n10`/`lw` and the two dual-handle sliders in the Inventory filter panel filter on them (reusing `_setupDual`/`addRangeFilter`), with a live "N of 1424 in range" readout. Key finding from the analysis: catastrophic source values **saturate at the 81 ceiling** (median 81; ~53% n10 / ~68% lw at max), both models score AUC ~0.96–0.97 (lw marginally higher), so the predictive signal is a sharp low-vs-high contrast rather than a graded ramp — which is why landslide-quintile bands collapse and FR-based class breaks are used instead.

**lw × n10 scatter (Analysis tab).** A brushable scatter of the joint susceptibility distribution: a pale-Blues terrain-density backdrop (quintile bins) with a dark-Reds landslide grid on top (log bins for raw count, quintile for the proportion mode), drawn on a canvas behind an SVG overlay. Drag a box to set the n10/lw sliders (two-way synced); "clear box" resets. Terrain density is precomputed offline into `inventory/static/inventory/susc_terrain_density.json` (82×82 joint histogram of all AK cells, `grid[n10*82+lw]`). Reds are always darker in value than blues (colorblind-safe); proportion is shown as landslides per 1000 km² (each cell = 0.0081 km²).

**Floating analysis panels.** The seasonal histogram, time-series, and scatter are all floating/draggable/resizable panels via the shared `makeFloatingPanel(panel, {handle, toggle, close, onResize, onChange})` helper in `map.js` (header is the drag handle; `ResizeObserver` redraws; each self-contained — no shared `charts-container` strip). Time-series y-axis is events/month (finest bin granularity). Chart subtitles only say "in view" when "Limit to map view" is checked.

Task #69 (self-hosted recolorable susceptibility) — **done**. Follow-on: sample lw/n10 values at each landslide centroid and expose susceptibility-based filtering — **done** (sliders + scatter above).

## Forward-looking integration

The Tethys monitoring stack (sensor dashboards, access-controlled admin) stays in its own repo. Public-facing components migrate to this repo as needed; they read from `tethys_db` directly via psycopg2 (no Django models for landslide data — keeps PostGIS as the single source of truth, owned by the Tethys repo).

The Tethys-side `landslides` app was decommissioned 2026-05-20. Source code was moved to `archived_apps/tethysapp-landslides/` in the Tethys repo and removed from `Dockerfile.tethys`, so `/apps/landslides/` 404s. The `landslides` table in `tethys_db` is unchanged — still owned by the Tethys repo, read directly by this app over the shared Docker network.

## Known future work: derived-value layer

Several fields in the inventory have a "preferred" value that's chosen from multiple sources by domain rules (e.g., `volume_preferred` overridden by `volume_site_specific` when present; otherwise inferred from `polygon_volume` = thickness × area aggregated across polygons). Today, the editor UI shows all of these as independent free-text fields with no enforced relationship — it's possible to set `volume_preferred` to something inconsistent with the polygon-derived total.

When ready, this wants a unified inference / validation layer: rules that compute derived values from inputs, flag inconsistencies, and let the editor either accept the computed value or explicitly override (with the override recorded as the source). Affects volume, area, dates (date_min / date_max / seismic_datetime priority), and possibly classification (when class implies type or vice versa).

Tracking this as "deferred but anticipated" so when we get to it, the UI design accounts for it rather than retrofitting.

## Conventions

- Don't commit `.env` or `data/` (gitignored).
- Hig is the superuser on production. Reset password via: `docker exec -it landslidescience-web python manage.py changepassword Hig`.
- WhiteNoise serves static files in production; runserver serves them in dev.
- Caddy auto-issues Let's Encrypt certs; non-canonical domains 301 to canonical.
- Audit log lives in landslidescience SQLite (`LandslideEditMeta`), not in PostGIS, so the Tethys schema stays clean.
- After editing a landslide record, the inventory caches (`features`, `home_counts`, `timed_events`, `timeline_events`, `slug_map`) are invalidated automatically. No manual cache flush needed.
