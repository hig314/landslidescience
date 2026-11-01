#!/opt/anaconda3/bin/python3
"""Bake the slope overlays: six fixed horizontal scales, z3-z11 (Hig, 2026-11-01).

Replaces tools/permafrost/bake_slope_pyramid.py, whose colour meant something
different at every zoom. Here a row's scale never changes with zoom, and
zooming out only averages the picture:

  local   ordinary slope (Horn) on the z12 grid, ~9 m
  60 m    steepest diameter of a 60 m circle, on the z12 grid
  200 m   ... on z11 (~17 m)
  500 m   ... on z10 (~35 m)
  1 km    ... on z9  (~70 m)
  2 km    ... on z8  (~139 m)

(chord_slope.py defines the measure; pixel sizes are 512 px tiles at 63 N.)

Per scale, every z12 tile is coloured on the z12 grid, so the coast and the
coverage edge are as sharp at every scale:
  1. elevation: z12 Mapterhorn, block-averaged to the scale's grid. The sea is
     left at Mapterhorn's own height in the calculation (about 0 m), so a sea
     cliff reads as a cliff; land_mask() decides what is water. A tile Mapterhorn does not have is open sea, also 0 m.
     Tiles beyond the coverage were never fetched and read 0 m too, which is
     safe because the fetch has a 3 km margin and the longest reach is 1 km.
  2. the scale's value, bilinear up to z12 where its grid is coarser.
  3. colour (the one ramp, sp_color_slope.txt); sea and outside the coverage
     polygon become transparent.
  4. zooming out = averaging the coloured picture, premultiplied by alpha:
     z12 -> z11 -> ... -> z3. Scattered steep ground fades in proportion to
     how much of the pixel it fills; neither lost to a mean of slopes nor
     spread over a whole range by a maximum.

z12 itself is not stored: slope_scales.js computes local and 60 m there in the
browser from the same Mapterhorn tiles. The four coarser scales overzoom z11.

The work unit is a z8 tile (16 x 16 z12 tiles plus a one-tile margin), in a
process pool, so memory is ~1 GB a worker. Finished blocks are recorded in
done_<id>.txt and skipped, so a run can be stopped and resumed.

    bake_slope.py --block 8 45 150 --out DIR          # one z8 block: a test
    bake_slope.py --workers 6                         # everything
    bake_slope.py --pack --dest DIR                   # tiles -> DIR/sp_slope_*.pmtiles
                                                      # (17 GB: not data/overlays on the boot disk)
"""
import argparse, json, math, os, shutil, subprocess, sys
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
import rasterio
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shp_transform

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'tools' / 'lidar'))
import chord_slope as C                                     # noqa: E402

SRC = Path(os.environ.get('MAPTERHORN', '/Volumes/Nunatak/terrain_src/mapterhorn')) / '12'
BUILD = Path(os.environ.get('SLOPE_BUILD', '/Volumes/Nunatak/terrain_build/slope'))
COVERAGE = ROOT / 'data/regions_seed/alaska_3338.geojson'
RAMP = ROOT / 'tools/permafrost/sp_color_slope.txt'
QUALITY = int(os.environ.get('BAKE_QUALITY', '85'))
TILE, Z_SRC, Z_BLOCK, Z_TOP, Z_MIN = 512, 12, 8, 11, 3
FLAT_WIN, FLAT_MAX_M = 11, 5.0
PAD_TILES = 1          # margin of z12 tiles round a block, ~4.5 km: reach is at most 1 km

# id, length (None = Horn), zoom of the grid it is computed on
SCALES = [('local', None, 12), ('60', 60, 12), ('200', 200, 11),
          ('500', 500, 10), ('1k', 1000, 9), ('2k', 2000, 8)]


def ramp_lut():
    """RGBA (0-1) per 0.1 degree, 0..90."""
    rows = [l.split() for l in open(RAMP) if l.strip() and not l.startswith(('#', 'nv'))]
    v = np.array([float(r[0]) for r in rows])
    c = np.array([[float(x) for x in r[1:]] for r in rows])
    d = np.arange(0, 901) / 10.0
    return np.stack([np.interp(d, v, c[:, i]) for i in range(4)], -1).astype(np.float32) / 255


LUT = ramp_lut()


def colour(s):
    """Premultiplied RGBA float32 for slope s (deg); NaN -> clear."""
    i = np.nan_to_num(s * 10 + 0.5, nan=-1).astype(np.int32)
    out = LUT[np.clip(i, 0, 900)].copy()
    out[..., :3] *= out[..., 3:4]
    out[i < 0] = 0
    return out


