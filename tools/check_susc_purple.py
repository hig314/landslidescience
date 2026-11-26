#!/usr/bin/env python3
"""Check a recoloured susceptibility archive against the ramp file and the old archive.

    /opt/anaconda3/bin/python tools/check_susc_purple.py lw n10 dggs nasa

1. Same tile addresses as the yellow-red archive.
2. At the raster's own (maximum) zoom, every opaque pixel is one of the ramp
   file's colours, with that class's alpha (read from the .txt, which the
   recolour tool does not use: it works from the old pixels).
3. Alpha is unchanged against the old archive, tile for tile (a sample).
"""
import io, random, sys
from pathlib import Path
import numpy as np
from PIL import Image
from pmtiles.reader import MmapSource, Reader, all_tiles

ROOT = Path(__file__).resolve().parent.parent
TXT = {'lw': 'susc_color_lw.txt', 'n10': 'susc_color_n10.txt', 'dggs': 'dggs_susc_color.txt', 'nasa': 'nasa_susc_color.txt'}


def rgba(b):
    return np.asarray(Image.open(io.BytesIO(b)).convert('RGBA'))


for m in sys.argv[1:]:
    want = set()
    for line in (ROOT / 'tools' / TXT[m]).read_text().splitlines():
        t = line.split()
        if len(t) == 5 and not line.startswith('#') and t[0] != 'nv' and int(t[4]) > 0:
            want.add(tuple(int(v) for v in t[1:]))
    old = open(ROOT / 'data/overlays' / f'susc_{m}.pmtiles', 'rb')
    new = open(ROOT / 'data/overlays' / f'susc_{m}_purple.pmtiles', 'rb')
    go, gn = MmapSource(old), MmapSource(new)
    zmax = Reader(gn).header()['max_zoom']
    ro = Reader(go)
    addr_new = 0; seen = {}; stray = 0; px = 0; alpha_diff = 0; sampled = 0
    random.seed(1)
    for (z, x, y), data in all_tiles(gn):
        addr_new += 1
        if z != zmax or random.random() > 0.02:
            continue
        a = rgba(data); sampled += 1
        op = a[a[..., 3] > 0]
        px += len(op)
        cols, cnt = np.unique(op, axis=0, return_counts=True)
        for c, k in zip(cols, cnt):
            c = tuple(int(v) for v in c)
            seen[c] = seen.get(c, 0) + int(k)
            if c not in want:
                stray += int(k)
        o = rgba(ro.get(z, x, y))
        alpha_diff += int((o[..., 3] != a[..., 3]).sum())
    n_old = Reader(go).header()['addressed_tiles_count']
    ok = addr_new == n_old and stray == 0 and alpha_diff == 0 and set(seen) <= want
    print(f"{m}: tiles {addr_new} vs {n_old}; z{zmax} sample {sampled} tiles, {px} opaque px, "
          f"{len(seen)} colours of {len(want)} in the ramp file, stray px {stray}, alpha changed px {alpha_diff} -> {'OK' if ok else 'FAIL'}")
    if stray:
        print('   stray:', sorted(((k, c) for c, k in seen.items() if c not in want), reverse=True)[:6])
