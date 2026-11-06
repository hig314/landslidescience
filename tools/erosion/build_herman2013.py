#!/opt/anaconda3/bin/python3
"""Herman et al. (2013) exhumation rates -> a display GeoJSON.

    tools/erosion/build_herman2013.py

Source: the Supplementary Table of Herman, Seward, Valla, Carter, Kohn,
Willett & Ehlers (2013), "Worldwide acceleration of mountain erosion under a
cooling climate", Nature 504, 423-426, doi:10.1038/nature12877. It is the
only form the inversion results were published in: a 207-page PDF of
longitude, latitude, erosion rate (mm/yr) and resolution, one block per
2 Myr interval (2-0, 4-2, 6-4, 8-6 Ma) -- "the values used for Figures 1, 2
and 3 ... reported at grid points around existing data location". Only grid
points with resolution above 0.25 are in it, so this is scattered nodes near
the thermochronometric samples, not a continuous field.

The PDF is fetched once into data/erosion_src/herman2013/ (data/ is
gitignored) and read with pdftotext -layout (poppler). The minus signs in it
are a soft hyphen followed by a non-breaking hyphen, and the interval headers
carry the same pair; both are normalised before parsing. The parse is checked
against itself: every line that holds a digit must be a data row, an interval
header or a page furniture line, or the build stops.

Output: inventory/static/inventory/herman2013_rates.geojson -- one Point per
grid node with the rate and resolution of each interval it has:
    e20 e42 e64 e86   erosion rate, mm/yr   (2-0, 4-2, 6-4, 8-6 Ma)
    r20 r42 r64 r86   resolution, 0-1
Edit this script, not the file.

SWITCHED OFF 2026-11-05 (map.js EROSION_ACTIVE): Alaska holds 316 of the
4,116 nodes, all in the St. Elias and eastern Chugach, too few to be useful
on this map. The output is therefore not committed; the copy from the last
run is kept in data/erosion_src/herman2013/. Run this again (seconds, from
the PDF kept there) before switching the rows back on.
"""
import collections, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / 'data' / 'erosion_src' / 'herman2013'
URL = ('https://static-content.springer.com/esm/art%3A10.1038%2Fnature12877/MediaObjects/'
       '41586_2013_BFnature12877_MOESM338_ESM.pdf')
PDF = SRC_DIR / 'herman2013_supplementary_table.pdf'
OUT = ROOT / 'inventory' / 'static' / 'inventory' / 'herman2013_rates.geojson'
KEYS = {'2-0': '20', '4-2': '42', '6-4': '64', '8-6': '86'}
NUM = r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?'
ROW = re.compile(r'^\s*(%s)\s+(%s)\s+(%s)\s+(%s)\s*$' % (NUM, NUM, NUM, NUM))
HEAD = re.compile(r'^\s*(\d)\s*-\s*(\d)\s*(?:Ma)?\s*$')
FURNITURE = re.compile(r'nature|NATURE|Ma\b|mm/yr')


def main():
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    if not PDF.exists():
        subprocess.run(['curl', '-fL', '--retry', '3', '-o', str(PDF), URL], check=True)
    txt = subprocess.run(['/opt/homebrew/bin/pdftotext', '-layout', str(PDF), '-'],
                         check=True, capture_output=True).stdout.decode('utf-8', 'replace')
    txt = (txt.replace('­', '').replace('‐', '-').replace('‑', '-')
              .replace('−', '-'))
    txt = re.sub(r'-\s*-', '-', txt)
    cur, rows, odd = None, collections.OrderedDict(), []
    for ln in re.split(r'[\n\r\f\v]+', txt):
        ln = ln.replace('\t', ' ')
        m = HEAD.match(ln)
        if m:
            cur = '%s-%s' % m.groups()
            if cur not in KEYS:
                sys.exit('unexpected interval header: ' + ln.strip())
            rows.setdefault(cur, [])
            continue
        m = ROW.match(ln)
        if m and cur:
            rows[cur].append(tuple(float(x) for x in m.groups()))
            continue
        if re.search(r'\d', ln) and not FURNITURE.search(ln):
            odd.append(ln.strip())
    if odd:
        sys.exit('%d lines with digits were not understood, e.g. %r' % (len(odd), odd[:5]))
    if list(rows) != list(KEYS):
        sys.exit('intervals found: %s' % list(rows))
    nodes = collections.OrderedDict()
    for iv, rr in rows.items():
        k = KEYS[iv]
        for lon, lat, e, res in rr:
            if not (-180 <= lon <= 180 and -90 <= lat <= 90 and e > 0 and 0 < res <= 1):
                sys.exit('implausible row in %s: %r' % (iv, (lon, lat, e, res)))
            p = nodes.setdefault((lon, lat), {})
            if 'e' + k in p:      # the table repeats a handful of rows verbatim
                if (p['e' + k], p['r' + k]) != (e, res):
                    sys.exit('two different values at one node in %s: %r' % (iv, (lon, lat)))
                continue
            p['e' + k], p['r' + k] = e, res
    feats = [{'type': 'Feature', 'properties': p,
              'geometry': {'type': 'Point', 'coordinates': [lon, lat]}}
             for (lon, lat), p in nodes.items()]
    OUT.write_text(json.dumps({
        'type': 'FeatureCollection',
        'source': 'Herman et al. 2013, Nature 504, 423-426, doi:10.1038/nature12877, Supplementary Table',
        'units': 'e*: erosion rate, mm/yr; r*: resolution, 0-1; suffix = interval in Ma (20 = 2-0 Ma)',
        'features': feats}, separators=(',', ':')) + '\n')
    for iv, rr in rows.items():
        k = KEYS[iv]
        n = sum(1 for p in nodes.values() if 'e' + k in p)
        print('%s Ma: %d rows in the table, %d nodes' % (iv, len(rr), n))
    print('%d nodes -> %s (%.0f kB)' % (len(feats), OUT, OUT.stat().st_size / 1024))


if __name__ == '__main__':
    main()
