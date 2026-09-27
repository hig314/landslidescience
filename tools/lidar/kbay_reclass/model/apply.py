"""Apply the whole chain to a site WITHOUT Grewingk (Hig's 59.50236 -151.02577 'vendor did poorly'
spot is outside Grewingk's footprint). Every model is trained on ALL six Grewingk sites.

    python model/apply.py vendor_poor

vendor TIN + kbay_max -> pts.npz (no truth: dz = NaN; slope/curv from the vendor TIN) -> features
-> ground model p -> drop50 TIN (zone features need it) -> zone model -> zone solver (e) ->
slope-adaptive Best -> v2a -> blend model + support rule -> dtm_blend.tif.
"""
import json, os, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model')
import sitekit as K, loso as L, v2
from sklearn.ensemble import HistGradientBoostingClassifier

TRAIN = ['patch1', 'forest_tall', 'alder', 'meadow_shrub', 'bare_gentle', 'island']
s = sys.argv[1]; d = f'site/{s}'; os.makedirs(d, exist_ok=True)
# ---- timing: every stage of processing ONE patch, to extrapolate to the whole survey ----
import time, joblib
_T0 = time.time(); _last = [_T0]; TIMES = {}
def TICK(stage):
    now = time.time(); TIMES[stage] = round(now - _last[0], 1); _last[0] = now
    print(f'  [{stage}] {TIMES[stage]:.1f} s', flush=True)
SAVED = 'model/saved'; os.makedirs(SAVED, exist_ok=True)
def cached_model(name, train):
    fp = f'{SAVED}/{name}.joblib'
    if os.path.exists(fp): return joblib.load(fp)
    t = time.time(); m = train(); joblib.dump(m, fp)
    TIMES[f'TRAIN_{name} (one-off)'] = round(time.time() - t, 1); _last[0] = time.time()
    return m
S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)

# 1. vendor TIN + highest return
K.tin(S['kbay'], f'{d}/vendor_dtm.tif', S, d, 'dtm_vendor')
if not os.path.exists(f'{d}/vendor_dtm.tif'):
    # No vendor ground (class 2) in this unit at all: a sliver at the survey edge
    # holding only water or noise returns (five such units in the full run,
    # 2026-09-27, each < 0.07 km2 of coverage). Nothing to model; say so and
    # finish cleanly so the runner marks it DONE-empty instead of FAILED.
    open(f'{d}/EMPTY', 'w').write('no class-2 (ground) returns in unit: vendor TIN produced no raster\n')
    print('EMPTY unit: no vendor ground returns; nothing to model', flush=True)
    sys.exit(0)
K.run([S['kbay'], {"type": "filters.range", "limits": "Classification[1:6]"},
       {"type": "writers.gdal", "filename": f"{d}/kbay_max.tif", "resolution": 1, "radius": 0.71, "output_type": "max",
        "origin_x": x0, "origin_y": y0, "width": NX, "height": NY, "data_type": "float32", "nodata": -9999}], 'kbay_max', d)
TICK('1 vendor TIN + max grid')
# 2. points (no truth)
import pyarrow.feather as f
t = f.read_table(K.export_points(S, d), columns=['xyz', 'Classification', 'ReturnNumber', 'NumberOfReturns', 'Intensity'])
xyz = np.asarray(t.column('xyz').combine_chunks().flatten()).reshape(-1, 3)
V, T = K.rd(f'{d}/vendor_dtm.tif'); Vs = ndimage.uniform_filter(np.nan_to_num(V, nan=np.nanmedian(V)), 3)
gy, gx = np.gradient(Vs); slope = np.degrees(np.arctan(np.hypot(gx, gy))); curv = Vs - ndimage.uniform_filter(Vs, 15)
row = (y1 - xyz[:, 1]) - 0.5; col = (xyz[:, 0] - x0) - 0.5
cls_in, n_lt = K.admit_low_tide(xyz[:, 0], xyz[:, 1], xyz[:, 2], np.asarray(t.column('Classification')), S['bounds'])
print(f'low-tide class-22 returns admitted as ground: {n_lt}', flush=True)
np.savez(f'{d}/pts.npz', x=xyz[:, 0], y=xyz[:, 1], z=xyz[:, 2], dz=np.full(len(xyz), np.nan, np.float32),
         cls=cls_in, rn=np.asarray(t.column('ReturnNumber')),
         nr=np.asarray(t.column('NumberOfReturns')), inten=np.asarray(t.column('Intensity')),
         slope=ndimage.map_coordinates(slope, [row, col], order=1, mode='nearest').astype(np.float32),
         curv=ndimage.map_coordinates(curv, [row, col], order=1, mode='nearest').astype(np.float32))
