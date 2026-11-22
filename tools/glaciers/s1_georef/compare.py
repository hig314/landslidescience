"""Regression gate for a placement method: its placements against the
accepted ones (eye-checked, web/georef.json), and what it does with the
rejected (review_skip.txt).
usage: compare.py DIR [DIR ...]   (match2 output folders: <id>.json)"""
import json, math, sys
from pathlib import Path
ROOT = Path('/Volumes/Nunatak/glacier_s1_georef')


def gdist(a, b):
    lat = math.radians(60)
    return math.hypot(a[0] - b[0], a[3] - b[3]) * math.cos(lat)


def main():
    acc = json.load(open(ROOT / 'web' / 'georef.json'))
    skip = open(ROOT / 'review_skip.txt').read().strip().split(',')
    for D in sys.argv[1:]:
        D = ROOT / D; near = far = 0; far_ids = []; margins = []
        for f, g in acc.items():
            i = f[:-4]; p = D / (i + '.json')
            if not p.exists():
                continue
            b = json.load(open(p)); b = b.get('best', b)
            d = gdist(b['bounds_3857'], g['bounds_3857'])
            if d < 300: near += 1; margins.append(b.get('margin', 0))
            else: far += 1; far_ids.append('%s %.1fkm z%.1f m%.1f' % (i[:6], d / 1000, b['z'], b.get('margin', 0)))
        print('%s: accepted kept %d, moved %d' % (D.name, near, far))
        for x in far_ids: print('   moved', x)
        for i in skip:
            p = D / (i + '.json')
            if p.exists():
                b = json.load(open(p)); b = b.get('best', b)
                name = json.load(open(ROOT / 'v' / i / 'meta.json'))['name'][:24]
                print('   rejected %s %-24s %s/%s z%.1f margin %.1f' % (i[:6], name, b['orbit'], b['water'], b['z'], b.get('margin', 0)))


if __name__ == '__main__':
    main()
