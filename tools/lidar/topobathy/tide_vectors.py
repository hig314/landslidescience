#!/opt/anaconda3/bin/python3
"""Hig's hand-mapped tide shorelines (KBSP map) as evidence for the shore model, and the reviewer's edited contours merged with them (2026-11-04).

    tide_vectors.py build                 water/tide_vector_lines.geojson + water/tide_sea.geojson, from the Oct 2024 low- and high-tide polygons
    tide_vectors.py closures              water/tide_low_closures.geojson alone, from the low water as written: its outline's closing edges (build writes it too)
    tide_vectors.py clip SRC.tif DST.tif  a derived ground grid (ArcticDEM) voided inside tide_sea.geojson, windowed
    tide_vectors.py merge REVISED ORIG    water/tide_lines.geojson: the reviewer's changed lines + the vector lines they do not cover; a report beside it

The polygons are overlapping pieces of WATER at the two tides (Shore, measured 95% on 3DEP's sea; Pool at low tide) with Island cut out of them: that water's outline is the waterline.  Hig, 2026-11-04: high and low are not precise
levels (a mix of datasets, smaller tides outside the peninsula), so a line is a contour that means something locally and is not assumed to hold one level far along the coast; and it
does not overrule lidar or multibeam, so it stops where those begin.  Its level is therefore read from the lidar: the median height of the same line under lidar within LEVEL_R_M.
A polygon's edge is only a waterline near the sea: pieces further than SHORE_NEAR_M from 3DEP's sea edge (a piece's cut line inland or across open water) are dropped.
tide_sea.geojson is the high-tide water within SEA_NEAR_M of a kept line: there 3DEP's radar smear and ArcticDEM's garbled water edge are no observation of land (the blurred cliffs)."""
import argparse, json, os
from pathlib import Path
import fiona, numpy as np, rasterio, shapely
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform as tf, transform_geom
from rasterio.windows import Window, bounds as win_bounds
from rasterio import features
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import LineString, mapping, shape
import shapely.ops
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent.parent / "water"
SRC = Path("/Users/Hig/Documents/Outreach/KBSP_Map/New Shapefiles")
LOW, HIGH = SRC / "Low_Tide_Improved/Low_tide_polygons.shp", SRC / "High Tide/High_tide_polygons.shp"
SEA10 = "/Volumes/Nunatak/lidar_build/kbay_joint/obs/water_3dep_bay10.tif"
LIDAR = ["/Volumes/Nunatak/lidar_build/cog/kbay_2023_reclass_v2.tif", "/Volumes/Nunatak/lidar_build/kbay_joint/obs/chugachmiut_v2_clean.tif",
         "/Volumes/Nunatak/lidar_build/cog/seldovia_2019.tif", "/Volumes/Nunatak/lidar_build/cog/homer_2019.tif"]
EDIT_TOL_M, MIN_EDIT_M = 2.0, 10.0      # a revised line within EDIT_TOL_M of the original at its level is not an edit; shorter moved stretches are noise
LONG_EDGE_M = 150.0     # Hig 2026-11-04: an outer edge closing a polygon across open water is a long straight segment; the drawn shores are 6-8 m (p99 53-82 m)
STEP_M, SHORE_NEAR_M, LIDAR_KEEP_M, LEVEL_R_M, LEVEL_MIN_N, CHUNK_M, MIN_PIECE_M, RING_MIN_M2, SEA_NEAR_M = 25.0, 1000.0, 30.0, 15000.0, 200, 500.0, 40.0, 200.0, 200.0
LEVEL_NEAR_M = 300.0
LOW_OFF_HIGH_M, HIGH_UNMAPPED_M = 1.0, 100.0
ZONE_TOL_M, LEVEL_SAME_M, ZONE_STEP_M = 2.0, 0.5, 5.0
CLAMP = dict(low=(-2.4, -0.6), high=(3.0, 5.0))     # the lidar reads a low line high (a level above the flat stands up as a ridge along the line: Taylor, Port Chatham)
# THE LOW LINE IS NOT ONE LEVEL (2026-11-04, Hig: "it runs uphill where there's a river entering ... more like the edge of where it's always wet"; and measure it "where the lidar actually extends to low tide").  Under lidar, keeping only the points with a real bed slope
# through the line (LOW_REAL_DROP_M each way; most of the rest read a flattened or flight-time water surface, up to 4 m): next to wide water (WIDE_WATER_M) the line stands at -1.7 m, NMAD 0.26 (491 points, the whole bay); up a channel it rises with the distance along the water
# from that wide water, by RISE_TABLE: the medians of 300 such points (fitted on all; east and west of the bay are printed by build)
WIDE_WATER_M, OPEN_SLACK_M, LOW_REAL_DROP_M, LOW_SLOPE_MAX_DEG, LOW_MIN_N, CHANNEL_SIGMA_M = 50.0, 30.0, 0.15, 5.0, 30, 0.7
LOW_OFF_NMAD = 2.0
RISE_TABLE = ((30.0, 65.0, 175.0, 375.0, 750.0, 1500.0), (0.0, 0.8, 2.4, 2.5, 3.1, 4.1))
PLAUSIBLE = dict(low=(-3.5, 2.5), high=(1.5, 7.0))     # a point further off is the line beside a cliff, not the line's level


