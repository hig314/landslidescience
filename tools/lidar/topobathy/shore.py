"""THE SHORELINE STAGE (stage C, 2026-10-24): where water meets land, decided once, as one model.

Hig (2026-10-24): "Start with unequivocal ground, unequivocal water, and if available a lake shoreline
between. Take the classification of points in the band containing the shoreline as a hypothesis, and
extend the land and water into that band based on logic that says, 'Are the points in this area best
explained by water, or land?' ... Assume the shoreline is a continuous curve with some finite maximum
complexity ... the shoreline should both be a position in horizontal space, a water level, and also a
ground surface-slope as it passes through the water line."

Reads the composite's out/ after build 2 and the water stage: <id>.tif, class_now.tif, source_index.tif,
water_z.tif, water_bodies.{tif,geojson}, coverage.tif; the recipe's mapped water (lakes whose source
trusts its edge, islands) and the layers' land_support counts.

THE MODEL (the foundational rules it rests on: water is a level, not a height; a level is an inequality
on the bed; a mapped shoreline is a prior, never a stamp)

  LEVEL     every water cell has a level, shore_level.tif: a flat body's one level (water.py's consensus;
            one mapped lake, one level); a RIVER's level a monotone function of the distance down its
            channel fitted to its water returns (isotonic), pinned to the water it enters and meeting the
            lake it leaves (one surface through the outlet: the fit is shifted to the lake's level at its
            head, by nothing at its mouth); a fit rougher than river_max_rms_m is no water surface.  Tidal
            water's observed surface that day (a perched pool keeps its one level).  Water with no body
            keeps its returns' own level where a patch of them is FLAT (nobody_flat_m over nobody_min_cells);
            rough returns classed water are returns off something standing there, judged as solid returns.
            A wet cell with no level of its own takes the level of the levelled water it CONNECTS to through
            water (route_m), never the nearest across land; a wet patch no level reaches (a mapped lake only
            the prior saw) stands at its prior where that is flat (prior_flat_m), else it is no water.
            Nothing else is a level: not a bed, not a prior's hydroflattening, not a void.
  SURE      unequivocal water: a water return at its level, a measured bed, a hole inside a body.
            unequivocal land: a solid return standing sure_dz_m above the nearest level, or farther than
            the band from any water.  Ice joined to the glacier (an ice component of glacier_min_km2 or
            more, or touching a glacier polygon) is land.
  BAND      the rest, within band_m of water (band_tidal_m of tidal water): every observed cell is scored
            under two hypotheses.  WATER predicts z = level (sigma_w_m).  LAND predicts the land surface
            extrapolated from the nearest sure land at its own slope, z = z_rim - tan(s) d (sigma_l_m
            growing sigma_l_per_m with d), and never below the level.  Costs are capped (cost_cap): a log
            at the level fits neither and its neighbours decide it.  A trusted mapped shoreline is a prior
            (prior_cost), the point class a weak hint (class_cost): wet ground classed water that stands
            above the level with land continuity is land.  An unobserved cell (a prior's, a void) costs
            nothing either way, except that inside a body the water stage's union leans it to water.
  CURVE     the labels minimise cost + smoothness by a graph cut with one parameter, `smooth`: the price
            of a label change between neighbours, cheaper across a break in height (break_m), so the line
            follows the bank.  A thin embayment is short and cheap; a jigsaw is long and dear.
  FLOATING  raised land the cut leaves enclosed by water, judged by the body's kind: a proglacial lake
            (ice contact, or glacier within glacier_near_m) expects bergs -- floating unless two surveys
            (island_corr of the patch, land_support) or a mapped island say island; the sea expects
            structures only; a lake with no glacier expects nothing to float (an island, or a speck under
            island_min_m2 that is the surface).  Ice not joined to the glacier floats where bergs are
            expected and is land (a snowfield) elsewhere.  A small flat BODY (under berg_max_m2) standing
            sure_dz_m above the water around it -- a berg's flat top classed water, a deck -- is no pond with
            a level of its own and is judged the same way.
  SLOPE     the bank slope through the waterline, per shoreline cell (shore_slope.tif) and per vertex.

Writes shore_label.tif (0 none/void, 1 land, 2 water, 3 floating, 4 glacier), shore_level.tif,
shore_slope.tif, shoreline.geojson (EPSG:4326, one line per water outline ring: body, kind, level, slope
statistics, confidence, the slope at every vertex) and shore.json.  The fill reads the label and the level
in place of its own shore rules; water_surface.py's surface is the level over every water cell.
"""
from __future__ import annotations
import json
import time
import datetime as _dt
from pathlib import Path
import numpy as np
import rasterio
from rasterio import features
from rasterio.warp import transform_geom
from scipy import ndimage, sparse
from scipy.sparse.csgraph import maximum_flow, breadth_first_order
from .axes import CLASSES, is_prior
from .water import load_bodies, load_mapped, FLAT_KINDS, TIDAL_KINDS
from .fill import slope_deg, _nmean

DEFAULTS = dict(band_m=40.0, band_tidal_m=100.0, sure_dz_m=1.0, sigma_w_m=0.25, sigma_l_m=0.3, sigma_l_per_m=0.03,
                cost_cap=8.0, prior_cost=1.0, prior_edge_m=6.0, class_cost=0.5, body_cost=1.0, debug=False, smooth=2.0, break_m=0.5,
                inland_m=10.0, slope_min_deg=1.0, slope_max_deg=70.0,
                glacier_min_km2=0.5, glacier_touch_m2=10000.0, glacier_near_m=300.0, island_corr=0.25, island_min_m2=50.0,
                river_smooth_m=50.0, tidal_smooth_m=20.0, glaciers=None, min_ring_m=20.0,
                nobody_min_cells=4, nobody_flat_m=0.5, river_max_rms_m=1.0, berg_max_m2=50000.0, route_m=60.0, prior_flat_m=1.0,
                tide_max_m=4.5, tide_stand_lo_m=0.5, low_tide_m=None, river_tide_tol_m=0.3,
                nobody_isolated_cells=100, poly_level_cells=100, poly_level_tol_m=0.1)
LAND_CODES = ("ground", "built", "canopy", "snow")
LBL = dict(none=0, land=1, water=2, floating=3, glacier=4)
S8 = np.ones((3, 3), bool)


def _read(out, name, dtype=np.float32):
    p = Path(out) / name
    if not p.exists():
        return None
    with rasterio.open(p) as ds:
        a = ds.read(1)
        if dtype is np.float32:
            a = a.astype(np.float32)
            if ds.nodata is not None:
                a[a == ds.nodata] = np.nan
            return a
        return a.astype(dtype)


def _gauss_norm(v, has, sig):
    """Normalised Gaussian over `has` cells only (sig in cells); NaN where nothing is near."""
    num = ndimage.gaussian_filter(np.where(has, v, 0.0).astype(np.float64), sig)
    den = ndimage.gaussian_filter(has.astype(np.float64), sig)
    return np.where(den > 1e-3, num / np.maximum(den, 1e-3), np.nan).astype(np.float32)


def _nearest(v, has):
    """Each cell takes the nearest `has` cell's value (NaN where there is none anywhere)."""
    if not has.any():
        return np.full(v.shape, np.nan, np.float32)
    _, (i, j) = ndimage.distance_transform_edt(~has, return_indices=True)
    return v[i, j]


def _route_levels(wet, lev, need, res, route_m, log):
    """Every `need` cell (wet, no level) takes the level of the levelled wet cell nearest to it THROUGH wet cells
    (shortest path over the 8-neighbour grid, scipy's dijkstra with the source of each cell returned), searched within
    route_m of the need cells so the graph stays small; a need cell farther than that from any level, in a component
    that has one, takes the component's Euclidean nearest.  Returns (level, the need cells in components with no level)."""
    from scipy.sparse.csgraph import dijkstra
    out = lev.copy()
    if not need.any():
        return out, np.zeros(wet.shape, bool)
    H, W = wet.shape
    wl, wn = ndimage.label(wet, structure=S8)
    has = wet & np.isfinite(lev)
    cnt = np.bincount(wl[has], minlength=wn + 1) > 0; cnt[0] = False
    comp_ok = cnt[wl]
    nolev = need & ~comp_ok
    need = need & comp_ok
    if need.any():
        near = ndimage.distance_transform_edt(~need, sampling=res) <= route_m
        sub = wet & comp_ok & near
        n = int(sub.sum())
        idx = np.full(wet.shape, -1, np.int32); idx[sub] = np.arange(n, dtype=np.int32)
        rr, cc = np.nonzero(sub)
        rows = []; cols = []; wts = []
        for dr, dc, wt in ((0, 1, 1.0), (1, 0, 1.0), (1, 1, 1.41421356), (1, -1, 1.41421356)):
            r2 = rr + dr; c2 = cc + dc
            ok = (r2 >= 0) & (r2 < H) & (c2 >= 0) & (c2 < W)
            j = idx[r2[ok], c2[ok]]; i = idx[rr[ok], cc[ok]]
            m = j >= 0
            rows.append(i[m]); cols.append(j[m]); wts.append(np.full(int(m.sum()), wt, np.float32))
        G = sparse.csr_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
        src = idx[has & sub]
        t0 = time.time()
        dist, _, sources = dijkstra(G, directed=False, indices=src, min_only=True, return_predecessors=True)
        tgt = idx[need]
        reached = np.isfinite(dist[tgt])
        sv = lev[rr[sources[tgt[reached]]], cc[sources[tgt[reached]]]]
        nr, nc = np.nonzero(need)
        out[nr[reached], nc[reached]] = sv
        left = need.copy(); left[nr[reached], nc[reached]] = False
        log(f"  levels routed through the water: {int(reached.sum())} cells over a graph of {n} ({time.time() - t0:.1f} s); "
            f"{int(left.sum())} farther than {route_m:.0f} m take their component's nearest level")
        if left.any():
            objs = ndimage.find_objects(wl)
            for k in np.unique(wl[left]):
                k = int(k)
                if k <= 0 or objs[k - 1] is None:
                    continue
                sl = objs[k - 1]
                hc = has[sl] & (wl[sl] == k)
                if not hc.any():
                    continue
                nv = _nearest(np.where(hc, lev[sl], np.nan), hc)
                lk = left[sl] & (wl[sl] == k)
                blk = out[sl]; blk[lk] = nv[lk]; out[sl] = blk
    return out, nolev


