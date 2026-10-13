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
