# CLAUDE.md — landslidescience.org

Public-facing Django site at <https://landslidescience.org>. Companion to the
private Tethys stack at `github.com/hig314/tethys-timescale-grafana`.

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
| `/inventory/manage/export/` | inventory_editors + Hig | Download zip of GeoJSON snapshot |
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

**Not yet supported via UI**: polygon editing, creating new landslide records. Those flow through QGIS/PostGIS for now. Future phase will add click-on-map polygon creation and external polygon imports.

## GeoJSON round-trip

The export/import flow lets you snapshot the current inventory, edit in QGIS or by hand, then re-apply. Format:

```
landslidescience_inventory_YYMMDD.zip
├── manifest.json                 # export format version, timestamp, column lists
├── landslides.geojson            # 1 feature per row (geometry: null), all columns in properties
└── landslide_polygons.geojson    # 1 feature per row, MultiPolygon geometry, properties carry landslide_id/role/area/thickness
```

Column names match PostGIS exactly (snake_case). Geometries use `ST_AsGeoJSON(geom, 15)` — full IEEE 754 precision so round-trip is byte-stable.

**First-shot scope**: import only UPDATEs existing records (matched by id). Records or polygons with new ids are previewed but not inserted; records present in DB but missing from upload are kept silently. INSERT support and upload-driven deletion are deferred.

Schema fingerprint: `python manage.py roundtrip_test` verifies that download → upload-without-changes is a byte-identical no-op against the live DB.

Workflow:
1. Click "⬇ Export" on `/inventory/manage/`. Save the zip.
2. Unzip in QGIS — drag both `.geojson` files in, edit attributes/geometries.
3. Re-zip (must contain both files at the top level).
4. Click "⬆ Import" → upload → preview → confirm.

## Forward-looking integration

The Tethys monitoring stack (sensor dashboards, access-controlled admin) stays in its own repo. Public-facing components migrate to this repo as needed; they read from `tethys_db` directly via psycopg2 (no Django models for landslide data — keeps PostGIS as the single source of truth, owned by the Tethys repo).

Phase 3 of the inventory port is decommissioning the Tethys-side `landslides` app once we've soaked on the new one for a couple of weeks.

## Conventions

- Don't commit `.env` or `data/` (gitignored).
- Hig is the superuser on production. Reset password via: `docker exec -it landslidescience-web python manage.py changepassword Hig`.
- WhiteNoise serves static files in production; runserver serves them in dev.
- Caddy auto-issues Let's Encrypt certs; non-canonical domains 301 to canonical.
- Audit log lives in landslidescience SQLite (`LandslideEditMeta`), not in PostGIS, so the Tethys schema stays clean.
- After editing a landslide record, the inventory caches (`features`, `home_counts`, `timed_events`, `timeline_events`, `slug_map`) are invalidated automatically. No manual cache flush needed.
