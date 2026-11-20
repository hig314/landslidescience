#!/opt/anaconda3/bin/python3
"""Two form-only landform statistics, prototype (Hig, 2026-11-18).

Exploratory, beside the required-strength work in ../scoops and under the same
rule: NO comparison with landslide mapping. Nothing here assumes a process;
each number is a piece of geometry.

RIDGE RADIUS  R_L
    At every pixel, fit a quadric surface (least squares) to the heights in
    the disc of diameter L around it. The fitted surface has a direction of
    strongest downward bending; a pixel is on a crest when the top of the
    fitted surface along that direction falls inside the pixel. There,
    R_L = 1 / |curvature| is the radius of the circle that matches the fitted
    cross-section of the ridge at its top. Every height in the disc goes into
    the fit, so it is the low-noise version of "drop at distance x from the
    crest": the fitted drop at L/2 is L^2 / (8 R).
    A knife edge has no radius of its own: its R_L grows in proportion to L
    (R = 4L / 15s for a tent of flank gradient s). A rounded crest of radius
    R0 reads R0 at every L below its width. So the pair of numbers that
    describes a ridge is R_L and how it changes with L.

VALLEY FALL-WIDTH  F = S_v * W
    At a point on a stream: S_v is the fall between the points 1 km up and
    1 km down the stream, over the straight distance between them. W is the
    width of the floor on the cross-section square to that line: from the
    stream outward each way until the ground starts up a wall (a rise of
    WALL_RISE m within WALL_RUN m). F is the area, in the vertical plane, that
    the floor sweeps through for each metre along the valley; drainage area A
    comes with it so F can be read against what a stream that size usually has.

Heights are 3DEP as served by Mapterhorn z12 (~9 m), resampled to a 10 m Albers
grid by ../scoops/statewide.block_dem.

    landform.py --name brooks --lonlat=-151,68 --size 60000 --out DIR
"""
import argparse, ctypes, json, math, subprocess, sys, time
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates, uniform_filter1d, maximum_filter
from scipy.signal import fftconvolve

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'scoops'))

WALL_RISE, WALL_RUN = 10.0, 50.0      # m: a wall starts where the ground rises this much within this run
XMAX = 3000.0                         # m: longest half cross-section
REACH = 1000.0                        # m along the stream, each way, for the valley line
AMIN_KM2 = 2.0                        # streams smaller than this are not measured
RIDGE_SCALES = (250, 500, 1000, 2000)  # m, disc diameters
PX_PER_L = 24                         # disc diameter in pixels of the grid a scale is fitted on


def load_flow():
    so, src = HERE / '_flow_core.so', HERE / 'flow_core.c'
    if not so.exists() or so.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-o', str(so), str(src)], check=True)
    lib = ctypes.CDLL(str(so))
    P = np.ctypeslib.ndpointer
    lib.flow.argtypes = [P(np.float32, flags='C'), P(np.uint8, flags='C'), ctypes.c_int, ctypes.c_int, ctypes.c_double,
                         P(np.float64, flags='C'), P(np.int32, flags='C'), P(np.float64, flags='C')]
    lib.flow.restype = ctypes.c_int
    return lib


def route(dem, sea):
    ny, nx = dem.shape
    fill, rcv, area = np.empty(dem.size), np.empty(dem.size, np.int32), np.empty(dem.size)
    if load_flow().flow(np.ascontiguousarray(dem, np.float32).ravel(), sea.astype(np.uint8).ravel(), ny, nx, 1e-3, fill, rcv, area):
        raise MemoryError('flow')
    return rcv, area


# ---------------------------------------------------------------- ridges

def quadric(z, npx):
    """Least-squares quadric over a disc npx pixels across, at every pixel.
    Returns gradient (gx, gy) and second derivatives (zxx, zxy, zyy), per pixel unit."""
    r = npx / 2
    k = int(math.ceil(r))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1].astype(np.float64)
    w = (xx * xx + yy * yy <= r * r).astype(np.float64)
    s0, s2, s4, s22 = w.sum(), (w * xx * xx).sum(), (w * xx ** 4).sum(), (w * xx * xx * yy * yy).sum()
    zm = z - z.mean()

    def corr(kern):
        return fftconvolve(zm, (w * kern)[::-1, ::-1], mode='same')
    c0, cx, cy = corr(1), corr(xx), corr(yy)
    cxx, cyy, cxy = corr(xx * xx), corr(yy * yy), corr(xx * yy)
    # z ~ a + b x + c y + d x^2 + e y^2 + f x y: b, c, f separate; [a, d, e] is a fixed 3x3
    m = np.linalg.inv(np.array([[s0, s2, s2], [s2, s4, s22], [s2, s22, s4]]))
    d = m[1, 0] * c0 + m[1, 1] * cxx + m[1, 2] * cyy
    e = m[2, 0] * c0 + m[2, 1] * cxx + m[2, 2] * cyy
    return cx / s2, cy / s2, 2 * d, cxy / s22, 2 * e


