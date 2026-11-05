"""The mapped high-tide line is authoritative over the DEMs (2026-11-04, Hig: the shore from 3DEP "really should be trashed, and the one from my mapping should become authoritative").
A last step on the finished surface, where a vector_high line exists (tide_vectors.py) and no reviewer's line stands nearer:
  on the line       the surface is the line's level;
  on the water side the surface stands no higher than the line (tide_sea.geojson: the mapped water beside a line), sloping down to the fit beyond the DEM's false shore; a bed or a fit further out stays;
  on the land side  a convex ramp from the line up to the DEM at a width that follows the DEM's slope over the first 100 m inland, joining the DEM at its own slope;
  all the way out    nothing modelled stands as a peak in the high water unless a line, a cast or a rock holds it (no_peaks).
Lidar is never touched (Hig: tide lines "shouldn't overrule lidar or multibeam"), and a multibeam bed is under the level anyway.
The width, measured on 340 km of coast under lidar (segments of 50 m, KBay 2023, Chugachmiut, Seldovia, Homer; BAY_SOURCES 8.6): 3DEP's profile sits about 10 m off whatever the slope (slope does not predict the offset:
r = -0.11), so the height error grows with slope and so does the ramp worth having.  SLOPE_WIDTH beats a fixed 50 m on gentle coast (mean error, first 50 m: 2.2 vs 2.4 m under 15 degrees) and equals it on steep coast (5.4 m over 40; 3DEP as it is, 10.9)."""
import json
from pathlib import Path
import numpy as np, rasterio
from rasterio import features
from scipy import ndimage

SLOPE_WIDTH = ((25.0, 40.0), (20.0, 50.0))     # degrees of the DEM's slope over SLOPE_SPAN_M -> ramp width in m
SLOPE_SPAN_M, ALONG_SIGMA_M, RAMP_SIGMA_M = 100.0, 25.0, 4.0
SEA_REACH_M = 200.0                            # tide_vectors.SEA_NEAR_M: how far tide_sea.geojson reaches from a line
FIELD_SIGMA_M, TOP_SPAN_M = 8.0, 20.0
EDT = ndimage.distance_transform_edt


