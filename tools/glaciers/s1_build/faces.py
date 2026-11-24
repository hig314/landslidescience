"""Confident ice faces from a built S1 stack (build.py), for /glaciers/.

    faces.py NAME [--line ID] [--api URL] [--min-score 0.6] [--stage]

Design (Hig, 2026-11-23): not a face every month, only the months whose own
image shows the face clearly; the story is in the confident ones. Motion
tracking was tried and dropped (docs/GLACIERS.md, "Ice faces from our S1
timelapses").

1. The flow line: the network's flow lines are fetched from --api (public
   list); the one with the most length in the frame (or --line) is followed
   down its down_id chain, and the part in the frame is the line the faces
   are found along, measured up-glacier from where it leaves the frame.
2. The channel: what moves. Bare rock and moraine keep their speckle texture
   month to month; ice and water do not. Pixels whose mean month-to-month
   texture correlation is low, connected to the flow line, small specks
   (grounded bergs) filled.
3. Per month, two candidate faces (facekit.faces_consistent: bright through
   the months either side and not water in > 25% of the past 12;
   facekit.faces_single: this month's own smoothed image), each the edge of
   the glacier against the fjord inside the channel, the piece crossing the
   line furthest down-glacier.
4. Each scored (facekit.face_confidence and friends): the fraction of the
   face that is a clear bright-to-water step (> 3 dB, water on the far
   side), times straightness (chord / length, full marks at 0.6), half if it
   does not reach both walls, times the fraction lying within 250 m (along
   the line) of where it crosses it. The better candidate kept if >= the
   threshold; then a face more than 200 m down-fjord of the kept faces on
   both sides of it is dropped (a winter ice sheet read as glacier).

Writes, in OUT/NAME/faces/: faces.geojson (one LineString per kept month,
properties month, score, method, along_m, line_id), scores.json (every
month's candidates), kept.jpg (each kept face on its frame), position.png.
--stage copies faces.geojson to data/glaciers/s1_build/NAME/, where
`manage.py s1_videos faces data/glaciers/s1_build/NAME` loads it.
"""
import argparse, json, os, shutil, sys, urllib.request
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import facekit as fk                                    # noqa: E402

from build import STAGE                                 # noqa: E402  (the main checkout's data/, which dev mounts)


def fetch_lines(api, d):
    """The network's flow lines (public list), cached beside the stack."""
    fp = os.path.join(d, 'faces', 'network_lines.json')
    req = urllib.request.Request(api.rstrip('/') + '/glaciers/lines/api/scarps/',
                                 headers={'User-Agent': 'landslidescience s1 faces'})
    with urllib.request.urlopen(req, timeout=120) as r:
        fc = json.load(r)
    json.dump(fc, open(fp, 'w'))
    return [f for f in fc['features'] if f['properties'].get('kind') == 'flowline']


def path_line(S, feats, line_id=None):
    """(LineString px, reversed so distance runs up-glacier from the frame
    exit; the starting line's id). The start is --line or the flow line with
    the most length in the frame; it is followed down its down_id chain."""
    from shapely.geometry import LineString, box
    from shapely.ops import linemerge, substring
    h, w = S['vv'].shape[1:]
    frame = box(0, 0, w - 1, h - 1)
    by = {f['properties']['id']: f for f in feats}

    def px(f):
        g = linemerge([LineString([fk.to_px(S['T'], *c) for c in p]) for p in fk.parts(f['geometry'])])
        return g if g.geom_type == 'LineString' else max(g.geoms, key=lambda x: x.length)
    if line_id is None:
        inside = [(px(f).intersection(frame).length, f['properties']['id']) for f in feats]
        line_id = max(inside)[1]
        if max(inside)[0] <= 0:
            raise SystemExit('no flow line of the network crosses the frame: add one first')
    coords = list(px(by[line_id]).coords)
    seen, cur = {line_id}, by[line_id]
    while cur['properties'].get('down_id') in by and cur['properties']['down_id'] not in seen:
        nxt = by[cur['properties']['down_id']]
        seen.add(nxt['properties']['id'])
        L = px(nxt)
        a = L.project(LineString(coords).interpolate(LineString(coords).length))
        rest = substring(L, a, L.length)
        if rest.length > 0:
            coords += list(rest.coords)[1:]
        cur = nxt
    g = LineString(coords).intersection(frame)
    if g.geom_type != 'LineString':
        g = max(g.geoms, key=lambda x: x.length)
    return LineString(list(g.coords)[::-1]), line_id


