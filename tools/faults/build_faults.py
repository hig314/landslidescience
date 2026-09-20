#!/usr/bin/env python3
"""Build the three fault-line display files for the inventory map.

Sources
-------
  tools/faults/src/ak_qff_dds3.geojson
      DGGS DDS 3 (Koehler 2013), statewide Quaternary faults and folds. The
      pristine file; never edited by hand.
  USGS "Alaska Fault Trace Mapping, 2021" (Bender & Haeussler,
      doi:10.5066/P9H02FXB), 648 traces on 25 faults mapped at 1:10,000 on
      ArcticDEM 3.0. Downloaded from ScienceBase, cached in data/faults_src/.
  USGS "Geologic Inputs for the 2023 Alaska NSHM" v2.0 (Bender, Haeussler &
      Powers, doi:10.5066/P97NRR0F), 105 simplified fault sections with slip
      rate, dip and rake. Same download and cache.

Outputs (inventory/static/inventory/)
-------
  ak_qff.geojson, ak_adem2021.geojson, ak_nshm2023.geojson
  Display copies: slimmed properties, rounded coordinates, and a DEPRECATED
  flag (plus DEPRECATED_WHY) on the pieces the map should not draw. The map's
  'faults-line' layer filters on that flag; nothing is deleted, so a flagged
  piece can be inspected in the file and the decision is recorded beside it.

What gets deprecated (Hig, 2026-09-20)
--------------------------------------
1. The four DGGS "seismic zone" ovals (Minto Flats, Fairbanks, Salcha,
   Rampart): closed outlines of diffuse seismicity, not fault traces.
2. Coarse duplicates. Where two sources trace the same structure within
   NEAR_KM of each other, the one with the sparser vertices is the generalised
   copy of the other and is deprecated over exactly the stretch where the
   finer one exists; the rest of it stays live. This is what "deprecate the
   Castle Mountain fault east of -151.1" (NSHM line vs. DGGS detail),
   "the Denali fault northwest of Haines" (DGGS inferred line vs. NSHM) and
   "Totschunda northwest of -141.3" (NSHM vs. DGGS detail) all are. The 2021
   ArcticDEM traces are never deprecated: at 1:10,000 they are the finest
   thing in the set everywhere they exist. Comparisons are only made across
   sources — two DGGS lines a kilometre apart may be two strands — and only
   between lines whose names share a word (Castle Mountain / Castle Mountain
   fault (Susitna section); Yakutat Foothills / Yakutat fault), so a distinct
   parallel structure (Cape Cleare beside Patton Bay on Montague Island) is
   never hidden by its neighbour. The seismic-zone ovals count for nothing.

Run:  /opt/anaconda3/bin/python3 tools/faults/build_faults.py [--report]
"""
import argparse, json, math, os, sys, urllib.request
import numpy as np
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(REPO, 'inventory', 'static', 'inventory')
CACHE = os.path.join(REPO, 'data', 'faults_src')
QFF_SRC = os.path.join(HERE, 'src', 'ak_qff_dds3.geojson')
SB = 'https://www.sciencebase.gov/catalog/file/get/'
ADEM_URL = SB + '60b6a94dd34e86b9388488f3?f=__disk__2c%2F48%2F83%2F2c488395b202018b1d09b9e90e3bb1f7417101ce'
NSHM_ZIP = SB + '60b6a9d6d34e86b93884890e?f=__disk__bd%2Fbc%2Fac%2Fbdbcac9ae61f972bb94f83605565ae12ef2cfaf9'

NEAR_KM = 2.5          # two lines closer than this are "the same structure"
FINER = 0.65           # the other line must have <= this fraction of my vertex spacing
STEP_KM = 0.25         # sampling step along a line
MIN_LIVE_INNER_KM = 5  # an uncovered gap shorter than this, between covered runs, is covered too
MIN_LIVE_END_KM = 3    # an uncovered stub at an end shorter than this is covered too
MIN_DEP_KM = 6         # a covered run shorter than this is left live: a crossing fault
                       # covers ~2*NEAR_KM of a line it crosses, and that is not a duplicate