K.step_features(S, d)
v2.write_like = v2.write_like                                             # (uses grewingk_dtm.tif as template)
import shutil; shutil.copy(f'{d}/vendor_dtm.tif', f'{d}/grewingk_dtm.tif')   # TEMPLATE ONLY (grid/profile), never a truth
open(f'{d}/NO_GREWINGK_grewingk_dtm_is_a_template_copy_of_vendor', 'w').write('see apply.py\n')
v2.write_like(s, 'offset_field.tif', np.zeros((NY, NX)))                # no Grewingk: zero field so shared steps run; nothing is scored here
TICK('2 export + features'); print('features done', flush=True)

# 3. ground model on all six sites (trained once, cached)
rng = np.random.default_rng(0)
def _train_ground():
    Xs, ys = [], []
    for tr in TRAIN:
        P, X, lab, names, vend = L.load(tr); idx = np.flatnonzero(lab >= 0); idx = rng.choice(idx, min(600_000, len(idx)), replace=False)
        Xs.append(X[idx]); ys.append(lab[idx])
    return HistGradientBoostingClassifier(max_iter=400, learning_rate=0.08, max_leaf_nodes=63, l2_regularization=1.0).fit(np.vstack(Xs), np.concatenate(ys))
mdl = cached_model('ground', _train_ground)
P, X, lab, names, vend = L.load(s); prob = mdl.predict_proba(X)[:, 1].astype(np.float32); np.save(f'{d}/prob.npy', prob)
ok = ~np.isin(P['cls'], EXCL)
_sel = vend & (prob >= 0.5) & ok
if int(_sel.sum()) < 50:
    # Too little ground to triangulate: a sliver that is essentially all water
    # (u_563_6588, 2026-09-27: 4 vendor ground points against 49,641 water).
    # Same outcome as the no-ground guard in step 1: DONE, empty, no surface.
    open(f'{d}/EMPTY', 'w').write(f'only {int(_sel.sum())} model ground points in unit: too few to triangulate\n')
    print(f'EMPTY unit: only {int(_sel.sum())} model ground points; nothing to model', flush=True)
    sys.exit(0)
L.dtm_from(P, _sel, S, d, 'drop50')
TICK('3 ground model predict + drop50 TIN'); print('ground model done', flush=True)

# 4. zones: cell features (target not used), model on all six
import cellzone as CZ, rasterio
Xc, _, shp = CZ.cell_features(s)
def _train_zone():
    Xz, yz = [], []
    for tr in TRAIN:
        D = np.load(f'site/{tr}/cellfeat.npz'); idx = np.flatnonzero(D['y'] >= 0); idx = rng.choice(idx, min(400_000, len(idx)), replace=False)
        Xz.append(D['X'][idx]); yz.append(D['y'][idx])
    return HistGradientBoostingClassifier(max_iter=300, max_leaf_nodes=63, l2_regularization=1.0).fit(np.vstack(Xz), np.concatenate(yz))
mz = cached_model('zone', _train_zone)
pz = mz.predict_proba(Xc)[:, 1].reshape(shp).astype(np.float32)
with rasterio.open(f'{d}/dtm_drop50.tif') as src: prof = src.profile
prof.update(nodata=None)
with rasterio.open(f'{d}/zone_prob.tif', 'w', **prof) as dst: dst.write(pz, 1)
TICK('4 zone features + predict'); print('zones done', flush=True)

