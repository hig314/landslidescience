# CLAUDE.md — landslidescience.org

Public-facing Django site at <https://landslidescience.org>. Companion to the
private Tethys stack at `github.com/hig314/tethys-timescale-grafana`.

## Layout

| App | Purpose |
|---|---|
| `pages` | Editable site content (homepage, `/tracyarm2025/`). Page model in SQLite, edited via `/admin/`. |
| `inventory` | Public landslide inventory map. Reads `tethys_db.landslides` (PostGIS) over the shared Docker network — no Django ORM, raw psycopg2. |

## Production

Droplet: `root@143.198.140.54`, deployed at `/opt/landslidescience`.
Caddy (running in the Tethys monitoring stack at `/opt/monitoring`) reverse-proxies
`landslidescience.org` to the `landslidescience-web` container. The container
joins both `monitoring_external` (so Caddy can reach it) and `monitoring_internal`
(so it can reach `tethys_db`).

Deploy:

```bash
ssh root@143.198.140.54 'cd /opt/landslidescience && \
  git pull && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d'
```

Production `.env` lives at `/opt/landslidescience/.env`, mode 600, never committed.
DB credentials in there mirror the monitoring stack's `.env`.

## Local dev

```bash
docker compose up -d                     # uses docker-compose.override.yml automatically
# → http://127.0.0.1:8001/
```

The local container joins the running local Tethys stack's
`tethys-timescale-grafana_internal` network (declared external in the override),
so the inventory map page works locally if the local Tethys stack is up.

If the Tethys stack isn't running locally, the homepage and admin still work;
only `/inventory/*` will fail (no DB to read from).

## Forward-looking integration

The Tethys monitoring stack (sensor dashboards, access-controlled admin) stays in
its own repo. As public-facing components from Tethys move here, they read from
`tethys_db` directly via psycopg2 (no Django models for landslide data).

Phase 3 of the inventory port is decommissioning the Tethys-side `landslides`
app once we've soaked on the new one for a couple of weeks.

## Conventions

- Don't commit `.env` or `data/` (gitignored).
- Hig is the superuser on production. Reset password via:
  `docker exec -it landslidescience-web python manage.py changepassword Hig`
- WhiteNoise serves static files in production; runserver serves them in dev.
- Caddy auto-issues Let's Encrypt certs; non-canonical domains 301 to canonical.
