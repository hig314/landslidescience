# Soundings — sparse lake and nearshore depths

Where scattered depth measurements live, how one gets in, and how the
composites use them. Started 2026-10-12 with Nick Riordan's 2018 Kachemak Bay
State Park lake soundings. Schema and reasoning:
`inventory/management/commands/migrate_soundings.py`.

## The shape

| what | where |
|---|---|
| untouched originals | `/Volumes/Nunatak/bathy_src/<slug>/raw/` — never edited |
| per-source ingest (judgement) | `tools/soundings/ingest_<slug>.py`, run on the Mac |
| package (what the ingest wrote) | `data/soundings/<slug>/` — `survey.json`, `points.csv`, optional `contours.geojson` |
| the record | PostGIS: `sounding_surveys`, `sounding_levels`, `soundings`, `sounding_contours`, view `soundings_z` |
| composite input | `manage.py export_soundings` → a dated CSV, copied to `$LIDAR_BUILD/obs/soundings/`; a recipe's `fill.points` names it |
| on the site | `/inventory/api/soundings/`; layer in `/lidar/` (Soundings) and `/inventory/` (Reference layers → Lake soundings); dots with ±σ on the inventory ∿ profile |

**Depth is what was measured; elevation is derived** (`z = level − depth`),
because the level is often unknown for the survey day and a better one later
should move every point in the lake without touching the measurements.
`sigma_z = sqrt(sigma_depth² + sigma_level²)`.

**Nothing is deleted for being wrong**: a bad row is loaded with
`flag = 'rejected'` and a reason (Upper Hazel "854"); `suspect` keeps it with
sigma doubled in the export. Rows that were never soundings (route waypoints,
GPS waypoint numbers) are listed in the survey's `ingest` block, not loaded.

**Lower bounds** (`depth_bound = 'min'`, 2026-10-13): a lead line that ran
out (">120", "MAX") records that the bed is at LEAST `depth_m` down. Drawn on
the colour scale at that depth with a heavy dark ring and "≥"; on the profile a
separate black series with no error bar; exported in the `bound` column, which
the composite fill keeps OUT of its exact points (honouring it as an inequality
is not built yet). Re-run `migrate_soundings` after deploying it: it adds the
column and re-creates `soundings_z`.

**Gating**: `public = false` surveys are served only to accounts that may
see restricted data (`auth.can_view_restricted`), applied to the rows.

## Adding a survey

1. Put the originals somewhere (the inbox is fine) and write
   `tools/soundings/ingest_<slug>.py` on the pattern of
   `ingest_riordan_2018.py`: read the originals as delivered, convert units
   (keep the original value), assign each point a level (by RUN of points,
   not nearest label — nearest put China Poot's south arm on the next lake),
   flag what is wrong, record everything in `ingest`, copy the originals to
   `bathy_src/<slug>/raw/`.
2. `docker exec landslidescience-web-1 python manage.py load_soundings data/soundings/<slug>/`
   (`--dry-run` first; `--replace` to reload; it refuses to clobber rows
   edited after the last load).
3. For a composite: `manage.py export_soundings --out data/soundings/export/soundings_YYYYMMDD.csv`,
   copy to `$LIDAR_BUILD/obs/soundings/`, name it in the recipe.

Production: `migrate_soundings` once, then the package dirs rsynced to the
droplet's `data/soundings/` and `load_soundings` run there (dev and prod are
separate databases, as for every PostGIS command).

## Dense surveys: the NOS digital soundings and the CRM centres (2026-11-01)

Hig, reviewing the Taylor Bay bathymetry: "expose the data you're using to
build the bathymetry as sounding points, like we do with lake soundings — I'd
like to understand where the inference is coming from." The topobathy fill
infers the sea bed between the modern surveys from the NOS hydrographic
surveys' digital soundings (38 surveys, 1915–84, 325,932 points of record)
and from the CRM's 500 m cell centres (21,710, sigma 10, a compilation and
never an observation), so they are sounding surveys here too:
`ingest_nos_geodas.py` writes one package per NOS survey (`nos_h05101`…) and
`crm_southak_500m`, each point with the sigma the fill gave it (lead line 2 m,
echo sounder 0.8 m, plus position × the local bed gradient; a lone line takes
the 10 m cap), every point hanging from one level, `mllw` = NAVD88 −1.38 m at
Seldovia. Superseded rows (`active = 0`) are not loaded.

They are **`dense: true`** (`sounding_surveys.dense`, added by re-running
`migrate_soundings`): the API serves a dense survey's points only inside a
bbox (`?dense=1&bbox=`, capped at 80,000 a response with `truncated`), and
`soundings.js` fetches them per view from zoom 12.5, merging them into the
same layer as the lake soundings — same dots, popup, profile and 3D markers.
A plain request still returns the lake surveys whole, with the dense surveys
listed and pointless.

**Exports after this:** `export_soundings` exports every survey, so an export
made after these are loaded carries the NOS and CRM points too. A recipe then
names that ONE file in `fill.points`, not the export plus the separate
`nos_*.csv` / `crm_*.csv` files (the 2026-10-31 recipes name the four files;
they predate the load) — naming both would count every point twice. Or
restrict the export with `--survey` to the lake surveys.

## ADF&G hand-drawn contour maps (planned, table ready)

Fish and Game lake maps record old surveys as hand-drawn depth contours,
usually without the sounding positions. They go in `sounding_contours`: one
line per contour, its depth (original unit kept), the level it hangs from
(the map's stated surface, else the lidar water surface, with a sigma for
the date gap), and a generous sigma — the line is an interpretation between
soundings nobody located, and the map's georeferencing adds metres of
position error on a steep shore. Suggested start: sigma 2 m, or a quarter of
the contour interval, whichever is larger; a map whose shoreline disagrees
with the lidar shore by tens of metres should be warped to it first (that
disagreement is the georeferencing error, measured).

The export samples each contour every `--contour-step` m (default 20) with
the contour's sigma, so the fill's smoothing spline treats a contour as a
string of weak soundings. Where a lake has both, the real soundings (sigma
~1 m) outweigh the contour samples. To ingest: georeference the scan (QGIS),
digitise the contours to a GeoPackage with a `depth` field, then an ingest
script on the pattern above writes `contours.geojson`.

## Next (not built)

- Editing in the browser: flag / unflag, sigma, notes, and a new sounding by
  clicking the map (the scarp tracer's pattern). Write endpoints would go in
  `inventory/soundings.py`; `load_soundings --replace` already refuses to
  clobber rows edited after the last load.
- The /lidar/ group-profile and the inventory profile could also draw contour
  crossings.

**Positions of the 1915–31 surveys are shifted** (2026-11-01): NCEI's
conversion of their Early Alaska Datums to NAD83 is some 400 m E / 320 m N
short in Kachemak Bay (casts on land). `tools/lidar/topobathy/nos_offsets.py`
measures one translation per survey (land above MHHW, and depth matching
against 1977–84 where they overlap); the same JSON moves the composite's
points and, through `ingest_nos_geodas.py --offsets`, these packages, whose
title, notes and `ingest.position_shift` record it. BAY_SOURCES.md §8.2;
/lidar/methods/ §11 explains it to readers.
