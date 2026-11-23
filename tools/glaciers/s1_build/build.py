"""Build a Sentinel-1 monthly timelapse for an area, georeferenced from the start.

Source: Microsoft Planetary Computer `sentinel-1-rtc` (radiometrically terrain
corrected GRD, 10 m UTM COGs, gamma0 linear power; anonymous SAS token).
Only the window over the area is read from each scene (COG range requests).

Output, in OUT/<name>/:
  scenes/<item id>.npz   per-scene window, VV/VH in dB (float16) on the output grid
  stack.npz              monthly composites: vv, vh (months, h, w) dB float32,
                         months ['YYYY-MM'], n (scenes per month), transform
  frames/NNNN.png        rendered frames (no label)
  <name>.mp4             clean video (for the map); <name>_label.mp4 with dates
  meta.json              bounds (lon/lat and EPSG:3857), grid, orbit, months, n
  sheet.jpg              one July frame per year, for a quick look

The rendering repeats the Earth Engine script the earlier videos came from:
monthly MEAN in dB, 3x3 Lee filter, RGB = VV [-20,0], VH [-25,-5], VV-VH [1,15].
The difference that remains is the source: Earth Engine's S1_GRD is sigma0
on the ellipsoid; this is gamma0 flattened for terrain, so slopes facing the
radar are less bright.

WHICH PASS (`choose`): every pass direction + relative orbit over the area
is a candidate. The catalogue says which months each has; one scene a year
(the nearest to mid-July) is then read at 1/10 resolution to see whether it
actually covers the frame (a swath edge can cut it). The candidate with the
most months in covered years wins. The table of candidates, with each one's
first and last month and its gaps, is printed and kept in meta.json, so a
run forced onto the other direction (--orbit) shows how far it goes. A pass
that stops (descending over much of Alaska after Sentinel-1B failed in
December 2021) can start again when the acquisition plan changes
(Sentinel-1C from 2025, 1D from 2026): rerun later and new scenes come in.

--stage copies the clean mp4 and meta.json to the main checkout's
data/glaciers/s1_build/NAME/, which the dev servers mount; then
`manage.py s1_videos built data/glaciers/s1_build/NAME` puts it on /glaciers/.

Usage:
  build.py NAME LAT LON [--w-km 10.6] [--h-km 6.7] [--orbit auto|asc|desc]
           [--rel N|all] [--start 2014-10] [--end 2026-11] [--workers 8]
           [--stage]
"""
import argparse, concurrent.futures as cf, datetime as dt, json, math, os, subprocess, sys, time, urllib.request
from collections import Counter, defaultdict

import numpy as np

