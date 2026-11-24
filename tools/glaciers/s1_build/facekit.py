"""Shared bits for the ice-face experiments on a built stack (exploration).

load(name) -> dict: vv, vh (months, h, w) dB, months, T (3857 affine as 6-tuple),
faces (Hig's traces in pixel coords, with their month), lines (flow lines, px).
"""
import json, math, os
import numpy as np

OUT = '/Volumes/Nunatak/glacier_s1_build'
R = 6378137.0


def merc(lon, lat):
    return math.radians(lon) * R, R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def to_px(T, lon, lat):
    x, y = merc(lon, lat)
    return (x - T[2]) / T[0], (y - T[5]) / T[4]


def to_lonlat(T, px, py):
    x, y = T[2] + px * T[0], T[5] + py * T[4]
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def parts(geom):
    if geom['type'] == 'LineString':
        return [geom['coordinates']]
    return geom['coordinates']


def load(name):
    d = os.path.join(OUT, name)
    z = np.load(os.path.join(d, 'stack.npz'))
    T = [float(v) for v in z['transform']]          # rasterio Affine order: a, b, c, d, e, f
    T = [T[0], T[1], T[2], T[3], T[4], T[5]]
    out = {'vv': z['vv'], 'vh': z['vh'], 'months': [str(m) for m in z['months']], 'n': z['n'], 'T': T, 'dir': d}
    faces = []
    fp = os.path.join(d, 'hig_faces.json')
    if os.path.exists(fp):
        for f in json.load(open(fp)):
            pts = [np.array([to_px(T, *c) for c in p]) for p in parts(f['geom'])]
            faces.append({'id': f['id'], 'month': f['d0'][:7], 'px': pts})
    out['faces'] = faces
    lines = []
    lp = os.path.join(d, 'lines.json')
    if os.path.exists(lp):
        for f in json.load(open(lp))['features']:
            pts = [np.array([to_px(T, *c) for c in p]) for p in parts(f['geometry'])]
            lines.append({'id': f['properties']['id'], 'name': f['properties']['name'], 'px': pts})
    out['lines'] = lines
    return out


def render(vv, vh):
    """The video's colours (build.render without the Lee filter import cycle)."""
    import build
    return build.render(vv, vh)


def main_line(S, line_id=None):
    """The flow line (px) as one shapely LineString, the longest part in frame,
    reversed so distance runs up-glacier from where it leaves the frame."""
    from shapely.geometry import LineString, box
    from shapely.ops import linemerge
    h, w = S['vv'].shape[1:]
    frame = box(0, 0, w - 1, h - 1)
    L = [l for l in S['lines'] if (line_id is None and l['name']) or l['id'] == line_id][0]
    g = linemerge([LineString(p) for p in L['px']]).intersection(frame)
    if g.geom_type != 'LineString':
        g = max(g.geoms, key=lambda x: x.length)
    return LineString(list(g.coords)[::-1])     # flow lines run head -> terminus: distance from the terminus end


def kymo(S, line, half=20, step=1.0, smooth=1.5):
    """Sample each month along `line` (px), averaging across +-half px
    perpendicular (in dB). Returns dist (px), and (months, n) arrays vv, vh."""
    from scipy.ndimage import map_coordinates, gaussian_filter
    n = int(line.length / step)
    s = np.arange(n) * step
    P = np.array([line.interpolate(d).coords[0] for d in s])
    tang = np.gradient(P, axis=0); tang /= np.linalg.norm(tang, axis=1)[:, None] + 1e-9
    nor = np.stack([-tang[:, 1], tang[:, 0]], 1)
    offs = np.arange(-half, half + 1)
    X = P[:, 0][:, None] + nor[:, 0][:, None] * offs
    Y = P[:, 1][:, None] + nor[:, 1][:, None] * offs
    out = {}
    for b in ('vv', 'vh'):
        A = np.empty((len(S['months']), n), np.float32)
        for k in range(len(S['months'])):
            img = S[b][k]
            if smooth:
                img = gaussian_filter(np.nan_to_num(img, nan=np.nanmedian(img)), smooth)
            A[k] = np.median(map_coordinates(img, [Y.ravel(), X.ravel()], order=1).reshape(X.shape), 1)
        out[b] = A
    return s, out


def crossing(line, trace_px):
    """Distance along `line` where a traced face crosses it (None if not)."""
    from shapely.geometry import LineString
    best = None
    for p in trace_px:
        x = line.intersection(LineString(p))
        if x.is_empty:
            continue
        pts = [x] if x.geom_type == 'Point' else list(getattr(x, 'geoms', []))
        for q in pts:
            d = line.project(q)
            best = d if best is None else max(best, d)
    return best


def step_score(K, w=8):
    """Per month and position: brighter up-glacier than down-glacier, in
    both bands (row-standardised), i.e. water -> ice going up the line."""
    sc = 0
    for b in ('vv', 'vh'):
        A = K[b].astype(np.float64)
        A = (A - A.mean(1, keepdims=True)) / (A.std(1, keepdims=True) + 1e-6)
        c = np.cumsum(np.pad(A, ((0, 0), (1, 0))), 1)
        n = A.shape[1]
        S = np.full(A.shape, np.nan)
        for x in range(w, n - w):
            S[:, x] = (c[:, x + w] - c[:, x]) / w - (c[:, x] - c[:, x - w]) / w
        sc = sc + S
    sc = sc / 2
    sc[:, :w] = sc[:, w:w + 1]          # a face at (or beyond) the frame edge
    sc[:, -w:] = -9
    return sc


