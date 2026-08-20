# ONBOARDING.md — orientation for a new collaborator (and their Claude)

You are working on **landslidescience.org**: a public Django site hosting the
Alaska landslide inventory map and an experimental glacier-dynamics app. This
file is the guided tour. Three documents matter, in this order:

1. **This file** — how the project is shaped, how to run it, how to verify.
2. **[HAZARDS.md](HAZARDS.md)** — the footguns. Every entry was earned by a
   real incident. Read it before editing; re-read the relevant section before
   touching deploy, the database, or map.js.
3. **[CLAUDE.md](CLAUDE.md)** — the deep reference: per-feature design notes,
   workflows, schemas, and the reasoning behind them. Long, but it is the
   record of *why* things are the way they are. Search it before redesigning
   anything.

## The one rule that outranks everything

**dev → test → (revise → test) → push + deploy.** Never push to GitHub or
deploy to production until the change has been tested in local dev and the
project owner (Hig) has explicitly approved. GitHub is the sync point with
production. Committing locally before testing is fine; pushing is not.
Details in CLAUDE.md §Development workflow.

## What this is, technically

- **Django 5.2**, two databases with a sharp split:
  - **PostGIS** (`tethys_db`, shared Docker network, container name differs
    per environment) holds all *landslide/science data* — accessed with **raw
    psycopg2 SQL, no Django ORM models**. Schema changes ship as idempotent
    management commands (`ADD COLUMN IF NOT EXISTS`), run once per
    environment. The Django migration system never touches PostGIS.
  - **SQLite** (`db.sqlite3`) holds Django-native things: auth, `Page`,
    `HostedFile`, `QmsLayer`, `TraceRaster`, `LandslideEditMeta` (audit log).
    Normal Django migrations apply here (auto-run via `entrypoint.sh`).
- **Frontend: no framework, no build step.** Vanilla JS (ES5-style IIFEs),
  MapLibre GL JS 5.5 from CDN, templates rendered by Django. What you write
  is what ships (after `collectstatic`).
- **Offline tooling** in `tools/`: GDAL tile baking, ITS_LIVE zarr
  processing, analysis scripts. These run on a workstation (the web container
  has **no GDAL**); their outputs land in `data/` and ship by rsync.

## Repo tour

| Path | What lives there |
|---|---|
| `pages/` | Editable site content (homepage, embargo pages). `Page` model, edited in /admin/. |
| `inventory/` | The flagship app: public landslide map, editor management UI, import/export, photos, Planet Stories, trace rasters, snapshots. |
| `inventory/views.py` | Very large; raw-SQL endpoints, edit/review forms, rule cascade hooks. Key constants: `_FILTER_PROPS_SQL`, `_EDIT_FIELD_GROUPS`, `public_landslide_filter()`. |
| `inventory/static/inventory/js/map.js` | The main map. ~6,500 lines, one IIFE. Search for section banner comments. See HAZARDS before editing. |
| `inventory/static/inventory/js/*.js` (shared modules) | **Single sources of truth**: `basemaps.js` (basemap descriptors + tile-URL transforms), `ls_colors.js` (symbology), `ls_overlays.js` (glacier overlay descriptors), `ls_proj.js` (EPSG:3413 math), `ls_hash.js` (URL-hash codec), `ls_export.js` (high-res PNG export). Add/change these things ONLY in their module — never a second copy. |
| `glaciers/` | Sibling map app at `/glaciers/`: Lagrangian tracer visualization of ITS_LIVE glacier velocity. The raw image-pair research stack (`/glaciers/pairs/`) is dev-only, gated on `data/glaciers/experiments/` existing (`experimental_enabled()`). |
| `files/` | Admin-managed public file hosting at `/files/<name>`. |
| `tools/` | Offline data pipeline. `*color*.txt` files are the gdaldem color ramps — also parsed at runtime by `api_ramps` for export legends, so they are load-bearing in two places. |
| `data/` | **Gitignored, volume-mounted, environment-specific.** Tiles, media, snapshots, tracer bundles. Never in the image; ships by rsync. |
| `CLAUDE.md` | Deep reference (auto-loaded by Claude Code). |
| `HAZARDS.md` | Footgun list. |

## Environments

|  | dev | prod |
|---|---|---|
| Where | your machine, `docker compose up -d` | DigitalOcean droplet, `/opt/landslidescience` |
| URL | http://localhost:8001 | https://landslidescience.org |
| Code | bind-mounted (`docker-compose.override.yml`) — Python/templates live-reload; **static JS/CSS needs `collectstatic` + container restart** | **baked into the image** — every code change needs `git pull && docker compose build && up -d --force-recreate`. A restart alone deploys nothing. |
| Container | `landslidescience-web-1` | `landslidescience-web` |
| PostGIS | your local tethys stack's DB | the droplet's DB — **a different database with different data** |
| `data/` | local directory | droplet directory, synced by rsync (excludes `experiments/`, `fit_tiles/`) |

**Port 8000 is NOT this project.** It's the separate Tethys stack, which
serves an older copy of the map — easy to stare at stale code there and
conclude your change didn't work. Dev is **8001**.