def _polys(path):
    G, ty = [], []
    with fiona.open(path) as f:
        src = CRS.from_wkt(f.crs_wkt) if f.crs_wkt else CRS.from_epsg(4326)
        for r in f:
            if r["geometry"] is not None:
                G.append(shapely.make_valid(shape(transform_geom(src, "EPSG:6334", dict(r["geometry"]))))); ty.append(r["properties"].get("Type"))
    return G, ty


def _water_edge_dist():
    with rasterio.open(SEA10) as s:
        a = s.read(1) == 2; T = s.transform
    edge = a ^ ndimage.binary_erosion(a, iterations=1)
    return ndimage.distance_transform_edt(~edge, sampling=10.0).astype(np.float32), T


def _lidar_near(T, shape_):
    m = np.zeros(shape_, bool)
    for p in LIDAR:
        if not Path(p).exists():
            print("  no", p); continue
        with rasterio.open(p) as s, WarpedVRT(s, crs="EPSG:6334", transform=T, width=shape_[1], height=shape_[0], resampling=Resampling.nearest) as v:
            m |= ~np.ma.getmaskarray(v.read(1, masked=True))
    return ndimage.distance_transform_edt(~m, sampling=10.0).astype(np.float32)


def _sample_lidar(x, y):
    v = np.full(x.size, np.nan)
    for p in LIDAR:
        if not Path(p).exists():
            continue
        with rasterio.open(p) as s:
            xs, ys = (np.array(q) for q in tf("EPSG:6334", s.crs, x.tolist(), y.tolist()))
            b = s.bounds; idx = np.flatnonzero(np.isnan(v) & (xs > b.left) & (xs < b.right) & (ys > b.bottom) & (ys < b.top))
            if idx.size:
                w = np.array([a[0] for a in s.sample(zip(xs[idx], ys[idx]))], float)
                nd = s.nodata if s.nodata is not None else -9999
                w[(w == nd) | (w < -9000)] = np.nan
                v[idx] = w
    return v


def _rings(water):
    out = []
    for part in getattr(water, "geoms", [water]):
        if part.geom_type != "Polygon":
            continue
        out.append((part.exterior, part.area, "water"))
        out += [(r, shapely.Polygon(r).area, "island") for r in part.interiors]
    return out


DROPPED = []