def track(sc, lam=0.02, adv=4.0, maxjump=None):
    """Viterbi path through months maximising step score minus lam per px
    moved (adv times more for an advance, i.e. moving down-line)."""
    m, n = sc.shape
    x = np.arange(n)
    D = np.abs(x[:, None] - x[None, :]).astype(np.float64)          # [prev, cur]
    P = lam * D * np.where(x[None, :] < x[:, None], adv, 1.0)
    if maxjump:
        P[D > maxjump] = 1e9
    acc = np.nan_to_num(sc[0], nan=-9)
    back = np.zeros((m, n), int)
    for k in range(1, m):
        tot = acc[:, None] - P
        back[k] = tot.argmax(0)
        acc = tot.max(0) + np.nan_to_num(sc[k], nan=-9)
    path = np.zeros(m, int); path[-1] = acc.argmax()
    for k in range(m - 1, 0, -1):
        path[k - 1] = back[k, path[k]]
    return path


def outline_mask(S, gid_json):
    """Raster mask (h, w) of the RGI outline from /glaciers/api/glacier/<gid>/."""
    from PIL import Image, ImageDraw
    g = json.load(open(gid_json))['glacier']['outline']
    h, w = S['vv'].shape[1:]
    im = Image.new('L', (w, h), 0); d = ImageDraw.Draw(im)
    polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
    for poly in polys:
        for i, ring in enumerate(poly):
            d.polygon([to_px(S['T'], *c) for c in ring], fill=255 if i == 0 else 0)
    return np.asarray(im) > 0


def anomaly(S, smooth=1.5):
    """(months, h, w) VV+VH in dB minus each month's frame median: removes
    the frame-wide seasonal shifts (wet snow), keeps the local contrast."""
    from scipy.ndimage import gaussian_filter
    m = len(S['months'])
    X = np.empty(S['vv'].shape, np.float32)
    for k in range(m):
        a = S['vv'][k] - np.nanmedian(S['vv'][k]) + S['vh'][k] - np.nanmedian(S['vh'][k])
        X[k] = gaussian_filter(np.nan_to_num(a), smooth) if smooth else a
    return X


def change_date(X, min_seg=3):
    """Best single step DOWN per pixel (ice -> water): index c of the first
    'after' month, the drop (mean before - mean after, dB) and its t value."""
    m, h, w = X.shape
    C = np.zeros((h, w), np.int16); D = np.zeros((h, w), np.float32); Tv = np.zeros((h, w), np.float32)
    ks = np.arange(min_seg, m - min_seg + 1)
    for r0 in range(0, h, 64):
        x = X[:, r0:r0 + 64].astype(np.float64)
        cs = np.cumsum(x, 0); cs2 = np.cumsum(x * x, 0)
        tot, tot2 = cs[-1], cs2[-1]
        best_t = np.full(x.shape[1:], -np.inf); best_c = np.zeros(x.shape[1:], int); best_d = np.zeros(x.shape[1:])
        for c in ks:
            s1, q1 = cs[c - 1], cs2[c - 1]
            s2, q2 = tot - s1, tot2 - q1
            m1, m2 = s1 / c, s2 / (m - c)
            var = (q1 - c * m1 ** 2 + q2 - (m - c) * m2 ** 2) / (m - 2)
            t = (m1 - m2) / np.sqrt(np.maximum(var, 1e-6) * (1 / c + 1 / (m - c)))
            better = t > best_t
            best_t[better] = t[better]; best_c[better] = c; best_d[better] = (m1 - m2)[better]
        C[r0:r0 + 64] = best_c; D[r0:r0 + 64] = best_d; Tv[r0:r0 + 64] = best_t
    return C, D, Tv


def pieces_crossing(contours, line, near=6):
    """Contours (skimage row/col arrays) that cross or touch the flow line,
    as shapely LineStrings in (x, y) px; the one nearest the line's
    down-glacier end first."""
    from shapely.geometry import LineString
    out = []
    for c in contours:
        if len(c) < 4:
            continue
        L = LineString(c[:, ::-1])
        if L.distance(line) <= near:
            x = L.intersection(line)
            d = line.project(x.centroid if not x.is_empty else L.centroid)
            out.append((d, L))
    out.sort(key=lambda t: t[0])
    return out


def faces_from_cdate(C, ch, M, k, line, m):
    """Face at month index k: the contour between water by month k and ice
    after it, inside changed-or-outline pixels; pieces crossing the line."""
    from skimage.measure import find_contours
    cm = np.where(ch, C, m + 5).astype(np.float64)
    valid = ch | M
    cs = find_contours(cm, k + 0.5, mask=valid)
    return pieces_crossing(cs, line)


def faces_from_threshold(X, k, M, line, open_r=2):
    """Face at month k from that month alone: Otsu on the anomaly inside the
    outline, opened; the water body touching the line nearest its down-glacier
    end; its edge inside the outline."""
    from skimage.filters import threshold_otsu
    from skimage.morphology import binary_opening, disk
    from skimage.measure import label, find_contours
    x = X[k]
    thr = threshold_otsu(x[M])
    water = binary_opening(x < thr, disk(open_r))
    lab = label(water)
    L = line
    pick = 0
    for d in np.arange(0, L.length, 2.0):
        px, py = L.interpolate(d).coords[0]
        v = lab[int(round(py)), int(round(px))]
        if v:
            pick = v; break
        if d > 80 and not pick:
            break
    if not pick:
        return []
    body = (lab == pick).astype(np.float64)
    cs = find_contours(body, 0.5, mask=M)
    return pieces_crossing(cs, line)