def apply(z, T, res, lines, sea, lidar, log=print):
    """(new surface, code raster 1 capped 2 ramp 3 on the line, report).  lines: GeoJSON features with level_m and source; sea: geometries; lidar: bool grid."""
    shape = z.shape; H_ = []; R_ = []; Hs_ = []; L_ = []
    for f in lines:
        q = f.get("properties") or {}
        if q.get("level_m") is not None and q.get("source") == "vector_high":
            H_.append((f["geometry"], float(q["level_m"]))); Hs_.append((f["geometry"], float(q.get("level_sigma_m", 0.1))))
        elif q.get("level_m") is not None and q.get("source") == "vector_low":
            L_.append((f["geometry"], float(q["level_m"])))
        elif q.get("level_m") is not None:
            R_.append((f["geometry"], 1))
    Hl = features.rasterize(H_, out_shape=shape, transform=T, fill=np.nan, dtype="float32") if H_ else np.full(shape, np.nan, np.float32)
    H = np.isfinite(Hl); code = np.zeros(shape, np.uint8)
    Ll = features.rasterize(L_, out_shape=shape, transform=T, fill=np.nan, dtype="float32") if L_ else np.full(shape, np.nan, np.float32); Ll[H] = np.nan
    if not H.any():
        return z, code, dict(line_km=0.0)
    water = features.rasterize([(g, 1) for g in sea], out_shape=shape, transform=T, fill=0, dtype="uint8") > 0 if sea else np.zeros(shape, bool)
    dL, (ii, jj) = EDT(~H, sampling=res, return_indices=True); dL = dL.astype(np.float32)
    # level_sigma_m is the line's uncertainty, for the reader (shore3d's tube): it is no licence for a DEM standing above the line, which is the error this step removes.  The fit itself sits on the level
    Hc = Hl; lv = Hc[ii, jj]; top = lv
    mine = dL <= SEA_REACH_M + SLOPE_SPAN_M
    if R_:
        Rr = features.rasterize(R_, out_shape=shape, transform=T, fill=0, dtype="uint8") > 0
        mine &= EDT(~Rr, sampling=res).astype(np.float32) > dL          # nearer a reviewer's line: theirs (the polygon there is out of date)
    land = ~water; have = np.isfinite(z)
    # the DEM's slope, per line cell, over the first SLOPE_SPAN_M inland; smoothed along the coast
    ring = land & have & (dL >= SLOPE_SPAN_M - 10.0) & (dL < SLOPE_SPAN_M + 10.0)
    def along(val, m):           # a value on the line's cells from the cells whose nearest line cell it is, smoothed along the coast
        sz = np.zeros(shape, np.float32); cn = np.zeros(shape, np.float32); np.add.at(sz, (ii[m], jj[m]), val[m]); np.add.at(cn, (ii[m], jj[m]), 1.0)
        g = H & (cn > 0); v = np.zeros(shape, np.float32); v[g] = sz[g] / cn[g]; sg = ALONG_SIGMA_M / res
        return ndimage.gaussian_filter(v, sg) / np.maximum(ndimage.gaussian_filter(g.astype(np.float32), sg), 1e-6)
    W = np.interp(np.degrees(np.arctan(np.maximum((along(z, ring) - np.nan_to_num(Hl)) / SLOPE_SPAN_M, 0.0))), *SLOPE_WIDTH).astype(np.float32); del ring
    Wc = ndimage.gaussian_filter(W[ii, jj], max(FIELD_SIGMA_M / res, 1.0))      # the nearest line cell jumps at a headland's medial axis: smoothed, or the jump shows as a crease
    # THE LAND SIDE.  A sea cliff is convex along a dropline (2026-11-04, Hig: steep at the shore where erosion is happening, less steep above; lidar agrees: median 32 degrees in the first 10 m, 12 at 40-60 m on 15-25 degree coast, 39 and 21 at 25-35, about straight past 45).
    # u is a harmonic coordinate, 0 on the line and 1 at the band's far edge, and S the harmonic surface between the line's level and the DEM, so neither has rays from a nearest line cell.  S rises about linearly with u, and the profile is reshaped to the quadratic that keeps
    # both ends and leaves the top at the DEM's own slope m (as a fraction of the ramp's mean; capped at 1, a straight ramp, where the DEM steepens above it): lv + (S - lv)((2 - m) + (m - 1) u).  No step and no kink where it meets the DEM
    from .fill import harmonic
    free = mine & ~lidar                                       # it stops at lidar or a reviewer's line, which it does not touch, and meets them through the same boundary
    band = land & free & have & ~H & (dL < Wc)
    sg0 = max(FIELD_SIGMA_M / res, 1.0)
    zt_ = along(z, land & have & ~H & (np.abs(dL - Wc) <= 2.0 * res)); zu_ = along(z, land & have & ~H & (np.abs(dL - Wc - TOP_SPAN_M) <= 2.0 * res))
    m_ = np.where(H, np.clip((zu_ - zt_) / TOP_SPAN_M / np.maximum((zt_ - np.nan_to_num(Hl)) / np.maximum(W, 1.0), 1e-3), 0.0, 1.0), 0.0).astype(np.float32); Mc = ndimage.gaussian_filter(m_[ii, jj], 2.0 * sg0)
    fv = np.where(H, Hc, z).astype(np.float64); fx = np.isfinite(fv) & ~band
    S_ = harmonic(fv, fx, band, coarse=16, iters=300); u_ = harmonic(np.where(H, 0.0, 1.0), fx, band, coarse=16, iters=300)
    S_ = np.where(np.isfinite(S_), S_, z); u_ = np.clip(np.nan_to_num(u_, nan=1.0), 0.0, 1.0); lv_s = ndimage.gaussian_filter(lv, sg0)
    out = z.copy(); new_ = lv_s + (S_ - lv_s) * np.where(S_ > lv_s, (2.0 - Mc) + (Mc - 1.0) * u_, 1.0)
    out[band] = new_[band]; code[band] = 2; del m_, Mc, fv, fx, S_, u_, new_, zt_, zu_
    # the water side: contour_sea builds the water from the mapped lines, so all that can stand above the line here is an observation the DEM clip left (it was a slope to the fit here until the fit stopped needing one, 2026-11-04)
    cap = water & mine & have & ~lidar & (dL <= SEA_REACH_M) & (z > top)
    out[cap] = lv[cap]; code[cap] = 1
    on = H & have & ~lidar & mine
    out[on] = Hc[on]; code[on] = 3
    used = W[H]
    rep = dict(line_km=round(float(H.sum()) * res / 1e3, 1), width_m_p10_50_90=[round(float(v)) for v in np.percentile(used, [10, 50, 90])], capped_ha=round(float((code == 1).sum()) * res * res / 1e4, 1),
               ramp_ha=round(float(band.sum()) * res * res / 1e4, 1), line_under_lidar_km=round(float((H & lidar).sum()) * res / 1e3, 2),
               capped_mean_drop_m=round(float((z - out)[code == 1].mean()), 2) if (code == 1).any() else 0.0, ramp_mean_change_m=round(float((out - z)[band].mean()), 2) if band.any() else 0.0)
    log(f"  high line: {rep}")
    return out, code, rep


