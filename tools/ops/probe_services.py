#!/usr/bin/env python3
"""Probe the outside services the site reads live, and record what answers.

The map depends on a dozen servers nobody here runs (NOAA charts, NRCan,
PGC, Mapterhorn, Macrostrat, ASF, GIBS, GeoBC, script CDNs ...). When one is
down the only symptom is a blank layer, which looks like our bug. This asks
each for one representative thing, the way the site does, and checks the
answer is the kind of thing expected: several of these fail with status 200
and an HTML error page (NOAA's "Could not access any server machines"), so
status alone proves nothing.

    python3 tools/ops/probe_services.py                       # print a table
    python3 tools/ops/probe_services.py --out data/service_status.json

With --out it keeps a small state file: the latest result per service, when
each last changed state, and the last 200 state changes. /traffic/services/
renders that file. Exit status 1 if anything is down. Standard library only.

    python3 tools/ops/probe_services.py --basemaps --out data/service_status.json

--basemaps adds one request for every outside URL in the basemap table itself
(inventory/static/inventory/js/basemaps.js: `tiles`, `labelTiles`, `style`),
so a basemap nobody thought to list below is still checked. It exists because
USGS withdrew the service behind "USGS Imagery" and nothing noticed
(2026-11-23). Its rows are named "Basemap: <label>" and are kept in the state
file between the runs that make them.

Cron on the droplet: tools/ops/probe_cron.sh (every 30 minutes), and
tools/ops/probe_daily_cron.sh (once a day, with --basemaps).
Adding a service: one row in PROBES. Keep one request per service per run;
these are other people's servers.
"""
import argparse
import datetime
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

UA = 'landslidescience-service-probe/1 (+https://landslidescience.org)'
TIMEOUT = 25


def tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return z, x, y


def bbox3857(z, x, y):
    w = 20037508.342789244
    s = 2 * w / 2 ** z
    return '%.3f,%.3f,%.3f,%.3f' % (-w + x * s, w - (y + 1) * s, -w + (x + 1) * s, w - y * s)


HOMER = (-151.45, 59.62)        # charted water, lidar, 3DEP
ANCH = (-149.6, 61.2)           # OPERA coverage
STEWART = (-129.99, 55.94)      # BC / NRCan coverage
ZXY = '{z}/{x}/{y}'


def xyz(tpl, where, z):
    z, x, y = tile(where[0], where[1], z)
    return tpl.format(z=z, x=x, y=y, bbox=bbox3857(z, x, y))


