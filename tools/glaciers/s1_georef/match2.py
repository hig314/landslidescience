"""Second-generation placement: one consensus search at a known scale.

What match.py left free that is not free (2026-11-22):
  scale   Hig's exports are 10 m a pixel in Web Mercator units and the
          reference grid is in Web Mercator too, so the template's size on
          the reference is exact: 10 / (reference pixel, mercator m). The
          +-5 % scale search of match.py let candidates drift (Hubbard's
          ranged 4.5..5.02 m, the corner moving by hundreds of metres).
  orbit   the video's name says ascending or descending (it agreed with
          the winning simulation on 53 of 56 accepted placements); only
          an unnamed video tries both.
Then, instead of six independent peaks that decide.py votes on:
  consensus  each product's score map (lum, feat, stab) is turned into a
          significance map (minus its median, over its robust sd) and the
          maps are summed; a placement must be good in all of them at once.
  refine  around the consensus peak, the same sum at the reference's full
          resolution, then a parabolic sub-pixel fit of the peak.
  water   each search runs twice: water (DEM <= 2 m) filled dark and
          counted, and water left out of the correlation altogether
          (two-sided weighted NCC, dwncc). Hubbard needs the first (its
          coast is the best stable feature); Icy Bay the second (its
          mélange is bright, so a dark fill is a wrong picture of it).
          The two are compared by significance, the best wins.
  prior   the anchor (a point on the glacier the video is about)
          as a soft prior; a peak further than PRIOR_KM from the frame
          loses PRIOR_W significance per extra km. On the 76 site videos
          (2026-11-22) it changed none of the 63 accepted placements and
          recovered a Tsaa and a Guyot.
Then fine.py repeats the consensus on a 10 m simulation around the result.
Regression gate: compare.py against the eye-checked placements.

usage: match2.py STACK.npz REF.npz META.json OUT.json [--prior-km 3 --prior-w 1] [--centre] [--feat-w 1] [--down 4]
OUT.json: {best: {bounds_3857, z, ncc:{prod: ncc}, orbit}, peaks: [...top 5 coarse...]}"""
import argparse, json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, cv2
from match import gradmag, wncc, stable_mask

R = 6378137.0
MERC = 10.0
PRODS = ('lum', 'feat', 'stab')
WEIGHT = {'lum': 1.0, 'feat': 1.0, 'stab': 1.0}   # --feat-w sets feat's
FLAT_WATER = False   # --flat-water: lakes the DEM holds at one height count as water too


def land_mask(dem):
    """where the DEM is land: above 2 m, and with --flat-water not part of a
    hydro-flattened lake (3DEP holds a lake at one height: Alsek Lake is 24 m
    everywhere, so 'DEM <= 2 m' called it land and the simulation drew a flat
    plain where the video has dark water). Flat = no height change over 7 x 7
    pixels, kept only in patches of 50+ pixels so flat ice or a road is not
    taken for a lake."""
    land = np.isfinite(dem) & (dem > 2)
    if FLAT_WATER:
        z = np.nan_to_num(dem, nan=-1e4).astype(np.float32)
        k = np.ones((7, 7), np.uint8)
        flat = (cv2.dilate(z, k) - cv2.erode(z, k)) < 0.05
        n, lab, stats, _ = cv2.connectedComponentsWithStats(flat.astype(np.uint8), 8)
        big = np.zeros(n, bool); big[1:] = stats[1:, cv2.CC_STAT_AREA] >= 50
        land &= ~big[lab]
    return land


