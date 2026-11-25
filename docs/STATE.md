# State of play

*Last revised 2026-11-24 (glaciers entries; the rest 2026-11-21).* The one dated snapshot in the project notes: what
is waiting to be confirmed on production, what is open, and a short log of
what shipped. **It rots**; correct or delete an entry rather than working
around it. For lidar publishing state do not trust any number written down
anywhere: read `/lidar/audit/`.

How this file is kept: a feature's design lives in its `docs/` file, not
here. When something ships it gets one line in the log and, if part of it has
not been exercised on production, one line in the checklist. Log lines older
than about two months can go; git has them.

## Restart here (2026-11-25)

**Main stream (inventory + hosting): nothing in flight.** Code on prod is
60e668c (main = origin/main = droplet as of the last deploy). Hig has a new
topic for after the restart.

- **Outside inventories, finished 2026-11-24/25** (detail:
  docs/EXTERNAL_INVENTORIES.md, "Promotion" and the sources table):
  - Third source `usgs_schaefer2026` (Schaefer and others 2026, USGS data
    release doi:10.5066/P1B64SZT; 239 PSInSAR outlines, CC0) fetched on prod;
    hard yellow outline style. Not the older `insar_schaefer` work.
  - Promotion text = their id + their comment / citation / confidence /
    rate; several sources per record (`landslide_external_links`,
    `migrate_external_links` run on prod, 21 links); unlink; noted_by from
    their citation's first author; promotion starts from their outline;
    Link offers strong overlap first; Schaefer sets `insar_other` + cascade.
  - `backfill_promotion_text --apply` was run on prod 2026-11-24 (21
    records); a re-run is a no-op.
  - Untried on prod (browser side): see "To confirm on production".
  - Not done, Hig's call: an Inventory-tab explorer for Schaefer ("not
    yet"); Canada's `deformation_insar_data` → `insar_other` (offered, not
    asked); the InSAR categorisation rethink (Open threads).
- **Dev trap met twice this session:** dev serves static JS under fixed
  names and Chrome reuses a stale copy after collectstatic, so a fixed
  feature looks unfixed. Hard reload (Cmd+Shift+R). A middleware cannot fix
  it (runserver serves /static/ before middleware); `runserver --nostatic`
  + WhiteNoise would, not done.