def score(pred, trace_px, step=2.0):
    """Mean and max distance (px) from the traced face to the detected one,
    and the reverse mean over the detected part within the trace's reach."""
    from shapely.geometry import LineString, MultiLineString
    if not pred:
        return None
    P = MultiLineString([p for _d, p in pred])
    tr = MultiLineString([LineString(p) for p in trace_px])
    a = [P.distance(tr.interpolate(s)) for s in np.arange(0, tr.length, step)]
    from shapely.geometry import Point
    b = []
    for g in P.geoms:
        for s in np.arange(0, g.length, step):
            q = g.interpolate(s)
            if tr.distance(q) < 60:
                b.append(tr.distance(q))
    return {'trace_to_pred_mean': float(np.mean(a)), 'trace_to_pred_max': float(np.max(a)),
            'pred_to_trace_mean': float(np.mean(b)) if b else None}


def faces_hybrid(X, k, M, line, along, open_r=2, gap=12, edge=3):
    """Month k's face: Otsu water mask (opened, holes filled: icebergs,
    bright wind patches), the water body just down-glacier of the tracked
    position `along` (px on the line) and the ice body just up-glacier of
    it, and only the water edge within `edge` px of that ice body."""
    from scipy.ndimage import binary_fill_holes, binary_dilation
    from skimage.filters import threshold_otsu
    from skimage.morphology import binary_opening, disk
    from skimage.measure import label, find_contours
    x = X[k]
    thr = threshold_otsu(x[M])
    water = binary_fill_holes(binary_opening(x < thr, disk(open_r)))
    wl = label(water); il = label(~water & M)

    def body(lab, d0, d1, sgn):
        for d in np.arange(d0, d1, sgn * 2.0):
            if d < 0 or d > line.length:
                break
            px, py = line.interpolate(d).coords[0]
            v = lab[int(round(py)), int(round(px))]
            if v:
                return lab == v
        return None
    wb = body(wl, along - 1, along - gap * 3, -1)
    ib = body(il, along + 1, along + gap * 3, 1)
    if wb is None or ib is None:
        return []
    near = binary_dilation(ib, disk(edge))
    cs = find_contours(wb.astype(np.float64), 0.5, mask=near)
    return pieces_crossing(cs, line, near=8)


def trim_across(pieces, line, along, dmax=35):
    """Keep the run of each piece whose points project onto the flow line
    within dmax px of the tracked position: a face runs across the valley,
    a shore along it. Returns the longest run that crosses or nears the line."""
    from shapely.geometry import LineString, Point
    best = None
    for _d, g in pieces:
        pts = list(g.coords)
        keep = [abs(line.project(Point(p)) - along) <= dmax for p in pts]
        run = []
        for p, k in zip(pts + [None], keep + [False]):
            if k:
                run.append(p); continue
            if len(run) >= 4:
                L = LineString(run)
                dist = L.distance(line)
                if dist <= 8 and (best is None or L.length > best.length):
                    best = L
            run = []
    return [] if best is None else [(along, best)]


# --- second pass (Hig, 2026-11-23): the margin of CONSISTENT glacier --------
# Floating ice in the fjord is bright but different every month, so a sharp
# bright edge is not the face. The face is where ice that stays bright over
# neighbouring months meets the fjord. Rock is masked by texture: its speckle
# pattern repeats month to month, moving ice and water do not, so the fjord
# walls end the face line.

def texture_corr(S, hp_sigma=4, win=9):
    """Mean month-to-month local correlation of high-passed VV+VH."""
    from scipy.ndimage import uniform_filter, gaussian_filter
    R = (S['vv'] + S['vh']).astype(np.float32)
    R = np.nan_to_num(R - np.nanmedian(R, axis=(1, 2), keepdims=True))
    cor = np.zeros(R.shape[1:], np.float32)
    prev = None
    for k in range(len(R)):
        hp = R[k] - gaussian_filter(R[k], hp_sigma)
        if prev is not None:
            ab = uniform_filter(prev * hp, win); aa = uniform_filter(prev * prev, win); bb = uniform_filter(hp * hp, win)
            cor += ab / np.sqrt(aa * bb + 1e-6)
        prev = hp
    return cor / (len(R) - 1)


def water_months(X, channel):
    """(months, h, w) bool: each month's water, Otsu inside the channel."""
    from skimage.filters import threshold_otsu
    return np.stack([x < threshold_otsu(x[channel]) for x in X])


