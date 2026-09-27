"""Horizontal co-registration KBay -> Grewingk per site (Nuth & Kaab style, on ground bare in BOTH
surveys): dz = KBay - Grewingk - offset_field ~ -(sx*dG/dx + sy*dG/dy) + c. Robust IRLS.
Then re-score open steep ground with Grewingk shifted by (sx, sy), to see how much of the
'too low on steep faces' is the surveys not lining up rather than classification."""
import json, os, sys
import numpy as np
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import v2, sitekit as K
res = {}
for s in v2.SITES:
    d = f'site/{s}'
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); V, _ = K.rd(f'{d}/vendor_dtm.tif'); M, _ = K.rd(f'{d}/kbay_max.tif'); GM, _ = K.rd(f'{d}/grewingk_max.tif'); F = v2.field(s)
    Gf = np.where(np.isfinite(G), G, np.nanmedian(G)); gyr, gx = np.gradient(ndimage.uniform_filter(Gf, 3)); gy = -gyr
    bare = np.isfinite(G) & np.isfinite(V) & (M - V < 0.3) & (GM - G < 0.3) & (np.hypot(gx, gy) < 1.5) & (np.hypot(gx, gy) > 0.1)
    dz = (V - F - G)[bare]; A = np.c_[np.ones(bare.sum()), -gx[bare], -gy[bare]]; w = np.ones(len(dz))
    for it in range(8):
        sw = np.sqrt(w); x, *_ = np.linalg.lstsq(A * sw[:, None], dz * sw, rcond=None)
        r = dz - A @ x; sgm = 1.4826 * np.median(np.abs(r)); w = np.clip(1 - (r / (3 * sgm)) ** 2, 0, None) ** 2
    c, sx, sy = x
    # shift Grewingk: a KBay point seen at (x,y) is truly at (x - sx, y - sy): compare with G(x - sx, y - sy)
    # output(r, c) = input(r - shift_r, c - shift_c); want G(x - sx, y - sy): rows run SOUTH, so
    # y - sy is row + sy -> shift_r = -sy; x - sx is col - sx -> shift_c = +sx
    Gs = ndimage.shift(Gf, (-sy, sx), order=1, mode='nearest'); Gs[~np.isfinite(G)] = np.nan
    slg = np.degrees(np.arctan(np.hypot(gx, gy)))
    e = np.zeros_like(G, bool); e[30:-30, 30:-30] = True
    ost = e & np.isfinite(G) & (M - F - G < 0.3) & (GM - G < 0.3) & (slg > 30)
    out = dict(shift_x=round(float(sx), 3), shift_y=round(float(sy), 3), n_bare=int(bare.sum()), open_steep_cells=int(ost.sum()))
    for n_, f in [('vendor', 'vendor_dtm.tif'), ('blend', 'dtm_blend.tif')]:
        D, _ = K.rd(f'{d}/{f}')
        for tag, GG in [('as_is', G), ('coreg', Gs)]:
            err = (D - F - GG)[ost & np.isfinite(D)]
            out[f'{n_}_{tag}_low'] = round(100 * float(np.mean(err < -0.5)), 1) if err.size > 100 else None
    res[s] = out; print(s, json.dumps(out), flush=True)
json.dump(res, open('site/coreg.json', 'w'), indent=1)
