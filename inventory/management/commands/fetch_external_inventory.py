"""Mirror a third-party landslide inventory into its own PostGIS table.

    python manage.py fetch_external_inventory --list
    python manage.py fetch_external_inventory ak_dggs_dds23
    python manage.py fetch_external_inventory canada_pcld --dry-run
    python manage.py fetch_external_inventory --all

Each source in `inventory/external.py`'s registry gets one table per layer,
whose columns are the columns the publisher ships. Nothing is translated on
the way in -- see that module's docstring for why. Turning one of these rows
into a row of ours is a separate, deliberate act.

IDEMPOTENT, AND A REFETCH IS A REPLACE
--------------------------------------
Re-running replaces a layer's rows inside one transaction. Not an upsert by
their id: these publishers revise, retire and renumber entries between
releases (the Canadian database carries a "modified in version" column for
exactly that reason), so an upsert would silently accumulate rows that the
current release no longer contains, and the mirror would stop being a mirror.
Replacing means the table always equals one release. The cost is that a fetch
which dies halfway rolls back to the previous release rather than leaving a
half-written one, which is the right failure.

WHY THE COUNT IS CHECKED
------------------------
A paged fetch that stops early looks exactly like a small dataset. The ArcGIS
path asks the service for its own record count first and refuses to write a
layer whose fetched total does not match, because a mirror that is quietly
two-thirds complete is worse than no mirror: nothing downstream can tell.
"""
from django.core.management.base import BaseCommand, CommandError

from inventory import external


