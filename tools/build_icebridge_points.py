"""Build GeoJSON point layers from downloaded IceBridge L2 CSVs.

Reads every CSV under data/glaciers/icebridge_raw/{IRUAFHF2,IRARES2}/ (run
tools/fetch_icebridge_data.py first). Each file opens with a block of
'#'-prefixed metadata lines (source filename, date, variable glossary)
before the real header row — those are skipped. Per the NSIDC technical
references,
bed_height_m / ice_thickness_m are left EMPTY on rows where no glacier bed
could be interpreted from that trace — there is no numeric sentinel, so a
row counts as having a value iff the CSV field is non-empty. Keeps only
rows with a bed_height_m, and from those keeps lon_deg_e, lat_deg_n,
bed_height_m, ice_thickness_m. Writes one FeatureCollection per instrument:

  data/glaciers/icebridge_uaf_hf.json
  data/glaciers/icebridge_ares.json

Named .json (not .geojson) so they're served as-is by the existing
glaciers:tracer_data route/regex with no backend changes — _catalog() in
views.py already skips any data/glaciers/*.json without name/center/bin
keys, so these don't get mistaken for a tracer-bundle header.

Each feature is a Point at (lon_deg_e, lat_deg_n), WGS 84, with properties
bed_height_m and ice_thickness_m (meters, WGS 84 ellipsoid heights).

Along-track radar sampling is far denser than any web-map zoom level needs
(ARES alone: 2.88M qualifying rows -> a 545 MB GeoJSON, unfetchable client-
side). GRID_DEG grid-decimates: one point kept per ~GRID_DEG-degree cell,
first-seen wins, applied across all files together so repeated/overlapping
flight lines collapse too. This is a display simplification, not a science
product — the raw CSVs are the record of truth and stay in icebridge_raw/.

Usage: python tools/build_icebridge_points.py
"""
import csv
import json
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'icebridge_raw'
OUT_DIR = Path(__file__).resolve().parent.parent / 'data' / 'glaciers'

# ~0.0005 deg is ~55 m N-S at these latitudes (less E-W) -- far finer than a
# point is visually distinguishable at any zoom this app renders at.
GRID_DEG = 0.0005

# output slug -> source short_name
SOURCES = {
    'uaf_hf': 'IRUAFHF2',
    'ares': 'IRARES2',
}


def build(slug, short_name):
    raw_dir = RAW_DIR / short_name
    csv_files = sorted(raw_dir.glob('*.csv'))
    if not csv_files:
        print(f'{slug}: no CSVs found in {raw_dir} — run fetch_icebridge_data.py first')
        return

    features = []
    lons, lats, beds, thicks = [], [], [], []
    seen_cells = set()
    n_rows = 0
    for path in csv_files:
        with path.open(newline='') as f:
            lines = (line for line in f if not line.startswith('#'))
            for row in csv.DictReader(lines):
                bed = (row.get('bed_height_m') or '').strip()
                if not bed:
                    continue
                lon = (row.get('lon_deg_e') or '').strip()
                lat = (row.get('lat_deg_n') or '').strip()
                if not lon or not lat:
                    continue
                n_rows += 1
                lon_f, lat_f, bed_f = float(lon), float(lat), float(bed)
                cell = (round(lon_f / GRID_DEG), round(lat_f / GRID_DEG))
                if cell in seen_cells:
                    continue
                seen_cells.add(cell)
                thickness = (row.get('ice_thickness_m') or '').strip()
                lons.append(lon_f)
                lats.append(lat_f)
                beds.append(bed_f)
                if thickness:
                    thicks.append(float(thickness))
                features.append({
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [lon_f, lat_f]},
                    'properties': {
                        'bed_height_m': bed_f,
                        'ice_thickness_m': float(thickness) if thickness else None,
                    },
                })

    out_path = OUT_DIR / f'icebridge_{slug}.json'
    with out_path.open('w') as f:
        json.dump({'type': 'FeatureCollection', 'features': features}, f)

    if lons:
        print(f'{slug}: {len(features)} points (from {n_rows} raw rows, grid={GRID_DEG}°) -> {out_path}  '
              f'lon [{min(lons):.3f}, {max(lons):.3f}]  lat [{min(lats):.3f}, {max(lats):.3f}]')
        print(f'  bed_height_m: {_stats(beds)}')
        print(f'  ice_thickness_m: {_stats(thicks)}')
    else:
        print(f'{slug}: 0 points with a bed_height_m -> {out_path}')


def _stats(values):
    """min / p10 / median / p90 / max — the inputs for picking real ramp stops
    (see ICEBRIDGE_RAMPS in glaciers.js, currently provisional round numbers)."""
    if not values:
        return 'no values'
    s = sorted(values)
    def pct(p):
        return s[min(len(s) - 1, int(p * (len(s) - 1)))]
    return (f'min {s[0]:.0f}  p10 {pct(0.10):.0f}  median {pct(0.50):.0f}  '
            f'p90 {pct(0.90):.0f}  max {s[-1]:.0f}  (n={len(s)})')


def main():
    for slug, short_name in SOURCES.items():
        build(slug, short_name)


if __name__ == '__main__':
    main()
