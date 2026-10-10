#!/opt/anaconda3/bin/python3
"""Kernel-weighted lapse fits for the permafrost downscale (replaces the 10 km
block fits of fit_blocks.py, 2026-10-09, after Hig asked for a smooth,
physically reasonable lapse surface rather than blocks).

At EVERY 1 km cell the regression is fitted over its neighbourhood with
Gaussian weights (SIGMA_KM), so the coefficient fields are continuous and as
smooth as the kernel -- no blocks, no seams -- while contrasts wider than the
kernel (interior vs. coast) survive. The weighted moments are convolutions
(scipy gaussian_filter of w, w*e, w*e^2, w*T, w*e*T ...), so the whole state
is one pass. Nodata cells carry weight 0, which is what lets the Obu fit be
estimated THROUGH glaciers and the near-glacier gaps from the valid cells
around them.

    Gruber:  MAAT = a + b (elev - emean)                    b = lapse, C/m
    Obu:     MAGT = a + b (elev - emean) + c (north - nmean) c = aspect term, C

Where the neighbourhood has too little relief (sd of elevation under the
kernel < RELIEF_MIN) or too little aspect range to determine a coefficient,
the WIDE kernel's (SIGMA_WIDE_KM) value is used; where that also fails, the
domain-wide weighted median. Coefficients are clipped to physical ranges.

The 1 km value itself is preserved: apply_downscale.py anchors the 60 m
field on the published 1 km value (T60 = T1k + b (e60 - e1k) [+ c (n60 -
n1k)]), with the kernel's own fitted value a + b(e - emean) standing in for
T1k only where the product has no data (Obu over ice). So the output here
is: the coefficient fields, the INPAINTED 1 km Obu MAGT and STD, and the
fitted PZI(MAAT) curve.

PZI(MAAT): Gruber's published index is Phi((T0 - MAAT)/sigma) to 0.002 rms
(T0 -4.75, sigma 2.55 on 1.87 M Alaskan cells), but is floored to 0.01 below
0.1 and cut to 0 above MAAT ~0 C, which puts a step in any elevation
profile. The curve used here is that CDF rescaled to reach exactly 0 at
MAAT = T_ZERO (0 C, Gruber's own cut), refitted to his >= 0.1 cells:
    PZI = (Phi((T0 - T)/s) - Phi((T0 - T_ZERO)/s)) / (1 - Phi((T0 - T_ZERO)/s)),  0 above T_ZERO.

Outputs (1 km, EPSG:3338, permafrost_build/kfit/):
    gruber_b.tif  gruber_scale.tif (1 = local kernel, 2 = wide, 3 = median)
    obu_b.tif obu_c.tif obu_scale.tif obu_magt_filled.tif obu_std_filled.tif
    pzi_curve.json  diagnostics.png
"""
import json
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import gaussian_filter
from scipy.optimize import least_squares
from scipy.stats import norm

B = Path('/Volumes/Nunatak/permafrost_build')
SRC, OUT = B / 'src', B / 'kfit'
SIGMA_KM, SIGMA_WIDE_KM = 15.0, 50.0
RELIEF_MIN = 75.0           # m, weighted sd of elevation under the kernel
NORTH_SD_MIN = 0.04         # weighted sd of northness
W_MIN = 50.0                # minimum summed kernel weight (cells-equivalent)
GRUBER_B_RANGE = (-0.010, 0.002)    # C/m
OBU_B_RANGE = (-0.010, 0.005)
OBU_C_RANGE = (-6.0, 3.0)           # C per unit northness
T_ZERO = 0.0                        # C, where the PZI curve reaches 0
NODATA = -9999.0


def read(name):
    with rasterio.open(SRC / name) as d:
        a = d.read(1).astype(np.float64)
        a[(a == d.nodata) | ~np.isfinite(a)] = np.nan
        return a, d.profile


def G(a, s):
    return gaussian_filter(a, s, mode='constant', cval=0.0, truncate=3.0)


def moments(w, fields, s):
    """Kernel-weighted sums of every field and pairwise product."""
    # gaussian_filter normalises its kernel, so G(w) is the weighted FRACTION
    # of valid cells; the summed weight (cells-equivalent) is that times the
    # kernel's own sum, 2 pi s^2.
    m = {'1': G(w, s), 'n_eff': G(w, s) * 2 * np.pi * s ** 2}
    for k, f in fields.items():
        m[k] = G(w * f, s)
    keys = list(fields)
    for i, k in enumerate(keys):
        for l in keys[i:]:
            m[k + l] = G(w * fields[k] * fields[l], s)
    return m


def fit_gruber(w, e, T, s):
    m = moments(w, {'e': e, 'T': T}, s)
    S1 = np.maximum(m['1'], 1e-9)
    em, Tm = m['e'] / S1, m['T'] / S1
    Vee = m['ee'] / S1 - em ** 2
    VeT = m['eT'] / S1 - em * Tm
    ok = (m['n_eff'] >= W_MIN) & (Vee > RELIEF_MIN ** 2)
    b = np.where(ok, VeT / np.where(Vee > 0, Vee, 1), np.nan)
    return b, em, Tm, ok, m['n_eff']


