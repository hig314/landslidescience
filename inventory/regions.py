"""Spatial region subsets — polygon-defined, automatically maintained.

A subset with kind='region' carries a defining polygon (subsets.region_geom,
MultiPolygon 4326; schema via migrate_regions). Membership is computed —
the record's stored primary-polygon centroid (centroid_lon/lat, maintained
by the rule cascade) falls inside the polygon (ST_Covers, so a boundary
centroid counts in) — and stays MATERIALIZED in landslide_subsets, so every
read path (map ?subset= filter, counts, exports, snapshots, facets) is
unchanged. This module is the only writer for region-kind memberships.

Guards, everywhere:
  - frozen subsets (referenced by snapshots.subset_id) are never touched —
    the same rule that protects alaska-2025's hand membership protects a
    snapshotted region from drifting after publication;
  - deprecated landslides keep their historical memberships (no-op);
  - a record with no centroid has no region memberships.

Refresh triggers: per-record via the rule cascade (apply_rules_for_landslide
calls refresh_for_landslide — regions are derived state and ride the same
choke point every centroid change flows through); per-subset via
recompute_subset when a polygon is loaded/replaced (load_region_geometry);
globally via the refresh_region_subsets command.
"""
import logging

log = logging.getLogger(__name__)


def frozen_subset_ids(cur):
    cur.execute("SELECT DISTINCT subset_id FROM snapshots WHERE subset_id IS NOT NULL")
    return {r[0] for r in cur.fetchall()}


def refresh_for_landslide(cur, ls_id):
    """Re-evaluate ONE landslide's membership in every unfrozen region
    subset. Caller owns the transaction. Returns True if anything changed."""
    cur.execute("SELECT deprecated_at FROM landslides WHERE id = %s", (ls_id,))
    row = cur.fetchone()
    if not row or row[0] is not None:
        return False   # missing or deprecated — history stays put

    cur.execute("""
        SELECT s.id FROM subsets s, landslides l
        WHERE l.id = %s AND s.kind = 'region' AND s.region_geom IS NOT NULL
          AND l.centroid_lon IS NOT NULL AND l.centroid_lat IS NOT NULL
          AND ST_Covers(s.region_geom,
                        ST_SetSRID(ST_MakePoint(l.centroid_lon, l.centroid_lat), 4326))
    """, (ls_id,))
    desired = {r[0] for r in cur.fetchall()}

    cur.execute("""
        SELECT ls.subset_id FROM landslide_subsets ls
        JOIN subsets s ON s.id = ls.subset_id
        WHERE ls.landslide_id = %s AND s.kind = 'region'
    """, (ls_id,))
    current = {r[0] for r in cur.fetchall()}

    frozen = frozen_subset_ids(cur)
    to_add = desired - current - frozen
    to_remove = current - desired - frozen
    for sid in to_add:
        cur.execute("INSERT INTO landslide_subsets (landslide_id, subset_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING", (ls_id, sid))
    if to_remove:
        cur.execute("DELETE FROM landslide_subsets WHERE landslide_id = %s "
                    "AND subset_id = ANY(%s)", (ls_id, list(to_remove)))
    return bool(to_add or to_remove)


def recompute_subset(cur, subset_id):
    """Full membership rewrite for one region subset (after its polygon is
    loaded/replaced). Caller owns the transaction.

    Returns {'added': [(id, name)...], 'removed': [(id, name)...]} — the
    membership diff, which doubles as the conversion/change record. Raises
    ValueError for a frozen or non-region subset (callers surface the
    message; nothing is written).
    """
    cur.execute("SELECT slug, kind, region_geom IS NULL FROM subsets WHERE id = %s",
                (subset_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f'subset id {subset_id} does not exist')
    slug, kind, geom_null = row
    if kind != 'region':
        raise ValueError(f'subset "{slug}" is kind={kind!r}, not a region')
    if geom_null:
        raise ValueError(f'subset "{slug}" has no region polygon loaded')
    if subset_id in frozen_subset_ids(cur):
        raise ValueError(f'subset "{slug}" is frozen by a snapshot — membership is immutable')

    # Desired: active (non-deprecated) records whose centroid the polygon covers.
    cur.execute("""
        SELECT l.id, l.unique_name FROM landslides l, subsets s
        WHERE s.id = %s AND l.deprecated_at IS NULL
          AND l.centroid_lon IS NOT NULL AND l.centroid_lat IS NOT NULL
          AND ST_Covers(s.region_geom,
                        ST_SetSRID(ST_MakePoint(l.centroid_lon, l.centroid_lat), 4326))
    """, (subset_id,))
    desired = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute("""
        SELECT ls.landslide_id, l.unique_name FROM landslide_subsets ls
        JOIN landslides l ON l.id = ls.landslide_id
        WHERE ls.subset_id = %s
    """, (subset_id,))
    current = {r[0]: r[1] for r in cur.fetchall()}

    added = sorted(((i, n) for i, n in desired.items() if i not in current))
    removed = sorted(((i, n) for i, n in current.items() if i not in desired))
    for i, _n in added:
        cur.execute("INSERT INTO landslide_subsets (landslide_id, subset_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING", (i, subset_id))
    if removed:
        cur.execute("DELETE FROM landslide_subsets WHERE subset_id = %s "
                    "AND landslide_id = ANY(%s)", (subset_id, [i for i, _n in removed]))
    return {'added': added, 'removed': removed}