def decode(p):
    try:
        with rasterio.open(p) as t:
            a = t.read().astype(np.float32)
    except Exception as e:      # a truncated download: name it, never read it as sea
        raise RuntimeError(f'cannot decode {p}: {e}') from e
    return a[0] * 256 + a[1] + a[2] / 256 - 32768


def read_block(bx, by):
    """z12 elevation of z8 tile (bx, by) plus the margin; absent tiles are 0 m."""
    k = 2 ** (Z_SRC - Z_BLOCK)
    n = 2 ** Z_SRC
    span = k + 2 * PAD_TILES
    z = np.zeros((span * TILE, span * TILE), np.float32)
    x0, y0 = bx * k - PAD_TILES, by * k - PAD_TILES
    have = 0
    for j in range(span):
        y = y0 + j
        if not 0 <= y < n:
            continue
        for i in range(span):
            p = SRC / str((x0 + i) % n) / f'{y}.webp'
            if p.exists():
                z[j * TILE:(j + 1) * TILE, i * TILE:(i + 1) * TILE] = decode(p)
                have += 1
    return z, y0 * TILE, have


_COV = None


def coverage_px():
    """The coverage polygon in global z12 pixels, x unwrapped west of -180."""
    global _COV
    if _COV is None:
        g = json.load(open(COVERAGE))
        g.pop('crs', None)
        W = 2 ** Z_SRC * TILE

        def f(x, y):
            lon, lat = warp_transform('EPSG:3338', 'EPSG:4326', list(x), list(y))
            lon = np.asarray(lon, float); lat = np.radians(np.asarray(lat, float))
            lon = np.where(lon > 0, lon - 360, lon)     # the western Aleutians: -187, not 173
            return ((lon + 180) / 360 * W,
                    (1 - np.log(np.tan(lat) + 1 / np.cos(lat)) / np.pi) / 2 * W)
        _COV = shp_transform(f, shape(g)).buffer(0)
    return _COV


def coverage_mask(bx, by):
    """True on the block's z12 pixels (no margin) inside the coverage."""
    k = 2 ** (Z_SRC - Z_BLOCK)
    W, S = 2 ** Z_SRC * TILE, k * TILE
    x0, y0 = bx * S, by * S
    cov = coverage_px()
    # A block in the eastern hemisphere is the unwrapped polygon shifted one world.
    off = -W if bx >= 2 ** (Z_BLOCK - 1) else 0
    win = box(x0 + off, y0, x0 + off + S, y0 + S)
    part = cov.intersection(win)
    if part.is_empty:
        return None
    return rasterize([mapping(part)], out_shape=(S, S), dtype=np.uint8,
                     transform=Affine(1, 0, x0 + off, 0, 1, y0)).astype(bool)


def land_mask(z):
    """False on water that must not be coloured.

    Mapterhorn's sea is not always 0 m. In Alaska whole source cells carry it
    as a constant -3 m or +2.5 m (found 2026-11-01 in Prince William Sound:
    green flat water with straight edges where the source cells meet). So:
    anything at or below 0 m, plus exactly flat ground under FLAT_MAX_M in a
    window FLAT_WIN pixels wide, grown back out to the patch's edge. Lakes
    above that height are real flat surfaces and stay. The heights are
    quantised to 0.5 m at z12, so land this low is rarely exactly flat that
    far across; a tidal flat that is loses only a nearly clear colour.
    The browser does not repeat this: it takes the mask from the z11 tile.
    """
    kern = np.ones((FLAT_WIN, FLAT_WIN), np.uint8)
    flat = (cv2.dilate(z, kern) == cv2.erode(z, kern)) & (z <= FLAT_MAX_M)
    grown = cv2.dilate(flat.astype(np.uint8), kern).astype(bool) & (z <= FLAT_MAX_M)
    return (z > 0) & ~grown