def fit_obu(w, e, n, T, s):
    m = moments(w, {'e': e, 'n': n, 'T': T}, s)
    S1 = np.maximum(m['1'], 1e-9)
    em, nm, Tm = m['e'] / S1, m['n'] / S1, m['T'] / S1
    Vee = m['ee'] / S1 - em ** 2
    Vnn = m['nn'] / S1 - nm ** 2
    Ven = m['en'] / S1 - em * nm
    VeT = m['eT'] / S1 - em * Tm
    VnT = m['nT'] / S1 - nm * Tm
    det = Vee * Vnn - Ven ** 2
    ok_b = (m['n_eff'] >= W_MIN) & (Vee > RELIEF_MIN ** 2)
    ok_c = ok_b & (Vnn > NORTH_SD_MIN ** 2) & (det > 1e-12 * np.maximum(Vee * Vnn, 1e-12))
    # full 2-parameter solve where aspect is determined, elevation-only otherwise
    b2 = (Vnn * VeT - Ven * VnT) / np.where(det != 0, det, 1)
    c2 = (Vee * VnT - Ven * VeT) / np.where(det != 0, det, 1)
    b1 = VeT / np.where(Vee > 0, Vee, 1)
    b = np.where(ok_c, b2, np.where(ok_b, b1, np.nan))
    c = np.where(ok_c, c2, np.nan)
    return b, c, em, nm, Tm, ok_b, ok_c, m['n_eff']


def fallback(local, wide, w_wide, ok_local, ok_wide, lo, hi):
    """Local where determined, wide where only that is, else the weighted
    median of the local estimates. Returns the field and a scale code."""
    med = np.nanmedian(local[ok_local]) if ok_local.any() else 0.0
    out = np.where(ok_local, local, np.where(ok_wide, wide, med))
    scale = np.where(ok_local, 1, np.where(ok_wide, 2, 3)).astype(np.uint8)
    return np.clip(out, lo, hi), scale


def write(name, a, prof, dtype='float32', nodata=NODATA):
    p = prof.copy()
    p.update(dtype=dtype, nodata=nodata, compress='ZSTD', predictor=3 if dtype == 'float32' else 2,
             tiled=True, blockxsize=256, blockysize=256)
    arr = a.astype(dtype)
    if dtype == 'float32':
        arr[~np.isfinite(a)] = nodata
    with rasterio.open(OUT / name, 'w', **p) as d:
        d.write(arr, 1)


def fit_curve(maat, pzi):
    ok = np.isfinite(maat) & np.isfinite(pzi) & (pzi >= 0.1) & (pzi <= 0.999)
    T, P = maat[ok], pzi[ok]

    def curve(x, t):
        T0, s = x
        p0 = norm.cdf((T0 - T_ZERO) / s)
        return np.clip((norm.cdf((T0 - t) / s) - p0) / (1 - p0), 0, 1)

    r = least_squares(lambda x: curve(x, T) - P, [-4.75, 2.55])
    T0, s = r.x
    res = curve(r.x, T) - P
    print(f'PZI curve on {len(T):,} cells (PZI >= 0.1): T0 = {T0:.3f} C, sigma = {s:.3f} C, zero at {T_ZERO} C; '
          f'residual sd {res.std():.4f}, max |res| {np.abs(res).max():.3f}')
    return {'form': 'rescaled normal CDF, see kernel_fit.py', 'T0': float(T0), 'sigma': float(s), 'T_zero': T_ZERO,
            'n_fit': int(len(T)), 'residual_sd': float(res.std())}


