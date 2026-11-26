#!/usr/bin/env python3
"""Recolour the susceptibility archives from yellow-orange-red to purple.

    /opt/anaconda3/bin/python tools/recolor_susc_archives.py [lw n10 dggs nasa]

Reads data/overlays/susc_<m>.pmtiles (the 2026 yellow-red bakes) and writes
data/overlays/susc_<m>_purple.pmtiles: the same tiles at the same addresses,
every pixel moved from the old ramp to the purple one of
tools/susc_purple_ramps.py. A pixel's GREEN value places it on the old ramp
(it falls monotonically along YlOrRd), which also carries the pixels that
were averaged at low zoom. Alpha is untouched; the DGGS "not assessed" class
(pale blue, B > R + 20) is left as baked.

Why recolour instead of re-baking from the rasters: the USGS source grids
are not on this machine, the result is the same at each raster's own zoom
(every class has exactly one colour there), and it is what the site showed
from 2026-11-25 while the browser did this same mapping tile by tile.

A new look is a new archive id (docs/OVERLAYS.md); the old archives stay.
Push with tools/permafrost/r2_sync_overlays.sh.
"""
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import zxy_to_tileid
from pmtiles.writer import Writer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from susc_purple_ramps import purple  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FAMILY = {'lw': 'usgs', 'n10': 'usgs', 'dggs': 'full', 'nasa': 'full'}


def lut(fam):
    return np.array([purple(g, fam) for g in range(256)], dtype=np.uint8)


def recolour(data, table):
    im = Image.open(io.BytesIO(data)).convert('RGBA')
    a = np.asarray(im).copy()
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    hit = (al > 0) & ~(b.astype(np.int16) > r.astype(np.int16) + 20)
    a[hit, :3] = table[g[hit]]
    out = io.BytesIO()
    Image.fromarray(a, 'RGBA').save(out, 'PNG', optimize=False, compress_level=7)
    return out.getvalue()


def run(m):
    src = ROOT / 'data' / 'overlays' / f'susc_{m}.pmtiles'
    dst = ROOT / 'data' / 'overlays' / f'susc_{m}_purple.pmtiles'
    table = lut(FAMILY[m])
    with open(src, 'rb') as fh, open(dst, 'wb') as out:
        get = MmapSource(fh)
        rd = Reader(get)
        header, meta = rd.header(), rd.metadata()
        w = Writer(out)
        cache, n = {}, 0
        for (z, x, y), data in all_tiles(get):
            new = cache.get(data) if len(data) < 2000 else None     # blank / uniform tiles repeat
            if new is None:
                new = recolour(data, table)
                if len(data) < 2000:
                    cache[data] = new
            w.write_tile(zxy_to_tileid(z, x, y), new)
            n += 1
            if n % 10000 == 0:
                print(f'  {m}: {n} tiles', flush=True)
        meta = dict(meta)
        meta['name'] = meta.get('name', f'susc_{m}') + ' (purple ramp)'
        w.finalize(header, meta)
    print(f'{m}: {n} tiles -> {dst.name} ({dst.stat().st_size / 1e6:.0f} MB, was {src.stat().st_size / 1e6:.0f} MB)')


if __name__ == '__main__':
    for m in (sys.argv[1:] or ['lw', 'n10', 'dggs', 'nasa']):
        run(m)