def merc(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def ref_image(ref, orbit, down, prod, water):
    """the simulation and where it counts. water='dark': water (DEM <= 2 m)
    filled with land's 2nd percentile and counted (Hubbard: the coast is the
    best stable feature). water='free': water does not count at all (Icy
    Bay: its mélange is bright, the dark fill is a wrong picture of it)."""
    I = ref[orbit].astype(np.float32)
    dem = ref['dem']
    land = land_mask(dem)
    fill = np.percentile(I[land], 2) if water == 'dark' else float(I[land].mean())
    I = np.where(land, I, fill).astype(np.float32)
    v = np.ones_like(I) if water == 'dark' else land.astype(np.float32)
    if down > 1:
        I = cv2.resize(I, None, fx=1 / down, fy=1 / down, interpolation=cv2.INTER_AREA)
        v = cv2.resize(v, (I.shape[1], I.shape[0]), interpolation=cv2.INTER_AREA)
    if prod == 'feat':
        I = gradmag(I)
        if water == 'free':                      # the fill's edge is not a feature
            v = cv2.erode(v, np.ones((5, 5), np.uint8))
    return I, (v > 0.5).astype(np.float32)


MIN_OVERLAP = 0.3


def dwncc(img, v, t, w, min_overlap=None):
    """NCC with weights on both sides: w (0..1) on the template, v (0/1) on
    the image; each offset uses only pixels where both count. Six FFT
    correlations (Padfield's masked NCC, weighted)."""
    if min_overlap is None:
        min_overlap = MIN_OVERLAP
    img = ((img - img[v > 0].mean()) / (img[v > 0].std() + 1e-9)).astype(np.float32) * v
    v = v.astype(np.float32)
    t = ((t - (w * t).sum() / w.sum()) / (np.sqrt((w * (t - (w * t).sum() / w.sum()) ** 2).sum() / w.sum()) + 1e-9))
    w = w.astype(np.float32); wt = (w * t).astype(np.float32); wt2 = (w * t * t).astype(np.float32)
    c = lambda a, b: cv2.matchTemplate(a, b, cv2.TM_CCORR)
    A = c(v, w); St = c(v, wt); St2 = c(v, wt2); SI = c(img, w); SI2 = c(img * img, w); StI = c(img, wt)
    A1 = np.maximum(A, 1e-6)
    cov = StI - St * SI / A1; vt = St2 - St * St / A1; vI = SI2 - SI * SI / A1
    r = cov / np.sqrt(np.maximum(vt, 1e-9) * np.maximum(vI, 1e-9))
    r = np.clip(r, -1, 1)
    r[A < min_overlap * float(w.sum())] = np.nan          # too little land under the frame
    r[vI < 0.05 * A1] = np.nan                            # a flat patch (filled water): no picture to match
    return r.astype(np.float32)


def video(st, prod, w, h):
    v = (st['feat_med'] if prod == 'feat' else st['lum_med']).astype(np.float32)
    t = cv2.resize(v, (w, h), interpolation=cv2.INTER_AREA)
    m = (stable_mask(st) if prod == 'stab' else st['mask']).astype(np.float32)
    wt = cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA)
    if prod != 'stab':
        wt = (wt > 0.99).astype(np.float32)
    return t.astype(np.float64), wt.astype(np.float64)


def zmap(r):
    """significance: (ncc - median) / robust sd over the offsets that count;
    offsets that do not count get a floor, not a zero that would shrink the sd."""
    ok = np.isfinite(r)
    sub = r[::3, ::3][ok[::3, ::3]]
    med = float(np.median(sub)); sd = max(1.4826 * float(np.median(np.abs(sub - med))), 0.03)
    return np.where(ok, (r - med) / sd, -5.0).astype(np.float32)


def score(st, ref, orbit, down, win=None, water='dark'):
    px = float(ref['transform'][0]) * down
    h0, w0 = st['mask'].shape
    w = int(round(w0 * MERC / px)); h = int(round(h0 * MERC / px))
    nccs = []
    for prod in PRODS:
        I, v = ref_image(ref, orbit, down, prod, water)
        if win is not None:
            x0, y0, x1, y1 = win
            I = I[y0:y1, x0:x1]; v = v[y0:y1, x0:x1]
        t, wt = video(st, prod, w, h)
        nccs.append(dwncc(I, v, t.astype(np.float32), wt.astype(np.float32)))
    return nccs, (w, h), px


def subpix(S, x, y):
    def fit(a, b, c):
        d = a - 2 * b + c
        return 0.0 if d >= 0 else 0.5 * (a - c) / d
    dx = fit(S[y, x - 1], S[y, x], S[y, x + 1]) if 0 < x < S.shape[1] - 1 else 0.0
    dy = fit(S[y - 1, x], S[y, x], S[y + 1, x]) if 0 < y < S.shape[0] - 1 else 0.0
    return x + dx, y + dy


def peaks(S, n, excl):
    S = S.copy(); out = []
    for _ in range(n):
        _, mx, _, (x, y) = cv2.minMaxLoc(S)
        out.append((float(mx), x, y))
        S[max(0, y - excl):y + excl + 1, max(0, x - excl):x + excl + 1] = -1e9
    return out


def place(st, ref, orbit, down, anchor, prior_km, prior_w, water, centre=False):
    T = ref['transform']
    nccs, (w, h), px = score(st, ref, orbit, down, water=water)
    S = sum(WEIGHT[p] * zmap(r) for p, r in zip(PRODS, nccs))
    if anchor is not None and prior_w > 0:
        ax, ay = merc(*anchor)
        ys, xs = np.mgrid[0:S.shape[0], 0:S.shape[1]]
        X0 = T[2] + xs * px; Y1 = T[5] - ys * px                 # frame's top-left for each offset
        if centre:                                   # anchor = the middle of the view (Hig's centres)
            kx = np.abs(X0 + w * px / 2 - ax); ky = np.abs(Y1 - h * px / 2 - ay)
        else:                                        # anchor = somewhere in the view
            kx = np.maximum(0, np.maximum(X0 - ax, ax - (X0 + w * px)))
            ky = np.maximum(0, np.maximum((Y1 - h * px) - ay, ay - Y1))
        lat = math.radians(anchor[1])
        dkm = np.hypot(kx, ky) * math.cos(lat) / 1000.0              # ground km from frame to anchor
        S = S - prior_w * np.maximum(0, dkm - prior_km)
    pk = peaks(S, 5, max(3, int(0.15 * min(w, h))))
    return S, pk, (w, h), px, nccs


