"""Flight-line check on steep faces (Hig, 2026-09-26): is the cliff sawtooth random noise, or
overlapping flight lines that put the face in slightly different places?

For each 1 m cell (steep rock cells, and a flat bare-ground control):
  face plane = PCA of the cell's returns (normal = direction of least spread, in 3D)
  r          = each return's distance from that plane ALONG THE NORMAL
Then per flight line (PointSourceId):
  - pooled scatter of r WITHIN a line vs scatter of r over all lines together. If lines are
    misaligned, within-line scatter is much smaller than total.
  - per cell and line pair, the difference of the lines' median r. Misalignment is a 3-D shift t
    between the lines, so these differences should fit  delta = n . t  across all cells
    (n = that cell's normal). Solved per pair by least squares; the fraction of variance t
    explains says whether it is a systematic shift or noise.
"""
import json, os, sys
import numpy as np, rasterio
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
import pyarrow.feather as feather
from scipy import ndimage
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import sitekit as K


def cell_pca(cid, x, y, z, N):
    n = np.bincount(cid, minlength=N).astype(float)
    m = lambda a: np.bincount(cid, weights=a, minlength=N) / np.maximum(n, 1)
    mx, my, mz = m(x), m(y), m(z)
    dx, dy, dz = x - mx[cid], y - my[cid], z - mz[cid]
    C = np.zeros((N, 3, 3))
    for i, a in enumerate((dx, dy, dz)):
        for j, b in enumerate((dx, dy, dz)):
            if j < i: continue
            v = np.bincount(cid, weights=a * b, minlength=N) / np.maximum(n, 1)
            C[:, i, j] = v; C[:, j, i] = v
    ok = n >= 12
    nrm = np.zeros((N, 3)); nrm[:, 2] = 1
    w, V = np.linalg.eigh(C[ok])
    nrm[ok] = V[:, :, 0]                                   # smallest eigenvalue -> normal
    nrm[nrm[:, 2] < 0] *= -1                               # point it up
    r = dx * nrm[cid, 0] + dy * nrm[cid, 1] + dz * nrm[cid, 2]
    return r, nrm, n, ok


def analyse(s, which):
    d = f'site/{s}'; S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1 - x0), int(y1 - y0); N = NX * NY
    t = feather.read_table(f'{d}/kbay.feather', columns=['xyz', 'Classification', 'PointSourceId'])
    xyz = np.asarray(t.column('xyz').combine_chunks().flatten()).reshape(-1, 3)
    cls = np.asarray(t.column('Classification')); src = np.asarray(t.column('PointSourceId'))
    keep = ~np.isin(cls, EXCL) & (cls != 9)
    x, y, z, src = xyz[keep, 0], xyz[keep, 1], xyz[keep, 2], src[keep]
    cid = np.clip((y1 - y).astype(int), 0, NY - 1) * NX + np.clip((x - x0).astype(int), 0, NX - 1)
    D, _ = K.rd(f'{d}/dtm_final.tif'); Df = np.where(np.isfinite(D), D, np.nanmedian(D))
    gy, gx = np.gradient(Df); sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    with rasterio.open(f'{d}/rock_mask.tif') as r_: rock = r_.read(1) > 0.5
    e = np.zeros((NY, NX), bool); e[30:-30, 30:-30] = True
    sel_cells = (e & rock & (sl >= 45)) if which == 'steep rock' else (e & rock & (sl < 10))
    pm = sel_cells.ravel()[cid]
    x, y, z, src, cid = x[pm], y[pm], z[pm], src[pm], cid[pm]
    r, nrm, n, ok = cell_pca(cid, x, y, z, N)
    good = ok[cid]; r, src, cid = r[good], src[good], cid[good]
    # per (cell, line): count, median r
    lines = np.unique(src); L = len(lines); li = np.searchsorted(lines, src)
    key = cid.astype(np.int64) * L + li
    o = np.lexsort((r, key)); ks = key[o]; st = np.r_[0, np.flatnonzero(ks[1:] != ks[:-1]) + 1]; en = np.r_[st[1:], len(ks)]
    cnt = en - st; med = r[o][(st + en - 1) // 2]; kc = ks[st] // L; kl = ks[st] % L
    # within-line scatter (pooled, only groups of >= 5) vs total
    grp = np.repeat(np.arange(len(st)), cnt)
    gmean = np.bincount(grp, weights=r[o]) / cnt
    within = np.sqrt(np.sum((r[o] - gmean[grp]) ** 2 * (cnt[grp] >= 5)) / max(np.sum(cnt[cnt >= 5]), 1))
    cm = np.bincount(cid, weights=r) / np.maximum(np.bincount(cid), 1)
    total = np.sqrt(np.mean((r - cm[cid]) ** 2))
    multi = np.bincount(kc[cnt >= 5], minlength=N) >= 2
    print(f'\n{s} [{which}] cells {int(sel_cells.sum())}, with >= 2 lines (>= 5 returns each): {int(multi.sum())}')
    print(f'  scatter across the face normal: all lines together {total*100:.1f} cm | within one line {within*100:.1f} cm')
    # per line pair: delta = median_a - median_b in shared cells; fit delta = n . t
    res = []
    good_g = cnt >= 5
    by_cell = {}
    for g in np.flatnonzero(good_g):
        by_cell.setdefault(kc[g], []).append((kl[g], med[g]))
    pairs = {}
    for c, lst in by_cell.items():
        if len(lst) < 2: continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = sorted([lst[i], lst[j]])
                pairs.setdefault((a[0], b[0]), []).append((c, a[1] - b[1]))
    for (a, b), v in sorted(pairs.items(), key=lambda kv: -len(kv[1])):
        if len(v) < 200: continue
        c = np.array([p[0] for p in v]); dl = np.array([p[1] for p in v])
        A = nrm[c]
        tvec, *_ = np.linalg.lstsq(A, dl, rcond=None)
        pred = A @ tvec; ss = np.sum((dl - dl.mean()) ** 2); r2 = 1 - np.sum((dl - pred) ** 2) / max(ss, 1e-9)
        res.append(dict(pair=(int(lines[a]), int(lines[b])), cells=len(v), median_delta_cm=round(float(np.median(dl)) * 100, 1),
                        abs_delta_p50_cm=round(float(np.median(np.abs(dl))) * 100, 1),
                        shift_xyz_cm=[round(float(q) * 100, 1) for q in tvec], explained=round(float(r2), 2)))
        print(f'  lines {lines[a]} vs {lines[b]}: {len(v):6d} shared cells | |diff| median {np.median(np.abs(dl))*100:5.1f} cm | '
              f'best 3-D shift dx {tvec[0]*100:+5.1f} dy {tvec[1]*100:+5.1f} dz {tvec[2]*100:+5.1f} cm, explains {r2*100:3.0f}% of the differences')
    return dict(cells=int(sel_cells.sum()), multi=int(multi.sum()), total_cm=round(total * 100, 1), within_cm=round(within * 100, 1), pairs=res)


if __name__ == '__main__':
    out = {}
    for s in ['woz_boulders', 'patch1']:
        for which in ['steep rock', 'gentle rock']:
            out[f'{s}|{which}'] = analyse(s, which)
    json.dump(out, open('site/flightlines.json', 'w'), indent=1)