def _lines(water, kind, dist, Td, lnear, inside=None):
    """Kept pieces of the union's outline: [(xy array, closed, enclosed m2, side)], with the points' lidar heights for the level."""
    Ti = ~Td; cal_x, cal_y, cal_z = [], [], []; pieces = []; n_all = n_far = n_lid = n_cross = n_long = 0
    for ring, area, side in _rings(water):
        if area < RING_MIN_M2 or ring.length < MIN_PIECE_M:
            continue
        n = max(int(ring.length / STEP_M), 4); d = np.linspace(0, ring.length, n, endpoint=False)
        P = shapely.get_coordinates(shapely.line_interpolate_point(ring, d))
        c, r = Ti * (P[:, 0], P[:, 1]); r = np.clip(r.astype(int), 0, dist.shape[0] - 1); c = np.clip(c.astype(int), 0, dist.shape[1] - 1)
        near = dist[r, c] <= SHORE_NEAR_M; lid = lnear[r, c] <= LIDAR_KEEP_M; n_all += n; n_far += int((~near).sum())
        h = _sample_lidar(P[:, 0], P[:, 1]) if (near & lid).any() else np.full(n, np.nan)
        q = np.isfinite(h) if kind == "low" else np.isfinite(h) & (dist[r, c] <= LEVEL_NEAR_M) & (h >= PLAUSIBLE[kind][0]) & (h <= PLAUSIBLE[kind][1])      # low: _low_calibration chooses.  High: the level comes only from the outline close to the sea (SHORE_NEAR_M wider moved Taylor's by a metre)
        cal_x += P[q, 0].tolist(); cal_y += P[q, 1].tolist(); cal_z += h[q].tolist()
        keep = near & ~lid; n_lid += int((near & lid).sum())
        if inside is not None:                     # a low line on the land at high tide has crossed the high line: it cannot be right there
            ins = shapely.contains_xy(inside, P[:, 0], P[:, 1]); n_cross += int((keep & ~ins).sum()); keep &= ins
        V = shapely.get_coordinates(ring); sl = np.hypot(*np.diff(V, axis=0).T); si = np.clip(np.searchsorted(np.r_[0.0, np.cumsum(sl)], d, side="right") - 1, 0, sl.size - 1)
        lng = sl[si] > LONG_EDGE_M; n_long += int((keep & lng).sum())
        for i_ in np.unique(si[keep & lng]):
            DROPPED.append(dict(type="Feature", properties=dict(kind=kind, length_m=round(float(sl[i_]), 1)), geometry=dict(type="LineString", coordinates=V[i_:i_ + 2].tolist())))
        keep &= ~lng
        if keep.all():
            pieces.append((np.vstack([P, P[:1]]), True, area, side)); continue
        if not keep.any():
            continue
        k0 = int(np.flatnonzero(~keep)[0]); ord_ = (np.arange(n) + k0) % n
        lab, m = ndimage.label(keep[ord_])
        for i in range(1, m + 1):
            ii = ord_[lab == i]
            if ii.size * STEP_M >= MIN_PIECE_M:
                pieces.append((P[ii], False, 0.0, side))
    print(f"  {kind}: {n_all * STEP_M / 1e3:.0f} km of outline, {n_far * STEP_M / 1e3:.0f} km away from the sea edge dropped, {n_lid * STEP_M / 1e3:.0f} km under lidar left to it, "
          f"{n_cross * STEP_M / 1e3:.0f} km across the high line dropped, {n_long * STEP_M / 1e3:.0f} km on edges over {LONG_EDGE_M:g} m dropped; {len(pieces)} pieces; {len(cal_z)} calibration points", flush=True)
    return pieces, np.array(cal_x), np.array(cal_y), np.array(cal_z)


class UpChannel:
    """Distance along the low water from wide water, on a 10 m grid (0 beyond any channel); the rise of the low line there."""
    def __init__(self, low):
        from rasterio.transform import from_origin
        from skimage.graph import MCP_Geometric
        b = low.bounds; R_ = 10.0; self.T = from_origin(b[0], b[3], R_, R_); sh = (int((b[3] - b[1]) / R_) + 1, int((b[2] - b[0]) / R_) + 1)
        m = features.rasterize([(mapping(low), 1)], out_shape=sh, transform=self.T, fill=0, dtype="uint8") > 0; din = ndimage.distance_transform_edt(m, sampling=R_).astype(np.float32); self.din = din; self.m = m
        d, _ = MCP_Geometric(np.where(m, 1.0, -1.0), fully_connected=True).find_costs(np.argwhere(din > WIDE_WATER_M))
        self.d = np.maximum(np.where(np.isfinite(d), d * R_, 0.0) - WIDE_WATER_M, 0.0).astype(np.float32)
        _, self.near = ndimage.distance_transform_edt(~m, return_indices=True)

    def at(self, x, y):
        c, r = ~self.T * (np.asarray(x), np.asarray(y)); r = np.clip(r.astype(int), 0, self.d.shape[0] - 1); c = np.clip(c.astype(int), 0, self.d.shape[1] - 1)
        return self.d[self.near[0][r, c], self.near[1][r, c]]

    def rise(self, x, y):
        return np.interp(self.at(x, y), *RISE_TABLE)

    def normal(self, x, y):
        sd = ndimage.gaussian_filter(ndimage.distance_transform_edt(~self.m, sampling=10.0).astype(np.float32) - self.din, 1.5); gy, gx = np.gradient(sd, 10.0)
        c, r = ~self.T * (np.asarray(x), np.asarray(y)); r = np.clip(r.astype(int), 0, sd.shape[0] - 1); c = np.clip(c.astype(int), 0, sd.shape[1] - 1)
        nx, ny = gx[r, c], -gy[r, c]; n = np.hypot(nx, ny) + 1e-6
        return nx / n, ny / n       # toward the land