def ridge_scale(dem, bad, ice, dx, L):
    """Crest pixels at scale L: arrays x, y (m, in the window, from its NW corner), R (m), on_ice."""
    f = max(1, int(round(L / PX_PER_L / dx)))
    ny, nx = (dem.shape[0] // f) * f, (dem.shape[1] // f) * f

    def down(a):
        return a[:ny, :nx].reshape(ny // f, f, nx // f, f).mean(axis=(1, 3))
    z, px = down(dem.astype(np.float64)), dx * f
    npx = L / px
    gx, gy, zxx, zxy, zyy = quadric(z, npx)
    # the more negative principal curvature and its direction
    half, det = (zxx + zyy) / 2, np.hypot((zxx - zyy) / 2, zxy)
    k1, k2 = half - det, half + det
    th = 0.5 * np.arctan2(2 * zxy, zxx - zyy) + math.pi / 2     # direction of k1
    ex, ey = np.cos(th), np.sin(th)
    with np.errstate(divide='ignore', invalid='ignore'):
        t = -(gx * ex + gy * ey) / k1                           # px from the pixel to the fitted top
    drop = -k1 * npx ** 2 / 8                                   # fitted drop at L/2, m (k1 is m per px^2)
    crest = (k1 < 0) & (np.abs(t) <= 0.6) & (np.abs(k1) > np.abs(k2)) & (drop >= 0.04 * L)
    # nothing whose disc touches the sea, the window edge or missing data
    k = int(math.ceil(npx / 2)) + 1
    badc = maximum_filter(down(bad.astype(np.float64)) > 0, size=2 * k + 1)
    badc[:k] = badc[-k:] = True; badc[:, :k] = badc[:, -k:] = True
    crest &= ~badc
    icef = fftconvolve(down(ice.astype(np.float64)), np.ones((2 * k + 1, 2 * k + 1)) / (2 * k + 1) ** 2, mode='same')
    iy, ix = np.nonzero(crest)
    R = px * px / -k1[iy, ix]
    return dict(x=(ix + .5) * px, y=(iy + .5) * px, R=R, ice=icef[iy, ix] > 0.1, px=px,
                k1=(k1 / (px * px)).astype(np.float32), icef=icef.astype(np.float32))


def ridges(dem, bad, ice, dx):
    out = {}
    for L in RIDGE_SCALES:
        out[L] = ridge_scale(dem, bad, ice, dx, L)
    # how the radius changes with the size of the disc, at the same spot: the fit at 2L, read at the crests of L.
    # 0: a round crest with a radius of its own. 1: straight flanks meeting at an edge (R follows L).
    # More than 1: flanks that flatten outward (a cusp), or the 2L disc reaching the valley floors.
    for L in RIDGE_SCALES:
        lo, hi = out[L], out.get(2 * L)
        if hi is None:
            continue
        k = map_coordinates(hi['k1'], [lo['y'] / hi['px'] - .5, lo['x'] / hi['px'] - .5], order=1, mode='nearest')
        with np.errstate(divide='ignore', invalid='ignore'):
            lo['n'] = np.where(k < 0, np.log2((-1 / k) / lo['R']), np.nan)
    return out


# ---------------------------------------------------------------- valleys

def valleys(dem, rcv, area, ice, sea, dx, stride=5):
    ny, nx = dem.shape
    n = dem.size
    cells = AMIN_KM2 * 1e6 / (dx * dx)
    # main donor: the upstream neighbour with the most area
    donor = np.full(n, -1, np.int32)
    idx = np.nonzero(rcv >= 0)[0]
    idx = idx[np.argsort(area[idx], kind='stable')]
    donor[rcv[idx]] = idx
    ch = np.nonzero((area >= cells) & (rcv >= 0))[0]
    ch = ch[(ch * 2654435761 % 4294967296) % stride == 0]       # a fixed subsample, not a choice
    k = int(round(REACH / dx / 1.2))                            # D8 steps average ~1.2 px
    up, dn = ch.copy(), ch.copy()
    for _ in range(k):
        d = donor[up]; up = np.where(d >= 0, d, up)
        r = rcv[dn]; dn = np.where(r >= 0, r, dn)
    zf = dem.ravel()
    uy, ux, vy, vx = up // nx, up % nx, dn // nx, dn % nx
    cy, cx = ch // nx, ch % nx
    chord = np.hypot(uy - vy, ux - vx) * dx
    ok = (chord >= REACH) & ~sea.ravel()[dn]                    # a full reach, not ending in the sea or the edge
    edge = k + 2
    ok &= (vy > edge) & (vx > edge) & (vy < ny - edge) & (vx < nx - edge)
    Sv = (zf[up] - zf[dn]) / np.maximum(chord, 1)
    ax, ay = (vx - ux) / np.maximum(chord / dx, 1e-9), (vy - uy) / np.maximum(chord / dx, 1e-9)   # unit vector down valley
    px_, py_ = -ay, ax                                          # square to it
    m = int(XMAX / dx)
    offs = np.arange(-m, m + 1)
    X = cx[:, None] + px_[:, None] * offs[None, :]
    Y = cy[:, None] + py_[:, None] * offs[None, :]
    inside = (X >= 0) & (Y >= 0) & (X <= nx - 1) & (Y <= ny - 1)
    prof = map_coordinates(dem, [Y, X], order=1, mode='nearest')
    icep = map_coordinates(ice.astype(np.float32), [Y, X], order=1, mode='nearest')
    prof = uniform_filter1d(prof, 3, axis=1)
    run = int(round(WALL_RUN / dx))
    rise = np.full(prof.shape, np.nan)
    W_side, capped = [], []
    for sgn in (1, -1):
        p = prof[:, m:] if sgn > 0 else prof[:, m::-1]
        ins = inside[:, m:] if sgn > 0 else inside[:, m::-1]
        up_ = p[:, run:] - p[:, :-run]                          # rise over the next WALL_RUN, going outward
        z0 = p[:, :3].min(axis=1)[:, None]
        stop = (up_ >= WALL_RISE) | (p[:, :-run] - z0 <= -WALL_RISE) | ~ins[:, :-run]
        first = np.where(stop.any(axis=1), stop.argmax(axis=1), stop.shape[1])
        W_side.append(first * dx)
        capped.append((first >= stop.shape[1]) | ~np.take_along_axis(ins[:, :-run], np.minimum(first, stop.shape[1] - 1)[:, None], 1)[:, 0])
    W = W_side[0] + W_side[1]
    # ice on the floor: share of the floor's cross-section that is glacier
    j = np.abs(offs)[None, :] * dx
    onfloor = ((offs[None, :] >= 0) & (j <= W_side[0][:, None])) | ((offs[None, :] < 0) & (j <= W_side[1][:, None]))
    icefrac = (icep * onfloor).sum(axis=1) / np.maximum(onfloor.sum(axis=1), 1)
    keep = ok
    return dict(cell=ch[keep], cell_all=ch, x=(cx[keep] + .5) * dx, y=(cy[keep] + .5) * dx, A=area[ch][keep] * dx * dx / 1e6, Sv=Sv[keep], W=W[keep],
                Wl=W_side[1][keep], Wr=W_side[0][keep], open=(capped[0] | capped[1])[keep], ice=icefrac[keep],
                nx_=px_[keep], ny_=py_[keep])


# ---------------------------------------------------------------- spreading

def to_root(ptr):
    """Follow pointers to their end (a cell that points at itself)."""
    for _ in range(26):
        nxt = ptr[ptr]
        if np.array_equal(nxt, ptr):
            break
        ptr = nxt
    return ptr


def spread_ridges(dem, r, dx, fields):
    """Give every cell the values of the crest above it: climb the steepest way up until a crest cell.
    A climb that tops out off the crest set takes the nearest crest within 300 m, else nothing."""
    from scipy.spatial import cKDTree
    ny, nx = dem.shape
    up, _ = route(-dem, np.zeros(dem.shape, bool))              # receivers of the upside-down ground = the way up
    f = int(round(r['px'] / dx))
    cy, cx = (r['y'] / r['px']).astype(int), (r['x'] / r['px']).astype(int)
    owner = np.full(dem.size, -1, np.int32)                     # crest index at each crest cell
    for oy in range(f):
        for ox in range(f):
            yy, xx = cy * f + oy, cx * f + ox
            m = (yy < ny) & (xx < nx)
            owner[yy[m] * nx + xx[m]] = np.nonzero(m)[0]
    self_ = np.arange(dem.size, dtype=np.int32)
    root = to_root(np.where((owner >= 0) | (up < 0), self_, up).astype(np.int32))
    own = owner[root]
    miss = np.unique(root[own < 0])
    if len(miss) and len(cy):
        dist, j = cKDTree(np.c_[r['y'], r['x']]).query(np.c_[(miss // nx + .5) * dx, (miss % nx + .5) * dx], distance_upper_bound=300)
        owner2 = owner.copy(); owner2[miss] = np.where(np.isfinite(dist), j, -1)
        own = owner2[root]
    out = {}
    for name in fields:
        v = np.append(r[name].astype(np.float32), np.nan)
        out[name] = v[own].reshape(ny, nx)
    return out


def spread_valleys(dem, rcv, v, fields):
    """Give every cell the values of the first measured stream point its water reaches."""
    ny, nx = dem.shape
    owner = np.full(dem.size, -2, np.int32)
    owner[v['cell_all']] = -1                                   # measured but not kept: stops the search, gives nothing
    owner[v['cell']] = np.arange(len(v['cell']))
    self_ = np.arange(dem.size, dtype=np.int32)
    root = to_root(np.where((owner > -2) | (rcv < 0), self_, rcv).astype(np.int32))
    own = owner[root]
    out = {}
    for name, val in fields.items():
        a = np.append(val.astype(np.float32), [np.nan, np.nan])  # -1 and -2 both read nan
        out[name] = a[own].reshape(ny, nx)
    return out


def blur(a, dx, sigma, log, f=4, min_w=0.25):
    """Long-wavelength version of a spread raster, on a grid f times coarser: a Gaussian mean (sd sigma, m)
    over the cells that have a value; kept only where at least min_w of the Gaussian's weight had one.
    log: average the logarithm (a geometric mean)."""
    from scipy.ndimage import gaussian_filter
    if log:
        with np.errstate(divide='ignore', invalid='ignore'):
            a = np.log(a)
    ny, nx = a.shape[0] // f * f, a.shape[1] // f * f
    blk = a[:ny, :nx].reshape(ny // f, f, nx // f, f)
    ok = np.isfinite(blk)
    cnt = ok.sum(axis=(1, 3)).astype(np.float64)
    val = np.where(ok, blk, 0).sum(axis=(1, 3), dtype=np.float64)
    s = sigma / (dx * f)
    w = gaussian_filter(cnt, s, mode='constant')
    v = gaussian_filter(val, s, mode='constant')
    full = gaussian_filter(np.full(cnt.shape, float(f * f)), s, mode='constant')
    with np.errstate(divide='ignore', invalid='ignore'):
        out = np.where(w / full >= min_w, v / w, np.nan)
    return (np.exp(out) if log else out).astype(np.float32)


def write_tif(path, a, x0, y1, dx):
    import rasterio
    from rasterio.transform import from_origin
    with rasterio.open(path, 'w', driver='GTiff', width=a.shape[1], height=a.shape[0], count=1, dtype='float32', crs='EPSG:3338',
                       transform=from_origin(x0, y1, dx, dx), nodata=np.nan, compress='deflate', predictor=3, tiled=True) as d:
        d.write(a.astype(np.float32), 1)


# ---------------------------------------------------------------- inputs

def glacier_mask(x0, y1, n, dx, cache):
    """RGI 7 outlines from the dev database, rasterised on the window's grid."""
    import rasterio.features
    from rasterio.transform import from_origin
    from rasterio.warp import transform as tr, transform_geom
    if not cache.exists():
        xs, ys = [x0, x0 + n * dx, x0, x0 + n * dx], [y1, y1, y1 - n * dx, y1 - n * dx]
        lo, la = tr('EPSG:3338', 'EPSG:4326', xs, ys)
        sql = ("SELECT ST_AsGeoJSON(ST_Collect(geom), 5) FROM glaciers WHERE geom && ST_MakeEnvelope(%s,%s,%s,%s,4326)"
               % (min(lo) - .05, min(la) - .05, max(lo) + .05, max(la) + .05))
        code = ("from inventory.views import _get_conn, _put_conn\nc=_get_conn()\ncur=c.cursor()\ncur.execute(\"\"\"%s\"\"\")\n"
                "r=cur.fetchone()[0]\n_put_conn(c)\nopen('/app/data/_landform_glaciers.json','w').write(r or 'null')\n" % sql)
        subprocess.run(['docker', 'exec', 'landslidescience-web-1', 'python', 'manage.py', 'shell', '-c', code], check=True,
                       stdout=subprocess.DEVNULL)
        tmp = HERE.parents[2] / 'data/_landform_glaciers.json'
        cache.write_text(tmp.read_text()); tmp.unlink()
    g = json.loads(cache.read_text())
    if not g:
        return np.zeros((n, n), bool)
    g = transform_geom('EPSG:4326', 'EPSG:3338', g)
    return rasterio.features.rasterize([(g, 1)], out_shape=(n, n), transform=from_origin(x0, y1, dx, dx), dtype='uint8').astype(bool)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--lonlat', required=True)
    ap.add_argument('--size', type=float, default=60000)
    ap.add_argument('--dx', type=float, default=10)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    import statewide as SW
    from rasterio.warp import transform as tr
    t0 = time.time()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    lo, la = [float(v) for v in a.lonlat.split(',')]
    (xc,), (yc,) = tr('EPSG:4326', 'EPSG:3338', [lo], [la])
    n = int(round(a.size / a.dx))
    x0, y1 = xc - a.size / 2, yc + a.size / 2
    demf = out / f'{a.name}_dem.npy'
    if demf.exists():
        dem = np.load(demf)
    else:
        dem = SW.block_dem(x0, y1, n, n, a.dx, tp=512)
        np.save(demf, dem)
    print(f'dem {dem.shape} {dem.min():.0f}..{dem.max():.0f} m  {time.time() - t0:.0f}s', flush=True)
    sea = dem <= 0.5
    ice = glacier_mask(x0, y1, n, a.dx, out / f'{a.name}_glaciers.json')
    print(f'ice {ice.mean() * 100:.1f}%  sea {sea.mean() * 100:.1f}%', flush=True)
    rd = ridges(dem, sea, ice, a.dx)
    for L, r in rd.items():
        land = ~r['ice']
        msg = f'ridge L={L}: {len(r["R"])} crest px, R median {np.median(r["R"][land]) if land.any() else float("nan"):.0f} m (ice-free)'
        if 'n' in r:
            msg += f', n median {np.nanmedian(r["n"][land]):.2f}'
        print(msg, flush=True)
    rcv, area = route(dem, sea)
    v = valleys(dem, rcv, area, ice, sea, a.dx)
    print(f'valley points {len(v["x"])}  {time.time() - t0:.0f}s', flush=True)
    save = dict(x0=x0, y1=y1, dx=a.dx, n=n)
    for L, r in rd.items():
        for key in ('x', 'y', 'R', 'ice', 'n'):   # n: only where a scale 2L was run
            if key in r:
                save[f'r{L}_{key}'] = r[key]
    for key, val in v.items():
        save[f'v_{key}'] = val
    np.savez_compressed(out / f'{a.name}.npz', **save)
    # the values carried onto the slopes: crests down, floors up
    land = ~sea
    sr = spread_ridges(dem, rd[500], a.dx, ['R', 'n'])
    walled = ~v['open']
    F = np.where(walled, np.maximum(v['Sv'], 0) * v['W'], np.nan)
    sv = spread_valleys(dem, rcv, v, dict(F=F, W=np.where(walled, v['W'], np.nan), Sv=np.where(walled, v['Sv'], np.nan),
                                          ice=np.where(walled, v['ice'], np.nan)))
    for key, arr in [('ridge_R500', sr['R']), ('ridge_n500', sr['n']), ('valley_F', sv['F']), ('valley_W', sv['W']),
                     ('valley_Sv', sv['Sv']), ('valley_ice', sv['ice'])]:
        write_tif(out / f'{a.name}_{key}.tif', np.where(land, arr, np.nan), x0, y1, a.dx)
        print(f'{key}: {np.isfinite(arr[land]).mean() * 100:.0f}% of land covered', flush=True)
    np.save(out / f'{a.name}_area.npy', (area.reshape(dem.shape) * a.dx * a.dx / 1e6).astype(np.float32))
    np.save(out / f'{a.name}_ice.npy', ice)
    print(f'done {time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