Because the two PostGIS databases are separate, **every management command
that touches landslide data runs once per environment** (schema commands,
data migrations, flag scans, `init_groups`…).

## Running and verifying (recipes that actually work here)

```bash
docker compose up -d                      # dev at :8001
# after editing static JS/CSS:
docker exec landslidescience-web-1 python manage.py collectstatic --noinput
docker restart landslidescience-web-1
```

- `/inventory/*` and `/glaciers/*` sit behind a preview password
  (`INVENTORY_PREVIEW_PASSWORD` in the container env); logged-in users
  bypass it. For scripted checks, the Django test client inside the
  container is the cheapest authenticated path:

  ```bash
  docker exec -i landslidescience-web-1 python manage.py shell <<'EOF'
  from django.test import Client
  from django.contrib.auth.models import User
  c = Client(SERVER_NAME='localhost')
  c.force_login(User.objects.filter(is_superuser=True).first())
  print(c.get('/inventory/').status_code)
  EOF
  ```

- **There is no node.** Syntax-check edited JS with macOS's JXA engine:
  `osascript -l JavaScript -e 'try{new Function(<file contents>);"OK"}catch(e){e.message}'`.
  (Don't use naive brace/paren counters — regex literals false-positive.)
- For real-browser verification (map behavior, visibility, exports), drive
  headless Chrome over the DevTools protocol (`--remote-debugging-port` +
  `--remote-allow-origins=*`; a tiny cookie-injecting localhost proxy
  supplies the session for authenticated pages). WebGL needs
  `--enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader` and is
  *slow* — expect map settling to take tens of seconds headless.
- After any template/JS surgery, run the structural checks: every
  `getElementById('x')` id exists in a template; `{% %}` open/close tags
  balance; no multi-line `{# #}` comments (see HAZARDS).
- **Verify what the user sees, not what the DOM contains.** Screenshot and
  look at it. A button can exist, be clickable by script, and still sit
  2,000 px below the fold (this happened).

## How the pieces talk (data flow)

```
tools/*.py (workstation, GDAL/zarr)          editors (browser)
      │  bake tiles / bundles                       │ upload, draw, edit
      ▼                                             ▼
   data/  ──volume-mount──▶  Django views ◀──raw SQL──▶ PostGIS tethys_db
      ▲                        │      ▲                      (landslides,
      └──rsync to prod         │      └── SQLite (auth,       polygons, …)
                               ▼           audit, models)
                        JSON/GeoJSON APIs, tiles
                               │  immutable caching + ?v= tokens
                               ▼
                    map.js + shared JS modules (MapLibre)
```

Public visibility of a landslide = `reviewed_at IS NOT NULL AND
deprecated_at IS NULL`, centralized in `public_landslide_filter()` — every
public surface applies it.

## Recipes for common changes

**Add a column to `landslides`** (the full checklist — several raw-SQL sites):
1. Idempotent management command with `ADD COLUMN IF NOT EXISTS` (copy
   `migrate_superelevation.py`). Run on dev; run on prod at deploy time
   *before* the code that selects it goes live.
2. Edit form: appears automatically (columns are discovered from
   `information_schema`); place it in `_EDIT_FIELD_GROUPS` or it lands in
   the trailing "Other" group.
3. If filterable/displayed on the map: add to `_FILTER_PROPS_SQL` (one
   place, feeds both the points and polygons sources — never hand-mirror
   into one of them).
4. If it should appear in timed/timeline event feeds: those two SELECTs
   unpack by **position** (`r[20]`, `r[21]`…) — extend both the SQL and the
   index-based unpack, in both functions.
5. `api_detail` is free (`row_to_json`). Map UI: filter checkbox in
   `home.html`, wiring in `map.js` (a new flags-bitmask bit is
   **append-only** — see HAZARDS), methods page entry.

**Add a basemap**: `basemaps.js` only (descriptor + any URL transform), plus
a committed thumbnail. **Add a glacier overlay**: `ls_overlays.js` only.
**Change symbology**: `ls_colors.js` only.

**Rebuild tiles/bundles**: rebuild with the `tools/` script, rsync to prod's
`data/`, and **bump the matching `?v=` cache token** (`SUSC_TILE_V`,
`OPERA_TILE_V`, `ITSLIVE_TILE_V`, `HUGONNET_TILE_V`, `TRACER_DATA_V`…) —
tiles are served immutable-cached; stale tokens mean users keep old pixels.

**Deploy** (after approval): see CLAUDE.md §Production. Push → `git pull` on
the droplet → `build` → `up -d --force-recreate` → `manage.py check` → run
any per-environment commands → spot-check the public URLs.

## Style expectations

- Match the surrounding code: comment density is high and comments explain
  *why* (often citing the incident that motivated the code). Keep that up —
  future readers include future Claude instances with no session memory.
- The frontend is deliberately dependency-light. Don't introduce build
  steps, frameworks, or npm without explicit discussion.
- Commit messages here are long-form and narrative: what, why, what was
  measured. Follow suit.
