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
| `files` | Admin-managed public file hosting. `HostedFile` model in SQLite; bytes stored under `data/media/`; served (unlisted) at `/files/<name>`. See *Hosted files* below. |

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
| `/inventory/manage/<id>/delete/` | **superusers only** (POST) | Permanent hard-delete (Danger zone); distinct from deprecate |
| `/inventory/manage/settings/` | inventory_editors + Hig | Map display settings (colors, point sizes) |
| `/inventory/export/` | public *(behind preview password)* | Download zip of GeoJSON + QGIS .qml styles |
| `/inventory/manage/import/` | inventory_editors + Hig | Upload zip/.geojson; preview diff; confirm to apply |
| `/files/<name>` | public *(unlisted)* | Serves an admin-uploaded `HostedFile` by its URL token (no auth, no preview barrier). |
| `/admin/` | site_admins (Page + HostedFile perms) + Hig | Django admin — Page + HostedFile models + User/Group management |

## Auth & permissions

Two non-superuser groups (created/maintained idempotently by `python manage.py init_groups`):

| Group | What they can do | Where they work |
|---|---|---|
| `inventory_editors` | Edit landslide records via custom UI | `/inventory/manage/` |
| `site_admins` | Edit Page content (homepage, /tracyarm2025/) + manage `HostedFile`s (`/files/`) | `/admin/` |

Adding a user (do this via `/admin/auth/user/`):
1. Create user with a temp password.
2. Set `is_staff=True` (required to log in at /admin/login/, which is the only login page).
3. For inventory editors: add to the `inventory_editors` group. They will see an empty Django admin landing — they navigate to `/inventory/manage/` for their work.
4. For site admins: add to the `site_admins` group. They get full CRUD on Page **and HostedFile** in /admin/.

Hig (superuser) bypasses all role checks.

**Sessions are rolling** — `SESSION_SAVE_EVERY_REQUEST = True` (settings.py) resets the 2-week `SESSION_COOKIE_AGE` clock on every request, so an actively-used editor session doesn't lapse mid-work; an idle one still expires after two weeks (that's expected, not a bug — distinct from the *fleet-wide* logout that only a `DJANGO_SECRET_KEY` change causes). When a session does expire, the manage endpoints 302-redirect to the login page; the in-app draw flow (`_drawPost` in `map.js`) detects that redirect / non-JSON response and shows a clear "log in again" message instead of choking on the login HTML with `Unexpected token '<' … is not valid JSON`. Staged draw components live server-side (`provisional_polygons`), so they survive the re-login.

If the "empty admin landing for editors" friction becomes annoying, wire up `django.contrib.auth.urls` at `/accounts/login/` and update `inventory.auth.inventory_editor_required` to redirect there. For now, deferred.

## Hosted files

The `files` app hosts arbitrary admin-uploaded files at stable, human-readable public URLs — a place to park a KML, PDF, dataset, etc. and hand out a link. Managed entirely through Django admin at `/admin/files/hostedfile/` (superusers + `site_admins`).

- **Model `HostedFile`** (SQLite): `file` (FileField), `name` (the public URL token), plus `title`/`description` (admin-only notes), `inline` (bool), `content_type` (MIME override). On save, blank `name` auto-fills from the uploaded filename.
- **Public URL = `/files/<name>`**, served by `files.views.serve` (a `FileResponse`). The URL token is **decoupled from disk storage**: `name` is the URL, `file.name` is wherever Django's storage wrote the bytes (it may suffix on collision — fine). `name` is unique and regex-constrained to `[A-Za-z0-9._-]` (matches `files/urls.py`), so no path traversal. MIME is `content_type` if set, else guessed — with an `_EXTRA_TYPES` table in `views.py` for geo types Python misses (`.kml`/`.kmz`/`.geojson`/`.gpkg`). `inline` toggles `Content-Disposition: inline` vs `attachment`.
- **Fully public, unlisted.** No auth and no preview-password barrier (that middleware only guards `/inventory/*`). Nothing links to the files, so they're reachable only by someone who knows the URL. `robots.txt` disallows all during pre-release anyway.
- **Storage: `MEDIA_ROOT = data/media/`** (`upload_to='hosted_files/'`). `data/` is volume-mounted in dev and prod and gitignored, so uploads **persist across deploys** and are never committed or baked into the image. There is **no `/media/` static route** — the only way out is the `/files/<name>` view.
- **Permissions** are granted in `init_groups` (HostedFile CRUD → `site_admins`), so **re-run `init_groups` after deploying** if the group needs the perms (as with any group change).

## Pre-launch preview password

While `INVENTORY_PREVIEW_PASSWORD` is set, all `/inventory/*` paths require either authentication OR a session flag set by entering the password at `/inventory/preview/`. **Unset the env var to make `/inventory/*` fully public (post-launch).** `preview_login` short-circuits for an already-authenticated user (or one who already entered the password) — redirecting to `next` instead of showing the form — so a post-login `?next=/inventory/preview/…` redirect chain doesn't strand a logged-in editor on the barrier (and it never bounces `next` back to itself). A logged-in editor who unexpectedly lands here means the request arrived **unauthenticated** (session cookie missing/expired); a fleet-wide logout is almost always a **`DJANGO_SECRET_KEY` change** (settings.py:7 falls back to a constant, so the only way every signed session invalidates at once is the env var changing) — keep it stable in prod `.env`.

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

### Refresh dev with production data

To test flags/display against real, already-curated data, mirror prod's PostGIS landslide tables into dev. Dev's **SQLite** (auth/sessions/`LandslideEditMeta`) stays untouched, so the dev login is unchanged. Containers: prod DB = `monitoring-tethys_db-1`, dev DB = `tethys-timescale-grafana-tethys_db-1` (both `kartoza/postgis:16`; app DB `landslides`, user `tethys`, dev password `tethys_pass`).