def faces_consistent(X, k, channel, line, thr, win=2, q=25, min_px=1500, edge=3, back=0, open_r=2,
                     W=None, hist=12, max_wet=0.25, readvance=6, whole=False, steady=None, fwin=2,
                     fsmooth=3.0, fstat='median', connect=None):
    """Month k: glacier = channel pixels whose q-th percentile brightness over
    months k-win..k+win (k-win..k with back) is above thr (bright nearly
    every month; thr None = Otsu inside the channel, per month), the
    components touching the flow line; fjord = the rest of the channel
    touching the line down-glacier of them. The face is the glacier's edge
    against the fjord, inside the channel (so it stops at the walls)."""
    from scipy.ndimage import binary_fill_holes, binary_dilation, binary_opening
    from skimage.measure import label, find_contours
    from skimage.morphology import disk
    m = len(X)
    lo, hi = max(0, k - win), min(m, k + (0 if back else win) + 1)
    P = np.percentile(X[lo:hi], q, axis=0)
    if thr is None:                 # this month's own split of the channel into ice and water
        from skimage.filters import threshold_otsu
        thr = threshold_otsu(P[channel])
    stay = P > thr
    if W is not None and k > 0:
        # floating ice comes and goes: a place that was open water in more
        # than max_wet of the previous `hist` months is fjord, unless it has
        # been ice for `readvance` months running (a real readvance)
        h0 = max(0, k - hist)
        wet = W[h0:k].mean(0) > max_wet
        if readvance and k >= readvance:
            wet &= W[k - readvance + 1:k + 1].any(0)
        stay &= ~wet
    if steady is not None:
        # floating ice changes every month, on both sides of any edge it
        # makes; glacier ice does not. Median of the month-to-month changes
        # over k-fwin..k+fwin, so the one step at a retreating face does not
        # count. steady: a dB limit, or 'otsu' (this month's split, channel)
        js = range(max(1, k - fwin), min(m, k + fwin + 1))
        from scipy.ndimage import gaussian_filter
        D = np.stack([np.abs(X[j] - X[j - 1]) for j in js])
        Fk = gaussian_filter(np.median(D, 0) if fstat == 'median' else D.mean(0), fsmooth)
        if steady == 'otsu':
            from skimage.filters import threshold_otsu
            lim = threshold_otsu(Fk[channel])
        else:
            lim = steady
        stay &= Fk < lim
    glac = binary_opening(stay & channel, disk(open_r))      # wide: cuts ice patches joined by a neck
    lab = label(glac)
    ids = set()
    pts = [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 3.0)]
    on = [lab[int(round(y)), int(round(x))] for x, y in pts]
    for v in on:
        if v and (lab == v).sum() >= min_px:
            ids.add(v)
    if connect and ids:
        ids = long_on_line(on, ids, connect / 3.0)
    if not ids:
        return [], None, None
    glac = binary_fill_holes(np.isin(lab, list(ids)))
    # the fjord: non-glacier channel touching the line, down-glacier of the
    # first glacier pixel met going up the line
    first = next((i for i, v in enumerate(on) if v in ids), None)
    fl = label(channel & ~glac)
    fids = {fl[int(round(y)), int(round(x))] for x, y in pts[:max(first, 1)]} - {0}
    if not fids:
        return [], glac, None
    fjord = np.isin(fl, list(fids))
    if whole:
        # the glacier's whole edge inside the channel: it stops where the
        # channel does, at the fjord walls; the piece crossing the line
        # furthest down-glacier
        cs = find_contours(glac.astype(np.float64), 0.5, mask=channel)
        return pieces_crossing(cs, line, near=10), glac, fjord
    mask = channel & binary_dilation(fjord, disk(edge))
    cs = find_contours(glac.astype(np.float64), 0.5, mask=mask)
    return pieces_crossing(cs, line, near=10), glac, fjord


# --- third pass (Hig, 2026-11-23): is the brightness PATTERN the same as the
# month before? Glacier detail persists while it flows down-valley; floating
# ice jumps about. Offset tracking between consecutive months: the best local
# normalised cross-correlation over shifts, and the shift that gave it.

def texture(S, hp_sigma=4, smooth=1.0):
    """(months, h, w) high-passed VV+VH (dB, frame median removed)."""
    from scipy.ndimage import gaussian_filter
    R = (S['vv'] + S['vh']).astype(np.float32)
    R = np.nan_to_num(R - np.nanmedian(R, axis=(1, 2), keepdims=True))
    return np.stack([gaussian_filter(r, smooth) - gaussian_filter(r, hp_sigma) for r in R])


def track_pair(a, b, R=24, step=2, win=15):
    """For each pixel of b (month k): max over shifts s (|s| <= R) of the
    local NCC between b and a shifted by s (a = month k-1), i.e. b(x) ~
    a(x - s): s is the displacement from k-1 to k. Returns ncc, dy, dx."""
    from scipy.ndimage import uniform_filter
    h, w = b.shape
    bb = uniform_filter(b * b, win)
    best = np.full((h, w), -1.0, np.float32); by = np.zeros((h, w), np.int8); bx = np.zeros((h, w), np.int8)
    for dy in range(-R, R + 1, step):
        for dx in range(-R, R + 1, step):
            s = np.roll(np.roll(a, dy, 0), dx, 1)          # s(x) = a(x - (dy, dx))
            ab = uniform_filter(s * b, win); ss = uniform_filter(s * s, win)
            c = ab / np.sqrt(ss * bb + 1e-6)
            better = c > best
            best[better] = c[better]; by[better] = dy; bx[better] = dx
    return best, by, bx


def flow_direction(vx, vy, frac, sigma=25):
    """Unit down-flow direction everywhere: the steady flow, weighted by how
    often it was matched, smoothed wide so it reaches the fjord and the face."""
    from scipy.ndimage import gaussian_filter
    w = np.where(np.isfinite(vx) & np.isfinite(vy), frac, 0)
    ux = gaussian_filter(np.nan_to_num(vx) * w, sigma); uy = gaussian_filter(np.nan_to_num(vy) * w, sigma)
    n = np.hypot(ux, uy) + 1e-9
    return ux / n, uy / n


def flowing(ncc, dy, dx, days, ux, uy, min_ncc=0.45, vmin=1.0, vmax=15.0, max_cos=0.5, coh=2.0, px_m=10.0):
    """One pass pair: pixels whose texture reappears shifted DOWN-FLOW at a
    glacier's speed (vmin..vmax m/day), agreeing with their neighbours'
    shifts. A zero shift is not glacier: the fjord matches itself there."""
    from scipy.ndimage import median_filter
    sx, sy = dx.astype(np.float32), dy.astype(np.float32)
    v = np.hypot(sx, sy) * px_m / days                 # m/day
    cosang = (sx * ux + sy * uy) / (np.hypot(sx, sy) + 1e-6)
    mx, my = median_filter(sx, 9), median_filter(sy, 9)
    agree = np.hypot(sx - mx, sy - my) <= coh
    return (ncc > min_ncc) & (v >= vmin) & (v <= vmax) & (cosang >= max_cos) & agree