def main():
    OUT.mkdir(exist_ok=True)
    e, prof = read('elev_1km.tif')
    n, _ = read('northness_1km.tif')
    maat, _ = read('gruber_maat_3338_1km.tif')
    pzi, _ = read('gruber_pzi_3338_1km.tif')
    magt, _ = read('obu_magtm_3338_1km.tif')
    std, _ = read('obu_magtstd_3338_1km.tif')
    s_loc, s_wide = SIGMA_KM, SIGMA_WIDE_KM          # 1 km cells
    land = np.isfinite(maat) & np.isfinite(e)

    # ---- Gruber ---------------------------------------------------------
    w = (np.isfinite(maat) & np.isfinite(e)).astype(np.float64)
    ez, Tz = np.nan_to_num(e), np.nan_to_num(maat)
    bL, _, _, okL, _ = fit_gruber(w, ez, Tz, s_loc)
    bW, _, _, okW, _ = fit_gruber(w, ez, Tz, s_wide)
    gb, gscale = fallback(bL, bW, None, okL, okW, *GRUBER_B_RANGE)
    gb[~land] = np.nan
    print(f'Gruber lapse: local kernel {np.mean(gscale[land] == 1):.0%}, wide {np.mean(gscale[land] == 2):.0%}, median {np.mean(gscale[land] == 3):.0%}; '
          f'b median {np.nanmedian(gb[land]) * 1000:.2f} C/km, p10/p90 {np.nanpercentile(gb[land], 10) * 1000:.2f}/{np.nanpercentile(gb[land], 90) * 1000:.2f}')
    write('gruber_b.tif', gb, prof)
    write('gruber_scale.tif', np.where(land, gscale, 0), prof, 'uint8', 0)

    # ---- Obu ------------------------------------------------------------
    w = (np.isfinite(magt) & np.isfinite(e) & np.isfinite(n)).astype(np.float64)
    nz, Mz = np.nan_to_num(n), np.nan_to_num(magt)
    bL, cL, emL, nmL, TmL, okbL, okcL, S1L = fit_obu(w, ez, nz, Mz, s_loc)
    bW, cW, emW, nmW, TmW, okbW, okcW, S1W = fit_obu(w, ez, nz, Mz, s_wide)
    ob, obscale = fallback(bL, bW, None, okbL, okbW, *OBU_B_RANGE)
    oc, ocscale = fallback(cL, cW, None, okcL, okcW, *OBU_C_RANGE)
    ob[~land] = np.nan; oc[~land] = np.nan
    # inpaint MAGT and STD at 1 km where Obu has no data: the kernel's own
    # fitted value at the cell's elevation and aspect; wide kernel where the
    # local one has no support (deep inside a large icefield)
    fitL = TmL + ob * (ez - emL) + oc * (nz - nmL)
    fitW = TmW + ob * (ez - emW) + oc * (nz - nmW)
    filled = np.where(np.isfinite(magt), magt, np.where(S1L >= W_MIN, fitL, fitW))
    filled[~land] = np.nan
    ws = (np.isfinite(std)).astype(np.float64)
    stdL = G(ws * np.nan_to_num(std), s_loc) / np.maximum(G(ws, s_loc), 1e-9)
    stdW = G(ws * np.nan_to_num(std), s_wide) / np.maximum(G(ws, s_wide), 1e-9)
    std_filled = np.where(np.isfinite(std), std, np.where(G(ws, s_loc) * 2 * np.pi * s_loc ** 2 >= W_MIN, stdL, stdW))
    std_filled[~land] = np.nan
    nfill = int((land & ~np.isfinite(magt)).sum())
    print(f'Obu lapse: local {np.mean(obscale[land] == 1):.0%}, wide {np.mean(obscale[land] == 2):.0%}, median {np.mean(obscale[land] == 3):.0%}; '
          f'b median {np.nanmedian(ob[land]) * 1000:.2f} C/km, p10/p90 {np.nanpercentile(ob[land], 10) * 1000:.2f}/{np.nanpercentile(ob[land], 90) * 1000:.2f}')
    print(f'Obu aspect: local {np.mean(ocscale[land] == 1):.0%}, wide {np.mean(ocscale[land] == 2):.0%}, median {np.mean(ocscale[land] == 3):.0%}; '
          f'c median {np.nanmedian(oc[land]):.2f} C, p10/p90 {np.nanpercentile(oc[land], 10):.2f}/{np.nanpercentile(oc[land], 90):.2f}; north colder in {np.mean(oc[land] < 0):.0%}')
    print(f'Obu MAGT inpainted on {nfill:,} land cells without data ({nfill / land.sum():.1%} of land); '
          f'inpainted median {np.nanmedian(filled[land & ~np.isfinite(magt)]):.2f} C')
    write('obu_b.tif', ob, prof); write('obu_c.tif', oc, prof)
    write('obu_scale.tif', np.where(land, obscale, 0), prof, 'uint8', 0)
    write('obu_magt_filled.tif', filled, prof); write('obu_std_filled.tif', std_filled, prof)

    # ---- PZI curve --------------------------------------------------------
    curve = fit_curve(maat, pzi)
    (OUT / 'pzi_curve.json').write_text(json.dumps(curve, indent=1))

    # ---- diagnostics figure ----------------------------------------------
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 3, figsize=(17, 9))
    panels = [(gb * 1000, 'Gruber MAAT lapse (°C/km), σ=15 km kernel', (-8, 0), 'viridis'),
              (np.where(land, gscale, np.nan).astype(float), 'Gruber: 1 local · 2 wide · 3 median', (1, 3), 'Set1'),
              (filled, 'Obu MAGT, inpainted over ice (°C)', (-12, 6), 'RdBu_r'),
              (ob * 1000, 'Obu MAGT lapse (°C/km)', (-5, 5), 'RdBu_r'),
              (oc, 'Obu northness coefficient (°C)', (-2, 2), 'RdBu_r'),
              (np.where(land, obscale, np.nan).astype(float), 'Obu: 1 local · 2 wide · 3 median', (1, 3), 'Set1')]
    for a, (v, t, lim, cm) in zip(ax.ravel(), panels):
        im = a.imshow(v, vmin=lim[0], vmax=lim[1], cmap=cm); a.set_title(t); a.axis('off'); plt.colorbar(im, ax=a, fraction=0.03)
    plt.suptitle('Kernel-weighted lapse fits (kernel_fit.py)'); plt.tight_layout()
    plt.savefig(OUT / 'diagnostics.png', dpi=90)
    print('wrote', OUT / 'diagnostics.png')


if __name__ == '__main__':
    main()
