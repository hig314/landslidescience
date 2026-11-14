# CLAUDE.md — landslidescience.org

Public-facing Django site at <https://landslidescience.org>. Companion to the
private Tethys stack at `github.com/hig314/tethys-timescale-grafana`.

**This file is the short, always-loaded part**: the rules, the map of the
project, and an index of where the detail lives. The per-feature reference is
in `docs/` and is read on demand. **Before editing a feature, read its
`docs/` file** (table below) and **[HAZARDS.md](HAZARDS.md)** (the footgun
list, every entry earned by a real incident). New to the repo:
**[ONBOARDING.md](ONBOARDING.md)**. Two work streams run in parallel in
separate worktrees: read **[WORKSTREAMS.md](WORKSTREAMS.md)** before touching
anything under `tools/lidar/`.

## Where the detail lives

| Working on | Read first |
|---|---|
| Resuming work, "what shipped / what is open", known bugs left alone | [docs/STATE.md](docs/STATE.md) (the one dated snapshot; it rots) |
| Record edit/review forms, autosave, class rule inputs, polygon draw/edit (Terra Draw), features vocabulary, naming standard + flag scans, subsets/regions/country, import/induction, collisions, GeoJSON round-trip, Planet Stories, permanent delete | [docs/RECORDS.md](docs/RECORDS.md) |
| `/inventory/` map page: sidebar, wiper, **URL hash grammar** and default views, filter parity, symbology (`ls_colors.js`), shared basemap code, pointer policy (`ls_tools.js`), profile / probe / go-to, imagery review history | [docs/MAP_UI.md](docs/MAP_UI.md) |
| Overlay framework; OPERA + NRCan InSAR, coherence, OPERA DIST, Canadian / NOAA basemaps, erosion rates (switched off), shaded relief, susceptibility (USGS, DGGS, NASA) | [docs/OVERLAYS.md](docs/OVERLAYS.md) |
| Permafrost products, 60 m downscale, Surface properties, fixed-scale slopes, contours | [docs/PERMAFROST.md](docs/PERMAFROST.md) |
| Analysis panel (quantity cube, axes = wiper panes, sample, brush, paint, reviewed area), floating panels, OPERA joint histogram | [docs/ANALYSIS.md](docs/ANALYSIS.md), plan in [ANALYSIS_TOOL_PLAN.md](ANALYSIS_TOOL_PLAN.md) |
| Lidar/bathymetry hosting (COG + PMTiles, R2, gating, owner-only), build traps, `dem_shade.js`, the `/lidar/` viewer URL / export / 3D context, lidar in `/inventory/` | [docs/LIDAR.md](docs/LIDAR.md); publishing state and recipe in `tools/lidar/RESUME.md`; live truth at `/lidar/audit/` |
| Terrain comparison `/lidar/compare/`, saved comparisons | [LIDAR_COMPARE_PLAN.md](LIDAR_COMPARE_PLAN.md) §10–11, and its entry in docs/STATE.md |
| Landform traces (kinds, groups, group profile), lake soundings, faults & folds layer | [docs/TRACES.md](docs/TRACES.md), [tools/soundings/README.md](tools/soundings/README.md) |
| Editor GeoTIFF uploads to trace over | [docs/TRACE_RASTERS.md](docs/TRACE_RASTERS.md) |
| Mirrored third-party inventories, promotion | [docs/EXTERNAL_INVENTORIES.md](docs/EXTERNAL_INVENTORIES.md) |
| Explorer `/inventory/table/` | [docs/EXPLORER.md](docs/EXPLORER.md) |
| `/files/`, photo drops `/drops/`, field photos | [docs/FILES_DROPS_PHOTOS.md](docs/FILES_DROPS_PHOTOS.md) |
| `/glaciers/` | [docs/GLACIERS.md](docs/GLACIERS.md) |
| Groups, permissions, the two sign-in doors, sessions | [docs/ACCOUNTS.md](docs/ACCOUNTS.md) |
| Refresh dev from prod, robots/indexing, Umami analytics, Tethys relationship | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Collaborator write-only data staging | [DATA_DROP.md](DATA_DROP.md) |
| Topobathy compositing (the other work stream) | `tools/lidar/TOPOBATHY_PLAN.md` |
| Required strength / Scoops3D (exploratory; overlay row off behind `CREQ_ACTIVE`) | `tools/terrain/scoops/README.md` |

