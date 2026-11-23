"""Eye check of a placement: the video median | the DEM hillshade at the
same bounds, with the DEM's 2 m coast and RGI outlines drawn on both.
usage: look.py ID BOUNDS_JSON_OR_MATCH2_JSON OUT.jpg [peak index]
LOOK_TAG=_c reads ref/<ID>_c.npz and its RGI (a cand_batch --tag run)."""
import json, math, os, sys
from pathlib import Path
import numpy as np, cv2
ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')
R = 6378137.0


def lonlat(x, y):
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def main():
    vid, src, out = sys.argv[1:4]
    k = int(sys.argv[4]) if len(sys.argv) > 4 else None
    j = json.load(open(src))
    b = j['best']['bounds_3857'] if k is None and 'best' in j else j['peaks'][k]['bounds_3857'] if k is not None else j['bounds_3857']
    st = np.load(ROOT / 'v' / vid / 'stack.npz'); med = np.ascontiguousarray(st['med_rgb'][..., ::-1])
    h, w = med.shape[:2]
    r = np.load(ROOT / 'ref' / (vid + os.environ.get('LOOK_TAG', '') + '.npz')); z = np.nan_to_num(r['dem'], nan=0); T = r['transform']
    # sample the DEM on the video grid
    xs = (b[0] + (np.arange(w) + 0.5) * (b[2] - b[0]) / w - T[2]) / T[0]
    ys = (b[3] - (np.arange(h) + 0.5) * (b[3] - b[1]) / h - T[5]) / T[4]
    mx, my = np.meshgrid(xs.astype(np.float32), ys.astype(np.float32))
    zc = cv2.remap(z.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderValue=0)
    gy, gx = np.gradient(zc, 10 * math.cos(math.radians(lonlat(0, (b[1] + b[3]) / 2)[1])))
    hs = np.clip(0.5 + (-gx + gy) * 0.5 / np.sqrt(1 + gx ** 2 + gy ** 2), 0, 1)
    hs = cv2.cvtColor((hs * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    coast = cv2.morphologyEx((zc <= 2).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((5, 5), np.uint8)) > 0
    rgi = json.load(open(ROOT / 'ref' / (vid + os.environ.get('LOOK_TAG', '') + '_rgi.json')))
    m = np.zeros((h, w), np.uint8); sx = w / (b[2] - b[0]); sy = h / (b[3] - b[1])
    for _g, _n, g in rgi:
        for p in (g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]):
            for ring in p:
                a = np.array(ring)
                cv2.polylines(m, [np.stack([(a[:, 0] - b[0]) * sx, (b[3] - a[:, 1]) * sy], 1).round().astype(np.int32)], True, 1, 4)
    for im in (med, hs):
        im[m > 0] = (0, 255, 255); im[coast] = (255, 160, 0)
    s = cv2.hconcat([med, hs]); s = cv2.resize(s, None, fx=1400 / (2 * w), fy=1400 / (2 * w), interpolation=cv2.INTER_AREA)
    cv2.imwrite(out, s, [cv2.IMWRITE_JPEG_QUALITY, 80])
    lo, la = lonlat((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    print('centre %.4f %.4f' % (lo, la))


if __name__ == '__main__':
    main()
