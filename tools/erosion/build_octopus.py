#!/opt/anaconda3/bin/python3
"""OCTOPUS cosmogenic denudation rates -> one vector PMTiles overlay.

    tools/erosion/build_octopus.py fetch     # WFS -> data/erosion_src/octopus/*.geojsonl
    tools/erosion/build_octopus.py tiles     # -> data/overlays/octopus_crn.pmtiles

OCTOPUS v2 (Codilean et al. 2022, ESSD 14, 3695; CC BY 4.0) publishes
basin-averaged 10Be / 26Al denudation rates with the drainage basin each
sample integrates. Their GeoServer (octopusdata.org) has an open WFS but
sends no CORS header, and the basins are ~50 kB of outline apiece, so the
browser cannot read it directly: this mirrors the two CRN collections that
carry rates

    crn_int   global collection          (the bulk)
    crn_aus   Australian collection      (kept apart by OCTOPUS; same schema)

and packs them as vector tiles. (The third, crn_xxl -- 261 continental
river basins -- is left out: OCTOPUS publishes 10Be concentrations for
them but no denudation rate, every EBE_MMKYR is -9999.99, checked
2026-11-05.) Nothing is reinterpreted: every kept field
is the publisher's, under the publisher's name. The one addition is `rate`,
in mm/yr so it shares a scale with the thermochronometric exhumation rates
beside it: EBE_MMKYR / 1000 (10Be, recalculated by OCTOPUS with CAIRN),
else EAL_MMKYR / 1000 (26Al) where there is no 10Be value, else absent.
-9999 / -9999.99 / 'NA' are the publisher's missing values and are dropped.

`fetch` pages the WFS (250 features a request, sorted by OBSID1, gzip) into
one feature per line; it resumes a layer that is already complete. `tiles`
writes two source-layers -- `basins` (polygons) and
`sites` (the sample points, carrying the same fields, joined by OBSID1,
so a rate is visible at world scale where its basin is smaller than a
pixel) -- through GDAL's PMTiles writer.

SWITCHED OFF 2026-11-05 (map.js EROSION_ACTIVE): 24 basins in Alaska and
Yukon (two studies) of 5,673 worldwide, too few to be useful on this map.
The archive from the last run is kept beside the mirror, in
data/erosion_src/octopus/octopus_crn.pmtiles, NOT in data/overlays/ (which
tools/permafrost/r2_sync_overlays.sh pushes whole) and not on R2. To bring
it back: `tiles` (it writes data/overlays/), push to R2, flip the flag.

Refetching replaces the files: OCTOPUS revises between versions (DBVERNO
rides every feature and the popup shows it).
"""
import gzip, json, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'data' / 'erosion_src' / 'octopus'
OUT = ROOT / 'data' / 'overlays'
WFS = 'https://octopusdata.org/geoserver/wfs'
GDAL = Path('/opt/homebrew/bin')
COLLECTIONS = ['crn_int', 'crn_aus']
PAGE = 250

# Publisher fields kept in the tiles (the popup's rows, in this order).
KEEP_TEXT = ['OBSID1', 'OBSID2', 'STUDYID', 'CRN_SUBCMP', 'CNTRY', 'REGION_INT', 'REGION_REG',
             'BASIN', 'RIVNAME', 'AUTHOR', 'PUBYEAR', 'REFDOI', 'DBVERNO', 'DBDOI']
KEEP_NUM = ['AREA', 'ELEV_AVE', 'SLP_AVE', 'GLA_PCNT', 'BE10NC', 'BE10NC_ERR',
            'EBE_MMKYR', 'EBE_ERR', 'EAL_MMKYR', 'EAL_ERR']


def get(params):
    url = WFS + '?' + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={
                'Accept-Encoding': 'gzip', 'User-Agent': 'landslidescience.org mirror build'})
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                return raw
        except Exception as e:                                  # noqa: BLE001
            print(f'   retry {attempt + 1}: {e}', flush=True)
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f'gave up on {url}')


def hits(layer):
    x = get({'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
             'typeNames': 'be10-denude:' + layer, 'resultType': 'hits'}).decode()
    return int(x.split('numberMatched="')[1].split('"')[0])


