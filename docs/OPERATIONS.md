# Operations

Refreshing dev from prod, robots/indexing, the Umami analytics stack, the Tethys relationship.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

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

## Indexing & robots.txt

Public and indexable since 2026-09-10. Two things had to change together, and
this is the trap worth remembering: **`robots.txt` allowing a path is not
enough** — a crawler that is permitted to fetch a page still obeys that page's
own `<meta name="robots">`. The pre-release setup had both, so relaxing only
one would have left the site invisible with no obvious symptom.

- `landslidescience/urls.py` → `_ROBOTS` is the policy, with a comment giving
  the reason for every `Disallow` (auth-gated / machine-only / expensive /
  unlisted / provisional / duplicate).
- The base templates now carry an empty `{% block robots %}`; pages that must
  stay out of search **override it** — `/glaciers/` (still experimental), the
  preview gate, and the sign-in page do.

Deliberately still closed: `/inventory/api/` and `/inventory/export/` (a ~1 MB
payload and a 4 MB zip per fetch), `/tiles/` + `/lidar/` (tile pyramids are
effectively unbounded URL space), `/files/` (unlisted by design),
and `/glaciers/` (experimental).

`/inventory/archive/` is deliberately **open** (2026-09-10): the snapshots are
the citable record behind published work, and being findable is most of their
point. Accepted cost — dated copies of the same records compete with the live
map in search results.

## Traffic analytics — self-hosted Umami

Who visits, what they read, and which of the site's tools actually get used.
Umami v3 (MIT, cookieless, no consent banner needed), self-hosted alongside
the app. Code: `landslidescience/analytics.py` (the forwarder),
`landslidescience/templates/_analytics.html` (the tag + `LSTrack` shim),
`umami` service in the compose files.