def fill_flow(vx, vy, frac, sigma=15, min_frac=0.3):
    """The steady flow, kept where it was matched often, filled elsewhere by
    normalised convolution (so it reaches the face and the fjord)."""
    from scipy.ndimage import gaussian_filter
    ok = np.isfinite(vx) & np.isfinite(vy) & (frac >= min_frac)
    w = gaussian_filter(ok.astype(np.float32), sigma) + 1e-6
    fx = gaussian_filter(np.where(ok, vx, 0).astype(np.float32), sigma) / w
    fy = gaussian_filter(np.where(ok, vy, 0).astype(np.float32), sigma) / w
    return np.where(ok, vx, fx), np.where(ok, vy, fy)


def predicted_match(A, B, vx, vy, days, Mbar, win=25):
    """How well pass B is predicted by pass A carried along the steady flow
    (vx, vy px/day for `days`) plus a static pattern Mbar (crevasse fields
    that are always bright): local R^2 of B on [A advected, Mbar]."""
    from scipy.ndimage import map_coordinates, uniform_filter
    h, w = B.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    Aw = map_coordinates(A, [yy - vy * days, xx - vx * days], order=1, mode='nearest')   # B(x) ~ A(x - v t)
    def ncc(p, q):
        pq = uniform_filter(p * q, win); pp = uniform_filter(p * p, win); qq = uniform_filter(q * q, win)
        return pq / np.sqrt(pp * qq + 1e-6)
    r1, r2, r12 = ncc(B, Aw), ncc(B, Mbar), ncc(Aw, Mbar)
    r2v = (r1 ** 2 + r2 ** 2 - 2 * r1 * r2 * r12) / np.maximum(1 - r12 ** 2, 1e-3)
    return np.clip(r2v, 0, 1), r1


# --- fourth pass (Hig, 2026-11-23): fewer, surer faces. Not every month
# needs one; score each month's face by how clearly that month's OWN image
# shows it, and keep the clear ones.

def face_confidence(Xk, glac, fjord, channel, piece, r=8, step=2.0, sep=3.0, thr=None):
    """Score one face (a shapely LineString in px) in month image Xk.
    Along the face, every `step` px: local mean of Xk on the glacier side
    minus on the fjord side (discs of radius r, inside the channel), and
    whether the fjord side is mostly water (below thr, Otsu in the channel).
    Returns dict: clear = fraction of the face length with a step > sep dB
    and a watery fjord side; med = median step (dB); water = median water
    fraction on the fjord side; n = sample count."""
    from scipy.ndimage import uniform_filter
    from skimage.filters import threshold_otsu
    if thr is None:
        thr = threshold_otsu(Xk[channel])
    g = (glac & channel).astype(np.float32)
    f = (channel & ~glac).astype(np.float32)
    if fjord is not None:
        f = (fjord & channel).astype(np.float32)
    s = 2 * r + 1
    cg, cf = uniform_filter(g, s), uniform_filter(f, s)
    mg = uniform_filter(Xk * g, s) / np.maximum(cg, 1e-6)
    mf = uniform_filter(Xk * f, s) / np.maximum(cf, 1e-6)
    wf = uniform_filter((Xk < thr) * f, s) / np.maximum(cf, 1e-6)
    h, w = Xk.shape
    d, wat = [], []
    for t in np.arange(0, piece.length, step):
        x, y = piece.interpolate(t).coords[0]
        i, j = int(round(y)), int(round(x))
        if not (0 <= i < h and 0 <= j < w) or not channel[i, j]:
            continue
        if cg[i, j] < 0.15 or cf[i, j] < 0.15:      # one side barely present: a wall, not the face
            continue
        d.append(mg[i, j] - mf[i, j]); wat.append(wf[i, j])
    if not d:
        return {'clear': 0.0, 'med': np.nan, 'water': np.nan, 'n': 0}
    d, wat = np.array(d), np.array(wat)
    return {'clear': float(np.mean((d > sep) & (wat > 0.5))), 'med': float(np.median(d)),
            'water': float(np.median(wat)), 'n': int(len(d))}


def from_top(on, ids, gap):
    """Glacier ice is continuous with the trunk (Hig's "consistent
    glacier"): walking DOWN the line from its up-glacier end, keep the bright
    components met while the line stays on them, across gaps of at most
    `gap` samples (a crevasse, a dark stripe), and stop at the first longer
    stretch off them. A berg patch out in the fjord is then fjord, not the
    glacier's front. `on` = component label per line sample, exit first."""
    keep, run, started = set(), 0, False
    for v in reversed(on):
        if v in ids:
            keep.add(v); run = 0; started = True
        elif started:
            run += 1
            if run > gap:
                break
    return keep


def long_on_line(on, ids, n):
    """Only the components the line runs along for at least n samples: the
    glacier's front component runs a long way up the line, a berg patch in
    the fjord is short where the line crosses it."""
    import collections
    c = collections.Counter(v for v in on if v in ids)
    return {v for v in ids if c[v] >= n}


