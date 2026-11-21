# Work streams — how the sub-projects relate (2026-09-21)

Several Claude sessions work on this repository in parallel, in separate git
worktrees (two at first; glaciers became the third on 2026-11-20). This file
says what each stream owns, how the pieces connect, and what to do at the
boundaries. Read it before editing anything under `tools/lidar/` or
`glaciers/`.

## The pieces

| piece | where | what it is | serves |
|---|---|---|---|
| **Landslide inventory** | `inventory/`, `/inventory/` on the site | the public landslide map: scarps, faults, susceptibility, OPERA DIST, IceBoost bed, imagery | the public |
| **Lidar viewer + hosting** | `lidar/`, `/lidar/`, `tools/lidar/build_lidar.py`, `make_catalog.py`, `datasets.json`, R2 | turns survey deliveries into archive COGs + PMTiles, publishes them, and shows them with hillshade, slope, difference and 3D; the landform trace tool (2026-10-11). The profile tool is on `/inventory/`, not here | the public; the inventory (elevation context); trailgeek.org |
| **Topobathy compositing** (this branch) | `tools/lidar/topobathy/`, `composite.py`, `grid_bathy.py`, `mask_tool/`, `composites/`, `TOPOBATHY_PLAN.md` | makes *new* DEMs out of existing ones: a dated, classified, quality-aware layer stack folded through masks, with a local mask editor; plus an observe stage (gridding sparse soundings) | the lidar viewer (composites become datasets) |

The chain is one-directional:

    survey deliveries ──build_lidar──▶ archive COGs (+ datasets.json entry)
                                            │
                                            ▼  (sources, by id or path)
                                topobathy compositing ──▶ composite COG + datasets.json entry (archive_mode: copy)
                                            │
                                            ▼
                                       /lidar/ viewer  ──▶  /inventory/ (elevation context)

Compositing consumes what hosting produces and produces one more thing
for hosting to publish. It never edits a source COG, never publishes to
R2 itself, and never runs on the droplet. Its outputs are ordinary
datasets, `dev_only` until Hig lifts the gate, published by the hosting
pipeline like any other survey.

## Streams, branches, worktrees

| stream | branch | worktree | owns |
|---|---|---|---|
| **Inventory + lidar hosting/viewer** | `main` | `~/Claude_projects/landslidescience` | everything not listed on the right; `datasets.json`; `RESUME.md`; the dev server on :8001; `$LIDAR_BUILD/cog/`, pyramids, catalogue, R2 |
| **Topobathy compositing** | `topobathy-compositing` | `~/Claude_projects/landslidescience-topobathy` | `tools/lidar/topobathy/`, `tools/lidar/composite.py`, `tools/lidar/grid_bathy.py`, `tools/lidar/mask_tool/`, `tools/lidar/composites/`, `tools/lidar/TOPOBATHY_PLAN.md`; mask-tool servers on :8765–8767; `$LIDAR_BUILD/composite/`, `$LIDAR_BUILD/obs/` |
| **Glaciers** (2026-11-20) | `glaciers` | `~/Claude_projects/landslidescience-glaciers` | `glaciers/` (the app, its templates and `static/glaciers/js/`), `tools/glaciers/`, `tools/build_glacier_*.py`, `docs/GLACIERS.md`, `GLACIERS_PLAN.md`; the glacier tables in PostGIS; `data/glaciers/`, `data/glacier_videos/`; its own dev server on :8004 |

Rules at the boundary:

- **Do not edit the other stream's files.** If you need a change there,
  write it down (this file, or the plan) and leave it for the other
  session.
- **`datasets.json` is hosting's.** When compositing has a composite to
  publish it adds one entry *on its branch* and says so; hosting merges
  and runs `build_lidar` / catalogue / R2 as usual. That is the only
  file both streams will touch, and a merge conflict there is trivial.
- **Point clouds (2026-11-06).** `tools/lidar/points.json` and
  `points_publish.py` are hosting's: the registry of each survey's point
  cloud revisions and the tool that indexes and uploads them to R2
  `points/<survey>/<rev>/`. The registry's header is the specification
  (r0 = as delivered, linked upstream where an agency serves it; r1… are
  ours and are never overwritten; `full` = tiles rewritten, `recipe` = a
  corrections file applied to the parent). Compositing revises points; when
  it has a revision worth publishing it writes the entry on its branch
  (kind, parent, what changed, where the tiles or the corrections file are)
  and says so, as with `datasets.json`. Hosting uploads. Nothing but Kenai
  2008 r1 is published yet; the `recipe` form is not built.
- **`build_lidar.py` helpers** (`retag_generic_nad83`,
  `apply_vertical_shift`, the COG profile) are imported by compositing,
  not copied. Hosting may change them; compositing adapts at merge.
- **Glaciers borrows the inventory's map modules; it does not own them.**
  The `/glaciers/` pages load `basemaps.js`, `ls_hash.js`, `ls_proj.js`,
  `ls_tools.js`, `ls_colors.js`, `ls_overlays.js`, `scarps.js`,
  `trace_rasters.js`, `lidar_ui.js` and the shading code from
  `inventory/static/inventory/js/`. Those, `landslidescience/` (settings,
  root urls), the base templates and anything else outside the glaciers row
  are `main`'s. A change glaciers needs there is written down for the main
  session (here, or docs/GLACIERS.md), or made on `main` and merged into the
  branch; never edited on the glaciers branch, where it would diverge
  silently from what the inventory runs. The exception is `ls_overlays.js`'s
  glacier overlay definitions, which both pages draw: change them on `main`.
