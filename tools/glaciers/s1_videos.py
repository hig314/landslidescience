#!/usr/bin/env python3
"""Sentinel-1 timelapse videos in a public Google Drive folder: list, probe, read dates.

Hig makes monthly S1 RGB timelapses in Earth Engine; they land in one Drive
folder shared "anyone with the link". This reads that folder WITHOUT
downloading the videos: ffprobe and ffmpeg fetch only the index and the first
and last frames by HTTP range request (a few MB a video).

    python3 tools/glaciers/s1_videos.py probe <folder id or url> [--out DIR]

writes, per video, into DIR (default: the main checkout's
data/glaciers/s1_review/):
    <file id>.json        name, orbit, frames, size, the first and last date labels
    <file id>_first.png   the label corner of the first frame
    <file id>_last.png    the label corner of the last frame
    <file id>.jpg         the last frame, 640 px wide (for the review page)
and listing.json. Then

    python3 tools/glaciers/s1_videos.py dates

streams each video once and writes <file id>_dates.json, the label of every
frame. Drive throttles (a 35 MB file takes minutes), so use instead

    python3 tools/glaciers/s1_videos.py gap

which finds the frames either side of 1 Jan 2022 by seeking (about eight
single-frame reads a video) and writes <file id>_gap.json. A video already probed is skipped, so a rerun picks up only
new files.

THE DATES are burned into each frame (top left, "YYYY-MM", one frame a month).
The S1B gap needs no reading of every frame: months spanned from first to last
label, minus the frame count, is the number of missing months. The gap (S1B
failed Dec 2021; S1C from 2025) shows as a year or more missing. Months the
export skipped otherwise number a handful.

The labels are read by `read_labels` from glyphs: the font is fixed-width,
so each character cell is compared with a set of digit templates
(`s1_glyphs.json` beside this file), made once by hand from the crops.
"""
import argparse
import concurrent.futures as cf
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

FFPROBE = '/opt/homebrew/bin/ffprobe'
FFMPEG = '/opt/homebrew/bin/ffmpeg'
GET = 'https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t'
FOLDER_VIEW = 'https://drive.google.com/embeddedfolderview?id={id}'
DEFAULT_OUT = Path(__file__).resolve().parents[2].parent / 'landslidescience' / 'data' / 'glaciers' / 's1_review'
# Label corner of a frame, in source pixels. The label's size varies between
# videos (up to about 650 x 160 seen), so the crop is generous.
CROP = r'crop=min(800\,iw):min(240\,ih):0:0'
NAME_RE = re.compile(r'^(?P<site>.+?)_Sentinel1_(?P<orbit>ASCENDING|DESCENDING)_(?P<cadence>[A-Z]+)_RGB\.(?P<ext>mp4|webm|gif)$')


def folder_id(s):
    m = re.search(r'/folders/([A-Za-z0-9_-]{20,})', s)
    return m.group(1) if m else s


def list_folder(fid):
    """Files in a public folder: [(file id, name)]. Drive's embedded folder
    view needs no login or API key."""
    req = urllib.request.Request(FOLDER_VIEW.format(id=fid), headers={'User-Agent': 'Mozilla/5.0'})
    s = urllib.request.urlopen(req, timeout=60).read().decode('utf8')
    ents = re.findall(r'<div class="flip-entry" id="entry-([A-Za-z0-9_-]+)".*?<div class="flip-entry-title">(.*?)</div>', s, re.S)
    return [(i, html.unescape(t)) for i, t in ents]


def parse_name(name):
    m = NAME_RE.match(name)
    if not m:
        return {'site': Path(name).stem, 'orbit': None, 'cadence': None}
    return {'site': m.group('site'), 'orbit': m.group('orbit'), 'cadence': m.group('cadence')}


