"""Measure the annual cycle as HARMONICS rather than assuming a sinusoid.

Real glacier seasonality is often coherent but not sinusoidal — Hubbard's
velocity peak is bimodal, and a single sine fitted to that lands somewhere
between the two peaks with the wrong height and no structure. A truncated
Fourier series in the annual phase handles any repeatable shape: harmonic 1
is the familiar annual sinusoid, harmonic 2 gives two peaks per year,
harmonic 3 sharpens troughs, and so on.

The decomposition runs on the TV monthly series (tools/fit_monthly_tv.py),
which assumes nothing about cyclicity, so the harmonics are MEASURED from
the data rather than imposed on it:

    v(t) = secular(t) + sum_k [ a_k cos(2 pi k t) + b_k sin(2 pi k t) ] + resid

`secular` is a centred 13-month moving average, which removes the annual
band and everything slower, so the harmonic fit sees only the sub-annual
anomaly. Fitting the two simultaneously would let a wandering secular term
absorb genuine seasonality.

EVERY harmonic must earn its place by REPLICATION, not by fit improvement:
each is refitted on odd and even years separately and kept only if the two
halves agree in phase and magnitude. Adding harmonics blindly is just a
richer way to overfit — the exact failure this exercise set out to test.

Outputs <name>_harm.npz: coefficients per cell per harmonic, variance
explained cumulatively, and the number of harmonics retained.

Usage: python tools/harmonic_cycle.py [--k=3]
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent / 'data' / 'glaciers' / 'experiments'
K_MAX = 3
SECULAR_WIN = 13           # months; odd, centred — kills the annual band
MIN_MONTHS = 60
AGREE_COS = 0.6            # phase agreement between odd/even year halves
AGREE_RATIO = 2.5          # magnitude ratio tolerance
MIN_AMP = 3.0              # m/yr — below the measurement floor


def moving_average(a, win):
    """Centred moving average along axis 0, edge-padded."""
    pad = win // 2
    ap = np.pad(a, ((pad, pad), (0, 0)), mode='edge')
    ker = np.ones(win) / win
    out = np.empty_like(a)
    for j in range(a.shape[1]):
        out[:, j] = np.convolve(ap[:, j], ker, mode='valid')
    return out


def harmonic_design(t, k):
    cols = [np.ones_like(t)]
    for h in range(1, k + 1):
        cols.append(np.cos(2 * np.pi * h * t))
        cols.append(np.sin(2 * np.pi * h * t))
    return np.column_stack(cols)


def main(kmax, site='columbia'):
    h = json.load(open(ROOT / f'{site}_monthly.json'))
    g = h['grid']; ny, nx, nm = g['ny'], g['nx'], h['n_months']; nod = h['nodata']
    raw = gzip.open(ROOT / f'{site}_monthly.bin.gz', 'rb').read()
    n = nm * ny * nx
    vx = np.frombuffer(raw, '<i2', count=n, offset=0).astype('f4').reshape(nm, ny, nx)
    vy = np.frombuffer(raw, '<i2', count=n, offset=n * 2).astype('f4').reshape(nm, ny, nx)
    vx = np.where(vx == nod, np.nan, vx); vy = np.where(vy == nod, np.nan, vy)
    t = h['t0'] + (np.arange(nm) + 0.5) / 12.0
    ok = np.isfinite(vx).all(axis=0) & np.isfinite(vy).all(axis=0)
    idx = np.nonzero(ok.ravel())[0]
    print(f'== harmonic decomposition of the assumption-free series: '
          f'{idx.size} cells, {nm} months, k<= {kmax}')

    X = vx.reshape(nm, -1)[:, idx]
    Y = vy.reshape(nm, -1)[:, idx]
    # Remove secular variation first so harmonics cannot absorb it.
    ax = X - moving_average(X, SECULAR_WIN)
    ay = Y - moving_average(Y, SECULAR_WIN)

    yr = np.floor(t).astype(int)
    hA = (yr % 2 == 0); hB = ~hA
    ncell = idx.size
    coefs = np.zeros((ncell, kmax, 4), np.float32)     # ax_cos, ax_sin, ay_cos, ay_sin
    keep = np.zeros((ncell, kmax), bool)
    var_exp = np.zeros((ncell, kmax), np.float32)

    tot = (ax ** 2 + ay ** 2).sum(axis=0)
    tot = np.maximum(tot, 1e-9)
    fitx = np.zeros_like(ax); fity = np.zeros_like(ay)

    for k in range(1, kmax + 1):
        c = np.cos(2 * np.pi * k * t)[:, None]
        s = np.sin(2 * np.pi * k * t)[:, None]
        # Orthogonal projection (cos/sin are near-orthogonal over whole years)
        norm = (c * c).sum()
        def proj(res):
            return (res * c).sum(axis=0) / norm, (res * s).sum(axis=0) / norm
        rx = ax - fitx; ry = ay - fity
        axc, axs = proj(rx); ayc, ays = proj(ry)

        # replication test on this harmonic alone
        def half(mask, res):
            cm = c[mask]; sm = s[mask]; nm_ = (cm * cm).sum()
            return ((res[mask] * cm).sum(axis=0) / nm_,
                    (res[mask] * sm).sum(axis=0) / nm_)
        a1 = np.stack(half(hA, rx) + half(hA, ry))       # [4, ncell]
        a2 = np.stack(half(hB, rx) + half(hB, ry))
        n1 = np.linalg.norm(a1, axis=0); n2 = np.linalg.norm(a2, axis=0)
        cos = (a1 * a2).sum(axis=0) / np.maximum(n1 * n2, 1e-9)
        ratio = np.maximum(n1, n2) / np.maximum(np.minimum(n1, n2), 1e-9)
        amp = np.sqrt(axc ** 2 + axs ** 2 + ayc ** 2 + ays ** 2)
        k_ok = (cos > AGREE_COS) & (ratio < AGREE_RATIO) & (amp > MIN_AMP)
        keep[:, k - 1] = k_ok
        coefs[:, k - 1, 0] = np.where(k_ok, axc, 0)
        coefs[:, k - 1, 1] = np.where(k_ok, axs, 0)
        coefs[:, k - 1, 2] = np.where(k_ok, ayc, 0)
        coefs[:, k - 1, 3] = np.where(k_ok, ays, 0)
        fitx = fitx + np.where(k_ok, axc, 0) * c + np.where(k_ok, axs, 0) * s
        fity = fity + np.where(k_ok, ayc, 0) * c + np.where(k_ok, ays, 0) * s
        res = ((ax - fitx) ** 2 + (ay - fity) ** 2).sum(axis=0)
        var_exp[:, k - 1] = 1 - res / tot
        print(f'   harmonic {k}: retained in {100 * k_ok.mean():5.1f}% of cells, '
              f'cumulative variance explained (median) {np.median(var_exp[:, k-1]):.3f}')

    spd = np.hypot(np.nanmean(X, axis=0), np.nanmean(Y, axis=0))
    print(f'\n{"band m/yr":>14}{"cells":>8}{"h1":>7}{"h1+2":>7}{"h1+2+3":>9}'
          f'{"% with h2":>11}{"% with h3":>11}')
    for lo, hi in ((5, 30), (30, 100), (100, 500), (500, 20000)):
        m = (spd >= lo) & (spd < hi)
        if m.sum() < 30:
            continue
        print(f'{lo:>6}-{hi:<7}{m.sum():>8}'
              f'{np.median(var_exp[m, 0]):>7.2f}'
              f'{np.median(var_exp[m, min(1, kmax-1)]):>7.2f}'
              f'{np.median(var_exp[m, kmax-1]):>9.2f}'
              f'{100 * keep[m, min(1, kmax-1)].mean():>10.0f}%'
              f'{100 * keep[m, kmax-1].mean():>10.0f}%')

    np.savez(ROOT / f'{site}_harm.npz', coefs=coefs, keep=keep, var_exp=var_exp,
             idx=idx, grid=json.dumps(g), t0=h['t0'], kmax=kmax)
    print(f'\n   wrote {site}_harm.npz')


if __name__ == '__main__':
    k = K_MAX; site = 'columbia'
    for a in sys.argv[1:]:
        if a.startswith('--k='):
            k = int(a.split('=', 1)[1])
        elif a.startswith('--site='):
            site = a.split('=', 1)[1]
    main(k, site)
