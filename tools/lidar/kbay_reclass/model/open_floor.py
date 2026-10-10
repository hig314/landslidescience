"""Open-steep FLOOR on top of the blend (Hig, 2026-09-25). On steep open ground every return is
the ground, so the surface must not sit below them: where a cell's returns are almost all single
pulses (>= SINGLE_MIN) and mostly on one surface (>= FRAC_MIN within 0.10 m of the cell plane,
n >= 6) and the slope is >= SLOPE_MIN, the final surface is raised to at least (plane - 0.10 m).
The floor is only ever a lower bound -- it never lowers anything -- and is restricted to steep
ground because a tight single-return surface on gentle ground can be a herb mat."""
import json, os, sys
import numpy as np
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import v2, sitekit as K, surfacefit as SF
SINGLE_MIN, FRAC_MIN, SLOPE_MIN = [float(a) for a in sys.argv[1:4]] if len(sys.argv) > 3 else (0.9, 0.6, 30.0)
res = {}
for s in v2.SITES:
    S, f, r, use = SF.features(s); d = f'site/{s}'
    Bl, _ = K.rd(f'{d}/dtm_blend.tif'); Bf = np.where(np.isfinite(Bl), Bl, np.nanmedian(Bl))
    gy, gx = np.gradient(ndimage.uniform_filter(Bf, 3)); sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    m = (f['n'] >= 6) & (f['single'] >= SINGLE_MIN) & (f['frac10'] >= FRAC_MIN) & (sl >= SLOPE_MIN) & np.isfinite(f['plane'])
    floor = np.where(m, f['plane'] - 0.10, -np.inf)
    C = np.where(np.isfinite(Bl), np.maximum(Bl, floor), Bl)
    v2.write_like(s, 'dtm_final2.tif', C); v2.write_like(s, 'floor_mask.tif', (m & (floor > Bl)).astype(float))
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); M, _ = K.rd(f'{d}/kbay_max.tif'); GM, _ = K.rd(f'{d}/grewingk_max.tif'); F = v2.field(s)
    Gs = ndimage.uniform_filter(np.nan_to_num(G, nan=np.nanmedian(G)), 3); gyg, gxg = np.gradient(Gs); slg = np.degrees(np.arctan(np.hypot(gxg, gyg)))
    e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
    ost = e & np.isfinite(G) & (M - F - G < 0.3) & (GM - G < 0.3) & (slg > 30)
    out = {'raised_cells_pct': round(100*float((m & (floor > Bl))[e].mean()), 2)}
    for n_, Dm in [('vendor', K.rd(f'{d}/vendor_dtm.tif')[0]), ('blend', Bl), ('final2', C)]:
        sc = v2.score(s, Dm); err = Dm - F - G; v = err[ost & np.isfinite(err)]
        out[n_] = dict(all=(sc['ALL']['high'], sc['ALL']['low']), open_steep=(round(100*float(np.mean(v > 0.5)), 1), round(100*float(np.mean(v < -0.5)), 1)) if v.size > 100 else None)
    res[s] = out
    print('%-13s raised %5.2f%% of cells | ALL hi/lo vendor %s blend %s final2 %s | open steep vendor %s blend %s final2 %s' % (
        s, out['raised_cells_pct'], out['vendor']['all'], out['blend']['all'], out['final2']['all'], out['vendor']['open_steep'], out['blend']['open_steep'], out['final2']['open_steep']), flush=True)
json.dump(res, open('site/open_floor.json', 'w'), indent=1)
