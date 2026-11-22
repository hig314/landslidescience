"""Placements from match2.py + fine.py, for the videos an eye check accepted.

Unlike decide.py there is nothing to vote on: match2 already asks all
three products at once. The grade is the margin (significance of the
best peak over the best elsewhere): 'good' >= GOOD_MARGIN, else 'fair'.
Only ids in the ACCEPTED file are written: a placement reaches the map
after someone has looked at it (look.py), never on the score alone.

usage: decide2.py M2DIR ACCEPTED.txt   (one id a line, '#' comments;
       'ID v1' keeps that video's first-method placement)
Writes v/<id>/placement.json (the previous one kept as placement_v1.json
the first time) and rgi_check.jpg."""
import json, math, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from decide import ROOT, draw, rgi_score

GOOD_MARGIN = 3.0
R = 6378137.0


def main():
    m2 = ROOT / sys.argv[1]
    rows = [l.split('#')[0].split() for l in open(sys.argv[2])]
    for row in filter(None, rows):
        i = row[0]; d = ROOT / 'v' / i
        if row[1:] == ['v1']:
            if (d / 'placement_v1.json').exists():
                shutil.copy(d / 'placement_v1.json', d / 'placement.json')
            print('%s kept v1' % i)
            continue
        f = m2 / (i + '_fine.json')
        b = json.load(open(f)) if f.exists() else json.load(open(m2 / (i + '.json')))['best']
        if (d / 'placement.json').exists() and not (d / 'placement_v1.json').exists():
            shutil.copy(d / 'placement.json', d / 'placement_v1.json')
        st = np.load(d / 'stack.npz')
        rgi = json.load(open(ROOT / 'ref' / (i + '_rgi.json')))
        bb = b['bounds_3857']
        lon = lambda x: math.degrees(x / R); lat = lambda y: math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
        out = dict(id=i, key='m2_%s_%s' % (b['orbit'], b['water']), grade='good' if b['margin'] >= GOOD_MARGIN else 'fair',
                   z=b['z'], margin=b['margin'], ncc=b['ncc'], shift_fine_m=b.get('shift_m'), rgi=rgi_score(st, rgi, bb),
                   bounds_3857=bb, bounds=[lon(bb[0]), lat(bb[1]), lon(bb[2]), lat(bb[3])],
                   frames=int(st['good'].sum()), dropped=[int(x) for x in st['dropped']], method='match2+fine')
        (d / 'placement.json').write_text(json.dumps(out, indent=1))
        draw(st, rgi, bb, d / 'rgi_check.jpg')
        print('%s %-4s margin %5.1f fine shift %s rgi %s' % (i, out['grade'], b['margin'], b.get('shift_m'), '%.2f' % out['rgi'] if out['rgi'] else '-'))


if __name__ == '__main__':
    main()