def no_peaks(z, water, modelled, anchors, res, log=print):
    """Downhill to the sea (2026-11-04, Hig: "it should be all downhill"): a modelled cell in the high water stands no higher, and lies no lower, than the path to an anchor allows: a line, a cast, a rock, an observation or the grid's edge.  Morphological reconstruction both
    ways, so only the peaks and the pits nobody measured go.  Returns (surface, cells moved)."""
    from skimage.morphology import reconstruction
    dom = water & modelled & ~anchors & np.isfinite(z); dom[[0, -1], :] = False; dom[:, [0, -1]] = False
    if not dom.any():
        return z, np.zeros(z.shape, bool)
    r, c = np.nonzero(ndimage.binary_dilation(dom, iterations=2)); r0, r1, c0, c1 = r.min(), r.max() + 1, c.min(), c.max() + 1
    a = np.where(np.isfinite(z[r0:r1, c0:c1]), z[r0:r1, c0:c1], np.nanmin(z)).astype(np.float64); d = dom[r0:r1, c0:c1]; S4 = ndimage.generate_binary_structure(2, 1)
    rec = reconstruction(np.where(d, a.min(), a), a, method="dilation", footprint=S4); pk = d & (rec < a - 1e-3); log(f"  high line: peaks: {pk.sum() * res * res / 1e4:.2f} ha, mean {float((a - rec)[pk].mean()) if pk.any() else 0.0:.2f} m, max {float((a - rec)[pk].max()) if pk.any() else 0.0:.2f} m")
    rc2 = reconstruction(np.where(d, rec.max(), rec), rec, method="erosion", footprint=S4); pt = d & (rc2 > rec + 1e-3); log(f"  high line: pits: {pt.sum() * res * res / 1e4:.2f} ha, mean {float((rc2 - rec)[pt].mean()) if pt.any() else 0.0:.2f} m, max {float((rc2 - rec)[pt].max()) if pt.any() else 0.0:.2f} m")
    mv = pk | pt; out = z.copy(); out[r0:r1, c0:c1][mv] = rc2[mv].astype(np.float32); m = np.zeros(z.shape, bool); m[r0:r1, c0:c1] = mv
    return out, m


def lidar_on_grid(T, crs, shape):
    from .tide_vectors import LIDAR
    from .fill import _read_on_grid
    m = np.zeros(shape, bool)
    for p in LIDAR:
        if Path(p).exists():
            m |= np.isfinite(_read_on_grid(p, T, crs, shape, (0, shape[0]), (0, shape[1]), nearest=True))
    return m