def fetch():
    SRC.mkdir(parents=True, exist_ok=True)
    for coll in COLLECTIONS:
        for kind in ('outlets', 'basins'):
            layer = f'{coll}_{kind}'
            out = SRC / f'{layer}.geojsonl'
            want = hits(layer)
            if out.exists() and sum(1 for _ in open(out)) == want:
                print(f'== {layer}: {want} already here'); continue
            tmp = out.with_suffix('.part')
            n, seen, t0 = 0, set(), time.time()
            with open(tmp, 'w') as fh:
                while n < want:
                    d = json.loads(get({
                        'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
                        'typeNames': 'be10-denude:' + layer, 'outputFormat': 'application/json',
                        'srsName': 'EPSG:4326', 'sortBy': 'OBSID1',
                        'count': PAGE, 'startIndex': n}))
                    fs = d.get('features') or []
                    if not fs:
                        break
                    for f in fs:
                        seen.add(f['properties'].get('OBSID1'))
                        fh.write(json.dumps({'type': 'Feature', 'properties': f['properties'],
                                             'geometry': f['geometry']}, separators=(',', ':')) + '\n')
                    n += len(fs)
                    print(f'   {layer}: {n}/{want}  {tmp.stat().st_size / 2**20:.0f} MB  '
                          f'{time.time() - t0:.0f}s', flush=True)
            if n != want or len(seen) != want:
                raise SystemExit(f'{layer}: got {n} features, {len(seen)} distinct OBSID1, '
                                 f'service says {want} -- not keeping a partial mirror')
            tmp.replace(out)
            print(f'== {layer}: {n} features, {out.stat().st_size / 2**20:.0f} MB')


def num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x <= -9999 else x


def slim(p):
    q = {}
    for k in KEEP_TEXT:
        v = p.get(k)
        if v not in (None, '', 'NA', '-9999'):
            q[k] = str(v)
    for k in KEEP_NUM:
        x = num(p.get(k))
        if x is not None:
            q[k] = x
    be, al = q.get('EBE_MMKYR'), q.get('EAL_MMKYR')
    r = be if be is not None and be > 0 else (al if al is not None and al > 0 else None)
    if r is not None:
        q['rate'] = round(r / 1000.0, 6)
        q['rate_from'] = '10Be' if r is be else '26Al'
    return q


def tiles():
    work = SRC / 'tiles_work'
    work.mkdir(exist_ok=True)
    for name, colls, maxz in (('octopus_crn', COLLECTIONS, 9),):
        props, nb, ns, norate, lines = {}, 0, 0, 0, []
        for c in colls:
            for line in open(SRC / f'{c}_basins.geojsonl'):
                f = json.loads(line)
                q = slim(f['properties']); props[q.get('OBSID1')] = q
                norate += 'rate' not in q
                lines.append((-(q.get('AREA') or 0), json.dumps(
                    {'type': 'Feature', 'properties': q, 'geometry': f['geometry']},
                    separators=(',', ':'))))
                nb += 1
        # Largest basins first, so a nested small basin draws on top of the
        # big one that contains it. Sorted here, as text: an ORDER BY inside
        # ogr2ogr held every outline in memory at once and took the machine
        # into swap (2026-11-05).
        lines.sort(key=lambda t: t[0])
        with open(work / 'basins.geojsonl', 'w') as fb:
            for _, ln in lines:
                fb.write(ln + '\n')
        del lines
        fs = open(work / 'sites.geojsonl', 'w')
        for c in colls:
            for line in open(SRC / f'{c}_outlets.geojsonl'):
                f = json.loads(line)
                q = props.get(f['properties'].get('OBSID1'))
                if q is None:
                    raise SystemExit(f"{c}: site {f['properties'].get('OBSID1')} has no basin")
                fs.write(json.dumps({'type': 'Feature', 'properties': q, 'geometry': f['geometry']},
                                    separators=(',', ':')) + '\n'); ns += 1
        fs.close()
        gpkg = work / f'{name}.gpkg'
        gpkg.unlink(missing_ok=True)
        env = {'PATH': str(GDAL) + ':/usr/bin:/bin', 'GDAL_NUM_THREADS': '2'}
        subprocess.run([GDAL / 'ogr2ogr', '-f', 'GPKG', gpkg, work / 'basins.geojsonl', '-nln', 'basins',
                        '-nlt', 'MULTIPOLYGON'], check=True, env=env)
        subprocess.run([GDAL / 'ogr2ogr', '-f', 'GPKG', '-update', gpkg, work / 'sites.geojsonl',
                        '-nln', 'sites'], check=True, env=env)
        out = OUT / f'{name}.pmtiles'
        out.unlink(missing_ok=True)
        subprocess.run([GDAL / 'ogr2ogr', '-f', 'PMTiles', out, gpkg,
                        '-dsco', 'MINZOOM=0', '-dsco', f'MAXZOOM={maxz}',
                        '-dsco', 'SIMPLIFICATION=2', '-dsco', 'SIMPLIFICATION_MAX_ZOOM=1',
                        '-dsco', 'MAX_SIZE=1500000', '-dsco', 'MAX_FEATURES=400000',
                        '-dsco', f'NAME={name}',
                        '-dsco', 'DESCRIPTION=OCTOPUS v2 CRN denudation rates (Codilean et al. 2022), CC BY 4.0'],
                       check=True, env=env)
        print(f'== {name}: {nb} basins ({norate} without a rate), {ns} sites -> '
              f'{out} ({out.stat().st_size / 2**20:.1f} MB)')


if __name__ == '__main__':
    {'fetch': fetch, 'tiles': tiles}.get(sys.argv[1] if len(sys.argv) > 1 else '', lambda: sys.exit(__doc__))()