# --------------------------------------------------------------------------------------- the level
def _river_level(cells, wz, res, P, pin_down, pin_up, log, name, tidal_down=False):
    """A river's level: a non-decreasing function of the geodesic distance down the channel from its
    downstream end, fitted to its water returns by isotonic regression, pinned at the downstream end to the
    water it enters (pin_down: (mask of the end cells, level)) and capped by the lake it leaves (pin_up).
    Returns (level over cells, info)."""
    from skimage.graph import MCP_Geometric
    H, W = cells.shape
    obs = cells & np.isfinite(wz)
    vals = wz[obs]
    info = dict(n_obs=int(obs.sum()))
    if pin_down is not None and pin_down[0].any():
        start = pin_down[0] & cells; lev0 = float(pin_down[1])
    else:
        start = np.zeros(cells.shape, bool); lev0 = None
        if vals.size >= 20:
            lo = np.percentile(vals, 2)
            start = obs & (wz <= lo + 0.05)
        if not start.any():
            start = cells
    costs = np.where(cells, 1.0, np.inf)
    m = MCP_Geometric(costs, fully_connected=True)
    sr, sc = np.nonzero(start)
    d, _ = m.find_costs(list(zip(sr.tolist(), sc.tolist())))
    d = np.where(cells & np.isfinite(d), d * res, np.nan).astype(np.float32)
    dmax = float(np.nanmax(d)) if np.isfinite(d).any() else 0.0
    if vals.size >= 20:
        from sklearn.isotonic import IsotonicRegression
        x = d[obs].astype(np.float64); y = vals.astype(np.float64); w = np.ones(y.size)
        ok = np.isfinite(x)
        x, y, w = x[ok], y[ok], w[ok]
        if lev0 is not None:
            x = np.r_[0.0, x]; y = np.r_[lev0, y]; w = np.r_[1000.0, w]
        ir = IsotonicRegression(increasing=True, out_of_bounds="clip")
        ir.fit(x, y, sample_weight=w)
        lev = np.full(cells.shape, np.nan, np.float32)
        dd = d[cells]; lev[cells] = ir.predict(np.nan_to_num(dd, nan=0.0)).astype(np.float32)
        fit = ir.predict(x[1:] if lev0 is not None else x)
        rms = float(np.sqrt(np.mean((fit - (y[1:] if lev0 is not None else y)) ** 2)))
        info.update(fit_rms_m=round(rms, 3), drop_m=round(float(lev[cells].max() - lev[cells].min()), 2), length_m=round(dmax, 0), method="isotonic")
        if rms > float(P["river_max_rms_m"]):
            # a river's surface is smooth; returns this rough along a channel are off something else (a cascade of berg
            # tops, wet ice, a bar) and give no level
            info.update(method="rejected", why=f"fit rms {rms:.2f} m over {dmax:.0f} m: not a water surface")
            log(f"  river {name}: rejected, {info['why']}")
            return np.full(cells.shape, np.nan, np.float32), info
        # THE LOW-TIDE RIVER (2026-10-25, Hig): a river entering tidal water was surveyed at that day's tide, which held
        # its lower reach up at the sea's level; the river still runs to the sea at low tide.  Over the reach the fit
        # holds at the pinned level (within river_tide_tol_m) the surface is rebuilt as a ramp from the first cell above
        # it down to low_tide_m at the mouth, never above the fit.  The viewer intersects it with the tide it draws.
        lt = P.get("low_tide_m")
        if tidal_down and lev0 is not None and lt is not None and float(lt) < lev0 - 0.05:
            # the tidal reach: the river below the highest tide (tide_max_m) -- at low tide it drains across the flat to
            # the sea at MLLW; its surveyed surface there is the tide's that day, not the river's, so the low-tide surface
            # is a ramp from the fit at the reach's upstream end down to low_tide_m at the mouth, never above the fit
            held = cells & np.isfinite(d) & (lev <= float(P.get("tide_max_m", 4.5)))
            if held.any():
                d_up = float(np.nanmax(d[held])); v_up = float(np.nanmax(lev[held]))
                if d_up > 0:
                    ramp = float(lt) + (v_up - float(lt)) * np.clip(d / max(d_up, 1e-6), 0, 1)
                    low = held & (ramp < lev)
                    lev[low] = ramp[low].astype(np.float32)
                    info.update(low_tide_reach_m=round(d_up, 0), mouth_level_m=round(float(lt), 2), held_level_m=round(lev0, 2),
                                reach_top_level_m=round(v_up, 2))
                    log(f"  river {name}: the low-tide river -- its {d_up:.0f} m tidal reach (surveyed at the tide, {lev0:.2f} m at the mouth) "
                        f"ramps from {v_up:.2f} m down to {float(lt):.2f} m at the mouth")
    else:
        lev = np.full(cells.shape, np.nan, np.float32)
        if lev0 is not None and pin_up is not None:
            lev[cells] = lev0 + (float(pin_up[1]) - lev0) * np.clip(d[cells] / max(dmax, 1.0), 0, 1)
            info.update(method="linear between pins")
        elif vals.size:
            lev[cells] = float(np.median(vals)); info.update(method="median of few returns")
        elif lev0 is not None:
            lev[cells] = lev0; info.update(method="the water it enters")
        else:
            info.update(method="none"); return lev, info
    if pin_up is not None:
        # ONE SURFACE THROUGH THE OUTLET: the water is continuous where the river leaves the lake, so the fit meets the
        # lake's level at its head.  The returns may stand lower (another date, a lower stand): the shift that closes
        # the gap is spread down the river -- all of it at the head, none at the mouth -- so the slope changes a little
        # everywhere and nowhere steps.  A fit above the lake is capped at it.
        end, lu = pin_up; lu = float(lu)
        em = end & cells & np.isfinite(lev) & np.isfinite(d)
        if em.any():
            d_end = float(np.median(d[em])); l_end = float(np.median(lev[em]))
            if d_end > 0 and lu > l_end:
                lev[cells] += (lu - l_end) * np.clip(d[cells] / d_end, 0, 1)
            info.update(outlet_shift_m=round(lu - l_end, 2))
        lev[cells] = np.minimum(lev[cells], lu)
    # smooth ALONG the river (a Gaussian over its cells) so the isotonic steps do not print as sills
    sg = max(float(P["river_smooth_m"]) / res, 1.0)
    sm = _gauss_norm(np.nan_to_num(lev), cells & np.isfinite(lev), sg)
    lev = np.where(cells, sm, np.nan).astype(np.float32)
    if pin_up is not None:
        lev[cells] = np.minimum(lev[cells], float(pin_up[1]))
    return lev, info


def tide_range(P):
    """The heights a water level can take in a box with a sea (low_tide_m set): from low tide less 1.5 m less
    tide_stand_lo_m (a flight at the lowest tide; the same floor as the tidal stands) to tide_max_m.  (None, None)
    without a sea.  No water in such a box stands below it -- a level there is a measured bed read as one."""
    if P.get("low_tide_m") is None:
        return None, None
    return float(P["low_tide_m"]) - 1.5 - float(P["tide_stand_lo_m"]), float(P["tide_max_m"])


def clear_impossible_levels(lev, wet, P, log, when):
    """Rule 3 (a level is an inequality on the bed, never the bed): a level below the lowest stand of the tide is a bed
    read as a level (Halibut Cove: tidal levels to -14 m, the lagoon's to -70 m).  Cleared, so the cell takes the level
    of the water it connects to; enforced once after the levels are set and again before they are routed."""
    lo_r, _ = tide_range(P)
    if lo_r is None:
        return 0
    bad = wet & np.isfinite(lev) & (lev < lo_r)
    if bad.any():
        lev[bad] = np.nan
        log(f"  levels below the lowest stand of the tide ({lo_r:.2f} m) are beds read as levels, cleared {when}: "
            f"{int(bad.sum())} cells")
    return int(bad.sum())


def level_raster(z, cls, bid, btab, wz, res, P, log):
    """shore_level over every water cell: flat bodies at their consensus, rivers by _river_level, tidal
    water at its observed surface, body-less water at its own returns.  Returns (level, bodies info)."""
    lev = np.full(z.shape, np.nan, np.float32)
    mx = int(bid.max()) if bid.size else 0
    kind = {}; L = np.full(mx + 1, np.nan)
    for k, b in btab.items():
        k = int(k)
        if 0 < k <= mx:
            kind[k] = b.get("kind")
            if b.get("kind") in FLAT_KINDS and b.get("consensus_level_m") is not None:
                L[k] = float(b["consensus_level_m"])
    flat = np.isfinite(L[np.clip(bid, 0, mx)]) & (bid > 0)
    lev[flat] = L[bid[flat]]
    info = {}
    # tidal water: the observed surface that day, smoothed, nearest-filled within the body, else its level
    tidal_ids = [k for k, v in kind.items() if v in TIDAL_KINDS]
    if tidal_ids:
        tm = np.isin(bid, tidal_ids) & ~flat                     # a perched pool already has its one level
        has = tm & np.isfinite(wz)
        # only a height within the tide's range is a stand of the tide: the build's water_z carries a layer's height on
        # every cell its class steps call water, and over the multibeam that height is the bed -- 629 cells at -10..-70 m
        # in Halibut Cove's tidal bodies, which the smoothing spread into levels of -9 to -14 m (15 ha, runs 20c-20g)
        lo_r, hi_r = tide_range(P)
        if lo_r is not None:
            has &= (wz >= lo_r) & (wz <= hi_r)
        if has.any():
            sm = _gauss_norm(np.nan_to_num(wz), has, max(float(P["tidal_smooth_m"]) / res, 1.0))
            ok = tm & np.isfinite(sm)
            lev[ok] = sm[ok]
            rest = tm & ~ok
            if rest.any():
                lev[rest] = _nearest(np.where(ok, lev, np.nan), ok)[rest]
        for k in tidal_ids:
            m = (bid == k) & ~np.isfinite(lev)
            if m.any():
                b = btab[k]; v = b.get("level_hi_m", b.get("latest_level_m"))
                if v is not None:
                    lev[m] = float(v)
    # rivers (and sloped tidal creeks are tidal above): a monotone level down the channel
    river_ids = [k for k, v in kind.items() if v == "river"]
    objs = ndimage.find_objects(bid)
    for k in river_ids:
        sl = objs[k - 1] if k - 1 < len(objs) else None
        if sl is None:
            continue
        sl = tuple(slice(max(s.start - 3, 0), min(s.stop + 3, n)) for s, n in zip(sl, bid.shape))
        cells = bid[sl] == k
        if cells.sum() < 10:
            continue
        ring = ndimage.binary_dilation(cells, S8, iterations=2) & ~cells
        nb = bid[sl][ring]; nb = nb[(nb > 0) & (nb != k)]
        wv = wz[sl][cells]; wv = wv[np.isfinite(wv)]
        own = float(np.median(wv)) if wv.size else np.nan
        pin_down = None; pin_up = None; down_id = up_id = None
        for j in np.unique(nb):
            j = int(j)
            lj = lev[sl][(bid[sl] == j)]; lj = lj[np.isfinite(lj)]
            if not lj.size:
                continue
            lj = float(np.median(lj))
            if np.isfinite(own) and lj < own - 0.3 or (not np.isfinite(own) and kind.get(j) in TIDAL_KINDS):
                if pin_down is None or lj < pin_down[1]:
                    end = ring & (ndimage.binary_dilation(bid[sl] == j, S8, iterations=2))
                    end = ndimage.binary_dilation(end, S8, iterations=2) & cells
                    pin_down = (end, lj); down_id = j
            elif np.isfinite(own) and lj > own + 0.3 and kind.get(j) in FLAT_KINDS:
                if pin_up is None or lj > pin_up[1]:
                    end = ring & (ndimage.binary_dilation(bid[sl] == j, S8, iterations=2))
                    end = ndimage.binary_dilation(end, S8, iterations=2) & cells
                    pin_up = (end, lj); up_id = j
        lv, inf = _river_level(cells, wz[sl], res, P, pin_down, pin_up, log, k, tidal_down=(kind.get(down_id) in TIDAL_KINDS))
        inf.update(enters=down_id, enters_level_m=None if pin_down is None else round(pin_down[1], 2),
                   leaves=up_id, leaves_level_m=None if pin_up is None else round(pin_up[1], 2))
        info[k] = inf
        if inf.get("method") == "rejected":
            continue                                             # no level: its returns are judged as solid returns
        ok = cells & np.isfinite(lv)
        blk = lev[sl]; blk[ok] = lv[ok]; lev[sl] = blk
    # water with no body: a FLAT patch of returns is a pool at its one level (the median); a rough patch is no
    # water surface -- returns off wet ice, berg tops, a bar -- and gets no level (run() judges them as solid returns)
    wc = (bid == 0) & (cls == CLASSES["water"])
    nobody = wc & np.isfinite(wz)
    if nobody.any():
        nl, nn = ndimage.label(nobody, structure=S8)
        idx = np.arange(1, nn + 1)
        cnt = np.bincount(nl.ravel(), minlength=nn + 1)[1:]
        lo = ndimage.labeled_comprehension(wz, nl, idx, lambda v: np.percentile(v, 16), float, np.nan)
        hi = ndimage.labeled_comprehension(wz, nl, idx, lambda v: np.percentile(v, 84), float, np.nan)
        md = ndimage.labeled_comprehension(wz, nl, idx, np.median, float, np.nan)
        flat_c = (cnt >= int(P["nobody_min_cells"])) & ((hi - lo) <= float(P["nobody_flat_m"]))
        # an ISOLATED pool -- no levelled water within route_m -- must be big enough to be a pond of its own
        # (nobody_isolated_cells): 31 cells of a prior's hydroflattening on a hillside at 489 m were a pond (run 18)
        if np.isfinite(lev).any():
            dlev = ndimage.distance_transform_edt(~np.isfinite(lev), sampling=res)
            iso = np.asarray(ndimage.minimum(dlev, nl, idx)) > float(P["route_m"])
        else:
            iso = np.ones(nn, bool)
        flat_c &= ~iso | (cnt >= int(P["nobody_isolated_cells"]))
        lut = np.r_[np.nan, np.where(flat_c, md, np.nan)].astype(np.float32)
        v = lut[nl]; own = np.isfinite(v)
        # a flat pool BESIDE levelled water is that water at another stand (returns off the lake 0.4 m above its
        # level, 2021 against 2023): it takes the level beside it, not its own; only an isolated pool is its own level
        beside = own & ~iso[np.maximum(nl - 1, 0)] & (nl > 0)
        if beside.any():
            near_l = _nearest(np.where(np.isfinite(lev), lev, np.nan), np.isfinite(lev))
            v = np.where(beside, near_l, v)
        lev[own] = v[own]
        rest = wc & ~np.isfinite(wz)                            # a hole in such a pool, beside its returns
        if rest.any() and own.any():
            near = _nearest(np.where(own, lev, np.nan), own)
            dn = ndimage.distance_transform_edt(~own, sampling=res)
            take = rest & (dn <= 20.0)
            lev[take] = near[take]
        log(f"  body-less water: {int(flat_c.sum())} flat pools levelled ({int(cnt[flat_c].sum()) * res * res / 1e4:.2f} ha); "
            f"{int((~flat_c).sum())} rough patches of water returns get no level ({int(cnt[~flat_c].sum()) * res * res / 1e4:.2f} ha)")
    # a body's cells the rules above left without a level take the nearest level inside the body
    left = (bid > 0) & ~np.isfinite(lev)
    if left.any():
        have = (bid > 0) & np.isfinite(lev)
        if have.any():
            near = _nearest(np.where(have, lev, np.nan), have)
            lev[left] = near[left]
    if river_ids:
        log(f"  rivers: {len(info)} levelled down their channels -- " +
            ", ".join(f"body {k} {v.get('method')} drop {v.get('drop_m')} m over {v.get('length_m')} m"
                      f"{' into ' + str(v['enters']) if v.get('enters') else ''}{' from ' + str(v['leaves']) if v.get('leaves') else ''}" for k, v in list(info.items())[:8]))
    return lev, info


