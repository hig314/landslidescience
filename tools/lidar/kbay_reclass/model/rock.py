"""Rock promotion (Hig, 2026-09-26: the upper-Woz boulder pile, 59.50074 -151.00479, where the
VENDOR surface sits below every return across thousands of bare-rock cells).

A 1 m cell is ROCK when: n >= 6 returns, >= 95% single-return pulses, its 15 x 15 m surroundings
are >= 90% single-return, mean intensity < IMAX (rock ~34k, leaves ~54k at this wavelength; the
tight/rough/texture tests could not tell boulders from dense leaf-on shrub, intensity can), and
the vendor TIN sits > 0.3 m below every return in it. Rare on the six Grewingk sites (0-900
cells); ~7,600 cells at the boulder pile. Only connected patches of >= 4 cells.
In rock cells every return is surface -> ground; TIN them together with the edited ground
(dtm_tinslope's point set is rebuilt here) and replace the blend inside the patches, feathered
over 1 m. Writes dtm_final.tif (and rock_mask.tif)."""
import json, os, sys
import numpy as np
EXCL = (7, 18, 21, 22)   # noise, high noise, SNOW, TEMPORAL EXCLUSION: never ground or surface (stress test 2026-09-26)
from scipy import ndimage
import pyarrow as pa, pyarrow.feather as feather
os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model'); import sitekit as K, v2, surfacefit as SF, best_slope as BS
IMAX = float(os.environ.get('IMAX', '45000'))