def _low_calibration(cx, cy, cz, up):
    """The points that say what the low line's level is: lidar with a real bed slope through the line, at an open shore.  Returns them, and prints how the channel rule does on the rest."""
    nx, ny = up.normal(cx, cy); a, b, c = (_sample_lidar(cx + o * nx, cy + o * ny) for o in (-40.0, -20.0, 20.0)); d0 = LOW_REAL_DROP_M
    real = np.isfinite(a + b + c) & (a < b - d0 / 2) & (b < cz - d0) & (c > cz + d0) & (np.degrees(np.arctan((c - b) / 40.0)) < LOW_SLOPE_MAX_DEG)
    u = up.at(cx, cy); op = real & (u <= OPEN_SLACK_M); L0 = float(np.median(cz[op]))
    print(f"  low: {cx.size} points under lidar, {int(real.sum())} on a real bed slope, {int(op.sum())} of them at an open shore: median {L0:.2f}, NMAD {1.4826 * np.median(np.abs(cz[op] - L0)):.2f}", flush=True)
    for nm, k in (("all", real), ("west of x 590 km", real & (cx < 590e3)), ("east of it", real & (cx >= 590e3))):
        if k.any():
            print(f"    {nm}: n {int(k.sum())}; off the level alone: median |e| {np.median(np.abs(cz[k] - L0)):.2f}, rms {np.sqrt(np.mean((cz[k] - L0) ** 2)):.2f}; with the channel rise: median |e| {np.median(np.abs(cz[k] - L0 - np.interp(u[k], *RISE_TABLE))):.2f}, rms {np.sqrt(np.mean((cz[k] - L0 - np.interp(u[k], *RISE_TABLE)) ** 2)):.2f}", flush=True)
    # ...and a flight above low tide leaves a slope through the line too, at that flight's water edge (Port Graham, Seldovia: +0.4, +1.4): a point more than LOW_OFF_NMAD scatters off the whole bay's level says nothing about the low line
    nm = 1.4826 * float(np.median(np.abs(cz[op] - L0))); fit = op & (np.abs(cz - L0) <= LOW_OFF_NMAD * nm)
    print(f"    open points within {LOW_OFF_NMAD:g} NMAD of it: {int(fit.sum())} of {int(op.sum())}; their p5/50/95 {np.percentile(cz[fit], [5, 50, 95]).round(2)}", flush=True)
    return cx[fit], cy[fit], cz[fit]


def _levels(pieces, cx, cy, cz, kind, up=None):
    lo, hi = CLAMP[kind]; t = cKDTree(np.column_stack([cx, cy])); F = []; used = []; need = LOW_MIN_N if kind == "low" else LEVEL_MIN_N
    for xy, closed, area, side in pieces:
        total = np.hypot(*np.diff(xy, axis=0).T).sum()
        ks = 1 if closed else max(int(round(total / CHUNK_M)), 1)
        segs = np.array_split(np.arange(len(xy)), ks); bump = np.zeros(len(xy))
        if up is not None:              # a channel's line in pieces of about a level: its rise in steps of a quarter metre
            bump = np.round(up.rise(xy[:, 0], xy[:, 1]) / 0.25) * 0.25
            segs = [q for sg_ in segs for q in np.split(sg_, np.flatnonzero(np.diff(bump[sg_])) + 1)]
        for seg in segs:
            if seg.size < 1:
                continue
            b_ = float(np.max(bump[seg]))
            seg = np.r_[seg, seg[-1] + 1] if seg[-1] + 1 < len(xy) else seg
            if seg.size < 2:
                continue
            a = xy[seg]; m = a.mean(0); ii = t.query_ball_point(m, LEVEL_R_M)
            nearby = len(ii) >= need
            if not nearby:
                ii = t.query(m, k=need)[1]
            raw = float(np.median(cz[ii])); lv = float(np.clip(raw, lo, hi)) + b_; used.append(lv)
            sig = max(1.4826 * float(np.median(np.abs(cz[ii] - raw))), 0.1 if b_ == 0 else CHANNEL_SIGMA_M)      # Hig 2026-11-04: nearest lidar gives the level, with a tube around the path: its samples' spread
            sl = shapely.simplify(LineString(a), 1.0)
            F.append(dict(type="Feature", properties=dict(level_m=round(lv, 2), label=f"{kind} tide (Hig)", source=f"vector_{kind}", level_sigma_m=round(sig, 2), channel_rise_m=round(b_, 2), closed=bool(closed), enclosed_m2=round(area), side=side, level_basis="local" if nearby else "nearest"),
                          geometry=dict(type="LineString", coordinates=np.round(shapely.get_coordinates(sl), 2).tolist())))
    u = np.array(used); print(f"  {kind}: {len(F)} lines, level p10/50/90 {np.percentile(u, [10, 50, 90]).round(2)}; level from further than {LEVEL_R_M / 1e3:.0f} km on {sum(f['properties']['level_basis'] == 'nearest' for f in F)}", flush=True)
    return F


