"""Per-landslide InSAR kinematic-element pages.

One page per landslide, linked from the map's detail panel, showing the rigid
kinematic elements resolved from OPERA DISP-S1 point time series: the fitted
rotation, its gravitational consistency, and the rotational-velocity time
series Omega(t). Records are produced offline by tools/insar_kinematics.py
into data/kinematics/<landslide_id>.json plus sibling PNGs.

DEV-ONLY SURFACE, gated the same way /glaciers/pairs/ is: the views 404
unless data/kinematics/ exists on disk. data/ is volume-mounted and shipped
selectively by rsync, so production simply has no such directory and the
routes stay dark until the method is settled. Nothing here is baked into the
image beyond the (inert) code.

Each element also serialises as GeoJSON (elements.geojson) so the same
polygons and properties can be pulled straight into QGIS — that is the
working format for the area-search work, not an afterthought.
"""
import json
import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

KIN_DIR = Path(settings.BASE_DIR) / 'data' / 'kinematics'
# Figures are named by the generator as <id>_d<k>_<kind>.png. Serving is
# restricted to that shape AND to the requested landslide's own id, so the
# route cannot be walked into the rest of data/.
_FIG_RE = re.compile(r'^\d+_d\d+_(fit|omega)\.png$')


def enabled():
    return KIN_DIR.is_dir()


def record(landslide_id):
    """Parsed record for a landslide, or None if there is no analysis."""
    if not enabled():
        return None
    p = KIN_DIR / f'{int(landslide_id)}.json'
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return None


def has_record(landslide_id):
    return record(landslide_id) is not None


def _summary(rec):
    """Page-level roll-up: the honest headline is about TIME-VARIATION.

    A single period-average Omega is a poor summary when the rate changes
    sign inside the record, so the summary reports the peak resolved
    excursion alongside the average rather than the average alone.
    """
    out = []
    for d in rec.get('domains', []):
        ser = d.get('omega_series') or []
        peak = None
        for s in ser:
            if s.get('se') and abs(s['omega']) / s['se'] >= 2:
                if peak is None or abs(s['omega']) > abs(peak['omega']):
                    peak = s
        out.append({'k': d['k'], 'peak': peak, 'n_windows': len(ser),
                    'n_two_track': sum(1 for s in ser if '+' in (s.get('tracks') or ''))})
    return out


@require_safe
def page(request, landslide_id):
    rec = record(landslide_id)
    if rec is None:
        raise Http404('no kinematic analysis for this landslide')
    from . import views as inv_views
    name = None
    try:
        conn = inv_views._get_conn()
        try:
            cur = conn.cursor()
            cur.execute('SELECT unique_name FROM landslides WHERE id = %s', (int(landslide_id),))
            row = cur.fetchone()
            conn.rollback()
            name = row[0] if row else None
        finally:
            inv_views._put_conn(conn)
    except Exception:
        name = None
    for d in rec.get('domains', []):
        # Pre-format for the template: Django templates cannot index dicts by
        # a variable key, and per-track series are keyed by direction.
        d['per_track_rows'] = [
            {'track': k, 'n': len(v)}
            for k, v in sorted((d.get('omega_per_track') or {}).items())]
    return render(request, 'inventory/kinematics.html', {
        'rec': rec,
        'landslide_id': int(landslide_id),
        'name': name,
        'slug': inv_views._slug_for_id(int(landslide_id)),
        'summary': _summary(rec),
    })


@require_safe
def figure(request, landslide_id, name):
    if not enabled() or not _FIG_RE.match(name):
        raise Http404
    if not name.startswith(f'{int(landslide_id)}_'):
        raise Http404
    p = KIN_DIR / name
    if not p.is_file():
        raise Http404
    resp = FileResponse(p.open('rb'), content_type='image/png')
    resp['Cache-Control'] = 'no-cache'
    return resp


@require_safe
def elements_geojson(request, landslide_id):
    """Kinematic elements as GeoJSON — the GIS hand-off format."""
    rec = record(landslide_id)
    if rec is None:
        raise Http404
    feats = []
    for d in rec.get('domains', []):
        props = {k: v for k, v in d.items()
                 if k not in ('polygon', 'omega_series', 'omega_core',
                              'omega_per_track', 'figs', 'per_track_rows')}
        props['landslide_id'] = rec['landslide_id']
        props['site'] = rec.get('site')
        cw = props.pop('common_window', None) or {}
        for k, v in cw.items():
            props[f'common_window_{k}'] = v
        feats.append({'type': 'Feature',
                      'geometry': {'type': 'Polygon', 'coordinates': [d['polygon']]},
                      'properties': props})
    body = json.dumps({'type': 'FeatureCollection', 'features': feats}, default=float)
    resp = HttpResponse(body, content_type='application/geo+json')
    resp['Content-Disposition'] = (
        f'attachment; filename="kinematic_elements_{rec["landslide_id"]}.geojson"')
    return resp