OVALS = {'Minto Flats seismic zone', 'Fairbanks seismic zone', 'Salcha seismic zone', 'Rampart seismic zone'}
OVAL_WHY = ('Seismic-zone outline (a closed oval around diffuse seismicity, not a mapped fault trace); hidden on '
            'landslidescience.org since 2026-09-20 in favour of the USGS 2021 ArcticDEM traces and NSHM 2023 '
            'sections in interior Alaska.')


STOP = {'fault', 'faults', 'zone', 'section', 'the', 'and', 'of', 'anticline', 'thrust', 'belt', 'fold', 'system',
        'also', 'known', 'as', 'inferred', 'north', 'south', 'east', 'west', 'center', 'central', 'middle', 'upper',
        'lower', 'outboard', 'seismicity', 'seismic', 'northern', 'southern', 'eastern', 'western', 'strand', 'unnamed',
        'river', 'creek', 'mountain', 'mountains', 'glacier', 'lake', 'bay', 'point', 'hills', 'dome', 'basin'}
# (river/creek/mountain… are dropped so "Rude River" and "Lewis River" do not
# match on "river"; the proper noun has to match.)


def name_tokens(name):
    import re
    toks = set(re.findall(r'[a-z0-9]+', name.lower()))
    return {t for t in toks if t not in STOP and len(t) >= 3}


def same_structure(F, G):
    """Names share a proper-noun token, or one squashed name contains the other's token
    ("Ten Fathom" / "Tenfathom fault")."""
    if F['toks'] & G['toks']:
        return True
    fs, gs = F['name'].lower().replace(' ', ''), G['name'].lower().replace(' ', '')
    return any(t in gs for t in F['toks'] if len(t) >= 5) or any(t in fs for t in G['toks'] if len(t) >= 5)


def fetch(url, name):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if not os.path.exists(p):
        print('downloading', name, file=sys.stderr)
        urllib.request.urlretrieve(url, p)
    return p


def load_sources():
    qff = json.load(open(QFF_SRC))
    adem = json.load(open(fetch(ADEM_URL, 'Alaska_ArcticDEM_FaultTraces_2021.json')))
    import zipfile
    z = zipfile.ZipFile(fetch(NSHM_ZIP, 'NSHM2023_Alaska_FaultSections.zip'))
    nshm = json.loads(z.read('GeoJSON/NSHM2023_Alaska_FaultSections.json'))
    return qff, adem, nshm


# ---- geometry helpers (local km frame; Alaska-wide, so cos(lat) per point) ----
def xy(lon, lat):
    return ((lon + 150.0) * 111.0 * math.cos(math.radians(lat)), lat * 111.0)


def parts_of(g):
    cs = g['coordinates'] if g['type'] == 'MultiLineString' else [g['coordinates']]
    return [[tuple(p[:2]) for p in part] for part in cs if len(part) >= 2]


def part_length(part):
    return sum(math.dist(xy(*a), xy(*b)) for a, b in zip(part, part[1:]))


def spacing(parts):
    L = sum(part_length(p) for p in parts)
    nv = sum(len(p) for p in parts)
    return L / max(nv - len(parts), 1)


def samples(part, step):
    """(x, y, s) along the part every `step` km, s = distance from the start."""
    out = []
    s = 0.0
    for a, b in zip(part, part[1:]):
        A, B = np.array(xy(*a)), np.array(xy(*b))
        L = float(np.linalg.norm(B - A))
        k = max(1, int(L / step))
        for t in np.linspace(0, 1, k, endpoint=False):
            P = A + (B - A) * t
            out.append((P[0], P[1], s + L * t))
        s += L
    B = np.array(xy(*part[-1]))
    out.append((B[0], B[1], s))
    return out


