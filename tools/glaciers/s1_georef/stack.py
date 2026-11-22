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


def stat_rows(fn, make, rows=128):
    """fn over row blocks of a (n, h, w) stack, as float32 (h, w). `make`
    returns the stack (called once); blocks are cast to float32 one at a
    time so no whole float32 copy of the stack exists."""
    S = make()
    n, h, w = S.shape
    out = np.empty((h, w), np.float32)
    for r0 in range(0, h, rows):
        r1 = min(h, r0 + rows)
        out[r0:r1] = fn(S[:, r0:r1].astype(np.float32), r0, r1)
    return out


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
    # Memory: a 2572x1960x105 video took 28 GB with float stacks and whole-
    # array medians (2026-11-22). Stacks are float16 / uint8 and every
    # per-pixel statistic runs over row blocks (stat_rows), so the peak is
    # about three stacks of float16.
    F = np.stack([feature(fr[i], mask).astype(np.float16) for i in range(n)])
    win = cv2.createHanningWindow((w, h), cv2.CV_64F)
    shifts = np.zeros((n, 2)); resp = np.zeros(n)
    gi = [i for i in range(n) if good[i]]
    for it in range(2):
        ref = stat_rows(lambda blk, r0, r1: np.median(blk, 0),
                        lambda: np.stack([apply_shift(F[i].astype(np.float32), *shifts[i]).astype(np.float16) for i in gi]))
        for i in gi:
            dx, dy, r = shift_of(F[i].astype(np.float32), ref, win)
            shifts[i] = (dx, dy); resp[i] = r
    # registered log luminance, every non-blank frame (float16)
    L = np.stack([np.log1p(apply_shift(fr[i].astype(np.float32), *shifts[i]).mean(-1)).astype(np.float16) for i in gi])
    # Frame check: after registration, each frame's luminance against the
    # median, over STABLE pixels (low temporal spread: rock, mostly). A frame
    # processed with the wrong geometry (Brady-Abyss 2022-05) correlates
    # poorly there even when wet snow has blanked the ice; such frames are
    # dropped before the statistics. Robust threshold: median - 8 MAD (4 MAD dropped ordinary noisy wet-snow months).
    Ls = np.stack([cv2.GaussianBlur(x.astype(np.float32), (0, 0), 2).astype(np.float16) for x in L])
    q = stat_rows(lambda blk, r0, r1: np.subtract(*np.percentile(blk.astype(np.float32), [75, 25], 0)), lambda: Ls)
    stable = mask & (q < np.percentile(q[mask], 25))
    med0 = stat_rows(lambda blk, r0, r1: np.median(blk, 0), lambda: Ls)[stable]
    fscore = np.full(n, np.nan)
    for j, i in enumerate(gi):
        fscore[i] = np.corrcoef(Ls[j][stable].astype(np.float32), med0)[0, 1]
    del Ls
    fs = fscore[good]
    mad = np.median(np.abs(fs - np.median(fs)))
    thr = np.median(fs) - 8 * 1.4826 * mad
    dropped = [int(i) for i in range(n) if good[i] and fscore[i] < thr]
    keep = good.copy(); keep[dropped] = False
    print('frame check: corr median %.3f, threshold %.3f, dropped %s' % (np.median(fs), thr, dropped))
    sel = [j for j, i in enumerate(gi) if keep[i]]
    L = L[sel]
    gk = [gi[j] for j in sel]
    good = keep
    # stats on the registered stack, by row blocks
    Lm = stat_rows(lambda blk, r0, r1: np.median(blk, 0), lambda: L)
    p10 = stat_rows(lambda blk, r0, r1: np.percentile(blk, 10, 0), lambda: L)
    p90 = stat_rows(lambda blk, r0, r1: np.percentile(blk, 90, 0), lambda: L)
    sd = stat_rows(lambda blk, r0, r1: blk.std(0), lambda: L)
    # sign agreement: per frame, luminance minus that frame's local (15 px) mean;
    # +1 always brighter than surroundings, -1 always darker, ~0 flips
    sgn = np.zeros((h, w), np.float32)
    for x in L:
        x = x.astype(np.float32); sgn += np.sign(x - cv2.blur(x, (31, 31)))
    sgn /= len(L)
    del L
    Fr = np.stack([apply_shift(F[i].astype(np.float32), *shifts[i]).astype(np.float16) for i in gk])
    del F
    fmed = stat_rows(lambda blk, r0, r1: np.median(blk, 0), lambda: Fr)
    del Fr
    mr = np.zeros((h, w, 3), np.uint8)
    for c in range(3):
        R = np.stack([apply_shift(np.ascontiguousarray(fr[i][..., c]), *shifts[i]) for i in gk])     # uint8
        mr[..., c] = np.median(R, 0).astype(np.uint8)
        del R
    st = dict(med_rgb=mr, lum_med=Lm, lum_p10=p10,
              lum_p90=p90, lum_std=sd, feat_med=fmed, sign=sgn,
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