- **Glaciers dev server.** `docker-compose.glaciers.yml`, run from the
  glaciers worktree (command in its header), serves that worktree's code on
  :8004. It mounts the MAIN checkout's `data/` and uses the same dev
  PostGIS, so there is one set of records, one SQLite and one sign-in.
  Consequences: a Django (SQLite) migration added on either branch is
  applied to the shared file as soon as that container starts, so new
  models go in on `main` first; a `migrate_glaciers` schema change on the
  branch changes the tables the main server's `/glaciers/` reads too; and
  the worktree has no `data/` of its own (host tools that write under
  `data/` must be pointed at the main checkout). R2's CORS list names
  dev origins by port, so lidar layers read from R2 may not load on :8004
  until that port is added in the Cloudflare dashboard.
- **Servers and ports.** Dev Django on :8001 (hosting; :8000 is Tethys);
  glaciers dev Django on :8004 (:8002 ad-hoc proxies, :8003 Umami).
  Mask tool on :8765–8767 (compositing; Flask, no Django, safe to leave
  running across a dev-server restart).
- **Build volume.** Both streams write to `/Volumes/Nunatak/lidar_build`
  in disjoint directories (table above). Nothing under `composite/` or
  `obs/` is published or deployed.
- **Memory.** Claude's memory directory is keyed on the working
  directory, so each worktree has its own; the shared facts (deploy
  rules, GDAL paths, datum traps) are in `CLAUDE.md`, `HAZARDS.md` and
  `tools/lidar/RESUME.md`, which both streams read.
- **Pushing and deploying** follow `CLAUDE.md` for both streams: never
  without Hig's explicit approval. The compositing branch is not pushed
  until it has something to publish.

## Merging back

The compositing branch merges into `main` when a composite is ready to
publish or when hosting needs one of its pieces (e.g. the profile tool
or the observation-package idea). Expected conflicts: `datasets.json`
only. After a merge, `TOPOBATHY_PLAN.md` on `main` is the current one.

Glaciers merges into `main` when a piece is ready for Hig to test for
shipping; deploys happen from `main` only, by the main session's rules.
Before starting glaciers work, merge `main` into the branch, so the shared
modules it loads are current.

## Where each stream keeps its state

- Hosting: `tools/lidar/RESUME.md` (state of play), `/lidar/audit/`
  (live seams check).
- Glaciers: `docs/GLACIERS.md` (plumbing, dated sections newest last),
  `GLACIERS_PLAN.md` (decisions), and the `/glaciers/` resume list in
  `docs/STATE.md`, which the glaciers stream keeps current on its branch.
- Compositing: `tools/lidar/TOPOBATHY_PLAN.md` — first section "State of
  play", then "Revision 2" (the three-axis model), then the paused
  interpolation section; dated sections at the end record what was
  built and learned, newest last.

## Sister sites — trailgeek.org (added 2026-10-11)

trailgeek.org (repo `hig314/trailgeek`, local `~/Claude_projects/trail_geek`,
developed mostly from Claude Code on the web) is a trails site: Django +
MapLibre 5.24 + D3, its own droplet and database. Its canonical account of
the relationship is its `docs/SISTER_PROJECTS.md`; this is the view from here.

**What it takes from this repo:**

- **Code, by pinned copy.** `basemaps.js`, `ls_hash.js`,
  `dem_shade_bridge.js` and the vendored demshade build, copied verbatim
  under a provenance header; `tools/shared.json` pins the commit and each
  file's SHA-256, `tools/sync_shared.py check` fails its CI if a copy is
  edited there, `sync` pulls a newer commit. **So a change to one of those
  files here is a change to trailgeek at its next sync** — say so in the
  commit message. To change a shared file, change it here; never there.
- **Data, by URL.** The public lidar catalogue
  (`/lidar/catalog.geojson`, CORS `*`) and the R2 archives / tile Worker,
  both of which now allow the trailgeek.org origin. Gated surveys are not
  available to it (a landslidescience session does not cross sites).
  `datasets.json` property names are therefore an API trailgeek reads:
  add properties freely, rename or remove none without telling it.

**What it has that we do not, and the reverse:**

- Its alignment editor (`tg_editor.js`) is a richer line tool than our
  traces: legs with shared joints, snapping to the trail network, follow /
  split modes, undo. Our landform traces are deliberately simpler (one line,
  a kind, a note). If ours grows editing (vertex drag, undo, snapping to
  contours or crests), look at `tg_editor.js` first; the right end state is
  one module both sites load, not two editors.
- Its profile is server-evaluated on lidar COGs (`core/dem.py`, GDAL
  `/vsicurl/`); ours (`profile.js`) reads the PMTiles in the browser.
  Different jobs (trail grades vs. a quick look), same data.
- It uses Alt-click to delete a vertex in its editor; here Alt-click is the
  elevation probe. Harmless while the two never share a page; worth
  settling before a shared tool module exists.

**Open items between the two:**

- **`dem_stack.js`** — trailgeek extracted `/lidar/`'s inline DEM-stack
  logic into a DOM-free module (`core/static/core/js/dem_stack.js`,
  `LSDemStack`) and asked this repo to adopt it at
  `inventory/static/inventory/js/dem_stack.js`; then it moves into the
  pinned files. Not done yet. It is the natural first step of any `/lidar/`
  refactor, and it shrinks the ~1,960-line inline template.
- **`ls_tools.js`** is listed there as reusable but not yet copied; with
  `/lidar/` now loading it (trace tool), it is proven outside `map.js`.
- **`hig-maplibre-kit`** — the plan (its PLAN.md §2) to move the shared
  modules into one package both sites vendor. Deliberately deferred; it
  edits this repo and needs its own reviewed change.
- **MapLibre versions**: trailgeek and `/lidar/` are on 5.24.0, `/inventory/`
  and `/glaciers/` on 5.5.0. A shared tool module has to run on both until
  the inventory is upgraded.