# name, what breaks without it, url, expected content-type prefix, min bytes, tries
PROBES = [
    ('NOAA chart display (WMS)', 'NOAA chart basemap',
     xyz('https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/NOAAChartDisplay/MapServer/exts/'
         'MaritimeChartService/WMSServer?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=1,2,3,4,5,6,7'
         '&STYLES=&CRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true&WIDTH=256&HEIGHT=256&BBOX={bbox}',
         HOMER, 10), 'image/', 300, 3),      # fails ~1 request in 20 by itself: 3 tries
    ('NRCan land-deformation WMS', 'NRCan InSAR overlay',
     xyz('https://datacube.services.geo.ca/ows/land-deformation?service=WMS&version=1.3.0&request=GetMap'
         '&layers=mosaic-asc&styles=&crs=EPSG:3857&format=image/png&transparent=true&width=256&height=256'
         '&bbox={bbox}', STEWART, 9), 'image/', 100, 1),
    ('NRCan Toporama WMS', 'US / Canada Topo (Canadian side)',
     xyz('https://maps.geogratis.gc.ca/wms/toporama_en?service=WMS&version=1.1.1&request=GetMap'
         '&layers=WMS-Toporama&styles=&srs=EPSG:3857&format=image/png&transparent=true&width=256&height=256'
         '&bbox={bbox}', STEWART, 11), 'image/', 100, 1),
    ('GeoBC orthophoto WMS', 'BC Orthophoto 1995-2004 (cold tiles)',
     xyz('https://openmaps.gov.bc.ca/imagex/ecw_wms.dll?service=WMS&version=1.1.1&request=GetMap'
         '&layers=bc_bc_bc_xc1m_bcalb_1995_2004&styles=&srs=EPSG:3857&format=image/png&width=256&height=256'
         '&bbox={bbox}', STEWART, 13), 'image/', 100, 1),
    ('PGC ArcticDEM ImageServer', 'ArcticDEM relief, profile, probe',
     'https://di-pgc.img.arcgis.com/arcgis/rest/services/arcticdem_latest/ImageServer?f=json',
     'application/json', 200, 1),
    ('Mapterhorn terrain tiles', '/lidar/ context, contours, relief, profiles',
     xyz('https://tiles.mapterhorn.com/' + ZXY + '.webp', HOMER, 10), 'image/', 500, 1),
    ('Macrostrat carto tiles', 'Bedrock geology overlay',
     xyz('https://tiles.macrostrat.org/carto/' + ZXY + '.png', ANCH, 7), 'image/', 100, 1),
    ('Macrostrat point API', 'Geology click lookup',
     'https://macrostrat.org/api/v2/geologic_units/map?lat=61.20000&lng=-149.60000', 'application/json', 50, 1),
    ('ASF OPERA velocity tiles', 'OPERA InSAR overlays (cold tiles)',
     xyz('https://d3g9emy65n853h.cloudfront.net/main/asc/vel/' + ZXY + '.png', ANCH, 8), 'image/', 100, 1),
    ('NASA GIBS (DIST)', 'Surface disturbance overlay (cold tiles, dates)',
     'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi?SERVICE=WMTS&VERSION=1.0.0'
     '&REQUEST=DescribeDomains&LAYER=OPERA_L3_DIST-ANN-HLS_Color_Index'
     '&TILEMATRIXSET=GoogleMapsCompatible_Level12', '', 100, 1),
    ('Esri World Imagery', 'default imagery basemap',
     xyz('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
         HOMER, 10), 'image/', 500, 1),
    ('USGS National Map topo', 'US / Canada Topo',
     xyz('https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
         HOMER, 10), 'image/', 500, 1),
    ('USGS National Map imagery', 'USGS Imagery basemap',
     xyz('https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}',
         HOMER, 12), 'image/', 500, 1),    # z12: this service has no z9-11 tiles over most of Alaska (2026-11-23)

    ('USDA AHAP ImageServer', 'AHAP 1978-86 basemap',
     xyz('https://apps.geo.fpac.usda.gov/nrcs-imagery/rest/services/ortho_imagery/'
         'ahap_1978_to_1986_150cm_colorbalance/ImageServer/exportImage?bbox={bbox}&bboxSR=3857&imageSR=3857'
         '&size=256,256&format=jpgpng&f=image', HOMER, 11), 'image/', 100, 1),
    ('EOX Sentinel-2 cloudless', 'Sentinel-2 basemap',
     xyz('https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg',
         HOMER, 10), 'image/', 500, 1),
    ('R2 lidar bucket (catalogue of bytes)', 'every public lidar survey, overlay archives',
     'https://lidar.landslidescience.org/overlays/susc_nasa_purple.pmtiles', 'RANGE', 127, 1),
    ('unpkg (maplibre-gl, pmtiles, terra-draw, contour)', 'the map itself',
     'https://unpkg.com/maplibre-gl@5.5.0/dist/maplibre-gl.js', '', 100000, 1),
    ('Uppy CDN', 'photo drop uploader',
     'https://releases.transloadit.com/uppy/v4.13.3/uppy.min.mjs', '', 50000, 1),
    # The site itself is not probed: this runs inside its own container, which
    # cannot reach the droplet's public address, and a host cannot usefully
    # watch itself anyway.
]


BASEMAPS_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
                           'inventory', 'static', 'inventory', 'js', 'basemaps.js')