def faces_single(Xk, channel, line, sigma=3.0, open_r=4, min_px=1500, connect=None, wet=None):
    """Month k's own face: smooth this month's image, split the channel by
    Otsu, keep the bright components on the flow line and the dark fjord
    down-glacier of them; the edge between, inside the channel. Same
    returns as faces_consistent."""
    from scipy.ndimage import gaussian_filter, binary_fill_holes, binary_opening
    from skimage.filters import threshold_otsu
    from skimage.measure import label, find_contours
    from skimage.morphology import disk
    G = gaussian_filter(Xk, sigma)
    thr = threshold_otsu(G[channel])
    stay = (G > thr) & channel
    if wet is not None:             # open water through much of the past year: fjord (packed melange is not glacier)
        stay &= ~wet
    glac = binary_opening(stay, disk(open_r))
    lab = label(glac)
    pts = [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 3.0)]
    on = [lab[int(round(y)), int(round(x))] for x, y in pts]
    ids = {v for v in on if v and (lab == v).sum() >= min_px}
    if connect and ids:
        ids = long_on_line(on, ids, connect / 3.0)
    if not ids:
        return [], None, None
    glac = binary_fill_holes(np.isin(lab, list(ids)))
    first = next(i for i, v in enumerate(on) if v in ids)
    fl = label(channel & ~glac)
    fids = {fl[int(round(y)), int(round(x))] for x, y in pts[:max(first, 1)]} - {0}
    fjord = np.isin(fl, list(fids)) if fids else None
    cs = find_contours(glac.astype(np.float64), 0.5, mask=channel)
    return pieces_crossing(cs, line, near=10), glac, fjord


def wall_to_wall(piece, channel, near=6):
    """(spans, straight): both ends of the face within `near` px of the
    channel edge (the fjord walls), and chord / length (1 = straight)."""
    from scipy.ndimage import distance_transform_edt
    D = distance_transform_edt(channel)
    h, w = channel.shape
    ends = [piece.coords[0], piece.coords[-1]]
    ok = all(D[min(h - 1, max(0, int(round(y)))), min(w - 1, max(0, int(round(x))))] <= near for x, y in ends)
    (x0, y0), (x1, y1) = ends
    return ok, float(np.hypot(x1 - x0, y1 - y0) / max(piece.length, 1e-6))


def drop_spikes(along, tol=20.0, up_tol=50.0):
    """Indices to keep from a time series of face positions (px along the
    line, up-glacier positive). First, repeatedly drop the worst face lying
    more than tol px down-glacier of BOTH its kept neighbours (a winter ice
    sheet or packed melange read as glacier: it advances and retreats
    again); then, the same for faces more than up_tol px UP-glacier of both
    (another lake, a dark patch). Down-fjord first: those are the common
    error, and next to them a true face looks like an up-glacier spike. A
    real retreat sits between its neighbours and is kept."""
    keep = list(range(len(along)))
    for sign, lim in ((1, tol), (-1, up_tol)):
        while len(keep) > 2:
            worst, wi = 0.0, None
            for j in range(len(keep)):
                a = along[keep[j]]
                nb = [along[keep[i]] for i in (j - 1, j + 1) if 0 <= i < len(keep)]
                ex = (min(nb) - a) if sign > 0 else (a - max(nb))
                if ex > lim and ex > worst:
                    worst, wi = ex, j
            if wi is None:
                break
            keep.pop(wi)
    return keep


def across(piece, line, at, band=25.0, step=2.0):
    """Fraction of the face lying within `band` px (along the flow line) of
    where it crosses it (`at`): a face across the fjord scores ~1; one with
    a long leg down the fjord (a patch edge) or up a margin scores less."""
    ds = np.array([line.project(piece.interpolate(t)) for t in np.arange(0, piece.length, step)])
    return float(np.mean(np.abs(ds - at) <= band)) if len(ds) else 0.0


# --- lake-terminating fronts (Hig, 2026-11-24, Sitkagi): the ice face is the
# lagoon's ice cliff, followed to its logical end, i.e. the part of the
# shore that is CHANGING. Stable shore (moraine, beach) is not face.

def changing_mask(X, drop=3.0, t=6.0, open_r=1):
    """Pixels that turned from bright (ice) to dark (water) once in the
    record: the best single step down is > drop dB with t value > t."""
    from scipy.ndimage import binary_opening
    from skimage.morphology import disk
    C, D, Tv = change_date(X)
    return binary_opening((D > drop) & (Tv > t), disk(open_r)), C


def lake_mask(Xk, line, sigma=2.0, open_r=2, min_px=200):
    """This month's lagoon: dark water (Otsu over the frame, smoothed) in a
    component the flow line crosses that does not touch the frame edge
    (that is the sea); the largest such."""
    from scipy.ndimage import gaussian_filter, binary_opening, binary_fill_holes
    from skimage.filters import threshold_otsu
    from skimage.measure import label
    from skimage.morphology import disk
    G = gaussian_filter(Xk, sigma)
    water = binary_opening(G < threshold_otsu(G), disk(open_r))
    lab = label(water)
    h, w = lab.shape
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    on = {lab[int(round(y)), int(round(x))] for x, y in
          [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 2.0)]} - {0} - edge
    best = max(on, key=lambda v: (lab == v).sum(), default=None)
    if best is None or (lab == best).sum() < min_px:
        return None
    return binary_fill_holes(lab == best)