def channel_mask(S, line, cache):
    """What moves (ice and water), connected to the flow line."""
    from scipy.ndimage import gaussian_filter, binary_opening, binary_closing
    from skimage.measure import label
    from skimage.morphology import disk, remove_small_holes
    if os.path.exists(cache):
        cor = np.load(cache)
    else:
        cor = fk.texture_corr(S)
        np.save(cache, cor)
    rock = binary_opening(gaussian_filter(cor, 2) > 0.45, disk(2))
    lab = label(~rock)
    v = {lab[int(round(y)), int(round(x))] for x, y in
         [line.interpolate(d).coords[0] for d in np.arange(0, line.length, 5)]} - {0}
    return remove_small_holes(binary_closing(np.isin(lab, list(v)), disk(2)), 800)


def strip_mask(S, line, m):
    """For ice the texture test calls rock (a slow, debris-covered piedmont
    lobe, Sitkagi): every pixel within m metres of the flow line."""
    from shapely.geometry import Point
    from scipy.ndimage import distance_transform_edt
    h, w = S['vv'].shape[1:]
    on = np.ones((h, w), bool)
    for d in np.arange(0, line.length, 1.0):
        x, y = line.interpolate(d).coords[0]
        i, j = int(round(y)), int(round(x))
        if 0 <= i < h and 0 <= j < w:
            on[i, j] = False
    return distance_transform_edt(on) <= m / 10.0


def score_months(S, line, channel, connect=50, a_wet=0.4):
    X = fk.anomaly(S)
    W = fk.water_months(X, channel)
    out = []
    for k in range(len(S['months'])):
        cands = []
        for kind, (P, glac, fjord) in (
                ('consistent', fk.faces_consistent(X, k, channel, line, None, win=1, W=W, hist=12,
                                                   max_wet=0.25, readvance=0, whole=True, connect=connect)),
                ('single', fk.faces_single(X[k], channel, line, connect=connect,
                                           wet=W[max(0, k - 12):k].mean(0) > a_wet if k else None))):
            if not P:
                continue
            d, L = P[0]
            c = fk.face_confidence(X[k], glac, fjord, channel, L)
            spans, straight = fk.wall_to_wall(L, channel)
            c.update(method=kind, along=float(d), spans=bool(spans), straight=straight,
                     across=fk.across(L, line, d), coords=[list(map(float, q)) for q in L.coords])
            c['score'] = (c['clear'] * min(1.0, straight / 0.6) * (1.0 if spans else 0.5)
                          * min(1.0, c['across'] / 0.85))
            cands.append(c)
        out.append(sorted(cands, key=lambda c: -c['score']))
    return out


def score_lake(S, line):
    """Lake-terminating fronts (--lake; Hig, 2026-11-24, Sitkagi): each
    month the lagoon (the dark water the line crosses that does not touch
    the frame edge) and its shore where it is CHANGING, i.e. next to pixels
    that turned from ice to water in the record; the face runs along that
    whole stretch and stops where the shore is stable (moraine, beach).
    Scored by the clear bright-to-water fraction alone (no walls)."""
    X = fk.anomaly(S)
    chg, _ = fk.changing_mask(X)
    every = np.ones(X.shape[1:], bool)
    out = []
    for k in range(len(S['months'])):
        lk = fk.lake_mask(X[k], line)
        r = fk.lake_face(lk, chg, line) if lk is not None else None
        if not r:
            out.append([]); continue
        d, L = r
        c = fk.face_confidence(X[k], ~lk, lk, every, L)
        c.update(method='lake', along=float(d), spans=True, straight=1.0, across=1.0,
                 coords=[list(map(float, q)) for q in L.coords])
        c['score'] = c['clear']
        out.append([c])
    return out


