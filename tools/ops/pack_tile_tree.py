#!/usr/bin/env python3
"""Pack an XYZ tile tree (<dir>/{z}/{x}/{y}.png) into one PMTiles archive.

    python3 tools/ops/pack_tile_tree.py data/susc_tiles/lw data/overlays/susc_lw.pmtiles "USGS susceptibility, lw"

Why: the droplet's disk is small and R2 egress is free. A tree of 10^5 loose
PNGs is awkward to move and costly to store as objects; one archive is read
by the map over range requests through /overlays/<id>.pmtiles, exactly as
the permafrost and NASA overlays are. The tiles are copied byte for byte,
so the picture cannot change. Check an archive by reading tiles back with
`pmtiles tile <archive> z x y` and comparing them with the tree's files, and
its addressed-tile count (`pmtiles show`) with the tree's file count.

Needs the `pmtiles` CLI (/opt/homebrew/bin/pmtiles).
"""
import argparse
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PMTILES = '/opt/homebrew/bin/pmtiles'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tree'); ap.add_argument('out'); ap.add_argument('name')
    a = ap.parse_args()
    tree, out = Path(a.tree), Path(a.out)
    zooms = sorted(int(p.name) for p in tree.iterdir() if p.is_dir() and p.name.isdigit())
    if not zooms:
        sys.exit(f'no zoom directories in {tree}')
    with tempfile.TemporaryDirectory() as tmp:
        mb = Path(tmp) / 'pack.mbtiles'
        con = sqlite3.connect(mb)
        con.executescript("""PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE metadata (name TEXT, value TEXT);
            CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);""")
        n = 0
        for z in zooms:
            for xdir in (tree / str(z)).iterdir():
                if not xdir.name.isdigit():
                    continue
                rows = [(z, int(xdir.name), 2 ** z - 1 - int(t.stem), t.read_bytes())   # XYZ -> TMS row
                        for t in xdir.iterdir() if t.suffix == '.png' and t.stem.isdigit()]
                con.executemany('INSERT INTO tiles VALUES (?,?,?,?)', rows)
                n += len(rows)
            con.commit()
        con.execute('CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)')
        for k, v in (('name', a.name), ('format', 'png'), ('type', 'overlay'),
                     ('minzoom', zooms[0]), ('maxzoom', zooms[-1])):
            con.execute('INSERT INTO metadata VALUES (?,?)', (k, str(v)))
        con.commit(); con.close()
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([PMTILES, 'convert', str(mb), str(out)], check=True, stdout=subprocess.DEVNULL)
    print(f'{out}: {n} tiles, z{zooms[0]}-{zooms[-1]}, {out.stat().st_size / 1e6:.0f} MB')


if __name__ == '__main__':
    main()