def lake_face(lake, changing, line, near=5, min_len=15):
    """The lagoon's shore where it is changing: shore points within `near`
    px of a pixel that turned from ice to water; the longest run of such
    points, as a LineString in px. (along, LineString) or None."""
    from scipy.ndimage import distance_transform_edt
    from skimage.measure import find_contours
    from shapely.geometry import LineString, Point
    D = distance_transform_edt(~changing)
    best = None
    for c in find_contours(lake.astype(np.float64), 0.5):
        ok = D[np.clip(np.round(c[:, 0]).astype(int), 0, D.shape[0] - 1),
               np.clip(np.round(c[:, 1]).astype(int), 0, D.shape[1] - 1)] <= near
        closed = np.allclose(c[0], c[-1])
        # runs of consecutive kept points (a closed contour wraps round)
        if closed and ok.all():
            runs = [np.arange(len(c))]
        else:
            idx = np.arange(len(c))
            if closed and ok[0] and ok[-1]:
                k0 = np.argmin(ok)                   # start the walk at a gap
                idx = np.roll(idx, -k0); ok = np.roll(ok, -k0)
            runs, cur = [], []
            for i, o in zip(idx, ok):
                if o:
                    cur.append(i)
                elif cur:
                    runs.append(cur); cur = []
            if cur:
                runs.append(cur)
        for r in runs:
            if len(r) < 2:
                continue
            L = LineString(c[r][:, ::-1])
            if L.length >= min_len and (best is None or L.length > best.length):
                best = L
    if best is None:
        return None
    x = best.intersection(line)
    along = line.project(x.centroid if not x.is_empty else Point(best.interpolate(best.project(line.centroid)).coords[0]))
    if x.is_empty:
        from shapely.ops import nearest_points
        along = line.project(nearest_points(best, line)[1])
    return along, best


# --- second pass (Hig, 2026-11-24, South Sawyer): months with no confident
# face, searched near the nearest confident one. Winter melange is bright
# enough that a split of the whole channel calls it glacier (Nov 2017), and
# some faces are a bright LINE between dimmer ice and water (Feb 2024).

def faces_local(Xk, channel, line, prior, along_prior, D=40, sigma=2.0, ridge=True, open_r=2):
    """Month k's face within D px of a prior face (LineString px) at
    along_prior on the line. Returns (pieces, glac, fjord, thr). Up-glacier of the band is glacier, down-glacier
    fjord; inside the band, this band's own Otsu split of the smoothed image,
    plus bright ridges (a face seen as a bright line) as glacier. Same
    returns as faces_consistent."""
    from scipy.ndimage import gaussian_filter, distance_transform_edt, binary_opening, binary_fill_holes
    from skimage.filters import threshold_otsu, sato
    from skimage.measure import label, find_contours
    from skimage.morphology import disk
    h, w = Xk.shape
    on = np.ones((h, w), bool)
    for t in np.arange(0, prior.length, 0.5):
        x, y = prior.interpolate(t).coords[0]
        i, j = int(round(y)), int(round(x))
        if 0 <= i < h and 0 <= j < w:
            on[i, j] = False
    band = (distance_transform_edt(on) <= D) & channel
    if band.sum() < 50:
        return [], None, None
    outside = label(channel & ~band)

    def at(d):
        d = min(max(d, 0), line.length)
        x, y = line.interpolate(d).coords[0]
        return int(round(y)), int(round(x))
    # the first outside-band channel pixel on the line beyond the band, each way
    up = next((outside[at(d)] for d in np.arange(along_prior + D, line.length, 2.0) if outside[at(d)]), 0)
    dn = next((outside[at(d)] for d in np.arange(along_prior - D, 0, -2.0) if outside[at(d)]), 0)
    if not up:
        return [], None, None
    G = gaussian_filter(Xk, sigma)
    thr = threshold_otsu(G[band])
    bright = (G > thr) & band
    if ridge:
        R = sato(G, sigmas=(1, 2), black_ridges=False)
        bright |= (R > np.percentile(R[band], 90)) & band
    glac = binary_opening((outside == up) | bright, disk(open_r)) | (outside == up)
    lab = label(glac)
    ui, uj = next(at(d) for d in np.arange(along_prior + D, line.length, 2.0) if outside[at(d)] == up)
    glac = binary_fill_holes(lab == lab[ui, uj])
    fl = label(channel & ~glac)
    fjord = None
    if dn:
        di, dj = next(at(d) for d in np.arange(along_prior - D, 0, -2.0) if outside[at(d)] == dn)
        if fl[di, dj]:
            fjord = fl == fl[di, dj]
    cs = find_contours(glac.astype(np.float64), 0.5, mask=channel)
    # the band's own split is what "water" means here (winter melange is
    # not dark against the whole channel): face_confidence(thr=...)
    return pieces_crossing(cs, line, near=10), glac, fjord, thr


# --- the face as the bright line (Hig, 2026-11-24, South Sawyer): a calving
# cliff facing the radar returns strongly, so the face is often the
# brightest thing in the scene; its DOWN-FJORD edge is the face. Plumes and
# floating ice, which the edge methods follow, are dimmer than it.

def line_coords(line, channel, step=1.0):
    """For every channel pixel: s = distance along the line of its nearest
    line point (px), n = signed distance across (left +). Returns (s, n)
    arrays (NaN off the channel)."""
    from scipy.spatial import cKDTree
    ds = np.arange(0, line.length, step)
    P = np.array([line.interpolate(d).coords[0] for d in ds])
    T = np.gradient(P, axis=0); T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-9)
    yy, xx = np.nonzero(channel)
    _, i = cKDTree(P).query(np.c_[xx, yy])
    v = np.c_[xx, yy] - P[i]
    s = np.full(channel.shape, np.nan); n = np.full(channel.shape, np.nan)
    s[yy, xx] = ds[i]
    n[yy, xx] = T[i, 0] * v[:, 1] - T[i, 1] * v[:, 0]
    return s, n


