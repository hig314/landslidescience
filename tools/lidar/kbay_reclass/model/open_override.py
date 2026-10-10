"""Open-surface override on top of the blend (Hig, 2026-09-25).
A 1 m cell is OPEN SURFACE when its returns lie on one surface: n >= 6, plane-fit RMS <= RMS_MAX,
>= 80% of returns within 0.10 m of the plane, >= 90% single-return pulses (canopy splits pulses).
Measured: open ground (bare in both surveys) RMS 1.5-4 cm, 100% within 0.1 m, 100% single
returns, at ANY slope; brush RMS 0.65-1.5 m, 4-12% within 0.1 m, 32-62% single.
In open cells every return within 0.15 m of the cell plane is ground; their TIN replaces the
blend there, feathered over 2 m. Elsewhere the blend is unchanged."""
import json, os, sys
import numpy as np
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import v2, sitekit as K, surfacefit as SF
import pyarrow as pa, pyarrow.feather as feather
RMS_MAX = float(sys.argv[1]) if len(sys.argv) > 1 else 0.08
SLOPE_MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
CURV_MIN = float(sys.argv[3]) if len(sys.argv) > 3 else 0.4
res = {}
for s in v2.SITES:
    S, f, r, use = SF.features(s); d = f'site/{s}'
    openc = (f['n'] >= 6) & (f['rms'] <= RMS_MAX) & (f['frac10'] >= 0.8) & (f['single'] >= 0.9)
    idx = np.flatnonzero(use)
    on = openc.ravel()[S.cid[idx]] & (np.abs(r) <= 0.15)
    sel = idx[on]
    X = np.r_[S.x[sel], S.x[S.water]]; Y = np.r_[S.y[sel], S.y[S.water]]; Z = np.r_[S.z[sel], S.z[S.water]]
    fp = f'{d}/open.feather'
    feather.write_feather(pa.table({'X': X, 'Y': Y, 'Z': Z, 'Classification': np.full(len(X), 2, np.uint8)}), fp)
    K.tin({"type": "readers.arrow", "filename": fp}, f'{d}/dtm_open.tif', S.S, d, 'dtm_open'); os.remove(fp)
    O, _ = K.rd(f'{d}/dtm_open.tif'); Bl, _ = K.rd(f'{d}/dtm_blend.tif')
    wgt = np.clip(1 - ndimage.distance_transform_edt(~openc) / 2.0, 0, 1)
    # Raise the blend only where truncation happens (steep or curved, from KBay's own surface);
    # a tight single-return surface on gentle ground can be a herb/fern mat 0.2-0.5 m up.
    # Lowering is allowed anywhere: a tight surface BELOW the blend is the safer reading.
    Bf = np.where(np.isfinite(Bl), Bl, np.nanmedian(Bl))
    gyb, gxb = np.gradient(ndimage.uniform_filter(Bf, 3)); slb = np.degrees(np.arctan(np.hypot(gxb, gyb)))
    cub = np.abs(Bf - ndimage.uniform_filter(Bf, 9))
    steepcurved = (slb >= SLOPE_MIN) | (cub >= CURV_MIN)
    up_ok = ndimage.binary_dilation(steepcurved, iterations=2)
    O2 = np.where((O > Bl) & ~up_ok, Bl, O)
    C = np.where(np.isfinite(O), wgt * O2 + (1 - wgt) * Bl, Bl)
    v2.write_like(s, 'dtm_final2.tif', C); v2.write_like(s, 'open_mask.tif', openc.astype(float))
    # score overall + on open steep ground
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); M, _ = K.rd(f'{d}/kbay_max.tif'); GM, _ = K.rd(f'{d}/grewingk_max.tif'); F = v2.field(s)
    Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3); gy, gx = np.gradient(Gs); sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
    ost = e & np.isfinite(G) & (M - F - G < 0.3) & (GM - G < 0.3) & (sl > 30)
    out = {}
    for n_, D in [('vendor', K.rd(f'{d}/vendor_dtm.tif')[0]), ('blend', Bl), ('final2', C)]:
        sc = v2.score(s, D); err = D - F - G; v = err[ost & np.isfinite(err)]
        out[n_] = dict(all=(sc['ALL']['high'], sc['ALL']['low']), open_steep=(round(100*float(np.mean(v > 0.5)), 1), round(100*float(np.mean(v < -0.5)), 1)) if v.size > 100 else None)
    out['open_cells_pct'] = round(100*float(openc[e].mean()), 1)
    res[s] = out
    print('%-13s open cells %5.1f%% | ALL hi/lo  vendor %s  blend %s  final2 %s | open steep  vendor %s  blend %s  final2 %s' % (
        s, out['open_cells_pct'], out['vendor']['all'], out['blend']['all'], out['final2']['all'],
        out['vendor']['open_steep'], out['blend']['open_steep'], out['final2']['open_steep']), flush=True)
json.dump(res, open(f'site/open_override_{RMS_MAX}_{SLOPE_MIN}_{CURV_MIN}.json', 'w'), indent=1)