A phrase like "CLAUDE.md §The analysis panel" in older notes, code comments
or memory means the section of that name in the `docs/` file above.

**Keeping this file short.** A new feature's design note goes in its `docs/`
file (new file + a row here if none fits). "Shipped on <date>" and open
threads go in docs/STATE.md. This file gains a line only for a rule that
applies whatever is being worked on. Machine-local holds that must never be
committed (the repo is public) live in `CLAUDE.local.md`, which is gitignored.

## Invariants that apply everywhere

Each is explained where it is defined; they are listed here because breaking
one is silent.

- **One source of truth per shared behaviour; add to it, never beside it.**
  Basemaps and tile-URL transforms: `basemaps.js`. Colours and point styling:
  `ls_colors.js`. Glacier overlays: `ls_overlays.js`. Who owns a click, drag,
  cursor, Escape: `ls_tools.js` (register, never another `map.on('click')`).
  URL state: `ls_hash.js` + the `_VIEW_PARAMS` table in `map.js`. Filterable
  landslide properties: `_FILTER_PROPS_SQL` in `views.py` (feeds points and
  polygons). Public visibility: `views.public_landslide_filter`. Layer
  filters: the `_landslideFilterLayers` registry. DEM readers: `dem_read.js`.
- **The URL grammar is additive only.** Stored default views, snapshots and
  shared links hold these strings for good. Run
  `node tools/hash_codec_check.js OLD.js NEW.js` before a grammar change.
- **Load-bearing URLs**: `/inventory/planet/<slug>.mp4`,
  `/inventory/photo/<id>/…`, overlay/row ids named in stored views. Redirect,
  never rename.
- **Landslide data is the left/right-pane-parity case; imagery and overlays
  are per pane.** Anything added to the landslide data stack is mirrored to
  the wiper's second map through the shared def builders.
- **Hand-kept agreements are checked by `manage.py check_invariants`**; a
  new one gets a check there, not just a comment.
- **The class rule has hand-kept dependants**: `forms.DISPLAY_REQUIRED`,
  `map.js` `CLASS_ORDER`, `views._CLASS_COLOR`, the three copies of the
  `year_num` era expression. A label missing from `CLASS_ORDER` hides the
  record with no sign.
- **PostGIS commands run once per environment** (dev and prod are separate
  databases); a deploy that SELECTs a new column needs its `migrate_*` run
  right after. Caches are per gunicorn worker; `_invalidate` handles it.
- **`data/` is not in git or the image.** Built data (tiles, cubes, grids)
  ships by its own rsync or R2 push; the droplet has ~5 GB free, so big
  archives go to R2, not the droplet.
- **Gated lidar is enforced on the bytes** and gating is not retroactive on
  R2. Never rsync all of `data/lidar/` without reading `CLAUDE.local.md`.
  Surveys that must not leave this machine go in the gitignored
  `tools/lidar/datasets.local.json`; every tool reads the manifest through
  `tools/lidar/lidar_manifest.py`, never `datasets.json` directly.
- **Standing hold:** no comparison of the required-strength (Scoops3D) maps
  with landslide mapping until Hig lifts it.
- **Docs that must move with the code**: `/inventory/methods/` and
  `/lidar/methods/` (see Conventions), and the feature's `docs/` file.

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
| `files` | Admin-managed public file hosting. `HostedFile` model in SQLite; bytes stored under `data/media/`; served (unlisted) at `/files/<name>`. See *Hosted files* below. |
| `drops` | Photo drops: upload links for folders of photos. `Drop`/`DropFile` in SQLite; bytes in a **private R2 bucket**, never on the droplet. See *Photo drops* below. |

## URL map

