# lidar-tiles

A Cloudflare Worker that serves single lidar tiles out of the PMTiles archives in
the `landslidescience-lidar` R2 bucket, caching each tile at the Cloudflare edge.
Why and how: see the header of `src/index.ts`.

```
GET https://tiles.landslidescience.org/t/<archive>/<z>/<x>/<y>.png?v=<version>
GET https://tiles.landslidescience.org/t/<archive>.json
```

`<archive>` is the file under `pmtiles/` without `.pmtiles` (`kbay_2023`,
`kbay_2023_slope`, `ctx_3dep`). `tools/lidar/make_catalog.py` writes these URLs
into the catalog as `tiles_url` / `slope_tiles_url` (and `context.tiles_url`)
when `LIDAR_TILES_PUBLIC_BASE` is set; clients use them in preference to
`pmtiles_url` via `DemShade.catalogOpts`. With the variable unset the catalog has
no tile URLs and every client reads the archives directly, exactly as before --
so switching the Worker on or off is a catalog regeneration, not a code deploy.

## Develop

```
npm install
npm test                               # handler tests against a real local archive
npx wrangler dev --env dev --port 8787 # real Workers runtime, archives over HTTP from R2
```

For an end-to-end check on the dev site, regenerate the dev catalog with
`LIDAR_TILES_PUBLIC_BASE=http://127.0.0.1:8787/t python3 tools/lidar/make_catalog.py`
and load http://localhost:8001/lidar/.

## Deploy (needs a Cloudflare API token)

Put these in `~/.cloudflare.env` (mode 600, never in the repo):

```
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ACCOUNT_ID=259ec95984f6188dec10f7b6ec192763
```

The token needs: Account > Workers Scripts > Edit; Account > Workers R2 Storage >
Read; Zone (landslidescience.org) > Workers Routes > Edit; Zone > DNS > Edit (the
custom domain `tiles.landslidescience.org` is created by the deploy). Then:

```
set -a; . ~/.cloudflare.env; set +a
npx wrangler deploy
LIDAR_TILES_PUBLIC_BASE=https://tiles.landslidescience.org/t python3 tools/lidar/make_catalog.py
```

and install the catalog on the droplet as usual.

## Cost

Every tile request invokes the Worker, cached or not. Workers Free covers
100,000 requests a day (a full-screen view is a few hundred tiles, so a few
hundred views a day) with a 10 ms CPU budget per request; cache hits use well
under that. Past that, Workers Paid is $5/month for 10 million requests.