# --------------------------------------------------------------------------------------- the glacier
def read_on_grid(path, prof, dtype=np.int32):
    """A stage raster on the composite's grid: read as is when the grids match, else resampled nearest BY GEOREFERENCE
    (a coarse lattice, or a full-box raster against a cropped composite -- 2026-10-24, the crop test sliced the box's
    top-left corner out of a full-grid water_bodies.tif and judged the crop with another place's bodies)."""
    from rasterio.warp import reproject, Resampling
    with rasterio.open(path) as ds:
        same = ds.shape == (prof["height"], prof["width"]) and ds.transform.almost_equals(prof["transform"], precision=1e-6)
        if same:
            return ds.read(1).astype(dtype)
        out = np.zeros((prof["height"], prof["width"]), dtype)
        reproject(rasterio.band(ds, 1), out, dst_transform=prof["transform"], dst_crs=prof["crs"], resampling=Resampling.nearest,
                  dst_nodata=0)
        return out


def water_blind_mask(recipe, prof, si, log=print):
    """Cells whose every contributing source is WATER-BLIND -- a layer's `water_blind`: names of sources that cannot
    observe a water surface or a bed (photogrammetry over water reconstructs a noisy surface around the waterline:
    the 2023 SfM read 1-3 m under Emerald Lake's level and was taken for its bed, 2026-10-24) -- read from the layer's
    `source_bits` raster (bit k = source k; names in joint_report.json beside it, or the layer's `source_bit_map`)
    where that layer won the cell.  Inside the water (a body, a trusted polygon) such a cell is no observation: the
    stage treats it as unobserved and the fill as a hole.  None when no layer declares any."""
    from rasterio.warp import reproject, Resampling
    out = np.zeros((prof["height"], prof["width"]), bool); any_ = False
    for k, L in enumerate(recipe.d["layers"]):
        names = L.get("water_blind"); sb = L.get("source_bits")
        if not names or not sb:
            continue
        rep = Path(sb).parent / "joint_report.json"
        bits = (json.loads(rep.read_text()).get("src_bits") if rep.exists() else None) or L.get("source_bit_map") or {}
        bb = 0
        for nme in names:
            if nme not in bits:
                log(f"  water-blind source {nme}: no bit for it beside {sb}"); continue
            bb |= int(bits[nme])
        if not bb:
            continue
        src = np.zeros(out.shape, np.uint16)
        with rasterio.open(sb) as ds:
            reproject(rasterio.band(ds, 1), src, dst_transform=prof["transform"], dst_crs=prof["crs"],
                      resampling=Resampling.nearest, dst_nodata=0)
        m = (src != 0) & ((src & ~np.uint16(bb)) == 0)
        if si is not None:
            m &= si == k
        out |= m; any_ = True
        log(f"  water-blind: {L['name']} cells from {', '.join(names)} alone: {int(m.sum()) * abs(prof['transform'].a) ** 2 / 1e6:.3f} km2")
    return out if any_ else None


def glacier_mask(cls, res, P, prof, log, trusted_water=None):
    """Ice joined to a glacier: every ice component of glacier_min_km2 or more, plus any ice component of
    glacier_touch_m2 or more touching a glacier polygon (P["glaciers"], a vector file in any CRS) that does not
    lie mostly inside a TRUSTED mapped water polygon -- the outline is older than the survey, and the water has
    moved past it (bergs in the newly opened lake sit inside the 2020 outline)."""
    ice = cls == CLASSES.get("ice", -1)
    if not ice.any():
        return np.zeros(cls.shape, bool)
    lab, n = ndimage.label(ice, structure=S8)
    sz = np.bincount(lab.ravel(), minlength=n + 1) * res * res / 1e6
    big = np.zeros(n + 1, bool); big[1:] = sz[1:] >= float(P["glacier_min_km2"])
    inwater = np.zeros(n + 1, bool)
    if trusted_water is not None and trusted_water.any():
        fr = np.bincount(lab.ravel(), weights=trusted_water.ravel(), minlength=n + 1) / np.maximum(np.bincount(lab.ravel(), minlength=n + 1), 1)
        inwater = fr > 0.5
    poly = np.zeros(cls.shape, bool)
    gp = P.get("glaciers")
    if gp and Path(str(gp)).exists():
        import fiona
        from rasterio.warp import transform_bounds
        geoms = []
        H, W = cls.shape; T = prof["transform"]
        x0, y1 = T * (0, 0); x1, y0 = T * (W, H)
        with fiona.open(str(gp)) as src:
            src_crs = src.crs_wkt or src.crs
            bb = transform_bounds(prof["crs"], src_crs, x0, y0, x1, y1)
            for f in src.filter(bbox=bb):
                geoms.append((transform_geom(src_crs, prof["crs"], f["geometry"]), 1))
        if geoms:
            poly = features.rasterize(geoms, out_shape=cls.shape, transform=prof["transform"], fill=0, dtype="uint8").astype(bool)
            hit = np.unique(lab[ice & poly]); hit = hit[hit > 0]
            ok = (sz[hit] * 1e6 >= float(P.get("glacier_touch_m2", 10000.0))) & ~inwater[hit]
            big[hit[ok]] = True
    gl = big[lab] & ice
    log(f"  glacier: {int(big[1:].sum())} ice components ({float(sz[1:][big[1:]].sum()):.2f} km2) are the glacier"
        f"{' (polygon ' + str(gp) + ')' if poly.any() else ''}; other ice {int((ice & ~gl).sum()) * res * res / 1e4:.1f} ha")
    return gl


# --------------------------------------------------------------------------------------- the sill test
def sill_test(z, obs, wzr, bid, btab, res, P, log):
    """THE FLAT-WATER RULE, perched water (2026-10-28, WATER_REVIEW_PLAN.md section 1; run 20h2's bay at
    59.63493,-151.21814): a body can hold a level above the sea only if its outlet stands up to hold it.  Three bodies
    of one flight's sea surface (2.75 m, 7-10 ha) over the -53 m multibeam were kinded perched, and the water surface
    kept their level over the tide the viewer chose -- 4 m of "topography" on the low-tide sea.

    The sill of a body is the lowest height a path from it to the sea must climb over (the sea = the sea and tidal
    bodies).  The path's heights are what is known to stand there: an observed surface or bed, else a water return
    (the water was at least that high; the bed under it lower), else nothing (unknown: the path is blocked, so a body
    whose outlet nobody saw keeps its kind).  Tested at a ladder of heights by connectivity: a body's cells below a
    height that join the sea through cells below it have a sill below it.  A body whose sill lies below the lowest tide
    is joined to the open sea and is the sea: re-kinded tidal (its level that day, never one of its own).  Candidates
    are perched bodies and unmapped low-lying lakes (level under tide_max_m); a mapped one is reported, not changed.
    Returns ({id: override}, {id: report})."""
    lo_t = P.get("low_tide_m")
    if lo_t is None:
        return {}, {}
    hi_t = float(P["tide_max_m"])
    seed_ids = [int(k) for k, b in btab.items() if b.get("kind") in ("sea", "tidal")]
    cand = {}
    for k, b in btab.items():
        k = int(k); lv = b.get("consensus_level_m")
        if b.get("kind") == "perched" or (b.get("kind") in ("lake", "ephemeral") and lv is not None and lv < hi_t):
            cand[k] = b
    if not seed_ids or not cand:
        return {}, {}
    T = np.where(obs, z, np.where(np.isfinite(wzr), wzr, np.nan)).astype(np.float32)
    seed = np.isin(bid, seed_ids)
    mx = int(bid.max())
    ladder = [float(lo_t)] + [v for v in (0.0, 1.0, 2.0, 3.0, hi_t) if v > float(lo_t)]
    below = {}                                        # id -> the lowest rung its sill lies under
    for th in ladder:
        low = np.isfinite(T) & (T < th)
        lab, n = ndimage.label(low | seed, structure=S8)
        has = np.zeros(n + 1, bool); has[np.unique(lab[seed])] = True; has[0] = False
        ok = has[lab] & low & (bid > 0)
        cnt = np.bincount(bid[ok], minlength=mx + 1)
        for k in cand:
            if k not in below and k <= mx and cnt[k] > 0:
                below[k] = th
        del lab, low, ok
    over, rep = {}, {}
    for k, b in cand.items():
        th = below.get(k)
        r = dict(kind=b.get("kind"), level_m=b.get("consensus_level_m"), sill_below_m=th, mapped=bool(b.get("mapped_by")),
                 x=b.get("x"), y=b.get("y"), area_m2=b.get("area_m2"))
        rep[k] = r
        if th is not None and th <= float(lo_t) and not b.get("mapped_by"):
            over[k] = dict(kind="tidal", consensus_level_m=None, was=b.get("kind"), was_level_m=b.get("consensus_level_m"),
                           why=f"sill test: joined to the sea through ground or bed below the lowest tide ({lo_t} m)")
            r["rekinded"] = "tidal"
    n_f = sum(1 for r in rep.values() if r["sill_below_m"] is not None and r["sill_below_m"] <= float(lo_t))
    log(f"  sill test: {len(cand)} perched / low-lying bodies; {n_f} joined to the open sea below the lowest tide "
        f"({len(over)} re-kinded tidal, {sum(r['area_m2'] or 0 for k, r in rep.items() if k in over) / 1e4:.1f} ha; "
        f"{n_f - len(over)} mapped, reported); sills: " +
        ", ".join(f"{k}<{r['sill_below_m']}" for k, r in list(rep.items())[:12]))
    return over, rep


