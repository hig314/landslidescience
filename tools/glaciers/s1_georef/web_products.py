"""Placed videos -> the map's static product images + georef.json.

For each v/<id>/ with a placement.json whose grade is in --grades (and not
listed in --skip), writes OUT/<id>_<product>.webp (the frame's own pixel
grid, so its corners are the placement bounds; the burned-in label box is
transparent) and adds the video to OUT/georef.json, keyed by its file name,
for `manage.py s1_videos georef`. Products (glaciers/videos.py
GEOREF_PRODUCTS):
  median       median RGB of the registered, checked frames
  range        log-brightness p90 / median / p10 as R / G / B
  variability  temporal sd of log brightness (magma)
  sign         sign persistence, -1 blue .. 0 white .. +1 orange
  edges        median edge strength (grey)

usage: web_products.py OUT [--grades good,fair] [--skip id,id] [--manual MANUAL.json]
MANUAL.json: {id: bounds_3857} placements set by hand (tie points), grade 'manual'."""
import argparse, json, math
from pathlib import Path
import numpy as np
from PIL import Image
from matplotlib import colormaps

ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')
R = 6378137.0


def lonlat(b):
    lon = lambda x: math.degrees(x / R)
    lat = lambda y: math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
    return [round(lon(b[0]), 6), round(lat(b[1]), 6), round(lon(b[2]), 6), round(lat(b[3]), 6)]


def nrm(x, m, lo=1, hi=99):
    a, b = np.percentile(x[m], [lo, hi])
    return np.clip((x - a) / (b - a + 1e-9), 0, 1)


def save(rgb01_or_u8, m, path):
    x = rgb01_or_u8
    if x.dtype != np.uint8:
        x = (np.clip(x, 0, 1) * 255).astype(np.uint8)
    a = np.where(m, 255, 0).astype(np.uint8)
    Image.fromarray(np.dstack([x[..., :3], a]), 'RGBA').save(path, 'WEBP', quality=82, method=5)


def products(st, out, vid):
    m = st['mask']
    save(st['med_rgb'], m, out / ('%s_median.webp' % vid))
    save(np.dstack([nrm(st['lum_p90'], m), nrm(st['lum_med'], m), nrm(st['lum_p10'], m)]), m, out / ('%s_range.webp' % vid))
    save(colormaps['magma'](nrm(st['lum_std'], m))[..., :3], m, out / ('%s_variability.webp' % vid))
    sg = np.clip((st['sign'].astype(np.float32) + 1) / 2, 0, 1)
    save(colormaps['PuOr_r'](sg)[..., :3], m, out / ('%s_sign.webp' % vid))
    e = nrm(st['feat_med'].astype(np.float32), m, 1, 99.5)
    save(np.dstack([e, e, e]), m, out / ('%s_edges.webp' % vid))
    return ['median', 'range', 'variability', 'sign', 'edges']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--grades', default='good,fair'); ap.add_argument('--skip', default='')
    ap.add_argument('--manual', default=None)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    grades = a.grades.split(','); skip = set(filter(None, a.skip.split(',')))
    manual = json.load(open(a.manual)) if a.manual else {}
    gj = out / 'georef.json'
    geo = json.loads(gj.read_text()) if gj.exists() else {}
    for d in sorted((ROOT / 'v').iterdir()):
        vid = d.name
        if vid in skip or not (d / 'stack.npz').exists():
            continue
        if vid in manual:
            b = manual[vid]; grade = 'manual'; pl = {}
        elif (d / 'placement.json').exists():
            pl = json.load(open(d / 'placement.json'))
            if pl['grade'] not in grades:
                continue
            b = pl['bounds_3857']; grade = pl['grade']
        else:
            continue
        st = np.load(d / 'stack.npz')
        prods = products(st, out, vid)
        geo[vid + '.mp4'] = dict(bounds=lonlat(b), bounds_3857=[round(v, 1) for v in b], grade=grade,
                                 method=pl.get('key', 'tie points'), products=prods,
                                 frames=int(st['good'].sum()), dropped=[int(x) for x in st['dropped']])
        print(vid, grade, lonlat(b))
    gj.write_text(json.dumps(geo, indent=1))


if __name__ == '__main__':
    main()
