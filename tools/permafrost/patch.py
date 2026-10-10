#!/opt/anaconda3/bin/python3
"""Test-patch extraction: the raw 1 km points and the 60 m downscale within
a circle, so the fit can be seen against the published cells at any site.

    patch.py --lon -149.0 --lat 63.4 [--radius 10] [--name cantwell]

Writes permafrost_build/patches/<name>_1km.csv (one row per published 1 km
cell: x, y, elev, northness, gruber MAAT, PZI, obu MAGT, STD, prob, the
fitted lapse b / aspect c at that cell), <name>_60m.csv (every 60 m cell:
x, y, elev, northness, MAAT60, PZI60, MAGT60, prob60) and <name>.png:

  top row     Gruber: MAAT vs elevation (1 km cells as points, 60 m as a
              cloud, the kernel lapse through the patch mean) and PZI vs
              elevation (Gruber's cells, his 0.01 floor visible, against the
              continuous curve the downscale uses)
  bottom row  Obu: MAGT vs elevation coloured by northness, and probability
              vs elevation -- inpainted cells (no published value) drawn
              hollow
"""
import argparse, json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform
from rasterio.windows import from_bounds
from scipy.stats import norm

B = Path('/Volumes/Nunatak/permafrost_build')


def circle_read(path, cx, cy, r):
    """Values, x, y of the raster's cells within the circle."""
    with rasterio.open(path) as d:
        win = from_bounds(cx - r, cy - r, cx + r, cy + r, d.transform)
        win = win.round_offsets().round_lengths()
        a = d.read(1, window=win).astype(float)
        a[(a == d.nodata) | ~np.isfinite(a)] = np.nan
        t = d.window_transform(win)
        rows, cols = np.indices(a.shape)
        xs = t.c + (cols + 0.5) * t.a
        ys = t.f + (rows + 0.5) * t.e
        inside = (xs - cx) ** 2 + (ys - cy) ** 2 <= r ** 2
        return a[inside], xs[inside], ys[inside]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lon', type=float, required=True); ap.add_argument('--lat', type=float, required=True)
    ap.add_argument('--radius', type=float, default=10.0, help='km')
    ap.add_argument('--name', default=None)
    a = ap.parse_args()
    name = a.name or f'{abs(a.lat):.2f}{"N" if a.lat >= 0 else "S"}_{abs(a.lon):.2f}{"W" if a.lon < 0 else "E"}'
    out = B / 'patches'; out.mkdir(exist_ok=True)
    (cx,), (cy,) = transform('EPSG:4326', 'EPSG:3338', [a.lon], [a.lat])
    r = a.radius * 1000

    k1 = {'elev': B / 'src/elev_1km.tif', 'north': B / 'src/northness_1km.tif',
          'maat': B / 'src/gruber_maat_3338_1km.tif', 'pzi': B / 'src/gruber_pzi_3338_1km.tif',
          'magt': B / 'src/obu_magtm_3338_1km.tif', 'std': B / 'src/obu_magtstd_3338_1km.tif',
          'prob': B / 'src/obu_perprob_3338_1km.tif', 'magt_filled': B / 'kfit/obu_magt_filled.tif',
          'gruber_b': B / 'kfit/gruber_b.tif', 'obu_b': B / 'kfit/obu_b.tif', 'obu_c': B / 'kfit/obu_c.tif'}
    k60 = {'elev': B / 'elev60.tif', 'north': B / 'northness60.tif', 'maat60': B / 'out/gruber_maat60.tif',
           'pzi60': B / 'out/gruber_pzi60.tif', 'magt60': B / 'out/obu_magt60.tif', 'prob60': B / 'out/obu_prob60.tif'}
    d1 = {}; d60 = {}
    for k, p in k1.items():
        d1[k], x1, y1 = circle_read(p, cx, cy, r)
    for k, p in k60.items():
        d60[k], x60, y60 = circle_read(p, cx, cy, r)
    hdr1 = ['x', 'y'] + list(k1); hdr60 = ['x', 'y'] + list(k60)
    np.savetxt(out / f'{name}_1km.csv', np.column_stack([x1, y1] + [d1[k] for k in k1]), delimiter=',', header=','.join(hdr1), comments='', fmt='%.4f')
    np.savetxt(out / f'{name}_60m.csv', np.column_stack([x60, y60] + [d60[k] for k in k60]), delimiter=',', header=','.join(hdr60), comments='', fmt='%.4f')
    curve = json.loads((B / 'kfit/pzi_curve.json').read_text())
    T0, s, Tz = curve['T0'], curve['sigma'], curve['T_zero']
    p0 = norm.cdf((T0 - Tz) / s)
    pzi_of = lambda t: np.clip((norm.cdf((T0 - t) / s) - p0) / (1 - p0), 0, 1)

    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 2, figsize=(14, 10))
    ok = np.isfinite(d1['maat']) & np.isfinite(d1['elev'])
    e1, T1 = d1['elev'][ok], d1['maat'][ok]
    b = np.nanmedian(d1['gruber_b'])
    # Gruber MAAT vs elevation
    A = ax[0, 0]
    A.scatter(d60['elev'], d60['maat60'], s=1, c='#c6dbef', label='60 m downscale')
    A.scatter(e1, T1, s=14, c='#08519c', label='Gruber 1 km cells')
    ee = np.linspace(np.nanmin(d60['elev']), np.nanmax(d60['elev']), 50)
    A.plot(ee, np.nanmean(T1) + b * (ee - np.nanmean(e1)), 'k--', label=f'kernel lapse {b * 1000:.2f} °C/km through patch mean')
    A.set_xlabel('elevation (m)'); A.set_ylabel('MAAT (°C)'); A.set_title('Gruber MAAT'); A.legend(fontsize=8)
    # Gruber PZI vs elevation
    A = ax[0, 1]
    A.scatter(d60['elev'], d60['pzi60'], s=1, c='#c6dbef', label='60 m downscale')
    A.scatter(d1['elev'], d1['pzi'], s=14, c='#08519c', label='Gruber 1 km cells (0.01 floor visible)')
    A.plot(ee, pzi_of(np.nanmean(T1) + b * (ee - np.nanmean(e1))), 'k--', label='continuous curve on the lapse')
    A.set_xlabel('elevation (m)'); A.set_ylabel('PZI'); A.set_title('Gruber PZI'); A.legend(fontsize=8); A.set_ylim(-0.02, 1.02)
    # Obu MAGT vs elevation coloured by northness
    A = ax[1, 0]
    sc = A.scatter(d60['elev'], d60['magt60'], s=1, c=d60['north'], cmap='coolwarm_r', vmin=-0.6, vmax=0.6)
    has = np.isfinite(d1['magt'])
    A.scatter(d1['elev'][has], d1['magt'][has], s=16, c=d1['north'][has], cmap='coolwarm_r', vmin=-0.6, vmax=0.6, edgecolors='k', linewidths=0.4, label='Obu 1 km cells')
    A.scatter(d1['elev'][~has], d1['magt_filled'][~has], s=16, facecolors='none', edgecolors='k', linewidths=0.6, label='inpainted (no published value)')
    ob, oc = np.nanmedian(d1['obu_b']), np.nanmedian(d1['obu_c'])
    A.plot(ee, np.nanmean(d1['magt'][has]) + ob * (ee - np.nanmean(d1['elev'][has])), 'k--', label=f'lapse {ob * 1000:.2f} °C/km, northness {oc:.2f} °C')
    plt.colorbar(sc, ax=A, fraction=0.04, label='northness (blue = north-facing)')
    A.set_xlabel('elevation (m)'); A.set_ylabel('MAGT (°C)'); A.set_title('Obu MAGT'); A.legend(fontsize=8)
    # Obu probability vs elevation
    A = ax[1, 1]
    A.scatter(d60['elev'], d60['prob60'], s=1, c='#dadaeb', label='60 m downscale')
    A.scatter(d1['elev'][has], d1['prob'][has], s=16, c='#54278f', label='Obu 1 km cells')
    A.set_xlabel('elevation (m)'); A.set_ylabel('permafrost probability'); A.set_title('Obu probability'); A.legend(fontsize=8); A.set_ylim(-0.02, 1.02)
    fig.suptitle(f'{name}: {a.radius:.0f} km patch at {a.lat:.3f} N {abs(a.lon):.3f} W — {ok.sum()} Gruber cells, {has.sum()} Obu cells, {len(d60["elev"])} 60 m cells')
    plt.tight_layout(); plt.savefig(out / f'{name}.png', dpi=90)
    print('wrote', out / f'{name}.png', 'and the two CSVs')


if __name__ == '__main__':
    main()
