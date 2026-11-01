#!/opt/anaconda3/bin/python3
"""Slope at a fixed horizontal scale: the steepest diameter (Hig, 2026-11-01).

For a pixel p and a length L, draw the circle of diameter L centred on p.
Every diameter of it is a straight line of horizontal length L bisected by
p; its slope is |z(p + L/2 u) - z(p - L/2 u)| / L. The pixel's value is
the steepest of those diameters, as an angle in degrees:

    S_L(p) = atan( max_theta |z(p + r u_theta) - z(p - r u_theta)| / L ),
    r = L/2, theta over [0, pi) in NDIR steps.

Endpoint heights are bilinear. For a plane of gradient G the steepest
diameter is the fall line and S_L = atan(G) exactly; with NDIR = 24 the
worst sampled direction is 3.75 deg off the fall line, so a plane reads
at most 0.2 % shallow (cos 3.75 deg) -- checked by `selftest`.

The grid is Web Mercator (the Mapterhorn tile grid itself, no
reprojection). Mercator is conformal, so a circle on the ground is a
circle in pixels; only its radius in pixels changes with latitude, and it
is taken per row: r_px = r / (res * cos(lat)).

Which grid a scale is computed on is the caller's choice (bake_slope.py):
about L/12 per pixel, on elevation block-averaged from z12, so a 2 km
diameter reads two ~140 m mean heights rather than two 9 m points.

The finest scale is NOT this: it is ordinary slope (Horn's gradient,
`horn`) on the z12 grid, as gdaldem computes it (Hig: the highest
resolution is simple slope as usually calculated).
"""
import math

import numpy as np

NDIR = 24
WORLD = 2 * math.pi * 6378137.0


def mercator_res(z, tile=512):
    """Metres per pixel at the equator for zoom z and a `tile`-px tile."""
    return WORLD / (2 ** z * tile)


def row_lat(y_px, z, tile=512):
    """Latitude (deg) of pixel-row centres y_px (global pixel rows at zoom z)."""
    n = 2 ** z * tile
    return np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (np.asarray(y_px) + 0.5) / n))))


def _bilinear(a, yy, xx):
    """a sampled at float coords (yy, xx), NaN outside or touching NaN."""
    h, w = a.shape
    y0 = np.floor(yy).astype(np.int64); x0 = np.floor(xx).astype(np.int64)
    fy = yy - y0; fx = xx - x0
    ok = (y0 >= 0) & (x0 >= 0) & (y0 + 1 < h) & (x0 + 1 < w)
    y0c = np.clip(y0, 0, h - 2); x0c = np.clip(x0, 0, w - 2)
    v = (a[y0c, x0c] * (1 - fy) * (1 - fx) + a[y0c, x0c + 1] * (1 - fy) * fx +
         a[y0c + 1, x0c] * fy * (1 - fx) + a[y0c + 1, x0c + 1] * fy * fx)
    v[~ok] = np.nan
    return v


def diameter_slope(z, L, px_m, ndir=NDIR):
    """S_L in degrees for elevation array z (NaN = void).

    px_m: ground metres per pixel, a scalar or one value per row (Mercator).
    Pixels whose circle reaches a void or the array edge are NaN, so the
    caller must pad by the radius (bake: a halo from neighbouring tiles).
    """
    h, w = z.shape
    px = np.broadcast_to(np.asarray(px_m, dtype=np.float64).reshape(-1, 1), (h, 1))
    r = (L / 2.0) / px                         # radius in pixels, per row
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    best = np.zeros((h, w))
    bad = np.zeros((h, w), bool)
    for k in range(ndir):
        t = math.pi * k / ndir
        dy = r * math.sin(t); dx = r * math.cos(t)
        a = _bilinear(z, yy + dy, xx + dx)
        b = _bilinear(z, yy - dy, xx - dx)
        d = np.abs(a - b)
        bad |= np.isnan(d)
        np.fmax(best, d, out=best)
    s = np.degrees(np.arctan(best / L))
    s[bad | np.isnan(z)] = np.nan
    return s