def rock_site(s):
    S, f, r, use = SF.features(s); d = f'site/{s}'
    V, _ = K.rd(f'{d}/vendor_dtm.tif'); Bl, _ = K.rd(f'{d}/dtm_blend.tif')
    P = np.load(f'{d}/pts.npz'); keep = ~np.isin(P['cls'], EXCL); inten = P['inten'][keep].astype(float)
    N = S.NX * S.NY
    zmin = np.full(N, np.inf); np.minimum.at(zmin, S.cid[use], S.z[use]); zmin = zmin.reshape(S.NY, S.NX)
    I = (np.bincount(S.cid[use], weights=inten[use], minlength=N) / np.maximum(np.bincount(S.cid[use], minlength=N), 1)).reshape(S.NY, S.NX)
    sf = np.nan_to_num(np.where(f['n'] >= 6, f['single'], np.nan), nan=0); nf = (f['n'] >= 6).astype(float)
    ctx = ndimage.uniform_filter(sf * nf, 15) / np.maximum(ndimage.uniform_filter(nf, 15), 1e-6)
    rock = (f['n'] >= 6) & (f['single'] >= 0.95) & (ctx >= 0.9) & (I < IMAX) & ((zmin - V) > 0.3)
    # CLIFF variant (Hig, 2026-09-26, 59.49864 -151.00824: bare-rock cliff, 64-88 deg, shown as tall
    # 'vegetation' by vendor and blend). On a near-vertical face one 1 m cell holds returns from
    # the base to metres up, so its LOWEST return is at the base and 'vendor below every return'
    # never fires. On steep ground (>= 45 deg, 1 m slope max over 3x3) a dark, all-single-return
    # cell among single-return cells is bare rock: every return is surface. Checked on the Grewingk
    # sites: the all-returns plane is within 0.5 m of truth in 93-97% of such cells.
    Bf = np.where(np.isfinite(Bl), Bl, np.nanmedian(Bl)); gyb, gxb = np.gradient(Bf)
    steep = ndimage.maximum_filter(np.degrees(np.arctan(np.hypot(gxb, gyb))), 3) >= 45
    rock |= (f['n'] >= 6) & (f['single'] >= 0.95) & (ctx >= 0.9) & (I < IMAX) & steep
    lab, n = ndimage.label(rock, structure=np.ones((3, 3)))
    if n:
        size = np.r_[0, ndimage.sum(rock, lab, np.arange(1, n + 1))]; rock = size[lab] >= 4
    out = dict(rock_cells=int(rock.sum()))
    if rock.sum() == 0:
        write_final(s, Bl, rock); return out
    # point set: every return in rock cells + the edited ground everywhere (same selection as dtm_tinslope)
    in_rock = use & rock.ravel()[S.cid]
    Tn, _ = K.rd(f'{d}/dtm_tinslope.tif')
    # edited ground points = returns within 0.05 m of the tinslope surface that the vendor called ground
    cls = P['cls'][keep]
    row = (S.y1 - S.y) - 0.5; col = (S.x - S.x0) - 0.5
    hT = S.z - ndimage.map_coordinates(np.where(np.isfinite(Tn), Tn, np.nanmedian(Tn)), [row, col], order=1, mode='nearest')
    edited = (cls == 2) & (np.abs(hT) < 0.05) & ~in_rock
    sel = in_rock | edited
    t = pa.table({'X': S.x[sel], 'Y': S.y[sel], 'Z': S.z[sel], 'Classification': np.full(int(sel.sum()), 2, np.uint8)})
    fp = f'{d}/rock.feather'; feather.write_feather(t, fp)
    if os.environ.get('TOLTIN', '1') == '1':
        # Tolerance TIN (toltin.py): coarse-to-fine, a return becomes a vertex only if it departs from
        # the surface by more than EPS measured perpendicular to the facet, with >= 3 returns agreeing.
        # A TIN through EVERY return turned 0.1-0.2 m horizontal error on steep faces into crenulations.
        import toltin
        os.remove(fp)
        EPS_ = float(os.environ.get('EPS', '0.15'))
        R = toltin.tol_tin(S.x[sel], S.y[sel], S.z[sel], (S.x0, S.y1, S.NX, S.NY), eps=EPS_)
        if os.environ.get('BREAKLINES', '1') == '1':
            R, nbl = add_breaklines(S, sel, R, EPS_)
            out['breakline_vertices'] = nbl
        # Vertical steps at 1 m: returns from a cliff's top and base interleave horizontally across
        # the lip (0.1-0.2 m position error), and a 2.5D surface sampled at 1 m flips between the two
        # levels cell by cell -- a checkerboard along every cliff edge (Hig, 2026-09-26). Tolerance
        # does not help (it is not noise within a surface). STEPFIX='median3': 3x3 median of the rock
        # surface on steep cells; 'cellmed': steep cells take the median height of their returns, so
        # a cell straddling the lip gets a consistent middle value -- a ramp over 1-2 cells, which is
        # the honest 1 m representation of a vertical step.
        mode = os.environ.get('STEPFIX', 'facefit')
        stp = rock & steep
        if mode == 'facefit':
            # fit steep faces in their own frame (facefit.py): average the ~10 cm across-face scatter
            # BEFORE reading heights, instead of interpolating through individual returns
            import facefit
            region = ndimage.binary_dilation(stp, iterations=1)
            F, nz = facefit.face_fit(S.x[sel], S.y[sel], S.z[sel], S.cid[sel], (S.x0, S.y1, S.NX, S.NY), cells=region)
            if os.environ.get('CURVED', '1') == '1':
                Fq = facefit.face_fit_quad(S.x[sel], S.y[sel], S.z[sel], S.cid[sel], (S.x0, S.y1, S.NX, S.NY), cells=region)
                F = np.where(np.isfinite(Fq), Fq, F)
            # Use the fit only where it IS a face: the cell is steep and its fitted plane is steeper
            # than 35 deg. A plateau cell just back from a cliff lip got a plane tilted by the face
            # in its neighbourhood and sank below the plateau -- closed pits (Hig, 2026-09-26).
            face = stp & (np.abs(nz) <= np.cos(np.radians(35))) & np.isfinite(F)
            wf = np.clip(ndimage.gaussian_filter(face.astype(float), 0.7) * 1.8, 0, 1) * np.isfinite(F)
            R = np.where(wf > 0, wf * np.nan_to_num(F) + (1 - wf) * R, R)
            # pit guard in the fit region: a cell > 0.3 m below all 8 neighbours takes their median
            Rf = np.where(np.isfinite(R), R, np.nanmedian(R))
            fp8 = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], bool)
            pit = region & (Rf < ndimage.minimum_filter(Rf, footprint=fp8, mode='nearest') - 0.3)
            if pit.any():
                R = np.where(pit, ndimage.median_filter(Rf, footprint=fp8, mode='nearest'), R)
            # multi-cell holes at the lip (2-3 cells across): fill small closed depressions to their
            # spill level (morphological reconstruction), only where >= 0.3 m deep, <= 6 cells, and in
            # or beside the cliff-fit region -- real hollows elsewhere are left alone
            from skimage.morphology import reconstruction
            Rf = np.where(np.isfinite(R), R, np.nanmedian(R))
            seed = Rf.copy(); seed[1:-1, 1:-1] = Rf.max()
            filled = reconstruction(seed, Rf, method='erosion')
            dep = (filled - Rf) > 0.3
            lab, nl = ndimage.label(dep)
            if nl:
                size = np.r_[0, ndimage.sum(dep, lab, np.arange(1, nl + 1))]
                near = np.r_[False, ndimage.maximum(ndimage.binary_dilation(region, iterations=2), lab, np.arange(1, nl + 1)).astype(bool)]
                fix = (size[lab] <= 6) & near[lab] & dep
                R = np.where(fix, filled, R)
        elif mode == 'median3':
            R = np.where(stp, ndimage.median_filter(np.where(np.isfinite(R), R, np.nanmedian(R)), 3), R)
        elif mode == 'gauss':
            # smooth steep rock toward a clean ramp (sigma ~1 m), weights so non-steep cells are untouched
            Rf = np.where(np.isfinite(R), R, np.nanmedian(R))
            Rs = ndimage.gaussian_filter(Rf, float(os.environ.get('SIGMA', '1.0')))
            R = np.where(ndimage.binary_dilation(stp, iterations=1), Rs, R)
        elif mode == 'cellmed':
            zc = S.z[sel]; cc = S.cid[sel]
            o = np.lexsort((zc, cc)); cs = cc[o]; st = np.r_[0, np.flatnonzero(cs[1:] != cs[:-1]) + 1]; en = np.r_[st[1:], len(cs)]
            med = np.full(S.NX * S.NY, np.nan); med[cs[st]] = zc[o][(st + en - 1) // 2]
            med = med.reshape(S.NY, S.NX)
            R = np.where(stp & np.isfinite(med), med, R)
        v2.write_like(s, 'dtm_rock.tif', R)
    else:
        K.tin({"type": "readers.arrow", "filename": fp}, f'{d}/dtm_rock.tif', S.S, d, 'dtm_rock'); os.remove(fp)
        R, _ = K.rd(f'{d}/dtm_rock.tif')
    # The rock mask is patchy along a cliff lip (it fires in ~3/4 of the cells), and blending the rock
    # surface in only where it fires alternated rock / blend cell by cell -- a checkerboard along
    # every edge that no smoothing of the rock surface could remove (Hig, 2026-09-26). Close the
    # mask's small gaps and fade the weight smoothly instead of switching per cell.
    rock_c = ndimage.binary_closing(rock, structure=np.ones((3, 3)), iterations=2) | rock
    rock_c = ndimage.binary_fill_holes(rock_c) & ndimage.binary_dilation(rock, iterations=3)
    wgt = np.clip(ndimage.gaussian_filter(rock_c.astype(float), 1.0) * 1.6, 0, 1)
    # Inside rock cells use the rock surface itself. np.maximum(R, Bl) (the first version, 'only
    # raise') zig-zags cell by cell wherever the two surfaces cross -- crenulations of its own.
    comb = np.maximum(R, Bl) if os.environ.get('ROCK_MAX', '0') == '1' else R
    C = np.where(np.isfinite(R) & np.isfinite(Bl), wgt * comb + (1 - wgt) * Bl, Bl)
    write_final(s, C, rock)
    return out


def add_breaklines(S, sel, R, eps):
    """Soft breaklines along cliff lips and bases (Hig, 2026-09-26).
    A triangle that bridges a cliff's lip and base samples the step at arbitrary heights, which is
    part of the lip checkerboard. Standard practice is a constrained TIN along the break lines;
    without a constrained-Delaunay library we force DENSE vertices along them (one per 1 m cell)
    into the tolerance TIN, which ordinary Delaunay then rarely bridges.
      steep bands: slope >= 45 deg on the surface smoothed at 1.5 m, closed
      edge cells:  band cells with a non-band 8-neighbour
      lip / base:  compare an edge cell with its non-band neighbours. At a LIP the plateau beside
                   the face is HIGHER than the edge cell (which already sits a little way down the
                   face); at a BASE the flat ground beside it is LOWER.
      vertex:      lip -> the highest return in the cell; base -> the lowest.
    Returns the rebuilt surface and the number of breakline vertices."""
    import toltin
    Rf = np.where(np.isfinite(R), R, np.nanmedian(R))
    Sm = ndimage.gaussian_filter(Rf, 1.5)
    gy, gx = np.gradient(Sm); sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    band = ndimage.binary_closing(sl >= 45, structure=np.ones((3, 3)))
    band = ndimage.binary_opening(band, structure=np.ones((2, 2)))
    edge = band & ~ndimage.binary_erosion(band, structure=np.ones((3, 3)))
    # mean height of the NON-band 8-neighbours of each cell
    nb = (~band).astype(float); zn = ndimage.convolve(np.where(~band, Rf, 0.0), np.ones((3, 3)), mode='nearest')
    cn = ndimage.convolve(nb, np.ones((3, 3)), mode='nearest')
    zout = np.where(cn > 0, zn / np.maximum(cn, 1), np.nan)
    lip = edge & np.isfinite(zout) & (zout > Rf)          # plateau beside it is higher
    base = edge & np.isfinite(zout) & (zout < Rf)         # flat ground beside it is lower
    x, y, z, cid = S.x[sel], S.y[sel], S.z[sel], S.cid[sel]
    N = S.NX * S.NY
    zmax = np.full(N, -np.inf); np.maximum.at(zmax, cid, z)
    zmin = np.full(N, np.inf); np.minimum.at(zmin, cid, z)
    fx, fy, fz = [], [], []
    for mask, zsel in ((lip, zmax), (base, zmin)):
        cells = np.flatnonzero(mask.ravel() & np.isfinite(zsel))
        if not len(cells): continue
        # the return that attains the extreme in each cell
        want = np.isin(cid, cells)
        ci, zi = cid[want], z[want]; xi, yi = x[want], y[want]
        hit = np.isclose(zi, zsel[ci])
        o = np.lexsort((zi[hit], ci[hit])); first = np.r_[True, ci[hit][o][1:] != ci[hit][o][:-1]]
        fx.append(xi[hit][o][first]); fy.append(yi[hit][o][first]); fz.append(zi[hit][o][first])
    if not fx:
        return R, 0
    fx, fy, fz = np.concatenate(fx), np.concatenate(fy), np.concatenate(fz)
    R2 = toltin.tol_tin(x, y, z, (S.x0, S.y1, S.NX, S.NY), eps=eps, force=(fx, fy, fz))
    return R2, int(len(fx))



def write_final(s, C, rock):
    """dtm_final.tif with its ENCLOSED holes filled (fill_holes.py; margin gaps stay empty),
    plus rock_mask.tif and fill_mask.tif (1 = flat water fill, 2 = harmonic land fill)."""
    import fill_holes as FH
    S_ = K.site_info(s); x0, x1, y0, y1 = S_['bounds']; NX, NY = int(x1 - x0), int(y1 - y0)
    P = np.load(f'site/{s}/pts.npz'); w = P['cls'] == 9
    cid = np.clip((y1 - P['y'][w]).astype(int), 0, NY - 1) * NX + np.clip((P['x'][w] - x0).astype(int), 0, NX - 1)
    wc = np.zeros(NX * NY, bool); wc[cid] = True
    C, fm = FH.fill_enclosed(C, wc.reshape(NY, NX))
    v2.write_like(s, 'dtm_final.tif', C); v2.write_like(s, 'rock_mask.tif', rock.astype(float))
    v2.write_like(s, 'fill_mask.tif', fm.astype(float))

if __name__ == '__main__':
    for s in sys.argv[1:] or v2.SITES + ['woz_boulders']:
        o = rock_site(s)
        if s in v2.SITES:
            B, _ = K.rd(f'site/{s}/dtm_blend.tif'); C, _ = K.rd(f'site/{s}/dtm_final.tif')
            sb, sc = v2.score(s, B), v2.score(s, C)
            print('%-13s rock cells %5d | blend %4.1f/%4.1f -> final %4.1f/%4.1f' % (s, o['rock_cells'], sb['ALL']['high'], sb['ALL']['low'], sc['ALL']['high'], sc['ALL']['low']), flush=True)
        else:
            print('%-13s rock cells %5d' % (s, o['rock_cells']), flush=True)
