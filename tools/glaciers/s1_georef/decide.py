"""Choose one placement per video from match.json's candidates, and grade it.

match.json holds six candidates: three products (lum, feat, stab) x two
orbit simulations. Two things decide, neither of them the match score alone:

  support  how many candidates land at the same place (within AGREE_M). The
           products are partly independent and the wrong orbit's simulation
           is a different picture, so agreement is evidence.
  rgi      an independent check: RGI 7.0 outlines (not the DEM the match
           used) rasterised on the video at that placement; the median edge
           strength under the outline over the median edge strength of the
           frame. Outlines follow margins and nunataks, so at the right
           place they sit on edges (> ~1.3); elsewhere ~1.0.

grade: 'good' (support >= 2 and rgi >= RGI_GOOD, or support >= 3),
       'fair' (one of the two), 'none'.
Every grade is meant to be looked at in rgi_check.jpg before use; the
grade orders that review, it does not replace it.

usage: decide.py [ID ...]   (default: every folder under v/)
Writes v/<id>/placement.json and v/<id>/rgi_check.jpg."""
import json, math, sys
from pathlib import Path
import numpy as np, cv2

ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')
AGREE_M = 800.0
RGI_GOOD = 1.3


def rgi_mask(rgi, b, shape):
    h, w = shape
    sx = w / (b[2] - b[0]); sy = h / (b[3] - b[1])
    m = np.zeros((h, w), np.uint8)
    for _gid, _name, g in rgi:
        polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
        for p in polys:
            for r in p:
                a = np.array(r)
                pts = np.stack([(a[:, 0] - b[0]) * sx, (b[3] - a[:, 1]) * sy], 1).round().astype(np.int32)
                cv2.polylines(m, [pts], True, 1, 5)
    return m > 0


def rgi_score(st, rgi, b):
    f = st['feat_med']; ok = st['mask']
    m = rgi_mask(rgi, b, ok.shape) & ok
    if m.sum() < 500:
        return None
    return float(np.median(f[m]) / max(np.median(f[ok]), 1e-9))


def draw(st, rgi, b, out):
    v = np.ascontiguousarray(st['med_rgb'][..., ::-1]).copy()
    v[rgi_mask(rgi, b, v.shape[:2])] = (0, 255, 255)
    cv2.imwrite(str(out), cv2.resize(v, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 85])


def decide(d):
    vid = d.name
    m = json.load(open(d / 'match.json'))
    st = np.load(d / 'stack.npz')
    rgi = json.load(open(ROOT / 'ref' / (vid + '_rgi.json')))
    cands = []
    for k, r in m.items():
        b = r['bounds_3857']
        cands.append(dict(key=k, b=b, ncc=r['ncc'], margin=r['ncc'] - r['second'], rgi=rgi_score(st, rgi, b)))
    for c in cands:
        c['support'] = sum(1 for o in cands if math.hypot(o['b'][0] - c['b'][0], o['b'][3] - c['b'][3]) * 0.5 < AGREE_M)
        # (mercator metres at ~60 N are about twice ground metres)
    best = max(cands, key=lambda c: (c['support'], c['rgi'] or 0, c['margin']))
    good_rgi = (best['rgi'] or 0) >= RGI_GOOD
    grade = 'good' if (best['support'] >= 2 and good_rgi) or best['support'] >= 3 else \
            'fair' if (best['support'] >= 2 or good_rgi) else 'none'
    b = best['b']; R = 6378137.0
    lon = lambda x: math.degrees(x / R); lat = lambda y: math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
    out = dict(id=vid, key=best['key'], grade=grade, support=best['support'], rgi=best['rgi'], ncc=best['ncc'],
               margin=round(best['margin'], 3), bounds_3857=b, bounds=[lon(b[0]), lat(b[1]), lon(b[2]), lat(b[3])],
               frames=int(st['good'].sum()), dropped=[int(x) for x in st['dropped']],
               candidates=[{k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items() if k != 'b'} for c in cands])
    (d / 'placement.json').write_text(json.dumps(out, indent=1))
    draw(st, rgi, b, d / 'rgi_check.jpg')
    return out


def main():
    ids = sys.argv[1:] or sorted(p.name for p in (ROOT / 'v').iterdir() if (p / 'match.json').exists())
    for i in ids:
        try:
            o = decide(ROOT / 'v' / i)
            meta = json.load(open(ROOT / 'v' / i / 'meta.json'))
            print('%s %-26s %-4s %-9s support %d rgi %s ncc %.2f margin %.2f' % (
                i, meta['name'][:26], o['grade'], o['key'], o['support'], '%.2f' % o['rgi'] if o['rgi'] else '-', o['ncc'], o['margin']))
        except Exception as e:
            print(i, 'FAILED', e)


if __name__ == '__main__':
    main()
