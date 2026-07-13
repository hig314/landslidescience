"""Load/replace a region subset's defining polygon and recompute membership.

Reads any pyogrio-readable vector file (gpkg/geojson/shp/kml), optionally
filtered to one feature by an attribute value, unions the geometry, and
writes it as the subset's region_geom (kind flips to 'region'). Membership
is then recomputed — centroid-in-polygon, see inventory/regions.py — and the
full gained/lost diff is printed: **that diff is the record of what changed**
when a hand-curated subset is converted to a spatial one. Run --dry-run
first and keep the output.

  python manage.py load_region_geometry alaska data/regions_seed/region_candidates.gpkg \
      --layer alaska_tiger2024_3nm [--dry-run]
  python manage.py load_region_geometry kim data/regions_seed/region_candidates.gpkg \
      --layer paper_subareas --feature Name=Kim_all [--dry-run]
  python manage.py load_region_geometry higman ... --create --name Higman

Guards: frozen (snapshotted) subsets are refused; geometry must be valid
polygons in EPSG:4326; each polygon PART must span < 180° of longitude —
an unsplit antimeridian ring silently becomes a world-wrapping area. (The
whole MultiPolygon may of course span the dateline via split parts, as the
TIGER Alaska boundary does.)
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Load/replace a region subset's polygon and recompute its membership."

    def add_arguments(self, parser):
        parser.add_argument('slug')
        parser.add_argument('path')
        parser.add_argument('--layer', help='Layer name (multi-layer sources like gpkg).')
        parser.add_argument('--feature', help='ATTR=VALUE filter selecting feature(s).')
        parser.add_argument('--create', action='store_true',
                            help='Create the subset (kind=region) if it does not exist.')
        parser.add_argument('--name', help='Display name when creating (default: slug).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report the membership diff, then ROLLBACK.')

    # -- geometry loading ----------------------------------------------------
    def _load_geom_wkb(self, path, layer, feature_filter):
        from pyogrio.raw import read as pyogrio_read
        from shapely import wkb as _wkb
        from shapely.geometry import MultiPolygon
        from shapely.ops import unary_union

        kwargs = {'read_geometry': True, 'force_2d': True}
        if layer:
            kwargs['layer'] = layer
        meta, _fids, geometry, field_data = pyogrio_read(path, **kwargs)
        crs_label = (meta.get('crs') or '').lower()
        if crs_label and 'epsg:4326' not in crs_label:
            raise CommandError(f'Source CRS is {meta.get("crs")!r}; reproject to EPSG:4326.')
        fields = list(meta.get('fields'))

        keep = range(len(geometry))
        if feature_filter:
            if '=' not in feature_filter:
                raise CommandError('--feature expects ATTR=VALUE')
            attr, val = feature_filter.split('=', 1)
            try:
                fi = fields.index(attr)
            except ValueError:
                raise CommandError(f'No attribute {attr!r}; source has {fields}')
            keep = [i for i in range(len(geometry))
                    if str(field_data[fi][i]) == val]
            if not keep:
                raise CommandError(f'No feature matches {feature_filter!r}.')

        shapes = [_wkb.loads(bytes(geometry[i])) for i in keep
                  if geometry[i] is not None and len(geometry[i])]
        if not shapes:
            raise CommandError('Selected feature(s) carry no geometry.')
        merged = unary_union(shapes)
        if merged.geom_type == 'Polygon':
            merged = MultiPolygon([merged])
        if merged.geom_type != 'MultiPolygon' or merged.is_empty:
            raise CommandError(f'Union produced {merged.geom_type}; need (Multi)Polygon.')

        # Per-PART antimeridian guard (the whole multipolygon may legally
        # span the dateline through split parts).
        for part in merged.geoms:
            minx, _, maxx, _ = part.bounds
            if maxx - minx >= 180:
                raise CommandError(
                    f'A polygon part spans {maxx - minx:.0f}° of longitude — '
                    'an unsplit antimeridian ring. Split the polygon at ±180° '
                    'and re-export.')
        return merged.wkb, len(shapes)

    # -- command body ----------------------------------------------------------
    def handle(self, *args, **opts):
        from inventory import regions
        from inventory.views import _get_conn, _put_conn, _invalidate

        wkb, n_src = self._load_geom_wkb(opts['path'], opts['layer'], opts['feature'])
        self.stdout.write(f'geometry loaded ({n_src} source feature(s)).')

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, kind FROM subsets WHERE slug = %s", (opts['slug'],))
            row = cur.fetchone()
            if row is None:
                if not opts['create']:
                    raise CommandError(f'No subset {opts["slug"]!r} (use --create to add it).')
                cur.execute("""INSERT INTO subsets (slug, name, kind)
                               VALUES (%s, %s, 'region') RETURNING id""",
                            (opts['slug'], opts['name'] or opts['slug'].title()))
                subset_id = cur.fetchone()[0]
                self.stdout.write(f'created region subset {opts["slug"]!r} (id {subset_id}).')
            else:
                subset_id, kind = row
                if subset_id in regions.frozen_subset_ids(cur):
                    raise CommandError(f'{opts["slug"]!r} is frozen by a snapshot — refused.')
                if kind != 'region':
                    self.stdout.write(f'converting {opts["slug"]!r} from kind={kind!r} to region.')

            cur.execute("""
                UPDATE subsets
                SET kind = 'region',
                    region_geom = ST_Multi(ST_CollectionExtract(
                        ST_MakeValid(ST_GeomFromWKB(%s, 4326)), 3)),
                    region_updated_at = now()
                WHERE id = %s
            """, (wkb, subset_id))
            cur.execute("""SELECT round((ST_Area(ST_Transform(region_geom, 3338))/1e6)::numeric),
                                  ST_NPoints(region_geom) FROM subsets WHERE id = %s""",
                        (subset_id,))
            area_km2, npoints = cur.fetchone()
            self.stdout.write(f'region_geom set: {npoints} vertices, ~{area_km2:,.0f} km².')

            diff = regions.recompute_subset(cur, subset_id)
            self.stdout.write(f"\nmembership diff — +{len(diff['added'])} / "
                              f"-{len(diff['removed'])}:")
            for i, name in diff['added']:
                self.stdout.write(f'  + #{i} {name}')
            for i, name in diff['removed']:
                self.stdout.write(f'  - #{i} {name}')
            if not diff['added'] and not diff['removed']:
                self.stdout.write('  (no changes)')

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
        finally:
            _put_conn(conn)
        if not opts['dry_run']:
            _invalidate('features', 'home_counts', 'timed_events',
                        'timeline_events', 'slug_map', 'slug_for_id')
            self.stdout.write('Restart the web container so cached subset counts refresh.')