def cut(part, s0, s1):
    """The sub-line of `part` between chainages s0..s1 (km), interpolated ends."""
    pts = []
    s = 0.0
    for a, b in zip(part, part[1:]):
        L = math.dist(xy(*a), xy(*b))
        e = s + L
        if e < s0 or s > s1 or L == 0:
            s = e
            continue
        t0 = max(0.0, (s0 - s) / L)
        t1 = min(1.0, (s1 - s) / L)
        p0 = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
        p1 = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
        if not pts or pts[-1] != p0:
            pts.append(p0)
        pts.append(p1)
        s = e
    return pts if len(pts) >= 2 else None


def runs(flags, chain):
    """Contiguous runs of equal flag → [(flag, s_start, s_end)]."""
    out = []
    i = 0
    while i < len(flags):
        j = i
        while j + 1 < len(flags) and flags[j + 1] == flags[i]:
            j += 1
        out.append([flags[i], chain[i], chain[j]])
        i = j + 1
    return out


def smooth(rr, total):
    """Apply the minimum-length rules; returns runs with merged neighbours."""
    changed = True
    while changed and len(rr) > 1:
        changed = False
        for k, (f, a, b) in enumerate(rr):
            L = b - a
            at_end = k == 0 or k == len(rr) - 1
            if not f and ((at_end and L < MIN_LIVE_END_KM and len(rr) > 1) or (not at_end and L < MIN_LIVE_INNER_KM)):
                rr[k][0] = True; changed = True; break
            if f and L < MIN_DEP_KM and len(rr) > 1:
                rr[k][0] = False; changed = True; break
        # merge equal neighbours
        m = [rr[0]]
        for f, a, b in rr[1:]:
            if m[-1][0] == f:
                m[-1][2] = b
            else:
                m.append([f, a, b])
        rr = m
    return rr