# --------------------------------------------------------------------------------------- the graph cut
def _cut(cw, cl, band, zb, res, P, log):
    """Min-cut labelling of the band: water (source side) vs land.  Costs in nats, scaled to integers."""
    scale = 100.0
    lam = float(P["smooth"]) * scale; brk = float(P["break_m"])
    lab, n = ndimage.label(band, structure=S8)
    water = np.zeros(band.shape, bool)
    stats = dict(components=int(n), cells=int(band.sum()), energy=0.0)
    objs = ndimage.find_objects(lab)
    t0 = time.time()
    for k in range(1, n + 1):
        sl = objs[k - 1]
        m = lab[sl] == k
        nn = int(m.sum())
        if nn == 0:
            continue
        idx = -np.ones(m.shape, np.int64); idx[m] = np.arange(nn)
        s, t = nn, nn + 1
        W_ = np.rint(np.nan_to_num(cw[sl][m], nan=0.0) * scale).astype(np.int64)
        L_ = np.rint(np.nan_to_num(cl[sl][m], nan=0.0) * scale).astype(np.int64)
        rows = [np.full(nn, s), np.arange(nn)]; cols = [np.arange(nn), np.full(nn, t)]; caps = [L_, W_]
        zz = zb[sl]
        r, c = np.nonzero(m)
        for dr, dc in ((0, 1), (1, 0)):
            r2, c2 = r + dr, c + dc
            inb = (r2 < m.shape[0]) & (c2 < m.shape[1])
            r2, c2, r1, c1 = r2[inb], c2[inb], r[inb], c[inb]
            ok = m[r2, c2]
            r1, c1, r2, c2 = r1[ok], c1[ok], r2[ok], c2[ok]
            if r1.size == 0:
                continue
            dz = np.abs(zz[r1, c1] - zz[r2, c2])
            w = 0.2 + np.exp(-np.nan_to_num(dz, nan=0.0) / brk)
            cap = np.rint(lam * w).astype(np.int64)
            a, b = idx[r1, c1], idx[r2, c2]
            rows += [a, b]; cols += [b, a]; caps += [cap, cap]
        R = np.concatenate(rows); C = np.concatenate(cols); V = np.concatenate(caps)
        keep = V > 0
        G = sparse.csr_matrix((V[keep].astype(np.int32), (R[keep], C[keep])), shape=(nn + 2, nn + 2))
        G.sum_duplicates()
        if nn > 2_000_000:
            log(f"    band component {k}: {nn} cells, cutting...")
        res_ = maximum_flow(G, s, t)
        flow = res_.flow if hasattr(res_, "flow") else res_.residual
        resid = G - flow
        resid.data = np.maximum(resid.data, 0); resid.eliminate_zeros()
        reach = breadth_first_order(resid, s, directed=True, return_predecessors=False)
        side = np.zeros(nn + 2, bool); side[reach] = True
        wm = np.zeros(m.shape, bool); wm[m] = side[:nn]
        water[sl] |= wm
        stats["energy"] += float(res_.flow_value) / scale
    stats["seconds"] = round(time.time() - t0, 1)
    return water, stats