```bash
# 1. Dump the 11 app tables from prod, data-only (password read from the web
#    container's env, never printed); pull to /tmp.
ssh root@143.198.140.54 'PW=$(docker exec landslidescience-web printenv TETHYS_DB_PASSWORD); \
  docker exec -e PGPASSWORD="$PW" monitoring-tethys_db-1 pg_dump -h 127.0.0.1 -U tethys -d landslides \
  --data-only --no-owner -t public.landslides -t public.landslide_polygons -t public.landslide_polygons_history \
  -t public.landslide_subsets -t public.landslide_planet_stories -t public.planet_stories -t public.subsets \
  -t public.provisional_polygons -t public.map_settings -t public.snapshots -t public.survey_circles > /tmp/ls.sql'
ssh root@143.198.140.54 'cat /tmp/ls.sql; rm -f /tmp/ls.sql' > /tmp/ls.sql
# 2. TRUNCATE + load into dev (one transaction; rolls back on any error).
{ echo "TRUNCATE TABLE landslides, landslide_polygons, landslide_polygons_history, landslide_subsets, \
  landslide_planet_stories, planet_stories, subsets, provisional_polygons, map_settings, snapshots, \
  survey_circles RESTART IDENTITY CASCADE;"; cat /tmp/ls.sql; } | \
  docker exec -i -e PGPASSWORD=tethys_pass tethys-timescale-grafana-tethys_db-1 \
  psql -h 127.0.0.1 -U tethys -d landslides --single-transaction -v ON_ERROR_STOP=1
rm -f /tmp/ls.sql && docker compose restart web   # restart clears the in-memory feature cache
```

**Use `--data-only`, not `--clean`/schema:** dev has a `landslide_overview` **view** on `landslides`, so a schema dump's `DROP TABLE landslides` fails on the dependency. Data-only sidesteps it (dev already has the matching schema — same app). `psql` needs **TCP + `PGPASSWORD`** (`-h 127.0.0.1`); the default socket uses peer auth and fails. After the load, dev's `LandslideEditMeta` still references the old dev ids, so the Manage "last edited by" column may be stale (cosmetic).

## Editing landslide records (workflow for editors)