def face_bright(Xk, channel, line, sn, q=97, min_px=150, close_r=2, nbin=2.0, back=12):
    """The face as the down-fjord edge of the bright region the line runs
    into: pixels above the q-th percentile of the channel this month,
    closed; of the components the line crosses (min_px or more), the one
    met first going up the line; across the fjord in bins of nbin px, its
    most down-fjord pixel. Returns (along, LineString, info) or None; info:
    cover = fraction of the fjord width (at the face) with a face point,
    step = median dB from the face pixel to the water `back` px in front,
    jump = 90th percentile of along-line jumps between neighbouring bins (px)."""
    from scipy.ndimage import binary_closing
    from skimage.measure import label
    from skimage.morphology import disk
    from shapely.geometry import LineString
    s, n = sn
    B = binary_closing((Xk > np.percentile(Xk[channel], q)) & channel, disk(close_r)) & channel
    lab = label(B)
    h, w = B.shape
    comp = None
    for d in np.arange(0, line.length, 2.0):
        x, y = line.interpolate(d).coords[0]
        v = lab[int(round(y)), int(round(x))]
        if v and (lab == v).sum() >= min_px:
            comp = v
            break
    if comp is None:
        return None
    C = lab == comp
    s0 = np.nanmin(s[C])
    # the fjord width at the face: channel pixels within 15 px along of the face's front
    near = channel & (np.abs(s - s0) <= 15)
    nlo, nhi = np.nanmin(n[near]), np.nanmax(n[near])
    pts, got, total = [], 0, 0
    yy, xx = np.nonzero(C)
    for b0 in np.arange(nlo, nhi, nbin):
        total += 1
        sel = (n[yy, xx] >= b0) & (n[yy, xx] < b0 + nbin)
        if not sel.any():
            continue
        j = np.argmin(s[yy[sel], xx[sel]])
        pts.append((s[yy[sel], xx[sel]][j], b0, xx[sel][j], yy[sel][j]))
        got += 1
    if len(pts) < 5:
        return None
    pts.sort(key=lambda p: p[1])
    S_ = np.array([p[0] for p in pts])
    # keep the run of bins around the line whose front stays near the median
    # (a lobe of crevassed ice far up-glacier is not the face)
    med = np.median(S_)
    ok = np.abs(S_ - med) <= 25
    L = LineString([(p[2], p[3]) for p, o in zip(pts, ok) if o])
    if L.length < 5:
        return None
    # step: the face pixel against the water just in front, along the line direction
    steps = []
    for p, o in zip(pts, ok):
        if not o:
            continue
        x, y = p[2], p[3]
        sel = near & (np.abs(n - p[1]) <= nbin) & (s < p[0] - back * 0.5) & (s >= p[0] - back * 1.5)
        if sel.any():
            steps.append(Xk[y, x] - np.median(Xk[sel]))
    Sk = S_[ok]
    jump = float(np.percentile(np.abs(np.diff(Sk)), 90)) if len(Sk) > 2 else 99.0
    info = {'cover': float(ok.sum()) / max(total, 1), 'step': float(np.median(steps)) if steps else 0.0,
            'jump': jump, 'n': int(ok.sum()), 'width': float(nhi - nlo), 'nbin': nbin,
            # the front, bin by bin: (s, n, x, y), for joining with an edge face
            'pts': [(float(p[0]), float(p[1]), int(p[2]), int(p[3])) for p, o in zip(pts, ok) if o]}
    x = L.intersection(line)
    from shapely.ops import nearest_points
    along = line.project(x.centroid) if not x.is_empty else line.project(nearest_points(L, line)[1])
    return along, L, info


def cliff_hybrid(edge, info, sn, max_ds=20.0, max_join=15.0, min_cover=0.3):
    """The face on the bright cliff where the cliff is seen, carried to the
    fjord walls by an edge face (Hig, 2026-11-24, South Sawyer Aug 2019: the
    edge face sat ~125 m in front of a clear cliff). edge: LineString px;
    info: face_bright's. Cliff bins more than max_ds px along the line from
    the edge face at the same place across are dropped (a tributary's edge,
    a lobe far up-glacier); the edge face's parts beyond the cliff on each
    side are kept only if they meet the cliff within max_join px along the
    line. Returns (LineString, cover) or None (cover = cliff part / width)."""
    from shapely.geometry import LineString
    s, n = sn
    h, w = s.shape
    E = []
    for t in np.arange(0, edge.length, 0.5):
        x, y = edge.interpolate(t).coords[0]
        i, j = int(round(y)), int(round(x))
        if 0 <= i < h and 0 <= j < w and np.isfinite(s[i, j]):
            E.append((s[i, j], n[i, j], x, y))
    if len(E) < 4 or not info.get('pts'):
        return None
    E = np.array(E)
    keep = []
    for ps, pn, px, py in info['pts']:
        near = np.abs(E[:, 1] - pn) <= info['nbin']
        if near.any() and abs(ps - np.median(E[near, 0])) <= max_ds:
            keep.append((ps, pn, px, py))
    if not keep:
        return None
    keep.sort(key=lambda p: p[1])
    cover = len(keep) * info['nbin'] / max(info['width'], 1e-6)
    if cover < min_cover:
        return None
    n0, n1 = keep[0][1], keep[-1][1]
    lo = E[E[:, 1] < n0 - info['nbin']]
    hi = E[E[:, 1] > n1 + info['nbin']]
    out = []
    if len(lo) and abs(lo[np.argmax(lo[:, 1]), 0] - keep[0][0]) <= max_join:
        out += [(x, y) for _, _, x, y in sorted(lo.tolist(), key=lambda r: r[1])]
    out += [(x, y) for _, _, x, y in keep]
    if len(hi) and abs(hi[np.argmin(hi[:, 1]), 0] - keep[-1][0]) <= max_join:
        out += [(x, y) for _, _, x, y in sorted(hi.tolist(), key=lambda r: r[1])]
    L = LineString(out).simplify(0.7)
    return (L, min(1.0, cover)) if L.length > 5 else None