# --------------------------------------------------------------------------------------- the stage
def run(recipe, log=print):
    cfg = dict(DEFAULTS); cfg.update(recipe.d.get("shore", {}))
    if cfg.get("low_tide_m") is None:                         # the low-tide river: MLLW from the fill's params
        cfg["low_tide_m"] = (recipe.d.get("fill") or {}).get("low_tide_m")
    out = recipe.out_dir; t0 = time.time()
    with rasterio.open(out / f"{recipe.id}.tif") as ds:
        prof = ds.profile; res = float(ds.res[0])
        z = ds.read(1).astype(np.float32); z[z == ds.nodata] = np.nan
    cls = _read(out, "class_now.tif", np.uint8)
    si = _read(out, "source_index.tif", np.int32)
    wz = _read(out, "water_z.tif")
    cov = _read(out, "coverage.tif", np.int32)
    B = load_bodies(out, overrides=False)               # raw: this stage writes the corrections (shore_bodies.json)
    (out / "shore_bodies.json").unlink(missing_ok=True)
    if B is None:
        log("  no water bodies (run the water stage first)"); return None
    bid = read_on_grid(B[0], prof, np.int32)
    btab = B[1]
    raw = recipe.d["layers"]
    # cells a previous fill won are holes (by the RAW layer index: source_index was written by a build that had the
    # fill layer, which Recipe drops from .layers while its file is absent); a prior's cells are no observation
    fill_idx = [k for k, L in enumerate(raw) if L.get("optional")]
    prior_idx = [k for k, L in enumerate(raw) if is_prior(L)]
    if si is not None:
        for k in fill_idx:
            nf = int((si == k).sum())
            if nf:
                z[si == k] = np.nan; log(f"  a previous fill's cells are holes again: {nf * res * res / 1e6:.3f} km2")
    prior_cell = np.isin(si, prior_idx) if (si is not None and prior_idx) else np.zeros(z.shape, bool)
    outside = (cov == 0) if cov is not None else np.zeros(z.shape, bool)
    # ---- the mapped water: trusted shorelines (a prior for the cut, no glacier inside one, one level inside one) and islands
    mp = np.zeros(z.shape, bool); mi = np.zeros(z.shape, bool); mpid = np.zeros(z.shape, np.int32); n_tr = 0
    noise_m = np.zeros(z.shape, bool); review_over = {}
    wcfg = recipe.d.get("water") or {}
    if wcfg.get("mapped"):
        feats = load_mapped(wcfg["mapped"], prof["crs"])
        tr = [f["geom"] for f in feats if f["kind"] in ("lake", "river") and f.get("edge") == "trusted"]
        isl = [(f["geom"], 1) for f in feats if f["kind"] == "island"]
        if tr:
            mpid = features.rasterize([(g, i + 1) for i, g in enumerate(tr)], out_shape=z.shape, transform=prof["transform"], fill=0, dtype="int32")
            mp = mpid > 0; n_tr = len(tr)
        if isl:
            mi = features.rasterize(isl, out_shape=z.shape, transform=prof["transform"], fill=0, dtype="uint8").astype(bool)
        # THE REVIEW (2026-10-28, WATER_REVIEW_PLAN.md W3; tools/lidar/water/review.geojson, polygons.geojson): a flag the
        # reviewer called noise is no observation inside the water (open space for the fill, below); a reviewed polygon
        # sets the kind of the bodies mostly under it (lake | river | sea) and, given level_m, a lake's one level
        nz_ = [(f["geom"], 1) for f in feats if f["kind"] == "noise"]
        if nz_:
            noise_m = features.rasterize(nz_, out_shape=z.shape, transform=prof["transform"], fill=0, dtype="uint8").astype(bool)
        rv = [f for f in feats if f["id"] == "reviewed" and f["kind"] in ("lake", "river", "sea")]
        if rv:
            rvid = features.rasterize([(f["geom"], i + 1) for i, f in enumerate(rv)], out_shape=z.shape, transform=prof["transform"],
                                      fill=0, dtype="int32")
            nb_ = int(bid.max()) + 1
            tot_ = np.bincount(bid.ravel(), minlength=nb_)
            for i, f in enumerate(rv):
                inside_ = np.bincount(bid[rvid == i + 1], minlength=nb_)
                for k in np.nonzero((inside_ >= 0.5 * np.maximum(tot_, 1)) & (np.arange(nb_) > 0))[0]:
                    k = int(k)
                    if k not in btab:
                        continue
                    o = dict(kind={"lake": "lake", "river": "river", "sea": "tidal"}[f["kind"]], was=btab[k].get("kind"),
                             was_level_m=btab[k].get("consensus_level_m"), why=f"reviewed polygon {f.get('fid') or f.get('name') or i}")
                    o["consensus_level_m"] = (f["level_m"] if f["kind"] == "lake" and f.get("level_m") is not None
                                              else (btab[k].get("consensus_level_m") if f["kind"] == "lake" else None))
                    review_over[k] = o
                    btab[k] = dict(btab[k], kind=o["kind"], consensus_level_m=o["consensus_level_m"], mapped_by="reviewed")
            log(f"  reviewed polygons: {len(rv)}; {len(review_over)} bodies take their kind or level from one")
    # ---- WATER STAMPS (recipe "water_stamps": rasters, 1 = water; make_ice's lake_2021 / retreat_2023, which the
    # build applies as class steps with a ceiling): mapped water of their own kind, like a trusted polygon for the
    # UNOBSERVED -- a cell inside one that no survey observed is water (2026-10-25: the 2012 prior's ice stood as a
    # 130 m plateau in the retreat beyond Hig's 2017-22 shoreline, where every 2023 source read the lake)
    stamp = np.zeros(z.shape, bool)
    for sp_ in (recipe.d.get("water_stamps") or []):
        sp_ = Path(str(recipe.resolve(sp_)))
        if sp_.exists():
            from rasterio.warp import reproject, Resampling as _Rs
            st_ = np.zeros(z.shape, np.uint8)
            with rasterio.open(sp_) as ds_:
                reproject(rasterio.band(ds_, 1), st_, dst_transform=prof["transform"], dst_crs=prof["crs"], resampling=_Rs.nearest, dst_nodata=0)
            stamp |= st_ == 1
    if stamp.any():
        log(f"  water stamps: {int(stamp.sum()) * res * res / 1e4:.1f} ha of mapped water (unobserved cells inside are water)")
    # ---- WATER-BLIND sources (a layer's water_blind) see nothing inside the water: their cells inside a body or a
    # trusted water polygon are unobserved -- the SfM's noisy surface over Emerald Lake was its shore and its bed
    blind = water_blind_mask(recipe, prof, si, log)
    blind_in = np.zeros(z.shape, bool)
    if blind is not None:
        bl_in = blind & ((bid > 0) | (mp & ~mi) | stamp)
        if bl_in.any():
            z[bl_in] = np.nan; blind_in = bl_in
            log(f"  water-blind cells inside the water: {int(bl_in.sum()) * res * res / 1e4:.2f} ha are no observation")
    if noise_m.any():
        nz_in = noise_m & np.isfinite(z)
        z[nz_in] = np.nan; blind_in = blind_in | nz_in
        log(f"  reviewed as noise: {int(nz_in.sum()) * res * res / 1e4:.2f} ha of observations inside the water are no observation")
    obs = np.isfinite(z) & ~prior_cell
    land_codes = [CLASSES[k] for k in LAND_CODES if k in CLASSES]
    water_cls = cls == CLASSES["water"]
    bed_cls = cls == CLASSES["bed"]
    ice_cls = cls == CLASSES.get("ice", -1)
    log(f"  shore: {z.shape[1]}x{z.shape[0]} @ {res} m; observed {int(obs.sum()) * res * res / 1e6:.1f} km2, bodies {len(btab)}")
    # ---- THE SILL TEST (the flat-water rule): a perched body joined to the open sea below the lowest tide is the sea
    wzr0 = wz if wz is not None else np.full(z.shape, np.nan, np.float32)
    overrides, sills = sill_test(z, obs, wzr0, bid, btab, res, cfg, log)
    overrides = {**overrides, **review_over}               # a reviewed polygon outranks the sill test
    for k, o in overrides.items():
        btab[k] = dict(btab[k], kind=o["kind"], consensus_level_m=o["consensus_level_m"])
        btab[k].setdefault("notes", []); btab[k]["notes"] = list(btab[k]["notes"]) + [o["why"]]
    # ---- LEVEL
    lev, river_info = level_raster(z, cls, bid, btab, wz, res, cfg, log)
    has_lev = np.isfinite(lev)
    # a water return with no level of its own (a rough patch, a river fit rejected) is a return off something that
    # stands at that height -- wet ice, a berg, a bar -- and is judged as a solid return; levelled water is the water
    # TIDAL STANDS (2026-10-25, Halibut Cove and its lagoon): in tidal water the level is the surface that day, and the
    # day had many levels -- a flight line hours after another saw the tide a metre higher, and the water stage split
    # the lagoon into sheets at 1.0 and 2.5 m.  A water return within band_tidal_m of tidal water that no level fits is
    # not a return off something standing there: it is the sea at its own stand, when its height lies within the
    # tide's range (from tide_stand_lo_m below the lowest tidal level seen to tide_max_m).  Its level is its own,
    # smoothed over its patch; it is sure water; the tide the viewer draws replaces it over the sea anyway.
    tidal_ids0 = [int(k) for k, b in btab.items() if b.get("kind") in TIDAL_KINDS]
    tstand = np.zeros(z.shape, bool)
    wzr = wz if wz is not None else np.full(z.shape, np.nan, np.float32)   # the water returns themselves (water_z.tif)
    if tidal_ids0:
        tcell = np.isin(bid, tidal_ids0)
        tl = lev[tcell & np.isfinite(lev)]
        if tl.size:
            # the range floor: the lowest tidal level seen, but never below low tide less 1.5 m (a tidal body's "level" can
            # be a measured bed read as one; a stand of the sea cannot lie 26 m down)
            lo_t = float(np.nanmin(tl))
            if cfg.get("low_tide_m") is not None:
                lo_t = max(lo_t, float(cfg["low_tide_m"]) - 1.5)
            lo_t -= float(cfg["tide_stand_lo_m"]); hi_t = float(cfg["tide_max_m"])
            reach = ndimage.distance_transform_edt(~tcell, sampling=res) <= float(cfg["band_tidal_m"])
            # the return itself is the water stage's water_z (the build keeps no water return in its surface, so z is
            # void on these cells and only the class says water)
            inrange = water_cls & np.isfinite(wzr) & reach & (wzr >= lo_t) & (wzr <= hi_t)
            tstand = inrange & (~has_lev | (tcell & (np.abs(wzr - np.nan_to_num(lev, nan=-1e9)) > float(cfg["sigma_w_m"]))))
            if tstand.any():
                sm_ = _gauss_norm(np.nan_to_num(wzr), tstand, max(float(cfg["tidal_smooth_m"]) / res, 1.0))
                lev[tstand] = sm_[tstand]; has_lev = np.isfinite(lev)
                log(f"  tidal stands: {int(tstand.sum()) * res * res / 1e4:.2f} ha of water returns at other stands of the tide "
                    f"({lo_t:.2f}..{hi_t:.2f} m) are the surface at their own level")
    if clear_impossible_levels(lev, np.isfinite(lev), cfg, log, "as set"):
        has_lev = np.isfinite(lev)
    solid = obs & (np.isin(cls, land_codes) | (water_cls & ~has_lev))
    water_any = (bid > 0) | (water_cls & has_lev)
    log(f"  level: {int(has_lev.sum()) * res * res / 1e6:.2f} km2 of water with a level; "
        f"{int((water_any & ~has_lev).sum()) * res * res / 1e4:.1f} ha of water without one")
    # the level that applies to a cell near water: its own, else the nearest levelled water's
    dlev, (li, lj) = ndimage.distance_transform_edt(~has_lev, sampling=res, return_indices=True)
    L_near = np.where(has_lev, lev, lev[li, lj]).astype(np.float32)
    del li, lj
    # ---- GLACIER
    glacier = glacier_mask(cls, res, cfg, prof, log, trusted_water=mp & ~mi) & obs
    # ---- SURE
    tidal_ids = [int(k) for k, b in btab.items() if b.get("kind") in TIDAL_KINDS]
    near_tidal = np.isin(bid, tidal_ids) if tidal_ids else np.zeros(z.shape, bool)
    bw = np.where(near_tidal, float(cfg["band_tidal_m"]), float(cfg["band_m"]))
    # the band reaches bw from the water's cells (its own) and from the nearest water for the dry side
    dwat = ndimage.distance_transform_edt(~water_any, sampling=res)
    bw_near = np.where(water_any, bw, _nearest(bw.astype(np.float32), water_any))
    dz = z - L_near
    sure_dz = float(cfg["sure_dz_m"])
    sure_land = (solid & ((dz >= sure_dz) | (dwat > bw_near))) | glacier
    sure_land |= solid & ~np.isfinite(L_near)
    in_body = bid > 0
    # unequivocal water: a measured bed; a water return at the level inside a body; and a SOLID return inside a body
    # standing below the level (ground seen through shallow water, the bed of a pond that was dry on another date):
    # the level is an inequality on the bed, so that observation is the bed, under water
    sw = float(cfg["sigma_w_m"])
    sure_water = (obs & bed_cls) | (obs & water_cls & in_body & (np.abs(dz) <= sw) & ~sure_land) | (obs & solid & in_body & (dz <= -sw))
    sure_water |= tstand & ~glacier
    # ...and a water RETURN inside a body at the body's level is unequivocal water too: the build keeps no water
    # return in its surface, so z is void there and `obs` is false -- the class and water_z say it (2026-10-25, run 20:
    # 6,890 such cells at Halibut Cove sat in the band and the cut gave them the shore's land)
    sure_water |= water_cls & np.isfinite(wzr) & in_body & np.isfinite(lev) & (np.abs(wzr - lev) <= sw) & ~sure_land
    # ...and in a RIVER's tidal reach the surveyed surface stands ABOVE its low-tide level (the tide held it up that
    # day): a water return in a river body at or above the level, within the tide's range, is the river (2026-10-25,
    # run 20b: the ramp lowered the level 1-5 m under the returns, the cells fell into the band and the cut made them
    # land, and the low-tide river vanished from shore_level)
    river_ids0 = [int(k) for k, b in btab.items() if b.get("kind") == "river"]
    if river_ids0:
        rcell = np.isin(bid, river_ids0)
        sure_water |= (water_cls & np.isfinite(wzr) & rcell & np.isfinite(lev) & (wzr >= lev - sw)
                       & (wzr <= float(cfg["tide_max_m"])) & ~sure_land)
    hole_water = ~obs & in_body & ~outside                      # a hole inside a body: water, no observation
    # the band is a ribbon either side of the water's edge: within bw of water, and within bw of the dry side
    dnw = ndimage.distance_transform_edt(water_any, sampling=res)
    band = ~sure_land & ~sure_water & ~outside & (dwat <= bw_near) & (dnw <= bw_near) & np.isfinite(L_near)
    # observed cells inside a body beyond the band that are neither sure water nor sure land -- a berg top out
    # on the lake, a boat -- are raised land for the enclosed-patch judgement below
    inside_raised = obs & in_body & ~band & ~sure_water & ~sure_land & ~outside
    hole_water &= ~band
    log(f"  sure land {int(sure_land.sum()) * res * res / 1e6:.2f} km2 (glacier {int(glacier.sum()) * res * res / 1e6:.2f}), "
        f"sure water {int(sure_water.sum()) * res * res / 1e6:.2f} km2, holes in bodies {int(hole_water.sum()) * res * res / 1e6:.2f} km2, "
        f"band {int(band.sum()) * res * res / 1e6:.2f} km2 ({int(band.sum())} cells), raised inside beyond it {int(inside_raised.sum()) * res * res / 1e4:.2f} ha")
    # ---- UNARY COSTS over the band
    cw = np.zeros(z.shape, np.float32); cl = np.zeros(z.shape, np.float32)
    cap = float(cfg["cost_cap"])
    bobs = band & obs
    # water: z at the level
    rw = (z - L_near) / float(cfg["sigma_w_m"])
    cw[bobs] = np.minimum(0.5 * rw[bobs] ** 2, cap)
    cw[bobs & bed_cls] = 0.0                                     # a measured bed is under water by definition
    # land: the surface extrapolated from the nearest sure land at its slope, never below the level
    S = slope_deg(z, res)
    fin_land = np.isfinite(S) & sure_land
    srim = _nmean(np.nan_to_num(S), fin_land, max(int(round(2 * float(cfg["inland_m"]) / res)) + 1, 3))
    srim = np.clip(np.nan_to_num(srim, nan=float(cfg["slope_min_deg"])), float(cfg["slope_min_deg"]), float(cfg["slope_max_deg"]))
    d_l, (ri, ci) = ndimage.distance_transform_edt(~sure_land, sampling=res, return_indices=True)
    ext = z[ri, ci] - np.tan(np.radians(srim[ri, ci])) * d_l
    sig_l = float(cfg["sigma_l_m"]) + float(cfg["sigma_l_per_m"]) * d_l
    # land near water is a BANK descending to it (the sure land continued at its slope) or a FLAT near its level (the
    # sure land's height continued level: a beach, a bar, a terrace); the cheaper explanation is the land hypothesis
    rl = (z - ext) / sig_l; rf = (z - z[ri, ci]) / sig_l
    cl[bobs] = np.minimum(np.minimum(0.5 * rl[bobs] ** 2, 0.5 * rf[bobs] ** 2), cap)
    below = bobs & (z < L_near - float(cfg["sigma_w_m"]))         # land is never under the water
    cl[below] = cap
    del ri, ci, rl, rf
    cl[bobs & bed_cls] = cap
    # hints: the class, weakly; the water stage's union for the unobserved inside a body
    cc = float(cfg["class_cost"])
    cl[bobs & water_cls] += cc; cw[bobs & solid] += cc
    cl[band & ~obs & in_body] += cc
    # outside every body no survey saw water: a claim of water there contradicts them all (body_cost)
    cw[band & ~in_body] += float(cfg["body_cost"])
    # the trusted mapped shoreline as a prior, fading over prior_edge_m either side of its line
    if mp.any():
        if True:
            de = ndimage.distance_transform_edt(mp, sampling=res); do = ndimage.distance_transform_edt(~mp, sampling=res)
            pe = float(cfg["prior_edge_m"]); pc = float(cfg["prior_cost"])
            cl[band & mp & ~mi] += pc * np.clip(de[band & mp & ~mi] / pe, 0, 1)
            cw[band & ~mp] += pc * np.clip(do[band & ~mp] / pe, 0, 1)
            cw[band & mi] += pc
            del de, do
    log(f"  costs: band cells observed {int(bobs.sum())}, unobserved {int((band & ~obs).sum())}; mapped prior from {n_tr} trusted polygons"
        f"{', ' + str(int(mi.sum()) * res * res / 1e4) + ' ha of islands' if mi.any() else ''}")
    # ---- THE CUT
    zb = np.where(obs, z, np.nan).astype(np.float32)
    cut_water, cst = _cut(cw, cl, band, zb, res, cfg, log)
    log(f"  cut: {cst['components']} band components, energy {cst['energy']:.0f}, {cst['seconds']} s; "
        f"band -> water {int(cut_water.sum()) * res * res / 1e4:.1f} ha, land {int((band & ~cut_water).sum()) * res * res / 1e4:.1f} ha")
    water = sure_water | hole_water | (band & cut_water)
    land = sure_land | (band & ~cut_water) | inside_raised
    # ---- FLOATING: raised land enclosed by water, by the body's kind.  Land is ANCHORED when its patch reaches
    # beyond the band on the dry side (farther than bw from any water) or touches the glacier; the rest is enclosed.
    label = np.zeros(z.shape, np.uint8)
    label[land] = LBL["land"]; label[water] = LBL["water"]; label[glacier] = LBL["glacier"]
    ll, ln = ndimage.label(land & ~glacier, structure=S8)
    anchored = np.zeros(ln + 1, bool)
    # anchored: a land component touching the glacier or the grid's unsurveyed outside, or larger than anything that
    # floats (berg_max_m2, the prior on a berg's size); every other component is a patch, judged below by what rings it
    anch_cells = (land & ~glacier) & ndimage.binary_dilation(glacier | outside, S8)
    anchored[np.unique(ll[anch_cells])] = True
    sz_l = np.bincount(ll.ravel(), minlength=ln + 1) * res * res
    anchored |= sz_l > float(cfg["berg_max_m2"])
    anchored[0] = True
    enclosed = ~anchored[ll] & (ll > 0)
    land_n = recipe_land_count(recipe, out, z.shape, prof)
    gl_near = ndimage.distance_transform_edt(~glacier, sampling=res) <= float(cfg["glacier_near_m"]) if glacier.any() else np.zeros(z.shape, bool)
    kinds = {int(k): b.get("kind") for k, b in btab.items()}
    icec = {int(k): bool(b.get("ice_contact")) for k, b in btab.items()}
    float_tab = []; n_isl = n_flt = n_spk = 0
    if enclosed.any():
        pl, pn = ndimage.label(enclosed, structure=S8)
        pidx = np.arange(1, pn + 1)
        sz = np.bincount(pl.ravel(), minlength=pn + 1)[1:] * res * res
        own = ndimage.grey_dilation(pl, size=3); ring = (own > 0) & (pl == 0)
        rb = bid[ring]; ro = own[ring]
        # the body around each patch: the commonest id on its ring
        body_of = np.zeros(pn + 1, np.int32)
        if rb.size:
            key = ro.astype(np.int64) * (int(bid.max()) + 1) + rb
            u, cnt = np.unique(key, return_counts=True)
            order = np.lexsort((-cnt, u // (int(bid.max()) + 1)))
            seen = set()
            for kk in order:
                p_, b_ = int(u[kk] // (int(bid.max()) + 1)), int(u[kk] % (int(bid.max()) + 1))
                if p_ not in seen and b_ > 0:
                    body_of[p_] = b_; seen.add(p_)
        corr = np.asarray(ndimage.sum(enclosed & (land_n >= 2), pl, pidx)) >= float(cfg["island_corr"]) * sz / (res * res)
        # enclosed BY WATER: the ring is water, or unobserved cells inside a body or a trusted polygon (water once the void
        # is resolved below); a patch ringed by void beyond every body -- a strip of returns in the prior's country -- is land
        wetish = (label == LBL["water"]) | ((label == 0) & (in_body | (mp & ~mi)))
        wfrac = np.asarray(ndimage.mean(wetish.astype(np.float32), np.where(ring, own, 0), pidx))
        mapped_isl = np.asarray(ndimage.maximum(ndimage.binary_dilation(mi, iterations=2), pl, pidx)) > 0 if mi.any() else np.zeros(pn, bool)
        near_gl = np.asarray(ndimage.maximum(gl_near, pl, pidx)) > 0
        icy = np.asarray(ndimage.mean(ice_cls.astype(np.float32), pl, pidx)) > 0.5
        built = np.asarray(ndimage.mean((cls == CLASSES.get("built", -1)).astype(np.float32), pl, pidx)) > 0.5
        waterc = np.asarray(ndimage.mean(water_cls.astype(np.float32), pl, pidx)) > 0.5
        dzm = np.asarray(ndimage.median(np.where(enclosed, np.nan_to_num(dz, nan=0.0), 0.0), pl, pidx))
        com = ndimage.center_of_mass(enclosed, pl, pidx)
        decision = np.full(pn, LBL["land"], np.uint8)
        for q in range(pn):
            b = int(body_of[q + 1]); kd = kinds.get(b)
            proglacial = bool(icec.get(b)) or bool(near_gl[q])
            if mapped_isl[q] or wfrac[q] < 0.5:
                decision[q] = LBL["land"]; n_isl += 1
            elif dzm[q] < sw:
                decision[q] = LBL["water"]; n_spk += 1          # AT the level, within the water's own noise: the surface, whoever saw
            elif corr[q]:                                       # it (two lidars read China Poot Lake's surface as ground 6 cm under its level)
                decision[q] = LBL["land"]; n_isl += 1           # beyond that noise two surveys' ground is ground: a tide flat 0.38 m over the
            elif dzm[q] < sure_dz:                              # day's tide is the dry flat (run 20f, 5 ha at 598227,6603813, sure_dz had
                decision[q] = LBL["water"]; n_spk += 1          # outranked the corroboration); one survey's patch near the level is the surface
            elif proglacial:
                decision[q] = LBL["floating"]; n_flt += 1       # a proglacial lake expects bergs, whatever their returns were classed
            elif waterc[q]:
                decision[q] = LBL["water"]; n_spk += 1          # water returns above the level: the surface at another stand
            elif kd in TIDAL_KINDS:
                decision[q] = LBL["floating"] if built[q] else LBL["land"]; n_flt += int(built[q]); n_isl += int(not built[q])
            elif sz[q] < float(cfg["island_min_m2"]) or waterc[q]:
                decision[q] = LBL["water"]; n_spk += 1
            else:
                decision[q] = LBL["land"]; n_isl += 1
            if decision[q] == LBL["floating"] or sz[q] >= 2000:
                x_, y_ = prof["transform"] * (float(com[q][1]) + 0.5, float(com[q][0]) + 0.5)
                float_tab.append(dict(x=round(x_), y=round(y_), area_m2=int(sz[q]), body=b, kind=kd,
                                      above_level_m=round(float(dzm[q]), 2), ice=bool(icy[q]), corroborated=bool(corr[q]),
                                      mapped_island=bool(mapped_isl[q]), proglacial=proglacial,
                                      label={v: k for k, v in LBL.items()}[int(decision[q])]))
        lut = np.r_[0, decision].astype(np.uint8)
        label[enclosed] = lut[pl[enclosed]]
        lev[enclosed & (label != LBL["land"])] = np.nan          # what floats, or is the surface, has the water's level around it
        log(f"  enclosed raised patches: {pn} -- islands {n_isl}, floating {n_flt}, specks to the surface {n_spk}")
    # ice not joined to the glacier: floating where bergs are expected (a proglacial body nearby or inside any body), land elsewhere
    loose = ice_cls & obs & ~glacier & (label != LBL["floating"])
    if loose.any():
        prog_b = [k for k in kinds if icec.get(k)]
        exp_ = gl_near | (np.isin(bid, prog_b) if prog_b else np.zeros(z.shape, bool))
        dexp = (ndimage.distance_transform_edt(~exp_, sampling=res) <= float(cfg["glacier_near_m"])) if exp_.any() else np.zeros(z.shape, bool)
        fl = loose & dexp
        label[fl] = LBL["floating"]
        label[loose & ~fl] = LBL["land"]
        log(f"  ice not joined to the glacier: {int(fl.sum()) * res * res / 1e4:.2f} ha floats, {int((loose & ~fl).sum()) * res * res / 1e4:.2f} ha is land")
    label[outside] = 0
    # unobserved cells the water encloses, and unobserved cells inside a trusted water polygon, are water: the void
    # between the bergs a survey saw as land (no body covers it), a prior's cells inside the lake (no observation)
    none = (label == 0) & ~outside
    if none.any():
        nl, nn = ndimage.label(none, structure=S8)
        touch = np.zeros(nn + 1, bool)
        touch[np.unique(nl[ndimage.binary_dilation(np.isin(label, (LBL["land"], LBL["glacier"])), S8) & none])] = True; touch[0] = True
        encl = none & ~touch[nl]
        inpoly = none & ((mp & ~mi) | stamp)
        add = encl | inpoly
        if add.any():
            label[add] = LBL["water"]
            log(f"  unobserved cells taken as water: {int(encl.sum()) * res * res / 1e4:.2f} ha enclosed by water, "
                f"{int((inpoly & ~encl).sum()) * res * res / 1e4:.2f} ha inside a trusted water polygon; {int((none & ~add).sum()) * res * res / 1e4:.1f} ha stay undecided (void)")
    # rule 5 for the UNOBSERVED (2026-10-25, the 2012 ice beside Grewingk's face): a prior's cell inside a trusted water
    # polygon (no island) costs nothing either way in the cut and takes its neighbours' label -- land, beside the sure
    # land of the ice face.  The map arbitrates where nothing was observed: it is water.  An observation can still
    # overrule the map (an island a survey saw stays an island).
    # ...and the same for a VOID cell (2026-10-26, Hig's review of run 20: the ice face sloped): the cut gave the void band
    # inside the retreat stamp -- the SfM's lake surface the stamp's ceiling had voided, 170 m of it -- the label of the
    # sure land beside it (the ice), cutting the strip at the face off from the lake's level, and the fill ramped a dry
    # domain from the bed up to the ice.  Nothing observed there: the map arbitrates, every unobserved cell is water.
    pin_ = ~obs & ~outside & ((mp & ~mi) | stamp) & (label == LBL["land"])
    if pin_.any():
        label[pin_] = LBL["water"]; lev[pin_] = np.nan
        log(f"  unobserved cells (a prior's or void) inside a trusted polygon or stamp labelled land are water: {int(pin_.sum()) * res * res / 1e4:.2f} ha")
    # ---- RAISED BODIES: a small flat body (the water stage's) whose ring is water standing sure_dz below its level is
    # no pond of its own -- a berg's flat top classed water, a deck -- and is judged like an enclosed raised patch
    wet = np.isin(label, (LBL["water"], LBL["floating"]))
    objs_b = ndimage.find_objects(bid)
    n_rb = 0
    for k, b in btab.items():
        k = int(k)
        if b.get("kind") not in FLAT_KINDS or b.get("consensus_level_m") is None or not (0 < k <= len(objs_b)) or objs_b[k - 1] is None:
            continue
        sl = objs_b[k - 1]
        area_k = int((bid[sl] == k).sum()) * res * res
        if area_k > float(cfg["berg_max_m2"]):
            continue
        sl = tuple(slice(max(s_.start - 3, 0), min(s_.stop + 3, n_)) for s_, n_ in zip(sl, bid.shape))
        cells = bid[sl] == k
        ring = ndimage.binary_dilation(cells, S8, iterations=2) & ~cells
        wr = ring & wet[sl] & np.isfinite(lev[sl])
        if ring.sum() == 0 or wr.sum() < 0.6 * ring.sum() or wr.sum() < 10:
            continue
        Lk = float(b["consensus_level_m"]); Lr = float(np.median(lev[sl][wr]))
        if Lk - Lr < sure_dz:
            continue
        rb = bid[sl][wr]; rb = rb[(rb > 0) & (rb != k)]
        bo = int(np.bincount(rb).argmax()) if rb.size else 0
        proglacial = bool(icec.get(bo)) or bool(gl_near[sl][cells].any())
        corr_k = float((land_n[sl][cells] >= 2).mean()) >= float(cfg["island_corr"])
        mapped_k = bool((ndimage.binary_dilation(mi[sl], iterations=2) & cells).any()) if mi.any() else False
        built_k = float((cls[sl][cells] == CLASSES.get("built", -1)).mean()) > 0.5
        if corr_k or mapped_k:
            dec = LBL["land"]
        elif proglacial:
            dec = LBL["floating"]
        elif kinds.get(bo) in TIDAL_KINDS:
            # a sheet of water returns standing above tidal water within the tide's range is the sea at another stand
            # (the lagoon's 2.5 m sheets ringed by its 1.0 m ones, 2026-10-25), at its own level; anything else
            # standing in the sea is a structure (built floats) or a bar
            # water evidence is a water return or a measured bed, whatever won the cell's class: over the multibeam the bed
            # wins it, and the lagoon's 2.5 m sheets over its -60 m bed were judged bars, bed and all (run 20f, 3,893 cells)
            wev = (cls[sl][cells] == CLASSES["water"]) | (cls[sl][cells] == CLASSES["bed"]) | np.isfinite(wzr[sl][cells])
            waterc_k = float(wev.mean()) > 0.5
            if waterc_k and Lk < float(cfg["tide_max_m"]):
                dec = -1
            elif waterc_k:
                # ...and above the tide's reach it is no stand at all: returns off something standing in the sea (a 0.7 ha
                # "lake" at 5.1 m over the -69 m multibeam bed, run 20f); the cells are the sea, at the level around them
                dec = LBL["water"]
            else:
                dec = LBL["floating"] if built_k else LBL["land"]
        else:
            dec = LBL["water"]
        if dec == -1:
            n_rb += 1; continue                                     # its own stand: label and level stay
        lab_ = label[sl]; lab_[cells] = dec; label[sl] = lab_
        if dec != LBL["land"]:
            lv_ = lev[sl]; lv_[cells] = np.nan; lev[sl] = lv_
        rr_, cc_ = np.nonzero(cells)
        x_, y_ = prof["transform"] * (float(cc_.mean()) + sl[1].start + 0.5, float(rr_.mean()) + sl[0].start + 0.5)
        float_tab.append(dict(x=round(x_), y=round(y_), area_m2=int(area_k), body=bo, kind=kinds.get(bo), raised_body=k,
                              above_level_m=round(Lk - Lr, 2), ice=False, corroborated=bool(corr_k), mapped_island=bool(mapped_k),
                              proglacial=proglacial, label={v: kk for kk, v in LBL.items()}[int(dec)]))
        n_rb += 1
    if n_rb:
        log(f"  raised bodies: {n_rb} small flat bodies standing above the water around them judged as raised patches")
    # A MEASURED BED BELOW THE WATER BESIDE IT IS UNDER WATER, whatever a later rule said (the level is an inequality on
    # the bed; enforced once here, after every rule that can relabel: run 20f's lagoon sheets took 3,223 multibeam cells
    # at -60 m to land).  Only BELOW the level: a sonar run at high tide over a flat a lidar saw dry at low tide is a bed
    # above the low-tide water, and the cut's land there stands.  The cell takes the level of the water it connects to, below.
    bed_land = obs & bed_cls & (label == LBL["land"]) & np.isfinite(L_near) & (z < L_near - sw)
    if bed_land.any():
        label[bed_land] = LBL["water"]; lev[bed_land] = np.nan     # the water around it gives the level, not a raised body's own
        log(f"  measured bed labelled land by a later rule, made water: {int(bed_land.sum()) * res * res / 1e4:.2f} ha")
    # ---- the level on every water cell (floating cells carry the water's level under them): a cell with no level of
    # its own takes the level of the levelled water it connects to THROUGH WATER, never the nearest across land
    wet = np.isin(label, (LBL["water"], LBL["floating"]))
    # FLOATING ICE TAKES THE LEVEL OF THE WATER IT FLOATS ON (2026-10-28, the flat-water rule; run 20h2 at
    # 59.60118,-151.06276): ice not joined to the glacier was made floating with the level its cells already carried --
    # an SfM pool's 50.87 and 51.33 m, 2 m over the 48.91 m lake around it.  Its own surface is the ice, never the water:
    # every floating cell's level is cleared here and routed from the water it sits in.
    flt_ = label == LBL["floating"]
    if (flt_ & np.isfinite(lev)).any():
        log(f"  floating cells: {int((flt_ & np.isfinite(lev)).sum())} carried a level of their own, cleared (they take the water's)")
        lev[flt_] = np.nan
    need = wet & ~np.isfinite(lev)
    # a TRUSTED MAPPED POLYGON arbitrates (rule 5): the water inside one lake polygon is one body at one level, so a cell
    # of it with no level takes the level its flat bodies agree on (poly_level_tol_m over poly_level_cells or more) --
    # not the nearest water's through the void: the void at Grewingk's ice face, inside the 2023 lake polygon, took a
    # marginal stream's 65 m over the lake's 48.91 (run 18)
    if need.any() and n_tr:
        flat_ids = [int(k) for k, b in btab.items() if b.get("kind") in FLAT_KINDS and b.get("consensus_level_m") is not None]
        fcell = np.isin(bid, flat_ids) & np.isfinite(lev) & wet & (mpid > 0) if flat_ids else np.zeros(z.shape, bool)
        if fcell.any():
            pid = np.arange(1, n_tr + 1)
            lab_f = np.where(fcell, mpid, 0)
            cnt_p = np.bincount(lab_f.ravel(), minlength=n_tr + 1)[1:]
            p10_p = ndimage.labeled_comprehension(lev, lab_f, pid, lambda v: np.percentile(v, 10), float, np.nan)
            p90_p = ndimage.labeled_comprehension(lev, lab_f, pid, lambda v: np.percentile(v, 90), float, np.nan)
            med_p = ndimage.labeled_comprehension(lev, lab_f, pid, np.median, float, np.nan)
            ok_p = (cnt_p >= int(cfg["poly_level_cells"])) & ((p90_p - p10_p) <= float(cfg["poly_level_tol_m"]))
            lut_p = np.r_[np.nan, np.where(ok_p, med_p, np.nan)].astype(np.float32)
            v_p = lut_p[np.where(need, mpid, 0)]
            take_p = need & np.isfinite(v_p)
            if take_p.any():
                lev[take_p] = v_p[take_p]; need = wet & ~np.isfinite(lev)
            log(f"  trusted polygons: {int(ok_p.sum())} hold one level; {int(take_p.sum()) * res * res / 1e4:.2f} ha of levelless water inside them take it")
    if clear_impossible_levels(lev, wet, cfg, log, "before routing"):  # a flat body's consensus inside a polygon, above
        need = wet & ~np.isfinite(lev)
    lev_out, nolev = _route_levels(wet, lev, need, res, float(cfg["route_m"]), log)
    if nolev.any():
        # a wet patch no level reaches (a mapped lake only the prior saw, a flat pool of void): its prior stands as the
        # level where it is flat (a hydroflattened lake), else it is no water at all
        nl_, nn_ = ndimage.label(nolev, structure=S8)
        idx_ = np.arange(1, nn_ + 1)
        zf_ = np.where(np.isfinite(z), z, np.nan)
        fin_ = ndimage.sum(np.isfinite(zf_), nl_, idx_) / np.maximum(np.bincount(nl_.ravel(), minlength=nn_ + 1)[1:], 1)
        p10_ = ndimage.labeled_comprehension(zf_, nl_, idx_, lambda v: np.nanpercentile(v, 10) if np.isfinite(v).any() else np.nan, float, np.nan)
        p90_ = ndimage.labeled_comprehension(zf_, nl_, idx_, lambda v: np.nanpercentile(v, 90) if np.isfinite(v).any() else np.nan, float, np.nan)
        md_ = ndimage.labeled_comprehension(zf_, nl_, idx_, lambda v: np.nanmedian(v) if np.isfinite(v).any() else np.nan, float, np.nan)
        flat_ = (fin_ >= 0.5) & ((p90_ - p10_) <= float(cfg["prior_flat_m"]))
        lo_r, _ = tide_range(cfg)
        lvl_ = np.where(flat_, md_, np.nan)
        if lo_r is not None:
            # a patch whose surface lies below the lowest stand of the tide is a bed under the SEA, not a level of its own
            # (run 20g: 10,136 such patches took their bed's median, to -5 m and below, in the bay): the sea at low tide
            sea_ = np.isfinite(md_) & (md_ < lo_r)
            lvl_ = np.where(sea_, float(cfg["low_tide_m"]), lvl_)
            flat_ = flat_ | sea_
        lut_ = np.r_[np.nan, lvl_].astype(np.float32)
        v_ = lut_[nl_]
        take_ = nolev & np.isfinite(v_)
        lev_out[take_] = v_[take_]
        drop_ = nolev & ~np.isfinite(v_)
        label[drop_] = LBL["none"]
        log(f"  wet patches no level reaches: {int(flat_.sum())} flat at their prior ({int(take_.sum()) * res * res / 1e4:.2f} ha), "
            f"{int((~flat_).sum()) * 1} not flat -> no water ({int(drop_.sum()) * res * res / 1e4:.2f} ha)")
    wet = np.isin(label, (LBL["water"], LBL["floating"]))
    # RULE 5, ENFORCED (2026-10-27, Hig's review of run 20g): water inside one trusted lake polygon that connects THROUGH
    # WATER to the polygon's own level is that lake, at that level -- not only the levelless cells above.  At Grewingk's
    # face pools of the 2023 SfM's surface over the lake (no water return; a "river" at 51.3, a pool at 50.9) kept levels
    # 2 m over the lake's 48.9 and the fill gave them near-flat beds.  A pond cut off by land inside the polygon keeps its own.
    if n_tr:
        flat_ids = [int(k) for k, b in btab.items() if b.get("kind") in FLAT_KINDS and b.get("consensus_level_m") is not None]
        fcell = np.isin(bid, flat_ids) & np.isfinite(lev_out) & wet & (mpid > 0) if flat_ids else np.zeros(z.shape, bool)
        n_set = 0
        if fcell.any():
            pid = np.arange(1, n_tr + 1)
            lab_f = np.where(fcell, mpid, 0)
            cnt_p = np.bincount(lab_f.ravel(), minlength=n_tr + 1)[1:]
            p10_p = ndimage.labeled_comprehension(lev_out, lab_f, pid, lambda v: np.percentile(v, 10), float, np.nan)
            p90_p = ndimage.labeled_comprehension(lev_out, lab_f, pid, lambda v: np.percentile(v, 90), float, np.nan)
            med_p = ndimage.labeled_comprehension(lev_out, lab_f, pid, np.median, float, np.nan)
            ok_p = (cnt_p >= int(cfg["poly_level_cells"])) & ((p90_p - p10_p) <= float(cfg["poly_level_tol_m"]))
            lut_p = np.r_[np.nan, np.where(ok_p, med_p, np.nan)].astype(np.float32)
            Lp = lut_p[mpid]                                     # the polygon's level on its cells
            # ...and the WATER STAMPS are mapped lake too (make_ice's 2021 lake and 2023 retreat): the retreat is newer than
            # the polygons, and the face's pools lay in it, outside polygon and joined to it through water (smoke 20h try 2)
            inp = wet & (np.isfinite(Lp) | stamp)
            wc, _ = ndimage.label(inp, structure=S8)             # mapped water, joined through water
            tol_ = float(cfg["poly_level_tol_m"])
            at = inp & np.isfinite(Lp) & np.isfinite(lev_out) & (np.abs(lev_out - Lp) <= tol_)
            nc_ = int(wc.max()) + 1
            # a component's level: the median polygon level of its cells at that level (one lake per component)
            Lc = np.full(nc_, np.nan, np.float32)
            if at.any():
                Lc[1:] = ndimage.labeled_comprehension(Lp, np.where(at, wc, 0), np.arange(1, nc_), np.median, float, np.nan)
            Lw = Lc[wc]
            setc = inp & np.isfinite(Lw) & ~(np.isfinite(lev_out) & (np.abs(lev_out - Lw) <= tol_))
            lev_out[setc] = Lw[setc]; n_set = int(setc.sum())
        log(f"  trusted polygons, enforced: {n_set * res * res / 1e4:.2f} ha of water joined to a polygon's level take it")
    lev_out = np.where(wet, lev_out, np.nan).astype(np.float32)
    # FLOATING ICE TAKES ITS HOST WATER'S LEVEL, after routing (run 20i): clearing the floating cells' own levels above was
    # not enough -- routing then gave body-less ice patches the nearest level through water, a 50.87 m SfM pool cell
    # touching them, over the 48.91 m lake that surrounds them.  The host is the water ring around each patch: its median.
    flt_ = label == LBL["floating"]
    if flt_.any():
        Lf, nf = ndimage.label(flt_, structure=S8)
        own = ndimage.grey_dilation(Lf, size=3)
        ring = (own > 0) & (Lf == 0) & (label == LBL["water"]) & np.isfinite(lev_out)
        hm = np.full(nf + 1, np.nan, np.float32)
        if ring.any():
            hm[1:] = ndimage.labeled_comprehension(lev_out, np.where(ring, own, 0), np.arange(1, nf + 1), np.median, float, np.nan)
        Hf = hm[Lf]
        mv = flt_ & np.isfinite(Hf) & ~(np.isfinite(lev_out) & (np.abs(lev_out - Hf) <= 0.05))
        lev_out[mv] = Hf[mv]
        log(f"  floating ice at its host water's level: {nf} patches, {int(mv.sum())} cells moved")
    # ---- BANK SLOPE at the shoreline (land cells touching water), the slope of the land behind it
    shore_cells = np.isin(label, (LBL["land"], LBL["glacier"])) & ndimage.binary_dilation(wet, S8)
    fin_l = np.isfinite(S) & np.isin(label, (LBL["land"], LBL["glacier"]))
    sl_ = _nmean(np.nan_to_num(S), fin_l, max(int(round(2 * float(cfg["inland_m"]) / res)) + 1, 3))
    slope = np.where(shore_cells, sl_, np.nan).astype(np.float32)
    # ---- write rasters
    base = dict(prof, driver="GTiff", tiled=True, blockxsize=512, blockysize=512, compress="zstd", bigtiff="yes")
    def w(name, arr, dtype, nodata, pred=None):
        pr = dict(base, dtype=dtype, nodata=nodata)
        if pred: pr["predictor"] = pred
        with rasterio.open(out / name, "w", **pr) as d:
            d.write(np.where(np.isfinite(arr), arr, nodata).astype(dtype) if dtype == "float32" else arr.astype(dtype), 1)
    w("shore_label.tif", label, "uint8", 255)
    # the water-blind cells (no observation inside the water): the build's `blind_water` step voids the layer there
    # (2026-10-25: build 3 wrote the SfM's noisy surface back over Emerald Lake's modelled bed -- the stage and the
    # fill had dropped it, the build had not)
    w("water_blind.tif", (blind_in & np.isin(label, (LBL["water"], LBL["floating"]))).astype(np.uint8), "uint8", 255)
    w("shore_level.tif", lev_out, "float32", -9999.0, 3)
    w("shore_slope.tif", slope, "float32", -9999.0, 3)
    conf = np.where(band, np.minimum(1.0, np.abs(cw - cl) / 4.0), 1.0).astype(np.float32)
    w("shore_band.tif", np.where(band, np.where(cut_water, 2, 1), 0).astype(np.uint8), "uint8", 0)
    if cfg.get("debug"):
        w("shore_cost_w.tif", np.where(band, cw, np.nan), "float32", -9999.0, 3); w("shore_cost_l.tif", np.where(band, cl, np.nan), "float32", -9999.0, 3)
        w("shore_ext.tif", np.where(band, ext, np.nan).astype(np.float32), "float32", -9999.0, 3); w("shore_dland.tif", np.where(band, d_l, np.nan).astype(np.float32), "float32", -9999.0, 3)
    # ---- the shoreline as lines
    lines = vectorise(label, lev_out, slope, conf, bid, kinds, prof, res, cfg, log)
    (out / "shoreline.geojson").write_text(json.dumps(dict(type="FeatureCollection", name="shoreline", features=lines)))
    a = res * res / 1e6
    rep = dict(composite=recipe.id, built=_dt.datetime.now().isoformat(timespec="seconds"), res_m=res, params={k: v for k, v in cfg.items()},
               km2={k: round(float((label == v).sum()) * a, 4) for k, v in LBL.items() if v},
               band_km2=round(float(band.sum()) * a, 4), band_cells=int(band.sum()), cut=cst,
               sure_land_km2=round(float(sure_land.sum()) * a, 4), sure_water_km2=round(float(sure_water.sum()) * a, 4),
               enclosed=dict(islands=n_isl, floating=n_flt, specks=n_spk), floating=sorted(float_tab, key=lambda t: -t["area_m2"])[:200],
               rivers=river_info, shoreline_lines=len(lines), seconds=round(time.time() - t0, 1))
    (out / "shore.json").write_text(json.dumps(rep, indent=1))
    # the corrections to the water stage's bodies, read by load_bodies for every later consumer (fill, build 3, the water
    # surface, the flags): written every run, so a stale one never outlives the stage that wrote it
    (out / "shore_bodies.json").write_text(json.dumps(dict(built=rep["built"], overrides={str(k): v for k, v in overrides.items()},
                                                           sills={str(k): v for k, v in sills.items()}), indent=1))
    log(f"  labels: " + ", ".join(f"{k} {v} km2" for k, v in rep["km2"].items()) + f"; {len(lines)} shoreline rings; {rep['seconds']} s")
    return rep


def recipe_land_count(recipe, out, shape, prof):
    """How many survey layers saw LAND at each cell (ground, built, canopy, snow), a pooled layer counting
    its own surveys through land_support (joint_nland).  Shared with composite.cmd_fill."""
    import os
    land_codes = [CLASSES[k] for k in LAND_CODES if k in CLASSES]
    land_n = np.zeros(shape, np.uint8)
    for L in recipe.layers:
        cp = Path(out) / f"class_{L['name']}.tif"
        if L.get("optional") or not cp.exists():
            continue
        with rasterio.open(cp) as ds:
            c_ = ds.read(1)
        if c_.shape != shape:
            continue
        lnd = np.isin(c_, land_codes)
        sup = L.get("land_support")
        if sup and os.path.exists(str(sup)):
            from rasterio.warp import reproject, Resampling
            s_ = np.zeros(shape, np.float32)
            with rasterio.open(sup) as ds:
                reproject(rasterio.band(ds, 1), s_, dst_transform=prof["transform"], dst_crs=prof["crs"],
                          resampling=Resampling.max, dst_nodata=0)
            land_n += np.where(lnd, np.clip(np.maximum(np.rint(s_), 1), 0, 255), 0).astype(np.uint8)
        else:
            land_n += lnd.astype(np.uint8)
    return land_n


def vectorise(label, lev, slope, conf, bid, kinds, prof, res, P, log):
    """One feature per ring of the water outline (floating things are inside the water): body, kind, level,
    slope statistics, confidence, the bank slope at every vertex."""
    from shapely.geometry import shape as _shape, mapping
    wet = np.isin(label, (LBL["water"], LBL["floating"]))
    if not wet.any():
        return []
    wl, wn = ndimage.label(wet, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool))
    # nearest shoreline cell's slope / the water's level for sampling along the rings
    sh = np.isfinite(slope)
    slope_n = _nearest(slope, sh) if sh.any() else slope
    conf_n = conf
    T = prof["transform"]; inv = ~T
    feats = []
    min_len = float(P["min_ring_m"])
    for geom, val in features.shapes(wl.astype(np.int32), mask=wl > 0, connectivity=4, transform=T):
        k = int(val)
        g = _shape(geom).simplify(res * 0.7, preserve_topology=True)
        if g.is_empty:
            continue
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        m = wl == k
        ids = bid[m]; ids = ids[ids > 0]
        body = int(np.bincount(ids).argmax()) if ids.size else 0
        lv = lev[m]; lv = lv[np.isfinite(lv)]
        for pg in polys:
            rings = [(pg.exterior, "outer")] + [(r, "inner") for r in pg.interiors]
            for ring, role in rings:
                if ring.length < min_len:
                    continue
                xy = np.asarray(ring.coords)
                cc, rr = inv * (xy[:, 0], xy[:, 1])
                rr = np.clip(np.rint(rr - 0.5).astype(int), 0, label.shape[0] - 1); cc = np.clip(np.rint(cc - 0.5).astype(int), 0, label.shape[1] - 1)
                sv = slope_n[rr, cc]; cv = conf_n[rr, cc]
                props = dict(body=body, kind=kinds.get(body), role=role, length_m=round(float(ring.length), 1), n=int(len(xy)),
                             level_m=round(float(np.median(lv)), 2) if lv.size else None,
                             level_min_m=round(float(lv.min()), 2) if lv.size else None, level_max_m=round(float(lv.max()), 2) if lv.size else None,
                             slope_p10=round(float(np.nanpercentile(sv, 10)), 1) if np.isfinite(sv).any() else None,
                             slope_p50=round(float(np.nanpercentile(sv, 50)), 1) if np.isfinite(sv).any() else None,
                             slope_p90=round(float(np.nanpercentile(sv, 90)), 1) if np.isfinite(sv).any() else None,
                             confidence=round(float(np.nanmean(cv)), 2) if np.isfinite(cv).any() else None,
                             slope_deg=[None if not np.isfinite(v) else round(float(v), 1) for v in sv])
                feats.append(dict(type="Feature", geometry=transform_geom(prof["crs"], "EPSG:4326", mapping(ring)), properties=props))
    return feats