| Path | Audience | Notes |
|---|---|---|
| `/` | public | Homepage (Page model, edited from /admin/) |
| `/tracyarm2025/` | public | Time-aware embargo page (Page model) |
| `/inventory/` | public | Public landslide inventory map |
| `/inventory/table/` | public | Inventory explorer — spreadsheet-style filter / sort / cross-tab |
| `/inventory/api/table/` | public | Columnar dump of the whole visible table (backs the explorer) |
| `/inventory/methods/` | public | Methods doc |
| `/inventory/<slug>/` | public | Slug deep-link → map at the named landslide |
| `/inventory/api/*` | public | GeoJSON / JSON endpoints used by the map |
| `/inventory/api/external/*` | public (read) | Mirrored third-party inventories; `.../promote/<id>/` is editor-only |
| `/inventory/manage/` | inventory_editors + Hig | Searchable list of all records |
| `/inventory/manage/<id>/` | inventory_editors + Hig | Edit form for non-geometry fields |
| `/inventory/manage/<id>/delete/` | **superusers only** (POST) | Permanent hard-delete (Danger zone); distinct from deprecate |
| `/inventory/manage/settings/` | inventory_editors + Hig | Map display settings (colors, point sizes) |
| `/inventory/export/` | public | Download zip of GeoJSON + QGIS .qml styles |
| `/inventory/api/review/*`, `/inventory/tiles/review/…` | any signed-in account | Imagery review history: log, meta, share, point, tiles — one's own, or another's after they opt in (see *Imagery review history*) |
| `/lidar/` | public | Standalone lidar/bathymetry viewer + the ranged `pmtiles`/`cog`/catalogue routes |
| `/lidar/compare/` | public | Terrain comparison: small multiples at one ground scale/tilt/VE/shading, linked navigation, ground-draped scale bar + north arrow, PNG export. State only in the URL; public catalogue only. `/lidar/compare/spike/` = same page (dev-time address). See *Terrain comparison* in State of play |
| `/lidar/compare/saved/` | public | Saved comparisons list (`pages.SavedComparison`); edited in `/admin/pages/savedcomparison/` |
| `/lidar/compare/s/<code>/` | public | Short link (3 hex) → 302 to the saved query + `sv=<code>` |
| `/lidar/compare/save/` | superuser or `pages.add/change_savedcomparison` (POST JSON) | Submit / Update from the comparison page; refuses gated survey ids |
| `/lidar/methods/` | public | Reader-facing account of the viewer (`pages/lidar_methods.html`); the sidebar keeps a tooltip per control and a `?` per section linking here |
| `/lidar/audit/` | data admins | Publish-state check: for every survey, built / on R2 / in the catalogue / what the gate does |
| `/lidar/?catalog=dev` | dev only | Same viewer over research surfaces in `data/lidar_dev/` (`lidar/dev/*` routes 404 unless DEBUG). Kept OUTSIDE `data/lidar/` because the redeploy rsync in `tools/lidar/RESUME.md` copies all of `data/lidar/` to the droplet. |
| `/inventory/manage/import/` | inventory_editors + Hig | Upload zip/.geojson; preview diff; confirm to apply |
| `/s/t.js`, `/s/api/send` | public | First-party analytics beacon, forwarded to Umami (`landslidescience/analytics.py`) |
| `/traffic/` | superusers + site_admins | Signs the current Django user into the Umami dashboard (Umami's own login is disabled) |
| `/traffic/optout/` | public | Per-browser analytics opt-out (signed-in browsing is excluded automatically) |
| `/traffic/services/` | superusers + site_admins | Are the outside services the map reads answering (written by the `tools/ops/probe_cron.sh` cron) |
| `/files/<name>` | public *(unlisted)* | Serves an admin-uploaded `HostedFile` by its URL token (no auth). |
| `/drops/` | inventory_editors + Hig | Photo drops: create upload links, see what arrived |
| `/drops/<slug>/` | anyone with the link (+ optional passphrase) | Folder uploader — browser → private R2 directly, nothing through the droplet |
| `/drops/<slug>/files/` | any signed-in account | Gallery of a drop; `links.txt` = presigned URLs for bulk download |
| `/admin/` | site_admins (Page + HostedFile perms) + Hig | Django admin — Page + HostedFile models + User/Group management |

## Production

Droplet: `root@143.198.140.54`, deployed at `/opt/landslidescience` (git clone of this repo). Caddy (running in the Tethys monitoring stack at `/opt/monitoring`) reverse-proxies `landslidescience.org` to the `landslidescience-web` container. The container joins both `monitoring_external` (so Caddy can reach it) and `monitoring_internal` (so it can reach `tethys_db`).

Before a deploy, in dev: `docker exec landslidescience-web-1 python manage.py check_invariants` (the hand-kept agreements, as code; exit 1 = do not ship).

Deploy:

```bash
ssh root@143.198.140.54 'cd /opt/landslidescience && \
  git pull && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build && \
  docker exec landslidescience-web python manage.py deploy_ready --wait 30 && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate'
```

`deploy_ready` stops the deploy while an editor's GeoTIFF upload is still
baking (the bake is a thread in the container about to be replaced; a deploy
killed one on 2026-11-14). The image is already built at that point, so
re-running the last line later is enough.

After a deploy that adds new migrations or new groups: run `python manage.py migrate` and/or `python manage.py init_groups` once via `docker exec landslidescience-web ...` (migrations also auto-run on container startup via `entrypoint.sh`, so usually only `init_groups` is needed).

Production `.env` lives at `/opt/landslidescience/.env`, mode 600, never committed. DB credentials in there mirror the monitoring stack's `.env`; the `DROPS_R2_*` block (photo drops) was added 2026-10-05 and has no dev/prod difference — same bucket, same token.

### Collaborator data drop (write-only staging)

Collaborators ship built data products to prod without shell access via the
`datadrop` account (rrsync write-only jail into `data/incoming/`; promotion
into live paths is an allowlisted root step; staged data is inert until the
referencing code deploys). **Full workflow: [DATA_DROP.md](DATA_DROP.md)** —
that file is canonical; don't re-document the commands here.

## Local dev

```bash
docker compose up -d                     # uses docker-compose.override.yml automatically
# → http://127.0.0.1:8001/
```

The local container joins the running local Tethys stack's `tethys-timescale-grafana_internal` network (declared external in the override), so the inventory map page works locally if the local Tethys stack is up.

If the Tethys stack isn't running locally, the homepage and admin still work; only `/inventory/*` will fail (no DB to read from).

**Static JS/CSS edits in dev need a collect + restart** — the dev container
serves *collected* static (WhiteNoise caches at startup), so after editing
e.g. `inventory/static/inventory/js/map.js`:

```bash
docker exec landslidescience-web-1 python manage.py collectstatic --noinput
docker restart landslidescience-web-1
```

Templates and Python are read live from the source mount — no restart needed
for template edits (Python needs the dev server's autoreload or a restart).

**`window.__lsMap` is the main map** (map.js, just after construction; nothing
in the code reads it) — for the console or a headless probe: layer order,
visibility, `style.sourceCaches[...]._tiles` to see whether a raster's tiles
loaded. Added 2026-10-10 while chasing a "layer not showing" that turned out
to be a near-white ramp value over the Blank White basemap.

Local dev needs no preview password — that barrier was removed on 2026-09-10, so dev now matches production: `/inventory/*` and `/glaciers/*` are public and signing in at `/inventory/login/` is what adds the editor surfaces.

## Conventions

- **Methods page freshness:** `/inventory/methods/` is the public science
  documentation. Any change to the data model, classification, derived
  rules, subset semantics, or attribution obligations must update
  `methods.html` in the same change — it drifted silently through the
  region/country work until the user asked (2026-07-13); don't repeat that.
- **Lidar viewer methods freshness:** `/lidar/methods/` is to `/lidar/` what
  `methods.html` is to the inventory. Explanations live there and in short
  `title` tooltips on the controls, **not as paragraphs in the sidebar**
  (Hig, 2026-10-11: the 3D and Difference blocks had grown to fill it). A
  new control gets a tooltip of a sentence or two and, if it needs more, a
  paragraph in the matching section there; its anchors are linked from the
  sidebar's `?` buttons, so keep them stable.
- **New-tab policy:** internal navigation (map ↔ manage ↔ methods) is
  same-tab — the map restores its view from localStorage, and new tabs just
  accumulate stale maps. `target="_blank"` is reserved for external
  resources (imagery browsers, Planet, marineregions…) and the mid-draw
  session-expired login link (protects an active drawing session).
- Don't commit `.env` or `data/` (gitignored).
- Hig is the superuser on production. Reset password via: `docker exec -it landslidescience-web python manage.py changepassword Hig`.
- WhiteNoise serves static files in production; runserver serves them in dev.
- Caddy auto-issues Let's Encrypt certs; non-canonical domains 301 to canonical.
- Audit log lives in landslidescience SQLite (`LandslideEditMeta`), not in PostGIS, so the Tethys schema stays clean.
- After editing a landslide record, the inventory caches (`features`, `home_counts`, `timed_events`, `timeline_events`, `slug_map`) are invalidated automatically. No manual cache flush needed. **The cache is a per-process dict and production runs two gunicorn workers**, so `_invalidate` also bumps a stamp file (`data/.inventory_cache_stamp`, `inventory/cache_stamp.py`) and `CacheStampMiddleware` clears any worker whose copy predates it. Before that (fixed 2026-09-26) an edit cleared only the worker that handled it, and a map reload showed the old centroid or the new one depending on which worker answered.