os.environ.setdefault('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
os.environ.setdefault('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.tiff,.tif')
os.environ.setdefault('GDAL_HTTP_MAX_RETRY', '4')
os.environ.setdefault('GDAL_HTTP_RETRY_DELAY', '2')
import rasterio
from rasterio.transform import Affine
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling

STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
TOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc'
R = 6378137.0
OUT = '/Volumes/Nunatak/glacier_s1_build'
FFMPEG = '/opt/homebrew/bin/ffmpeg'
FPS = 10
STAGE = '/Users/Hig/Claude_projects/landslidescience/data/glaciers/s1_build'


def merc(lon, lat):
    return math.radians(lon) * R, R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def unmerc(x, y):
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def grid(lat, lon, w_km, h_km, res_m=10.0):
    """EPSG:3857 grid centred on the point, `res_m` metres on the ground."""
    k = math.cos(math.radians(lat))
    px = res_m / k
    w = int(round(w_km * 1000 / res_m / 2)) * 2      # even, for yuv420p
    h = int(round(h_km * 1000 / res_m / 2)) * 2
    cx, cy = merc(lon, lat)
    x0, y1 = cx - w * px / 2, cy + h * px / 2
    return Affine(px, 0, x0, 0, -px, y1), w, h


class Signer:
    def __init__(self):
        self.tok, self.t = None, 0

    def __call__(self, href):
        if time.time() - self.t > 1500:
            self.tok = json.load(urllib.request.urlopen(TOKEN))['token']
            self.t = time.time()
        return href + '?' + self.tok


def search(bbox, start, end):
    body = {'collections': ['sentinel-1-rtc'], 'bbox': bbox,
            'datetime': f'{start}/{end}', 'limit': 1000}
    items = []
    while body:
        req = urllib.request.Request(STAC, json.dumps(body).encode(), {'Content-Type': 'application/json'})
        r = json.load(urllib.request.urlopen(req))
        items += r['features']
        nx = [l for l in r.get('links', []) if l['rel'] == 'next']
        body = nx[0].get('body') if nx else None
    return items


def read_window(href, transform, w, h):
    with rasterio.open(href) as src:
        with WarpedVRT(src, crs='EPSG:3857', transform=transform, width=w, height=h,
                       resampling=Resampling.bilinear, src_nodata=src.nodata if src.nodata is not None else 0,
                       nodata=np.nan, dtype='float32') as vrt:
            a = vrt.read(1)
    a[~(a > 0)] = np.nan
    return 10 * np.log10(a)


def probe(href, x0, y0, x1, y1):
    """Fraction of the frame a scene covers, read at 1/10 resolution
    (the COG's overviews), in the scene's own grid."""
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    with rasterio.open(href) as src:
        b = transform_bounds('EPSG:3857', src.crs, x0, y0, x1, y1)
        win = from_bounds(*b, transform=src.transform)
        shape = (max(8, int(win.height / 10)), max(8, int(win.width / 10)))
        a = src.read(1, window=win, out_shape=shape, boundless=True, fill_value=0)
    return float(np.mean(np.isfinite(a) & (a > 0)))


def month_runs(have, first, last):
    """Runs of three or more missing months between first and last."""
    y, m = map(int, first.split('-')); out, run = [], []
    while '%04d-%02d' % (y, m) <= last:
        k = '%04d-%02d' % (y, m)
        if k in have:
            if len(run) >= 3:
                out.append('%s..%s' % (run[0], run[-1]))
            run = []
        else:
            run.append(k)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def choose(items, box, sign, min_valid, orbit, rel, workers):
    """Score every (direction, relative orbit); see the module docstring."""
    by = defaultdict(list)
    for it in items:
        p = it['properties']
        by[(p.get('sat:orbit_state'), str(p.get('sat:relative_orbit')))].append(it)
    picks = []                                  # (cand, year, item)
    for c, its in by.items():
        per_year = defaultdict(list)
        for it in its:
            per_year[it['properties']['datetime'][:4]].append(it)
        for yr, l in per_year.items():
            mid = dt.datetime(int(yr), 7, 15)
            best = min(l, key=lambda it: abs(dt.datetime.fromisoformat(it['properties']['datetime'][:19]) - mid))
            picks.append((c, yr, best))

    def one(p):
        try:
            return p, probe(sign(p[2]['assets']['vv']['href']), *box)
        except Exception:
            return p, None
    cover = defaultdict(dict)
    with cf.ThreadPoolExecutor(workers) as ex:
        for (c, yr, _it), f in ex.map(one, picks):
            cover[c][yr] = f
    table = []
    for c, its in by.items():
        months = sorted({it['properties']['datetime'][:7] for it in its})
        ok_years = {y for y, f in cover[c].items() if f is not None and f >= min_valid}
        good = [m for m in months if m[:4] in ok_years]
        table.append({'orbit': c[0], 'rel': c[1], 'scenes': len(its), 'months': len(months),
                      'months_covered': len(good), 'first': months[0], 'last': months[-1],
                      'gaps': month_runs(set(months), months[0], months[-1]),
                      'cover_by_year': {y: (None if f is None else round(f, 2)) for y, f in sorted(cover[c].items())}})
    table.sort(key=lambda r: -r['months_covered'])
    state = {'asc': 'ascending', 'desc': 'descending'}.get(orbit)
    want = [r for r in table if (orbit == 'auto' or r['orbit'] == state) and (rel is None or rel == 'all' or r['rel'] == rel)]
    if not want:
        raise SystemExit('no scenes for orbit %s rel %s' % (orbit, rel))
    return want[0], table


def lee(img):
    """The Earth Engine script's 3x3 Lee filter (NaN-aware)."""
    from scipy.ndimage import uniform_filter
    v = np.isfinite(img)
    z = np.where(v, img, 0.0)
    c = uniform_filter(v.astype(np.float32), 3)
    with np.errstate(invalid='ignore', divide='ignore'):
        m = uniform_filter(z, 3) / c
        m2 = uniform_filter(z * z, 3) / c
    var = np.maximum(m2 - m * m, 0)
    b = var / (var + m * m)
    out = m + b * (img - m)
    return np.where(v, out, np.nan)


def render(vv, vh):
    vv, vh = lee(vv), lee(vh)
    ratio = vv - vh
    chans = []
    for a, lo, hi in ((vv, -20, 0), (vh, -25, -5), (ratio, 1, 15)):
        x = np.clip((a - lo) / (hi - lo), 0, 1)
        chans.append(np.nan_to_num(x * 255, nan=0).astype(np.uint8))
    return np.dstack(chans)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('name'); ap.add_argument('lat', type=float); ap.add_argument('lon', type=float)
    ap.add_argument('--w-km', type=float, default=10.6); ap.add_argument('--h-km', type=float, default=6.7)
    ap.add_argument('--orbit', default='auto', choices=['auto', 'asc', 'desc'])
    ap.add_argument('--rel', default=None, help='relative orbit number, or "all" (default: the most frequent)')
    ap.add_argument('--start', default='2014-10'); ap.add_argument('--end', default=dt.date.today().strftime('%Y-%m'))
    ap.add_argument('--min-valid', type=float, default=0.7)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--stage', action='store_true', help='copy mp4 + meta.json to the dev data dir for manage.py s1_videos built')
    a = ap.parse_args()

    od = os.path.join(OUT, a.name)
    os.makedirs(os.path.join(od, 'scenes'), exist_ok=True)
    os.makedirs(os.path.join(od, 'frames'), exist_ok=True)
    T, w, h = grid(a.lat, a.lon, a.w_km, a.h_km)
    x0, y1 = T.c, T.f
    x1, y0 = x0 + w * T.a, y1 + h * T.e
    lo0, la0 = unmerc(x0, y0); lo1, la1 = unmerc(x1, y1)
    print(f'grid {w}x{h} px, {a.w_km}x{a.h_km} km, bounds {lo0:.5f},{la0:.5f},{lo1:.5f},{la1:.5f}')

    y, m = map(int, a.end.split('-'))
    end = (dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
    sign = Signer()
    items = [i for i in search([lo0, la0, lo1, la1], a.start + '-01', end)
             if 'vv' in i['assets'] and 'vh' in i['assets']]
    pick, table = choose(items, (x0, y0, x1, y1), sign, a.min_valid, a.orbit, a.rel, a.workers)
    print('candidates (months in years whose mid-July scene covers >= %d%% of the frame):' % (a.min_valid * 100))
    for r in table:
        print('  %-10s rel %-3s %3d scenes, %3d months (%3d covered), %s to %s%s%s' % (
            r['orbit'], r['rel'], r['scenes'], r['months'], r['months_covered'], r['first'], r['last'],
            '  gaps ' + ', '.join(r['gaps']) if r['gaps'] else '', '  <- chosen' if r is pick else ''))
    state = pick['orbit']
    rel = pick['rel'] if a.rel != 'all' else 'all'
    items = [i for i in items if i['properties'].get('sat:orbit_state') == state
             and (rel == 'all' or str(i['properties'].get('sat:relative_orbit')) == rel)]
    if pick is not table[0]:
        print('NOTE: forced onto %s rel %s; the most complete is %s rel %s' % (
            state, rel, table[0]['orbit'], table[0]['rel']))
    if pick['last'] < (dt.date.today() - dt.timedelta(days=120)).strftime('%Y-%m'):
        print('NOTE: %s rel %s has nothing after %s in the catalogue. A pass that stopped '
              '(Sentinel-1B failed Dec 2021) can resume when the acquisition plan changes; '
              'rerun later to pick up new scenes.' % (state, rel, pick['last']))
    print(f'{state} rel {rel}: {len(items)} scenes')

    def fetch(it):
        f = os.path.join(od, 'scenes', it['id'] + '.npz')
        if os.path.exists(f):
            return it['id'], f
        try:
            vv = read_window(sign(it['assets']['vv']['href']), T, w, h)
            vh = read_window(sign(it['assets']['vh']['href']), T, w, h)
        except Exception as e:
            return it['id'], 'ERR ' + str(e)[:120]
        np.savez_compressed(f, vv=vv.astype(np.float16), vh=vh.astype(np.float16))
        return it['id'], f

    t0 = time.time(); done = 0; errs = []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for iid, f in ex.map(fetch, items):
            done += 1
            if f.startswith('ERR'):
                errs.append((iid, f))
            if done % 25 == 0:
                print(f'  {done}/{len(items)} scenes, {time.time() - t0:.0f} s', flush=True)
    for e in errs:
        print('  failed', *e)

    by_month = defaultdict(list)
    for it in items:
        by_month[it['properties']['datetime'][:7]].append(it['id'])
    months, VV, VH, N, dropped = [], [], [], [], []
    for mo in sorted(by_month):
        vvs, vhs = [], []
        for iid in by_month[mo]:
            f = os.path.join(od, 'scenes', iid + '.npz')
            if not os.path.exists(f):
                continue
            z = np.load(f)
            vv = z['vv'].astype(np.float32)
            if np.isfinite(vv).mean() < a.min_valid:
                dropped.append(iid); continue
            vvs.append(vv); vhs.append(z['vh'].astype(np.float32))
        if not vvs:
            continue
        with np.errstate(invalid='ignore'), __import__('warnings').catch_warnings():
            __import__('warnings').simplefilter('ignore', RuntimeWarning)
            VV.append(np.nanmean(vvs, 0)); VH.append(np.nanmean(vhs, 0))
        months.append(mo); N.append(len(vvs))
    VV, VH = np.array(VV, np.float32), np.array(VH, np.float32)
    np.savez(os.path.join(od, 'stack.npz'), vv=VV, vh=VH, months=np.array(months), n=np.array(N),
             transform=np.array(T)[:6], crs='EPSG:3857')
    print(f'{len(months)} months ({months[0]} to {months[-1]}), {len(dropped)} scenes under {a.min_valid:.0%} cover')

    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', max(18, h // 22))
    except OSError:
        font = ImageFont.load_default()
    ld = os.path.join(od, 'frames_label'); os.makedirs(ld, exist_ok=True)
    for k, mo in enumerate(months):
        im = Image.fromarray(render(VV[k], VH[k]))
        im.save(os.path.join(od, 'frames', f'{k:04d}.png'))
        d = ImageDraw.Draw(im)
        d.text((12, 8), mo, font=font, fill='white', stroke_width=2, stroke_fill='black')
        im.save(os.path.join(ld, f'{k:04d}.png'))
    for src, out in (('frames', a.name + '.mp4'), ('frames_label', a.name + '_label.mp4')):
        subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-framerate', str(FPS), '-i',
                        os.path.join(od, src, '%04d.png'), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-crf', '18', '-movflags', '+faststart', os.path.join(od, out)], check=True)

    july = [k for k, mo in enumerate(months) if mo.endswith('-07') or mo.endswith('-08')]
    pick, seen = [], set()
    for k in july:
        if months[k][:4] not in seen:
            seen.add(months[k][:4]); pick.append(k)
    if pick:
        cols = 3; rows = -(-len(pick) // cols); s = 0.5
        tw, th = int(w * s), int(h * s)
        sheet = Image.new('RGB', (cols * tw, rows * th))
        for j, k in enumerate(pick):
            im = Image.open(os.path.join(ld, f'{k:04d}.png')).resize((tw, th))
            sheet.paste(im, ((j % cols) * tw, (j // cols) * th))
        sheet.save(os.path.join(od, 'sheet.jpg'), quality=85)

    meta = {'name': a.name, 'centre': [a.lon, a.lat], 'source': 'planetary-computer sentinel-1-rtc',
            'orbit': state, 'relative_orbit': rel, 'grid': {'w': w, 'h': h, 'res_m': 10,
            'transform_3857': list(np.array(T)[:6])}, 'bounds': [lo0, la0, lo1, la1],
            'bounds_3857': [x0, y0, x1, y1], 'months': months, 'n': N, 'fps': FPS,
            'candidates': table, 'video': a.name + '.mp4',
            'render': 'monthly mean dB, Lee 3x3, RGB VV[-20,0] VH[-25,-5] VV-VH[1,15]',
            'failed': [e[0] for e in errs], 'under_cover': dropped,
            'built': dt.datetime.now().isoformat(timespec='seconds')}
    json.dump(meta, open(os.path.join(od, 'meta.json'), 'w'), indent=1)
    if a.stage:
        import shutil
        sd = os.path.join(STAGE, a.name)
        os.makedirs(sd, exist_ok=True)
        shutil.copy2(os.path.join(od, a.name + '.mp4'), sd)
        shutil.copy2(os.path.join(od, 'meta.json'), sd)
        print('staged', sd)
    print('done', od)


if __name__ == '__main__':
    main()