def build(report=False):
    qff, adem, nshm = load_sources()
    # Normalised feature list: (src, name, props, parts)
    feats = []
    for f in qff['features']:
        p = f['properties']
        feats.append(dict(src='qff', name=p['NAME'], props=dict(p), parts=parts_of(f['geometry'])))
    for f in adem['features']:
        p = f['properties']
        feats.append(dict(src='adem2021', name=p['FaultName'],
                          props={k: p[k] for k in ('FaultName', 'Change', 'QfaultID', 'Feature', 'SlipSense1', 'SlipSense2')},
                          parts=parts_of(f['geometry'])))
    for f in nshm['features']:
        p = f['properties']
        feats.append(dict(src='nshm2023', name=p['name'],
                          props={k: p[k] for k in ('name', 'FaultID', 'state', 'rate', 'rateType', 'dip', 'rake')},
                          parts=parts_of(f['geometry'])))
    for F in feats:
        F['sp'] = spacing(F['parts']) if F['parts'] else float('inf')
        F['toks'] = name_tokens(F['name'])
        F['oval'] = F['src'] == 'qff' and F['name'] in OVALS

    # One tree of sample points over everything, each knowing its feature.
    pts, owner = [], []
    for i, F in enumerate(feats):
        if F['oval']:
            continue
        for part in F['parts']:
            for x, y, s in samples(part, STEP_KM):
                pts.append((x, y)); owner.append(i)
    tree = cKDTree(np.array(pts)); owner = np.array(owner)

    out = {'qff': [], 'adem2021': [], 'nshm2023': []}
    rep = []

    def emit(F, coords, dep, why=None, covered_by=None):
        props = dict(F['props'])
        if dep:
            props['DEPRECATED'] = True
            props['DEPRECATED_WHY'] = why
            if covered_by:
                props['DEPRECATED_BY'] = covered_by
        nd = 6 if F['src'] == 'adem2021' else 5 if F['src'] == 'nshm2023' else None
        cc = [[round(x, nd), round(y, nd)] for x, y in coords] if nd else [[x, y] for x, y in coords]
        out[F['src']].append({'type': 'Feature', 'properties': props,
                              'geometry': {'type': 'LineString', 'coordinates': cc}})

    for i, F in enumerate(feats):
        if not F['parts']:
            continue
        if F['src'] == 'qff' and F['name'] in OVALS:
            for part in F['parts']:
                emit(F, part, True, OVAL_WHY)
            rep.append((F['src'], F['name'], sum(part_length(p) for p in F['parts']), 'seismic-zone oval'))
            continue
        for part in F['parts']:
            if F['src'] == 'adem2021':
                emit(F, part, False)
                continue
            S = samples(part, STEP_KM)
            chain = [s for _, _, s in S]
            flags = []
            finest = {}
            for x, y, s in S:
                near = tree.query_ball_point((x, y), NEAR_KM)
                cov = False
                for j in set(owner[near]) if near else ():
                    G = feats[j]
                    if G['src'] == F['src'] or not same_structure(F, G):
                        continue
                    if G['sp'] <= FINER * F['sp']:
                        cov = True
                        key = (G['src'], G['name'])
                        finest[key] = finest.get(key, 0) + 1
                flags.append(cov)
            rr = runs(flags, chain)
            rr = smooth(rr, chain[-1])
            if len(rr) == 1 and not rr[0][0]:
                emit(F, part, False)
                continue
            by = max(finest.items(), key=lambda kv: kv[1])[0] if finest else None
            byname = {'qff': 'DGGS QFF', 'adem2021': 'USGS 2021 ArcticDEM traces', 'nshm2023': 'NSHM 2023 sections'}
            for f, a, b in rr:
                piece = cut(part, a, b) if len(rr) > 1 else part
                if not piece:
                    continue
                if f:
                    why = ('Coarser duplicate: %.1f km/vertex where %s "%s" runs within %g km at finer spacing; '
                           'hidden on landslidescience.org since 2026-09-20.'
                           % (F['sp'], byname.get(by[0], by[0]) if by else 'a finer source', by[1] if by else '', NEAR_KM))
                    emit(F, piece, True, why, ('%s: %s' % (byname[by[0]], by[1])) if by else None)
                    rep.append((F['src'], F['name'], b - a, 'covered by %s' % (by[1] if by else '?')))
                else:
                    emit(F, piece, False)

    names = {
        'qff': 'Alaska Quaternary faults and folds, DGGS DDS 3 (Koehler 2013). Display copy built by tools/faults/build_faults.py: '
               'pieces with DEPRECATED=true are hidden by the map (seismic-zone ovals; coarse duplicates of finer sources).',
        'adem2021': 'Alaska Fault Trace Mapping, 2021 (Bender & Haeussler, USGS; doi:10.5066/P9H02FXB), coords rounded to 1e-6. '
                    'Display copy built by tools/faults/build_faults.py.',
        'nshm2023': 'NSHM 2023 Alaska fault sections v2.0 (Bender, Haeussler & Powers; doi:10.5066/P97NRR0F), coords rounded to 1e-5. '
                    'Display copy built by tools/faults/build_faults.py: pieces with DEPRECATED=true are hidden by the map '
                    '(coarse duplicates of finer DGGS or 2021 traces).',
    }
    for src, fn in (('qff', 'ak_qff.geojson'), ('adem2021', 'ak_adem2021.geojson'), ('nshm2023', 'ak_nshm2023.geojson')):
        fc = {'type': 'FeatureCollection', 'name': names[src], 'features': out[src]}
        json.dump(fc, open(os.path.join(OUT, fn), 'w'), separators=(',', ':'))
        ndep = sum(1 for f in out[src] if f['properties'].get('DEPRECATED'))
        print('%-22s %5d features, %4d deprecated pieces, %7.0f KB' % (fn, len(out[src]), ndep, os.path.getsize(os.path.join(OUT, fn)) / 1024))

    if report:
        agg = {}
        for src, name, km, why in rep:
            k = (src, name, why)
            agg[k] = agg.get(k, 0) + km
        print('\nDeprecated, by feature (km):')
        for (src, name, why), km in sorted(agg.items(), key=lambda kv: (kv[0][0], -kv[1])):
            print('  %-9s %-62s %6.0f km  %s' % (src, name[:62], km, why))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', action='store_true')
    build(report=ap.parse_args().report)