| Piece | Where |
|---|---|
| Umami container | `umami` service; image pinned `ghcr.io/umami-software/umami:3.3.1` (~170 MB resident) |
| Its database | `umami` DB **in the existing `tethys_db`**, own `umami` role — a second Postgres is not worth 200 MB on a 4 GB box |
| Beacon | `/s/t.js` + `/s/api/send` on landslidescience.org, forwarded by Django |
| Dashboard | `https://stats.landslidescience.org`, entered **only** through `/traffic/` (Django-authenticated; Umami's own `/login` 403s) |
| Secrets | `.env`: `UMAMI_DB_PASSWORD`, `UMAMI_APP_SECRET`, `UMAMI_WEBSITE_ID`, `UMAMI_TEAM_WEBSITE_ID`, `UMAMI_PUBLIC_URL`, `UMAMI_BRIDGE_USER`, `UMAMI_BRIDGE_PASSWORD` |

**One login, not two.** Umami's own login form is switched **off**
(`DISABLE_LOGIN=1` makes `/login` return 403), so there is no second password
to hand out, rotate or leak, and nobody can reach the dashboard by finding its
URL. The only door is `/traffic/` (`analytics.dashboard`): it authorises the
caller against *Django's* auth (superuser or `site_admins`), mints a token
server-side via `POST /api/auth/login`, and redirects to Umami's built-in
`/sso?token=…&url=/websites/<id>`, which stores the token client-side and lands
on the dashboard. `DISABLE_LOGIN` blocks only the `/login` **page** — the login
API still works, which is what makes this possible.

The token is minted for **`UMAMI_BRIDGE_USER`**, a Umami account holding
`team-view-only` on the team that owns the website — so the token that reaches
a browser can read the analytics and nothing else (verified: reads 200, create
and delete 401). The full-privilege `admin` account still exists for
maintenance and is never used by a browser. Note that a website can only be
given a `teamId` **at creation**; an existing personally-owned website cannot
be moved into a team through the API, so create it team-owned from the start.

Accepted trade-off: `/sso` carries the token as a query parameter, so it lands
in that browser's history. That is Umami's own hand-off design, and the
read-only scope is what keeps the consequence small. The dashboard UI still
renders admin affordances (an "Edit" button) to a view-only user — they fail
server-side, which is where it matters.

Two Umami settings are **not** runtime-configurable, both checked the hard way:
`BASE_PATH` is baked in by Next.js at *their* build time (setting it on the
container gives 404s), so Umami cannot be mounted under a subpath without
rebuilding the image — hence a subdomain rather than `landslidescience.org/stats/`.
And the `umami.auth` cookie is not accepted server-side; only
`Authorization: Bearer` is, which is why the SSO hand-off (not a cookie
injection) is the mechanism.

**The beacon is first-party on purpose.** Filter lists match `umami.js` as a
filename, `umami.` as a hostname label, and `/api/send` under a recognisable
host — and this site's audience runs blockers, so the measurements lost would
not be a random sample. Nothing on the page names Umami: the browser loads
`/s/t.js` and posts to `/s/api/send`, both on landslidescience.org. No
`data-host-url` is needed because the tracker derives its endpoint from its
own script URL (`dirname(script.src) + '/api/send'`) — **serving the script
from `/s/` is what points the beacon at `/s/`**, so if you move one you move
both.

Two things must survive the forward or the data is *wrong* rather than merely
missing: the **client IP** (`X-Forwarded-For`, first entry, matched by
`CLIENT_IP_HEADER=x-forwarded-for` on the container — it is what Umami hashes
into the session id and resolves to a country) and the **`x-umami-*` headers**
(v3 carries the website id and hostname there, and the tracker reads
`{cache, disabled}` off the response; break either direction and every visit
fragments into one-hit sessions). Cookies are deliberately not forwarded.
Any upstream failure returns a quiet 204 — analytics must never break a page.

**`data-exclude-search="true"` sits beside it (2026-10-06).** The map keeps its filters in the query string and rewrites it with `replaceState` on every change; the tracker hooks `replaceState`, so until this was set each filter click counted as a pageview and the filter settings went to the dashboard.

**`data-exclude-hash="true"` is load-bearing.** The map and the explorer keep
their entire state in the URL fragment, so without it every pan, filter and
record click would mint a brand-new "page" and the pageview table would be
useless. Pageviews stay clean paths; what happens *inside* a page is recorded
as a named custom event instead.

**Custom events** — all emitted through `window.LSTrack.event(name, data)`,
which no-ops when analytics is off, blocked, or DNT is set, so call sites need
no guards of their own:

| Event | Where | Carries |
|---|---|---|
| `landslide_open` | `renderDetail` (map.js) | id, name, type, class |
| `basemap` / `overlay` / `map_tool` | `setBasemap`, overlay checkbox, wiper / measure / InSAR | which one |
| `explore_filter` / `explore_group` / `explore_chart` / `explore_preset` | explore.js | **column names only, never the values typed** |
| `download` | zip link, explorer CSV/TSV, map PNG | kind, row count |
| `explore_to_map` | "Show on map" | selection size |

**Privacy stance**: cookieless, DNT honoured client-side via
`data-do-not-track`, IP used only for the session hash and country then
discarded (city/region need a MaxMind DB we do not ship, so they stay empty),
and no filter *values* are ever recorded — only which column was used.

**Deploying it** (the analytics stack is NOT part of a plain code deploy):
1. In prod `.env`: `UMAMI_DB_PASSWORD`, `UMAMI_APP_SECRET` (`openssl rand -hex 32`), later `UMAMI_WEBSITE_ID`, `UMAMI_PUBLIC_URL`.
2. Create the role + database in `tethys_db`: `CREATE ROLE umami LOGIN PASSWORD '…'; CREATE DATABASE umami OWNER umami;`
3. `docker compose … up -d umami` — it runs its own Prisma migrations on start; watch for "Database is up to date."
4. **Change the default `admin` / `umami` password immediately** — `POST /api/me/password` with `{currentPassword, newPassword}` (the endpoint is `/api/me/password`, not `/api/users/<id>/password`).
5. Create a team (`POST /api/teams`, returns a **list**), then create **both** websites **with that `teamId`** (`POST /api/websites`) — a website cannot be moved into a team later, and the bridge user only sees team-owned sites. Public → `UMAMI_WEBSITE_ID`, collaborators → `UMAMI_TEAM_WEBSITE_ID`.
6. Create the bridge user (`POST /api/users` with `role: "view-only"`) and add it to the team (`POST /api/teams/<id>/users` with `role: "team-view-only"`). Put its credentials in `UMAMI_BRIDGE_USER` / `UMAMI_BRIDGE_PASSWORD`.
7. **`docker compose up -d web`, not `restart`** — `restart` reuses the old environment and will silently serve stale `UMAMI_*` values.
8. Caddy site block for the dashboard subdomain + the DNS A record — **done for the current prod, see below**; only needed again for a new environment.

**The dashboard is live at `https://stats.landslidescience.org`** (2026-09-10).
DNS is a Cloudflare **A record, DNS-only / grey cloud** → the droplet, matching
`monitoring.` — an orange-cloud proxy would put Cloudflare's TLS in front of
Caddy's and can break the ACME HTTP-01 challenge. Caddy issued the Let's
Encrypt certificate on the first attempt.

The site block lives in **`/opt/monitoring/Caddyfile`**, which belongs to the
`tethys-timescale-grafana` stack (see [[project_window_split]] in memory) and
is **untracked on purpose** — it is production-only config. Backup of the
pre-change file: `Caddyfile.bak.20260910-194230` (54 lines). What was appended:

```
stats.landslidescience.org {
    encode gzip
    reverse_proxy umami:3000
}
```

`Caddyfile.template` in that repo is **not** a generator for the live file —
it is a stale 27-line starter example (`http://YOUR.SERVER.IP`) that says so
itself, so a hand-added block is not at risk of being regenerated away. The
only real hazard is two people editing the file at once.

Nothing on the public site depends on this hostname: **collection works
without any Caddy change**, because the beacon rides the existing
landslidescience.org route. Only *reading* the dashboards needs it, and
`UMAMI_PUBLIC_URL` is what points `/traffic/` at it.

**Two audiences, two site records** (`analytics.site_for`). Signed-in traffic
reports to `UMAMI_TEAM_WEBSITE_ID`, everyone else to `UMAMI_WEBSITE_ID`, so
the public dashboard *cannot* contain a collaborator session — switch between
them in Umami's site picker. Separate sites rather than one site with tagged
sessions, because a tag still lands in the default Overview and would have to
be filtered out by hand every time, which is the obscuring this exists to
prevent.

Resolution order: the opt-out cookie wins over everything; then anyone signed
in goes to the collaborator site (there is no public sign-up, so every account
is an editor, a viewer, or Hig — **including Hig, asked for 2026-09-10**,
since the operator's own use of the editing tools is part of what that
dashboard is for); everyone else goes to the public site. `site_for` returning
`''` emits **no tracker tag at all**, so an excluded visit makes no analytics
request rather than one filtered later.

The only exclusion is the **per-browser opt-out cookie** (`ls_no_track`, two
years) at **`/traffic/optout/`**. That page is deliberately public: an opt-out
only the owner can reach is a worse thing to have built, and it doubles as the
site's do-not-measure control for visitors. Umami's own `IGNORE_IP` is not
used — it needs a stable address, which a field-based Alaska connection is not.

> **Read the collaborator dashboard as named people, not as an aggregate.**
> There are 3 accounts (2026-09), so region plus time-of-day identifies who is
> who. That is a reasonable thing to have for a tool built *for* those people,
> but it is not anonymous, and they should be told it exists.

## Forward-looking integration

The Tethys monitoring stack (sensor dashboards, access-controlled admin) stays in its own repo. Public-facing components migrate to this repo as needed; they read from `tethys_db` directly via psycopg2 (no Django models for landslide data — keeps PostGIS as the single source of truth, owned by the Tethys repo).

The Tethys-side `landslides` app was decommissioned 2026-05-20. Source code was moved to `archived_apps/tethysapp-landslides/` in the Tethys repo and removed from `Dockerfile.tethys`, so `/apps/landslides/` 404s. The `landslides` table in `tethys_db` is unchanged — still owned by the Tethys repo, read directly by this app over the shared Docker network.

## Outside-service probe (2026-11-14)

The map reads a dozen servers nobody here runs. `tools/ops/probe_services.py`
(standard library only) asks each for one representative thing the way the
site does and checks the answer is the kind expected — several fail with
status 200 and an HTML error page, NOAA's chart server among them, so status
alone proves nothing. With `--out data/service_status.json` it keeps the
latest result per service, when each last changed state and the last 200
changes; **`/traffic/services/`** (site admins) renders that file and makes no
outside request itself. Cron wrapper `tools/ops/probe_cron.sh` (every 30
min; logs only failures). One row in `PROBES` per service; when a feature
starts reading a new outside server, add its row in the same change. NOAA
gets three tries because it fails about 1 request in 20 on its own.

## Backups to R2 (2026-11-14)

`manage.py site_backup [--dry-run] [--only …]` (in the `drops` app, whose R2
client and private bucket it uses; keys under `backups/`) copies what exists
only on the droplet: a consistent gzipped snapshot of `data/db.sqlite3` per
day, `data/media/` (field photos, hosted files, trace-raster originals),
pre-baked trace `.pmtiles`, `data/snapshots/`, `data/planet_stories/`, and
whatever the cron wrapper staged in `data/backup_staging/` — a `pg_dump` of
the PostGIS `landslides` database, which the web container cannot make
itself. File trees are incremental and never deleted from the bucket; dated
dumps are thinned to 14 dailies plus each month's first. Its docstring is the
full account, including restore. `tools/ops/backup_cron.sh` is the nightly
wrapper. **Dev and prod share the bucket**, so the command refuses a real run
with `DEBUG` on. The droplet's token can also delete, so this covers disk
loss and mistakes, not a compromised droplet; an occasional pull of
`backups/` to Nunatak covers that.

## Invariant checks (2026-11-14)

`manage.py check_invariants [--no-db] [--strict]` states the hand-kept
agreements as code: class-rule labels vs `map.js` `CLASS_ORDER` and
`views._CLASS_COLOR` (by running the real rules over every input
combination), the copies of the `year_num` SQL, `forms.DISPLAY_REQUIRED`,
the pinned-label lists, ramp files; and against the database, public records
hidden by an unlisted class and records where the SQL age and the Python era
rule disagree. Code failures exit 1; data findings are reported. Run it in
dev before a deploy and on prod after one.