class Command(BaseCommand):
    help = 'Mirror a third-party landslide inventory into its own table'

    def add_arguments(self, p):
        p.add_argument('source', nargs='?', help='registry id')
        p.add_argument('--all', action='store_true', help='every source')
        p.add_argument('--list', action='store_true', help='show the registry')
        p.add_argument('--layer', help='just this layer')
        p.add_argument('--dry-run', action='store_true',
                       help='fetch and report, write nothing')

    def handle(self, *a, **o):
        if o['list']:
            for sid, s in external.SOURCES.items():
                self.stdout.write(f"{sid}\n    {s['title']}\n    {s['url']}")
                for layer, L in s['fetch']['layers'].items():
                    self.stdout.write(f"      {layer:10s} {L['geom']:16s} {L['label']}")
            return

        ids = list(external.SOURCES) if o['all'] else ([o['source']] if o['source'] else [])
        if not ids:
            raise CommandError('give a source id, --all, or --list')
        for sid in ids:
            if sid not in external.SOURCES:
                raise CommandError(f'unknown source {sid!r}')
            self._one(sid, o)

    # ------------------------------------------------------------------
    def _one(self, sid, o):
        s = external.SOURCES[sid]
        self.stdout.write(self.style.MIGRATE_HEADING(f'== {sid}: {s["title"]}'))
        layers = s['fetch']['layers']
        only = o.get('layer')
        if only and only not in layers:
            raise CommandError(f'{sid} has no layer {only!r}')
        for layer in ([only] if only else list(layers)):
            if s['fetch']['kind'] == 'arcgis':
                self._arcgis(sid, s, layer, o)
            else:
                self._csv(sid, s, layer, o)

    def _arcgis(self, sid, s, layer, o):
        L = s['fetch']['layers'][layer]
        svc = s['fetch']['service']
        self.stdout.write(f'  {layer}: reading schema')
        cols, aliases, domains, meta = external.arcgis_fields(svc, L['id'])
        # Keep the service's ALIAS as the stored label. The column name is
        # already their raw field name; the alias ("movement_category" for
        # "mvmt_categ") is the phrase their own app shows a reader, so it is
        # the one our popup should show too.
        cols = [(c, t, aliases.get(c, orig)) for c, t, orig in cols]
        self.stdout.write(f'    {len(cols)} fields declared')
        self.stdout.write(f'  {layer}: fetching features')
        feats, expected = external.arcgis_features(svc, L['id'])
        self.stdout.write(f'    fetched {len(feats)} (service reports {expected})')
        if expected is not None and len(feats) != expected:
            raise CommandError(
                f'{sid}/{layer}: fetched {len(feats)} of {expected} -- refusing to '
                f'write a partial mirror. Re-run; if it persists the service is '
                f'paging differently than assumed.')
        rows = []
        for f in feats:
            props = {external._col(k): v for k, v in (f.get('properties') or {}).items()}
            rows.append((f.get('geometry'), props))
        self._write(sid, layer, L['geom'], cols, rows, s, o, domains=domains,
                    aliases=aliases)

    def _csv(self, sid, s, layer, o):
        L = s['fetch']['layers'][layer]
        self.stdout.write(f'  {layer}: downloading csv')
        header, raw = external.csv_rows(L['url'])
        self.stdout.write(f'    {len(header)} columns, {len(raw)} rows')
        cols = [(external._col(h), 'text', h) for h in header]
        lon_i = header.index(L['lon_field'])
        lat_i = header.index(L['lat_field'])
        rows, skipped = [], 0
        for r in raw:
            if len(r) < len(header):
                r = r + [''] * (len(header) - len(r))
            try:
                lon, lat = float(r[lon_i]), float(r[lat_i])
            except (ValueError, IndexError):
                skipped += 1
                continue
            geom = {'type': 'Point', 'coordinates': [lon, lat]}
            rows.append((geom, {c[0]: (r[i] or None) for i, c in enumerate(cols)}))
        if skipped:
            self.stdout.write(self.style.WARNING(
                f'    {skipped} row(s) had no usable coordinates and were skipped'))
        self._write(sid, layer, L['geom'], cols, rows, s, o)

    # ------------------------------------------------------------------
    def _write(self, sid, layer, geom_type, cols, rows, s, o, domains=None,
               aliases=None):
        if o['dry_run']:
            self.stdout.write(self.style.WARNING(
                f'    dry run: would write {len(rows)} rows to '
                f'{external.table_name(sid, layer)}'))
            return
        from inventory.views import _get_conn, _put_conn
        import json as _json
        id_col = external._col(s['id_field'])
        conn = _get_conn()
        try:
            cur = conn.cursor()
            t, added = external.ensure_table(cur, sid, layer, cols, geom_type)
            if added:
                self.stdout.write(f'    added columns: {", ".join(added)}')
            cur.execute(f'DELETE FROM {t}')
            names = [c[0] for c in cols]
            types = {c[0]: c[1] for c in cols}
            collist = ', '.join(['ext_id', 'geom'] + names)
            # ArcGIS declares a field as a Date and then ships it in GeoJSON as
            # EPOCH MILLISECONDS, so a timestamptz column gets handed a bigint
            # and the insert fails. The declared type is the honest one -- the
            # integer is transport, not meaning -- so convert on the way in
            # rather than storing dates as numbers nobody will think to decode.
            def _ph(c):
                return ('to_timestamp(%s / 1000.0)'
                        if types.get(c) == 'timestamptz' else '%s')
            ph = ', '.join(['%s', 'ST_Multi(ST_GeomFromGeoJSON(%s))'
                            if geom_type.startswith('MULTI')
                            else 'ST_GeomFromGeoJSON(%s)'] +
                           [_ph(c) for c in names])
            sql = f'INSERT INTO {t} ({collist}) VALUES ({ph})'

            def _val(c, v):
                if types.get(c) != 'timestamptz' or v is None or v == '':
                    return v
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None       # unparseable date: null beats a wrong one

            n = 0
            for geom, props in rows:
                if not geom:
                    continue
                vals = [props.get(id_col), _json.dumps(geom)] + \
                       [_val(c, props.get(c)) for c in names]
                cur.execute(sql, vals)
                n += 1
            cur.execute(f'COMMENT ON TABLE {t} IS %s',
                        (f"{s['title']} -- mirrored verbatim. {s['citation']} "
                         f"{s['url']} Licence: {s['licence']}",))
            conn.commit()
            cur.execute(f'SELECT count(*) FROM {t}')
            total = cur.fetchone()[0]
        finally:
            _put_conn(conn)
        self.stdout.write(self.style.SUCCESS(
            f'    {t}: {total} rows ({n} written, {len(rows) - n} had no geometry)'))