def _closures(low, dist, Td):
    """The stretches of the low water's outline that are no waterline (2026-11-05, Hig's review of cm16, 59.18565,-151.59493: 130 ha of open sea at -49 m modelled as a flat at -2.3 m): an edge closing the polygon across open
    water (a segment over LONG_EDGE_M) or lying further than SHORE_NEAR_M from 3DEP's sea edge -- the two reasons _lines drops a piece as not a shore.  Past such an edge the low water is simply not mapped, and the ground
    there is not 'between the shores' (shoreface.py).  An outline under lidar is a real shore that _lines leaves to the lidar: it is not listed here."""
    from shapely.ops import substring
    Ti = ~Td; F = []
    for ring, area, side in _rings(low):
        if ring.length < MIN_PIECE_M:
            continue
        V = shapely.get_coordinates(ring); sl = np.hypot(*np.diff(V, axis=0).T)
        for i_ in np.flatnonzero(sl > LONG_EDGE_M):
            F.append(dict(type="Feature", properties=dict(why="long edge", length_m=round(float(sl[i_]), 1)), geometry=dict(type="LineString", coordinates=V[i_:i_ + 2].tolist())))
        n = max(int(ring.length / STEP_M), 4); d = np.linspace(0, ring.length, n, endpoint=False); st = ring.length / n
        P = shapely.get_coordinates(shapely.line_interpolate_point(ring, d))
        c, r = Ti * (P[:, 0], P[:, 1]); r = np.clip(r.astype(int), 0, dist.shape[0] - 1); c = np.clip(c.astype(int), 0, dist.shape[1] - 1)
        far = dist[r, c] > SHORE_NEAR_M
        if far.all():
            F.append(dict(type="Feature", properties=dict(why="far from the sea edge", length_m=round(float(ring.length), 1)), geometry=mapping(LineString(V)))); continue
        if not far.any():
            continue
        k0 = int(np.flatnonzero(~far)[0]); ord_ = (np.arange(n) + k0) % n
        lab, m = ndimage.label(far[ord_])
        for i in range(1, m + 1):
            ii = ord_[lab == i]; d0, d1 = d[ii[0]] - st / 2, d[ii[-1]] + st / 2
            for g in ([substring(ring, d0, d1)] if d0 <= d1 and d0 >= 0 else [substring(ring, d0 % ring.length, ring.length), substring(ring, 0.0, d1)]):
                if g.geom_type == "LineString" and g.length > 0:
                    F.append(dict(type="Feature", properties=dict(why="far from the sea edge", length_m=round(float(g.length), 1)), geometry=mapping(g)))
    return F


def closures(a):
    """tide_low_closures.geojson from the low water as written, without rebuilding the lines."""
    dist, Td = _water_edge_dist()
    low = shape(json.load(open(HERE / "tide_low_water.geojson"))["features"][0]["geometry"])
    F = _closures(low, dist, Td); (HERE / "tide_low_closures.geojson").write_text(json.dumps(_fc(F)))
    for w in ("long edge", "far from the sea edge"):
        print(f"  low water's closing edges, {w}: {sum(f['properties']['why'] == w for f in F)} pieces, {sum(f['properties']['length_m'] for f in F if f['properties']['why'] == w) / 1e3:.1f} km")
    print(f"  of {low.boundary.length / 1e3:.1f} km of outline")


def _fc(F):
    return dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=F)


