"""Find where a video's static product sits on the 3DEP radar simulation.

Unknowns: ground pixel size s (m), x/y ground-pixel ratio (1.0 = Web
Mercator; cos(lat) ~ 0.52 = plain lat/lon degrees), and the offset. Search:
for each (s, ratio), resample the video product to the reference grid and
run normalised cross-correlation (cv2.matchTemplate, masked) over the whole
reference; keep the best peak. Coarse grid first, then refine around it.

Pairs tried (video product vs simulation product):
  lum  : median log luminance      vs simulated brightness
  feat : median gradient magnitude vs gradient magnitude of the simulation
The peak's height against the second-best peak elsewhere says how sure it is.
The other orbit's simulation is scored too: the right orbit should win.

usage: python match.py STACK.npz REF.npz ORBIT(asc|desc) OUT.json [--coarse 3]
"""
import argparse, json, math
import numpy as np
import cv2


def gradmag(x, sigma=1.0):
    x = cv2.GaussianBlur(x, (0, 0), sigma)
    return np.hypot(cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3))


def prep_ref(ref, key, down):
    I = ref[key].astype(np.float32)
    dem = ref['dem']
    valid = np.isfinite(dem) & (dem > 2)              # land: water and no-data carry no terrain
    if down > 1:
        I = cv2.resize(I, None, fx=1 / down, fy=1 / down, interpolation=cv2.INTER_AREA)
        valid = cv2.resize(valid.astype(np.float32), (I.shape[1], I.shape[0]), interpolation=cv2.INTER_AREA) > 0.5
    return I, valid


def score_map(tmpl, tmask, img):
    # unmasked NCC takes the FFT path; the masked part (the burned-in label)
    # is filled with the template's mean so it adds nothing to the numerator
    t = tmpl.copy()
    t[tmask < 1] = t[tmask >= 1].mean()
    return cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)


def peak2(r, excl):
    """best peak, the best outside a radius of it, and the peak's
    significance: (peak - median of the map) / robust sd of the map. Raw NCC
    favours small templates (a 50 px template finds 0.55 anywhere); the
    significance does not, so it is what candidates are ranked by."""
    r = np.nan_to_num(r, nan=-1, posinf=-1, neginf=-1)
    _, mx, _, loc = cv2.minMaxLoc(r)
    r2 = r.copy()
    x, y = loc
    r2[max(0, y - excl):y + excl + 1, max(0, x - excl):x + excl + 1] = -1
    sub = r[::3, ::3]
    med = float(np.median(sub)); sd = max(1.4826 * float(np.median(np.abs(sub - med))), 0.03)   # floor: a map that is mostly filled water is flat
    return mx, loc, r2.max(), (mx - med) / sd


def run(st, ref, orbit, prod, sizes, ratios, down, res, win=None):
    vid = st['lum_med'] if prod == 'lum' else st['feat_med']
    vmask = st['mask'].astype(np.float32)
    key = orbit
    I, valid = prep_ref(ref, key, down)
    rres = res * down
    if prod == 'feat':
        I = gradmag(I)
    # where the reference is not land, fill with its mean: it neither helps nor hurts much
    I = np.where(valid, I, I[valid].mean()).astype(np.float32)
    ox = oy = 0
    if win is not None:                         # (x0, y0, x1, y1) in this grid
        x0, y0, x1, y1 = [int(v) for v in win]
        x0, y0 = max(0, x0), max(0, y0)
        I = I[y0:y1, x0:x1]; ox, oy = x0, y0
    best = None
    rows = []
    for s in sizes:
        for q in ratios:
            w = int(round(vid.shape[1] * s * q / rres)); h = int(round(vid.shape[0] * s / rres))
            if w < 24 or h < 16 or w >= I.shape[1] or h >= I.shape[0]:
                continue
            t = cv2.resize(vid.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA)
            m = (cv2.resize(vmask, (w, h), interpolation=cv2.INTER_AREA) > 0.99).astype(np.float32)
            if prod == 'lum':
                pass
            r = score_map(t, m, I)
            mx, loc, second, z = peak2(r, max(3, int(0.1 * min(w, h))))
            loc = (loc[0] + ox, loc[1] + oy)
            rows.append((s, q, mx, second, loc, z))
            # coarse search ranks by significance; the refine (a small window,
            # where the map statistics mean little) by NCC
            score = mx if win is not None else z
            if best is None or score > best[-1]:
                best = (s, q, mx, second, loc, w, h, z, score)
    return best, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stack'); ap.add_argument('ref'); ap.add_argument('orbit'); ap.add_argument('out')
    ap.add_argument('--coarse', type=int, default=4)
    ap.add_argument('--merc', type=float, default=None, help='fix the scale: metres a pixel in Web Mercator units')
    ap.add_argument('--smin', type=float, default=2); ap.add_argument('--smax', type=float, default=60)
    a = ap.parse_args()
    st = np.load(a.stack); ref = np.load(a.ref)
    res = float(ref['res'])
    lat_ratio = 0.52
    sizes = np.exp(np.linspace(math.log(a.smin), math.log(a.smax), 40))
    ratios = [1.0, lat_ratio]
    if a.merc is not None:
        # Hig's exports: `merc` metres a pixel in Web Mercator units, so the
        # ground pixel is merc * cos(lat) (found on four of seven videos,
        # 2026-11-21: 5.22 m at 58.4 N, 4.98 m at 59.9 N, from a free search)
        T = ref['transform']; R = 6378137.0
        yc = float(T[5]) - float(T[0]) * ref['dem'].shape[0] / 2
        latc = 2 * math.atan(math.exp(yc / R)) - math.pi / 2
        s0 = a.merc * math.cos(latc)
        sizes = s0 * np.exp(np.linspace(-0.05, 0.05, 5)); ratios = [1.0]
    out = {}
    for prod in ('lum', 'feat'):
        for orbit in ('asc', 'desc'):
            best, rows = run(st, ref, orbit, prod, sizes, ratios, a.coarse, res)
            s, q, mx, second, loc, w, h, z, _ = best
            # refine around the coarse best at 2x finer, in a window around it
            f = a.coarse / 2.0
            pad = 0.15 * max(w, h) * f
            win = (loc[0] * f - pad, loc[1] * f - pad, (loc[0] + w) * f + pad, (loc[1] + h) * f + pad)
            fs = s * np.exp(np.linspace(-0.08, 0.08, 9)); fq = q * np.exp(np.linspace(-0.06, 0.06, 7)) if a.merc is None else [1.0]
            b2, _ = run(st, ref, orbit, prod, fs, fq, a.coarse / f, res, win)
            s2, q2, mx2, sec2, loc2, w2, h2, _, _ = b2
            gres = res * a.coarse / f
            T = ref['transform']                 # affine of the 20 m 3857 grid: a, b, c, d, e, f
            px = float(T[0]) * gres / res        # mercator metres per refine-grid pixel
            X0 = float(T[2]) + loc2[0] * px; Y1 = float(T[5]) - loc2[1] * px
            out['%s_%s' % (prod, orbit)] = dict(grid_res=gres, coarse=dict(s=round(float(s), 2), ratio=float(q), ncc=round(float(mx), 3), second=round(float(second), 3), z=round(float(z), 1)),
                                               s=round(float(s2), 2), ratio=round(float(q2), 3), ncc=round(float(mx2), 3), second=round(float(sec2), 3),
                                               bounds_3857=[X0, Y1 - h2 * px, X0 + w2 * px, Y1])
            print(prod, orbit, out['%s_%s' % (prod, orbit)])
    json.dump(out, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