def _run(args, timeout=180):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def probe_one(fid, name, out, force=False):
    meta_p = out / (fid + '.json')
    if meta_p.exists() and not force:
        return json.loads(meta_p.read_text())
    url = GET.format(id=fid)
    rec = {'id': fid, 'name': name, **parse_name(name)}
    r = _run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
              'stream=nb_frames,width,height,r_frame_rate:format=duration,size', '-of', 'json', url])
    if r.returncode:
        rec['error'] = 'ffprobe: ' + r.stderr.strip()[:300]
        return rec                       # not cached: retried next run
    j = json.loads(r.stdout)
    st, fm = j['streams'][0], j['format']
    rec.update(frames=int(st.get('nb_frames') or 0), width=st['width'], height=st['height'],
               fps=st.get('r_frame_rate'), duration=float(fm.get('duration') or 0), size=int(fm.get('size') or 0))
    first, last = out / (fid + '_first.png'), out / (fid + '_last.png')
    big = out / (fid + '_lastfull.png')
    a = _run([FFMPEG, '-v', 'error', '-y', '-i', url, '-frames:v', '1', '-vf', CROP, str(first)])
    b = _run([FFMPEG, '-v', 'error', '-y', '-sseof', '-0.3', '-i', url, '-update', '1', str(big)])
    if a.returncode or b.returncode or not big.exists():
        rec['error'] = 'ffmpeg: ' + (a.stderr + b.stderr).strip()[:300]
        return rec
    _run([FFMPEG, '-v', 'error', '-y', '-i', str(big), '-vf', CROP, str(last)])
    _run([FFMPEG, '-v', 'error', '-y', '-i', str(big), '-vf', 'scale=640:-2', '-q:v', '5', str(out / (fid + '.jpg'))])
    big.unlink()
    meta_p.write_text(json.dumps(rec, indent=1))
    return rec


def cmd_probe(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = list_folder(folder_id(a.folder))
    (out / 'listing.json').write_text(json.dumps({'folder': folder_id(a.folder), 'files': files}, indent=1))
    print(len(files), 'files in the folder', flush=True)
    done = 0
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(probe_one, i, n, out, a.force): n for i, n in files}
        for f in cf.as_completed(futs):
            done += 1
            r = f.result()
            print('%3d/%d %s %s' % (done, len(files), futs[f], r.get('error', '') or 'ok'), flush=True)