- Uncommitted on purpose: the `docker-compose.override.yml` Nunatak pmtiles
  mount (local-only), `logs/`, the two `_scoops_core.so` builds,
  `inventory/DECK_EMBED.md` (another session's).

Carried from 2026-11-21, **not re-checked** this session:

- **Scoops3D reference pilots: paused.** Resume commands in
  `tools/terrain/scoops/README.md`, "Paused 2026-11-20". The
  no-comparison hold still applies to Scoops.
- **Topobathy `run_1118.sh`** belongs to the topobathy session; its state is
  atop `tools/lidar/TOPOBATHY_PLAN.md` (topobathy worktree) and in
  `$W/run_1118.log`.
- **Hand-placed sounding points: data copy to prod was pending** (export
  and `hp_import.py` in `data/soundings_handoff/`; run command in its
  header). Do not place points on prod before it runs.
- Before relaunching any long run: `pmset -g batt` on AC, Nunatak mounted,
  runs under `caffeinate -i -s`.

## To confirm on production

Each of these was deployed after Hig tested it in dev, but the part named has
not knowingly been used on prod. Most get confirmed by ordinary use and
nobody writes it down, so **from time to time Claude reads this list back to
Hig and strikes what he has used**; only what is still untried needs a
deliberate look. Add a line at deploy time whenever a path was checked only
by curl, by API or not at all.

- [ ] **Imagery review history** (0ec6b75, 2026-10-26): signed-in use on prod; the one-time notice has not been seen there.
- [ ] **ArcticDEM in the ∿ profile and Alt-click probe** (bd1a6a9, 2026-10-30): nobody has drawn a profile on prod.
- [ ] **Shaded relief overlays** (30dd97f, 2026-10-30): page loads and served JS only.
- [ ] **Overlay regrouping + fixed-scale slope rows** (dfe53d2, 2026-11-01): not drawn in a browser on prod.
- [ ] **NOAA chart basemap, NASA global susceptibility overlay** (9585f3d, 2026-11-05): neither drawn in a browser on prod.
- [ ] **NASA susceptibility and the five fixed-scale slopes as analysis axes** (df9247b, c0ce02a): checked by API only.
- [ ] **Edit-form checks** (069905e, 2026-11-11): nobody has saved through the banner / date-bracket / year-text checks on prod. Records 1166, 1174 and 1504 will trip them on their next edit (see *Edit-form follow-ups* below).
- [ ] **Hand-placed sounding points on /lidar/** (251acb6, deployed 2026-11-21): never tested in dev or prod; read endpoints answer 200, nobody has placed or edited a point.
- [ ] **Landform statistics as analysis axes** (7d208a9, 2026-11-21): density API checked on prod; not drawn in a browser there.
- [ ] **Terrain comparison** (2026-11-12/13): profiles, shared QMS bases, `hc=1`, JPEG export not browser-tested on prod.
- [ ] **Saved comparisons** (6c8dc13, 2026-11-13): nothing saved on prod yet; the first signed-in *Submit* is the first real test.
- [ ] **`/traffic/services/`** (2026-11-14): not opened signed in on prod.
- [ ] **Faster trace-raster bake** (3e8ec3b, 2026-11-14): one 8-bit RGB re-bake on prod (row 9, 3,308 tiles, minutes) and a 16-bit one on dev; a false-colour (4-band) bake and an upload through the browser have not run on the new code.
- [ ] **map.js split rounds 3–4** (3e8ec3b, eeb5f31): Hig used the map after each and found nothing; not knowingly exercised: committing a drawn polygon, promotion through to a saved record, saving an outline edit, tracing a scarp, the signed-out page.
- [ ] **Autosave error logging; `/manage/new/` redirect** (15186a4): nothing has failed on prod since, so the log line is unseen.
- [ ] **Restore from backup** (2026-11-14): the first backup uploaded 2.6 GB; nobody has restored anything from it.
- [ ] **Inventory-tab explorer** (2026-11-16): tested by Hig in dev; on prod not yet loaded (first load builds and caches each source's payload, ~1–2 s), the wiper pane and a review saved from the explorer's popup untested anywhere.
- [ ] **External-record review** (2026-11-16): tested by Hig in dev; on prod the first category made with `+` and the first saved review are the real test.
- [ ] **`/lidar/` Save image** (a43bf5c, 2026-10-19): the export engine's polling-for-done change was not re-tested on `/inventory/`'s own PNG export.
- [ ] **S1 auto faces: reject / restore** (ead84b5, 2026-11-24): exercised in dev in-process and by Hig in dev; on prod only the anonymous 403 was checked. The first admin reject on prod (card, popup or plot) is the real test; the tiles should reload under the new stamp.
- [ ] **Frame months while a video plays, Earth Engine videos** (ead84b5): checked on prod by API (107/107 placed videos carry months), not watched playing there; videos 113 (South Sawyer) and 114 (Sitkagi) not yet opened on prod.
- Promotion *Link to an existing record* and *Unlink* (2026-11-24/25): tested on dev through the API only; the popup's overlap list, Unlink, the Promoted-from cards' Remove link, and promoting from a Schaefer outline (their outline in the name box) not yet used on prod.

## Shipped log

One line each, newest first; detail is in the `docs/` file named.

- 2026-11-25 · Schaefer 2026 overlay restyled: hard yellow outline, no fill, dark casing (registry `style`) (60e668c) · docs/EXTERNAL_INVENTORIES.md
- 2026-11-25 · External inventory `usgs_schaefer2026` (Schaefer and others 2026, 239 PSInSAR outlines, zipped-shapefile fetch); several sources per record (`landslide_external_links`, `migrate_external_links` run on prod: 21 links), unlink, noted_by from their citation's first author, promotion starts from their outline, Link offers strong overlap first, Schaefer sets `insar_other` + cascade (cbc064e..a4dc4cc) · docs/EXTERNAL_INVENTORIES.md
- 2026-11-24 · Promotion text carries their id (+ DGGS OBJECTID) and their comment/citation, merged into ongoing_work; *Link to an existing record* in the mirrored-record popup; `backfill_promotion_text` applied on prod (21 records: 4 unstamped stamped by nearest point, 3 DGGS OBJECTID stamps corrected) (aca5194) · docs/EXTERNAL_INVENTORIES.md "Promotion"
- 2026-11-24 · /glaciers/: one video shown per framing (similar frames hidden, other look direction kept when mostly complete; 6ccedc8); Tyndall timelapse built (video 115) with 68 auto faces, after Hig moved its flow line onto the front · docs/GLACIERS.md "Similar frames", "Tyndall: the flow line has to cross the face"
- 2026-11-24 · /glaciers/: S1 auto faces live (source `s1auto`, 234 faces on Endicott, South Sawyer, Sitkagi), admin reject/restore, *Ice faces* switches, frame months for the Earth Engine videos, our South Sawyer + Sitkagi timelapses (videos 113, 114), year bar to the current year (ead84b5) · docs/GLACIERS.md "Ice faces from our S1 timelapses" › Start here
- 2026-11-23 · /glaciers/: S1 timelapses built from the archive (build.py, Endicott = video 112), month shown live, products, Left/Right frame stepping that loops (bd6618f, e40bfbc, 016e764) · docs/GLACIERS.md "Building S1 timelapses ourselves"
- 2026-11-21 · Ridge radius, ridge n, valley fall F as Cross-two-overlays axes, landslide-comparison hold lifted by Hig (7d208a9); hand-placed sounding points (251acb6, untested, data copy pending) · docs/ANALYSIS.md
- 2026-11-19 · Daymet precipitation + air temperature, overlays and analysis axes (76febe2); exploratory landform statistics, overlays only (6764ed9) · docs/PERMAFROST.md. Open: multi-size required-strength run stopped when Nunatak was unplugged, next steps atop tools/terrain/scoops/README.md
- 2026-11-16 · Inventory-tab explorer for the outside inventories ("Showing" switch; families, ring/size/fade, presets, their fields, review filter, spelling fold) · docs/EXTERNAL_INVENTORIES.md
- 2026-11-16 · Review of mirrored external records: shared categories (`+`), note, promoted read live; editors only; `migrate_external_review` per environment · docs/EXTERNAL_INVENTORIES.md
- 2026-11-14 · map.js split, round 4: `draw_ui.js`, `lidar_ui.js`, `scarp_ui.js`, `revise_ui.js`, `charts.js`; map.js is 333 KB. Start-up and opening each tool checked in Chrome on dev and prod; Hig tested after the deploy ("looks good", 2026-11-15) (eeb5f31) · docs/MAP_UI.md
- 2026-11-14 · map.js split, round 3: `trace_rasters.js`, `dist.js` (3e8ec3b; deployed on Hig's approval of the description, used by him on prod afterwards: "all looks good so far") · docs/MAP_UI.md
- 2026-11-14 · Trace-raster bake builds coarse zooms from child tiles (minutes, not hours, for a large upload); `deploy_ready` guard in the deploy line after a deploy killed a bake · docs/TRACE_RASTERS.md
- 2026-11-14 · map.js split, round 2: `photos.js`, `external_inv.js` (tested in dev by Hig); their state is now private behind `_photoShow` / `_photoOpenAt` / `_extEnsureRegistry` / `_extApplyOrDefer` · docs/MAP_UI.md
- 2026-11-14 · map.js split, round 1: `measure.js`, `opera_hist.js`, `export_ui.js`, `insar_ts.js` (tested in dev by Hig); InSAR tool no longer blanks when one pass has no coverage (ASF's 400 "no data" read as no coverage) · docs/MAP_UI.md, tools/jscheck/
- 2026-11-14 · Baked overlays (susceptibility, coherence, IceBoost, ITS_LIVE, Hugonnet) read from PMTiles on R2 (fad9b74); the five tile folders deleted from the droplet · docs/OVERLAYS.md
- 2026-11-14 · `check_invariants`, service probe + `/traffic/services/`, nightly `site_backup` to R2, local-only lidar manifest, autosave error logging, `/manage/new/` retired, Scoops tools committed with the row off (15186a4) · docs/OPERATIONS.md, docs/LIDAR.md
- 2026-11-14 · CLAUDE.md split into an index plus `docs/` (725b3c8)
- 2026-11-13 · Terrain comparison later rounds: re-order panes (09fcec9), QMS bases in the link (0af7cb2), `hc=1` (e4f502f), equal panes + JPEG (f2b7e33), saved comparisons (6c8dc13) · docs/LIDAR.md
- 2026-11-12 · Terrain comparison `/lidar/compare/` with profiles (7c93ea1, bf27a7c); `/lidar/` *Point cloud* link · docs/LIDAR.md, LIDAR_COMPARE_PLAN.md
- 2026-11-11 · Edit-form checks; Explore opens the record's view (069905e) · docs/RECORDS.md, docs/EXPLORER.md
- 2026-11-11 · Stereonet equal-area cells + aspect effect · docs/ANALYSIS.md
- 2026-11-10 · Slope-aspect stereonet (b3d1906) · docs/ANALYSIS.md
- 2026-11-08 · Fixed-scale slopes as analysis axes (c0ce02a) · docs/ANALYSIS.md
- 2026-11-07 · NASA susceptibility as an analysis axis (df9247b); stale-values-file fix (`DATA_V`) · docs/ANALYSIS.md
- 2026-11-06 · Homer 2019 + 2021 NCMP published (8a7a653); Kenai 2008 point cloud on R2 · tools/lidar/RESUME.md
- 2026-11-05 · NOAA chart basemap + NASA global susceptibility (9585f3d); erosion-rate overlays built and switched off · docs/OVERLAYS.md
- 2026-11-05 · Grand Plateau preliminary DSM, gated (64d2006); gated `_slope` pyramids closed to anonymous requests (c309b1c) · tools/lidar/RESUME.md
- 2026-11-01 · Overlay regrouping + slope at fixed scales (dfe53d2) · docs/PERMAFROST.md
- 2026-10-31 · Analysis panel: reviewed area + subsets (df4d19c..e0d93cb); drops resume mid-file (a400820) · docs/ANALYSIS.md, docs/FILES_DROPS_PHOTOS.md
- 2026-10-30 · Default views with restricted layers (806b024); shaded relief (30dd97f); ArcticDEM in profile/probe (bd1a6a9) · docs/MAP_UI.md, docs/OVERLAYS.md
- 2026-10-29 · Canadian layers: US / Canada Topo, BC Orthophoto, NRCan InSAR (4f4c5af, 9c504c2, 5a24b7d); emailed game cams · docs/OVERLAYS.md, docs/FILES_DROPS_PHOTOS.md
- 2026-10-26 · Edit-form fixes (c3232c0); imagery review history (0ec6b75) · docs/RECORDS.md, docs/MAP_UI.md
- 2026-10-19 · `/lidar/` links (`ce=`) and PNG export (9534b42, a43bf5c) · docs/LIDAR.md
- 2026-10-12 · Lake soundings · docs/TRACES.md
- 2026-10-11 · Landform traces (kinds, groups, group profile); the brush paints the map · docs/TRACES.md, docs/ANALYSIS.md
- 2026-10-10 · The analysis panel · docs/ANALYSIS.md
- 2026-10-09 · Permafrost overlays + Surface properties · docs/PERMAFROST.md
- 2026-10-05 · Photo drops · docs/FILES_DROPS_PHOTOS.md
- 2026-09-24 · External inventories; pointer policy (`ls_tools.js`) · docs/EXTERNAL_INVENTORIES.md, docs/MAP_UI.md

## Open threads

In no particular order.

- **InSAR categorisation needs restructuring (Hig, 2026-11-24).** The
  per-study InSAR columns (`insar_schaefer` = Schaefer's earlier Prince
  William Sound work, `insar_kim`, `insar_opera`, `insar_other`, OR-merged
  into `insar_creep` by derived.py) were built for a few studies; InSAR
  inventories are multiplying (Schaefer 2026 southern Alaska PSInSAR, NRCan
  via Canada's `deformation_insar_data`, our OPERA/NRCan overlays). For now a
  record promoted from or linked to Schaefer 2026 gets `insar_other = True`
  (registry `set` rule, docs/EXTERNAL_INVENTORIES.md). To design: a structure
  that scales with sources (e.g. a per-source InSAR observation table with
  study, sensor, period, rate, confidence) feeding `insar_creep`, plus what
  happens to the existing columns, the class rule's dependants and the
  methods page.
- **Glaciers work stream, 2026-11-24: S1 timelapses and auto faces LIVE,
  nothing in flight.** Worktree `landslidescience-glaciers` (branch
  `glaciers`, dev :8004) = main = prod at ead84b5 plus docs. Start at
  docs/GLACIERS.md, "Ice faces from our S1 timelapses" › *Start here*
  (recipe for a new glacier, the rules, what is open), then GLACIERS_PLAN.md
  §3. Work dirs: `/Volumes/Nunatak/glacier_s1_build/NAME/` (stacks, faces,
  review images), staging in the main checkout's `data/glaciers/s1_build/`.
  Dev-only data: prod's Dawes lines 2164–2203 copied into dev (same ids) for
  testing; dev's video ids differ from prod's. Next, by Hig's lead: run more
  glaciers; Endicott's three misses; maybe a placement nudge for videos.
- **Where the 2026-11-14 session stopped (nothing in flight).** main =
  origin/main = droplet at eeb5f31, apart from docs-only commits after it;
  the only uncommitted file is the local-only Nunatak mount line in
  `docker-compose.override.yml`, which stays uncommitted. Hig's stated next
  topic: back to the **terrain comparison** (`/lidar/compare/`; decisions and
  measurements in LIDAR_COMPARE_PLAN.md §10–11; not built: server-side
  sheets, publish/branch, the `lidar_scene.js` extraction, a shading worker
  pool; on prod, profiles / shared QMS bases / `hc=1` / JPEG export and the
  first *Submit* of a saved comparison are still untried, see the checklist).
- **S1 videos placed from their content — LIVE 2026-11-22** (22be63d;
  docs/GLACIERS.md, "Placing S1 timelapses from their content"). 71 of the
  76 site videos have a footprint box + five products on prod
  (`glacier_videos.georef`, `migrate_glaciers` run, 355 WebPs / 142 MB in
  `data/glacier_videos/georef/`, droplet 5.4 GB free after). Placed videos
  no longer show their path dot. Not placed: Grand Pacific "Ferris", Knik
  3b7679, both Agassiz, Bering East (Hig to give centres with the locator).
  Work dir `/Volumes/Nunatak/glacier_s1_georef/` (accepted.txt = the
  eye-checked list; review_skip_v2.txt the five). Strong candidates
  (`cand_batch.py cand/strong.json`): 11 of 18 placed and LIVE the same
  day, placed BEFORE import, so they went in without Drive: the local mp4s
  (sha1(drive id)[:16] names) + WebPs rsynced, rows inserted from
  `data/glaciers/s1_georef/cand_dev_rows.json` (added_by 's1 georef
  candidate'), then `s1_videos georef cand_georef.json`. Prod: 87 s1
  videos, 82 placed, 4.9 GB free. Open: 34 videos need a centre from Hig
  (review page's "Needs a centre" view saves `center` [lat, lon] +
  `center_text`, `loc_note` in its `decisions` docs; 5 site, 7 missed, 22
  not searched): rerun match2 + fine with that as the anchor.
  Same day, later: 28 placed from Hig's centres (`cand_batch.py --tag _c
  --centre`; work in `cand/centres*.json`, `cand/rows_c.json`, products in
  `cand/web_c/`): 24 on PROD (105 s1 videos, 100 placed, 4.7 GB free), the
  last 4 (McCarthy, Oily Lake, Lowell, Lituya Deltas) in dev only
  (`cand/rows_4.json` + `georef_4.json`), waiting for Hig. Alsek Lake needed
  `match2 --flat-water` (3DEP holds the lake at 24 m); Lowell (Yukon) needed
  fine.py's Mapterhorn zoom fallback. Not placed: both Caetani (Agassiz) and
  12 not yet given a centre.
- **S1 timelapse import — first batch LIVE 2026-11-21** (docs/GLACIERS.md,
  last section). Drive's quota had reset; the page's decisions were
  re-exported (20: 16 approved, 3 assigned, 1 rejected) and all 19 imported
  on prod, 0 failed (log `data/glaciers/s1_review/import_20261121.log` on the
  droplet; the 2026-11-20 manifest/decisions kept beside it as
  `*.2026-11-20.json`). Second batch the same evening: 44 more, 0 failed
  (`import_20261121b.log`; inputs before it kept as `*.2026-11-21a.json`);
  prod now has 76 `s1` videos and 5.6 GB free on the droplet, so the next
  batches need a look at disk first. Columbia3 sits on Columbia's lowest leg
  (Hig to say whether it moves up the east arm). Picking a leg on the page:
  paste a /glaciers/ URL centred on the leg (`map=z/lat/lon`); the import does
  not read it yet, so such videos are moved by hand after the run. Hig's placements after
  the run: Land lobe moved one leg up Columbia's east arm (line 416, 0.58–0.70;
  retreat), Baird Middle one leg up the Baird trunk (455, 0.64–0.81); Alsek and
  both Brady videos stay where the import put them. The review page now sorts
  groups by how sure the match is (strong / one thing to check / guessed / not
  placed; `tierOf` in its index.html, kept in the main checkout's
  `data/glaciers/s1_review/page/`). The rest of the folder (~125 videos) is unreviewed; the same
  steps bring in the next batch (reruns skip what is already in). The 93
  videos without a gap read keep the interim rule (40+ missing months = gap);
  do not re-run `gap` over the folder (it spends the quota).
- *(Superseded by the 2026-11-24 glaciers entry above; kept for the
  history of how prod was loaded.)* **`/glaciers/` — DEPLOYED 2026-11-18, then SET ASIDE by Hig the same
  day** (3ad1851, e65bb2b, 0a35193, 654a8bf; prod is at 654a8bf). Nothing
  of this work is uncommitted or undeployed. **To resume, start here:**
  (1) **prod is the working copy** (Hig, 2026-11-20: 352 flow lines, 9
  faces there that day). **Dev refreshed from prod 2026-11-20** (Hig's
  permission): `glacier_lines`, `glacier_videos`, `glacier_video_anchors`
  COPYed from prod in one transaction, sequences set, counts and row md5s
  matched before commit; the mirror tables already agreed (same counts and
  fetch times); `data/glacier_videos/` rsynced. Dev's pre-refresh tables
  are kept in the main checkout's `data/glaciers/dev_backup_20261120/`. The
  ordered next-work list is now GLACIERS_PLAN.md §3, which supersedes items
  2–3 here; first on it is plotting GLIMS outline crossings, which is why
  Portage (17 GLIMS outlines, no terminus-line source) plots one face;
  (2) the unbuilt list: pin-a-face control, consensus face series,
  elevation as the plot axis, cross-glacier joins, split-line and
  simplify-line buttons, loading network lines by map view, byte ranges
  for Planet mp4s, a retry for a failed Drive fetch; (3) `GLACIERS_PLAN.md`
  still describes the pre-network plan and wants rewriting round the
  network, with two questions open for Hig: which DEMs to trust before
  2000, and what "consensus" means for faces; (4) unlinked glaciers Hig was
  told about and did not ask for: RGI 01-13322 beside Guyot, three small
  ones by Tyndall; (5) the 60 Grotto-labelled faces that project onto
  Moraine/Apron (Hig has since made it a distributary, line 113).
  654a8bf: a video still being copied becomes playable without a reload
  (tested in dev with a stand-in row, not with a real fetch on prod). Reference maps tab, face tracing, shared date reader,
  metric along-line positions, distributaries, one-line face projection,
  videos on segments (Planet, uploaded or Drive-linked Sentinel-1): the
  dated sections at the end of docs/GLACIERS.md. **How prod was loaded:**
  not by the fetch commands. The tables were created from
  `migrate_glaciers --dry-run` and every glacier table was streamed from
  dev with COPY (glaciers, glacier_obs, glacier_centerlines,
  glacier_sources, glacier_lines, glacier_videos, glacier_video_anchors);
  counts and content checksums matched, id sequences were advanced,
  `rebuild` on prod left the network unchanged. So prod holds exactly what
  dev held that night, including Hig's Icy Bay network (89 flow lines, 8
  faces, 2 videos). `data/glaciers/sources/`, `data/glacier_videos/` and the
  Planet mp4 were rsynced; `glac_cover_mask` / `glac_debris_mask` are on R2.
  **From here dev and prod diverge:** edits made on one are not on the
  other. A refetch on prod (`fetch_glacier_source`) is still possible and
  untested there. **Not tried on prod:** adopting a glacier, tracing a
  face, uploading a video (gunicorn's 30 s window against a large file),
  archiving a new Planet story from this page. Planet mp4s
  (`/inventory/planet/`) still have no byte-range support; uploaded videos
  do. Dev-only leftovers: one extra vertex on Grotto's line at 60 % from a
  fork test (copied to prod with the line; shape unchanged).
- **`/glaciers/` generalised; retreat histories, phases 0–2 — built in dev
  2026-11-16, tried by Hig in dev, committed and pushed 2026-11-17, NOT
  deployed (prod needs the data steps below first).** Plan and
  decisions: `GLACIERS_PLAN.md`; plumbing: docs/GLACIERS.md. The page is now
  a glacier browser (Retreat / Flow / Layers tabs); the tracer engine moved
  to `gl_tracers.js` and loads on first use. Dev PostGIS holds RGI 7.0
  (27,509 glaciers) and four mirrored extent sources (79,372 rows, about
  1 GB). To ship: push; on prod `migrate_glaciers`, `fetch_glacier_source
  rgi7`, then `--all` (rsync `data/glaciers/sources/` first; the GLIMS fetch
  takes about 15 minutes and reads glims.org from the droplet); push
  `glac_cover_mask.pmtiles` and `glac_debris_mask.pmtiles` to R2 overlays.
  Also on prod for the network: `fetch_glacier_source centerlines` (needs
  `rgi7_centerlines.geojsonl` in the rsync). **Same day, Hig refocused**
  (GLACIERS_PLAN.md §1): calving-face position, then surface elevation, along a
  river-like flow network; area is low priority. Built in dev: the network
  editor, adopt / draft / import, and the distance-time plot. Dev holds the
  four Icy Bay glaciers adopted (80 lines) plus four drafted former-bed lines
  (ids 12, 25, 38, 84); Icy Bay / Taan Fiord is Hig's first exploration, to
  be extended by hand to the historic faces. After Hig's first try: joins
  are shared coordinates with snapping, end-to-end lines fuse, drawing is
  explicit (＋ buttons), Delete removes the active vertex (shared scarps.js,
  so `/lidar/` and the inventory pencil gain it), drafts are binned and
  smoothed. Dev now: 81 flow lines, outlets = Guyot 11, Tyndall 24, Tsaa 37,
  Yahtse 83 (Hig's edited draft, fused), and stray manual line 85 (Hig's,
  left alone). Not built: the control to pin a
  face, joining across glaciers by ITS_LIVE, elevation as the y axis.
  **2026-11-17 (deployed 2026-11-18, see above):** Reference maps tab (basemap cards,
  lidar, uploads / Sentinel via the inventory's modules) and the
  face-tracing block (docs/GLACIERS.md, last section). Not checked by
  hand: an uploaded or Sentinel image switched on in `/glaciers/`, a
  basemap switch with lidar on, `/inventory/` after the shared basemap
  card and `floorLayer` edits.
  Still `noindex`. Superseded next step: phase 3 (length and area series; G10040 carries an
  RGI 6 id and a per-year area) and phase 4, the consensus layer admins
  improve (Hig's D1). Loose: only 661 glaciers are named; 82 Glacier Bay
  lines and 5,202 GLIMS outlines match no glacier; `cascade` and
  `moraine_apron` NSIDC-0634 files want a look.
- **Embeds and control subsets** (`/lidar/compare/` deployed 2026-11-22,
  c49ada0; **`/inventory/` built 2026-11-22, dev only, uncommitted**): `ui=`
  control ids + *Reader's controls* picker, `?embed=1` (framable, signed
  out), deck protocol. Comparison design in docs/LIDAR.md (end of the
  terrain comparison section); the inventory's in docs/MAP_UI.md (*Embeds
  and control subsets*), which lists what the first pass leaves out. The
  deck's brief is `inventory/DECK_EMBED.md` (untracked, Hig's). Test page:
  `~/Claude_projects/Presentation/tools/serve.py` then
  `http://127.0.0.1:8792/tools/embed_test.html?src=http://localhost:8001/inventory/?embed=1`.
  Checked in a browser on dev: embed page, a pared-down `ui=`, deck steps
  there and back in a same-origin test frame, the picker. Not checked: the
  Presentation tool itself, a cross-site frame, a hidden frame, a projector.
- **Terrain comparison: landslides + baked overlays** (tested by Hig in dev,
  deployed 2026-11-16): `ls=1` / `ov=` on `/lidar/compare/`, `/inventory/`
  links in the paste box; the shared overlay rows and landslide layers moved
  out of `map.js` (LIDAR_COMPARE_PLAN.md round 10). Not done: OPERA / NRCan /
  DIST / geology / relief / contours on compare, clicking a landslide,
  `/inventory/<slug>/` links, inventory filters.
- **map.js split, paused after round 4** (609 KB → 333 KB, 13 files out).
  Next is the overlay framework (registry and source defs first, sidebar UI
  second), then stop: the wiper, basemaps, URL-state table, filters, detail
  panel and click handling stay. Method, tools and the three rules the last
  round added: docs/MAP_UI.md and `tools/jscheck/README.md`. Before any
  deploy that touches these files: `verify.sh`, `order.py`, `check_invariants`.
- **Deploys and bakes.** The deploy line runs `deploy_ready --wait 30`
  (CLAUDE.md). Known blind spot: for 30 min after a restart that killed a
  bake it still reports that bake as running (it cannot tell a dead thread
  from a live one); `rebuild_trace_rasters --stalled` clears it. A scratch
  log `data/rebake-9.log` on the droplet can be deleted.

- **Edit-form follow-ups (2026-11-11).** Prod records the year-text check
  will catch on their next edit (read-only survey of 256 dated records: 212
  already agree, 42 will show the "dates do not constrain" warning):
  *Caribou Divide 2001* (1166: "Modern" with both dates in 2001) is refused
  on its next full save until year text reads 2001, and the banner says so;
  *Chitna Creek A Holocene* (1174: "Holocene", `date_min` 1979) gets the
  amber Holocene warning but saves; *Jan Mayan 2024* (1504: blank) is filled
  with 2024 on its next save. An autosave on dev 1490 returned 500 at 03:13
  that day from the catch-all in `manage_edit_field`, which logs nothing, so
  the field and cause are unknown (the catch-all now logs the traceback, field and user: 2026-11-14, in dev).
  Dev 1490's `updated_at` and audit row were stamped by a diagnostic POST
  (dates written then cleared, both NULL again). Unanswered offers: a
  "Saved" notice after the edit page's own reload; grid-north aspect in the
  x-y panel and aspect overlay; the analysis panel `draw()` canvas ordering;
  `docker builder prune` on the droplet. Pre-existing on prod: console
  "Style is not done loading" from `ensurePendingLayers`.

- **Homer 2019 + 2021 NCMP green lidar, and point clouds on R2**
  (2026-11-06). `homer_2019_ncmp` and `homer_2021_ncmp` are **published**
  (8a7a653; 50 public surveys). Point clouds have a registry
  (`tools/lidar/points.json`), a tool (`points_publish.py`) and a bucket
  layout (`points/<survey>/<rev>/`); Kenai 2008 r1 (our ground
  classification, 67 GiB, 3,110 tiles) is the one test upload — **done and
  verified 2026-11-06 23:22**, public at
  `lidar.landslidescience.org/points/kenai_2008/`. The `/lidar/` *Point
  cloud* link and its Methods paragraph **shipped 2026-11-12** with the
  terrain comparison (Hig approved); the prod catalogue is rebuilt with
  `points_url` per the recipe in `tools/lidar/RESUME.md`. Hig's rule: link a cloud an agency already
  serves well, do not copy it; nothing beyond Kenai 2008 until the layout is
  judged; Grewingk waits on the reclassification work. State and commands:
  the last entry of `tools/lidar/RESUME.md`.

- **NOAA's chart server fails about 1 request in 20** (measured 2026-11-05:
  4 of 72 tiles at 6 in flight came back as an HTML "ArcGIS Web Adaptor:
  Could not access any server machines" page with status 200 and no CORS
  header, which the browser reports as a failed fetch). MapLibre does not
  retry, so the chart shows an occasional missing tile until the map moves.
  Not fixed: a retrying `addProtocol` loader in `basemaps.js` (like `reproj`)
  is the obvious route; a proxy would cost droplet disk and gunicorn time.
- **Overlay regrouping + fixed-scale slope shipped** (2026-11-01, dfe53d2; R2
  synced, not yet drawn in a browser on prod). The old `sp_slope` archive stays
  on R2 until Hig says to remove it. Coverage stops at the Alaska border, so the
  Canadian side of St. Elias is blank until coverage grows.
- **Required strength from topography (Scoops3D rebuild), exploratory,
  committed 2026-11-14 with its overlay row switched off (`CREQ_ACTIVE` in
  `map.js`; Hig: fine for it to be public, harmonise when work resumes)** (2026-11-01..05): `tools/terrain/scoops/` plus a dev-only
  overlay row `sp-creq-20-200`. State, results and held decisions:
  [tools/terrain/scoops/README.md](../tools/terrain/scoops/README.md). **No
  comparison with landslide mapping until Hig lifts that.**
- **Grand Plateau 2026 preliminary DSM is on prod, gated** (2026-11-05,
  64d2006): `grand_plateau_sfm_2026_prelim` from the drop `z2udka3Z6dGU`,
  z9-17, pyramids on the droplet only, no COG there (dead download link,
  accepted). Hig looked at it after the deploy: "this looks good"
  (2026-11-05). Nothing further is owed on it. The final
  NPS release (winter 2026-27) is to be a new public entry, with this one
  retired and its 1 GB removed from the droplet. Same day (c309b1c): the
  `_slope` pyramids of gated surveys had been open to anonymous requests;
  now gated. Details: the last entry of `tools/lidar/RESUME.md`.
- **Droplet disk**: 12 GB free of 78 (85%) on 2026-11-14, after a Docker
  build-cache prune (3.5 GB) and moving five tile trees to R2 (2.3 GB).
  What is left: the monitoring stack's Timescale database 29 GB (the other
  repo), Docker images 18.5 GB, `/var/log/journal` 2.9 GB (a vacuum to
  500 MB freed nothing: the files are active, not archived), and this site's
  `data/` ~10 GB: gated lidar 4.8, field photos 1.5, planet_stories 0.7,
  quantities 0.5, glaciers 0.5, trace_tiles 0.4, opera cache 0.3,
  permafrost 0.25.
- **Leftovers of the tile move**: the `/tiles/susc|coherence|iceboost|itslive|hugonnet/`
  routes and views in `landslidescience/urls.py` serve nothing now and can
  go; so can the `CREATE` branches in `terra_draw.js` / `_polygon_map.html`
  (the retired `/manage/new/` page). The dev trees under `data/*_tiles/`
  are the only tile-tree copies left (the archives on R2 hold the same bytes).
- **Shaded relief / ArcticDEM follow-ons, deferred** (not to start
  unprompted): bake the 2 m ArcticDEM ourselves; deeper Mapterhorn zooms
  outside Alaska; ArcticDEM through the tile Worker. The PGC service gives
  flat grey, blocky shading at low zoom (z5): that is display quality, not
  speed. Hig accepted it.

- **Tatonduk River C (prod id 1575) is hidden from the map** because its
  class is a bare `Catastrophic`: full size, 2018, creep not evaluated.
  That label is not in map.js `CLASS_ORDER`. The fix for this record is
  to tick *creep evaluated* in prod by hand. **Tatonduk River B 2024**
  has a `body` polygon on a catastrophic record and should be `source`.
  Still undecided (Hig deferred it): whether the rule should return
  NULL/magenta instead of bare `Catastrophic` (and `Slow Cryptic`), or
  map.js should get a fallback for unknown labels.
- **Review history loose ends**: floating panels covering the map are
  ignored; reviewer and since are not in the URL; there is no pause
  switch. ~40 of Hig's dev review rows were deleted by a test cleanup on
  2026-10-26 (no backup); dev history restarts from then.

- **`/lidar/` loose ends from 2026-10-19.** (1) Switching surveys
  mid-session can still drop the camera: every terrain rebuild resets the
  centre elevation and only a link's camera is held. (2) The DemShade
  package's `trackTerrainCenter` `relativeThreshold` reads
  `getFreeCameraOptions`, absent in MapLibre 5.24, so it is stuck at its
  0.5 m fallback. (3) The package's URL-keyed tile cache hands a second map
  a buffer already transferred to the first map's worker — worked around in
  the export with `&export=1`; worth raising with the package owner (shared
  with trailgeek). (4) Export: no legend, HTML markers (A/A′, go-to ring)
  missing, scale bar off by default when pitched; the export engine's
  polling-for-done change was not re-tested on `/inventory/`'s export.

- **A corrected online copy of the Portage drop.** The time-corrected,
  stamped frames exist only on Nunatak. Hig's call (2026-10-06): a second
  drop holding them (pushed with rclone, rows via `drops_reconcile`) is the
  right shape, and it is **on hold**. Viewing stays login-gated, decided the
  same day.

- **The generalised analysis tool, phase 3 — parked 2026-10-11 to move to
  another topic.** Phases 1–2 of [ANALYSIS_TOOL_PLAN.md](../ANALYSIS_TOOL_PLAN.md)
  were built, reviewed in four passes and deployed 2026-10-10; the painting
  brush (phase 3's first piece) deployed 2026-10-11. **The agreed next
  step is the sample shapes**: a landslide's own polygon as the sample
  first ("what does this slide's ground look like in these axes?" — the
  cheapest and most useful), then the current view (which folds the OPERA
  panel in as "sample = view"), then a drawn polygon; it means generalising
  `LSSample` from a circle to a shape, a polygon form of
  `api/analysis/sample/` (shapely is in the container), the 60 m reader
  testing inside-polygon, and a URL form per shape. After that: value
  archives for coherence / ITS_LIVE (which would let them paint), dh/dt +
  IceBoost warped onto one grid, and two findings parked on the way — the
  MAAT/MAGT ramp is white at 0 °C (invisible over the Blank White basemap;
  RdYlBu would need four archives re-baked) and aspect below 1° of slope
  piles up at 45° multiples (HAZARDS §Raster sources).
- **Rule-based derivation from mirrored inventories.** Promotion today seeds
  provenance and two fields, then the editor types the rest. `external.py`'s
  docstring anticipates a `derive` step with per-field rules (how to read
  their movement category, their size class, their timing). Bulk adoption is
  deliberately absent and should stay a decision, not a default.
- **Induction Phase 2–3** (supersede/merge UI for `block`/`review`
  collisions) — see *Induction safety & collision detection*.
- **Transient-overlay Escape stack.** The photo lightbox and the draw-choice
  menu still keep their own Escape listeners outside `ls_tools.js`; they are
  modal overlays rather than tools, and want a small stack of their own.
- **`/lidar/` now loads `ls_tools.js`** (2026-10-11, for the landform trace
  tool — its first pointer tool; tool group top-right). The profile and the
  Alt-click probe are the obvious next tools there; they register with the
  same dispatcher rather than growing a policy of their own.
- **Landform traces, deployed 2026-10-11** (with the group profile and the
  shared profile chart; first group on prod: Moosehorn-Seldovia moraines,
  15 traces copied from dev): the
  scarp table generalised to every traced line (`kind`: scarp, moraine
  crest, trimline, strandline, lineament; `traced_on`; named groups, one
  per trace, with a GeoJSON download each), traced on `/lidar/`.
  First use: Pleistocene moraines. See *Landform traces* below. Open: a
  profile along a trace; `/glaciers/` reading the traces as a reference
  layer; whether tracing other kinds on `/inventory/` is wanted.
- **Self-host the NRCan InSAR mosaics — deferred (Hig, 2026-10-29).**
  The NRCan overlay shows only the ascending mosaic, pre-coloured, from
  their WMS. Hig wants the data hosted here eventually; the plan is in
  *NRCan InSAR velocity overlay* below. Not started.
- **trailgeek.org** is a sister site that copies four of this repo's files
  by a pinned sync and reads the lidar catalogue; its own trace editor and
  3D are richer than ours. Its open ask of this repo: adopt `dem_stack.js`
  in `/lidar/`. Relationship and rules: WORKSTREAMS.md *Sister sites*.

## Known bugs left alone

**Two pre-existing bugs found 2026-09-24 and deliberately left alone**, so
nobody rediscovers them as regressions:

- The wiper's second map adds `survey-circles-label` and `insar-click-label`
  without a style `glyphs` property, logging two console errors whenever the
  wiper is enabled.
- Double-clicking a landslide emits `landslide_open` three times (the
  default-view path re-renders the panel). Analytics counts are inflated for
  double-clicked records.