def block_mean(a, k):
    if k == 1:
        return a
    h, w = a.shape
    return cv2.resize(a, (w // k, h // k), interpolation=cv2.INTER_AREA)


def fine_values(z, tx, ty, y_global, length):
    """A z12-grid scale (Horn, or the 60 m diameter) on one z12 tile of a block."""
    y0 = (ty + PAD_TILES) * TILE
    x0 = (tx + PAD_TILES) * TILE
    rows = y_global + np.arange(y0, y0 + TILE)
    row_m = C.mercator_res(Z_SRC) * np.cos(np.radians(C.row_lat(rows, Z_SRC)))
    if length is None:
        wide = np.concatenate(([row_m[0]], row_m, [row_m[-1]]))
        return C.horn(z[y0 - 1:y0 + TILE + 1, x0 - 1:x0 + TILE + 1], wide)[1:-1, 1:-1]
    r = (length / 2) / row_m[TILE // 2]
    pad = int(math.ceil(r)) + 1
    return C.diameter_slope_tile(z[y0 - pad:y0 + TILE + pad, x0 - pad:x0 + TILE + pad], length, r, row_m)


def coarse_values(z, y_global, length, zc):
    """A coarser scale over a whole block on its own grid, one tile of that grid
    at a time (one radius in pixels per tile), with EDGE extra cells all round
    so the bilinear step up to z12 is continuous across block edges."""
    EDGE = 1
    k = 2 ** (Z_SRC - zc)
    zk = block_mean(z, k)
    res = C.mercator_res(zc)
    margin = PAD_TILES * TILE // k
    S = 2 ** (Z_SRC - Z_BLOCK) * TILE // k           # block size on this grid
    out = np.full((S + 2 * EDGE, S + 2 * EDGE), np.nan, np.float32)
    n_c = 2 ** (zc - Z_BLOCK)
    y0_c = y_global // k                             # global row of zk[0]
    for j in range(n_c):
        for i in range(n_c):
            ya, xa = margin + j * TILE - EDGE, margin + i * TILE - EDGE
            size = TILE + 2 * EDGE
            rows = y0_c + np.arange(ya, ya + size)
            row_m = res * np.cos(np.radians(C.row_lat(rows, zc)))
            r = (length / 2) / row_m[size // 2]
            pad = int(math.ceil(r)) + 1
            out[j * TILE:j * TILE + size, i * TILE:i * TILE + size] = C.diameter_slope_tile(
                zk[ya - pad:ya + size + pad, xa - pad:xa + size + pad], length, r, row_m)
    return out, k, EDGE


def upsample(coarse, tx, ty):
    """z12 tile (tx, ty) of a block's coarse values, bilinear at pixel centres."""
    out, k, edge = coarse
    ys = (ty * TILE + np.arange(TILE) + 0.5) / k - 0.5 + edge
    xs = (tx * TILE + np.arange(TILE) + 0.5) / k - 0.5 + edge
    mx, my = np.meshgrid(xs.astype(np.float32), ys.astype(np.float32))
    return cv2.remap(out, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def half(a):
    """2 x 2 mean of a premultiplied RGBA array."""
    return cv2.resize(a, (a.shape[1] // 2, a.shape[0] // 2), interpolation=cv2.INTER_AREA)


def write_webp(rgba, path):
    if rgba[..., 3].max() < 1 / 255:
        return False
    a = rgba[..., 3:4]
    rgb = np.where(a > 1e-4, rgba[..., :3] / np.maximum(a, 1e-4), 0)
    u8 = (np.concatenate([rgb, a], -1).clip(0, 1) * 255 + 0.5).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    Image.fromarray(u8, 'RGBA').save(tmp, 'WEBP', quality=QUALITY, method=4, alpha_quality=100, exact=False)
    tmp.replace(path)
    return True


def bake_block(args):
    """All six scales for one z8 block: z11, z10, z9, z8 tiles + z8 RGBA for the top."""
    bx, by, out_root, ids = args
    mask = coverage_mask(bx, by)
    if mask is None:
        return bx, by, 'outside'
    z, y_global, have = read_block(bx, by)
    if have == 0:
        return bx, by, 'no tiles'
    k = 2 ** (Z_SRC - Z_BLOCK)
    land = land_mask(z)
    for sid, length, zc in SCALES:
        if sid not in ids:
            continue
        coarse = None if (length is None or zc == Z_SRC) else coarse_values(z, y_global, length, zc)
        m = 2 ** (Z_TOP - Z_BLOCK)                   # z11 tiles per block side
        z11 = np.zeros((m * TILE, m * TILE, 4), np.float32)
        for ty in range(k):
            for tx in range(k):
                ya, xa = (ty + PAD_TILES) * TILE, (tx + PAD_TILES) * TILE
                vis = land[ya:ya + TILE, xa:xa + TILE] & mask[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE]
                if not vis.any():
                    continue
                if coarse is not None:
                    v = upsample(coarse, tx, ty)
                else:
                    v = fine_values(z, tx, ty, y_global, length)
                v = np.where(vis, v, np.nan)
                z11[ty * TILE // 2:(ty + 1) * TILE // 2, tx * TILE // 2:(tx + 1) * TILE // 2] = half(colour(v))
        img = z11
        root = out_root / sid
        for zz in range(Z_TOP, Z_BLOCK - 1, -1):
            n = 2 ** (zz - Z_BLOCK)
            for j in range(n):
                for i in range(n):
                    write_webp(img[j * TILE:(j + 1) * TILE, i * TILE:(i + 1) * TILE],
                               root / str(zz) / str(bx * n + i) / f'{by * n + j}.webp')
            if zz > Z_BLOCK:
                img = half(img)
        hold = out_root / '_z8' / sid
        hold.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(hold / f'{bx}_{by}.npz', a=img)
    return bx, by, 'ok'


def bake_block_safe(args):
    """One bad block must not lose the night: report it, leave it unrecorded."""
    try:
        return bake_block(args)
    except Exception as e:
        return args[0], args[1], f'ERROR {e}'


def blocks_from_tiles():
    """z8 blocks holding a fetched z12 tile."""
    k = 2 ** (Z_SRC - Z_BLOCK)
    s = set()
    for xd in SRC.iterdir():
        if xd.is_dir() and xd.name.isdigit():
            x = int(xd.name)
            for f in os.listdir(xd):
                if f.endswith('.webp'):
                    s.add((x // k, int(f[:-5]) // k))
    return sorted(s)


def top_levels(out_root, ids):
    """z7..z3 from the z8 RGBA the blocks left behind."""
    for sid in ids:
        hold = out_root / '_z8' / sid
        cur = {}
        for f in hold.glob('*.npz'):
            x, y = f.stem.split('_')
            cur[(int(x), int(y))] = np.load(f)['a']
        for zz in range(Z_BLOCK - 1, Z_MIN - 1, -1):
            parents = {}
            for (x, y), a in cur.items():
                p = parents.setdefault((x // 2, y // 2), np.zeros((TILE, TILE, 4), np.float32))
                h = half(a)
                ox, oy = (x % 2) * TILE // 2, (y % 2) * TILE // 2
                p[oy:oy + TILE // 2, ox:ox + TILE // 2] = h
            for (x, y), a in parents.items():
                write_webp(a, out_root / sid / str(zz) / str(x) / f'{y}.webp')
            cur = parents
        print(f'{sid}: z{Z_MIN}-z{Z_BLOCK - 1} done', flush=True)


def pack(out_root, ids, dest):
    import build_lidar as BL
    desc = {'local': 'ordinary slope (Horn) on the ~9 m grid'}
    for sid in ids:
        length = dict((s[0], s[1]) for s in SCALES)[sid]
        title = f'Slope, {desc.get(sid) or "steepest diameter of a " + str(length) + " m circle"}'
        ds = {'id': f'sp_slope_{sid}', 'title': title, 'min_zoom': Z_MIN, 'max_zoom': Z_TOP}
        mb = out_root / f'sp_slope_{sid}.mbtiles'
        BL.dir_to_mbtiles(out_root / sid, mb, ds, 'webp', title + '; tools/terrain/bake_slope.py')
        final = Path(dest) / f'sp_slope_{sid}.pmtiles'
        # --tmpdir: convert spools the tile data to a temp file, which by default
        # is on the boot disk
        subprocess.run(['/opt/homebrew/bin/pmtiles', 'convert', '--tmpdir', str(out_root), str(mb), str(final)],
                       check=True)
        mb.unlink()
        print(f'{final}: {final.stat().st_size / 2**20:.0f} MB', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--block', nargs=3, type=int, metavar=('Z', 'X', 'Y'))
    ap.add_argument('--out', default=str(BUILD))
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--scales', default=','.join(s[0] for s in SCALES))
    ap.add_argument('--pack', action='store_true')
    ap.add_argument('--dest', default=str(ROOT / 'data' / 'overlays'))
    a = ap.parse_args()
    out = Path(a.out); ids = a.scales.split(',')
    out.mkdir(parents=True, exist_ok=True)
    if a.pack:
        pack(out, ids, a.dest)
        return
    if a.block:
        assert a.block[0] == Z_BLOCK
        print(bake_block((a.block[1], a.block[2], out, ids)))
        return
    done_f = out / 'done_blocks.txt'
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    work = [(x, y, out, ids) for x, y in blocks_from_tiles() if f'{x}/{y}' not in done]
    print(f'{len(work)} z8 blocks to bake, {len(done)} done', flush=True)
    failed = []
    with Pool(a.workers) as pool, open(done_f, 'a') as log:
        for i, (x, y, st) in enumerate(pool.imap_unordered(bake_block_safe, work), 1):
            if st.startswith('ERROR'):
                failed.append((x, y, st))
                print(f'FAILED {x}/{y}: {st}', flush=True)
            else:
                log.write(f'{x}/{y}\n'); log.flush()
            if i % 10 == 0 or i == len(work):
                print(f'{i}/{len(work)} (last {x}/{y}: {st})', flush=True)
    if failed:
        print(f'{len(failed)} blocks failed; fix them and re-run (done blocks are skipped). '
              'Top levels not built.', flush=True)
        sys.exit(1)
    top_levels(out, ids)


if __name__ == '__main__':
    main()