def second_pass(S, line, channel, sc, ks, min_step=4.0, max_gap=12):
    """Months with no confident face, searched near the nearest confident
    one in time (within max_gap months; facekit.faces_local). Scored as
    the first pass, with "water" judged by the band's own split, and only
    if the median bright-to-dark step across the face is at least min_step
    dB: the band split makes almost any face look clear, the step is what
    says it is a real contrast. Adds candidates to sc in place."""
    from shapely.geometry import LineString
    X = fk.anomaly(S)
    t = [int(m[:4]) * 12 + int(m[5:]) for m in S['months']]
    kset = set(ks)
    for k in range(len(S['months'])):
        if k in kset or not ks:
            continue
        j = min(ks, key=lambda q: abs(t[q] - t[k]))
        if abs(t[j] - t[k]) > max_gap:
            continue
        P = LineString(sc[j][0]['coords'])
        pcs, glac, fjord, thr = fk.faces_local(X[k], channel, line, P, sc[j][0]['along'])
        if not pcs:
            continue
        d, L = pcs[0]
        c = fk.face_confidence(X[k], glac, fjord, channel, L, thr=thr)
        spans, straight = fk.wall_to_wall(L, channel)
        c.update(method='local', along=float(d), spans=bool(spans), straight=straight,
                 across=fk.across(L, line, d), prior=S['months'][j], coords=[list(map(float, q)) for q in L.coords])
        ok = c['med'] is not None and c['med'] == c['med'] and c['med'] >= min_step
        c['score'] = (c['clear'] * min(1.0, straight / 0.6) * (1.0 if spans else 0.5)
                      * min(1.0, c['across'] / 0.85)) if ok else 0.0
        sc[k] = sorted(sc[k] + [c], key=lambda c: -c['score'])


def bright_check(S, line, channel, sc, require=False, min_step=6.0, max_jump=4.0, tol=8.0, agree=0.8,
                 min_cover=0.6, unconfirmed=0.85, hybrid=True):
    """The face as the bright line (Hig, 2026-11-24): a calving cliff facing
    the radar is often the brightest thing in the scene, and plumes and
    floating ice, which the edge methods can follow, are dimmer
    (facekit.face_bright). Where this month has a strong bright front
    (step to the water in front >= min_step dB, along-line jumps between
    neighbouring bins <= max_jump px), an edge candidate counts only if it
    lies within tol px of the bright front along >= `agree` of it; the
    bright front is also a candidate itself, scored by how much of the
    fjord width it covers (full marks at 0.8, none below min_cover).
    With no strong bright front, an edge candidate needs score >= unconfirmed.
    require=True: a month with
    no strong bright front keeps no edge candidate either."""
    from shapely.geometry import Point
    X = fk.anomaly(S)
    sn = fk.line_coords(line, channel)
    for k in range(len(S['months'])):
        br = fk.face_bright(X[k], channel, line, sn)
        strong = bool(br) and br[2]['step'] >= min_step and br[2]['jump'] <= max_jump
        for c in sc[k]:
            c.setdefault('pre', c['score'])          # the score before this check
            if not strong:
                # nothing to confirm it against: only a very clear edge face
                # stands on its own (Aug 2023 at South Sawyer, 0.68, followed
                # floating ice)
                if require or c['score'] < unconfirmed:
                    c['score'] = 0.0
                c['agree'] = None
                continue
            from shapely.geometry import LineString
            L = LineString(c['coords'])
            P = [br[1].interpolate(t) for t in np.arange(0, br[1].length, 1.0)]
            c['agree'] = float(np.mean([L.distance(p) <= tol for p in P]))
            if c['agree'] < agree:
                c['score'] = 0.0
        if strong and hybrid:
            # the cliff where it is seen, carried to the walls by the best
            # edge candidate (scored before this check): Hig 2026-11-24
            edges = sorted([c for c in sc[k] if c['method'] != 'bright' and 'pre' in c], key=lambda c: -c['pre'])
            if edges and edges[0]['pre'] >= 0.5:
                from shapely.geometry import LineString
                r = fk.cliff_hybrid(LineString(edges[0]['coords']), br[2], sn)
                if r:
                    H, hcov = r
                    spans, straight = fk.wall_to_wall(H, channel)
                    from shapely.ops import nearest_points
                    x = H.intersection(line)
                    hd = line.project(x.centroid) if not x.is_empty else line.project(nearest_points(H, line)[1])
                    # wall to wall: as sure as the cliff; otherwise as much as it covers
                    hs = 1.0 if spans else 0.999 * min(1.0, hcov / 0.8)
                    sc[k].append(dict(method='cliff', along=float(hd), score=hs, clear=1.0, med=br[2]['step'],
                                      water=None, spans=bool(spans), straight=straight, across=None,
                                      cover=hcov, base=edges[0]['method'],
                                      coords=[list(map(float, q)) for q in H.coords]))
        if strong:
            d, L, info = br
            # an edge face that agrees runs wall to wall: it wins over the
            # bright front itself, which often covers only part of the width
            # (the 0.999 keeps that order when both score 1)
            # on its own it must span most of the fjord (Hig: fewer, surer faces)
            bsc = 0.999 * min(1.0, info['cover'] / 0.8) if info['cover'] >= min_cover else 0.0
            sc[k] = sorted(sc[k] + [dict(method='bright', along=float(d), score=bsc,
                                          clear=1.0, med=info['step'], water=None, spans=None, straight=None,
                                          across=None, cover=info['cover'], jump=info['jump'],
                                          coords=[list(map(float, q)) for q in L.coords])],
                           key=lambda c: (-c['score'], c['method'] != 'cliff'))      # on a tie, the cliff


