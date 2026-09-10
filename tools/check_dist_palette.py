#!/usr/bin/env python3
"""Guard the two things about the DIST palette that can silently drift.

  1. map.js DIST_CLASSES must agree with tools/dist_color_status.txt, class
     by class. The ramp file feeds the colour key and the export legend; the
     JS table paints the pixels. If they disagree the key describes something
     the map is not drawing.
  2. inventory/dist.py MERGE_PRIORITY — which class wins in the merged "all
     years" tile — must be the ramp's own importance order, darkest first.
     Inventing a second ranking there is how the merged view starts
     disagreeing with what the per-year views emphasise.

  python3 tools/check_dist_palette.py     # exit 0 = consistent
"""
import colorsys
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def lightness(rgb):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    y = .2126 * lin(rgb[0]) + .7152 * lin(rgb[1]) + .0722 * lin(rgb[2])
    return 116 * (y ** (1 / 3) if y > 0.008856 else 7.787 * y + 16 / 116) - 16


def main():
    fails = []
    txt = {}
    for line in (ROOT / 'tools/dist_color_status.txt').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if parts[0] == 'nv':
            continue
        txt[int(float(parts[0]))] = [int(v) for v in parts[1:5]]

    js = (ROOT / 'inventory/static/inventory/js/map.js').read_text()
    blk = js[js.index('var DIST_CLASSES'):]
    blk = blk[:blk.index('];')]
    paint = {int(c): [int(v) for v in ours.split(',')]
             for c, _gibs, ours in re.findall(
                 r'\[(\d),\s*\[([^\]]+)\],\s*\[([^\]]+)\]\]', blk)}
    for code in range(9):
        if paint.get(code) != txt.get(code):
            fails.append('class %d: map.js %s != ramp file %s'
                         % (code, paint.get(code), txt.get(code)))

    py = (ROOT / 'inventory/dist.py').read_text()
    pri = [int(v) for v in re.search(r'MERGE_PRIORITY = \[([^\]]+)\]', py).group(1).split(',')]
    painted = [c for c in range(1, 9)]
    want = sorted(painted, key=lambda c: lightness(txt[c][:3]))   # darkest first
    want += [0, 255]                     # observed-but-quiet, then never-observed
    if pri != want:
        fails.append('MERGE_PRIORITY is %s but the ramp\'s darkest-first order is %s'
                     % (pri, want))

    for f in fails:
        print('FAIL  ' + f)
    if not fails:
        print('ok  map.js and the ramp file agree on all 9 classes')
        print('ok  MERGE_PRIORITY matches the ramp\'s darkest-first order: %s' % pri)
        print('    (%s)' % ', '.join('%d:L*%.0f' % (c, lightness(txt[c][:3]))
                                     for c in pri if c in txt and c != 0))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
