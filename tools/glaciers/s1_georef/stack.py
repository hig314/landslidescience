"""S1 timelapse -> registered frame stack -> per-pixel statistics over the whole span.

Exploratory (2026-11-21). One video in, one folder out:
  frames are decoded at full size; each frame's FEATURE is the smoothed
  gradient magnitude of log luminance, which does not care whether an edge is
  light-on-dark or dark-on-light. Frames are registered to the median feature
  by phase correlation (translation), twice; a frame whose shift is large or
  whose correlation peak is weak is reported.
Outputs (npz + pngs): median RGB, p10/p90/std of luminance, median feature
("edge persistence"), sign-agreement of the luminance anomaly, shifts.

usage: python stack.py VIDEO OUTDIR [--label-box W H]
"""
import argparse, json, subprocess, sys
from pathlib import Path
import numpy as np
import cv2

FFMPEG = '/opt/homebrew/bin/ffmpeg'
FFPROBE = '/opt/homebrew/bin/ffprobe'


def decode(path):
    info = json.loads(subprocess.check_output([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                               'stream=width,height', '-of', 'json', str(path)]))['streams'][0]
    w, h = info['width'], info['height']
    raw = subprocess.check_output([FFMPEG, '-v', 'error', '-i', str(path), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)


def lum(rgb):
    return np.log1p(rgb.astype(np.float32).mean(-1))


def feature(rgb, mask, sigma=2.0):
    L = cv2.GaussianBlur(lum(rgb), (0, 0), 1.0)
    gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
    f = cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), sigma)
    f[~mask] = 0
    return f


def shift_of(f, ref, win):
    (dx, dy), resp = cv2.phaseCorrelate(ref.astype(np.float64), f.astype(np.float64), win)
    return dx, dy, resp


def apply_shift(img, dx, dy):
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video'); ap.add_argument('out')
    ap.add_argument('--label-box', nargs=2, type=int, default=[560, 170])
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    fr = decode(a.video)
    n, h, w, _ = fr.shape
    mask = np.ones((h, w), bool)
    mask[:a.label_box[1], :a.label_box[0]] = False          # the burned-in month
    # a black frame (no acquisition that month) carries no information
    good = np.array([fr[i][mask].mean() > 8 for i in range(n)])
    F = np.stack([feature(fr[i], mask) for i in range(n)])
    win = cv2.createHanningWindow((w, h), cv2.CV_64F)
    shifts = np.zeros((n, 2)); resp = np.zeros(n)
    for it in range(2):
        aligned = np.stack([apply_shift(F[i], *shifts[i]) for i in range(n) if good[i]])
        ref = np.median(aligned, 0)
        for i in range(n):
            if good[i]:
                dx, dy, r = shift_of(F[i], ref, win)
                shifts[i] = (dx, dy); resp[i] = r
    # Frame check: after registration, each frame's luminance against the
    # median, over STABLE pixels (low temporal spread: rock, mostly). A frame
    # processed with the wrong geometry (Brady-Abyss 2022-05) correlates
    # poorly there even when wet snow has blanked the ice; such frames are
    # dropped before the statistics. Robust threshold: median - 8 MAD (4 MAD dropped ordinary noisy wet-snow months).
    Lall = {i: cv2.GaussianBlur(np.log1p(apply_shift(fr[i].astype(np.float32), *shifts[i]).mean(-1)), (0, 0), 2) for i in range(n) if good[i]}
    Ls = np.stack(list(Lall.values()))
    q = np.percentile(Ls, 75, 0) - np.percentile(Ls, 25, 0)
    stable = mask & (q < np.percentile(q[mask], 25))
    med0 = np.median(Ls, 0)[stable]
    fscore = np.full(n, np.nan)
    for i, Li in Lall.items():
        fscore[i] = np.corrcoef(Li[stable], med0)[0, 1]
    fs = fscore[good]
    mad = np.median(np.abs(fs - np.median(fs)))
    thr = np.median(fs) - 8 * 1.4826 * mad
    dropped = [int(i) for i in range(n) if good[i] and fscore[i] < thr]
    keep = good.copy(); keep[dropped] = False
    print('frame check: corr median %.3f, threshold %.3f, dropped %s' % (np.median(fs), thr, dropped))
    good = keep
    # stats on the registered stack
    reg = np.stack([apply_shift(fr[i].astype(np.float32), *shifts[i]) for i in range(n) if good[i]])
    L = np.log1p(reg.mean(-1))
    Fr = np.stack([apply_shift(F[i], *shifts[i]) for i in range(n) if good[i]])
    Lm = np.median(L, 0)
    # sign agreement: per frame, luminance minus that frame's local (15 px) mean;
    # +1 always brighter than surroundings, -1 always darker, ~0 flips
    loc = np.stack([cv2.blur(x, (31, 31)) for x in L])
    sgn = np.sign(L - loc).mean(0)
    st = dict(med_rgb=np.median(reg, 0).astype(np.uint8), lum_med=Lm, lum_p10=np.percentile(L, 10, 0),
              lum_p90=np.percentile(L, 90, 0), lum_std=L.std(0), feat_med=np.median(Fr, 0), sign=sgn,
              mask=mask, shifts=shifts, resp=resp, good=good, fscore=fscore, dropped=np.array(dropped, int))
    np.savez_compressed(out / 'stack.npz', **st)

    def png(name, x, lo=None, hi=None, cmap=None):
        x = x.astype(np.float32)
        lo = np.percentile(x[mask], 1) if lo is None else lo
        hi = np.percentile(x[mask], 99) if hi is None else hi
        y = np.clip((x - lo) / (hi - lo + 1e-9), 0, 1)
        y[~mask] = 0.5
        y = (y * 255).astype(np.uint8)
        if cmap is not None:
            y = cv2.applyColorMap(y, cmap)
        cv2.imwrite(str(out / name), y)
    mr = st['med_rgb'].copy(); mr[~mask] = 128
    cv2.imwrite(str(out / 'med_rgb.png'), cv2.cvtColor(mr, cv2.COLOR_RGB2BGR))
    png('lum_med.png', Lm); png('lum_std.png', st['lum_std']); png('feat_med.png', st['feat_med'])
    png('sign.png', sgn, -1, 1, cv2.COLORMAP_TWILIGHT_SHIFTED)
    # RGB of three time statistics: p10, median, p90
    def nrm(x):
        lo, hi = np.percentile(x[mask], [1, 99]); return np.clip((x - lo) / (hi - lo), 0, 1)
    tri = np.dstack([nrm(st['lum_p90']), nrm(Lm), nrm(st['lum_p10'])]); tri[~mask] = 0.5
    cv2.imwrite(str(out / 'p10_med_p90.png'), (tri * 255).astype(np.uint8))
    rep = [{'i': int(i), 'fscore': None if np.isnan(fscore[i]) else round(float(fscore[i]), 3), 'dx': round(float(shifts[i][0]), 2), 'dy': round(float(shifts[i][1]), 2), 'resp': round(float(resp[i]), 3),
            'good': bool(good[i])} for i in range(n)]
    (out / 'shifts.json').write_text(json.dumps(rep, indent=0))
    big = [r for r in rep if r['good'] and (abs(r['dx']) > 1.5 or abs(r['dy']) > 1.5)]
    print('%d frames, %d blank; resp median %.3f, min %.3f' % (n, n - good.sum(), np.median(resp[good]), resp[good].min()))
    print('shifted > 1.5 px:', big)
    weak = sorted([r for r in rep if r['good']], key=lambda r: r['resp'])[:5]
    print('weakest:', weak)


if __name__ == '__main__':
    main()