def frame_dates(fid, out, force=False):
    """The label of EVERY frame, read as the video streams through ffmpeg
    (nothing of the video is kept). Writes <id>_dates.json: the list of
    labels in frame order (None where a label could not be read)."""
    p = out / (fid + '_dates.json')
    if p.exists() and not force:
        return json.loads(p.read_text())
    sys.path.insert(0, str(Path(__file__).parent))
    import numpy as np
    import s1_labels as L
    from PIL import Image
    meta = json.loads((out / (fid + '.json')).read_text())
    w, h = min(800, meta['width']), min(240, meta['height'])
    proc = subprocess.Popen([FFMPEG, '-v', 'error', '-i', GET.format(id=fid), '-vf', 'crop=%d:%d:0:0' % (w, h),
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
    T = L.load_templates()
    tmp = out / (fid + '_frame.png')
    labels, n = [], w * h * 3
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            Image.fromarray(np.frombuffer(buf, np.uint8).reshape(h, w, 3)).save(tmp)
            s, d = L.read(tmp, T)
            labels.append(s if (s and isinstance(d, float) and d < 0.03) else None)
    finally:
        proc.stdout.close()
        proc.wait()
        if tmp.exists():
            tmp.unlink()
    rec = {'id': fid, 'labels': labels}
    p.write_text(json.dumps(rec))
    return rec


def local_labels(path, loose=0.15):
    """Every frame's label of a local video file: (fps, [label or None]).
    Some exports use a larger font that matches the templates less closely
    (Ellsworth: every frame at 0.10 against the usual 0.03 limit), so up to
    `loose` is accepted here; months_from_labels drops a label that breaks
    the order, which is what a misread digit does."""
    sys.path.insert(0, str(Path(__file__).parent))
    import tempfile
    import numpy as np
    import s1_labels as L
    from PIL import Image
    r = _run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
              'stream=width,height,r_frame_rate', '-of', 'json', str(path)])
    st = json.loads(r.stdout)['streams'][0]
    num, den = st['r_frame_rate'].split('/')
    fps = float(num) / float(den or 1)
    w, h = min(800, st['width']), min(240, st['height'])
    proc = subprocess.Popen([FFMPEG, '-v', 'error', '-i', str(path), '-vf', 'crop=%d:%d:0:0' % (w, h),
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
    T = L.load_templates()
    labels, n = [], w * h * 3
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / 'f.png'
        try:
            while True:
                buf = proc.stdout.read(n)
                if len(buf) < n:
                    break
                Image.fromarray(np.frombuffer(buf, np.uint8).reshape(h, w, 3)).save(tmp)
                s_, d = L.read(tmp, T)
                labels.append(s_ if (s_ and isinstance(d, float) and d < loose) else None)
        finally:
            proc.stdout.close()
            proc.wait()
    return fps, labels


def _mi(s):
    return int(s[:4]) * 12 + int(s[5:7]) - 1


def months_from_labels(labels):
    """Frame months from the labels read: a label that cannot be right
    (fewer months after an earlier label than frames) is dropped;
    an unread frame between two read ones gets a month when the months
    between them match the frames between them one for one; otherwise
    None (the page shows no month for that frame)."""
    L = [l if (l and len(l) == 7 and l[4] == '-' and 1 <= int(l[5:7]) <= 12) else None for l in labels]
    # Each frame is at least a month after the one before, so two read
    # labels k frames apart are at least k months apart. A label that breaks
    # this is a misread: repeatedly drop the one in conflict with the most
    # others until none is.
    def clash(a, b):                       # frames a < b
        return _mi(L[b]) - _mi(L[a]) < b - a
    while True:
        idx = [i for i, l in enumerate(L) if l]
        cnt = {}
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                if clash(idx[x], idx[y]):
                    cnt[idx[x]] = cnt.get(idx[x], 0) + 1
                    cnt[idx[y]] = cnt.get(idx[y], 0) + 1
        if not cnt:
            break
        L[max(cnt, key=cnt.get)] = None
    out = list(L)
    idx = [i for i, l in enumerate(L) if l]
    for i0, i1 in zip(idx, idx[1:]):
        if i1 - i0 > 1 and _mi(L[i1]) - _mi(L[i0]) == i1 - i0:
            for k in range(i0 + 1, i1):
                m = _mi(L[i0]) + (k - i0)
                out[k] = '%04d-%02d' % (m // 12, m % 12 + 1)
    return out


def cmd_months(a):
    import concurrent.futures as cf
    d = Path(a.videos)
    files = [f for f in a.files.split(',') if f] or sorted(p.name for p in d.glob('*.mp4'))
    res = json.loads(Path(a.out).read_text()) if Path(a.out).exists() else {}
    todo = [f for f in files if f not in res or a.redo]
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(local_labels, d / f): f for f in todo}
        for n, fu in enumerate(cf.as_completed(futs), 1):
            f = futs[fu]
            try:
                fps, labels = fu.result()
            except Exception as e:                  # noqa: BLE001
                print('%3d/%d %s error %s' % (n, len(todo), f, e), flush=True)
                continue
            months = months_from_labels(labels)
            res[f] = {'fps': fps, 'frames': len(labels), 'labels': labels, 'months': months}
            print('%3d/%d %s %d frames, %d read, %d with a month' % (
                n, len(todo), f, len(labels), sum(1 for l in labels if l), sum(1 for m in months if m)), flush=True)
            Path(a.out).write_text(json.dumps(res))


# The S1B outage: S1B failed on 23 Dec 2021 and S1C was in service by spring
# 2025, so a video with the gap jumps from a frame before 2022 to one well
# after. `gap` finds that jump by seeking, a frame at a time, instead of
# streaming the whole video (Drive serves a 35 MB file in minutes).
OUTAGE_START = '2022-01'


def _label_at(fid, i, fps, out, T):
    sys.path.insert(0, str(Path(__file__).parent))
    import s1_labels as L
    tmp = out / ('%s_seek%d.png' % (fid, i))
    try:
        r = _run([FFMPEG, '-v', 'error', '-y', '-ss', '%.4f' % ((i + 0.5) / fps), '-i', GET.format(id=fid),
                  '-frames:v', '1', '-vf', CROP, str(tmp)])
        if r.returncode or not tmp.exists():
            return None
        s, d = L.read(tmp, T)
        return s if (s and isinstance(d, float) and d < 0.03) else None
    finally:
        tmp.unlink(missing_ok=True)


def find_gap(fid, out, force=False):
    """Where the video crosses into 2022: the last frame before it and the
    first at or after it, both read by seeking. Writes <id>_gap.json."""
    p = out / (fid + '_gap.json')
    if p.exists() and not force:
        done = json.loads(p.read_text())
        if 'error' not in done:          # an error (often Drive refusing for a while) is tried again
            return done
    sys.path.insert(0, str(Path(__file__).parent))
    import s1_labels as L
    T = L.load_templates()
    meta = json.loads((out / (fid + '.json')).read_text())
    num, den = (meta.get('fps') or '10/1').split('/')
    fps = float(num) / float(den or 1)
    n = meta['frames']
    cache = {}

    def lab(i):
        if i not in cache:
            # an unreadable frame: try its neighbours (the label is all we need)
            for j in (i, i + 1, i - 1):
                if 0 <= j < n:
                    v = _label_at(fid, j, fps, out, T)
                    if v:
                        cache[i] = (j, v)
                        break
            else:
                cache[i] = (i, None)
        return cache[i]
    rec = {'id': fid}
    lo, hi = 0, n - 1
    jl, vl = lab(lo)
    jh, vh = lab(hi)
    if not vl or not vh:
        rec['error'] = 'first or last label unreadable by seeking'
    elif vh < OUTAGE_START:
        rec['note'] = 'ends before 2022'
    elif vl >= OUTAGE_START:
        rec['note'] = 'starts in or after 2022'
    else:
        while hi - lo > 1:
            mid = (lo + hi) // 2
            j, v = lab(mid)
            if v is None:
                rec['error'] = 'unreadable near frame %d' % mid
                break
            if v >= OUTAGE_START:
                hi = mid
            else:
                lo = mid
        if 'error' not in rec:
            rec['before'], rec['after'] = lab(lo)[1], lab(hi)[1]
            rec['frames'] = [lab(lo)[0], lab(hi)[0]]
    rec['probes'] = len(cache)
    p.write_text(json.dumps(rec))
    return rec


def cmd_gap(a):
    out = Path(a.out)
    files = json.loads((out / 'listing.json').read_text())['files']
    done = 0
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(find_gap, i, out, a.force): n for i, n in files}
        for f in cf.as_completed(futs):
            done += 1
            try:
                r = f.result()
                print('%3d/%d %s %s' % (done, len(files), futs[f],
                                        r.get('error') or r.get('note') or '%s -> %s' % (r['before'], r['after'])), flush=True)
            except Exception as e:
                print('%3d/%d %s ERROR %s' % (done, len(files), futs[f], e), flush=True)


def cmd_dates(a):
    out = Path(a.out)
    files = json.loads((out / 'listing.json').read_text())['files']
    done = 0
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(frame_dates, i, out, a.force): n for i, n in files}
        for f in cf.as_completed(futs):
            done += 1
            try:
                r = f.result()
                bad = sum(1 for x in r['labels'] if x is None)
                print('%3d/%d %s %d frames, %d unread' % (done, len(files), futs[f], len(r['labels']), bad), flush=True)
            except Exception as e:
                print('%3d/%d %s ERROR %s' % (done, len(files), futs[f], e), flush=True)