def diameter_slope_tile(z, L, r_px, row_m):
    """S_L (deg) on one tile, with a constant radius in pixels.

    The bake and the browser's calculation (slope_scales.js) both use this
    form, because it is shifted array slices rather than per-pixel lookups:
    about 30 times faster than `diameter_slope`. z is padded by
    ceil(r_px) + 1 on every side and the result is the unpadded tile. r_px is
    the tile's own (L/2 over the pixel size at its centre row). Mercator's
    scale changes across a tile, so a row's diameter is 2 r_px row_m[i] long
    on the ground, and the difference is divided by that actual length: a
    plane still reads exactly. row_m: ground metres per pixel per UNPADDED row.
    """
    pad = int(math.ceil(r_px)) + 1
    h, w = z.shape[0] - 2 * pad, z.shape[1] - 2 * pad
    best = np.zeros((h, w), np.float32)
    bad = np.zeros((h, w), bool)

    def sample(dy, dx):
        iy, ix = int(math.floor(dy)), int(math.floor(dx))
        fy, fx = dy - iy, dx - ix
        y0, x0 = pad + iy, pad + ix
        a = z[y0:y0 + h, x0:x0 + w]; b = z[y0:y0 + h, x0 + 1:x0 + 1 + w]
        c = z[y0 + 1:y0 + 1 + h, x0:x0 + w]; d = z[y0 + 1:y0 + 1 + h, x0 + 1:x0 + 1 + w]
        return (a * (1 - fy) * (1 - fx) + b * (1 - fy) * fx + c * fy * (1 - fx) + d * fy * fx)

    for k in range(NDIR):
        t = math.pi * k / NDIR
        dy, dx = r_px * math.sin(t), r_px * math.cos(t)
        d = np.abs(sample(dy, dx) - sample(-dy, -dx))
        bad |= np.isnan(d)
        np.fmax(best, d, out=best)
    length = (2 * r_px * np.asarray(row_m, dtype=np.float32)).reshape(-1, 1)
    s = np.degrees(np.arctan(best / length))
    s[bad] = np.nan
    return s


def horn(z, px_m):
    """Ordinary slope (deg), Horn's 3x3 gradient, as gdaldem slope. NaN edge."""
    h, w = z.shape
    px = np.broadcast_to(np.asarray(px_m, dtype=np.float64).reshape(-1, 1), (h, 1))
    p = np.pad(z, 1, constant_values=np.nan)
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, hh, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * px)
    dzdy = ((g + 2 * hh + i) - (a + 2 * b + c)) / (8 * px)
    return np.degrees(np.arctan(np.hypot(dzdx, dzdy)))


def block_mean(z, k):
    """Mean of k x k blocks (NaN-aware: a block with any data is its mean)."""
    h, w = (z.shape[0] // k) * k, (z.shape[1] // k) * k
    b = z[:h, :w].reshape(h // k, k, w // k, k)
    with np.errstate(invalid='ignore'):
        return np.nanmean(b, axis=(1, 3))


def selftest():
    """A plane must read its own slope at every scale, direction and latitude."""
    rng = np.random.default_rng(1)
    worst = 0.0
    for _ in range(20):
        g = rng.uniform(0.05, 1.5)               # gradient, m/m
        az = rng.uniform(0, 2 * math.pi)
        lat = rng.uniform(52, 71)
        px = mercator_res(12) * math.cos(math.radians(lat))
        n = 200
        yy, xx = np.mgrid[0:n, 0:n] * px
        z = g * (np.cos(az) * xx + np.sin(az) * yy)
        for L in (60, 200, 600):
            s = diameter_slope(z, L, px)
            m = np.nanmedian(s)
            truth = math.degrees(math.atan(g))
            worst = max(worst, (truth - m) / truth)
            assert truth * (1 - 0.003) <= m <= truth * (1 + 1e-9), (g, az, lat, L, m, truth)
            pad = int(math.ceil((L / 2) / px)) + 1
            s2 = diameter_slope_tile(z.astype(np.float32), L, (L / 2) / px, np.full(n - 2 * pad, px))
            assert abs(np.median(s2) - m) < 0.01, (L, np.median(s2), m)
        hs = np.nanmedian(horn(z, px))
        assert abs(hs - math.degrees(math.atan(g))) < 1e-6, hs
    print(f'selftest ok: worst plane under-read {worst * 100:.3f} % (bound 0.214 %)')


if __name__ == '__main__':
    selftest()