def sheet(S, line, sc, ks, fp, cols=4):
    from PIL import Image, ImageDraw
    tiles = []
    for k in ks:
        im = Image.fromarray(fk.render(S['vv'][k], S['vh'][k])); d = ImageDraw.Draw(im)
        d.line(list(line.coords), fill=(255, 255, 255), width=1)
        d.line([tuple(q) for q in sc[k][0]['coords']], fill=(0, 255, 0), width=3)
        d.rectangle((4, 4, 190, 20), fill=(0, 0, 0))
        d.text((8, 6), '%s  %.2f %s' % (S['months'][k], sc[k][0]['score'], sc[k][0]['method']), fill='white')
        tiles.append(im.resize((im.width // 2, im.height // 2)))
    if not tiles:
        return
    w, h = tiles[0].size
    out = Image.new('RGB', (cols * w, ((len(tiles) + cols - 1) // cols) * h))
    for i, t in enumerate(tiles):
        out.paste(t, ((i % cols) * w, (i // cols) * h))
    out.save(fp, quality=85)


def position_plot(S, sc, ks, fp):
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t = lambda mo: int(mo[:4]) + (int(mo[5:]) - 0.5) / 12
    fig, ax = plt.subplots(figsize=(11, 4))
    for k, c in enumerate(sc):
        if c:
            ax.plot(t(S['months'][k]), c[0]['along'] * 10, 'o', ms=4, mfc='none', mec='0.6')
    ax.plot([t(S['months'][k]) for k in ks], [sc[k][0]['along'] * 10 for k in ks], 'o-', ms=4, lw=0.8, c='C0',
            label='kept')
    ax.plot([], [], 'o', mfc='none', mec='0.6', label='best candidate, not kept')
    ax.set_ylabel('m up-glacier from the frame exit'); ax.legend(loc='upper left'); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(fp, dpi=110); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name')
    ap.add_argument('--line', type=int, help='flow line id to start from (default: most length in the frame)')
    ap.add_argument('--api', default='https://landslidescience.org', help='where the network is read from')
    ap.add_argument('--min-score', type=float, default=0.6)
    ap.add_argument('--connect', type=float, default=50,
                    help='px: a bright component counts as glacier only if the line runs along it this far (0 = off)')
    ap.add_argument('--single-wet', type=float, default=0.4,
                    help='single-month candidate: not glacier where water in more than this share of the past 12 months')
    ap.add_argument('--strip-m', type=float, default=0,
                    help='channel = within this many metres of the flow line, instead of the texture test '
                         '(for debris-covered, slow ice the texture test calls rock)')
    ap.add_argument('--lake', action='store_true',
                    help='lake-terminating front: the lagoon shore where it is changing (Sitkagi)')
    ap.add_argument('--no-second', dest='second', action='store_false',
                    help='skip the second pass (months searched near the nearest confident face)')
    ap.add_argument('--min-step', type=float, default=4.0,
                    help='second pass: median bright-to-dark step across the face, dB')
    ap.add_argument('--no-bright', dest='bright', action='store_false',
                    help='skip the bright-front check (and the bright-front candidates)')
    ap.add_argument('--bright-step', type=float, default=4.0,
                    help='a bright front counts when it is this many dB above the water in front')
    ap.add_argument('--unconfirmed', type=float, default=0.75,
                    help='score an edge face needs where there is no bright front to check it against')
    ap.add_argument('--no-cliff', dest='cliff', action='store_false',
                    help='do not put the face on the bright cliff (edge face carried to the walls)')
    ap.add_argument('--require-bright', action='store_true',
                    help='a month needs a strong bright front to keep any face')
    ap.add_argument('--stage', action='store_true')
    a = ap.parse_args()
    S = fk.load(a.name)
    fd = os.path.join(S['dir'], 'faces'); os.makedirs(fd, exist_ok=True)
    line, lid = path_line(S, fetch_lines(a.api, S['dir']), a.line)
    print('flow line %d, %.1f km in the frame' % (lid, line.length * 10 / 1000))
    if a.lake:
        sc = score_lake(S, line)
    else:
        if a.strip_m:
            channel = strip_mask(S, line, a.strip_m)
        else:
            channel = channel_mask(S, line, os.path.join(fd, 'texture_corr.npy'))
        print('channel %.0f%% of the frame' % (100 * channel.mean()))
        sc = score_months(S, line, channel, a.connect or None, a.single_wet)
    ks = [k for k, c in enumerate(sc) if c and c[0]['score'] >= a.min_score]
    keep = fk.drop_spikes([sc[k][0]['along'] for k in ks])
    spikes = [S['months'][ks[i]] for i in range(len(ks)) if i not in keep]
    ks = [ks[i] for i in keep]
    if a.second and not a.lake:
        n1 = len(ks)
        second_pass(S, line, channel, sc, ks, a.min_step)
        ks = [k for k, c in enumerate(sc) if c and c[0]['score'] >= a.min_score]
        keep = fk.drop_spikes([sc[k][0]['along'] for k in ks])
        spikes = [S['months'][ks[i]] for i in range(len(ks)) if i not in keep]
        ks = [ks[i] for i in keep]
        print('second pass: %d kept after it (%d before)' % (len(ks), n1))
    if a.bright and not a.lake:
        n1 = len(ks)
        bright_check(S, line, channel, sc, require=a.require_bright, min_step=a.bright_step,
                     unconfirmed=a.unconfirmed, hybrid=a.cliff)
        ks = [k for k, c in enumerate(sc) if c and c[0]['score'] >= a.min_score]
        keep = fk.drop_spikes([sc[k][0]['along'] for k in ks])
        spikes = [S['months'][ks[i]] for i in range(len(ks)) if i not in keep]
        ks = [ks[i] for i in keep]
        print('bright check: %d kept after it (%d before)' % (len(ks), n1))
    print('%d of %d months kept; dropped as spikes: %s' % (len(ks), len(sc), ', '.join(spikes) or 'none'))
    h, w = S['vv'].shape[1:]
    # frame_uv: the face as fractions of the frame (u from the west edge, v
    # from the north edge, Web Mercator like the frame), so the app can
    # redraw it wherever the video is placed (sources.refit_video_faces)
    feats = [{'type': 'Feature',
              'properties': {'month': S['months'][k], 'score': round(sc[k][0]['score'], 2),
                             'method': sc[k][0]['method'], 'along_m': round(sc[k][0]['along'] * 10),
                             'line_id': lid,
                             'frame_uv': [[round(x / w, 6), round(y / h, 6)] for x, y in sc[k][0]['coords']]},
              'geometry': {'type': 'LineString',
                           'coordinates': [[round(v, 6) for v in fk.to_lonlat(S['T'], x, y)]
                                           for x, y in sc[k][0]['coords']]}} for k in ks]
    meta = json.load(open(os.path.join(S['dir'], 'meta.json')))
    fc = {'type': 'FeatureCollection', 'build': meta.get('name', a.name), 'min_score': a.min_score,
          'mode': 'lake' if a.lake else 'fjord',
          'line_id': lid, 'features': feats}
    json.dump(fc, open(os.path.join(fd, 'faces.geojson'), 'w'))
    json.dump({S['months'][k]: [{x: v for x, v in c.items() if x != 'coords'} for c in cs]
               for k, cs in enumerate(sc)}, open(os.path.join(fd, 'scores.json'), 'w'), indent=0)
    sheet(S, line, sc, ks, os.path.join(fd, 'kept.jpg'))
    position_plot(S, sc, ks, os.path.join(fd, 'position.png'))
    if a.stage:
        dst = os.path.join(STAGE, a.name)
        os.makedirs(dst, exist_ok=True)
        shutil.copyfile(os.path.join(fd, 'faces.geojson'), os.path.join(dst, 'faces.geojson'))
        print('staged', os.path.normpath(os.path.join(dst, 'faces.geojson')))


if __name__ == '__main__':
    main()