def refine(st, ref, orbit, x, y, down, water, pad_px=12):
    """full-resolution consensus around a coarse peak at (x, y) of grid `down`."""
    f = down
    X, Y = x * f, y * f
    I0 = max(0, X - pad_px * f); J0 = max(0, Y - pad_px * f)
    px = float(ref['transform'][0])
    h0, w0 = st['mask'].shape
    w = int(round(w0 * MERC / px)); h = int(round(h0 * MERC / px))
    win = (I0, J0, X + w + pad_px * f, Y + h + pad_px * f)
    nccs, _, _ = score(st, ref, orbit, 1, win, water)
    S = sum(WEIGHT[p] * zmap(r) for p, r in zip(PRODS, nccs))
    _, mx, _, (bx, by) = cv2.minMaxLoc(S)
    sx, sy = subpix(S, bx, by)
    nc = {p: round(float(np.nan_to_num(r[by, bx])), 3) for p, r in zip(PRODS, nccs)}
    return I0 + sx, J0 + sy, w, h, px, nc, float(mx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stack'); ap.add_argument('ref'); ap.add_argument('meta'); ap.add_argument('out')
    ap.add_argument('--down', type=int, default=4); ap.add_argument('--feat-w', type=float, default=1.0)
    ap.add_argument('--prior-km', type=float, default=3.0); ap.add_argument('--prior-w', type=float, default=1.0)
    ap.add_argument('--flat-water', action='store_true', help='hydro-flattened lakes count as water (land_mask)')
    ap.add_argument('--min-overlap', type=float, default=0.3, help='least share of the frame that must fall on land')
    ap.add_argument('--centre', action='store_true', help="the anchor is the view's middle (a centre Hig gave), not just a point in it")
    a = ap.parse_args()
    WEIGHT['feat'] = a.feat_w
    global FLAT_WATER, MIN_OVERLAP
    FLAT_WATER = a.flat_water; MIN_OVERLAP = a.min_overlap
    st = np.load(a.stack); ref = np.load(a.ref); meta = json.load(open(a.meta))
    name = meta['name'].lower()
    orbits = ['asc'] if 'ascending' in name else ['desc'] if 'descending' in name else ['asc', 'desc']
    T = ref['transform']
    res = []
    for orbit in orbits:
        for water in ('dark', 'free'):
            S, pk, (w, h), px, nccs = place(st, ref, orbit, a.down, meta.get('anchor'), a.prior_km, a.prior_w, water, a.centre)
            for z, x, y in pk:
                res.append(dict(orbit=orbit, water=water, z=round(z, 2), x=x, y=y,
                                ncc={p: round(float(np.nan_to_num(r[y, x])), 3) for p, r in zip(PRODS, nccs)},
                                bounds_3857=[T[2] + x * px, T[5] - (y + h) * px, T[2] + (x + w) * px, T[5] - y * px]))
    res.sort(key=lambda r: -r['z'])
    b = res[0]
    sx, sy, w, h, px, nc, zf = refine(st, ref, b['orbit'], b['x'], b['y'], a.down, b['water'])
    X0 = T[2] + sx * px; Y1 = T[5] - sy * px
    # runner-up: the best peak elsewhere (> 1 km from this one) under the same orbit and water rule
    far = lambda r: math.hypot(r['bounds_3857'][0] - b['bounds_3857'][0], r['bounds_3857'][3] - b['bounds_3857'][3]) > 2000
    second = next((r['z'] for r in res[1:] if r['orbit'] == b['orbit'] and r['water'] == b['water'] and far(r)), None)
    best = dict(orbit=b['orbit'], water=b['water'], z=b['z'], second=second, margin=round(b['z'] - (second or 0), 2), z_fine=round(zf, 2),
                ncc=nc, bounds_3857=[X0, Y1 - h * px, X0 + w * px, Y1])
    json.dump(dict(best=best, peaks=res, prior=dict(km=a.prior_km, w=a.prior_w, centre=a.centre), flat_water=a.flat_water, min_overlap=a.min_overlap), open(a.out, 'w'), indent=1)
    print(json.dumps(best))


if __name__ == '__main__':
    main()
