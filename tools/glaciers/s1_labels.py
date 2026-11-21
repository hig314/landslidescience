"""Read the burned-in "YYYY-MM" label of an S1 timelapse frame (see s1_videos.py).

The label is white glyphs with a black outline at the top left. A pixel is
glyph if it is near-white AND within 3 px of a near-black pixel (bright
snow or yellow SAR backscatter is white but has no outline). Glyph boxes
taller than 20 px are the six digits (the dash is shorter); each is scaled
to 16 x 24 and matched to the nearest template in s1_glyphs.json."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as nd

GLYPHS = Path(__file__).with_name('s1_glyphs.json')
SIZE = (16, 24)


def boxes(path):
    a = np.asarray(Image.open(path).convert('RGB')).astype(int)
    white = (a.min(2) > 190) & ((a.max(2) - a.min(2)) < 45)
    near = nd.binary_dilation(a.max(2) < 70, iterations=3)
    g = white & near
    lab, n = nd.label(nd.binary_dilation(g, iterations=1))
    raw = [(o[1].start, o[1].stop, o[0].start, o[0].stop) for o in nd.find_objects(lab)]
    if not raw:
        return [], []
    hmax = max(b[3] - b[2] for b in raw)
    # digits are the tall boxes near the top (the dash is short; specks are small)
    tall = sorted(b for b in raw if b[3] - b[2] > 0.6 * hmax and b[2] < 90)
    # Two digits that touch ("15") come out as one box: split a box much wider
    # than the typical digit into equal parts. A "1" is narrower, so the
    # typical width is taken from the widest half.
    ws = sorted(b[1] - b[0] for b in tall)
    if not ws:
        return [], []
    w0 = float(np.median(ws[len(ws) // 2:]))
    out = []
    for x0, x1, y0, y1 in tall:
        k = int(round((x1 - x0) / w0)) if (x1 - x0) > 1.45 * w0 else 1
        for i in range(k):
            out.append((x0 + (x1 - x0) * i // k, x0 + (x1 - x0) * (i + 1) // k, y0, y1))
    cells = []
    for x0, x1, y0, y1 in out:
        c = Image.fromarray((g[y0:y1, x0:x1] * 255).astype('uint8')).resize(SIZE, Image.BILINEAR)
        cells.append(np.asarray(c).astype(float) / 255.0)
    return out, cells


def load_templates():
    if not GLYPHS.exists():
        return {}
    t = json.loads(GLYPHS.read_text())
    return {k: [np.array(v) for v in vs] for k, vs in t.items()}


def classify(cell, T):
    best, bd = None, 1e9
    for k, vs in T.items():
        for v in vs:
            d = float(((cell - v) ** 2).mean())
            if d < bd:
                best, bd = k, d
    return best, bd


def read(path, T=None):
    """'YYYY-MM' and the worst glyph distance, or (None, reason)."""
    T = T if T is not None else load_templates()
    bx, cells = boxes(path)
    if len(cells) != 6:
        return None, 'found %d digit boxes' % len(cells)
    ds = [classify(c, T) for c in cells]
    s = ''.join(d[0] or '?' for d in ds)
    return s[:4] + '-' + s[4:], max(d[1] for d in ds)
