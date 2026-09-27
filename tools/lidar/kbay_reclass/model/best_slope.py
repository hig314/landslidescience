"""BEST with a slope-adaptive trimming threshold (Hig, 2026-09-26: over-trimmed barren crag at
59.57281 -151.15884, 57 deg -- the ground model scored 2/3 of the vendor's correct ground there
below 0.9; crag steps split pulses and scatter heights like canopy).
Drop vendor ground where p < thr(slope): 0.9 up to 30 deg, easing linearly to 0.5 by 45 deg
(slope from the vendor TIN, KBay-only). Same zone surface on top as before."""
import json, os, sys
import numpy as np, rasterio
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import sitekit as K, loso as L, v2
LO, HI, S0, S1 = float(os.environ.get("LO", "0.5")), 0.9, 30.0, 45.0
def thr(slope): return HI - (HI - LO) * np.clip((slope - S0) / (S1 - S0), 0, 1)
if __name__ == '__main__':
    for s in v2.SITES:
        d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
        P, X, lab, names, vend = L.load(s); prob = np.load(f'{d}/prob.npy'); F = dict(np.load(f'{d}/feat.npz'))
        x, y, z, cls = P['x'], P['y'], P['z'], P['cls']
        cid = np.clip((y1-y).astype(int), 0, NY-1)*NX + np.clip((x-x0).astype(int), 0, NX-1)
        land = ~np.isin(cls, EXCL) & (cls != 9); water = cls == 9
        if water.any():
            W = np.zeros(NX*NY); Wn = np.zeros(NX*NY); np.add.at(W, cid[water], z[water]); np.add.at(Wn, cid[water], 1)
            Wl = ndimage.median_filter(np.where(Wn > 0, W/np.maximum(Wn, 1), np.nan).reshape(NY, NX), 15)
            Wl = np.where(np.isnan(Wl), np.median(z[water]), Wl).ravel()
            raised = water & (z > Wl[cid] + 0.15); wsurf = water & ~raised; rel = raised & (prob >= 0.5)
        else:
            wsurf = rel = np.zeros(len(z), bool)
        # LOCAL steepness: 1 m slope of the vendor TIN, max over 3x3 m. The 5 m-smoothed v_slope
        # read Hig's crag (57 deg at 1 m, 64 deg 3 m max) as 39-63 deg and still dropped its ground.
        Vd, _ = K.rd(f'{d}/vendor_dtm.tif'); gyv, gxv = np.gradient(np.where(np.isfinite(Vd), Vd, np.nanmedian(Vd)))
        sl_loc = ndimage.maximum_filter(np.degrees(np.arctan(np.hypot(gxv, gyv))), 3).ravel()[cid]
        keep_g = vend & (prob >= thr(sl_loc)) & land
        if os.environ.get('KEEP_SINGLE', '1') == '1':
            # Do not overrule the vendor's own ground call where there is no sign of layered canopy:
            # cells whose pulses are >= 95% single returns (Hig's boulder pile, 59.50074 -151.00479:
            # the ground model scored boulder tops like shrubs and Best cut up to 0.3 m+ below every
            # return; geometry cannot tell boulders from dense leaf-on shrubs, but the vendor could).
            N_ = NX * NY
            nsing = np.bincount(cid[land], weights=(P['nr'][land] == 1).astype(float), minlength=N_)
            ntot = np.bincount(cid[land], minlength=N_).astype(float)
            single = (nsing / np.maximum(ntot, 1)).reshape(NY, NX)
            single = ndimage.median_filter(single, 3)
            keep_g |= vend & land & (single.ravel()[cid] >= 0.95) & (ntot.reshape(NY, NX).ravel()[cid] >= 6)
        sel = keep_g | rel | (cls == 20) | wsurf
        L.dtm_from(P, sel, S, d, 'tinslope'); T, _ = K.rd(f'{d}/dtm_tinslope.tif')
        with rasterio.open(f'{d}/zone_s_e.tif') as src: zone = src.read(1).astype(bool)
        Z, _ = K.rd(f'{d}/dtm_s_e.tif')
        Z[Z < K.return_floor(s) - K.FLOOR_TOL] = np.nan   # the solver can dive far below every return where it has few points (snow edge, 2026-09-26): drop it there
        wgt = np.clip(1 - ndimage.distance_transform_edt(~zone)/3, 0, 1)
        C = np.where(np.isfinite(Z), wgt*Z + (1-wgt)*T, T)
        v2.write_like(s, 'dtm_bestslope.tif', C)
        sc = v2.score(s, C); print('%-13s bestslope ALL %4.1f/%4.1f' % (s, sc['ALL']['high'], sc['ALL']['low']), flush=True)