# 5. zone solver (variant e) and slope-adaptive Best
import subprocess
subprocess.run([sys.executable, '-c', f"""
import sys, os; os.chdir('{os.getcwd()}'); sys.path.insert(0,'model'); import surface as SU
class A: pass
a=A(); a.zone_p=0.8; a.dilate=1; a.tau=0.0; a.cand_w=1.0; a.lam_in=5.0; a.lam_out=0.005; a.iters=5; a.tag='s_e'
print(SU.solve_site('{s}', a))
"""], check=True)
import best_slope as BS
from loso import load as _load
F = dict(np.load(f'{d}/feat.npz')); x, y, z, cls = P['x'], P['y'], P['z'], P['cls']
cid = np.clip((y1-y).astype(int), 0, NY-1)*NX + np.clip((x-x0).astype(int), 0, NX-1)
land = ~np.isin(cls, EXCL) & (cls != 9)
gyv, gxv = np.gradient(np.where(np.isfinite(V), V, np.nanmedian(V)))
sl_loc = ndimage.maximum_filter(np.degrees(np.arctan(np.hypot(gxv, gyv))), 3).ravel()[cid]
keep_g = vend & (prob >= BS.thr(sl_loc)) & land
# same no-layering rule as best_slope.py: never drop vendor ground in >= 95% single-return cells
nsing = np.bincount(cid[land], weights=(P['nr'][land] == 1).astype(float), minlength=NX*NY)
ntot = np.bincount(cid[land], minlength=NX*NY).astype(float)
single = ndimage.median_filter((nsing / np.maximum(ntot, 1)).reshape(NY, NX), 3)
keep_g |= vend & land & (single.ravel()[cid] >= 0.95) & (ntot.ravel()[cid] >= 6)
sel = keep_g | (cls == 20) | (cls == 9)
L.dtm_from(P, sel, S, d, 'tinslope'); Tn, _ = K.rd(f'{d}/dtm_tinslope.tif')
with rasterio.open(f'{d}/zone_s_e.tif') as src: zone = src.read(1).astype(bool)
Z, _ = K.rd(f'{d}/dtm_s_e.tif')
Z[Z < K.return_floor(s) - K.FLOOR_TOL] = np.nan   # the solver can dive far below every return where it has few points (snow edge, 2026-09-26): drop it there
wgt = np.clip(1 - ndimage.distance_transform_edt(~zone)/3, 0, 1)
C = np.where(np.isfinite(Z), wgt*Z + (1-wgt)*Tn, Tn); v2.write_like(s, 'dtm_bestslope.tif', C)
TICK('5 zone solver + Best'); print('best done', flush=True)

# 6. v2a + blend (model on all six)
import blend as BL
def _train_blend():
    Xb, yb = [], []
    for tr in TRAIN:
        D = BL.features(tr); idx = np.flatnonzero(D['y'] >= 0); idx = rng.choice(idx, min(300_000, len(idx)), replace=False)
        Xb.append(D['X'][idx]); yb.append(D['y'][idx])
    return HistGradientBoostingClassifier(max_iter=300, max_leaf_nodes=31, l2_regularization=1.0).fit(np.vstack(Xb), np.concatenate(yb))
mb = cached_model('blend', _train_blend)
D = BL.features(s)
p = mb.predict_proba(D['X'])[:, 1].reshape(D['shape']); w = ndimage.uniform_filter(p, 3)
w = BL.guard(D, w)
A_, B_ = D['A'], D['B']
Cb = np.where(np.isfinite(A_) & np.isfinite(B_), B_ + w * (A_ - B_), np.where(np.isfinite(B_), B_, A_))
v2.write_like(s, 'dtm_blend.tif', Cb); v2.write_like(s, 'blend_w.tif', w)
print('blend done; v2a-weighted cells %.1f%%' % (100*np.mean(w > 0.5)), flush=True)
TICK('6 v2a + blend')
# 7. rock / cliff (tolerance TIN, breaklines when BREAKLINES=1)
import rock as RK
RK.rock_site(s)
TICK('7 rock + cliff surface')
npts = int(np.load(f'{d}/pts.npz')['x'].size)
TIMES['points'] = npts; TIMES['area_km2'] = (x1 - x0) * (y1 - y0) / 1e6
TIMES['total_processing_s'] = round(sum(v for k, v in TIMES.items() if k[0].isdigit()), 1)
json.dump(TIMES, open(f'{d}/timing.json', 'w'), indent=1)
print('TIMING', json.dumps(TIMES), flush=True)