1. Log in at `/admin/login/` (yes, even though you're not going to /admin/).
2. Hit `/inventory/manage/`.
3. Search by name or filter by type/class/subset; click into a record.
4. Edit non-geometry fields. Save.
5. The audit log records who and when. The list view shows it.

To bulk-add new landslides, use the upload path — see *Inventory induction workflow* below.

Both the edit and review forms embed a shared imagery-switchable preview map (`_polygon_map.html`) — the landslide's polygons over selectable basemaps (ESRI / Sentinel-2 / **AHAP 1978–86** / topo …), so the editor can flip modern↔historic imagery to spot slow change. Default fit zoom is capped at 15 (~the most real detail AHAP carries). `polygons_geojson` is built in both modes for it (and carries `landslide_class` + `role`). (AHAP loads slowly via the USDA ImageServer exportImage endpoint, and returns blank outside its AK extent — a pre-rendered AHAP tile cache near landslides is a possible future optimization.)

**Basemap handling is shared** between the main map and this preview map via `inventory/static/inventory/js/basemaps.js` (`window.LSBasemaps`): the built-in basemap descriptors (`DEFAULTS`) **and** the single `buildRasterStyle(bm, opts)` + the tile-URL transforms, so the two maps can't drift (they used to keep duplicate copies). Both load the module, pass `transformRequest: LSBasemaps.transformRequest` at construction, and call `registerProtocols()` once. **All tile-URL transforms funnel through one place** (`buildRasterStyle` for live tiles, `thumbnailUrl` for card previews): `{x}/{y}/{z}` native; `scheme:'tms'`/`{-y}` bottom-origin; `{q}/{quadkey}/{switch:…}/{s}/{subdomain}` rewritten per-tile by `transformRequest` (quadkey computed from z/x/y, loaded as a plain image → no CORS dependency); and **`reproject:'epsg3395'`** warped per-tile by the `reproj` `addProtocol` — a 1-D vertical resample (3857 and 3395 share X/x-tile-index; only latitude→Y differs, ~997 px offset at z12/63°N) that fetches the source 3395 tile(s), canvas-warps, returns JPEG. Reproject needs CORS (canvas readback), so the backend only sets `reproject` for EPSG:3395 services with `cors_status==enabled` (e.g. Yandex Satellite); thumbnails use the un-warped source tile. Add a basemap or a transform **only in basemaps.js**. The edit/review preview map also fetches `api/qms/promoted` so admin-shared QMS layers (incl. reprojected Yandex) are flippable while mapping geometry.

**Map symbology is shared** between the main inventory map, the swipe comparison map, and the edit/review preview map via `inventory/static/inventory/js/ls_colors.js` (`window.LSColors`) — the single source of truth. Styling is **attribute-driven, NOT by `landslide_class`** (so small records keep their real meaning instead of collapsing to a "Small …" color):

- **Dot size** ← `size_inclusion` (included = full dot, excluded = half) — `pointRadius(P, scale)`.
- **Slow color** ← `creep_behavior`: obvious = red, subtle = yellow, geomorph = green. **Patchy obvious** = a yellow dot with a small **red center** — a separate `points-patchy` circle layer (a MapLibre circle is only 2-tone, so the 3-tone symbol needs a sublayer), filtered to slow patchy via `LSColors.PATCHY_FILTER`.
- **Catastrophic color** ← age band from `year_num`: ≥2012 `#2b368f` (deep blue), Modern `#5479bd`, Holocene `#aecbe9`. Precursory creep is the **stroke/halo** (obvious/patchy red, subtle yellow, geomorph green).
- **Incomplete = magenta `#d11fa0`**: a slow record with no `creep_behavior`, or a catastrophic with no resolvable age — the very dimensions display rests on. Surfaces data gaps on the map (the map doubles as a completeness check).
- **Draw order** (`pointSortKey`): ≥2012 cat → slow obvious(+patchy) → slow subtle → Modern → Holocene → slow geomorph.

`api_features` carries `size_inclusion` + `creep_behavior`; `api_polygons` carries `creep_behavior` (so a polygon matches its centroid dot); the edit-preview polygons query carries `landslide_type`/`creep_behavior`/`year_num`. **`year_num` is derived to mirror `derived._resolve_event_era`** (priority: `seismic_datetime` → 4-digit `year_text` → `year_text` Modern/Holocene token → class era token → `date_min`) — defined once in `_FILTER_PROPS_SQL` and repeated in the timed/timeline-event queries + the edit-preview query. This matters because SMALL catastrophic records have `landslide_class = "Small catastrophic landslide"` (no era token), so keying age off the class alone left them magenta even when timed. Add or change a color/expression **only in ls_colors.js**. The legend (home.html `_CLASS_COLOR` + `_HALO_COLOR` + the `_cls_dot.html` partial) mirrors these colors — the two size-only "Small …" rows render a little **row of dots** (their members vary in color), patchy shows a red-center swatch, incomplete is magenta.

**Polygon primary convention is enforced on every automatic entry path** via `derived.normalize_primary()` (called by draw commit create+attach, `manage_polygons_save` adds *and* deletes, and `apply_import`): slow → body primary; catastrophic → exactly ONE source primary, deposits never primary; catastrophic with no source → no primary (centroid falls back to the deposit by role order). Keeps an already-flagged candidate, else lowest id — matching the centroid LATERAL's ordering, so compliant records never move. The review form's explicit per-row primary radio remains the manual escape hatch (not normalized). `python manage.py normalize_primary_polygons [--dry-run]` sweeps the backlog (restart web after a live run to drop cached centroids); run once per environment after deploy.

Polygon **roles** (source/body/deposit) ARE editable in the edit/review form (`polygon_role_<id>`), so a mis-typed landslide can be corrected end-to-end — switch `landslide_type` *and* the role (slow↔body, catastrophic↔source/deposit), and the rule cascade re-runs on save (also triggered outside review when type/role changed) to recompute the centroid/areas/class. The edit/review form also offers location-seeded reference links via `_imagery_suggestions` (centroid, or the polygon-union centroid for pending records): ESRI Wayback / Google satellite as paste-into-field suggestions next to `esri_wayback_link`/`google_images_link`, read-only OPERA InSAR ascending/descending links next to `insar_opera`, and a USGS TopoView (historic topo, zoom 13) link. The public map detail popup (`api_detail` → `map.js` imagery list) carries the deterministic OPERA + TopoView links too (`topoview_link`).

**ESRI Wayback link format (maintenance note).** `esri_wayback_link` uses ESRI's current **center+zoom** hash: `…/wayback/#mapCenter=<lon>%2C<lat>%2C<zoom>&mode=explore[&active=<release>]` (`active` = which historical capture is shown). ESRI **retired the old bounding-box hash** (`#ext=<lonW>,<latS>,<lonE>,<latN>`) — the new app silently ignores it, so legacy links opened to the whole world. The conversion (bbox → centroid + equivalent zoom, preserving `active`) lives in `views.py`: `_wayback_url` builds a link, `_convert_wayback_ext_url` converts one (handling `ext` at **any** hash position, e.g. `#active=N&ext=…`, and leaving links that already carry a `mapCenter` untouched) — shared by the auto-seeded suggestions and the migration. Two idempotent, `--dry-run`-capable commands fixed the stored data (run once per environment — the dev and prod databases/snapshots are separate):

- `migrate_wayback_urls` — convert stored `landslides.esri_wayback_link` values.
- `patch_snapshot_wayback_urls` — rewrite the frozen links inside published snapshot bundles (each `api/landslide/<id>/index.json` **and** the downloadable GeoJSON zip's `landslides.geojson` + `landslide_polygons_flat.geojson`) in place, without disturbing the bundle's scientific data or citable manifest.

If ESRI changes the format again, update those two `views.py` helpers and re-run both commands per environment.

**Form ergonomics.** Scalar fields **autosave on blur** (`autosave.js` → `manage_edit_field`), with a per-field status (blue ✓ / red error); the "Save changes" button remains for subset memberships + the polygon role/primary table. Date fields are typeable in the unambiguous **`14-Sep-2010`** form (and ISO). A field that feeds a rule re-runs the cascade and refreshes the derived fields in place. `owner`/`noted_by` auto-fill with the editor's identity (username / full name) on blank records (and on draw-created ones). The polygon primary (centroid-defining) is selectable per-row in the role table. Saving geometry no longer reloads the page — it refreshes the derived fields in place so unsaved field edits survive. On the public map a record missing the dimension its display rests on (slow without `creep_behavior`, catastrophic without a resolvable age) draws **magenta** (see *Map symbology* above) — a visible "incomplete" signal, not a hard error.

## In-app polygon geometry: edit & draw (Terra Draw)

Geometry can be created/edited in-browser via **Terra Draw** (loaded from CDN, editor-only; globals `terraDraw` + `terraDrawMaplibreGlAdapter` — note the casing). Draw with **click vertices → Enter to finish, Esc to cancel**; `pointerDistance` is tightened so clicks near existing vertices don't snap-close.

- **Edit existing** — on the edit/review preview map (`terra_draw.js`), the **✎ Edit geometry** button: reshape (drag/add/delete vertices), add a polygon (role), delete a polygon → **Save** POSTs to `manage_polygons_save`. Round-trip-safe: only changed rows rewritten (server `ST_Equals` skip); pre-edit geom snapshotted to `landslide_polygons_history`; rule cascade re-runs. Terra Draw select mode adds vertex/midpoint **handle Points** to its store — filtered out via `realPolys()` before diffing (else they look like new polygons). Geometry is `MULTIPOLYGON,4326`; drawn Polygons are `ST_Multi`-wrapped. **Coordinates are rounded to 9 dp on the way into Terra Draw** (`loadFeatures` → `round9`): Terra Draw rejects coords with >9 decimal places (`addFeatures` returns `valid:false, "invalid coordinates"`, *silently* — no throw), and stored geometry is served at 15 dp, so without rounding every existing polygon loaded empty (the editor showed nothing to edit). 9 dp ≈ 0.1 mm — far below mapping accuracy.
- **Draw new on the main map** — the **✏ draw** control (`DrawModeControl` in `map.js`): trace a polygon → name + role popup → staged server-side in `provisional_polygons` (per-editor, survives reload). **Same name = same landslide.** A draft teal overlay shows staged polygons (own source, re-added on `style.load`, MeasureControl pattern; `__drawActive` flag suppresses landslide clicks + is mutually exclusive with measure; basemap locks only while a polygon is open). The queue panel groups by name (`manage_draw_preview` flags dispersed/duplicate/collision); **Commit** (`manage_draw_commit`): new names → `apply_import` synthesize-by-name (type inferred from roles); **names that already exist → polygons are *attached* to that landslide** (e.g. a source for a committed deposit), `is_primary` normalized to the role convention. Then cascade + redirect into review. **Attach hard-block:** if the staged name matches an existing landslide whose centroid is **> ~5 km** away (`_PROV_DISPERSED_M`), commit is **refused** (409) with "… exists ~N km away — give this one a distinct name (e.g. 'X 2')" rather than silently merging two different features under one name (this is how record 1446 merged two 100 km-apart "Moose Creek" landslides). The legit nearby-attach case still works.
- **Provisional (pending) records on the map** — editors see them in **magenta** (`api/provisional/`, editor-only, not cached; `pending-*` layers in `map.js`), click → review form. Public never sees pending. The map also restores the last view (localStorage) when you return with no hash, and the edit/review forms have a **↩ Map** link, so you can bounce between mapping and form-filling.

**Migrations (run on prod at deploy, idempotent, like `migrate_deprecated`):** `migrate_polygon_history`, `migrate_provisional_polygons`, `migrate_flag_review` (adds `flagged` + `flag_reason`), `migrate_default_view` (adds `default_map_view`), `migrate_regions` (adds `subsets.kind`/`region_geom`, `landslides.country`, `countries` table; follow with `load_countries` + the `load_region_geometry` runs — see *Subsets* section). (The standalone `/manage/new/` draw page was superseded by the main-map tool and is now unused — safe to retire.)

**Permanent delete (superuser-only).** The edit/review forms carry a "Danger zone" (`_danger_zone.html`) that **hard-deletes** a landslide and everything keyed to it — polygons, polygon edit-history, subset memberships, Planet-Story links, the SQLite `LandslideEditMeta`, and clears any inbound `superseded_by` pointers — in one transaction via `manage_delete` (`POST manage/<id>/delete/`), then invalidates caches and redirects to the list with a banner. Itemizes the impact counts up front, gated behind an "I understand" checkbox **and** a `confirm()`. Distinct from **deprecation** (the soft, provenance-keeping retire); data editors only deprecate — hard delete is restricted to superusers (in-view `is_superuser` check, not just the editor decorator).

## Naming & disambiguation standard

Alaska placenames recur across distinct landslides (five "Moose Creek"s), and many features sit *near* a named feature without being on it. Names are **human-readable base + compact, admin-interpretable disambiguators**, read left→right from most to least significant:

- **Single landslide at a unique placename:** `Toyota Creek` (slow) / `Toyota Creek Holocene` or `Toyota Creek 2004` (catastrophic — the catastrophic event gets a time qualifier).
- **A slow + a catastrophic at ~the same spot** share the base name (`Toyota Creek` + `Toyota Creek Holocene`). If they're in *somewhat different* spots, add a **letter**: `Toyota Creek A`, `Toyota Creek B Holocene`.
- **Multiple of the same type at different spots** on/near the feature: letters — `Toyota Creek A`, `Toyota Creek B`.
- **The same slope failing catastrophically more than once:** distinguish by year/epoch; when more than one event falls in the **same time-bin** (same year, or both Holocene/Modern), append a **`.N`** index — `Toyota Creek B 2016.1`, `Toyota Creek B 2016.2`; `Barabara Creek B Holocene.1`, `… Holocene.2`. (The old trailing-letter form `… Holocene A`/`B` is deprecated — flag scan heuristic (c) catches it for conversion.)
- **A genuinely *different* feature that shares a name** (the Moose Creek case): append a **number** then letters — `Moose Creek 2 A`, `Moose Creek 2 B`. The number distinguishes the feature; letters distinguish slopes on it.
- **Weakly-associated placename** (near, not on, a named feature): a **`~`** distinctor right after the base name (reads as "approximately/near" — it replaced the old `X`, which collided with the slope-letter sequence at the 24th slope, also `X`). `Eureka Glacier ~ 2 C 2016.2` = a landslide on a feature *near* Eureka Glacier (2nd such feature borrowing the name), 3rd failing slope (`C`) on it, 2nd catastrophic failure (`.2`) in 2016. (`~` collapses to `-` in URL slugs like any non-alphanumeric, so it's URL-safe; slug collisions are auto-de-duped via `-<id>`.) **The side of `~` matters:** everything before it is the base feature; a number *after* it counts near-features — `Toyota Creek ~ 2` (2nd area near Toyota Creek) vs `Toyota Creek 2 ~` (near the distinct feature "Toyota Creek 2", case 5).

GNIS-based auto-naming (snap to the nearest placename + auto-suffix) is **future work**; today the standard is applied by hand via the rename workflow below.

### Flag + rename workflow (editor-only)

Tooling to find and fix non-conforming names without page reloads:

- **`flagged` (boolean) + `flag_reason` (text)** — editor-only review metadata (schema: `migrate_flag_review`). Not shown on public surfaces; the map's **Flagged** filter is editor-gated. `flagged` is editable like any field (autosave / inline), so editors clear it when a name is fixed.
- **`flag_name_issues` management command** (re-runnable; `--dry-run`; `--reset` clears the scan's own prior auto-flags first, by reason text, so a re-run is a clean sweep that drops stale flags while leaving hand-set flags alone) — scans active records and sets `flagged`+`flag_reason` (never clobbering a hand-written reason) on names that **(a)** contain `trib`/`neighbor` ("weakly-associated placename — consider the ~ distinctor"), or **(b)** are an un-disambiguated **token-prefix** of another name ("base name of 'Eagle Creek A' — needs disambiguation"). Token-prefix (not fuzzy ratio) so properly-disambiguated siblings ("… A 2014" vs "… 2015") aren't false-flagged; and a sibling whose only extra token is a **time qualifier** (a 4-digit year or `Holocene`/`Modern`, each optionally with a `.N` repeat index — `2024.1`, `Holocene.2`; standard case 2), a **bare number** (`Moose Creek 2`, case 5), or a **`~`** near-feature distinctor (case 6) is intended and **not** flagged. Heuristic **(c)** flags the **deprecated letter-suffix repeat form** — a single letter trailing a time qualifier (`… Holocene A`, `… 2016 B`) — for conversion to `.N`, with the bare first event getting a "first of repeat failures … use the '.N' form" note. Heuristic **(d)** flags every record carrying a **lone `X` token** (the retired distinctor — ambiguous against the 24th slope letter) so an editor can rename it `~` or clear the flag. `_is_time_qualifier` (regex `^(\d{4}|holocene|modern)(\.\d+)?$`) is the shared definition. `--reset` matches reasons by **prefix LIKE** so it also clears flags written by an earlier wording (e.g. the pre-`~` weak reason).
- **Other flag scans** (same pattern: re-runnable, `--dry-run`, `--reset` clears only its own reason-prefix, `COALESCE` never clobbers a hand-set reason, `_invalidate` at the end — and **always run without `--reset`** in normal use so they *add* flags without dropping unresolved ones):
  - **`flag_undated_catastrophic`** — active catastrophic with **no resolvable age** (the `year_num`-NULL / magenta set): no seismic, `year_text` not a 4-digit year nor Modern/Holocene token, no era token in the class, no `date_min`.
  - **`flag_weak_event_timing`** — active catastrophic with **weakly-constrained timing**: a one-sided date bracket (only a max date = a recency guess from the first image showing the event; or only a min date) or a specific 4-digit year with no bracket at all. The `flag_reason` notes the case so an editor can triage (max-only is often legit — confirm; a specific year with no support like "Your Creek D 2010" is a real guess; a field-known year like Great Mageik 1912 is a one-click clear).
  - All flag scans funnel into the **one `flagged` boolean** (distinguished by `flag_reason`), shown under the single editor **Flagged** filter. A record can match more than one scan; the first to run wins the reason (COALESCE).
- **Map labels (pin a field)** — the editor "Needs attention" panel has a **Map labels** dropdown (`#pin-field`); choosing a field draws its value as a label on every landslide (`pin-label` symbol layer on the `landslides` source, font **`Noto Sans Regular`** — served by both glyph servers; re-added by `initDataLayers` on basemap switch; choice persisted in `localStorage['ls_pin_field']`). The label respects the active filter. The pinnable set is **manually-entered text only** (`unique_name`, `owner`, `noted_by`, `year_text`) — `_PIN_LABELS` in map.js + the `#pin-field` `<option>`s in home.html must stay in sync; rule-derived columns (class, creep behavior) and `flagged` are deliberately excluded.
- **Inline edit in the info-box** — when a field is pinned, clicking a landslide shows an inline editor for that field in the detail panel; it POSTs to `manage_edit_field` (the same per-field autosave endpoint) and **live-patches the `landslides` source** (`_patchFeatureProp`) so the label + filter update without a reload.
- **Flag banner + Clear** — a flagged record shows a `⚑ Flagged for review … [Clear flag]` banner in the info-box (editor-only, independent of what's pinned); **Clear flag** POSTs `flagged=false` via `manage_edit_field`, live-patches the source, and the record drops out of the Flagged filter. (Clearing the flag is a review action, not a field-edit — so it's not done by pinning `flagged`.) When the reason is a `base name of '<NAME>'` reference, `<NAME>` renders as a **jump link** (`linkifyFlagReason` → `a.flag-jump`, resolved via `_featureByName` against `_featuresData`) that flies to + opens the referenced landslide — it's often nowhere near the flagged one. Reuses the permalink click handler's hash-nav.
- **The workflow:** run `flag_name_issues` → on the map filter to **Flagged** + pin **name** → spot duplicates / `trib` / `neighbor` from the labels → click a record → rename inline per the standard (`Moose Creek 2 A`…) → **Clear flag**.
- **Rename collision block:** `manage_edit_field` (the per-field autosave endpoint behind both the inline pinned-field editor *and* the edit/review form) refuses a `unique_name` rename that matches another non-deprecated record (normalized case/whitespace, the import/draw `name_key`) with a 409 — the rename-side counterpart to the draw-commit attach hard-block. Without it a rename to an existing name slipped through silently (it's a generic UPDATE). It blocks duplicate *naming*, not a merge — two same-named rows were never actually merged here.
- **Naming page:** the standard above is reader-facing at `/inventory/naming/` (`naming` view + `naming.html`), linked from **Methods** §1. 

**Right-click → copy coords:** right-click (Ctrl-click on Mac) anywhere on the main map *or* the edit preview map copies `lat, lon` to the clipboard (for pasting into Planet etc.) with a brief toast. Suppressed while a draw/measure/Terra-Draw session is active (there right-click deletes a vertex; `__drawActive`/`__measureActive`/`__lsTdActive` flags).

## Trace rasters — editor-uploaded GeoTIFF overlays

Editors upload a georeferenced image from the map (Reference maps tab → **Traced imagery (uploads)**), it renders as an overlay under the landslide layers, and they trace geometry over it with the existing ✏ draw tool — no GIS round-trip. The upload row doubles as the provenance record (image, capture date = date-bracketing evidence, source note, uploader, optional link to the landslide it produced — set via "⚲ link" while that landslide's info panel is open). Plan: `~/.claude/plans/geotiff-trace-overlays.md`.

- **Pipeline** (`inventory/raster_tiles.py`): validate (must carry CRS + real geotransform; instant 400 otherwise) → `WarpedVRT` to EPSG:3857 snapped to the max-zoom tile grid covering the **min-zoom tile range** (tile grids nest, so every tile at every zoom is an exact pixel-aligned window — WarpedVRT forbids boundless reads, and a partial window + `out_shape` silently stretches pixels) → per-band 2–98 % percentile stretch for non-uint8 (uint16 Planet/Landsat renders black without it) → 256 px RGBA PNGs via GDAL's PNG driver (alpha from the warp mask / `add_alpha`; fully-transparent tiles skipped; `.aux.xml` sidecars removed). Zoom range from native GSD +1 headroom, clamped by a ~6000-tile budget. Storage: originals `data/media/trace_rasters/originals/` (kept verbatim → always re-bakeable), tiles `data/trace_tiles/<id>/` — both volume-mounted, gitignored, survive deploys.
- **Bakes run in a background thread** (`trace_views._spawn_bake`) because gunicorn runs 2 workers with the default 30 s timeout; the row always ends in a terminal `status` (`ready`/`error` + message) and the map polls `status/` every 2.5 s. A container restart mid-bake leaves `processing` — rows older than 30 min surface as **stalled** with a ⟳ re-bake button; `python manage.py rebuild_trace_rasters --stalled|--id N|--all` is the CLI equivalent (synchronous, idempotent).
- **Model `TraceRaster`** (SQLite, normal Django migration `0003` — auto-applies via entrypoint; **no PostGIS change anywhere**). `landslide_id` is an FK-in-spirit like `LandslideEditMeta`.
- **Editor-only end to end**: all `api/trace_rasters/*` endpoints are `inventory_editor_required`; tiles (`/inventory/tiles/trace/<id>/z/x/y.png`, all-integer params — no traversal) check `is_inventory_editor` and return 403 (not a login redirect — they're image fetches), with `Cache-Control: private, immutable`. The map.js registry fetch is gated on `_isInventoryEditor`, so public maps and snapshots never reference any of it.
- **Layer stack position**: the very **bottom of the data stack** — below pending magenta too (`beforeId` candidate chain in `_traceAddLayer`; pending is the lowest data layer, and a freshly-traced-then-committed record must draw on top of the image it was traced from), then susc/faults/landslide layers above (`_traceReplayLayers()` in `initDataLayers`, re-added on basemap switch). **Deliberately NOT mirrored to the swipe pane** — an intentional exception to the pane-parity rule: the overlay is *imagery*, so keeping it main-pane-only makes the wiper a before/after comparator (uploaded historical image left, any basemap right) while landslide data stays continuous across the divider. Each row has an opacity slider (dim against the basemap while tracing), ⌖ zoom-to, and × delete (removes tiles + original + row).
- **UI scaling**: the uploads list is **collapsed by default** behind a summary line ("N uploads · M on map · processing…"); the ＋ Upload control stays visible regardless. Uploading auto-opens the list to show bake progress. A landslide whose info panel is open shows its **linked trace imagery as "Traced from" cards** (tile-derived thumbnail via `_traceThumbUrl` + title + image date); clicking a card enables the overlay and zooms to it — so returning to a record resurfaces its source imagery without hunting the list.
- **rasterio dependency** (+numpy; requirements.txt) is pyogrio's raster twin — GDAL in the wheel — and is imported **only inside `raster_tiles.py` functions**, never at module import: the site runs fine without it, and a broken wheel breaks only new uploads. The Dockerfile installs `libexpat1` (runtime lib rasterio's bundled GDAL links against; python:slim lacks it).
- **The tracing itself is the existing draw flow** — nothing changed there: trace → name+role → staged → Commit (same-name attach, 5 km hard-block, etc.) → review.

## Subsets: tags vs spatial regions, and the country attribute

Subsets come in two kinds (`subsets.kind`, schema via `migrate_regions`):

- **`tag`** — hand-curated, e.g. `alaska-2025` (publication subset, frozen by
  its snapshot — untouched by everything below).
- **`region`** — defined by a polygon (`subsets.region_geom`, MultiPolygon
  4326). Membership = the record's stored **primary-polygon centroid** falls
  inside (ST_Covers), computed by `inventory/regions.py` and **materialized
  in `landslide_subsets`** — so every read path (map `?subset=` filter,
  counts, exports, snapshots, facets) is unchanged; regions.py is the only
  writer. Refreshes: per-record at the end of `apply_rules_for_landslide`
  (regions are derived state and ride the cascade choke point); per-subset
  via `load_region_geometry` (loads/replaces a polygon from any
  pyogrio-readable file, prints the full membership diff — **run --dry-run
  first and keep the output; that diff is the change record**); globally via
  `refresh_region_subsets [--dry-run]`. Guards everywhere: frozen
  (snapshotted) subsets are never touched, deprecated records keep their
  memberships, NULL-centroid records belong to no region, and each polygon
  PART must span <180° longitude (antimeridian split required — the whole
  MultiPolygon may span the dateline via split parts, like TIGER Alaska).
  UI: region memberships render as read-only "computed from location" chips
  on the edit/review forms (excluded from the checkbox sync alongside frozen
  subsets); the import picker excludes regions.

Current regions (converted 2026-07-12; per-record diff record in
`/Volumes/Nunatak/Landslides/AK_systematic_surveys/region_polygons/`):
`alaska` = TIGER/Line 2024 state boundary (extends to the 3-nmi
territorial-sea limit — smooth offshore edge, exact land border, all inside
waters interior; already dateline-split), `kim` = Kim_all study area
(deliberately crosses into Canada), `schaefer`, `higman` = study-area
polygons from the inventory paper. Seed files live in `data/regions_seed/`
(gitignored — copy to the droplet before running the loads on prod).

**`landslides.country`** is a derived rule (`compute_country`,
`/inventory/rules/country/`): centroid tested against the `countries`
reference table — Marine Regions **"EEZ + land union"** polygons
(`load_countries`; source layer `MarineRegions:eez_land` via WFS **1.0.0**
— the 2.0 export is lat/lon axis-swapped; **CC-BY, attribute Flanders
Marine Institute / marineregions.org** wherever country values are
published). Land + waters to 200 nmi per sovereign, so fjord/nearshore
centroids resolve by pure containment; `sovereign1` is used (country level,
not territory). The table is created empty by `migrate_regions` so the rule
is always valid; country is NULL until `load_countries` runs. This is the
universal floor beneath the region layer: a site outside every region still
carries its nationality.

## Inventory induction workflow

New landslides enter the inventory via upload at `/inventory/manage/import/`. The flow:

1. Editor uploads a GeoJSON zip, `.geojson`, `.gpkg`, `.shp` (+ sidecars), or `.kml`. Multi-format ingestion goes through `pyogrio` + `shapely` → normalized GeoJSON FeatureCollections.
2. Upload preview shows a diff (would-add / would-update / would-skip) and surfaces normalization warnings.
3. **Common-fields form** at Apply: blanket values for fields the user wants to set on all new records (e.g. `noted_by`, `landslide_type`). `owner` is auto-populated from the logged-in user (data-admin only). Subset choices exclude locked subsets (e.g. `alaska-2025`). `unique_name` is excluded from blanket population (must be unique per record).
4. On Apply, new landslides are inserted with `reviewed_at` NULL — i.e., **pending**.
5. The user is redirected to `/inventory/manage/review/<first_pending_id>/` — a review form with a mini-map (basemap selector only, no measure/circles, polygons embedded server-side). The form excludes rule-populated columns (those should be computed at save time; see below). On save, `reviewed_at` is stamped and the view redirects to the next pending record.
6. Pending records survive logout / browser close. They surface again next time the editor visits review.

Upload-side normalization (in `io_geojson.py`):
- `_normalize_controlled_vocab` / `_norm_against` — generous matching of `role` (source/body/deposit) and `landslide_type` (slow/catastrophic): case-insensitive + trim, punctuation-stripped, depluralized, `_VOCAB_ALIASES` (e.g. `bodies`→body), and a conservative fuzzy typo-correction (difflib ratio ≥ 0.8, unambiguous only — so `sorce`→source, `depsit`→deposit, but `flow`/`head-scarp`/`src` are left for validation). Every correction emits a warning shown in the preview.
- `_synthesize_landslides_from_flat_polygons` — when a flat-polygons-only file is uploaded, polygons grouped by `unique_name` are inferred into a synthesized landslide record (landslide_type inferred from the polygon roles).
- `LANDSLIDES_AUTO_COLS = ('created_at', 'updated_at', 'reviewed_at')` — these are server-managed and ignored in uploads.

## Induction safety & collision detection

A landslide is **publicly visible only when `reviewed_at IS NOT NULL AND deprecated_at IS NULL`** — i.e. inducted (reviewed) and not superseded. The predicate is centralized in `views.public_landslide_filter(alias)` and applied to every public surface (home counts, features/polygons/detail APIs, chart data, slug map; the snapshot inherits it through the API client). Two hidden states:
- **Pending** (`reviewed_at IS NULL`): freshly uploaded, not yet reviewed.
- **Deprecated** (`deprecated_at IS NOT NULL`, `superseded_by` → new id): retained for provenance but retired by a merge (see below). Schema via `migrate_deprecated.py`.

On review-save, `derived.apply_rules_for_landslide(cur, ls_id)` runs the full rule cascade for that record (centroids, areas, volumes, class — identical to the batch rule-apply) **then** stamps `reviewed_at`, in one transaction (cascade failure → stays pending). `/inventory/manage/` has a `?status=pending|active|deprecated|all` filter (default hides deprecated) with status badges.

**Centroid change-detection is distance-based.** `derived._equal(old, new, column)` decides whether a rule's recomputed value differs from the stored one. The four centroid columns (`centroid_albers_x/y`, `centroid_lat/lon`) are *positions*, so equality is judged as a true ground **distance in meters** (lat/lon degrees scaled by `_M_PER_DEG_LAT`, a conservative equator scale for longitude; Albers X/Y are already meters), thresholded at `_CENTROID_TOLERANCE_M` (0.5 m). The generic 1.0 tolerance — meant for areas in m² — previously applied to lat/lon **degrees**, so a centroid wrong by up to ~1° (~100 km) was treated as "equal" and never corrected, and lat/lon could sit inconsistent with the Albers columns (the mixed-axis centroid that corrupted merged-record 1446). Per-column metric comparison means no axis is masked, so all four columns correct toward the same computed centroid and stay consistent. Pre-existing drifted centroids (stored ≠ recomputed by >0.5 m) now surface in the rules-admin diff and self-correct on the next rule-apply/review-save.

**Collision detection** (`io_geojson._detect_collisions`, surfaced in the import preview, **report-only** for now): each would-add landslide is flagged if it collides with an existing non-deprecated record:
- **Name** — `name_key()` (NFC + whitespace-collapsed + casefolded) matches an existing name; classified `exact` vs `case` (case/whitespace-only diff). Names are compared, never auto-rewritten.
- **Location** — a candidate polygon within `COLLISION_NEAR_M` (200 m, geography `ST_DWithin`) AND polygon-pair IoU > `COLLISION_IOU` (0.80, EPSG:3338).

Each collision gets a `resolution` (one source of truth for preview + apply): **`update`** — polygons identical (IoU ≥ `COLLISION_IDENTICAL_IOU` = 0.999): the same landslide re-applied (master-file workflow) → `apply_import` UPDATEs the existing record in place, keeping its id/history (matched-updated count on the done page); **`block`** — name-exact dup with non-identical geometry: would violate the `landslides_unique_name_key` UNIQUE constraint, so apply refuses up front with an actionable message (never the raw 500 it used to throw — and any other DB error during apply is also caught → clean message, full rollback); **`review`** — case/whitespace name dup or a near (non-identical) overlap: inserts as new, surfaced for the editor. The preview's collision block shows the per-row action.

**Still to build (Phase 2–3, task #61):** the supersede/merge UI for `block`/`review` collisions that aren't simple identical re-imports — keep the improved upload, deprecate the original (`deprecated_at`/`superseded_by`), carry valuable linked data (Planet stories, subsets, null-only fields) forward via an explicit picker; plus a `[Place][Letter][year]` name suggester for distinct-location name clashes. Plan: `~/.claude/plans/temporal-toasting-jellyfish.md`.

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

**Runtime sync + auto-archive (`inventory/planet.py`).** Every path that
writes `planet_story_link` keeps the N:M tables in step and archives new
animations automatically — no manual command needed for the common case:
`planet.sync_story_link` (autosave `manage_edit_field` — the main path, which
previously updated only the text column, so review-form links never embedded
— and the `manage_edit` full-form save, whose old inline block it replaced)
plus `planet.ensure_story_rows` (additive, `apply_import` for file uploads,
filtered so a master-file re-import doesn't re-probe known stories). A
newly-linked slug is then probed + archived by `planet.ensure_archived_async`
in a background thread (downloads can exceed gunicorn's 30 s window):
HEAD 200 → timelapse → download to `data/planet_stories/<slug>.mp4` + stamp;
404 → comparison; network hiccup → left NULL for the batch commands to retry
(same disk layout + COALESCE stamping semantics, so hook and batch are
idempotent with each other). Slugs are charset-validated (`[A-Za-z0-9_-]+`,
matching the serving route) before becoming GCS URLs/filenames. Once
archived, `api_detail` returns `mp4_url` and the map detail panel embeds the
player on next open.

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
- **Reference maps tab**: categorized basemap cards (Imagery / Topo / Historical / Other) with thumbnails, Windy.com-style. "Reference layers" section at the bottom for toggleable overlays (currently: Survey circles, and the two USGS susceptibility models lw / n10). A **Compare (swipe)** pull-down (defaults to "none") adds a second, view-synced basemap on the right half of the map behind a draggable vertical divider — `#swipe-map` is a second `maplibregl.Map` (pointer-events:none, clipped via `clip-path`) carrying the **same data stack in the same draw order** as the main map, so the display reads seamlessly across the divider. `_swipeAddData` builds it from **shared def builders** (`_landslideLayerDefs`, `_suscSourceDef`/`_suscLayerDef`, `_faultsLayerDef`, `_polygonHoverDef`, `_surveyCircleLayerDefs`, `_pendingLayerDefs` — one def = identical paint on both panes; also consumed by `initDataLayers`/`ensurePendingLayers`), in the main map's bottom→top order: pending magenta → susceptibility rasters → faults → points → patchy → polygon fill/outline → hover → survey circles → pin-label. Every toggle/data/hover site mirrors via `_swipeAlso` (susc + faults + survey-circle visibility & data, `polygon-hover` filter via `_setPolygonHover`, `_patchFeatureProp` live edits, `ensurePinLabel`), and the active filter applies to both sides (`_applyLandslideFilter` registry). Only interaction-tool layers (measure, draw draft) are deliberately not mirrored. `_swipeAddData` bails until `initLayers` sets the palette (a hash-restored wiper can create the swipe map first); `initLayers` re-invokes it. Available to everyone; works with any basemap (reprojected Yandex / QMS / AHAP). Toggle classes off for a clean image-only compare.

**Wiper in the URL + per-landslide default views.** The URL hash carries the wiper (`…&swipe=<basemapId>&sx=<divider %>`) alongside the existing `map=zoom/lat/lon&base=…&id=…`, written on swipe enable/disable/divider-drag and restored on load (also via the `ls_map_view` localStorage view, and re-tried after `api/qms/promoted` merges in case the wiper's basemap is a shared QMS layer). That same hash string (no leading `#`) is the format of **`landslides.default_map_view`** (schema: `migrate_default_view`, idempotent, run per environment) — a curated per-landslide view. Editors set/update/clear it from the map detail panel ("Set default view" saves the *current* center/zoom/basemap/wiper via the `manage_edit_field` autosave endpoint, which validates the string's charset/shape because `slug_redirect` emits it into a Location header); anyone sees a "⌖ Default view" button when one is stored (`applyViewString` in map.js — sets basemap, enables/disables the wiper, flies to the view). Consumed by: slug deep-links (`slug_redirect` prefers it over the centroid+zoom-13 fallback), in-app permalink clicks (`data-view` attr), and snapshot slug stubs (`build_snapshot`). Caveat: a default view referencing a basemap other visitors don't have (e.g. an editor's *local* QMS layer) degrades gracefully — unknown `base=` is ignored, unknown `swipe=` renders no wiper — so curate default views on built-in or *shared* layers.
- **Analysis tab**: triggers for the seasonal histogram, time-series chart, and the lw × n10 susceptibility scatter — each opens as a floating, draggable, resizable panel (see *Floating analysis panels* below). The histogram + time-series respect the Limit toggle.

(The n10/lw susceptibility range sliders live in the **Inventory** filter panel alongside the source-area / deposit-area / volume / age dual-range filters.)

**Filter parity — points & polygons share one decision path.** `buildFilter()` (map.js) builds a single MapLibre expression `f` and applies it — via the **`_landslideFilterLayers` registry + `_applyLandslideFilter` helper** (one code path used by both `buildFilter` branches *and* the swipe-map mirror, so no hand-maintained id list to forget) — to `points`, `polygon-fill`, `polygon-outline`, `pin-label`, and the `points-patchy` compound-symbol sublayer. A registry entry may carry a `base` filter (points-patchy's `PATCHY_FILTER`) that's **AND-combined** with `f`, so the sublayer tracks the user filter instead of drifting (the bug where the patchy red centers ignored the Flagged filter). Add a future sublayer to the registry with its `base` and it participates automatically. For the filter to behave identically across sources, every property it reads (`flagged`, `year_num`, `area_src/dep`, the boolean flags, …) must exist on **both** the `landslides` source (`api_features`) and the `polygons` source (`api_polygons`). Those properties come from **one SQL fragment, `_FILTER_PROPS_SQL` in views.py**, spliced into both queries — the single source of truth. Add a filterable landslide-level field there once; never hand-mirror it into one query, or that filter silently drops every feature on the other source when active (the bug where checking "Flagged" hid all polygons was `flagged` present on points but missing from the hand-maintained polygon copy). Display-only props (labels/info-box: `unique_name`, `flag_reason`, `owner`, …) stay inline per-endpoint. `n10`/`lw` are the exception — merged client-side onto both sources by `landslide_id`.

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
