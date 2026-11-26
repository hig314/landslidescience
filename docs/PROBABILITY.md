# `/probability/`: site-specific hazard analyses

*Added 2026-11-25. First project: `/probability/homer/` (shallow landslides, Homer), unpublished.*

A category for hazard analyses of one place each, at whatever scale the place
calls for, that work toward stated probabilities rather than "susceptibility"
(Hig's reason for the name). The analyses and the viewer are built in a
separate public repository, **ProbHaz** (`github.com/hig314/ProbHaz`,
`~/Claude_projects/ProbHaz`); this app only hosts them.

## What lives where

| Thing | Where | How it gets there |
|---|---|---|
| Project registry (title, published flag, DEM, opening view, template) | `probability/projects.py` | edited here |
| Viewer JS/CSS | `probability/static/probability/` | **copied** by ProbHaz `publish/to_site.py`; edit in ProbHaz |
| Page body (sidebar, panels) | `probability/templates/probability/_viewer_body.html` | same copy; edit in ProbHaz |
| Page shell, `PH_CONFIG`, script tags | `probability/templates/probability/base.html` | edited here |
| A project's data (value tiles, GeoJSON, tables; Homer is 1.5 GB) | dev: `data/probability/<site>/`. Prod, unpublished: PRIVATE bucket (`drops.r2`), key `probability/<site>/...`. Prod, published: public bucket | `publish/to_site.py`, then `manage.py probability_push <site> --write` |

The shading library and `dem_shade_bridge.js` are the site's own copies under
`inventory/static/`; the surface is the lidar catalogue's pyramid
(`/lidar/pmtiles/<dem>.pmtiles`).

## Routes

| Path | Audience |
|---|---|
| `/probability/` | public; lists published projects, and unpublished ones to accounts that may see restricted data |
| `/probability/<site>/` | published: public. Unpublished: `can_view_restricted` (every new account is in `data_users`); anonymous goes to the sign-in page. Any case redirects to lowercase |
| `/probability/<site>/data/<tiles\|inventory\|zonal>/<name>.<pmtiles\|json\|geojson>` | same gate, on the bytes |

## Rules

- **The gate is on the data, not just the page**, and an unpublished
  project's data must never be put on the public bucket (it has no gate, and
  `rclone copy` is not retroactive: HAZARDS.md). That is why unpublished data
  is read from the private bucket THROUGH this server (`views._from_private_bucket`).
  The droplet could not hold it anyway (1.9 GB free on 2026-11-25).
- **Publishing a project** = upload its data under `probability/<site>/` on
  the public bucket, check it answers 206, then set `published: True`. In that
  order. There is no command for the upload yet.
- **One page template, written to be inherited** (Hig, 2026-11-25): a project
  that needs its own tools names a template in `projects.py` that extends
  `probability/base.html` and overrides a block.
- **The viewer's query-string names are permanent once a link is shared**
  (`s`, `lo`, `hi`, `hl`, `sym`, `ages`, `scar`, `sop`, the inventory toggles,
  and the `/lidar/` shading names). Additive only after publication.
- Pages carry `noindex` and `/probability/` is in `robots.txt` while nothing
  is published.

## Updating

    cd ~/Claude_projects/ProbHaz && ./publish/to_site.py homer      # code + data into this checkout
    docker exec landslidescience-web-1 python manage.py collectstatic --noinput && docker restart landslidescience-web-1
    # test at http://localhost:8001/probability/homer/
    docker exec landslidescience-web-1 python manage.py probability_push homer --write   # data to the private bucket
    # then the ordinary push + deploy for the code

Data and code deploy separately: a data-only change needs no site deploy.