def _poly(g):
    """The polygons of what make_valid or an overlay may return as a collection."""
    return unary_union([p for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon")])


def build(a):
    dist, Td = _water_edge_dist(); lnear = _lidar_near(Td, dist.shape)
    Gh, Th = _polys(HIGH); high = unary_union([g for g, t in zip(Gh, Th) if t == "Shore"]).difference(unary_union([g for g, t in zip(Gh, Th) if t == "Island"]))
    Gl, Tl = _polys(LOW); low = unary_union([g for g, t in zip(Gl, Tl) if t == "Shore"]).difference(unary_union([g for g, t in zip(Gl, Tl) if t == "Island"]))
    pools = [g for g, t in zip(Gl, Tl) if t == "Pool"]; low = low.union(unary_union(pools)) if pools else low
    # THE HIGH SHORE IS AUTHORITATIVE (Hig, 2026-11-04): where the low shore lies inboard of it, it moves to 1 m seaward of it, a nearly vertical cliff.  A wide stretch of low water past the high polygon is
    # not a crossing, the high shore is simply not mapped there (HIGH_UNMAPPED_M: two of them, 2,700 ha, against 6 ha of slivers); that low water stands
    high = _poly(high); low = _poly(low)
    out_ = low.difference(high); core_ = out_.buffer(-HIGH_UNMAPPED_M / 2, quad_segs=1).buffer(HIGH_UNMAPPED_M / 2 + 10.0, quad_segs=1).intersection(out_)
    high_in = high.buffer(-LOW_OFF_HIGH_M, quad_segs=2); low0 = low
    low = _poly(shapely.make_valid(low.intersection(high_in).union(core_)))
    moved_ = low.boundary.intersection(high.boundary.buffer(LOW_OFF_HIGH_M + 0.5, quad_segs=1))
    print(f"  low shore inboard of the high: {out_.difference(core_).area / 1e4:.1f} ha cut off; {moved_.length / 1e3:.1f} km of low shore now lies against the high", flush=True)
    (HERE / "tide_high_water.geojson").write_text(json.dumps(_fc([dict(type="Feature", properties={}, geometry=mapping(shapely.simplify(high, 0.5)))])))
    (HERE / "tide_low_water.geojson").write_text(json.dumps(_fc([dict(type="Feature", properties={}, geometry=mapping(shapely.simplify(low, 0.5)))])))
    print(f"  water: high {high.area / 1e6:.0f} km2, low {low.area / 1e6:.0f} km2", flush=True)
    ph, hx, hy, hz = _lines(high, "high", dist, Td, lnear)
    near_high = high.buffer(10.0, quad_segs=2); shapely.prepare(near_high)
    pl, lx, ly, lz = _lines(low, "low", dist, Td, lnear, inside=near_high)
    up = UpChannel(low); lx, ly, lz = _low_calibration(lx, ly, lz, up)
    F = _levels(pl, lx, ly, lz, "low", up) + _levels(ph, hx, hy, hz, "high")   # low first: where the two meet on a cliff the high line is written last
    (HERE / "tide_vector_lines.geojson").write_text(json.dumps(_fc(F)))
    (HERE / "tide_dropped_edges.geojson").write_text(json.dumps(_fc(DROPPED)))
    (HERE / "tide_low_closures.geojson").write_text(json.dumps(_fc(_closures(shapely.simplify(low, 0.5), dist, Td))))      # what shoreface.py reads: the low water as written
    hl = [LineString(p[0]) for p in ph]
    sea = shapely.simplify(unary_union(shapely.buffer(hl, SEA_NEAR_M, quad_segs=2)), 1.0).intersection(high)
    S = [dict(type="Feature", properties=dict(source="high tide (Hig)"), geometry=mapping(g)) for g in getattr(sea, "geoms", [sea]) if g.area >= 25]
    (HERE / "tide_sea.geojson").write_text(json.dumps(_fc(S)))
    print(f"  tide_sea: {len(S)} polygons, {sea.area / 1e6:.1f} km2", flush=True)


def clip(a):
    G = [shape(f["geometry"]) for f in json.load(open(HERE / "tide_sea.geojson"))["features"]]; tree = shapely.STRtree(G); n = 0
    with rasterio.open(a.src) as s:
        p = s.profile.copy(); p.update(compress="lzw", tiled=True, blockxsize=512, blockysize=512, bigtiff="YES")
        with rasterio.open(a.dst + ".part.tif", "w", **p) as d:
            for r0 in range(0, s.height, 4096):
                for c0 in range(0, s.width, 4096):
                    w = Window(c0, r0, min(4096, s.width - c0), min(4096, s.height - r0)); z = s.read(1, window=w); T = s.window_transform(w)
                    hit = tree.query(shapely.box(*win_bounds(w, s.transform)))
                    if hit.size and (z != s.nodata).any():
                        m = features.rasterize([(G[i], 1) for i in hit], out_shape=z.shape, transform=T, fill=0, dtype="uint8") > 0
                        n += int((m & (z != s.nodata)).sum()); z = np.where(m, s.nodata, z)
                    d.write(z, 1, window=w)
    os.replace(a.dst + ".part.tif", a.dst); print(f"  voided {n * 4 / 1e6:.2f} km2 of it", flush=True)
    os.system(f"/opt/homebrew/bin/gdaladdo -q -r average {a.dst} 4 16 64 256")


def _consistent(E, V, rep):
    """An edited line kept only where it agrees with the high and low shores (Hig, 2026-11-04: they are the only information about the intertidal; toss other contours, "including some of my revised lines if they're
    inconsistent"): at the level of either shore, the shore IS that contour, so the edit goes; between their levels it must lie between them; below the low level it goes too (2026-11-04: the surface goes "from the low tide contour down to the deep soundings", and those lines moved the old base contour, which the mapped coast no longer has)."""
    if not E or not V or not (HERE / "tide_high_water.geojson").exists():
        return E, []
    hw = shape(json.load(open(HERE / "tide_high_water.geojson"))["features"][0]["geometry"]); lw = shape(json.load(open(HERE / "tide_low_water.geojson"))["features"][0]["geometry"])
    hw_o, hw_i, lw_o, lw_i = hw.buffer(ZONE_TOL_M, quad_segs=1), hw.buffer(-ZONE_TOL_M, quad_segs=1), lw.buffer(ZONE_TOL_M, quad_segs=1), lw.buffer(-ZONE_TOL_M, quad_segs=1)
    for g in (hw_o, hw_i, lw_o, lw_i):
        shapely.prepare(g)
    pts = {k: [] for k in ("vector_high", "vector_low")}; lvs = {k: [] for k in pts}
    for f in V:
        c = shapely.get_coordinates(shape(f["geometry"]))[:, :2]; k = f["properties"]["source"]; pts[k].append(c); lvs[k].append(np.full(len(c), f["properties"]["level_m"]))
    tree = {k: cKDTree(np.vstack(pts[k])) for k in pts}; lv_ = {k: np.concatenate(lvs[k]) for k in pts}
    K = []; Tt = []; why = {}
    for f in E:
        g = shape(f["geometry"]); lv = f["properties"]["level_m"]; n = max(int(g.length / ZONE_STEP_M), 2); d = np.linspace(0, g.length, n); P = shapely.get_coordinates(shapely.line_interpolate_point(g, d))
        Lh = lv_["vector_high"][tree["vector_high"].query(P)[1]]; Ll = lv_["vector_low"][tree["vector_low"].query(P)[1]]
        x, y = P[:, 0], P[:, 1]; same = (np.abs(lv - Lh) <= LEVEL_SAME_M) | (np.abs(lv - Ll) <= LEVEL_SAME_M)
        above = lv > Lh + LEVEL_SAME_M; below = lv < Ll - LEVEL_SAME_M; mid = ~same & ~above & ~below
        ok = np.zeros(n, bool)
        ok |= above & ~shapely.contains_xy(hw_i, x, y)
        ok |= mid & shapely.contains_xy(hw_o, x, y) & ~shapely.contains_xy(lw_i, x, y)
        for lab, m in ((True, ok), (False, ~ok)):
            l_, nl = ndimage.label(m)
            for i in range(1, nl + 1):
                ii = np.flatnonzero(l_ == i)
                seg = shapely.ops.substring(g, d[ii[0]], d[ii[-1]]) if ii.size > 1 else None
                if seg is None or seg.length < MIN_EDIT_M:
                    continue
                r = "the shore's level" if same[ii].mean() >= .5 else ("below the low shore: the soundings decide" if below[ii].mean() >= .5 else "not between the shores")
                q = dict(f["properties"]) if lab else dict(f["properties"], source="tossed", why=r)
                (K if lab else Tt).append(dict(type="Feature", properties=q, geometry=dict(type="LineString", coordinates=np.round(shapely.get_coordinates(seg), 2).tolist())))
                if not lab:
                    w_ = why.setdefault(r, {}); w_[str(lv)] = round(w_.get(str(lv), 0) + seg.length / 1e3, 2)
    rep["km_edited_kept"] = round(sum(shape(f["geometry"]).length for f in K) / 1e3, 2); rep["km_edited_tossed"] = round(sum(shape(f["geometry"]).length for f in Tt) / 1e3, 2); rep["tossed_why"] = why
    return K, Tt


def merge(a):
    orig = {tuple(np.round(np.array(f["geometry"]["coordinates"])[:, :2], 1).ravel().tolist()) + (round(f["properties"]["level_m"], 2),) for f in json.load(open(a.orig))["features"]}
    rep = dict(changed=0, held_out=[], no_level=[], seen=0, km_changed_lines=0.0, km_edited=0.0); E = []; seen = set(); by = {}
    for f in json.load(open(a.orig))["features"]:
        by.setdefault(round(f["properties"]["level_m"], 2), []).append(shape(f["geometry"]))
    base = {k: unary_union(v).buffer(EDIT_TOL_M) for k, v in by.items()}
    with fiona.open(a.revised) as f:
        src = CRS.from_wkt(f.crs_wkt) if f.crs_wkt else CRS.from_epsg(6334)
        for r in f:
            if r["geometry"] is None:
                continue
            g = shape(transform_geom(src, "EPSG:6334", dict(r["geometry"]))); rep["seen"] += 1; q = dict(r["properties"])
            for ln in getattr(g, "geoms", [g]):
                c = shapely.get_coordinates(ln); lv = q.get("level_m"); key = tuple(np.round(c, 1).ravel().tolist()) + (round(lv, 2) if lv is not None else None,); seen.add(key)
                if key in orig:
                    continue
                if q.get("op"):
                    rep["held_out"].append(dict(fid=r["id"], op=q.get("op"), inside=q.get("inside"), level_m=lv, x=round(c[0, 0]), y=round(c[0, 1]))); continue
                if lv is None:
                    rep["no_level"].append(dict(fid=r["id"], x=round(c[0, 0]), y=round(c[0, 1]))); continue
                # only the stretches the reviewer moved (2026-11-04, Hig, 59.30601,-151.04754: one moved vertex held 10 km of the old model's contour as if drawn, against the casts and the vectors)
                was = base.get(round(float(lv), 2)); rep["km_changed_lines"] += ln.length / 1e3
                for part in getattr(ln.difference(was) if was is not None else ln, "geoms", [ln.difference(was) if was is not None else ln]):
                    if part.length >= MIN_EDIT_M:
                        E.append(dict(type="Feature", properties=dict(level_m=float(lv), label=q.get("label"), source="reviewer"), geometry=dict(type="LineString", coordinates=np.round(shapely.get_coordinates(part), 2).tolist()))); rep["km_edited"] += part.length / 1e3
                rep["changed"] += 1
    rep["removed_from_copy"] = len(orig - seen)
    V = json.load(open(HERE / "tide_vector_lines.geojson"))["features"] if not a.no_vectors else []
    E, T_ = _consistent(E, V, rep)
    # Hig, 2026-11-04: no edited lines for this run, except one traced off an image: --keep-edit x,y,level names it (the stretch at that level passing within 30 m of the point).  The others stay in the set-aside file, to come back
    if a.keep_edit is not None:
        x_, y_, lv_ = (float(v) for v in a.keep_edit.split(",")); pt = shapely.Point(x_, y_); kept = [f for f in E if abs(f["properties"]["level_m"] - lv_) < 0.01 and shape(f["geometry"]).distance(pt) <= 30.0]
        T_ += [dict(f, properties=dict(f["properties"], source="tossed", why="dropped for this run (Hig)")) for f in E if f not in kept]; E = kept
        rep["km_edited_kept"] = round(sum(shape(f["geometry"]).length for f in E) / 1e3, 2); rep["km_edited_tossed"] = round(sum(shape(f["geometry"]).length for f in T_) / 1e3, 2)
    (HERE / "tide_lines_tossed.geojson").write_text(json.dumps(_fc(T_)))
    # the high shore last: it wins a shared cell (it was the reviewer's until Hig, 2026-11-04: the high and low shores are the authority, and vectors near an edit now stay)
    (HERE / "tide_lines.geojson").write_text(json.dumps(_fc([f for f in V if f["properties"]["source"] != "vector_high"] + E + [f for f in V if f["properties"]["source"] == "vector_high"])))
    rep["vector_lines"] = len(V); (HERE / "tide_lines_report.json").write_text(json.dumps(rep, indent=1))
    print({k: (v if not isinstance(v, list) else len(v)) for k, v in rep.items()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("build")
    sp.add_parser("closures")
    c = sp.add_parser("clip"); c.add_argument("src"); c.add_argument("dst")
    m = sp.add_parser("merge"); m.add_argument("revised"); m.add_argument("orig"); m.add_argument("--no-vectors", action="store_true"); m.add_argument("--keep-edit")
    a = ap.parse_args(); dict(build=build, clip=clip, merge=merge, closures=closures)[a.cmd](a)