def basemap_probes(path=BASEMAPS_JS):
    """One PROBES row per outside URL in the basemap table. Read with regular
    expressions: the table is a list of flat object literals with
    single-quoted strings, and check_invariants fails if this finds fewer
    URLs than the file has `https://` tile or style entries."""
    import re
    src = open(path, encoding='utf-8').read()
    rows, label = [], '?'
    for m in re.finditer(r"\b(label|tiles|labelTiles|style):\s*'([^']*)'", src):
        key, val = m.group(1), m.group(2)
        if key == 'label':
            label = val
            continue
        if not val.startswith('http'):
            continue                                    # our own proxy, inline styles
        name = 'Basemap: %s%s' % (label, ' (second layer)' if key == 'labelTiles' else '')
        if key == 'style':
            rows.append((name, 'basemap table', val, 'application/json', 100, 3))
            continue
        # z12, not 10: USGSImageryOnly has no z9-11 tiles over most of Alaska.
        z, x, y = tile(HOMER[0], HOMER[1], 12)
        url = (val.replace('{z}', str(z)).replace('{x}', str(x)).replace('{y}', str(y))
                  .replace('{bbox-epsg-3857}', bbox3857(z, x, y)))
        if '{' in url:
            continue                                    # a template this cannot fill (none today)
        rows.append((name, 'basemap table', url, 'image/', 100, 3))
    return rows


def probe(url, ctype, min_bytes):
    hdrs = {'User-Agent': UA}
    if ctype == 'RANGE':
        hdrs['Range'] = 'bytes=0-126'
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=TIMEOUT) as r:
            body = r.read(2_000_000)
            got = (r.headers.get('Content-Type') or '').lower()
            ms = int((time.time() - t0) * 1000)
            if ctype == 'RANGE':
                if r.status != 206:
                    return False, f'status {r.status} to a ranged request (wanted 206)', ms
                if body[:7] != b'PMTiles':
                    return False, 'ranged read did not return a PMTiles header', ms
                return True, '', ms
            if ctype and not got.startswith(ctype):
                return False, f'answered {got or "no content-type"} ({body[:80]!r})', ms
            if len(body) < min_bytes:
                return False, f'only {len(body)} bytes ({body[:80]!r})', ms
            return True, '', ms
    except urllib.error.HTTPError as e:
        return False, f'HTTP {e.code}', int((time.time() - t0) * 1000)
    except Exception as e:                              # timeouts, DNS, TLS
        return False, f'{type(e).__name__}: {e}'[:160], int((time.time() - t0) * 1000)


def run(extra=()):
    out = []
    for name, used_by, url, ctype, min_bytes, tries in list(PROBES) + list(extra):
        fails = 0
        for _ in range(tries):
            ok, why, ms = probe(url, ctype, min_bytes)
            if ok:
                break
            fails += 1
        out.append({'name': name, 'used_by': used_by, 'ok': ok, 'detail': why,
                    'ms': ms, 'failed_tries': fails, 'tries': tries})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='state file to update (data/service_status.json)')
    ap.add_argument('--basemaps', action='store_true',
                    help='also ask for one tile from every outside URL in basemaps.js (the daily run)')
    a = ap.parse_args()
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    results = run(basemap_probes() if a.basemaps else ())
    for r in results:
        print('%-4s %6d ms  %-48s %s' % ('ok' if r['ok'] else 'DOWN', r['ms'], r['name'],
                                         r['detail'] or ('%d of %d tries failed' % (r['failed_tries'], r['tries'])
                                                         if r['failed_tries'] else '')))
    if a.out:
        try:
            state = json.load(open(a.out))
        except (OSError, ValueError):
            state = {'services': {}, 'changes': []}
        for r in results:
            prev = state['services'].get(r['name'])
            if prev is None or prev.get('ok') != r['ok']:
                r['since'] = now
                if prev is not None:
                    state['changes'].append({'at': now, 'name': r['name'], 'ok': r['ok'], 'detail': r['detail']})
            else:
                r['since'] = prev.get('since', now)
            state['services'][r['name']] = r
        for gone in set(state['services']) - {r['name'] for r in results}:
            if gone.startswith('Basemap: ') and not a.basemaps:
                continue                                # the daily run's rows
            del state['services'][gone]
        state['changes'] = state['changes'][-200:]
        state['checked'] = now
        if a.basemaps:
            state['checked_basemaps'] = now
        tmp = a.out + '.tmp'
        json.dump(state, open(tmp, 'w'), indent=1)
        os.replace(tmp, a.out)
    sys.exit(1 if any(not r['ok'] for r in results) else 0)


if __name__ == '__main__':
    main()