def cmd_collect(a):
    """The review page's decisions, exported one JSON file per video (the
    artifact's `decisions` collection), merged into <out>/decisions.json:
    {file id: {status, fix}}."""
    out = Path(a.out)
    src = Path(a.exported)
    dec = {}
    for f in sorted(src.glob('*.json')):
        d = json.loads(f.read_text())
        d = d.get('data', d)                 # the export may wrap the body
        dec[f.stem] = {'status': d.get('status') or '', 'fix': (d.get('fix') or '').strip()}
    (out / 'decisions.json').write_text(json.dumps(dec, indent=1))
    n = {}
    for v in dec.values():
        n[v['status'] or 'none'] = n.get(v['status'] or 'none', 0) + 1
    print('%d decisions: %s; %d with a correction' % (len(dec), n, sum(1 for v in dec.values() if v['fix'])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('probe')
    p.add_argument('folder')
    p.add_argument('--out', default=str(DEFAULT_OUT))
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--force', action='store_true', help='probe again, overwriting what is there')
    p.set_defaults(fn=cmd_probe)
    g = sub.add_parser('gap', help='find the S1B outage by seeking (a few frames a video)')
    g.add_argument('--out', default=str(DEFAULT_OUT))
    g.add_argument('--jobs', type=int, default=6)
    g.add_argument('--force', action='store_true')
    g.set_defaults(fn=cmd_gap)
    c = sub.add_parser('collect', help="merge the review page's exported decisions into decisions.json")
    c.add_argument('exported', help='folder of one JSON file per decision')
    c.add_argument('--out', default=str(DEFAULT_OUT))
    c.set_defaults(fn=cmd_collect)
    d = sub.add_parser('dates', help='read the label of every frame (streams each video once)')
    d.add_argument('--out', default=str(DEFAULT_OUT))
    d.add_argument('--jobs', type=int, default=4)
    d.add_argument('--force', action='store_true')
    d.set_defaults(fn=cmd_dates)
    m = sub.add_parser('months', help="read every frame's label from the SERVED video files (local), "
                                      "for georef.months: manage.py s1_videos months <json>")
    m.add_argument('videos', help='the video directory (data/glacier_videos)')
    m.add_argument('--out', required=True, help='JSON written: {file: {fps, frames, labels, months}}')
    m.add_argument('--files', default='', help='comma list of file names (default: every .mp4)')
    m.add_argument('--jobs', type=int, default=4)
    m.add_argument('--redo', action='store_true', help='read again files already in --out')
    m.set_defaults(fn=cmd_months)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
